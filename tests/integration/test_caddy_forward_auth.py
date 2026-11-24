"""Real-chain auth tests (T-0023): the real `caddy` binary in front of the real
auth router and stub Apps.

Why this module exists: every earlier `/auth/verify` test set the forwarded
headers by hand and never went through Caddy, so a Caddy-side bug (P-0008:
`handle_path` strips `/apps/{name}` before `forward_auth` runs, so verify could
not tell which App was asked) went unnoticed. Here every request goes through
Caddy, with the Caddyfile produced by `CaddyConfigManager`, and the tests assert
what verify was sent, what it decided, and what each App received.

Design (c): Caddy names the App. The `forward_auth` block generated for
`/apps/{name}/*` sends `X-Latarnia-App: {name}`; the catch-all deletes any
client-sent `X-Latarnia-App`; verify reads only that header. The rule pinned
here: whenever a stub App receives a request, verify was asked about that same
App — whatever the request-target form (dot segments, case, percent-encoding,
merged slashes, absolute-form) and whatever the client sent. And the identity
headers verify returned reach the App even when the client names them in
`Connection`: the App's `reverse_proxy` re-sets them after its hop-by-hop cleanup.
`X-Latarnia-App` is a Caddy → verify header: forward_auth's `header_up` only
affects the request to /auth/verify, so each App proxy (protected and
public-route) deletes it — a client-sent copy never reaches an App.

What the test changes in the generated Caddyfile (nothing else):
  - the site address line, `localhost {` -> `http://:<port> {` (plain HTTP/1.1,
    no host matcher, so absolute-form targets naming `localhost` or
    `127.0.0.1` reach the site). The generator has no plain-HTTP site address
    (dev is always `localhost`, which Caddy serves over HTTPS on :443);
  - a global options block is prepended (admin API off, no automatic HTTPS,
    loopback-only listeners). In production the generated file is an include,
    and global options live in the system Caddyfile the same way.
Everything inside the site block is the generator's output, unchanged.

Skipped as a whole when `caddy` is not on PATH (loud reason) or when Postgres
is unreachable.
"""
import http.client
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

CADDY = shutil.which("caddy")
if CADDY is None:
    pytest.skip(
        "caddy binary not installed — real-chain auth tests NOT run "
        "(install caddy and re-run tests/integration/test_caddy_forward_auth.py)",
        allow_module_level=True,
    )

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from latarnia.auth import AuthDB  # noqa: E402
from latarnia.auth.providers import TOTPAuthProvider  # noqa: E402
from latarnia.auth.roles import RoleStore  # noqa: E402
from latarnia.auth.routes import build_auth_router  # noqa: E402
from latarnia.auth.sessions import SessionStore  # noqa: E402
from latarnia.auth.users import UserStore  # noqa: E402
from latarnia.caddy.manager import CaddyConfigManager  # noqa: E402
from latarnia.core.config import ConfigManager  # noqa: E402
from latarnia.core.pg_client import PgClient  # noqa: E402

TEST_DB = "latarnia_platform_test_caddy_chain"
APPS = ("app_a", "app_b")
# Public routes in the generated Caddyfile (no forward_auth; t23-026).
PUBLIC_ROUTES = {"app_a": ["/pub/"]}
IDENTITY_HEADERS = ("X-Latarnia-User", "X-Latarnia-App-Role", "X-Latarnia-Is-Super")
GENERATED_SITE_LINE = "localhost {"

