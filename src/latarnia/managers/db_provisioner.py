"""
Database provisioner for Latarnia platform.

Handles per-app database creation, role management, and migration execution.
"""
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from ..core.config import ConfigManager
from ..core.pg_client import PgClient


SCHEMA_VERSIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_versions (
    id SERIAL PRIMARY KEY,
    migration_file TEXT NOT NULL,
    migration_number INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    duration_ms INTEGER NOT NULL
)
"""

# Extensions enabled in every Latarnia-provisioned per-app database.
# Apps don't need to add `CREATE EXTENSION` to their migrations — declaring
# `database: true` in the manifest is enough. The per-app role does not
# have CREATE EXTENSION privilege; the platform (running as superuser)
# ensures these are present at provisioning time and on every restart.
#
# Each entry must be installed at the OS level (e.g. `postgresql-17-pgvector`
# for `vector`); missing binaries surface as logged warnings, not failures,
# so dev hosts without all extensions installed still work for unrelated apps.
DEFAULT_EXTENSIONS = ["vector"]


@dataclass
class ProvisioningResult:
    """Result of a database provisioning operation."""
    success: bool
    database_name: Optional[str] = None
    role_name: Optional[str] = None
    connection_url: Optional[str] = None
    applied_migrations: List[str] = field(default_factory=list)
    error_message: Optional[str] = None


class DbProvisioner:
    """Manages per-app database provisioning and migrations."""

    def __init__(self, config_manager: ConfigManager, pg_client: PgClient):
        self.config_manager = config_manager
        self.pg_client = pg_client
        self.logger = logging.getLogger("latarnia.db_provisioner")

    def provision_database(self, app_name: str, app_path: Path) -> ProvisioningResult:
        """Full provisioning workflow for an app with database: true.

        Creates role + database if they don't exist, runs pending migrations,
        and returns a connection URL for the app.
        """
        db_name, role_name = self._generate_names(app_name)

        # Use a stable stored password so that platform restarts don't rotate
        # credentials under already-running app processes.
        stored = self._load_stored_password(role_name)
        if stored is not None:
            password = stored
            if not self.pg_client.role_exists(role_name):
                # Role was dropped externally; re-create with the stored password.
                self.pg_client.create_role(role_name, password)
                self.logger.warning("Role %s was missing; re-created with stored credentials", role_name)
            else:
                self.logger.debug("Role %s: reusing stored credentials", role_name)
        else:
            password = secrets.token_urlsafe(32)
            if self.pg_client.role_exists(role_name):
                self.pg_client.alter_role_password(role_name, password)
                self.logger.info("Role %s exists but no stored creds; rotated password", role_name)
            else:
                self.pg_client.create_role(role_name, password)
            self._store_password(role_name, password)

        try:
            # Create database if needed
            db_is_new = False
            if not self.pg_client.database_exists(db_name):
                self.pg_client.create_database(db_name, role_name)
                self.pg_client.revoke_public_connect(db_name)
                self.pg_client.grant_connect(db_name, role_name)
                db_is_new = True
                self.logger.info(f"Provisioned new database: {db_name}")
            else:
                self.logger.info(f"Database {db_name} already exists, reusing")

            # Ensure platform-default extensions are available before any
            # migrations run. Idempotent (CREATE EXTENSION IF NOT EXISTS),
            # runs on both new and reused databases so existing app DBs get
            # backfilled at the next platform start. Missing OS-level
            # binaries (e.g. postgresql-XX-pgvector not installed) are
            # logged as warnings, not provisioning failures.
            self._ensure_default_extensions(db_name)

            # Create schema_versions table
            self.pg_client.execute_on_db(db_name, SCHEMA_VERSIONS_DDL)

            # Run migrations
            applied = []
            migration_files = self._list_migration_files(app_path)
            already_applied = self._get_applied_migrations(db_name) if migration_files else set()
            if migration_files:
                pending = [f for f in migration_files if f.name not in already_applied]

                if pending:
                    success, applied, error = self._run_migrations(db_name, pending)
                    if not success:
                        if db_is_new:
                            # Clean slate on initial provision failure
                            self._cleanup(db_name, role_name)
                        return ProvisioningResult(
                            success=False, error_message=error
                        )

            # Include previously applied migrations in the result
            all_applied = sorted(already_applied | set(applied))
            applied = all_applied

            # Grant table/sequence privileges to app role so it can
            # use objects created by the superuser during migrations.
            self.pg_client.execute_on_db(
                db_name,
                f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {role_name}",
            )
            self.pg_client.execute_on_db(
                db_name,
                f"GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {role_name}",
            )

            # Build connection URL
            pg = self.config_manager.config.postgres
            connection_url = (
                f"postgresql://{role_name}:{password}"
                f"@{pg.host}:{pg.port}/{db_name}"
            )

            return ProvisioningResult(
                success=True,
                database_name=db_name,
                role_name=role_name,
                connection_url=connection_url,
                applied_migrations=applied,
            )

        except Exception as e:
            self.logger.error(f"Provisioning failed for {app_name}: {e}")
            return ProvisioningResult(success=False, error_message=str(e))

    def run_version_bump_migrations(
        self, db_name: str, app_path: Path
    ) -> Tuple[bool, List[str], Optional[str]]:
        """Run only pending migrations for a version bump.

        Unlike initial provisioning, does NOT drop the database on failure.
        Returns: (success, newly_applied_file_names, error_message)
        """
        migration_files = self._list_migration_files(app_path)
        if not migration_files:
            return True, [], None

        already_applied = self._get_applied_migrations(db_name)
        pending = [f for f in migration_files if f.name not in already_applied]

        if not pending:
            self.logger.info(f"No pending migrations for {db_name}")
            return True, [], None

        return self._run_migrations(db_name, pending)

    def _ensure_default_extensions(self, db_name: str) -> None:
        """Run `CREATE EXTENSION IF NOT EXISTS <ext>` for each platform-default.

        Failures are logged as warnings, not raised: a dev host without
        `postgresql-XX-pgvector` installed should still be able to provision
        DBs for apps that don't need vectors. Apps that *do* need a missing
        extension will fail loudly at their own migration / runtime.
        """
        for ext in DEFAULT_EXTENSIONS:
            try:
                self.pg_client.execute_on_db(
                    db_name, f'CREATE EXTENSION IF NOT EXISTS "{ext}"'
                )
                self.logger.info(f"Ensured extension {ext!r} on {db_name}")
            except Exception as e:
                self.logger.warning(
                    "Could not enable extension %r on %s: %s. "
                    "Apps that need %r will fail; install postgresql-XX-%s "
                    "on the host to fix.",
                    ext, db_name, e, ext, ext,
                )

    def _generate_names(self, app_name: str) -> Tuple[str, str]:
        """Generate database name and role name from app name."""
        pg = self.config_manager.config.postgres
        clean_name = app_name.replace("-", "_").lower()
        db_name = f"{pg.database_prefix}{clean_name}"
        role_name = f"{pg.role_prefix}{clean_name}_role"
        return db_name, role_name

    def _list_migration_files(self, app_path: Path) -> List[Path]:
        """List migration files sorted by numeric prefix."""
        migrations_dir = app_path / "migrations"
        if not migrations_dir.exists():
            return []
        files = list(migrations_dir.glob("*.sql"))
        files.sort(key=lambda f: int(f.name.split("_")[0]))
        return files

    def _get_applied_migrations(self, db_name: str) -> set:
        """Get set of already-applied migration file names."""
        try:
            rows = self.pg_client.query_on_db(
                db_name, "SELECT migration_file FROM schema_versions"
            )
            return {r["migration_file"] for r in rows}
        except Exception:
            return set()

    def _run_migrations(
        self, db_name: str, pending_files: List[Path]
    ) -> Tuple[bool, List[str], Optional[str]]:
        """Execute pending migration files in a single transaction.

        Returns: (success, applied_file_names, error_message)
        """
        applied = []
        try:
            with self.pg_client.transaction(db_name) as conn:
                try:
                    for mig_file in pending_files:
                        sql_content = mig_file.read_text()
                        checksum = hashlib.sha256(sql_content.encode()).hexdigest()
                        migration_number = int(mig_file.name.split("_")[0])

                        start_time = time.monotonic()
                        conn.execute(sql_content)
                        duration_ms = int((time.monotonic() - start_time) * 1000)

                        conn.execute(
                            "INSERT INTO schema_versions "
                            "(migration_file, migration_number, checksum, duration_ms) "
                            "VALUES (%s, %s, %s, %s)",
                            (mig_file.name, migration_number, checksum, duration_ms),
                        )
                        applied.append(mig_file.name)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

            self.logger.info(
                f"Applied {len(pending_files)} migration(s) to {db_name}: "
                + ", ".join(applied)
            )
            return True, applied, None
        except Exception as e:
            error_msg = f"Migration failed on {db_name}: {e}"
            self.logger.error(error_msg)
            return False, [], error_msg

    def _cleanup(self, db_name: str, role_name: str) -> None:
        """Drop database and role on initial provisioning failure."""
        try:
            self.pg_client.drop_database(db_name)
            self.pg_client.drop_role(role_name)
            self.logger.info(f"Cleaned up failed provisioning: {db_name}, {role_name}")
        except Exception as e:
            self.logger.error(f"Cleanup failed for {db_name}/{role_name}: {e}")
        creds_path = self._get_creds_path(role_name)
        if creds_path.exists():
            creds_path.unlink(missing_ok=True)

    def _get_creds_path(self, role_name: str) -> Path:
        creds_dir = Path(self.config_manager.get_data_dir()) / ".db_credentials"
        creds_dir.mkdir(parents=True, exist_ok=True)
        return creds_dir / f"{role_name}.json"

    def _load_stored_password(self, role_name: str) -> Optional[str]:
        path = self._get_creds_path(role_name)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text()).get("password")
        except Exception:
            return None

    def _store_password(self, role_name: str, password: str) -> None:
        path = self._get_creds_path(role_name)
        path.write_text(json.dumps({"role": role_name, "password": password}))
        path.chmod(0o600)
