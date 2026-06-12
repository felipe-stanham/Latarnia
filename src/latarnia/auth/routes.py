"""
Auth routes (P-0008): `/auth/*` (browser-facing) and `/api/auth/*` (JSON).

Built via `build_auth_router(...)` so all dependencies (DB stores, the TOTP
provider, config) are injected — keeps the module import-safe and testable.

Current-user resolution is cookie-only: the validated session cookie is
forwarded by Caddy to the backend, so it works both directly (dev) and via the
proxy. The `X-Latarnia-User` header is emitted by /auth/verify for downstream
*apps* and is never trusted as an identity for Latarnia's own endpoints.
"""
from __future__ import annotations

import base64
import io
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from .roles import ROLE_VALUES

import qrcode
from qrcode.image.pure import PyPNGImage
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

logger = logging.getLogger("latarnia.auth.routes")

# templates/ lives at the project root: src/latarnia/auth/routes.py -> parents[3]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
_templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))

# Headers the forward_auth response carries back to Caddy.
H_USER = "X-Latarnia-User"
H_ROLE = "X-Latarnia-App-Role"
H_SUPER = "X-Latarnia-Is-Super"

DASHBOARD_PATH = "/dashboard"


def safe_next(next_param: Optional[str]) -> str:
    """Return a validated same-origin redirect target, or DASHBOARD_PATH.

    Accepts `next` only if it starts with a single '/', does not start with
    '//' or '/\\', and contains no '://'. Tolerates '?' in otherwise-safe
    values (orig_uri carries the original query string). Falls back to
    DASHBOARD_PATH on any violation so open-redirect is impossible.
    """
    if not next_param:
        return DASHBOARD_PATH
    if not next_param.startswith("/"):
        return DASHBOARD_PATH
    if next_param.startswith("//") or next_param.startswith("/\\"):
        return DASHBOARD_PATH
    if "://" in next_param:
        return DASHBOARD_PATH
    return next_param


# Usernames are constrained to a safe, render-friendly character set. This is
# the authoritative defense against HTML/JS injection via a username that is
# later interpolated into the dashboard (the dashboard also escapes, belt-and-
# braces). 1–64 chars of letters, digits, dot, underscore, hyphen.
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def resolve_session_user(request, session_store, user_store, cookie_name):
    """Single source of truth for cookie-based current-user resolution.

    Returns the user row or None. Used by the auth router and by the platform's
    own role-scoped endpoints (e.g. /api/apps) so the two never diverge — Scope
    4's JWT support augments this one function.
    """
    token = request.cookies.get(cookie_name)
    uid = session_store.validate_session(token)
    if not uid:
        return None
    return user_store.get_user_by_id(uid)


def require_superuser(request, *, session_store, user_store, cookie_name):
    """Assert that the current principal is a Superuser; raise 403 otherwise.

    Handles both principals: Bearer JWT (request.state.jwt_claims set by
    JWTAuthMiddleware) and session cookie (resolved via resolve_session_user).
    Returns the user row for session callers, or None for Bearer callers (no
    user row is needed when the JWT already carries a verified super claim).
    """
    jwt_claims = getattr(getattr(request, "state", None), "jwt_claims", None)
    if jwt_claims is not None:
        if not jwt_claims.get("super", False):
            raise HTTPException(status_code=403, detail="Superuser required")
        return {"id": jwt_claims.get("sub"), "is_superuser": True}
    user = resolve_session_user(request, session_store, user_store, cookie_name)
    if not user or not user["is_superuser"]:
        raise HTTPException(status_code=403, detail="Superuser required")
    return user


