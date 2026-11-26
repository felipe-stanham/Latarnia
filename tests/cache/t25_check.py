"""T-0025 acceptance checks (docs/Tasks/T-0025.md), independent of tests/integration.

A Machine Token minted by a non-superuser must not carry, on any App, a role above the
caller's own role there. Real local dev Postgres (scratch DB, dropped on exit) + a minimal
FastAPI app wiring the real auth router and JWT middleware. Roles are granted through the
real superuser endpoint POST /api/auth/roles/{app}; "no token created" is checked both via
GET /api/auth/tokens and by counting rows in machine_tokens directly.

Usage: ENV=dev PYTHONPATH=src python tests/cache/t25_check.py <001|002|003|004|005|006>
Exit 0 = PASS, 1 = FAIL, 2 = refused/usage/blocked (no Postgres).
"""
import os
import sys

if os.environ.get("ENV", "dev") == "prod":
    print("ENV=prod refused", file=sys.stderr)
    sys.exit(2)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from latarnia.auth import AuthDB  # noqa: E402
from latarnia.auth.jwt_auth import JWTAuth  # noqa: E402
from latarnia.auth.middleware import JWTAuthMiddleware  # noqa: E402
from latarnia.auth.providers import TOTPAuthProvider  # noqa: E402
from latarnia.auth.roles import RoleStore  # noqa: E402
from latarnia.auth.routes import build_auth_router  # noqa: E402
from latarnia.auth.sessions import SessionStore  # noqa: E402
from latarnia.auth.tokens import MachineTokenStore  # noqa: E402
from latarnia.auth.users import UserStore  # noqa: E402
from latarnia.core.config import ConfigManager  # noqa: E402
from latarnia.core.pg_client import PgClient  # noqa: E402

COOKIE = "latarnia_session"
DB_NAME = "latarnia_platform_t25_check"
FULL_MSG = "Only a superuser can issue a token with the 'full' role"


class Env:
    def __init__(self):
        cfg = ConfigManager()
        cfg.load_config()
        if getattr(cfg, "env", os.environ.get("ENV", "dev")) == "prod":
            raise SystemExit(2)
        self.pg = PgClient(cfg)
        if not self.pg.check_connectivity():
            print("BLOCKED: local dev Postgres not reachable")
            raise SystemExit(2)
        self.db = AuthDB(cfg, self.pg)
        self.db.db_name = DB_NAME
        if self.pg.database_exists(DB_NAME):
            self.pg.drop_database(DB_NAME)
        assert self.db.initialize()
        self.users = UserStore(self.db)
        self.sessions = SessionStore(self.db, cfg)
        roles = RoleStore(self.db, self.users)
        totp = TOTPAuthProvider(self.db, lambda: b"x" * 32, issuer="T25")
        self.jwt = JWTAuth(lambda: "t25-check-jwt-secret-0123456789-abcdef")
        tokens = MachineTokenStore(self.db, self.jwt)
        app = FastAPI()
        app.add_middleware(JWTAuthMiddleware, jwt_auth=self.jwt, token_store=tokens,
                           session_store=self.sessions, cookie_name=COOKIE)
        app.include_router(build_auth_router(self.db, self.users, self.sessions, totp, cfg,
                                             role_store=roles, jwt_auth=self.jwt,
                                             token_store=tokens))
        self.client = TestClient(app, follow_redirects=False)
        self.admin, self.admin_sess = self.user("t25admin", True)

    def close(self):
        self.pg.drop_database(DB_NAME)

    def user(self, name, su=False):
        u, _ = self.users.create_user(name, is_superuser=su)
        self.users.activate_user(u["id"])
        return u, self.sessions.create_session(u["id"])

    def grant(self, user, app, role):
        self.client.cookies.clear()
        r = self.client.post(f"/api/auth/roles/{app}", json={"role": role, "user_id": str(user["id"])},
                             cookies={COOKIE: self.admin_sess})
        assert r.status_code == 200, f"grant {role} on {app} failed: {r.status_code} {r.text}"

    def mint(self, sess, scope, label="x"):
        self.client.cookies.clear()
        r = self.client.post("/api/auth/tokens", json={"label": label, "app_scope": scope},
                             cookies={COOKIE: sess})
        try:
            body = r.json()
        except Exception:
            body = r.text
        print(f"  POST /api/auth/tokens scope={scope} -> {r.status_code} {body if r.status_code != 200 else '{token: <jwt>}'}")
        return r.status_code, body

    def listed(self, sess):
        self.client.cookies.clear()
        return self.client.get("/api/auth/tokens", cookies={COOKIE: sess}).json()["tokens"]

    def rows(self, user):
        return self.db.query_one("SELECT COUNT(*) AS n FROM machine_tokens WHERE user_id = %s",
                                 (user["id"],))["n"]


def check(ok, msg):
    print(f"  {'ok  ' if ok else 'FAIL'} {msg}")
    return ok


def t001(e):
    bob, s = e.user("bob")
    code, body = e.mint(s, {"app_a": "webUI-full"})
    detail = body.get("detail", "") if isinstance(body, dict) else str(body)
    ok = check(code == 403, f"status 403 (got {code})")
    ok &= check("app_a" in detail and "none" in detail, f"detail names app_a and none: {detail!r}")
    ok &= check(e.listed(s) == [], "GET /api/auth/tokens lists no token")
    ok &= check(e.rows(bob) == 0, f"machine_tokens rows for user = 0 (got {e.rows(bob)})")
    return ok