GLOBAL_OPTIONS = """{
\tadmin off
\tauto_https off
\tdefault_bind 127.0.0.1
}

"""


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ThreadedServer:
    """An ASGI app under uvicorn on a background thread, on a pre-bound port."""

    def __init__(self, app):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(
            app, lifespan="off", log_config=None, access_log=False))
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self.sock]}, daemon=True)

    def start(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError(f"uvicorn on port {self.port} did not start")
            time.sleep(0.02)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.sock.close()


def _header_lists(scope) -> dict:
    """ASGI headers as {lower-case name: [values]} — duplicates stay visible."""
    out = {}
    for k, v in scope["headers"]:
        out.setdefault(k.decode("latin-1").lower(), []).append(v.decode("latin-1"))
    return out


def _stub_app(seen: list):
    """A stand-in App: records every request it receives, answers 200."""
    async def app(scope, receive, send):
        assert scope["type"] == "http"
        seen.append({
            "path": scope["path"],
            "raw_path": scope.get("raw_path", b"").decode("latin-1"),
            "headers": _header_lists(scope),
        })
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"stub app page"})
    return app


def _recording_verify(app, calls: list):
    """Wrap the auth app: record what every call to /auth/verify received —
    the `X-Latarnia-App` values (the App verify decides for) and, for
    diagnostics only, `X-Forwarded-Uri` (verify does not read it)."""
    async def wrapper(scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/auth/verify":
            headers = _header_lists(scope)
            calls.append({"app": headers.get("x-latarnia-app", []),
                          "uri": headers.get("x-forwarded-uri", [])})
        await app(scope, receive, send)
    return wrapper


def _build_caddyfile(tmp_dir: Path, auth_port: int, app_ports: dict, caddy_port: int):
    """Generate the include with CaddyConfigManager; adapt only the site address.

    Requires ENV=dev (the generator then emits the `localhost` site address).
    Returns (path, text).
    """
    cfg = ConfigManager()
    cfg.load_config()
    cfg.config.process_manager.data_dir = str(tmp_dir / "data")
    cfg.config.system.main_port = auth_port
    apps = [
        SimpleNamespace(
            name=name, type="service",
            manifest=SimpleNamespace(config=SimpleNamespace(
                has_web_ui=True, public_routes=list(PUBLIC_ROUTES.get(name, [])))),
            runtime_info=SimpleNamespace(assigned_port=port),
        )
        for name, port in app_ports.items()
    ]
    registry = SimpleNamespace(get_all_apps=lambda: apps)
    generated = CaddyConfigManager(cfg, SimpleNamespace(registry=registry)).generate_config()

    lines = generated.splitlines()
    assert lines[1] == GENERATED_SITE_LINE, f"unexpected generated site line: {lines[1]!r}"
    lines[1] = f"http://:{caddy_port} {{"
    text = GLOBAL_OPTIONS + "\n".join(lines) + "\n"
    path = tmp_dir / "Caddyfile"
    path.write_text(text)
    return path, text


def _get(port: int, path: str, cookie: str = None, headers: dict = None,
         cookie_name: str = "latarnia_session"):
    """HTTP/1.1 GET; the request target goes out as written."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        hdrs = dict(headers or {})
        if cookie:
            hdrs["Cookie"] = f"{cookie_name}={cookie}"
        conn.request("GET", path, headers=hdrs)
        resp = conn.getresponse()
        return SimpleNamespace(status=resp.status, headers=resp.headers,
                               body=resp.read().decode("utf-8", errors="replace"))
    finally:
        conn.close()


_STATUS_LINE = re.compile(rb"HTTP/\d(?:\.\d)? (\d{3})[^\r\n]*\r\n")


def _raw_get(c, target: str, cookie: str, what: str, header_lines=(),
             connection: str = "close"):
    """`GET <target> HTTP/1.1` over a raw socket: the exact bytes reach Caddy —
    the request-target (`..`, `.`, `%xx`, `//`, case, absolute-form), extra
    header lines (name case and duplicates kept) and the `Connection` value
    (it must contain `close`: the response is read until Caddy closes).

    No response, or one without an HTTP status line, fails the test with the
    raw bytes and what verify and the Apps observed (`what` labels the request).
    """
    request = "\r\n".join([
        f"GET {target} HTTP/1.1",
        f"Host: 127.0.0.1:{c.port}",
        f"Cookie: {c.cookie_name}={cookie}",
        f"Connection: {connection}",
        *header_lines,
    ]) + "\r\n\r\n"
    data = b""
    try:
        with socket.create_connection(("127.0.0.1", c.port), timeout=10) as sock:
            sock.sendall(request.encode("latin-1"))
            while chunk := sock.recv(65536):
                data += chunk
    except OSError as exc:
        pytest.fail(f"socket error {exc!r}, raw bytes so far {data[:2000]!r}; "
                    + _observed(c, what, "<no response>"))
    match = _STATUS_LINE.match(data)
    if match is None:
        pytest.fail(f"no HTTP status line, raw response {data[:2000]!r}; "
                    + _observed(c, what, "<no status line>"))
    return SimpleNamespace(status=int(match.group(1)),
                           status_line=match.group(0).decode("latin-1").rstrip("\r\n"))


def _start_caddy(caddyfile: Path, tmp_dir: Path, port: int, stack: ExitStack):
    """`caddy run` with a hermetic HOME/XDG; wait until it proxies /auth/login."""
    home = tmp_dir / "caddy-home"
    home.mkdir()
    env = dict(os.environ, HOME=str(home),
               XDG_CONFIG_HOME=str(home / "config"), XDG_DATA_HOME=str(home / "data"))
    log_path = tmp_dir / "caddy.log"
    log = open(log_path, "w")
    stack.callback(log.close)
    proc = subprocess.Popen(
        [CADDY, "run", "--config", str(caddyfile), "--adapter", "caddyfile"],
        stdout=log, stderr=subprocess.STDOUT, env=env)

    def _stop():
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    stack.callback(_stop)

    deadline = time.monotonic() + 20
    while True:
        if proc.poll() is not None:
            pytest.fail(f"caddy exited with {proc.returncode}:\n{log_path.read_text()}")
        try:
            if _get(port, "/auth/login").status == 200:
                return proc
        except (OSError, http.client.HTTPException):
            pass
        if time.monotonic() > deadline:
            pytest.fail(f"caddy not ready on :{port} after 20s:\n{log_path.read_text()}")
        time.sleep(0.1)


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    """Postgres auth DB + auth router + two stub Apps + caddy, for the module."""
    with ExitStack() as stack:
        mp = pytest.MonkeyPatch()
        stack.callback(mp.undo)
        mp.setenv("ENV", "dev")

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
        stack.callback(pg.drop_database, TEST_DB)

        users = UserStore(db)
        sessions = SessionStore(db, cfg)
        roles = RoleStore(db, users)
        totp_key = os.urandom(32)
        totp = TOTPAuthProvider(db, lambda: totp_key, issuer="Test")
        auth_app = FastAPI()
        auth_app.include_router(build_auth_router(db, users, sessions, totp, cfg,
                                                  role_store=roles))

        verify_calls = []
        servers = {"auth": _ThreadedServer(_recording_verify(auth_app, verify_calls))}
        seen = {name: [] for name in APPS}
        for name in APPS:
            servers[name] = _ThreadedServer(_stub_app(seen[name]))
        for server in servers.values():
            server.start()
            stack.callback(server.stop)

        tmp_dir = tmp_path_factory.mktemp("caddy_chain")
        caddy_port = _free_port()
        caddyfile, text = _build_caddyfile(
            tmp_dir, servers["auth"].port,
            {name: servers[name].port for name in APPS}, caddy_port)
        _start_caddy(caddyfile, tmp_dir, caddy_port, stack)

        yield SimpleNamespace(
            port=caddy_port, users=users, sessions=sessions, roles=roles,
            seen=seen, verify_calls=verify_calls, caddyfile=text,
            cookie_name=cfg.config.auth.cookie_name,
        )


def _clear(chain):
    for log in chain.seen.values():
        log.clear()
    chain.verify_calls.clear()


@pytest.fixture
def c(chain):
    """The chain with per-test request logs cleared."""
    _clear(chain)
    return chain


def _member(c, **app_roles):
    """A fresh active non-superuser with the given per-App roles and a session."""
    user, _ = c.users.create_user(f"chain-{uuid4().hex[:10]}")
    c.users.activate_user(user["id"])
    for app_name, role in app_roles.items():
        c.roles.set_role(user["id"], app_name, role, granted_by=user["id"])
    return user, c.sessions.create_session(user["id"])


def _through_caddy(c, path, token=None, headers=None):
    return _get(c.port, path, token, headers, cookie_name=c.cookie_name)


def _latarnia_headers(resp):
    return [k for k in resp.headers if k.lower().startswith("x-latarnia-")]


def _deliveries(c):
    return {name: [(r["path"], r["headers"].get("x-latarnia-app-role")) for r in log]
            for name, log in c.seen.items()}


def _verify_apps(c):
    """The `X-Latarnia-App` values of each /auth/verify call, in order."""
    return [call["app"] for call in c.verify_calls]


def _observed(c, what, status):
    return (f"{what}: status {status}, verify got {c.verify_calls}, "
            f"deliveries {_deliveries(c)}")


# Sentinel: Caddy answered without calling /auth/verify (e.g. a 400).
NO_VERIFY = "<verify not called>"


def _routed_app(c, observed):
    """The block Caddy routed the last (single) request to, read from what verify
    received: an App name, None for the catch-all (no `X-Latarnia-App`), or
    NO_VERIFY."""
    assert len(c.verify_calls) <= 1, observed
    if not c.verify_calls:
        return NO_VERIFY
    values = c.verify_calls[0]["app"]
    assert len(values) <= 1, observed
    return values[0] if values else None


def _assert_app_reached_only_for_itself(c, observed):
    """The rule under test: whenever a stub App receives a request, verify was
    asked about that same App — never with `X-Latarnia-App` missing or naming
    another App. One request at a time, so at most one App, once."""
    reached = [name for name, log in c.seen.items() for _ in log]
    if reached:
        assert len(reached) == 1, observed
        assert _verify_apps(c) == [[reached[0]]], observed


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_generated_caddyfile_is_what_runs(c):
    # The chain runs the generator's output: every App forward_auth block names
    # its App, the catch-all and every App proxy (protected and public-route)
    # delete the header, and only the site address line was adapted.
    lines = [line.strip() for line in c.caddyfile.splitlines()]
    assert c.caddyfile.count("forward_auth ") == len(APPS) + 1
    for name in APPS:
        assert lines.count(f"header_up X-Latarnia-App {name}") == 1
        for prefix in PUBLIC_ROUTES.get(name, []):
            assert f"handle /apps/{name}{prefix}* {{" in lines
    n_public = sum(len(routes) for routes in PUBLIC_ROUTES.values())
    assert n_public >= 1
    assert lines.count("header_up -X-Latarnia-App") == 1 + len(APPS) + n_public
    # Each App's reverse_proxy re-sets the identity headers (t23-016b).
    for header in IDENTITY_HEADERS:
        assert lines.count(f"header_up {header} {{http.request.header.{header}}}") == len(APPS)
    assert "X-Forwarded-Uri" not in c.caddyfile
    assert GENERATED_SITE_LINE not in c.caddyfile.splitlines()


def test_role_none_is_403_and_app_never_called(c):
    # t23-018
    _user, token = _member(c)
    r = _through_caddy(c, "/apps/app_a/page", token)
    assert r.status == 403, r.body
    assert r.body == "No access to this app"
    assert _latarnia_headers(r) == []
    assert _verify_apps(c) == [["app_a"]]
    assert c.seen == {"app_a": [], "app_b": []}


def test_granted_role_reaches_the_app(c):
    # t23-019: verify is asked about app_a, the role reaches the App, and the
    # App still gets its own root-relative path.
    user, token = _member(c, app_a="webUI-low")
    r = _through_caddy(c, "/apps/app_a/page", token)
    assert r.status == 200, r.body
    assert r.body == "stub app page"
    assert _verify_apps(c) == [["app_a"]]
    (req,) = c.seen["app_a"]
    assert req["path"] == "/page"
    assert req["headers"]["x-latarnia-app-role"] == ["webUI-low"]
    assert req["headers"]["x-latarnia-is-super"] == ["false"]
    assert req["headers"]["x-latarnia-user"] == [str(user["id"])]
    assert c.seen["app_b"] == []

    # A role on app_a does not open app_b.
    _clear(c)
    r = _through_caddy(c, "/apps/app_b/page", token)
    assert r.status == 403
    assert _verify_apps(c) == [["app_b"]]
    assert c.seen == {"app_a": [], "app_b": []}


def test_superuser_reaches_the_app_with_full(c):
    su = c.users.get_or_create_bootstrap_superuser()
    c.users.activate_user(su["id"])
    token = c.sessions.create_session(su["id"])
    r = _through_caddy(c, "/apps/app_b/page", token)
    assert r.status == 200, r.body
    (req,) = c.seen["app_b"]
    assert req["headers"]["x-latarnia-app-role"] == ["full"]
    assert req["headers"]["x-latarnia-is-super"] == ["true"]


def test_no_session_redirects_to_login_with_original_path(c):
    # t23-020
    r = _through_caddy(c, "/apps/app_a/page")
    assert r.status == 302
    assert r.headers["Location"] == "/auth/login?next=/apps/app_a/page"
    assert c.seen == {"app_a": [], "app_b": []}


def test_client_headers_cannot_spoof_identity_or_app(c):
    # t23-021: client-sent identity headers are overwritten; a client-sent
    # X-Latarnia-App is replaced on the request to /auth/verify (and deleted on
    # the request to the App, t23-026).
    user, token = _member(c, app_a="webUI-low")
    spoof = {"X-Latarnia-App-Role": "full", "X-Latarnia-Is-Super": "true",
             "X-Latarnia-User": "someone-else", "X-Latarnia-App": "other_app"}
    r = _through_caddy(c, "/apps/app_a/page", token, spoof)
    assert r.status == 200, r.body
    assert _verify_apps(c) == [["app_a"]]
    (req,) = c.seen["app_a"]
    assert req["headers"]["x-latarnia-app-role"] == ["webUI-low"]
    assert req["headers"]["x-latarnia-is-super"] == ["false"]
    assert req["headers"]["x-latarnia-user"] == [str(user["id"])]


def test_spoofed_app_header_cannot_borrow_another_apps_role(c):
    # t23-021: a role on app_b, a request for app_a claiming to be for app_b.
    _user, token = _member(c, app_b="webUI-low")
    r = _through_caddy(c, "/apps/app_a/page", token, {"X-Latarnia-App": "app_b"})
    assert r.status == 403, r.body
    assert _verify_apps(c) == [["app_a"]]
    assert c.seen == {"app_a": [], "app_b": []}


def test_catch_all_strips_client_app_header(c):
    # t23-021: role none on app_a, `X-Latarnia-App: app_a` on a non-App path ->
    # the catch-all deletes the header, so verify makes no App check (not 403).
    _user, token = _member(c)
    r = _through_caddy(c, "/dashboard", token, {"X-Latarnia-App": "app_a"})
    assert r.status != 403, r.body
    assert _verify_apps(c) == [[]]
    assert c.seen == {"app_a": [], "app_b": []}


def test_client_app_header_never_reaches_an_app(c):
    # t23-026: X-Latarnia-App is a Caddy -> verify header. forward_auth's
    # `header_up` only affects the request to /auth/verify; the App proxy
    # deletes a client-sent copy in protected and public-route blocks.
    _user, token = _member(c, app_a="webUI-low")
    r = _through_caddy(c, "/apps/app_a/page", token, {"X-Latarnia-App": "other_app"})
    observed = _observed(c, "/apps/app_a/page with X-Latarnia-App: other_app", r.status)
    assert r.status == 200, observed
    assert _verify_apps(c) == [["app_a"]], observed
    (req,) = c.seen["app_a"]
    assert req["path"] == "/page", observed
    assert "x-latarnia-app" not in req["headers"], observed
    assert req["headers"]["x-latarnia-app-role"] == ["webUI-low"], observed

    # Public route (no forward_auth, no session): the App gets the request
    # without X-Latarnia-App (and without identity headers).
    _clear(c)
    (prefix,) = PUBLIC_ROUTES["app_a"]
    path = f"/apps/app_a{prefix}item"
    spoof = {"X-Latarnia-App": "other_app", "X-Latarnia-App-Role": "full"}
    r = _through_caddy(c, path, headers=spoof)
    observed = _observed(c, f"{path} with {spoof}", r.status)
    assert r.status == 200, observed
    assert c.verify_calls == [], observed
    (req,) = c.seen["app_a"]
    assert req["path"] == f"{prefix}item", observed
    assert "x-latarnia-app" not in req["headers"], observed
    assert not [h for h in req["headers"] if h.startswith("x-latarnia-")], observed
    assert c.seen["app_b"] == [], observed


# t23-022: request targets and the App block Caddy routes them to (Caddy's path
# matcher decodes, resolves dot segments, merges slashes and folds case before
# matching). None = assert only "if Caddy routes it there". Sent over a raw
# socket so the exact target bytes reach Caddy.
TARGET_FORMS = [
    ("/apps/app_a/page", "app_a"),
    ("/apps/app_a/../app_a/page", "app_a"),
    ("/apps/app_a/./page", "app_a"),
    ("/APPS/app_a/page", "app_a"),
    ("/apps/APP_A/page", "app_a"),
    ("/apps/%61pp_a/page", "app_a"),
    ("/%61pps/app_a/page", "app_a"),
    ("/apps/app%5Fa/page", "app_a"),
    ("/apps//app_a/page", None),
    ("http://localhost:{port}/apps/app_a/page", "app_a"),   # absolute-form
    # Cross-App forms: the first App segment is not the App the path reaches.
    ("/apps/app_b/../app_a/page", "app_a"),
    ("/apps/app_a/%2e%2e/app_b/page", "app_b"),
]


@pytest.mark.parametrize("target, routes_to", TARGET_FORMS)
def test_every_target_form_is_checked_against_the_app_it_reaches(c, target, routes_to):
    # t23-022
    target = target.format(port=c.port)

    # Role none on every App -> 403 wherever Caddy routes to an App block, and
    # no App receives anything.
    _user, none_token = _member(c)
    what = f"{target} (role none)"
    r = _raw_get(c, target, none_token, what)
    observed = _observed(c, what, r.status_line)
    _assert_app_reached_only_for_itself(c, observed)
    assert c.seen == {"app_a": [], "app_b": []}, observed
    routed = _routed_app(c, observed)
    if routes_to is not None:
        assert routed == routes_to, observed
    if routed in APPS:
        assert r.status == 403, observed

    # Same form, user granted webUI-low on app_a only -> 200 with that role
    # where Caddy routes to app_a, 403 where it routes to app_b.
    _clear(c)
    _user, low_token = _member(c, app_a="webUI-low")
    what = f"{target} (webUI-low on app_a)"
    r = _raw_get(c, target, low_token, what)
    observed = _observed(c, what, r.status_line)
    _assert_app_reached_only_for_itself(c, observed)
    routed = _routed_app(c, observed)
    if routes_to is not None:
        assert routed == routes_to, observed
    if routed == "app_a":
        assert r.status == 200, observed
        (req,) = c.seen["app_a"]
        assert req["headers"]["x-latarnia-app-role"] == ["webUI-low"], observed
    else:
        assert c.seen == {"app_a": [], "app_b": []}, observed
        if routed == "app_b":
            assert r.status == 403, observed


# t23-024: a client `Connection` header naming the identity headers. The App's
# reverse_proxy deletes every header named in `Connection` (hop-by-hop cleanup);
# without its `header_up` re-set the App would receive no role at all (and an
# App that defaults a missing role to `full` would serve full access).
CONNECTION_VALUES = [
    "close, X-Latarnia-App-Role, X-Latarnia-User, X-Latarnia-Is-Super",
    # Naming X-Latarnia-App too: forward_auth's own `header_up` re-sets it for
    # verify after its hop-by-hop cleanup.
    "close, X-Latarnia-App-Role, X-Latarnia-User, X-Latarnia-Is-Super, X-Latarnia-App",
]


@pytest.mark.parametrize("spoof", [[], ["X-Latarnia-App-Role: full"]],
                         ids=["no-spoof", "spoofed-role"])
@pytest.mark.parametrize("connection", CONNECTION_VALUES)
def test_connection_header_cannot_strip_identity_headers(c, connection, spoof):
    # t23-024: also with a client-sent `X-Latarnia-App-Role: full` next to the
    # Connection header — the App still gets verify's role, not the client's.
    user, token = _member(c, app_a="webUI-low")
    what = f"/apps/app_a/page with Connection: {connection} and {spoof}"
    r = _raw_get(c, "/apps/app_a/page", token, what, header_lines=spoof,
                 connection=connection)
    observed = _observed(c, what, r.status_line)
    assert r.status == 200, observed
    assert _verify_apps(c) == [["app_a"]], observed
    assert len(c.seen["app_a"]) == 1 and c.seen["app_b"] == [], observed
    headers = c.seen["app_a"][0]["headers"]
    assert headers.get("x-latarnia-app-role") == ["webUI-low"], observed
    assert headers.get("x-latarnia-is-super") == ["false"], observed
    assert headers.get("x-latarnia-user") == [str(user["id"])], observed


# t23-025: client-sent `X-Latarnia-App` in other spellings and duplicated, sent
# over a raw socket so the header lines go out exactly as written.
APP_HEADER_SPOOFS = {
    "lowercase": ["x-latarnia-app: app_b"],
    "uppercase": ["X-LATARNIA-APP: app_b"],
    "duplicated": ["X-Latarnia-App: app_b", "X-Latarnia-App: app_b"],
}


@pytest.mark.parametrize("spoof", list(APP_HEADER_SPOOFS.values()),
                         ids=list(APP_HEADER_SPOOFS))
def test_client_app_header_variants_on_app_block(c, spoof):
    # t23-025: verify is asked about app_a only. The user also holds webUI-full
    # on app_b, so a verify decision for app_b would show as the wrong role.
    _user, token = _member(c, app_a="webUI-low", app_b="webUI-full")
    what = f"/apps/app_a/page with {spoof}"
    r = _raw_get(c, "/apps/app_a/page", token, what, header_lines=spoof)
    observed = _observed(c, what, r.status_line)
    assert r.status == 200, observed
    assert _verify_apps(c) == [["app_a"]], observed
    assert _deliveries(c) == {"app_a": [("/page", ["webUI-low"])], "app_b": []}, observed
    # t23-026: no spelling of the client's header reaches the App.
    assert "x-latarnia-app" not in c.seen["app_a"][0]["headers"], observed


@pytest.mark.parametrize("spoof", list(APP_HEADER_SPOOFS.values()),
                         ids=list(APP_HEADER_SPOOFS))
def test_client_app_header_variants_on_catch_all(c, spoof):
    # t23-025: role none on every App; on /dashboard the catch-all deletes every
    # variant, so verify sees no X-Latarnia-App and makes no App check.
    _user, token = _member(c)
    what = f"/dashboard with {spoof}"
    r = _raw_get(c, "/dashboard", token, what, header_lines=spoof)
    observed = _observed(c, what, r.status_line)
    assert r.status != 403, observed
    assert _verify_apps(c) == [[]], observed
    assert c.seen == {"app_a": [], "app_b": []}, observed