def _qr_data_uri(otpauth_uri: str) -> str:
    """Render a TOTP provisioning URI to a base64 PNG data URI (no PIL)."""
    img = qrcode.make(otpauth_uri, image_factory=PyPNGImage, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def _extract_app_name(forwarded_uri: str) -> Optional[str]:
    """Pull the app name out of `/apps/{name}/...`; None for other paths."""
    if not forwarded_uri:
        return None
    path = forwarded_uri.split("?", 1)[0]
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "apps":
        return parts[1]
    return None


def build_auth_router(auth_db, user_store, session_store, totp_provider,
                      config_manager, role_store=None, app_manager=None,
                      jwt_auth=None, token_store=None) -> APIRouter:
    """Assemble the auth router.

    `role_store` (RoleStore) is optional. When absent (pre-Scope-3), app role
    resolves to 'full' for superusers and 'none' otherwise. `app_manager`, when
    provided, lets role assignment reject unknown app names. `jwt_auth` +
    `token_store` (Scope 4) enable the machine-token endpoints.
    """
    router = APIRouter()
    cookie_name = config_manager.config.auth.cookie_name
    role_lookup = role_store.get_role if role_store is not None else None

    def _render(request, template, ctx, status_code=200):
        # Auth pages (esp. setup, which shows the plaintext TOTP secret for
        # manual entry) must never be cached by the browser or any proxy.
        resp = _templates.TemplateResponse(request, template, ctx, status_code=status_code)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def _cookie_secure() -> bool:
        # Secure cookies require HTTPS. In dev (plain http on :8000) the browser
        # would drop a Secure cookie, so we relax it there. Prod/tst run behind
        # Caddy TLS where Secure is always set.
        return config_manager.get_env() != "dev"

    def _set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            cookie_name, token,
            httponly=True, secure=_cookie_secure(), samesite="strict", path="/",
            max_age=config_manager.config.auth.session_ttl_hours * 3600,
        )

    def _current_user(request: Request):
        # Cookie-only, by design (see resolve_session_user). We deliberately do
        # NOT trust the X-Latarnia-User header for Latarnia's own privileged
        # endpoints — it's set by Caddy for downstream apps and is spoofable
        # whenever the platform port is reachable (e.g. dev, where ufw is off).
        return resolve_session_user(request, session_store, user_store, cookie_name)

    def _require_user(request: Request):
        user = _current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Authentication required")
        return user

    def _require_superuser(request: Request):
        return require_superuser(
            request,
            session_store=session_store,
            user_store=user_store,
            cookie_name=cookie_name,
        )

    def _role_for(user, app_name: Optional[str]) -> str:
        if app_name is None:
            return "full" if user["is_superuser"] else "none"
        if user["is_superuser"]:
            return "full"
        if role_lookup is not None:
            return role_lookup(user["id"], app_name)
        return "none"

    def _public_setup_url(request: Request, token: str) -> str:
        base = str(request.base_url).rstrip("/")
        return f"{base}/auth/setup?token={quote(token)}"

    # ------------------------------------------------------------------
    # Setup (first-run + invited users)
    # ------------------------------------------------------------------

    @router.get("/auth/setup", response_class=HTMLResponse)
    async def get_setup(request: Request, token: Optional[str] = None):
        if token:
            user = user_store.get_user_by_setup_token(token)
            if not user:
                return _render(
                    request, "auth/login.html",
                    {"error": "Invalid or expired setup link."},
                    status_code=400,
                )
            data = totp_provider.ensure_credentials(user["id"], username=user["username"])
            action = f"/auth/setup?token={quote(token)}"
            return _render(request, "auth/setup.html", {
                "qr_data_uri": _qr_data_uri(data["otpauth_uri"]),
                "secret": data["secret"], "action": action,
                "account_name": user["username"], "error": None,
            })

        # First-run: no active users yet.
        if user_store.count_active_users() == 0:
            user = user_store.get_or_create_bootstrap_superuser()
            data = totp_provider.ensure_credentials(user["id"], username=user["username"])
            return _render(request, "auth/setup.html", {
                "qr_data_uri": _qr_data_uri(data["otpauth_uri"]),
                "secret": data["secret"], "action": "/auth/setup",
                "account_name": user["username"], "error": None,
            })

        return RedirectResponse("/auth/login", status_code=303)

    @router.post("/auth/setup")
    async def post_setup(request: Request, code: str = Form(...),
                         token: Optional[str] = None):
        if token:
            user = user_store.get_user_by_setup_token(token)
            if not user:
                return _render(
                    request, "auth/login.html",
                    {"error": "Invalid or expired setup link."}, status_code=400)
        elif user_store.count_active_users() == 0:
            user = user_store.get_or_create_bootstrap_superuser()
        else:
            return RedirectResponse("/auth/login", status_code=303)

        if not totp_provider.validate(user["id"], {"code": code}):
            data = totp_provider.ensure_credentials(user["id"], username=user["username"])
            action = f"/auth/setup?token={quote(token)}" if token else "/auth/setup"
            return _render(request, "auth/setup.html", {
                "qr_data_uri": _qr_data_uri(data["otpauth_uri"]),
                "secret": data["secret"], "action": action,
                "account_name": user["username"],
                "error": "Invalid code — try again.",
            }, status_code=400)

        user_store.activate_user(user["id"])
        client_ip = request.client.host if request.client else ""
        sess = session_store.create_session(user["id"], client_ip)
        resp = RedirectResponse(DASHBOARD_PATH, status_code=303)
        _set_session_cookie(resp, sess)
        logger.info("Completed setup for user %s", user["username"])
        return resp

    # ------------------------------------------------------------------
    # Login / logout
    # ------------------------------------------------------------------

    @router.get("/auth/login", response_class=HTMLResponse)
    async def get_login(request: Request, next: Optional[str] = None):
        return _render(
            request, "auth/login.html", {"error": None, "next": next})

    @router.post("/auth/login")
    async def post_login(request: Request, username: str = Form(...),
                         code: str = Form(...), next: Optional[str] = None):
        user = user_store.get_user_by_username(username)
        bad = (not user) or (not user["is_active"]) or \
            (not totp_provider.validate(user["id"], {"code": code}))
        if bad:
            return _render(
                request, "auth/login.html",
                {"error": "Invalid username or code.", "next": next},
                status_code=401)

        user_store.touch_last_login(user["id"])
        client_ip = request.client.host if request.client else ""
        sess = session_store.create_session(user["id"], client_ip)
        target = safe_next(next)
        resp = RedirectResponse(target, status_code=303)
        _set_session_cookie(resp, sess)
        return resp

    @router.delete("/auth/session")
    async def logout(request: Request):
        token = request.cookies.get(cookie_name)
        session_store.invalidate_session(token)
        resp = JSONResponse({"success": True, "message": "Logged out"})
        resp.delete_cookie(cookie_name, path="/")
        return resp

    # ------------------------------------------------------------------
    # forward_auth verification (called by Caddy on every protected request)
    # ------------------------------------------------------------------

    @router.get("/auth/verify")
    async def verify(request: Request):
        token = request.cookies.get(cookie_name)
        uid = session_store.validate_session(token)
        if uid is None:
            return Response(status_code=401)
        user = user_store.get_user_by_id(uid)
        if not user or not user["is_active"]:
            return Response(status_code=401)

        app_name = _extract_app_name(request.headers.get("X-Forwarded-Uri", ""))
        headers = {
            H_USER: str(user["id"]),
            H_SUPER: "true" if user["is_superuser"] else "false",
            H_ROLE: _role_for(user, app_name),
        }
        return Response(status_code=200, headers=headers)

    # ------------------------------------------------------------------
    # User management API (superuser only)
    # ------------------------------------------------------------------

    def _sanitize_user(u: dict) -> dict:
        return {
            "id": str(u["id"]),
            "username": u["username"],
            "is_superuser": u["is_superuser"],
            "is_active": u["is_active"],
            "created_at": u["created_at"].isoformat() if u["created_at"] else None,
            "last_login_at": u["last_login_at"].isoformat() if u["last_login_at"] else None,
        }

    @router.get("/api/auth/users")
    async def list_users(request: Request):
        _require_superuser(request)
        return {"users": [_sanitize_user(u) for u in user_store.list_users()]}

    @router.post("/api/auth/users")
    async def create_user(request: Request):
        _require_superuser(request)
        body = await request.json()
        username = (body or {}).get("username", "").strip()
        if not username:
            raise HTTPException(status_code=400, detail="username is required")
        if not USERNAME_RE.match(username):
            raise HTTPException(
                status_code=400,
                detail="username must be 1-64 chars of letters, digits, '.', '_', '-'",
            )
        if user_store.get_user_by_username(username):
            raise HTTPException(status_code=409, detail="username already exists")
        is_superuser = bool((body or {}).get("is_superuser", False))
        ttl = config_manager.config.auth.setup_token_ttl_hours
        _, token = user_store.create_user(username, is_superuser=is_superuser,
                                           setup_ttl_hours=ttl)
        return {"setup_url": _public_setup_url(request, token)}

    @router.delete("/api/auth/users/{user_id}")
    async def hard_delete_user(request: Request, user_id: str):
        """Permanently delete a user (Superuser only).

        Route migration (P-0010 Scope 4): this endpoint previously performed
        deactivation. That behavior has moved to POST .../deactivate. DELETE
        now means hard-delete (irreversible).
        """
        actor = _require_superuser(request)
        target = user_store.get_user_by_id(user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        try:
            user_store.delete_user(user_id, actor["id"])
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"success": True, "message": f"User {target['username']} deleted"}

    @router.post("/api/auth/users/{user_id}/deactivate")
    async def deactivate_user(request: Request, user_id: str):
        """Deactivate a user — sets is_active=FALSE, clears sessions, revokes
        machine tokens (Superuser only)."""
        actor = _require_superuser(request)
        if str(actor["id"]) == str(user_id):
            raise HTTPException(status_code=403, detail="Cannot deactivate yourself")
        target = user_store.get_user_by_id(user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        user_store.deactivate_user(user_id, token_store=token_store)
        return {"success": True, "message": f"User {target['username']} deactivated"}

    @router.post("/api/auth/users/{user_id}/activate")
    async def activate_user(request: Request, user_id: str):
        """Re-activate a deactivated user who still holds TOTP credentials
        (Superuser only). Does not un-revoke machine tokens."""
        _require_superuser(request)
        target = user_store.get_user_by_id(user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        try:
            user_store.reactivate_user(user_id, totp_provider=totp_provider)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"success": True, "message": f"User {target['username']} activated"}

    @router.post("/api/auth/users/{user_id}/setup-token")
    async def reissue_setup_token(request: Request, user_id: str):
        """Re-issue a TOTP enrollment link (Superuser only).

        Deactivates the user, deletes their TOTP credential, revokes all their
        machine tokens, and returns a fresh one-time setup URL.
        """
        _require_superuser(request)
        target = user_store.get_user_by_id(user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        if token_store is None:
            raise HTTPException(status_code=503, detail="Token store unavailable")
        ttl = config_manager.config.auth.setup_token_ttl_hours
        token = user_store.reissue_setup_token(
            user_id, totp_provider=totp_provider,
            token_store=token_store, setup_ttl_hours=ttl,
        )
        return {"success": True, "setup_url": _public_setup_url(request, token)}

    # ------------------------------------------------------------------
    # Role API
    # ------------------------------------------------------------------

    @router.get("/api/auth/roles")
    async def my_roles(request: Request):
        """The calling user's identity + per-app role map (for the dashboard)."""
        user = _require_user(request)
        roles = role_store.get_all_roles(user["id"]) if role_store else {}
        return {
            "username": user["username"],
            "is_superuser": user["is_superuser"],
            "roles": roles,
        }

    @router.get("/api/auth/roles/all")
    async def all_user_roles(request: Request):
        """Every user with their role map — superuser only (admin UI)."""
        _require_superuser(request)
        if role_store is None:
            return {"users": []}
        return {"users": role_store.get_users_with_roles()}

    @router.post("/api/auth/roles/{app_name}")
    async def assign_role(request: Request, app_name: str):
        """Assign a user's role for an app. Superuser only; `full` is gated
        again in RoleStore.set_role (defense in depth)."""
        actor = _require_superuser(request)
        if role_store is None:
            raise HTTPException(status_code=503, detail="Role store unavailable")
        body = await request.json()
        role = (body or {}).get("role")
        target_user_id = (body or {}).get("user_id")
        if not role or not target_user_id:
            raise HTTPException(status_code=400, detail="role and user_id are required")
        if app_manager is not None and app_manager.registry.get_app_by_name(app_name) is None:
            raise HTTPException(status_code=404, detail=f"Unknown app: {app_name}")
        if not user_store.get_user_by_id(target_user_id):
            raise HTTPException(status_code=404, detail="User not found")
        try:
            role_store.set_role(target_user_id, app_name, role, granted_by=actor["id"])
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"success": True, "app_name": app_name, "role": role,
                "user_id": str(target_user_id)}

    # ------------------------------------------------------------------
    # Machine tokens (JWT) — Scope 4
    # ------------------------------------------------------------------

    @router.post("/api/auth/tokens")
    async def create_token(request: Request):
        actor = _require_user(request)
        if token_store is None:
            raise HTTPException(status_code=503, detail="Token store unavailable")
        body = await request.json() or {}
        label = (body.get("label") or "").strip()
        app_scope = body.get("app_scope") or {}
        if not label:
            raise HTTPException(status_code=400, detail="label is required")
        if not isinstance(app_scope, dict):
            raise HTTPException(status_code=400, detail="app_scope must be an object")
        for app_name, role in app_scope.items():
            if role not in ROLE_VALUES:
                raise HTTPException(status_code=400, detail=f"invalid role: {role}")
            # Superuser required to mint a token carrying a `full` grant.
            if role == "full" and not actor["is_superuser"]:
                raise HTTPException(
                    status_code=403,
                    detail="Only a superuser can issue a token with the 'full' role",
                )
        expires_at = None
        raw_exp = body.get("expires_at")
        if raw_exp:
            try:
                expires_at = datetime.fromisoformat(str(raw_exp).replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(status_code=400, detail="expires_at must be ISO-8601")
        raw = token_store.create_token(
            actor["id"], label, app_scope, is_super=actor["is_superuser"],
            granted_by=actor["id"], expires_at=expires_at,
        )
        return {"token": raw}

    @router.get("/api/auth/tokens")
    async def list_tokens(request: Request):
        actor = _require_user(request)
        if token_store is None:
            return {"tokens": []}
        return {"tokens": token_store.list_tokens(actor["id"])}

    @router.delete("/api/auth/tokens/{token_id}")
    async def revoke_token(request: Request, token_id: str):
        actor = _require_user(request)
        if token_store is None:
            raise HTTPException(status_code=503, detail="Token store unavailable")
        if not token_store.revoke(token_id, actor["id"]):
            raise HTTPException(status_code=404, detail="Token not found")
        return {"success": True, "message": "Token revoked"}

    return router
