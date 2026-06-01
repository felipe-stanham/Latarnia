"""User store (P-0008).

CRUD over the `users` table plus the setup-token lifecycle. The first active
user is the superuser, bootstrapped under username ``admin`` on first run.
Invited users are created inactive with a one-time setup token and become
active once they complete TOTP enrollment.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("latarnia.auth.users")

# Username assigned to the first (bootstrap) superuser created on first run.
BOOTSTRAP_USERNAME = "admin"

_USER_COLS = (
    "id, username, is_superuser, is_active, "
    "setup_token, setup_token_expires_at, created_at, last_login_at"
)


class UserStore:
    def __init__(self, auth_db):
        self.db = auth_db

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get_user_by_id(self, user_id):
        return self.db.query_one(
            f"SELECT {_USER_COLS} FROM users WHERE id = %s", (user_id,)
        )

    def get_user_by_username(self, username: str):
        return self.db.query_one(
            f"SELECT {_USER_COLS} FROM users WHERE username = %s", (username,)
        )

    def list_users(self) -> list:
        return self.db.query(
            f"SELECT {_USER_COLS} FROM users ORDER BY created_at"
        )

    def count_active_users(self) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM users WHERE is_active = TRUE"
        )
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    def create_user(self, username: str, is_superuser: bool = False,
                    setup_ttl_hours: int = 24):
        """Create an inactive user with a one-time setup token.

        Returns (user_row, setup_token). The invitee completes enrollment at
        /auth/setup?token=<setup_token>.
        """
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(hours=setup_ttl_hours)
        row = self.db.execute_returning(
            "INSERT INTO users (username, is_superuser, is_active, "
            "setup_token, setup_token_expires_at) "
            f"VALUES (%s, %s, FALSE, %s, %s) RETURNING {_USER_COLS}",
            (username, is_superuser, token, expires),
        )
        logger.info("Created user %s (superuser=%s)", username, is_superuser)
        return row, token

    def get_or_create_bootstrap_superuser(self):
        """First-run: the single inactive superuser candidate (username admin).

        Reused across setup-page refreshes so we don't create duplicate rows.
        """
        existing = self.get_user_by_username(BOOTSTRAP_USERNAME)
        if existing:
            return existing
        row = self.db.execute_returning(
            "INSERT INTO users (username, is_superuser, is_active) "
            f"VALUES (%s, TRUE, FALSE) RETURNING {_USER_COLS}",
            (BOOTSTRAP_USERNAME,),
        )
        logger.info("Bootstrapped superuser %r (inactive until first code)", BOOTSTRAP_USERNAME)
        return row

    # ------------------------------------------------------------------
    # Setup token + activation
    # ------------------------------------------------------------------

    def get_user_by_setup_token(self, token: str):
        """Return the user for a valid, unexpired setup token, else None."""
        return self.db.query_one(
            f"SELECT {_USER_COLS} FROM users "
            "WHERE setup_token = %s AND setup_token_expires_at > NOW()",
            (token,),
        )

    def activate_user(self, user_id) -> None:
        """Mark active, clear the setup token, stamp last_login."""
        self.db.execute(
            "UPDATE users SET is_active = TRUE, setup_token = NULL, "
            "setup_token_expires_at = NULL, last_login_at = NOW() WHERE id = %s",
            (user_id,),
        )

    def touch_last_login(self, user_id) -> None:
        self.db.execute(
            "UPDATE users SET last_login_at = NOW() WHERE id = %s", (user_id,)
        )

    # ------------------------------------------------------------------
    # Deactivation
    # ------------------------------------------------------------------

    def deactivate_user(self, user_id) -> None:
        """Deactivate and invalidate all of the user's sessions."""
        self.db.execute("UPDATE users SET is_active = FALSE WHERE id = %s", (user_id,))
        self.db.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
        logger.info("Deactivated user %s and cleared their sessions", user_id)
