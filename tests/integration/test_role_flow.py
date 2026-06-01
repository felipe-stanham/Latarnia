"""
Integration tests for P-0008 Scope 3 role endpoints (real Postgres, TestClient).

Covers cap-016 (role map), cap-017 (admin listing), cap-018 (superuser-only
`full` grant). Skipped when Postgres is unreachable.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latarnia.auth import AuthDB
from latarnia.auth.providers import TOTPAuthProvider
from latarnia.auth.roles import RoleStore
from latarnia.auth.routes import build_auth_router
from latarnia.auth.sessions import SessionStore
from latarnia.auth.users import UserStore
from latarnia.core.config import ConfigManager
from latarnia.core.pg_client import PgClient

COOKIE = "latarnia_session"
TEST_DB = "latarnia_platform_test_roles"


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
    app = FastAPI()
    app.include_router(build_auth_router(db, users, sessions, totp, cfg, role_store=roles))
    client = TestClient(app, follow_redirects=False)

    def make_user(username, is_superuser=False):
        u, _ = users.create_user(username, is_superuser=is_superuser)
        users.activate_user(u["id"])
        return u, sessions.create_session(u["id"])

    yield {"client": client, "users": users, "roles": roles, "make_user": make_user}
    pg.drop_database(TEST_DB)


def test_my_roles_reports_identity_and_map(ctx):
    client, roles = ctx["client"], ctx["roles"]
    bob, bob_token = ctx["make_user"]("bob")
    roles.set_role(bob["id"], "my_app", "webUI-med", granted_by=bob["id"])
    client.cookies.clear()
    r = client.get("/api/auth/roles", cookies={COOKIE: bob_token})
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "bob" and body["is_superuser"] is False
    assert body["roles"]["my_app"] == "webUI-med"


def test_my_roles_requires_auth(ctx):
    assert ctx["client"].get("/api/auth/roles").status_code == 401


def test_superuser_can_assign_and_it_persists(ctx):
    client, roles = ctx["client"], ctx["roles"]
    admin, admin_token = ctx["make_user"]("admin", is_superuser=True)
    bob, _ = ctx["make_user"]("bob")
    client.cookies.clear()
    r = client.post(f"/api/auth/roles/my_app",
                    json={"role": "webUI-low", "user_id": str(bob["id"])},
                    cookies={COOKIE: admin_token})
    assert r.status_code == 200
    assert roles.get_role(bob["id"], "my_app") == "webUI-low"


def test_superuser_can_assign_full(ctx):
    client = ctx["client"]
    admin, admin_token = ctx["make_user"]("admin", is_superuser=True)
    bob, _ = ctx["make_user"]("bob")
    client.cookies.clear()
    r = client.post("/api/auth/roles/my_app",
                    json={"role": "full", "user_id": str(bob["id"])},
                    cookies={COOKIE: admin_token})
    assert r.status_code == 200  # cap-018: superuser may grant full


def test_non_superuser_cannot_assign(ctx):
    client = ctx["client"]
    bob, bob_token = ctx["make_user"]("bob")
    carol, _ = ctx["make_user"]("carol")
    client.cookies.clear()
    # cap-018: non-superuser assigning any role (incl. full) -> 403.
    r = client.post("/api/auth/roles/my_app",
                    json={"role": "full", "user_id": str(carol["id"])},
                    cookies={COOKIE: bob_token})
    assert r.status_code == 403


def test_create_user_rejects_unsafe_username(ctx):
    client = ctx["client"]
    admin, admin_token = ctx["make_user"]("admin", is_superuser=True)
    client.cookies.clear()
    r = client.post("/api/auth/users",
                    json={"username": "<img src=x onerror=alert(1)>"},
                    cookies={COOKIE: admin_token})
    assert r.status_code == 400


def test_roles_all_is_superuser_only(ctx):
    client = ctx["client"]
    admin, admin_token = ctx["make_user"]("admin", is_superuser=True)
    bob, bob_token = ctx["make_user"]("bob")
    client.cookies.clear()
    assert client.get("/api/auth/roles/all", cookies={COOKIE: bob_token}).status_code == 403
    client.cookies.clear()
    r = client.get("/api/auth/roles/all", cookies={COOKIE: admin_token})
    assert r.status_code == 200
    names = {u["username"] for u in r.json()["users"]}
    assert {"admin", "bob"} <= names
