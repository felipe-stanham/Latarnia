"""
Platform auth database (P-0008).

Owns `latarnia_platform_{env}`: creates it if missing (as superuser, via the
shared PgClient), applies the SQL migrations in `migrations/` in order, and
exposes thin query/execute/transaction helpers that all auth modules use.

Migration tracking mirrors db_provisioner's `schema_versions` pattern
(filename + checksum, idempotent, applied in numeric order). No external
migration tool.
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import List

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


class AuthDB:
    """Platform auth DB lifecycle + query surface."""

    def __init__(self, config_manager: ConfigManager, pg_client: PgClient):
        self.config_manager = config_manager
        self.pg_client = pg_client
        self.db_name = config_manager.get_platform_db_name()
        self.migrations_dir = Path(__file__).parent / "migrations"
        self.logger = logging.getLogger("latarnia.auth.db")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        """Create the DB if missing and apply pending migrations.

        Returns True on success. Safe to call on every startup — DB creation
        and each migration are idempotent.
        """
        try:
            if not self.pg_client.database_exists(self.db_name):
                self.pg_client.create_plain_database(self.db_name)
                self.logger.info("Created platform auth DB %s", self.db_name)
            else:
                self.logger.debug("Platform auth DB %s already exists", self.db_name)

            self.pg_client.execute_on_db(self.db_name, SCHEMA_VERSIONS_DDL)
            self._run_migrations()
            return True
        except Exception as exc:
            self.logger.error("AuthDB initialization failed: %s", exc)
            return False

    def _migration_files(self) -> List[Path]:
        if not self.migrations_dir.exists():
            return []
        # Only numeric-prefixed files are migrations; ignore anything else so a
        # stray .sql can't crash the numeric sort.
        files = [f for f in self.migrations_dir.glob("*.sql") if f.name[:1].isdigit()]
        files.sort(key=lambda f: int(f.name.split("_")[0]))
        return files

    def _applied(self) -> set:
        try:
            rows = self.pg_client.query_on_db(
                self.db_name, "SELECT migration_file FROM schema_versions"
            )
            return {r["migration_file"] for r in rows}
        except Exception:
            return set()

    def _run_migrations(self) -> None:
        applied = self._applied()
        pending = [f for f in self._migration_files() if f.name not in applied]
        if not pending:
            self.logger.debug("No pending auth migrations for %s", self.db_name)
            return

        with self.pg_client.transaction(self.db_name) as conn:
            try:
                for mig in pending:
                    sql_text = mig.read_text()
                    checksum = hashlib.sha256(sql_text.encode()).hexdigest()
                    number = int(mig.name.split("_")[0])
                    start = time.monotonic()
                    conn.execute(sql_text)
                    duration_ms = int((time.monotonic() - start) * 1000)
                    conn.execute(
                        "INSERT INTO schema_versions "
                        "(migration_file, migration_number, checksum, duration_ms) "
                        "VALUES (%s, %s, %s, %s)",
                        (mig.name, number, checksum, duration_ms),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self.logger.info(
            "Applied %d auth migration(s) to %s: %s",
            len(pending), self.db_name, ", ".join(m.name for m in pending),
        )

    # ------------------------------------------------------------------
    # Query surface (all run as superuser against the platform DB)
    # ------------------------------------------------------------------

    def query(self, sql: str, params: tuple = ()) -> list:
        """Run a SELECT and return rows as dicts."""
        return self.pg_client.query_on_db(self.db_name, sql, params)

    def query_one(self, sql: str, params: tuple = ()):
        """Run a SELECT and return the first row (dict) or None."""
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple = ()) -> None:
        """Run an INSERT/UPDATE/DELETE (autocommit)."""
        self.pg_client.execute_on_db(self.db_name, sql, params)

    def execute_returning(self, sql: str, params: tuple = ()):
        """Run a statement with RETURNING and return the first row (dict)."""
        rows = self.pg_client.query_on_db(self.db_name, sql, params)
        return rows[0] if rows else None
