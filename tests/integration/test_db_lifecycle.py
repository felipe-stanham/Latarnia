"""
Integration test for database provisioning lifecycle.

Requires a running Postgres instance. Skipped if Postgres is unavailable.
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from latarnia.core.config import ConfigManager, PostgresConfig
from latarnia.core.pg_client import PgClient
from latarnia.managers.db_provisioner import DbProvisioner

# Skip all tests in this module if Postgres is not available
try:
    import psycopg
    _conn = psycopg.connect("postgresql://@localhost:5432/postgres", autocommit=True)
    _conn.close()
    PG_AVAILABLE = True
except Exception:
    PG_AVAILABLE = False

pytestmark = pytest.mark.skipif(not PG_AVAILABLE, reason="Postgres not available")


@pytest.fixture
def config_manager():
    cm = ConfigManager.__new__(ConfigManager)
    cm.config_path = Path("config/config.json")
    cm._config = None
    cm.logger = Mock()

    # Build a real config with Postgres settings
    from latarnia.core.config import LatarniaConfig
    cm._config = LatarniaConfig(
        postgres=PostgresConfig(
            host="localhost", port=5432,
            database_prefix="test_latarnia_", role_prefix="test_latarnia_",
        )
    )
    return cm


@pytest.fixture
def pg_client(config_manager):
    return PgClient(config_manager)


@pytest.fixture
def provisioner(config_manager, pg_client, tmp_path):
    config_manager.config.process_manager.data_dir = str(tmp_path)
    return DbProvisioner(config_manager, pg_client)


@pytest.fixture(autouse=True)
def cleanup(pg_client):
    """Clean up test databases and roles after each test."""
    yield
    for name in ["test_latarnia_integ_app", "test_latarnia_integ_app_v2"]:
        try:
            pg_client.drop_database(name)
        except Exception:
            pass
        try:
            pg_client.drop_role(f"{name}_role")
        except Exception:
            pass


class TestDatabaseLifecycle:

    def test_full_provision_and_migrate(self, provisioner, pg_client, tmp_path):
        """Full lifecycle: provision DB, run migrations, verify schema_versions."""
        # Create app with migrations
        mig_dir = tmp_path / "migrations"
        mig_dir.mkdir()
        (mig_dir / "001_initial.sql").write_text(
            "CREATE TABLE contacts (id SERIAL PRIMARY KEY, name TEXT NOT NULL);"
        )
        (mig_dir / "002_add_email.sql").write_text(
            "ALTER TABLE contacts ADD COLUMN email TEXT;"
        )

        result = provisioner.provision_database("integ_app", tmp_path)

        assert result.success is True
        assert result.database_name == "test_latarnia_integ_app"
        assert result.role_name == "test_latarnia_integ_app_role"
        assert "postgresql://" in result.connection_url

        # Verify DB exists
        assert pg_client.database_exists("test_latarnia_integ_app") is True

        # Verify role exists
        assert pg_client.role_exists("test_latarnia_integ_app_role") is True

        # Verify schema_versions has 2 entries
        rows = pg_client.query_on_db(
            "test_latarnia_integ_app",
            "SELECT migration_file FROM schema_versions ORDER BY migration_number",
        )
        assert len(rows) == 2
        assert rows[0]["migration_file"] == "001_initial.sql"
        assert rows[1]["migration_file"] == "002_add_email.sql"

        # Verify the actual table was created
        tables = pg_client.query_on_db(
            "test_latarnia_integ_app",
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = 'contacts'",
        )
        assert len(tables) == 1

    def test_version_bump_runs_only_pending(self, provisioner, pg_client, tmp_path):
        """Version bump: only new migrations run."""
        mig_dir = tmp_path / "migrations"
        mig_dir.mkdir()
        (mig_dir / "001_initial.sql").write_text(
            "CREATE TABLE items (id SERIAL PRIMARY KEY);"
        )

        # Initial provision
        result = provisioner.provision_database("integ_app", tmp_path)
        assert result.success is True

        # Add a new migration
        (mig_dir / "002_add_name.sql").write_text(
            "ALTER TABLE items ADD COLUMN name TEXT;"
        )

        # Version bump
        success, applied, error = provisioner.run_version_bump_migrations(
            "test_latarnia_integ_app", tmp_path
        )
        assert success is True
        assert applied == ["002_add_name.sql"]

        # Verify 2 entries in schema_versions
        rows = pg_client.query_on_db(
            "test_latarnia_integ_app",
            "SELECT migration_file FROM schema_versions ORDER BY migration_number",
        )
        assert len(rows) == 2

    def test_migration_failure_rolls_back(self, provisioner, pg_client, tmp_path):
        """Failed migration on initial provision cleans up DB and role."""
        mig_dir = tmp_path / "migrations"
        mig_dir.mkdir()
        (mig_dir / "001_bad.sql").write_text("THIS IS NOT VALID SQL;")

        result = provisioner.provision_database("integ_app", tmp_path)

        assert result.success is False
        assert result.error_message is not None
        # DB and role should be cleaned up
        assert pg_client.database_exists("test_latarnia_integ_app") is False
        assert pg_client.role_exists("test_latarnia_integ_app_role") is False

    def test_idempotent_reprovision(self, provisioner, pg_client, tmp_path):
        """Re-provisioning an existing DB works without error."""
        mig_dir = tmp_path / "migrations"
        mig_dir.mkdir()
        (mig_dir / "001_initial.sql").write_text(
            "CREATE TABLE things (id SERIAL PRIMARY KEY);"
        )

        # First provision
        r1 = provisioner.provision_database("integ_app", tmp_path)
        assert r1.success is True

        # Second provision (simulating platform restart)
        r2 = provisioner.provision_database("integ_app", tmp_path)
        assert r2.success is True
        assert r2.database_name == r1.database_name

        # Should still have exactly 1 migration
        rows = pg_client.query_on_db(
            "test_latarnia_integ_app",
            "SELECT migration_file FROM schema_versions",
        )
        assert len(rows) == 1
