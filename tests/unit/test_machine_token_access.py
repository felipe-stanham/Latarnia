"""Machine Token fixes (T-0022) and per-App authorization of the UI API proxy (T-0023).

- `GET /api/apps/{app_id}` and `GET /api/apps` honour a superuser token's
  `super` claim instead of falling through to the session branch.
- Two tokens minted with identical inputs in the same second differ (`jti`),
  so `machine_tokens.token_hash` stays unique.
- The `/api/apps/{app_id}/ui/*` proxy denies callers with no role on the App and
  forwards the caller's identity to it (T-0023).
"""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import latarnia.auth.jwt_auth as jwt_auth_module
from latarnia.auth.jwt_auth import JWTAuth
from latarnia.auth.tokens import MachineTokenStore


# ---------------------------------------------------------------------------
# /api/apps/{app_id} and /api/apps scoping (main.py)
# ---------------------------------------------------------------------------

def _app_entry(name):
    entry = MagicMock()
    entry.name = name
    entry.app_id = f"{name}-id"
    entry.to_dict.return_value = {"name": name, "app_id": f"{name}-id"}
    return entry


def _request(claims):
    return SimpleNamespace(state=SimpleNamespace(jwt_claims=claims))


@pytest.fixture
def main(monkeypatch):
    """latarnia.main with its registry, session resolver and role store faked."""
    from latarnia import main as main_module

    apps = {name: _app_entry(name) for name in ("app_a", "app_b")}
    registry = MagicMock()
    registry.get_app.side_effect = lambda app_id: apps.get(app_id)
    registry.get_all_apps.return_value = list(apps.values())
    monkeypatch.setattr(main_module, "app_manager", SimpleNamespace(registry=registry))

    health = MagicMock()
    health.get_overall_status.return_value = {"overall_status": "green", "detail": None}
    monkeypatch.setattr(main_module, "health_monitor", health)

    session_user = MagicMock(return_value=None)
    monkeypatch.setattr(main_module, "_session_user", session_user)
    role_store = MagicMock()
    monkeypatch.setattr(main_module, "role_store", role_store)
    return SimpleNamespace(module=main_module, session_user=session_user,
                           role_store=role_store)


def test_superuser_token_gets_app(main):
    # t22-001: super claim, empty scope, no session -> 200 with the App's data.
    result = asyncio.run(main.module.get_app(
        "app_a", _request({"sub": "u1", "super": True, "apps": {}})))
    assert result == {"name": "app_a", "app_id": "app_a-id"}
    main.session_user.assert_not_called()


def test_scoped_token_get_app(main):
    # t22-002: scoped token sees its App, 403 on another.
    claims = {"sub": "u1", "super": False, "apps": {"app_a": "webUI-med"}}
    assert asyncio.run(main.module.get_app("app_a", _request(claims)))["name"] == "app_a"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.module.get_app("app_b", _request(claims)))
    assert exc.value.status_code == 403
    assert exc.value.detail == "Token not scoped to this app"


def test_no_claims_no_session_get_app_is_401(main):
    # t22-003
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.module.get_app("app_a", _request(None)))
    assert exc.value.status_code == 401


def test_no_claims_no_session_list_is_401(main):
    # The fail-closed 401 must not be swallowed into a 500.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.module.get_all_apps(_request(None)))
    assert exc.value.status_code == 401


def test_superuser_token_lists_all_apps(main):
    # t22-004: super claim sees every App without the session or RoleStore.
    result = asyncio.run(main.module.get_all_apps(
        _request({"sub": "u1", "super": True, "apps": {}})))
    assert [a["name"] for a in result["apps"]] == ["app_a", "app_b"]
    main.role_store.is_visible.assert_not_called()


def test_scoped_token_lists_only_scoped_apps(main):
    # t22-004
    result = asyncio.run(main.module.get_all_apps(
        _request({"sub": "u1", "super": False, "apps": {"app_b": "full"}})))
    assert [a["name"] for a in result["apps"]] == ["app_b"]
    main.role_store.is_visible.assert_not_called()


