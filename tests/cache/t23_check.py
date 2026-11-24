"""T-0023 acceptance checks (independent of tests/unit + tests/integration).

Usage: t23_check.py <001..013|016|016b>   (run from repo root, PYTHONPATH=src, ENV=dev)
001-005 use a throwaway Postgres DB (dropped afterwards); 006-013 are in-process
with mocks; 016/016b generate the Caddyfile and run `caddy adapt` on it.
"""
import asyncio
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

if os.environ.get("ENV", "dev") == "prod":
    sys.exit("ENV=prod refused")
os.environ["ENV"] = "dev"

ROOT = Path(__file__).resolve().parents[2]
XL = re.compile(r"^x-latarnia-", re.I)


def xl(headers):
    return {k.lower(): v for k, v in headers.items() if XL.match(k)}


# --------------------------------------------------------------- verify (001-005)

def _auth_ctx():
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

    cfg = ConfigManager()
    cfg.load_config()
    pg = PgClient(cfg)
    assert pg.check_connectivity(), "Postgres not reachable (dev)"
    db = AuthDB(cfg, pg)
    db.db_name = "latarnia_platform_test_t23cache"
    if pg.database_exists(db.db_name):
        pg.drop_database(db.db_name)
    assert db.initialize()
    users, sessions = UserStore(db), SessionStore(db, cfg)
    roles = RoleStore(db, users)
    key = os.urandom(32)
    app = FastAPI()
    app.include_router(build_auth_router(db, users, sessions,
                                         TOTPAuthProvider(db, lambda: key, issuer="T"),
                                         cfg, role_store=roles))
    return SimpleNamespace(client=TestClient(app, follow_redirects=False), users=users,
                           sessions=sessions, roles=roles, pg=pg, db=db,
                           cookie=cfg.config.auth.cookie_name)


def _with_auth(fn):
    ctx = _auth_ctx()
    try:
        fn(ctx)
    finally:
        ctx.pg.drop_database(ctx.db.db_name)


def _member(ctx, name):
    u, _ = ctx.users.create_user(name)
    ctx.users.activate_user(u["id"])
    return u, ctx.sessions.create_session(u["id"])


def _verify(ctx, token=None, headers=None):
    ctx.client.cookies.clear()
    cookies = {ctx.cookie: token} if token else None
    return ctx.client.get("/auth/verify", headers=headers or {}, cookies=cookies)


def t001():
    def run(ctx):
        _u, tok = _member(ctx, "t23none")
        r = _verify(ctx, tok, {"X-Latarnia-App": "my_app"})
        assert r.status_code == 403, r.status_code
        assert r.text == "No access to this app", r.text
        assert xl(r.headers) == {}, xl(r.headers)
        print("t23-001 observed:", r.status_code, repr(r.text), xl(r.headers))
    _with_auth(run)


def t002():
    def run(ctx):
        u, tok = _member(ctx, "t23low")
        assert _verify(ctx, tok, {"X-Latarnia-App": "my_app"}).status_code == 403
        ctx.roles.set_role(u["id"], "my_app", "webUI-low", granted_by=u["id"])
        r = _verify(ctx, tok, {"X-Latarnia-App": "my_app"})
        assert r.status_code == 200, r.status_code
        assert r.headers["X-Latarnia-App-Role"] == "webUI-low"
        assert r.headers["X-Latarnia-Is-Super"] == "false"
        assert r.headers["X-Latarnia-User"] == str(u["id"])
        print("t23-002 observed:", r.status_code, xl(r.headers))
    _with_auth(run)


def t003():
    def run(ctx):
        su = ctx.users.get_or_create_bootstrap_superuser()
        ctx.users.activate_user(su["id"])
        tok = ctx.sessions.create_session(su["id"])
        r = _verify(ctx, tok, {"X-Latarnia-App": "my_app"})
        assert r.status_code == 200, r.status_code
        assert r.headers["X-Latarnia-App-Role"] == "full"
        assert r.headers["X-Latarnia-Is-Super"] == "true"
        print("t23-003 observed:", r.status_code, xl(r.headers))
    _with_auth(run)