def t002(e):
    bob, s = e.user("bob")
    e.grant(bob, "app_a", "webUI-med")
    ok = True
    for role, want in (("webUI-med", 200), ("webUI-low", 200), ("webUI-full", 403)):
        code, body = e.mint(s, {"app_a": role})
        ok &= check(code == want, f"{role} -> {want} (got {code})")
        if code == 200:
            claims = e.jwt.validate(body["token"]) or {}
            ok &= check(claims.get("apps") == {"app_a": role} and not claims.get("super"),
                        f"token claims apps={claims.get('apps')} super={claims.get('super')}")
    ok &= check(len(e.listed(s)) == 2 and e.rows(bob) == 2, f"exactly 2 tokens exist (rows={e.rows(bob)})")
    return ok


def t003(e):
    bob, s = e.user("bob")
    e.grant(bob, "app_a", "webUI-med")
    code, body = e.mint(s, {"app_a": "webUI-low", "app_b": "webUI-low"})
    ok = check(code == 403, f"status 403 (got {code})")
    ok &= check(isinstance(body, dict) and "app_b" in body.get("detail", ""), f"detail names app_b: {body}")
    ok &= check(e.listed(s) == [] and e.rows(bob) == 0, f"no token created (rows={e.rows(bob)})")
    # Order independence: over-cap entry first.
    code, body = e.mint(s, {"app_b": "webUI-low", "app_a": "webUI-low"})
    ok &= check(code == 403 and e.rows(bob) == 0, f"reversed order -> 403, no token (got {code}, rows={e.rows(bob)})")
    return ok


def t004(e):
    bob, s = e.user("bob")
    e.grant(bob, "app_a", "full")
    code, body = e.mint(s, {"app_a": "full"})
    ok = check(code == 403, f"status 403 (got {code})")
    ok &= check(isinstance(body, dict) and body.get("detail") == FULL_MSG, f"detail == {FULL_MSG!r}")
    ok &= check(e.rows(bob) == 0, "no token created")
    # Control: the same user may still mint up to webUI-full on app_a.
    code, _ = e.mint(s, {"app_a": "webUI-full"})
    ok &= check(code == 200, f"control: webUI-full under role full -> 200 (got {code})")
    return ok


def t005(e):
    code, body = e.mint(e.admin_sess, {"app_a": "webUI-full", "app_b": "full"})
    ok = check(code == 200 and "token" in body, f"superuser -> 200 with token (got {code})")
    if code == 200:
        claims = e.jwt.validate(body["token"]) or {}
        ok &= check(claims.get("apps") == {"app_a": "webUI-full", "app_b": "full"} and claims.get("super") is True,
                    f"claims apps={claims.get('apps')} super={claims.get('super')}")
    return ok


def t006(e):
    bob, s = e.user("bob")
    code, _ = e.mint(s, {"unknown_app": "webUI-low"})
    ok = check(code == 403, f"unknown_app -> 403 (got {code})")
    ok &= check(e.rows(bob) == 0, "no token created for unknown_app")
    code, body = e.mint(s, {})
    ok &= check(code == 200 and "token" in body, f"empty scope -> 200 with token (got {code})")
    return ok


def t007diff(base_ref):
    """Static half of t25-007: every pre-existing test in test_token_flow.py is unchanged
    (AST-equal to BASE) except test_token_scope_enforced, whose BASE statements must all
    still be there in order with only non-assert statements added (assertions unchanged)."""
    import ast
    import subprocess
    path = "tests/integration/test_token_flow.py"
    base_src = subprocess.run(["git", "show", f"{base_ref}:{path}"], check=True,
                              capture_output=True, text=True).stdout
    cur_src = open(path).read()

    def funcs(src):
        return {n.name: n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)}

    base, cur = funcs(base_src), funcs(cur_src)
    ok = True
    for name, node in base.items():
        if name not in cur:
            ok &= check(False, f"{name} removed")
            continue
        if name == "test_token_scope_enforced":
            b = [ast.dump(s) for s in node.body]
            c = [ast.dump(s) for s in cur[name].body]
            it = iter(c)
            subseq = all(s in it for s in b)
            added = [s for s in cur[name].body if ast.dump(s) not in b]
            b_asserts = [ast.dump(s) for s in node.body if isinstance(s, ast.Assert)]
            c_asserts = [ast.dump(s) for s in cur[name].body if isinstance(s, ast.Assert)]
            ok &= check(subseq, f"{name}: all original statements kept, in order")
            ok &= check(b_asserts == c_asserts, f"{name}: assertions identical ({len(c_asserts)})")
            ok &= check(not any(isinstance(s, ast.Assert) for s in added),
                        f"{name}: added only setup lines: {[ast.unparse(s) for s in added]}")
        elif name == "ctx":
            print(f"  info ctx fixture {'unchanged' if ast.dump(node) == ast.dump(cur[name]) else 'changed (yield exposes roles)'}")
        else:
            ok &= check(ast.dump(node) == ast.dump(cur[name]), f"{name} unchanged")
    print(f"  info new functions: {sorted(set(cur) - set(base))}")
    return ok


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "007diff":
        ok = t007diff(sys.argv[2] if len(sys.argv) > 2 else "dev")
        print(f"{'PASS' if ok else 'FAIL'} t25-007 (static: existing tests unchanged)")
        return 0 if ok else 1
    if len(sys.argv) != 2 or f"t{sys.argv[1]}" not in globals():
        print(__doc__)
        return 2
    n = sys.argv[1]
    e = Env()
    try:
        ok = globals()[f"t{n}"](e)
    except AssertionError as exc:
        print(f"  FAIL {exc}")
        ok = False
    finally:
        e.close()
    print(f"{'PASS' if ok else 'FAIL'} t25-{n}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
