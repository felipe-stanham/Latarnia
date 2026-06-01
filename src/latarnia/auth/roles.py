"""Per-app role store (P-0008 Scope 3).

Roles: none, webUI-low, webUI-med, webUI-full, full. Absence of an `app_roles`
row means effective role 'none'. Only a superuser may grant the `full` role —
enforced here (server-side) regardless of the calling endpoint.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("latarnia.auth.roles")

ROLE_VALUES = ["none", "webUI-low", "webUI-med", "webUI-full", "full"]


class RoleStore:
    def __init__(self, auth_db, user_store):
        self.db = auth_db
        self.users = user_store

    def get_role(self, user_id, app_name: str) -> str:
        row = self.db.query_one(
            "SELECT role FROM app_roles WHERE user_id = %s AND app_name = %s",
            (user_id, app_name),
        )
        return row["role"] if row else "none"

    def is_visible(self, user, app_name: str) -> bool:
        """Whether `user` should see `app_name` on the dashboard (cap-014/015).

        Superusers and unauthenticated direct access (dev, user=None) see all
        apps; everyone else sees an app only if their role for it isn't 'none'.
        """
        if user is None or user["is_superuser"]:
            return True
        return self.get_role(user["id"], app_name) != "none"

    def get_all_roles(self, user_id) -> dict:
        rows = self.db.query(
            "SELECT app_name, role FROM app_roles WHERE user_id = %s", (user_id,)
        )
        return {r["app_name"]: r["role"] for r in rows}

    def set_role(self, user_id, app_name: str, role: str, granted_by) -> None:
        """Assign (or clear) a user's role for an app.

        Raises ValueError on an unknown role, PermissionError if a non-superuser
        attempts to grant `full`. Setting `none` removes the row (effective
        role is 'none' by absence).
        """
        if role not in ROLE_VALUES:
            raise ValueError(f"Invalid role: {role!r}")
        if role == "full":
            grantor = self.users.get_user_by_id(granted_by)
            if not grantor or not grantor["is_superuser"]:
                raise PermissionError("Only a superuser can assign the 'full' role")

        if role == "none":
            self.db.execute(
                "DELETE FROM app_roles WHERE user_id = %s AND app_name = %s",
                (user_id, app_name),
            )
        else:
            self.db.execute(
                "INSERT INTO app_roles (user_id, app_name, role, granted_by) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (user_id, app_name) DO UPDATE "
                "SET role = EXCLUDED.role, granted_by = EXCLUDED.granted_by, "
                "    granted_at = NOW()",
                (user_id, app_name, role, granted_by),
            )
        logger.info("Set role %s=%s for user %s (by %s)", app_name, role, user_id, granted_by)

    def get_users_with_roles(self) -> list:
        """All users with their role maps — for the Users & Roles admin UI."""
        result = []
        for u in self.users.list_users():
            result.append({
                "id": str(u["id"]),
                "username": u["username"],
                "is_superuser": u["is_superuser"],
                "is_active": u["is_active"],
                "last_login_at": u["last_login_at"].isoformat() if u["last_login_at"] else None,
                "roles": self.get_all_roles(u["id"]),
            })
        return result