def t004():
    def run(ctx):
        _u, tok = _member(ctx, "t23none4")
        r1 = _verify(ctx, tok)
        r2 = _verify(ctx, tok, {"X-Forwarded-Uri": "/apps/my_app/page"})
        assert r1.status_code == 200, r1.status_code
        assert r2.status_code == 200, r2.status_code
        print("t23-004 observed: no header", r1.status_code, "| X-Forwarded-Uri only", r2.status_code,
              xl(r2.headers))
    _with_auth(run)


def t005():
    def run(ctx):
        r = _verify(ctx)
        r2 = _verify(ctx, None, {"X-Latarnia-App": "my_app"})
        assert r.status_code == 401 and r2.status_code == 401, (r.status_code, r2.status_code)
        print("t23-005 observed:", r.status_code, r2.status_code)
    _with_auth(run)


# --------------------------------------------------------------- UI API proxy (006-011)

def _proxy(session_user=None, role="none"):
    import latarnia.main as main
    from fastapi import HTTPException  # noqa: F401
    from latarnia.web import ui_renderer as rmod

    def entry(name):
        e = MagicMock()
        e.name, e.app_id, e.type = name, name, "service"
        e.runtime_info.assigned_port = 8199
        return e
    apps = {n: entry(n) for n in ("app_a", "app_b")}
    reg = MagicMock()
    reg.get_app.side_effect = apps.get
    main.app_manager = SimpleNamespace(registry=reg)
    main._session_user = lambda request: session_user
    rs = MagicMock()
    rs.get_role.return_value = role
    main.role_store = rs
    r = rmod.ui_renderer
    r.discover_ui_resources = AsyncMock(return_value=["items"])
    r.fetch_resource_list = AsyncMock(return_value=[{"id": 1}])
    r.fetch_resource_detail = AsyncMock(return_value={"id": 1})
    return main, r


def _req(claims=None):
    return SimpleNamespace(state=SimpleNamespace(jwt_claims=claims))


def _calls(main, app_id, req):
    return {
        "resources": lambda: main.get_app_ui_resources(app_id, req),
        "items": lambda: main.get_app_ui_resource(app_id, "items", req),
        "items/1": lambda: main.get_app_ui_resource_detail(app_id, "items", "1", req),
    }


def _expect_denied(main, r, app_id, req, status, detail):
    from fastapi import HTTPException
    for label, call in _calls(main, app_id, req).items():
        try:
            asyncio.run(call())
        except HTTPException as e:
            assert (e.status_code, e.detail) == (status, detail), (label, e.status_code, e.detail)
            print(f"  {label}: {e.status_code} {e.detail!r}")
        else:
            raise AssertionError(f"{label}: not denied")
    for m in (r.discover_ui_resources, r.fetch_resource_list, r.fetch_resource_detail):
        m.assert_not_called()
    print("  ui_renderer never called")


def t006():
    main, r = _proxy({"id": "u9", "is_superuser": False}, "none")
    print("t23-006:")
    _expect_denied(main, r, "app_a", _req(), 403, "No access to this app")


def t007():
    main, r = _proxy()
    print("t23-007:")
    _expect_denied(main, r, "app_a", _req({"sub": "u1", "super": False, "apps": {"app_b": "full"}}),
                   403, "Token not scoped to this app")


def t008():
    main, r = _proxy()
    res = asyncio.run(main.get_app_ui_resource(
        "app_a", "items", _req({"sub": "u1", "super": False, "apps": {"app_a": "webUI-low"}})))
    hdrs = r.fetch_resource_list.await_args.kwargs["headers"]
    assert hdrs == {"X-Latarnia-User": "u1", "X-Latarnia-App-Role": "webUI-low",
                    "X-Latarnia-Is-Super": "false"}, hdrs
    print("t23-008 observed: result", res, "headers", hdrs)


