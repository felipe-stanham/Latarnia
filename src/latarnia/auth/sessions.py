"""Session store (P-0008).

Opaque, server-side-revocable sessions. The cookie value is a random UUID; the
DB stores only its SHA-256 hash and an expiry. Validation rejects expired rows;
expired rows are also cleaned up lazily at login (cheap GC, Pi scale).
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("latarnia.auth.sessions")


def hash_token(token: str) -> str:
    """SHA-256 hex digest of a raw session token."""
    return hashlib.sha256(token.encode()).hexdigest()


class SessionStore:
    def __init__(self, auth_db, config_manager):
        self.db = auth_db
        self.config_manager = config_manager

    def _ttl(self) -> timedelta:
        return timedelta(hours=self.config_manager.config.auth.session_ttl_hours)

    def create_session(self, user_id, ip: str = "") -> str:
        """Create a session and return the raw token (the cookie value)."""
        self._gc_expired()
        token = uuid.uuid4().hex
        expires = datetime.now(timezone.utc) + self._ttl()
        self.db.execute(
            "INSERT INTO sessions (user_id, token_hash, expires_at, ip_address) "
            "VALUES (%s, %s, %s, %s)",
            (user_id, hash_token(token), expires, ip or None),
        )
        logger.info("Created session for user %s", user_id)
        return token

    def validate_session(self, token: str):
        """Return user_id for a valid, unexpired session, else None."""
        if not token:
            return None
        row = self.db.query_one(
            "SELECT user_id FROM sessions "
            "WHERE token_hash = %s AND expires_at > NOW()",
            (hash_token(token),),
        )
        return row["user_id"] if row else None

    def invalidate_session(self, token: str) -> None:
        if not token:
            return
        self.db.execute(
            "DELETE FROM sessions WHERE token_hash = %s", (hash_token(token),)
        )

    def invalidate_all_sessions(self, user_id) -> None:
        self.db.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))

    def _gc_expired(self) -> None:
        try:
            self.db.execute("DELETE FROM sessions WHERE expires_at <= NOW()")
        except Exception as exc:  # pragma: no cover - housekeeping only
            logger.debug("Session GC skipped: %s", exc)
