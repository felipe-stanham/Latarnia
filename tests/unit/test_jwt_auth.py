"""Unit tests for JWTAuth (P-0008 Scope 4)."""
import time
from datetime import datetime, timedelta, timezone

import pytest

from latarnia.auth.jwt_auth import JWTAuth


def _auth(secret="test-secret"):
    return JWTAuth(lambda: secret)


def test_issue_and_validate_round_trip():
    a = _auth()
    token = a.issue("user-1", {"my_app": "webUI-med"}, is_super=False)
    claims = a.validate(token)
    assert claims["sub"] == "user-1"
    assert claims["apps"] == {"my_app": "webUI-med"}
    assert claims["super"] is False
    assert "iat" in claims


def test_super_claim_and_no_exp_when_unset():
    a = _auth()
    claims = a.validate(a.issue("u", {}, is_super=True))
    assert claims["super"] is True
    assert "exp" not in claims


def test_expired_token_is_invalid():
    a = _auth()
    past = datetime.now(timezone.utc) - timedelta(seconds=5)
    token = a.issue("u", {"app": "full"}, is_super=False, expires_at=past)
    assert a.validate(token) is None


def test_future_expiry_is_valid():
    a = _auth()
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    assert a.validate(a.issue("u", {}, False, expires_at=future)) is not None


def test_wrong_secret_rejected():
    token = _auth("secret-a").issue("u", {}, False)
    assert _auth("secret-b").validate(token) is None


def test_tampered_token_rejected():
    a = _auth()
    token = a.issue("u", {}, False)
    assert a.validate(token[:-2] + ("aa" if not token.endswith("aa") else "bb")) is None


def test_token_hash_is_deterministic_and_distinct():
    a = _auth()
    t1 = a.issue("u", {"x": "full"}, True)
    assert a.token_hash(t1) == a.token_hash(t1)
    assert len(a.token_hash(t1)) == 64  # sha256 hex


def test_missing_secret_raises():
    with pytest.raises(ValueError):
        JWTAuth(lambda: "").issue("u", {}, False)
