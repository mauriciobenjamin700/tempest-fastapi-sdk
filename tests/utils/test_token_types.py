"""Tests for the shared ``typ`` claim vocabulary."""

from __future__ import annotations

from tempest_fastapi_sdk import (
    ACCESS_TOKEN_TYPE,
    MFA_TOKEN_TYPE,
    REFRESH_TOKEN_TYPE,
    token_type_allowed,
)


class TestDeclaredType:
    def test_matching_typ_is_allowed(self) -> None:
        payload = {"sub": "u1", "typ": ACCESS_TOKEN_TYPE}
        assert token_type_allowed(payload, (ACCESS_TOKEN_TYPE,)) is True

    def test_other_typ_is_rejected(self) -> None:
        for declared in (REFRESH_TOKEN_TYPE, MFA_TOKEN_TYPE, "something-else"):
            payload = {"sub": "u1", "typ": declared}
            assert token_type_allowed(payload, (ACCESS_TOKEN_TYPE,)) is False

    def test_typ_wins_over_legacy_markers(self) -> None:
        payload = {"sub": "u1", "typ": REFRESH_TOKEN_TYPE, "refresh": True}
        assert token_type_allowed(payload, (REFRESH_TOKEN_TYPE,)) is True
        assert token_type_allowed(payload, (ACCESS_TOKEN_TYPE,)) is False

    def test_non_string_typ_falls_through_to_markers(self) -> None:
        payload = {"sub": "u1", "typ": 7, "refresh": True}
        assert token_type_allowed(payload, (ACCESS_TOKEN_TYPE,)) is False


class TestLegacyMarkers:
    def test_refresh_marker_classifies_as_refresh(self) -> None:
        payload = {"sub": "u1", "refresh": True}
        assert token_type_allowed(payload, (ACCESS_TOKEN_TYPE,)) is False
        assert token_type_allowed(payload, (REFRESH_TOKEN_TYPE,)) is True

    def test_mfa_pending_marker_classifies_as_mfa(self) -> None:
        payload = {"sub": "u1", "purpose": "mfa_pending"}
        assert token_type_allowed(payload, (ACCESS_TOKEN_TYPE,)) is False
        assert token_type_allowed(payload, (MFA_TOKEN_TYPE,)) is True

    def test_refresh_false_is_not_a_marker(self) -> None:
        payload = {"sub": "u1", "refresh": False}
        assert token_type_allowed(payload, (ACCESS_TOKEN_TYPE,)) is True

    def test_untyped_token_is_accepted_for_back_compat(self) -> None:
        payload = {"sub": "u1", "email": "a@b.c"}
        assert token_type_allowed(payload, (ACCESS_TOKEN_TYPE,)) is True