# ---------------------------------------------------------------------------
# Same-second identical mints (jwt_auth.py / tokens.py)
# ---------------------------------------------------------------------------

def _frozen_auth(monkeypatch):
    monkeypatch.setattr(jwt_auth_module, "time", SimpleNamespace(time=lambda: 1_760_000_000))
    return JWTAuth(lambda: "test-secret")


def test_same_second_identical_issue_is_unique(monkeypatch):
    # t22-005
    auth = _frozen_auth(monkeypatch)
    t1 = auth.issue("u", {"a": "full"}, True)
    t2 = auth.issue("u", {"a": "full"}, True)
    assert t1 != t2
    assert auth.token_hash(t1) != auth.token_hash(t2)
    for token in (t1, t2):
        jti = auth.validate(token)["jti"]
        assert len(jti) == 32 and int(jti, 16) >= 0


def test_same_second_identical_create_token_inserts_distinct_hashes(monkeypatch):
    # t22-006 (unit half): the stored hashes differ, so UNIQUE cannot collide.
    db = MagicMock()
    store = MachineTokenStore(db, _frozen_auth(monkeypatch))
    raw1 = store.create_token("u", "agent", {"a": "full"}, True, granted_by="u")
    raw2 = store.create_token("u", "agent", {"a": "full"}, True, granted_by="u")
    assert raw1 != raw2
    hashes = [call.args[1][2] for call in db.execute.call_args_list]
    assert len(hashes) == 2 and hashes[0] != hashes[1]


# ---------------------------------------------------------------------------
# T-0023: UI API proxy checks per-App access and forwards identity (main.py)
# ---------------------------------------------------------------------------

SESSION_USER = {"id": "u2", "is_superuser": False}


@pytest.fixture
def proxy(main, monkeypatch):
    """`main` plus running service Apps and a recording ui_renderer."""
    from latarnia.web import ui_renderer as renderer_module

    for name in ("app_a", "app_b"):
        entry = main.module.app_manager.registry.get_app(name)
        entry.type = "service"
        entry.runtime_info.assigned_port = 8101

    renderer = renderer_module.ui_renderer
    discover = AsyncMock(return_value=["items"])
    fetch_list = AsyncMock(return_value=[{"id": 1, "name": "x"}])
    fetch_detail = AsyncMock(return_value={"id": 1, "name": "x"})
    monkeypatch.setattr(renderer, "discover_ui_resources", discover)
    monkeypatch.setattr(renderer, "fetch_resource_list", fetch_list)
    monkeypatch.setattr(renderer, "fetch_resource_detail", fetch_detail)
    return SimpleNamespace(main=main, discover=discover, fetch_list=fetch_list,
                           fetch_detail=fetch_detail)


# The three UI API proxy endpoints, as callables(module, app_id, request).
UI_ENDPOINTS = {
    "resources": lambda m, app_id, req: m.get_app_ui_resources(app_id, req),
    "list": lambda m, app_id, req: m.get_app_ui_resource(app_id, "items", req),
    "detail": lambda m, app_id, req: m.get_app_ui_resource_detail(app_id, "items", "1", req),
}


def _call_ui(proxy, endpoint, app_id, request):
    return asyncio.run(UI_ENDPOINTS[endpoint](proxy.main.module, app_id, request))


def _assert_renderer_untouched(proxy):
    proxy.discover.assert_not_called()
    proxy.fetch_list.assert_not_called()
    proxy.fetch_detail.assert_not_called()


@pytest.mark.parametrize("endpoint", UI_ENDPOINTS)
def test_ui_proxy_session_role_none_is_403(proxy, endpoint):
    # t23-006: role `none` on the App -> 403, the App is never called.
    proxy.main.session_user.return_value = SESSION_USER
    proxy.main.role_store.get_role.return_value = "none"
    with pytest.raises(HTTPException) as exc:
        _call_ui(proxy, endpoint, "app_a", _request(None))
    assert exc.value.status_code == 403
    assert exc.value.detail == "No access to this app"
    _assert_renderer_untouched(proxy)