def t009():
    main, r = _proxy()
    res = asyncio.run(main.get_app_ui_resource(
        "app_a", "items", _req({"sub": "root", "super": True, "apps": {}})))
    hdrs = r.fetch_resource_list.await_args.kwargs["headers"]
    assert hdrs["X-Latarnia-App-Role"] == "full" and hdrs["X-Latarnia-Is-Super"] == "true", hdrs
    print("t23-009 observed: result", res, "headers", hdrs)


def t010():
    main, r = _proxy({"id": "u7", "is_superuser": False}, "webUI-med")
    res = asyncio.run(main.get_app_ui_resources("app_a", _req()))
    hdrs = r.discover_ui_resources.await_args.kwargs["headers"]
    assert hdrs == {"X-Latarnia-User": "u7", "X-Latarnia-App-Role": "webUI-med",
                    "X-Latarnia-Is-Super": "false"}, hdrs
    main.role_store.get_role.assert_called_once_with("u7", "app_a")
    print("t23-010 observed: result", res, "headers", hdrs)


def t011():
    from fastapi import HTTPException
    for caller in (_req({"sub": "root", "super": True, "apps": {}}), _req()):
        main, r = _proxy({"id": "u7", "is_superuser": False}, "full")
        try:
            asyncio.run(main.get_app_ui_resource("missing", "items", caller))
        except HTTPException as e:
            assert (e.status_code, e.detail) == (404, "App missing not found"), (e.status_code, e.detail)
        else:
            raise AssertionError("no 404")
        r.fetch_resource_list.assert_not_called()
    print("t23-011 observed: 404 'App missing not found' for token and session callers")


# --------------------------------------------------------------- ui_renderer (012)

def t012():
    import httpx
    import latarnia.web.ui_renderer as rmod
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=[{"id": 1}])
    real = httpx.AsyncClient
    rmod.httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    try:
        r = rmod.UIRenderer()
        out = asyncio.run(r.fetch_resource_list("http://app", "items", headers={"X-Latarnia-User": "u1"}))
        assert out == [{"id": 1}], out
        assert xl(seen[-1].headers) == {"x-latarnia-user": "u1"}, xl(seen[-1].headers)
        asyncio.run(r.fetch_resource_list("http://app", "items"))
        assert xl(seen[-1].headers) == {}, xl(seen[-1].headers)
    finally:
        rmod.httpx.AsyncClient = real
    print("t23-012 observed: with headers", xl(seen[0].headers), "| without", xl(seen[1].headers))


# --------------------------------------------------------------- example_full_app (013)

def t013():
    from fastapi.testclient import TestClient
    p = ROOT / "examples" / "example_full_app" / "app.py"
    spec = importlib.util.spec_from_file_location("efa_t23cache", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    c = TestClient(mod.rest_app)
    ui = c.get("/ui").json()
    assert "caller" in ui, ui
    a = c.get("/api/caller", headers={"X-Latarnia-User": "u1", "X-Latarnia-App-Role": "webUI-med",
                                      "X-Latarnia-Is-Super": "false"}).json()
    assert a == [{"id": 1, "user": "u1", "role": "webUI-med", "is_super": "false"}], a
    b = c.get("/api/caller").json()
    assert b == [{"id": 1, "user": None, "role": None, "is_super": None}], b
    print("t23-013 observed: /ui", ui, "| with headers", a, "| without", b)


# --------------------------------------------------------------- Caddyfile (016, 016b)

def _generate(apps_spec):
    from latarnia.caddy.manager import CaddyConfigManager
    from latarnia.core.config import ConfigManager
    tmp = Path(tempfile.mkdtemp(prefix="t23cache_"))
    cfg = ConfigManager()
    cfg.load_config()
    cfg.config.process_manager.data_dir = str(tmp / "data")
    apps = [SimpleNamespace(name=n, type="service",
                            manifest=SimpleNamespace(config=SimpleNamespace(has_web_ui=True,
                                                                            public_routes=pub)),
                            runtime_info=SimpleNamespace(assigned_port=port))
            for n, port, pub in apps_spec]
    mgr = CaddyConfigManager(cfg, SimpleNamespace(registry=SimpleNamespace(get_all_apps=lambda: apps)))
    text = mgr.generate_config()
    return text, mgr._caddyfile_path(), tmp


def _block(text, opener):
    """Body of the brace block opened by the line whose stripped text == opener."""
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == opener)
    depth, out = 0, []
    for ln in lines[start:]:
        depth += ln.count("{") - ln.count("}")
        out.append(ln.strip())
        if depth == 0:
            return out[1:-1]
    raise AssertionError(f"unclosed {opener}")


