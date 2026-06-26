"""Machine-token store (P-0008 Scope 4) — CRUD over `machine_tokens`."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("latarnia.auth.tokens")


class MachineTokenStore:
    def __init__(self, auth_db, jwt_auth):
        self.db = auth_db
        self.jwt = jwt_auth

    def create_token(self, user_id, label: str, app_scope: dict, is_super: bool,
                     granted_by, expires_at: Optional[datetime] = None) -> str:
        """Sign a JWT, persist its record, and return the raw token (shown once)."""
        raw = self.jwt.issue(user_id, app_scope, is_super, expires_at)
        self.db.execute(
            "INSERT INTO machine_tokens "
            "(user_id, label, token_hash, app_scope, expires_at, granted_by) "
            "VALUES (%s, %s, %s, %s::jsonb, %s, %s)",
            (user_id, label, self.jwt.token_hash(raw),
             json.dumps(app_scope or {}), expires_at, granted_by),
        )
        logger.info("Issued machine token %r for user %s", label, user_id)
        return raw

    def list_tokens(self, user_id) -> list:
        rows = self.db.query(
            "SELECT id, label, app_scope, expires_at, created_at, revoked_at "
            "FROM machine_tokens WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
        return [{
            "id": str(r["id"]),
            "label": r["label"],
            "app_scope": r["app_scope"],
            "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "revoked_at": r["revoked_at"].isoformat() if r["revoked_at"] else None,
        } for r in rows]

    def revoke(self, token_id, user_id) -> bool:
        """Revoke one of the user's tokens. Returns False if not found."""
        row = self.db.execute_returning(
            "UPDATE machine_tokens SET revoked_at = NOW() "
            "WHERE id = %s AND user_id = %s AND revoked_at IS NULL RETURNING id",
            (token_id, user_id),
        )
        return row is not None

    def revoke_all_for_user(self, user_id) -> None:
        """Revoke all active machine tokens for a user (deactivate / re-issue)."""
        self.db.execute(
            "UPDATE machine_tokens SET revoked_at = NOW() "
            "WHERE user_id = %s AND revoked_at IS NULL",
            (user_id,),
        )
        logger.info("Revoked all active machine tokens for user %s", user_id)

    def is_active(self, token_hash: str) -> bool:
        """True iff a non-revoked record exists for this token hash.

        A self-contained-but-unknown JWT (no DB row) is treated as inactive,
        so deleting/never-recording a token also rejects it.
        """
        row = self.db.query_one(
            "SELECT revoked_at FROM machine_tokens WHERE token_hash = %s",
            (token_hash,),
        )
        return bool(row) and row["revoked_at"] is None
