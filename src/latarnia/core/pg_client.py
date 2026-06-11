"""
Synchronous Postgres client for Latarnia platform superuser operations.

Used for database/role provisioning and migration execution.
Not used by apps at runtime — apps receive a connection URL and manage their own connections.
"""
import logging
from typing import Optional

import psycopg
from psycopg.rows import dict_row

from .config import ConfigManager


class PgClient:
    """Synchronous Postgres client for platform superuser operations"""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.logger = logging.getLogger("latarnia.pg_client")

    def _connect(self, dbname: str = "postgres", autocommit: bool = True):
        """Open a connection to the given database as superuser."""
        dsn = self.config_manager.get_postgres_dsn(dbname)
        conn = psycopg.connect(dsn, autocommit=autocommit, row_factory=dict_row)
        return conn

    def check_connectivity(self) -> bool:
        """Test Postgres connectivity. Returns True if reachable."""
        try:
            with self._connect() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception as e:
            self.logger.warning(f"Postgres connectivity check failed: {e}")
            return False

    def get_postgres_metrics(self) -> dict:
        """Get Postgres connectivity and version info."""
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT version()").fetchone()
            version = row["version"] if row else "unknown"
            return {"status": "connected", "version": version}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def role_exists(self, role_name: str) -> bool:
        """Check if a Postgres role exists."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (role_name,)
            ).fetchone()
            return row is not None

    def create_role(self, role_name: str, password: str) -> None:
        """CREATE ROLE with LOGIN and PASSWORD."""
        with self._connect() as conn:
            conn.execute(
                psycopg.sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
                    psycopg.sql.Identifier(role_name),
                    psycopg.sql.Literal(password),
                )
            )
        self.logger.info(f"Created role: {role_name}")

    def alter_role_password(self, role_name: str, password: str) -> None:
        """ALTER ROLE to update password (for idempotent re-provisioning)."""
        with self._connect() as conn:
            conn.execute(
                psycopg.sql.SQL("ALTER ROLE {} WITH PASSWORD {}").format(
                    psycopg.sql.Identifier(role_name),
                    psycopg.sql.Literal(password),
                )
            )
        self.logger.debug(f"Updated password for role: {role_name}")

    def database_exists(self, db_name: str) -> bool:
        """Check if a database exists."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)
            ).fetchone()
            return row is not None

    def create_database(self, db_name: str, owner: str) -> None:
        """CREATE DATABASE with OWNER."""
        with self._connect() as conn:
            conn.execute(
                psycopg.sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    psycopg.sql.Identifier(db_name),
                    psycopg.sql.Identifier(owner),
                )
            )
        self.logger.info(f"Created database: {db_name} (owner: {owner})")

    def create_plain_database(self, db_name: str) -> None:
        """CREATE DATABASE owned by the connecting superuser (no separate role).

        Used for the platform-owned auth DB (`latarnia_platform_{env}`), which
        the platform accesses directly as superuser — unlike per-app DBs that
        get their own least-privilege role.
        """
        with self._connect() as conn:
            conn.execute(
                psycopg.sql.SQL("CREATE DATABASE {}").format(
                    psycopg.sql.Identifier(db_name)
                )
            )
        self.logger.info(f"Created database: {db_name} (superuser-owned)")

    def revoke_public_connect(self, db_name: str) -> None:
        """REVOKE CONNECT ON DATABASE FROM PUBLIC."""
        with self._connect() as conn:
            conn.execute(
                psycopg.sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
                    psycopg.sql.Identifier(db_name)
                )
            )
        self.logger.debug(f"Revoked public CONNECT on: {db_name}")

    def grant_connect(self, db_name: str, role_name: str) -> None:
        """GRANT CONNECT ON DATABASE TO role."""
        with self._connect() as conn:
            conn.execute(
                psycopg.sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    psycopg.sql.Identifier(db_name),
                    psycopg.sql.Identifier(role_name),
                )
            )
        self.logger.debug(f"Granted CONNECT on {db_name} to {role_name}")

    def execute_on_db(self, db_name: str, sql: str, params: tuple = ()) -> None:
        """Execute SQL against a specific database as superuser (autocommit)."""
        with self._connect(dbname=db_name) as conn:
            conn.execute(sql, params)

    def execute_on_db_transactional(self, db_name: str, statements: list) -> None:
        """Execute multiple SQL statements in a single transaction.

        Args:
            db_name: Target database
            statements: List of (sql, params) tuples
        """
        with self._connect(dbname=db_name, autocommit=False) as conn:
            try:
                for sql, params in statements:
                    conn.execute(sql, params)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def transaction(self, db_name: str):
        """Return a transactional connection context manager for a database."""
        return self._connect(dbname=db_name, autocommit=False)

    def query_on_db(self, db_name: str, sql: str, params: tuple = ()) -> list:
        """Execute a query on a specific database and return all rows as dicts."""
        with self._connect(dbname=db_name) as conn:
            return conn.execute(sql, params).fetchall()

    def drop_database(self, db_name: str) -> None:
        """DROP DATABASE IF EXISTS (with force disconnect)."""
        with self._connect() as conn:
            # Terminate existing connections first
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (db_name,),
            )
            conn.execute(
                psycopg.sql.SQL("DROP DATABASE IF EXISTS {}").format(
                    psycopg.sql.Identifier(db_name)
                )
            )
        self.logger.info(f"Dropped database: {db_name}")

    def drop_role(self, role_name: str) -> None:
        """DROP ROLE IF EXISTS."""
        with self._connect() as conn:
            conn.execute(
                psycopg.sql.SQL("DROP ROLE IF EXISTS {}").format(
                    psycopg.sql.Identifier(role_name)
                )
            )
        self.logger.info(f"Dropped role: {role_name}")