@pytest.mark.parametrize("endpoint", UI_ENDPOINTS)
def test_ui_proxy_token_scoped_elsewhere_is_403(proxy, endpoint):
    # t23-007: token scoped to another App -> 403, the App is never called.
    claims = {"sub": "u1", "super": False, "apps": {"app_b": "full"}}
    with pytest.raises(HTTPException) as exc:
        _call_ui(proxy, endpoint, "app_a", _request(claims))
    assert exc.value.status_code == 403
    assert exc.value.detail == "Token not scoped to this app"
    _assert_renderer_untouched(proxy)


@pytest.mark.parametrize("endpoint", UI_ENDPOINTS)
def test_ui_proxy_no_identity_is_401(proxy, endpoint):
    # Neither claims nor session (vanished after the middleware gate) -> 401.
    with pytest.raises(HTTPException) as exc:
        _call_ui(proxy, endpoint, "app_a", _request(None))
    assert exc.value.status_code == 401
    _assert_renderer_untouched(proxy)


def test_ui_proxy_scoped_token_forwards_identity(proxy):
    # t23-008: scoped token -> 200 and the App receives user, role, is_super.
    claims = {"sub": "u1", "super": False, "apps": {"app_a": "webUI-low"}}
    expected = {"X-Latarnia-User": "u1", "X-Latarnia-App-Role": "webUI-low",
                "X-Latarnia-Is-Super": "false"}

    result = _call_ui(proxy, "list", "app_a", _request(claims))
    assert result["count"] == 1
    proxy.fetch_list.assert_awaited_once_with(
        "http://localhost:8101", "items", headers=expected)

    _call_ui(proxy, "detail", "app_a", _request(claims))
    proxy.fetch_detail.assert_awaited_once_with(
        "http://localhost:8101", "items", "1", headers=expected)


def test_ui_proxy_superuser_token_forwards_full(proxy):
    # t23-009: superuser token -> role `full`, is_super `true`, even unscoped.
    claims = {"sub": "admin", "super": True, "apps": {}}
    _call_ui(proxy, "list", "app_a", _request(claims))
    proxy.fetch_list.assert_awaited_once_with(
        "http://localhost:8101", "items",
        headers={"X-Latarnia-User": "admin", "X-Latarnia-App-Role": "full",
                 "X-Latarnia-Is-Super": "true"})


def test_ui_proxy_session_user_forwards_role(proxy):
    # t23-010: session user with a role -> 200, discovery carries that identity.
    proxy.main.session_user.return_value = SESSION_USER
    proxy.main.role_store.get_role.return_value = "webUI-med"
    result = _call_ui(proxy, "resources", "app_a", _request(None))
    assert result["has_ui"] is True
    proxy.discover.assert_awaited_once_with(
        "http://localhost:8101",
        headers={"X-Latarnia-User": "u2", "X-Latarnia-App-Role": "webUI-med",
                 "X-Latarnia-Is-Super": "false"})
    proxy.main.role_store.get_role.assert_called_once_with("u2", "app_a")


def test_ui_proxy_superuser_session_forwards_full(proxy):
    # A superuser session needs no RoleStore row.
    proxy.main.session_user.return_value = {"id": "root", "is_superuser": True}
    _call_ui(proxy, "list", "app_a", _request(None))
    proxy.main.role_store.get_role.assert_not_called()
    proxy.fetch_list.assert_awaited_once_with(
        "http://localhost:8101", "items",
        headers={"X-Latarnia-User": "root", "X-Latarnia-App-Role": "full",
                 "X-Latarnia-Is-Super": "true"})


@pytest.mark.parametrize("endpoint", UI_ENDPOINTS)
def test_ui_proxy_missing_app_is_404_before_access_check(proxy, endpoint):
    # t23-011: existence check runs first, even for a caller with no identity.
    with pytest.raises(HTTPException) as exc:
        _call_ui(proxy, endpoint, "missing", _request(None))
    assert exc.value.status_code == 404
    assert exc.value.detail == "App missing not found"
    proxy.main.session_user.assert_not_called()
    _assert_renderer_untouched(proxy)


