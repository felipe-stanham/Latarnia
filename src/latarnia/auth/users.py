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
    # Deactivation / deletion
    # ------------------------------------------------------------------

    def deactivate_user(self, user_id, token_store=None) -> None:
        """Deactivate, invalidate sessions, and revoke machine tokens."""
        self.db.execute("UPDATE users SET is_active = FALSE WHERE id = %s", (user_id,))
        self.db.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
        if token_store is not None:
            token_store.revoke_all_for_user(user_id)
        logger.info("Deactivated user %s, cleared sessions, revoked machine tokens", user_id)

    def delete_user(self, user_id, requester_id) -> None:
        """Permanently delete a user and all their owned rows (CASCADE).

        Guards:
        - No self-delete (requester_id == user_id → ValueError).
        - Cannot delete the last active Superuser → ValueError.
        The granted_by FK in app_roles/machine_tokens is SET NULL (migration 006),
        so other users' roles/tokens granted by the deleted user survive.
        """
        if str(requester_id) == str(user_id):
            raise ValueError("Cannot delete yourself")
        target = self.get_user_by_id(user_id)
        if not target:
            raise LookupError("User not found")
        if target["is_superuser"] and target["is_active"]:
            row = self.db.query_one(
                "SELECT COUNT(*) AS n FROM users WHERE is_superuser = TRUE AND is_active = TRUE"
            )
            if int((row or {}).get("n", 0)) <= 1:
                raise ValueError("Cannot delete the last active Superuser")
        self.db.execute("DELETE FROM users WHERE id = %s", (user_id,))
        logger.info("Deleted user %s (by %s)", user_id, requester_id)

    # ------------------------------------------------------------------
    # Reactivation
    # ------------------------------------------------------------------

    def reactivate_user(self, user_id, totp_provider=None) -> None:
        """Re-activate a deactivated user who still holds TOTP credentials.

        Raises ValueError (409) if the user has no TOTP credential — the caller
        should re-issue a setup token instead.
        """
        target = self.get_user_by_id(user_id)
        if not target:
            raise LookupError("User not found")
        if totp_provider is not None and totp_provider.get_existing_secret(user_id) is None:
            raise ValueError("User has no TOTP credential — re-issue a setup token first")
        self.db.execute("UPDATE users SET is_active = TRUE WHERE id = %s", (user_id,))
        logger.info("Reactivated user %s", user_id)

    # ------------------------------------------------------------------
    # Re-issue TOTP setup
    # ------------------------------------------------------------------

    def reissue_setup_token(self, user_id, totp_provider, token_store,
                            setup_ttl_hours: int = 24) -> str:
        """Deactivate the user, delete their TOTP credential, revoke machine tokens,
        and mint a fresh setup token. Returns the raw setup token.

        The caller should present the returned token as a setup URL
        (/auth/setup?token=<token>). Completing setup via that URL will mint a
        fresh TOTP secret and re-activate the user.
        """
        # Delete the existing TOTP credential so ensure_credentials mints a
        # fresh secret (rather than returning the old one via get_existing_secret).
        self.db.execute(
            "DELETE FROM user_credentials WHERE user_id = %s AND auth_method = 'totp'",
            (user_id,),
        )
        # Revoke all active machine tokens.
        token_store.revoke_all_for_user(user_id)
        # Invalidate all sessions.
        self.db.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
        # Deactivate and mint a new setup token.
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(hours=setup_ttl_hours)
        self.db.execute(
            "UPDATE users SET is_active = FALSE, setup_token = %s, "
            "setup_token_expires_at = %s WHERE id = %s",
            (token, expires, user_id),
        )
        logger.info("Re-issued setup token for user %s (old TOTP deleted, tokens revoked)", user_id)
        return token
