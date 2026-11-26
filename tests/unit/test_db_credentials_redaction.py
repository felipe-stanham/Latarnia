"""T-0021: an App's Postgres password never reaches an API response or a log."""
import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import latarnia.main as main
from latarnia.managers.app_manager import (
    AppConfig, AppManifest, AppRegistryEntry, AppStatus, AppType, DatabaseInfo,
)
from latarnia.managers.db_provisioner import ProvisioningResult

PASSWORD = "S3cr3t-pw"
URL = f"postgresql://latarnia_crm_role:{PASSWORD}@localhost:5432/latarnia_crm"


@pytest.fixture
def entry(tmp_path):
    manifest = AppManifest(
        name="crm", type=AppType.SERVICE, description="CRM",
        version="1.0.0", author="Test", main_file="app.py",
        config=AppConfig(database=True),
    )
    return AppRegistryEntry(
        app_id="crm", name="crm", type=AppType.SERVICE,
        description="CRM", version="1.0.0", status=AppStatus.RUNNING,
        path=tmp_path, manifest=manifest,
        database_info=DatabaseInfo(
            provisioned=True, database_name="latarnia_crm",
            role_name="latarnia_crm_role", connection_url=URL,
            applied_migrations=["001_initial.sql"],
        ),
    )


@pytest.fixture
def platform(monkeypatch, entry):
    registry = Mock()
    registry.get_all_apps.return_value = [entry]
    registry.get_app.return_value = entry
    registry.get_apps_by_type.return_value = [entry]
    registry.get_apps_by_status.return_value = [entry]
    monkeypatch.setattr(main, "app_manager", SimpleNamespace(registry=registry))
    health = Mock()
    health.get_overall_status.return_value = {"overall_status": "green", "detail": "healthy"}
    monkeypatch.setattr(main, "health_monitor", health)
    roles = Mock()
    roles.is_visible.return_value = True
    monkeypatch.setattr(main, "role_store", roles)
    monkeypatch.setattr(main, "_session_user", lambda request: {"id": 1, "is_superuser": True})


def _request(claims=None):
    return SimpleNamespace(state=SimpleNamespace(jwt_claims=claims))


def _assert_redacted(payload):
    text = json.dumps(payload)
    assert PASSWORD not in text
    assert "connection_url" not in text


def test_list_apps_has_no_password_but_keeps_db_info(platform):
    payload = asyncio.run(main.get_all_apps(_request()))
    _assert_redacted(payload)
    db = payload["apps"][0]["database_info"]
    assert db["provisioned"] is True
    assert db["database_name"] == "latarnia_crm"
    assert db["role_name"] == "latarnia_crm_role"
    assert db["applied_migrations"] == ["001_initial.sql"]


def test_list_apps_for_scoped_machine_token_has_no_password(platform):
    payload = asyncio.run(main.get_all_apps(_request(claims={"apps": {"crm": "full"}})))
    assert payload["total_count"] == 1
    _assert_redacted(payload)


def test_other_app_endpoints_have_no_password(platform):
    _assert_redacted(asyncio.run(main.get_app("crm", _request())))
    _assert_redacted(asyncio.run(main.get_apps_by_type("service")))
    _assert_redacted(asyncio.run(main.get_apps_by_status("running")))


def test_serializing_keeps_url_in_memory_for_launchers(entry):
    entry.to_dict()
    assert entry.database_info.connection_url == URL


def test_repr_hides_password():
    assert PASSWORD not in repr(DatabaseInfo(provisioned=True, connection_url=URL))
    assert PASSWORD not in repr(ProvisioningResult(success=True, connection_url=URL))


@pytest.mark.parametrize("role_exists,stored", [(False, False), (True, False), (False, True)])
def test_provisioning_logs_no_password(tmp_path, caplog, role_exists, stored):
    from latarnia.core.config import ConfigManager, PostgresConfig
    from latarnia.core.pg_client import PgClient
    from latarnia.managers.db_provisioner import DbProvisioner

    cm = Mock(spec=ConfigManager)
    cm.config = Mock(postgres=PostgresConfig(host="localhost", port=5432))
    cm.get_data_dir.return_value = tmp_path
    pg = Mock(spec=PgClient)
    pg.role_exists.return_value = role_exists
    pg.database_exists.return_value = False
    pg.query_on_db.return_value = []

    provisioner = DbProvisioner(cm, pg)
    if stored:  # role dropped externally, re-created from stored credentials
        provisioner._store_password("latarnia_crm_role", PASSWORD)

    with caplog.at_level(logging.DEBUG):
        result = provisioner.provision_database("crm", tmp_path)

    assert result.success is True
    password = result.connection_url.split(":")[2].split("@")[0]
    assert password
    assert all(password not in r.getMessage() for r in caplog.records)
