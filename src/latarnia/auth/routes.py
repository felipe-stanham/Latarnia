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
from pathlib import Path
from typing import Optional
from urllib.parse import quote

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
                      config_manager, role_lookup=None) -> APIRouter:
    """Assemble the auth router.

    `role_lookup(user_id, app_name) -> str` is optional (added in Scope 3).
    Until then, app role resolves to 'full' for superusers and 'none' otherwise.
    """
    router = APIRouter()
    cookie_name = config_manager.config.auth.cookie_name

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
        # Cookie-only, by design. The session cookie is forwarded by Caddy to
        # the backend, so this works both directly (dev) and behind the proxy.
        # We deliberately do NOT trust the X-Latarnia-User header here: that
        # header is set by Caddy for downstream *apps*, and trusting it for
        # Latarnia's own privileged endpoints would be spoofable whenever the
        # platform port is reachable (e.g. dev, where ufw isn't in play).
        token = request.cookies.get(cookie_name)
        uid = session_store.validate_session(token)
        if not uid:
            return None
        return user_store.get_user_by_id(uid)

    def _require_superuser(request: Request):
        user = _current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not user["is_superuser"]:
            raise HTTPException(status_code=403, detail="Superuser required")
        return user

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
        target = next if (next and next.startswith("/")) else DASHBOARD_PATH
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
        if user_store.get_user_by_username(username):
            raise HTTPException(status_code=409, detail="username already exists")
        is_superuser = bool((body or {}).get("is_superuser", False))
        ttl = config_manager.config.auth.setup_token_ttl_hours
        _, token = user_store.create_user(username, is_superuser=is_superuser,
                                           setup_ttl_hours=ttl)
        return {"setup_url": _public_setup_url(request, token)}

    @router.delete("/api/auth/users/{user_id}")
    async def deactivate_user(request: Request, user_id: str):
        actor = _require_superuser(request)
        if str(actor["id"]) == str(user_id):
            raise HTTPException(status_code=403, detail="Cannot deactivate yourself")
        target = user_store.get_user_by_id(user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        user_store.deactivate_user(user_id)
        return {"success": True, "message": f"User {target['username']} deactivated"}

    return router