# ---------------------------------------------------------------------------
# T-0023: ui_renderer sends the caller's headers (ui_renderer.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def sent_requests(monkeypatch):
    """Route ui_renderer's httpx calls through a MockTransport; collect requests."""
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/ui":
            return httpx.Response(200, json=["items"])
        if request.url.path.count("/") == 2:   # /api/items
            return httpx.Response(200, json=[{"id": 1}])
        return httpx.Response(200, json={"id": 1})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    return seen


def _x_latarnia(request):
    return {k: v for k, v in request.headers.items() if k.lower().startswith("x-latarnia-")}


def test_ui_renderer_sends_headers_on_all_three_calls(sent_requests):
    # t23-012: headers given -> carried on the outgoing request.
    from latarnia.web.ui_renderer import UIRenderer
    renderer = UIRenderer()
    headers = {"X-Latarnia-User": "u1"}

    assert asyncio.run(renderer.fetch_resource_list(
        "http://app", "items", headers=headers)) == [{"id": 1}]
    assert asyncio.run(renderer.fetch_resource_detail(
        "http://app", "items", 1, headers=headers)) == {"id": 1}
    assert asyncio.run(renderer.discover_ui_resources(
        "http://app", headers=headers)) == ["items"]

    assert [r.url.path for r in sent_requests] == ["/api/items", "/api/items/1", "/ui"]
    for request in sent_requests:
        assert _x_latarnia(request) == {"x-latarnia-user": "u1"}


def test_ui_renderer_without_headers_sends_no_identity(sent_requests):
    # t23-012: no headers -> no X-Latarnia-* header on the outgoing request.
    from latarnia.web.ui_renderer import UIRenderer
    renderer = UIRenderer()
    asyncio.run(renderer.fetch_resource_list("http://app", "items"))
    asyncio.run(renderer.fetch_resource_detail("http://app", "items", 1))
    asyncio.run(renderer.discover_ui_resources("http://app"))
    assert len(sent_requests) == 3
    for request in sent_requests:
        assert _x_latarnia(request) == {}


# ---------------------------------------------------------------------------
# T-0023: example_full_app `caller` UI API resource (the fixture)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def example_client():
    path = Path(__file__).resolve().parents[2] / "examples" / "example_full_app" / "app.py"
    spec = importlib.util.spec_from_file_location("example_full_app_t0023", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return TestClient(module.rest_app)


def test_example_app_lists_caller_resource(example_client):
    # t23-013
    assert "caller" in example_client.get("/ui").json()


def test_example_app_caller_echoes_identity_headers(example_client):
    # t23-013
    r = example_client.get("/api/caller", headers={
        "X-Latarnia-User": "u1", "X-Latarnia-App-Role": "webUI-med",
        "X-Latarnia-Is-Super": "false"})
    assert r.status_code == 200
    assert r.json() == [{"id": 1, "user": "u1", "role": "webUI-med", "is_super": "false"}]


def test_example_app_caller_without_headers_is_null(example_client):
    # t23-013
    r = example_client.get("/api/caller")
    assert r.json() == [{"id": 1, "user": None, "role": None, "is_super": None}]


def test_example_app_caller_detail_echoes_identity_headers(example_client):
    # The dashboard detail modal calls /api/{resource}/{id}: same row, same identity.
    r = example_client.get("/api/caller/1", headers={
        "X-Latarnia-User": "u1", "X-Latarnia-App-Role": "webUI-low",
        "X-Latarnia-Is-Super": "false"})
    assert r.status_code == 200
    assert r.json() == {"id": 1, "user": "u1", "role": "webUI-low", "is_super": "false"}
    assert example_client.get("/api/caller/1").json() == {
        "id": 1, "user": None, "role": None, "is_super": None}
