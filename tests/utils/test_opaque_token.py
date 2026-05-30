"""Tests for tempest_fastapi_sdk.utils.opaque_token."""

from tempest_fastapi_sdk import (
    generate_opaque_token,
    hash_opaque_token,
    verify_opaque_token,
)


class TestGenerate:
    def test_returns_plaintext_and_hash(self) -> None:
        plaintext, token_hash = generate_opaque_token()
        assert isinstance(plaintext, str) and plaintext
        assert len(token_hash) == 64  # sha256 hex
        assert token_hash == hash_opaque_token(plaintext)

    def test_tokens_are_unique(self) -> None:
        a, _ = generate_opaque_token()
        b, _ = generate_opaque_token()
        assert a != b

    def test_nbytes_controls_entropy(self) -> None:
        short, _ = generate_opaque_token(8)
        long, _ = generate_opaque_token(64)
        assert len(long) > len(short)


class TestVerify:
    def test_matching_token_verifies(self) -> None:
        plaintext, token_hash = generate_opaque_token()
        assert verify_opaque_token(plaintext, token_hash) is True

    def test_wrong_token_rejected(self) -> None:
        _, token_hash = generate_opaque_token()
        assert verify_opaque_token("not-the-token", token_hash) is False

    def test_plaintext_never_equals_hash(self) -> None:
        plaintext, token_hash = generate_opaque_token()
        assert plaintext != token_hash

    def test_hash_is_deterministic(self) -> None:
        assert hash_opaque_token("abc") == hash_opaque_token("abc")