def _sub_block(body, prefix):
    start = next(i for i, ln in enumerate(body) if ln.startswith(prefix) and ln.endswith("{"))
    depth, out = 0, []
    for ln in body[start:]:
        depth += ln.count("{") - ln.count("}")
        out.append(ln)
        if depth == 0:
            return out[1:-1]
    raise AssertionError(prefix)


def _adapt(path):
    caddy = shutil.which("caddy")
    assert caddy, "caddy not on PATH — adapt check cannot run"
    p = subprocess.run([caddy, "adapt", "--adapter", "caddyfile", "--config", str(path)],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def t016():
    text, path, tmp = _generate([("a", 8151, []), ("b", 8152, [])])
    try:
        fa_a = _sub_block(_block(text, "handle_path /apps/a/* {"), "forward_auth ")
        fa_b = _sub_block(_block(text, "handle_path /apps/b/* {"), "forward_auth ")
        fa_all = _sub_block(_block(text, "handle /* {"), "forward_auth ")
        assert "header_up X-Latarnia-App a" in fa_a and not any(l.endswith(" b") and "X-Latarnia-App " in l for l in fa_a), fa_a
        assert "header_up X-Latarnia-App b" in fa_b and "header_up X-Latarnia-App a" not in fa_b, fa_b
        assert "header_up -X-Latarnia-App" in fa_all, fa_all
        assert "X-Forwarded-Uri" not in text
        adapted = json.dumps(_adapt(path))
        # The adapted JSON sets X-Latarnia-App to the App name on the verify request.
        assert '"X-Latarnia-App": ["a"]' in adapted and '"X-Latarnia-App": ["b"]' in adapted, \
            "adapted JSON lacks X-Latarnia-App set"
        print("t23-016 observed: fa(a)", [l for l in fa_a if "X-Latarnia-App" in l],
              "fa(b)", [l for l in fa_b if "X-Latarnia-App" in l],
              "catch-all", [l for l in fa_all if "X-Latarnia-App" in l],
              "| no X-Forwarded-Uri | caddy adapt rc=0")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t016b():
    text, path, tmp = _generate([("a", 8151, ["/b/"]), ("b", 8152, ["/pub/"])])
    try:
        for name, port in (("a", 8151), ("b", 8152)):
            rp = _sub_block(_block(text, f"handle_path /apps/{name}/* {{"), f"reverse_proxy localhost:{port}")
            for h in ("X-Latarnia-User", "X-Latarnia-App-Role", "X-Latarnia-Is-Super"):
                assert any(l.startswith(f"header_up {h} ") for l in rp), (name, h, rp)
            assert "header_up -X-Latarnia-App" in rp, rp
            print(f"t23-016b observed: protected {name} reverse_proxy:", rp)
        for opener in ("handle /apps/a/b/* {", "handle /apps/b/pub/* {"):
            rp = _sub_block(_block(text, opener), "reverse_proxy ")
            assert "header_up -X-Latarnia-App" in rp, (opener, rp)
            print(f"t23-016b observed: public {opener}", rp)
        _adapt(path)
        print("t23-016b: caddy adapt rc=0")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    name = sys.argv[1]
    globals()[f"t{name}"]()
    print(f"PASS t23-{name}")
