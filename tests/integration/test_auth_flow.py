"""
Integration tests for the P-0008 auth flow (real Postgres, FastAPI TestClient).

Covers cap-009 (setup), cap-010 (login), cap-011 (session/logout),
cap-012 (verify + headers), cap-013 (encrypted secret), cap-024 (user mgmt).

Skipped automatically when Postgres is unreachable.
"""
import os
import time

import pyotp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latarnia.auth import AuthDB
from latarnia.auth.providers import TOTPAuthProvider
from latarnia.auth.routes import build_auth_router
from latarnia.auth.sessions import SessionStore
from latarnia.auth.users import UserStore, BOOTSTRAP_USERNAME
from latarnia.core.config import ConfigManager
from latarnia.core.pg_client import PgClient

COOKIE = "latarnia_session"
TEST_DB = "latarnia_platform_test_auth"


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("ENV", "dev")  # relaxes Secure cookie for http TestClient
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

    key = os.urandom(32)
    users = UserStore(db)
    sessions = SessionStore(db, cfg)
    totp = TOTPAuthProvider(db, lambda: key, issuer="Test")
    app = FastAPI()
    app.include_router(build_auth_router(db, users, sessions, totp, cfg))
    client = TestClient(app, follow_redirects=False)

    yield {"client": client, "db": db, "users": users, "totp": totp, "pg": pg}

    pg.drop_database(TEST_DB)


def _code_for(secret, offset_windows=0):
    return pyotp.TOTP(secret).at(int(time.time()) + offset_windows * 30)


def _complete_first_setup(ctx):
    client, users, totp = ctx["client"], ctx["users"], ctx["totp"]
    # GET renders the QR setup page for the bootstrap superuser.
    r = client.get("/auth/setup")
    assert r.status_code == 200
    assert "Confirm Setup" in r.text
    user = users.get_user_by_username(BOOTSTRAP_USERNAME)
    secret = totp.get_existing_secret(user["id"])
    # POST a valid code -> activated superuser + session cookie.
    r = client.post("/auth/setup", data={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"
    assert COOKIE in r.cookies
    return user, secret, r.cookies[COOKIE]


# ---------------------------------------------------------------- cap-008/013

def test_tables_created_and_secret_encrypted(ctx):
    user, secret, _ = _complete_first_setup(ctx)
    row = ctx["db"].query_one(
        "SELECT auth_method, credential_data FROM user_credentials WHERE user_id=%s",
        (user["id"],),
    )
    assert row["auth_method"] == "totp"
    enc = row["credential_data"]["totp_secret_enc"]
    assert enc and enc != secret  # ciphertext, not the base32 secret


# -------------------------------------------------------------------- cap-009

def test_first_setup_creates_superuser(ctx):
    user, _, _ = _complete_first_setup(ctx)
    fresh = ctx["users"].get_user_by_username(BOOTSTRAP_USERNAME)
    assert fresh["is_superuser"] and fresh["is_active"]


def test_setup_invalid_code_stays_on_page(ctx):
    ctx["client"].get("/auth/setup")
    r = ctx["client"].post("/auth/setup", data={"code": "000000"})
    assert r.status_code == 400
    assert "Invalid code" in r.text


def test_setup_after_users_exist_redirects_to_login(ctx):
    _complete_first_setup(ctx)
    ctx["client"].cookies.clear()
    r = ctx["client"].get("/auth/setup")
    assert r.status_code == 303 and r.headers["location"] == "/auth/login"


# -------------------------------------------------------------------- cap-010

def test_login_with_valid_code(ctx):
    _user, secret, _ = _complete_first_setup(ctx)
    ctx["client"].cookies.clear()
    # Next-window code so it isn't a replay of the setup window.
    r = ctx["client"].post("/auth/login",
                           data={"username": BOOTSTRAP_USERNAME,
                                 "code": _code_for(secret, 1)})
    assert r.status_code == 303
    assert COOKIE in r.cookies


def test_login_unknown_user_rejected(ctx):
    _complete_first_setup(ctx)
    ctx["client"].cookies.clear()
    r = ctx["client"].post("/auth/login",
                           data={"username": "nobody", "code": "123456"})
    assert r.status_code == 401
    assert COOKIE not in r.cookies


# ---------------------------------------------------------------- cap-011/012

def test_verify_with_and_without_session(ctx):
    _user, _secret, token = _complete_first_setup(ctx)
    # Valid session for an app path -> 200 with headers.
    r = ctx["client"].get("/auth/verify",
                          headers={"X-Forwarded-Uri": "/apps/my_app/page"},
                          cookies={COOKIE: token})
    assert r.status_code == 200
    assert r.headers["X-Latarnia-Is-Super"] == "true"
    assert r.headers["X-Latarnia-App-Role"] == "full"  # superuser
    assert r.headers["X-Latarnia-User"]
    # No cookie -> 401.
    ctx["client"].cookies.clear()
    assert ctx["client"].get("/auth/verify").status_code == 401


def test_logout_invalidates_session(ctx):
    _user, _secret, token = _complete_first_setup(ctx)
    assert ctx["client"].get("/auth/verify", cookies={COOKIE: token}).status_code == 200
    ctx["client"].request("DELETE", "/auth/session", cookies={COOKIE: token})
    ctx["client"].cookies.clear()
    assert ctx["client"].get("/auth/verify", cookies={COOKIE: token}).status_code == 401


# -------------------------------------------------------------------- cap-024

def test_user_management_lifecycle(ctx):
    client, users, totp = ctx["client"], ctx["users"], ctx["totp"]
    admin, _secret, admin_token = _complete_first_setup(ctx)

    # Superuser invites a new user -> setup_url with token.
    client.cookies.clear()
    r = client.post("/api/auth/users", json={"username": "bob"},
                    cookies={COOKIE: admin_token})
    assert r.status_code == 200
    setup_url = r.json()["setup_url"]
    token = setup_url.split("token=")[1]

    # List shows both users (superuser only).
    client.cookies.clear()
    r = client.get("/api/auth/users", cookies={COOKIE: admin_token})
    assert r.status_code == 200
    names = {u["username"] for u in r.json()["users"]}
    assert {"admin", "bob"} <= names

    # Bob completes enrollment via the setup token.
    client.cookies.clear()
    r = client.get(f"/auth/setup?token={token}")
    assert r.status_code == 200 and "Confirm Setup" in r.text
    bob = users.get_user_by_username("bob")
    bob_secret = totp.get_existing_secret(bob["id"])
    r = client.post(f"/auth/setup?token={token}",
                    data={"code": pyotp.TOTP(bob_secret).now()})
    assert r.status_code == 303
    bob_token = r.cookies[COOKIE]

    # Setup token is single-use.
    client.cookies.clear()
    assert client.get(f"/auth/setup?token={token}").status_code == 400

    # Non-superuser (bob) cannot manage users.
    client.cookies.clear()
    r = client.post("/api/auth/users", json={"username": "carol"},
                    cookies={COOKIE: bob_token})
    assert r.status_code == 403

    # Superuser cannot deactivate themselves.
    client.cookies.clear()
    r = client.request("DELETE", f"/api/auth/users/{admin['id']}",
                       cookies={COOKIE: admin_token})
    assert r.status_code == 403

    # Superuser deactivates bob -> his sessions are invalidated.
    client.cookies.clear()
    r = client.request("DELETE", f"/api/auth/users/{bob['id']}",
                       cookies={COOKIE: admin_token})
    assert r.status_code == 200
    client.cookies.clear()
    assert client.get("/auth/verify", cookies={COOKIE: bob_token}).status_code == 401
