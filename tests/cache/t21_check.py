"""T-0021 acceptance checks (t21-001..006). Usage: t21_check.py <001..006>. In-process, mocks only."""
import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import latarnia.main as main
from latarnia.managers.app_manager import (
    AppConfig, AppManifest, AppRegistryEntry, AppStatus, AppType, DatabaseInfo)
from latarnia.managers.db_provisioner import ProvisioningResult

PW = "S3cr3t-pw"
URL = f"postgresql://latarnia_crm_role:{PW}@localhost:5432/latarnia_crm"


def make_entry():
    m = AppManifest(name="crm", type=AppType.SERVICE, description="CRM", version="1.0.0",
                    author="T", main_file="app.py", config=AppConfig(database=True))
    return AppRegistryEntry(
        app_id="crm", name="crm", type=AppType.SERVICE, description="CRM", version="1.0.0",
        status=AppStatus.RUNNING, path=Path("/tmp/crm"), manifest=m,
        database_info=DatabaseInfo(provisioned=True, database_name="latarnia_crm",
                                   role_name="latarnia_crm_role", connection_url=URL,
                                   applied_migrations=["001_initial.sql"]))


def wire(entry):
    reg = Mock()
    reg.get_all_apps.return_value = [entry]
    reg.get_app.return_value = entry
    reg.get_apps_by_type.return_value = [entry]
    reg.get_apps_by_status.return_value = [entry]
    main.app_manager = SimpleNamespace(registry=reg)
    h = Mock()
    h.get_overall_status.return_value = {"overall_status": "green", "detail": "ok"}
    main.health_monitor = h
    r = Mock()
    r.is_visible.return_value = True
    r.get_role.return_value = "full"
    main.role_store = r
    main._session_user = lambda request: {"id": 1, "is_superuser": True}


def req(claims=None):
    return SimpleNamespace(state=SimpleNamespace(jwt_claims=claims))


def clean(label, payload):
    t = json.dumps(payload, default=str)
    assert PW not in t, f"{label}: password present"
    assert "connection_url" not in t, f"{label}: connection_url key present"


def t001():
    wire(make_entry())
    p = asyncio.run(main.get_all_apps(req()))
    clean("list", p)
    db = p["apps"][0]["database_info"]
    assert db["provisioned"] is True and db["database_name"] == "latarnia_crm"
    assert db["role_name"] == "latarnia_crm_role" and db["applied_migrations"] == ["001_initial.sql"]
    print("database_info keys:", sorted(db))


def t002():
    wire(make_entry())
    clean("by id", asyncio.run(main.get_app("crm", req())))
    clean("by type", asyncio.run(main.get_apps_by_type("service")))
    clean("by status", asyncio.run(main.get_apps_by_status("running")))


def t003():
    wire(make_entry())
    p = asyncio.run(main.get_all_apps(req({"apps": {"crm": "full"}, "super": False})))
    assert p["total_count"] == 1 and p["apps"][0]["name"] == "crm", p.get("total_count")
    clean("machine token", p)


def t004():
    e = make_entry()
    e.to_dict()
    e.database_info.to_dict()
    assert e.database_info.connection_url == URL


def t005():
    assert PW not in repr(DatabaseInfo(provisioned=True, connection_url=URL))
    assert PW not in repr(ProvisioningResult(success=True, connection_url=URL))
    assert PW not in repr(make_entry())


def t006():
    from latarnia.core.config import ConfigManager, PostgresConfig
    from latarnia.core.pg_client import PgClient
    from latarnia.managers.db_provisioner import DbProvisioner
    cases = [("new role", False, False), ("existing role", True, False),
             ("re-created from stored", False, True)]
    for case, role_exists, stored in cases:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cm = Mock(spec=ConfigManager)
            cm.config = Mock(postgres=PostgresConfig(host="localhost", port=5432))
            cm.get_data_dir.return_value = td
            pg = Mock(spec=PgClient)
            pg.role_exists.return_value = role_exists
            pg.database_exists.return_value = False
            pg.query_on_db.return_value = []
            prov = DbProvisioner(cm, pg)
            if stored:
                prov._store_password("latarnia_crm_role", PW)
            recs = []

            class H(logging.Handler):
                def emit(self, r):
                    recs.append(r)

            root = logging.getLogger()
            old = root.level
            h = H()
            root.addHandler(h)
            root.setLevel(logging.DEBUG)
            try:
                res = prov.provision_database("crm", td)
            finally:
                root.removeHandler(h)
                root.setLevel(old)
            assert res.success, (case, res.error_message)
            pw = res.connection_url.split(":")[2].split("@")[0]
            assert pw
            assert recs, f"{case}: no log records captured"
            for r in recs:
                blob = r.getMessage() + repr(r.args) + (r.exc_text or "")
                assert pw not in blob, f"{case}: password in log record from {r.name}"
            print(f"{case}: {len(recs)} log records, none contain the password")


if __name__ == "__main__":
    n = sys.argv[1]
    globals()["t" + n]()
    print(f"PASS t21-{n}")
