"""Unit tests for TOTPAuthProvider (P-0008 Scope 2) — mocks the DB."""
import os
from unittest.mock import Mock

import pyotp
import pytest

from latarnia.auth.providers.totp import TOTPAuthProvider


def _key():
    return os.urandom(32)


def _provider(db=None, key=None):
    key = key or _key()
    return TOTPAuthProvider(db or Mock(), key_loader=lambda: key, issuer="Test"), key


def test_encrypt_decrypt_round_trip():
    provider, key = _provider()
    secret = provider.generate_secret()
    enc = provider.encrypt_secret(secret, key)
    assert enc != secret  # ciphertext, not plaintext base32
    assert provider.decrypt_secret(enc, key) == secret


def test_encrypt_uses_fresh_nonce_each_time():
    provider, key = _provider()
    s = provider.generate_secret()
    assert provider.encrypt_secret(s, key) != provider.encrypt_secret(s, key)


def test_bad_key_length_rejected():
    provider, _ = _provider(key=b"too-short")
    with pytest.raises(ValueError):
        provider.setup_credentials("uid", username="bob")


def test_setup_credentials_persists_encrypted_secret():
    db = Mock()
    provider, key = _provider(db=db)
    data = provider.setup_credentials("uid-1", username="alice")
    assert "otpauth_uri" in data and data["otpauth_uri"].startswith("otpauth://totp/")
    # The persisted credential_data (params tuple element 1) must hold
    # ciphertext, not the base32 secret.
    params = db.execute.call_args.args[1]
    cred_json = params[1]
    assert "totp_secret_enc" in cred_json
    assert data["secret"] not in cred_json


def test_validate_accepts_current_code():
    db = Mock()
    provider, key = _provider(db=db)
    secret = provider.generate_secret()
    enc = provider.encrypt_secret(secret, key)
    db.query_one.return_value = {
        "credential_data": {"totp_secret_enc": enc, "last_totp_window": 0}
    }
    # The atomic conditional UPDATE returns a row -> window was advanced.
    db.execute_returning.return_value = {"id": "x"}
    code = pyotp.TOTP(secret).now()
    assert provider.validate("uid", {"code": code}) is True
    assert db.execute_returning.called  # last-used window persisted atomically


def test_validate_rejects_replayed_window():
    db = Mock()
    provider, key = _provider(db=db)
    secret = provider.generate_secret()
    enc = provider.encrypt_secret(secret, key)
    db.query_one.return_value = {
        "credential_data": {"totp_secret_enc": enc, "last_totp_window": 0}
    }
    # The conditional UPDATE matched no row (window already consumed) -> replay.
    db.execute_returning.return_value = None
    code = pyotp.TOTP(secret).now()
    assert provider.validate("uid", {"code": code}) is False


def test_validate_rejects_wrong_code_and_bad_shape():
    db = Mock()
    provider, key = _provider(db=db)
    secret = provider.generate_secret()
    enc = provider.encrypt_secret(secret, key)
    db.query_one.return_value = {
        "credential_data": {"totp_secret_enc": enc, "last_totp_window": 0}
    }
    assert provider.validate("uid", {"code": "000000"}) is False
    assert provider.validate("uid", {"code": "12"}) is False
    assert provider.validate("uid", {"code": "abcdef"}) is False


def test_validate_no_credential_row():
    db = Mock()
    db.query_one.return_value = None
    provider, key = _provider(db=db)
    assert provider.validate("uid", {"code": "123456"}) is False


def test_ensure_credentials_reuses_existing_secret():
    db = Mock()
    provider, key = _provider(db=db)
    secret = provider.generate_secret()
    enc = provider.encrypt_secret(secret, key)
    db.query_one.return_value = {"credential_data": {"totp_secret_enc": enc}}
    data = provider.ensure_credentials("uid", username="alice")
    assert data["secret"] == secret  # decrypted existing, not regenerated
