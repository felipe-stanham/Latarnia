"""Unit tests for platform-owned App API docs (T-0020)."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latarnia.web import api_docs
from latarnia.web.api_docs import AppNotRunning, build_api_docs_router, split_openapi


SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Example Full App", "version": "1.0.0"},
    "paths": {
        "/health": {"get": {}},
        "/api/items": {"get": {"responses": {"200": {"content": {"application/json": {
            "schema": {"$ref": "#/components/schemas/Item"}}}}}}},
        "/rest/v1/items": {"get": {"responses": {"200": {"content": {"application/json": {
            "schema": {"$ref": "#/components/schemas/RestItem"}}}}}}},
        "/b/test": {"get": {}},
    },
    "components": {"schemas": {
        "Item": {"type": "object", "properties": {"tag": {"$ref": "#/components/schemas/Tag"}}},
        "Tag": {"type": "object"},
        "RestItem": {"type": "object"},
    }},
}


# ---------------------------------------------------------------------------
# split_openapi
# ---------------------------------------------------------------------------

def test_split_ui_keeps_only_api_paths_and_points_at_app_web_path():
    half = split_openapi(SPEC, "ui", "example_full_app")
    assert list(half["paths"]) == ["/api/items"]
    assert half["servers"] == [{"url": "/apps/example_full_app"}]
    assert half["info"]["title"] == "Example Full App — UI API"


def test_split_rest_reroots_paths_under_rest_server():
    half = split_openapi(SPEC, "rest", "example_full_app")
    assert list(half["paths"]) == ["/v1/items"]
    assert half["servers"] == [{"url": "/rest/example_full_app"}]


def test_split_prunes_components_transitively():
    ui = split_openapi(SPEC, "ui", "example_full_app")
    assert set(ui["components"]["schemas"]) == {"Item", "Tag"}
    rest = split_openapi(SPEC, "rest", "example_full_app")
    assert set(rest["components"]["schemas"]) == {"RestItem"}


def test_split_returns_none_when_half_is_empty():
    spec = {"openapi": "3.1.0", "info": {}, "paths": {"/health": {"get": {}}}}
    assert split_openapi(spec, "ui", "x") is None
    assert split_openapi(spec, "rest", "x") is None


def test_split_tolerates_odd_refs_and_keeps_security_schemes():
    spec = {
        "openapi": "3.1.0", "info": "not-a-dict",
        "paths": {"/rest/x": {"get": {"security": [{"bearer": []}], "responses": {"200": {
            "content": {"application/json": {"schema": {"anyOf": [
                {"$ref": "#/components/schemas"},
                {"$ref": "#/components/schemas/A/properties/b"},
            ]}}}}}}}},
        "components": {"schemas": {"A": {"type": "object"}, "B": {"type": "object"}},
                       "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}},
    }
    half = split_openapi(spec, "rest", "x")
    assert set(half["components"]["schemas"]) == {"A"}
    assert half["components"]["securitySchemes"] == {"bearer": {"type": "http", "scheme": "bearer"}}
    assert half["info"]["title"] == "x — App REST API"


def test_split_does_not_mutate_source():
    split_openapi(SPEC, "rest", "example_full_app")
    assert "/rest/v1/items" in SPEC["paths"]
    assert SPEC["info"]["title"] == "Example Full App"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def _app_entry(name, port=8151, app_type="service"):
    return SimpleNamespace(name=name, type=app_type,
                           runtime_info=SimpleNamespace(assigned_port=port))


class _Registry:
    def __init__(self, apps):
        self._apps = {a.name: a for a in apps}

    def get_app_by_name(self, name):
        return self._apps.get(name)


class _Roles:
    """user None (dev direct) and superusers see everything; else by role map."""
    def __init__(self, roles):
        self.roles = roles

    def is_visible(self, user, app_name):
        if user is None or user["is_superuser"]:
            return True
        return self.roles.get(app_name, "none") != "none"


def _client(monkeypatch, *, apps, roles=None, user=None, claims=None, spec=SPEC):
    async def fake_fetch(port):
        if spec is AppNotRunning:
            raise AppNotRunning()
        return spec
    monkeypatch.setattr(api_docs, "fetch_app_openapi", fake_fetch)

    app = FastAPI()

    @app.middleware("http")
    async def inject_claims(request, call_next):
        if claims is not None:
            request.state.jwt_claims = claims
        return await call_next(request)

    app.include_router(build_api_docs_router(
        SimpleNamespace(registry=_Registry(apps)),
        _Roles(roles or {}),
        lambda request: user,
    ))
    return TestClient(app)


USER = {"id": "u1", "is_superuser": False}


def test_user_with_role_gets_ui_schema(monkeypatch):
    c = _client(monkeypatch, apps=[_app_entry("example_full_app")],
                roles={"example_full_app": "webUI-low"}, user=USER)
    r = c.get("/api/docs/apps/example_full_app/ui")
    assert r.status_code == 200
    assert list(r.json()["paths"]) == ["/api/items"]


def test_user_with_role_none_gets_403_on_schema_and_page(monkeypatch):
    c = _client(monkeypatch, apps=[_app_entry("example_full_app")], roles={}, user=USER)
    assert c.get("/api/docs/apps/example_full_app/ui").status_code == 403
    assert c.get("/api/docs/apps/example_full_app/rest").status_code == 403
    assert c.get("/docs/apps/example_full_app/ui").status_code == 403
    assert c.get("/api/docs/apps/example_full_app").status_code == 403


def test_machine_token_scoped_to_app_reads_rest_schema(monkeypatch):
    c = _client(monkeypatch, apps=[_app_entry("example_full_app")],
                claims={"sub": "u1", "super": False, "apps": {"example_full_app": "webUI-low"}})
    r = c.get("/api/docs/apps/example_full_app/rest")
    assert r.status_code == 200
    assert list(r.json()["paths"]) == ["/v1/items"]


def test_machine_token_scoped_elsewhere_gets_403(monkeypatch):
    c = _client(monkeypatch, apps=[_app_entry("example_full_app")],
                claims={"sub": "u1", "super": False, "apps": {"other": "full"}})
    assert c.get("/api/docs/apps/example_full_app/rest").status_code == 403


def test_schema_without_session_fails_closed(monkeypatch):
    # /api/* reached with neither claims nor session (session revoked after the
    # middleware gate) → 401, never the dev "see everything" branch.
    c = _client(monkeypatch, apps=[_app_entry("example_full_app")], user=None)
    assert c.get("/api/docs/apps/example_full_app/ui").status_code == 401


def test_missing_half_is_404_and_summary_reports_it(monkeypatch):
    spec = {"openapi": "3.1.0", "info": {}, "paths": {"/health": {"get": {}}}}
    c = _client(monkeypatch, apps=[_app_entry("example_companion")],
                roles={"example_companion": "webUI-low"}, user=USER, spec=spec)
    assert c.get("/api/docs/apps/example_companion/rest").status_code == 404
    assert c.get("/docs/apps/example_companion/rest").status_code == 404
    assert c.get("/api/docs/apps/example_companion").json() == {
        "app": "example_companion", "running": True, "ui": False, "rest": False}


def test_stopped_app_is_503_with_page(monkeypatch):
    c = _client(monkeypatch, apps=[_app_entry("example_full_app", port=None)],
                roles={"example_full_app": "webUI-low"}, user=USER, spec=AppNotRunning)
    r = c.get("/docs/apps/example_full_app/ui")
    assert r.status_code == 503
    assert "not running" in r.text
    assert c.get("/api/docs/apps/example_full_app").json()["running"] is False


def test_page_renders_swagger_pointing_at_split_schema(monkeypatch):
    c = _client(monkeypatch, apps=[_app_entry("example_full_app")],
                roles={"example_full_app": "webUI-low"}, user=USER)
    r = c.get("/docs/apps/example_full_app/rest")
    assert r.status_code == 200
    assert "/api/docs/apps/example_full_app/rest" in r.text
    assert "swagger-ui" in r.text


def test_unknown_app_is_403_without_access_so_names_do_not_leak(monkeypatch):
    c = _client(monkeypatch, apps=[_app_entry("example_full_app")], roles={}, user=USER)
    assert c.get("/api/docs/apps/nope/ui").status_code == 403
    assert c.get("/api/docs/apps/example_full_app/ui").status_code == 403


def test_page_quotes_app_name_into_swagger_url(monkeypatch):
    c = _client(monkeypatch, apps=[_app_entry("a'b")], user={"id": "s", "is_superuser": True})
    r = c.get("/docs/apps/a'b/rest")
    assert r.status_code == 200
    assert "/api/docs/apps/a%27b/rest" in r.text
    assert "a&#x27;b" in r.text  # title escaped


def test_unknown_app_kind_or_streamlit_is_404(monkeypatch):
    c = _client(monkeypatch, apps=[_app_entry("st", app_type="streamlit")],
                user={"id": "s", "is_superuser": True})
    assert c.get("/api/docs/apps/nope/ui").status_code == 404
    assert c.get("/api/docs/apps/st/ui").status_code == 404
    c2 = _client(monkeypatch, apps=[_app_entry("example_full_app")],
                 roles={"example_full_app": "webUI-low"}, user=USER)
    assert c2.get("/api/docs/apps/example_full_app/admin").status_code == 404


def test_error_page_escapes_app_name(monkeypatch):
    c = _client(monkeypatch, apps=[], user={"id": "s", "is_superuser": True})
    r = c.get("/docs/apps/<b>x<\\b>/ui")
    assert r.status_code == 404
    assert "<b>x" not in r.text
