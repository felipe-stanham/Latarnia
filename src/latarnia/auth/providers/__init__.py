"""Pluggable authentication providers (P-0008).

`AuthProvider` is the protocol; `TOTPAuthProvider` is the V1 implementation.
Routes, sessions, and role logic depend only on the protocol — adding a new
method (password, passkey, OAuth2) means a new provider, not changes here.
"""

from .base import AuthProvider
from .totp import TOTPAuthProvider

__all__ = ["AuthProvider", "TOTPAuthProvider"]
