"""Machine Token fixes (T-0022).

- `GET /api/apps/{app_id}` and `GET /api/apps` honour a superuser token's
  `super` claim instead of falling through to the session branch.
- Two tokens minted with identical inputs in the same second differ (`jti`),
  so `machine_tokens.token_hash` stays unique.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

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
