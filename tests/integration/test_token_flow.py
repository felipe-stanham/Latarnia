"""
Integration tests for P-0008 Scope 4 — JWT middleware + machine tokens.

Covers cap-019 (issue/list/revoke; revoked -> 401) and cap-020 (no token ->
401, expired -> 401, scoped to a different app -> 403). Real Postgres + a
minimal app wiring the auth router, the JWTAuthMiddleware and two gated test
endpoints that mirror main.py's scoping rule. Skipped without Postgres.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from latarnia.auth import AuthDB
from latarnia.auth.jwt_auth import JWTAuth
from latarnia.auth.middleware import JWTAuthMiddleware
from latarnia.auth.providers import TOTPAuthProvider
from latarnia.auth.roles import RoleStore
from latarnia.auth.routes import build_auth_router
from latarnia.auth.sessions import SessionStore
from latarnia.auth.tokens import MachineTokenStore
from latarnia.auth.users import UserStore
from latarnia.core.config import ConfigManager
from latarnia.core.pg_client import PgClient

COOKIE = "latarnia_session"
TEST_DB = "latarnia_platform_test_tokens"


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("ENV", "dev")
    cfg = ConfigManager()
    cfg.load_config()
    pg = PgClient(cfg)
    if not pg.check_connectivity():
        pytest.skip("Postgres not reachable")

    db = AuthDB(cfg, pg)
    db.db_name = TEST_DB
    if pg.database_exists(TEST_DB):
        pg.drop_database(TEST_DB)
    assert db.initialize()

    users = UserStore(db)
    sessions = SessionStore(db, cfg)
    roles = RoleStore(db, users)
    totp = TOTPAuthProvider(db, lambda: b"x" * 32, issuer="Test")
    jwt_auth = JWTAuth(lambda: "test-jwt-secret-0123456789-abcdefghij")
    tokens = MachineTokenStore(db, jwt_auth)

    app = FastAPI()
    app.add_middleware(
        JWTAuthMiddleware, jwt_auth=jwt_auth, token_store=tokens,
        session_store=sessions, cookie_name=COOKIE,
    )
    app.include_router(build_auth_router(
        db, users, sessions, totp, cfg,
        role_store=roles, jwt_auth=jwt_auth, token_store=tokens,
    ))

    @app.get("/api/ping")
    async def ping():
        return {"ok": True}

    @app.get("/api/apps/{name}")
    async def get_app(name: str, request: Request):
        claims = getattr(request.state, "jwt_claims", None)
        if claims is not None and not claims.get("super"):
            if claims.get("apps", {}).get(name, "none") == "none":
                raise HTTPException(status_code=403, detail="not scoped")
        return {"name": name}

    client = TestClient(app, follow_redirects=False)

    def make_user(username, is_superuser=False):
        u, _ = users.create_user(username, is_superuser=is_superuser)
        users.activate_user(u["id"])
        return u, sessions.create_session(u["id"])

    yield {"client": client, "users": users, "tokens": tokens,
           "jwt": jwt_auth, "make_user": make_user}
    pg.drop_database(TEST_DB)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_gated_endpoint_requires_auth(ctx):
    # cap-020: no token -> 401
    assert ctx["client"].get("/api/ping").status_code == 401
    # garbage bearer -> 401
    assert ctx["client"].get("/api/ping", headers=_bearer("not-a-jwt")).status_code == 401


def test_issue_list_and_use_token(ctx):
    client = ctx["client"]
    admin, admin_token = ctx["make_user"]("admin", is_superuser=True)
    # cap-019: issue (session-authenticated)
    client.cookies.clear()
    r = client.post("/api/auth/tokens",
                    json={"label": "agent", "app_scope": {"app_a": "webUI-med"}},
                    cookies={COOKIE: admin_token})
    assert r.status_code == 200
    raw = r.json()["token"]
    # use it on a gated endpoint
    client.cookies.clear()
    assert client.get("/api/ping", headers=_bearer(raw)).status_code == 200
    # appears in the list
    client.cookies.clear()
    listing = client.get("/api/auth/tokens", cookies={COOKIE: admin_token}).json()["tokens"]
    assert any(t["label"] == "agent" for t in listing)


def test_token_scope_enforced(ctx):
    # cap-020: a non-super token scoped to app_a -> app_b is 403.
    # (A superuser token carries super=true and is intentionally unscoped.)
    client = ctx["client"]
    bob, bob_token = ctx["make_user"]("bob")
    client.cookies.clear()
    raw = client.post("/api/auth/tokens",
                      json={"label": "scoped", "app_scope": {"app_a": "webUI-med"}},
                      cookies={COOKIE: bob_token}).json()["token"]
    assert client.get("/api/apps/app_a", headers=_bearer(raw)).status_code == 200
    assert client.get("/api/apps/app_b", headers=_bearer(raw)).status_code == 403


def test_expired_token_rejected(ctx):
    # cap-020: expired JWT -> 401
    client, tokens = ctx["client"], ctx["tokens"]
    admin, _ = ctx["make_user"]("admin", is_superuser=True)
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    raw = tokens.create_token(admin["id"], "old", {"app_a": "full"},
                              is_super=True, granted_by=admin["id"], expires_at=past)
    assert client.get("/api/ping", headers=_bearer(raw)).status_code == 401


def test_revoked_token_rejected(ctx):
    # cap-019: revoke -> subsequent use 401
    client = ctx["client"]
    admin, admin_token = ctx["make_user"]("admin", is_superuser=True)
    client.cookies.clear()
    raw = client.post("/api/auth/tokens",
                      json={"label": "temp", "app_scope": {}},
                      cookies={COOKIE: admin_token}).json()["token"]
    assert client.get("/api/ping", headers=_bearer(raw)).status_code == 200
    client.cookies.clear()
    tid = client.get("/api/auth/tokens", cookies={COOKIE: admin_token}).json()["tokens"][0]["id"]
    client.cookies.clear()
    assert client.request("DELETE", f"/api/auth/tokens/{tid}",
                          cookies={COOKIE: admin_token}).status_code == 200
    assert client.get("/api/ping", headers=_bearer(raw)).status_code == 401


def test_non_superuser_cannot_mint_full_token(ctx):
    client = ctx["client"]
    bob, bob_token = ctx["make_user"]("bob")
    client.cookies.clear()
    r = client.post("/api/auth/tokens",
                    json={"label": "x", "app_scope": {"app_a": "full"}},
                    cookies={COOKIE: bob_token})
    assert r.status_code == 403


def test_session_cookie_also_authorizes_gated_endpoint(ctx):
    # Coexistence: session cookie (no bearer) also passes the middleware.
    client = ctx["client"]
    _bob, bob_token = ctx["make_user"]("bob")
    client.cookies.clear()
    assert client.get("/api/ping", cookies={COOKIE: bob_token}).status_code == 200
