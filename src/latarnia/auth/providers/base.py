"""AuthProvider protocol (P-0008).

Abstracts method-specific authentication logic away from routes, sessions, and
role enforcement. The V1 implementation is TOTP; future methods (password,
passkey, OAuth2) implement the same protocol without touching callers.

Each provider owns the shape of its own `user_credentials.credential_data`
and is responsible for reading/writing it.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AuthProvider(Protocol):
    """Interface every authentication method implements."""

    #: Value stored in `user_credentials.auth_method` (e.g. "totp").
    auth_method: str

    def setup_credentials(self, user_id: str, **kwargs) -> dict:
        """Provision credentials for a user and return setup data.

        For TOTP this generates + stores the encrypted secret and returns the
        provisioning URI / manual key for QR rendering. Idempotent per
        (user, method): re-running replaces the stored secret.
        """
        ...

    def validate(self, user_id: str, submission: dict) -> bool:
        """Validate a login/setup attempt. Returns True iff it succeeds."""
        ...

    def get_setup_form_spec(self) -> dict:
        """UI hints for the setup/login page (field names, labels, lengths)."""
        ...
