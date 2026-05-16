"""Tests for tempest_fastapi_sdk.utils.jwt.JWTUtils."""

from datetime import timedelta

import pytest

from tempest_fastapi_sdk import (
    ExpiredTokenException,
    InvalidTokenException,
    JWTUtils,
)


class TestEncodeDecode:
    def test_round_trip(self) -> None:
        utils = JWTUtils(secret="secret")
        token = utils.encode({"sub": "user-123"})
        payload = utils.decode(token)
        assert payload["sub"] == "user-123"
        assert "iat" in payload
        assert "exp" in payload

    def test_issuer_is_added_and_verified(self) -> None:
        utils = JWTUtils(secret="secret", issuer="tempest")
        token = utils.encode({"sub": "user-123"})
        payload = utils.decode(token)
        assert payload["iss"] == "tempest"

    def test_wrong_issuer_raises_invalid(self) -> None:
        signer = JWTUtils(secret="secret", issuer="tempest")
        token = signer.encode({"sub": "u"})
        verifier = JWTUtils(secret="secret", issuer="other")
        with pytest.raises(InvalidTokenException):
            verifier.decode(token)

    def test_wrong_secret_raises_invalid(self) -> None:
        signer = JWTUtils(secret="a")
        token = signer.encode({"sub": "u"})
        verifier = JWTUtils(secret="b")
        with pytest.raises(InvalidTokenException):
            verifier.decode(token)

    def test_malformed_token_raises_invalid(self) -> None:
        utils = JWTUtils(secret="secret")
        with pytest.raises(InvalidTokenException):
            utils.decode("not.a.real.jwt")


class TestExpiry:
    def test_expired_token_raises(self) -> None:
        utils = JWTUtils(
            secret="secret",
            default_ttl=timedelta(seconds=-1),
        )
        token = utils.encode({"sub": "u"})
        with pytest.raises(ExpiredTokenException):
            utils.decode(token)

    def test_per_call_ttl_overrides_default(self) -> None:
        utils = JWTUtils(secret="secret", default_ttl=timedelta(hours=1))
        token = utils.encode({"sub": "u"}, ttl=timedelta(seconds=-1))
        with pytest.raises(ExpiredTokenException):
            utils.decode(token)


class TestDecodeOrNone:
    def test_returns_payload_on_success(self) -> None:
        utils = JWTUtils(secret="secret")
        token = utils.encode({"sub": "u"})
        result = utils.decode_or_none(token)
        assert result is not None
        assert result["sub"] == "u"

    def test_returns_none_on_invalid(self) -> None:
        utils = JWTUtils(secret="secret")
        assert utils.decode_or_none("garbage") is None

    def test_returns_none_on_expired(self) -> None:
        utils = JWTUtils(
            secret="secret",
            default_ttl=timedelta(seconds=-1),
        )
        token = utils.encode({"sub": "u"})
        assert utils.decode_or_none(token) is None
