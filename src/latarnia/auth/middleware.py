"""
JWT/session auth middleware (P-0008 Scope 4).

Pure ASGI (not BaseHTTPMiddleware) so it can act ONLY on the gated prefixes
(`/api/`) and pass everything else — notably the mounted `/mcp` SSE app — through
untouched. BaseHTTPMiddleware buffers responses and would break SSE streaming.

Gate logic for a gated request:
  - `Authorization: Bearer <jwt>` present -> validate signature/expiry + check the
    token is recorded and not revoked. On success, attach claims to
    `scope["state"]["jwt_claims"]`. On failure, 401.
  - else a valid session cookie -> attach `scope["state"]["session_user_id"]`.
  - else 401.

MCP (`/mcp`) auth is enforced inside the gateway, not here (see mcp_gateway).
"""
from __future__ import annotations

import json
import logging
from http.cookies import SimpleCookie

from starlette.datastructures import Headers

logger = logging.getLogger("latarnia.auth.middleware")


class JWTAuthMiddleware:
    def __init__(self, app, *, jwt_auth, token_store, session_store,
                 cookie_name, gated_prefixes=("/api/",)):
        self.app = app
        self.jwt_auth = jwt_auth
        self.tokens = token_store
        self.sessions = session_store
        self.cookie_name = cookie_name
        self.gated_prefixes = tuple(gated_prefixes)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not scope["path"].startswith(self.gated_prefixes):
            return await self.app(scope, receive, send)

        headers = Headers(scope=scope)
        state = scope.setdefault("state", {})

        auth = headers.get("authorization", "")
        if auth.startswith("Bearer "):
            claims = self._authorize_bearer(auth[7:].strip())
            if claims is None:
                return await self._reject(send, "Invalid or expired token")
            state["jwt_claims"] = claims
            return await self.app(scope, receive, send)

        token = self._cookie(headers.get("cookie", ""))
        uid = self.sessions.validate_session(token) if token else None
        if uid:
            # Handlers re-resolve the user from the cookie (resolve_session_user);
            # we only need to confirm a valid session exists to pass the gate.
            return await self.app(scope, receive, send)

        return await self._reject(send, "Authentication required")

    def _authorize_bearer(self, token):
        claims = self.jwt_auth.validate(token)
        if claims is None:
            return None
        if not self.tokens.is_active(self.jwt_auth.token_hash(token)):
            return None  # unknown or revoked
        return claims

    def _cookie(self, cookie_header: str):
        if not cookie_header:
            return None
        try:
            jar = SimpleCookie()
            jar.load(cookie_header)
            morsel = jar.get(self.cookie_name)
            return morsel.value if morsel else None
        except Exception:
            return None

    async def _reject(self, send, detail: str):
        body = json.dumps({"detail": detail}).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})
