"""TOTP authentication provider (P-0008, V1).

RFC 6238 time-based one-time passwords. The 20-byte base32 secret is encrypted
at rest with AES-256-GCM using `LATARNIA_TOTP_ENC_KEY` and stored in
`user_credentials.credential_data.totp_secret_enc`. Plaintext is never written
to disk or logged. Replay is prevented by recording the last accepted 30s
window per user and rejecting codes from that window or earlier.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Callable, Optional

import pyotp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger("latarnia.auth.totp")

_NONCE_BYTES = 12
TOTP_PERIOD = 30


class TOTPAuthProvider:
    """AuthProvider implementation backed by `user_credentials` rows."""

    auth_method = "totp"

    def __init__(self, auth_db, key_loader: Callable[[], bytes], issuer: str = "Latarnia"):
        """`key_loader` returns the raw 32-byte AES key (loaded lazily so the
        secret can come from secrets.env at call time, never cached on disk)."""
        self.db = auth_db
        self._key_loader = key_loader
        self.issuer = issuer

    # ------------------------------------------------------------------
    # Crypto (pure functions — unit-testable without a DB)
    # ------------------------------------------------------------------

    @staticmethod
    def generate_secret() -> str:
        """A fresh base32 TOTP secret (160 bits)."""
        return pyotp.random_base32()

    @staticmethod
    def encrypt_secret(plaintext: str, key: bytes) -> str:
        """AES-256-GCM encrypt; return base64(nonce || ciphertext+tag)."""
        nonce = os.urandom(_NONCE_BYTES)
        ct = AESGCM(key).encrypt(nonce, plaintext.encode(), None)
        return base64.b64encode(nonce + ct).decode()

    @staticmethod
    def decrypt_secret(ciphertext_b64: str, key: bytes) -> str:
        """Inverse of encrypt_secret."""
        raw = base64.b64decode(ciphertext_b64)
        nonce, ct = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
        return AESGCM(key).decrypt(nonce, ct, None).decode()

    def _key(self) -> bytes:
        key = self._key_loader()
        if not key or len(key) != 32:
            raise ValueError(
                "LATARNIA_TOTP_ENC_KEY must decode to exactly 32 bytes"
            )
        return key

    # ------------------------------------------------------------------
    # AuthProvider protocol
    # ------------------------------------------------------------------

    def setup_credentials(self, user_id: str, *, username: str = "", **kwargs) -> dict:
        """Generate + store an encrypted secret; return QR provisioning data."""
        secret = self.generate_secret()
        enc = self.encrypt_secret(secret, self._key())
        cred = {"totp_secret_enc": enc, "last_totp_window": 0}
        self.db.execute(
            "INSERT INTO user_credentials (user_id, auth_method, credential_data) "
            "VALUES (%s, 'totp', %s::jsonb) "
            "ON CONFLICT (user_id, auth_method) DO UPDATE "
            "SET credential_data = EXCLUDED.credential_data, updated_at = NOW()",
            (user_id, json.dumps(cred)),
        )
        uri = pyotp.TOTP(secret).provisioning_uri(
            name=username or str(user_id), issuer_name=self.issuer
        )
        logger.info("Provisioned TOTP credentials for user %s", user_id)
        return {"secret": secret, "otpauth_uri": uri}

    def get_existing_secret(self, user_id: str) -> Optional[str]:
        """Decrypt and return the stored TOTP secret, or None if not set up."""
        row = self.db.query_one(
            "SELECT credential_data FROM user_credentials "
            "WHERE user_id = %s AND auth_method = 'totp'",
            (user_id,),
        )
        enc = (row or {}).get("credential_data", {}).get("totp_secret_enc") if row else None
        if not enc:
            return None
        try:
            return self.decrypt_secret(enc, self._key())
        except Exception:  # pragma: no cover - misconfigured key
            return None

    def ensure_credentials(self, user_id: str, *, username: str = "") -> dict:
        """Return QR provisioning data, provisioning a secret only if missing.

        Keeps the QR stable across setup-page refreshes (re-provisioning would
        invalidate an already-scanned secret).
        """
        secret = self.get_existing_secret(user_id)
        if secret is None:
            return self.setup_credentials(user_id, username=username)
        uri = pyotp.TOTP(secret).provisioning_uri(
            name=username or str(user_id), issuer_name=self.issuer
        )
        return {"secret": secret, "otpauth_uri": uri}

    def validate(self, user_id: str, submission: dict) -> bool:
        """Validate a 6-digit code with ±1 window tolerance and replay reject."""
        code = (submission or {}).get("code", "")
        code = "".join(ch for ch in str(code) if ch.isdigit())
        if len(code) != 6:
            return False

        row = self.db.query_one(
            "SELECT credential_data FROM user_credentials "
            "WHERE user_id = %s AND auth_method = 'totp'",
            (user_id,),
        )
        if not row:
            return False
        enc = (row["credential_data"] or {}).get("totp_secret_enc")
        if not enc:
            return False

        try:
            secret = self.decrypt_secret(enc, self._key())
        except Exception as exc:  # pragma: no cover - misconfigured key
            logger.error("TOTP secret decryption failed for user %s: %s", user_id, exc)
            return False

        totp = pyotp.TOTP(secret)
        now = int(time.time())
        matched_window: Optional[int] = None
        for offset in (-1, 0, 1):
            t = now + offset * TOTP_PERIOD
            if totp.verify(code, for_time=t, valid_window=0):
                matched_window = t // TOTP_PERIOD
                break

        if matched_window is None:
            return False

        # Atomic replay defense: the UPDATE only succeeds (RETURNING a row) if
        # the stored window is strictly older than the matched one. Two
        # concurrent logins reusing the same window code can't both win — the
        # second sees last_totp_window already advanced and gets no row back.
        updated = self.db.execute_returning(
            "UPDATE user_credentials "
            "SET credential_data = jsonb_set(credential_data, "
            "        '{last_totp_window}', to_jsonb(%s::bigint)), "
            "    updated_at = NOW() "
            "WHERE user_id = %s AND auth_method = 'totp' "
            "  AND COALESCE((credential_data->>'last_totp_window')::bigint, 0) < %s "
            "RETURNING id",
            (matched_window, user_id, matched_window),
        )
        if updated is None:
            logger.warning("Rejected replayed TOTP code for user %s", user_id)
            return False
        return True

    def get_setup_form_spec(self) -> dict:
        return {
            "method": "totp",
            "fields": [
                {"name": "code", "label": "6-digit code", "type": "text",
                 "inputmode": "numeric", "maxlength": 6, "required": True},
            ],
        }
