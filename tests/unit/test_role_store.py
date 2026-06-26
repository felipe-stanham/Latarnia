"""Unit tests for RoleStore (P-0008 Scope 3) — mocks the DB + user store."""
from unittest.mock import Mock

import pytest

from latarnia.auth.roles import RoleStore, ROLE_VALUES


def _store(superuser_ids=()):
    db = Mock()
    users = Mock()
    superset = set(superuser_ids)
    users.get_user_by_id.side_effect = lambda uid: (
        {"id": uid, "is_superuser": uid in superset} if uid else None
    )
    return RoleStore(db, users), db, users


def test_get_role_defaults_to_none():
    store, db, _ = _store()
    db.query_one.return_value = None
    assert store.get_role("u1", "my_app") == "none"


def test_get_role_returns_stored_value():
    store, db, _ = _store()
    db.query_one.return_value = {"role": "webUI-med"}
    assert store.get_role("u1", "my_app") == "webUI-med"


def test_set_role_upserts():
    store, db, _ = _store()
    store.set_role("u1", "my_app", "webUI-low", granted_by="admin")
    sql = db.execute.call_args.args[0]
    assert "INSERT INTO app_roles" in sql and "ON CONFLICT" in sql


def test_set_role_none_deletes():
    store, db, _ = _store()
    store.set_role("u1", "my_app", "none", granted_by="admin")
    assert "DELETE FROM app_roles" in db.execute.call_args.args[0]


def test_set_role_full_requires_superuser():
    # cap-018: non-superuser granting 'full' is rejected server-side.
    store, db, _ = _store(superuser_ids=())
    with pytest.raises(PermissionError):
        store.set_role("u1", "my_app", "full", granted_by="not_super")
    db.execute.assert_not_called()


def test_set_role_full_allowed_for_superuser():
    store, db, _ = _store(superuser_ids={"boss"})
    store.set_role("u1", "my_app", "full", granted_by="boss")
    assert "INSERT INTO app_roles" in db.execute.call_args.args[0]


def test_set_role_invalid_value():
    store, db, _ = _store()
    with pytest.raises(ValueError):
        store.set_role("u1", "my_app", "admin", granted_by="x")


def test_is_visible_rules():
    store, db, _ = _store()
    # Superuser sees everything without a role lookup.
    assert store.is_visible({"id": "s", "is_superuser": True}, "my_app") is True
    # Unauthenticated (dev direct) sees everything.
    assert store.is_visible(None, "my_app") is True
    # Regular user: none -> hidden, any other role -> visible.
    db.query_one.return_value = None
    assert store.is_visible({"id": "u", "is_superuser": False}, "my_app") is False
    db.query_one.return_value = {"role": "webUI-low"}
    assert store.is_visible({"id": "u", "is_superuser": False}, "my_app") is True


def test_role_values_enum():
    assert ROLE_VALUES == ["none", "webUI-low", "webUI-med", "webUI-full", "full"]
