"""
JWT machine-token signing/validation (P-0008 Scope 4).

HS256 signed with `LATARNIA_JWT_SECRET` (loaded lazily, never logged). Claims:
  sub  - user_id (str)
  iat  - issued-at (unix)
  exp  - expiry (unix; omitted for never-expiring tokens)
  apps - {app_name: role} scope copied from the machine_tokens record
  super- bool (true => full access to all apps, mirrors the user record)

Revocation is a DB concern (machine_tokens.revoked_at) checked per request by
the middleware; this module only handles signing/verification + hashing.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime
from typing import Callable, Optional

import jwt

ALGORITHM = "HS256"


class JWTAuth:
    def __init__(self, secret_loader: Callable[[], str]):
        self._secret_loader = secret_loader

    def _secret(self) -> str:
        secret = self._secret_loader()
        if not secret:
            raise ValueError("LATARNIA_JWT_SECRET is not set")
        return secret

    def issue(self, user_id, app_scope: dict, is_super: bool,
              expires_at: Optional[datetime] = None) -> str:
        now = int(time.time())
        payload = {
            "sub": str(user_id),
            "iat": now,
            "apps": app_scope or {},
            "super": bool(is_super),
        }
        if expires_at is not None:
            payload["exp"] = int(expires_at.timestamp())
        return jwt.encode(payload, self._secret(), algorithm=ALGORITHM)

    def validate(self, token: str) -> Optional[dict]:
        """Return claims for a valid, unexpired, correctly-signed token, else None."""
        if not token:
            return None
        try:
            return jwt.decode(token, self._secret(), algorithms=[ALGORITHM])
        except jwt.PyJWTError:
            return None

    @staticmethod
    def token_hash(token: str) -> str:
        """SHA-256 hex of the raw JWT — the revocation lookup key."""
        return hashlib.sha256(token.encode()).hexdigest()
