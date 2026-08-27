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


class TestStrictMode:
    """The consumer whose legacy tokens carry the type under another name."""

    def test_untyped_token_is_refused_under_strict(self) -> None:
        payload = {"sub": "u1", "email": "a@b.c"}
        assert token_type_allowed(payload, (ACCESS_TOKEN_TYPE,), strict=True) is False

    def test_strict_leaves_the_sdk_markers_working(self) -> None:
        """Strict refuses the *unclassifiable*, not the classified-by-fallback.

        A pre-`typ` refresh token still carries `refresh: True`, and a
        pre-`typ` MFA token still carries `purpose: "mfa_pending"`. Strict
        must keep reading those, or upgrading would reject the very tokens
        the fallback was written for.
        """
        refresh = {"sub": "u1", "refresh": True}
        assert token_type_allowed(refresh, (REFRESH_TOKEN_TYPE,), strict=True) is True
        assert token_type_allowed(refresh, (ACCESS_TOKEN_TYPE,), strict=True) is False

        pending = {"sub": "u1", "purpose": "mfa_pending"}
        assert token_type_allowed(pending, (MFA_TOKEN_TYPE,), strict=True) is True

    def test_declared_typ_is_unaffected_by_strict(self) -> None:
        payload = {"sub": "u1", "typ": ACCESS_TOKEN_TYPE}
        assert token_type_allowed(payload, (ACCESS_TOKEN_TYPE,), strict=True) is True


class TestLegacyClaims:
    """The shape that shipped the hole: a refresh token with `type`, not `typ`."""

    def test_foreign_claim_is_waved_through_by_default(self) -> None:
        """The defect the issue reports, pinned as it behaves today.

        Without `legacy_claims` the SDK cannot see the consumer's own type
        marker, so a refresh token reaches an access-only call site and is
        accepted — for as long as the refresh TTL lasts.
        """
        payload = {"sub": "u1", "type": REFRESH_TOKEN_TYPE}
        assert token_type_allowed(payload, (ACCESS_TOKEN_TYPE,)) is True

    def test_foreign_claim_classifies_the_token(self) -> None:
        payload = {"sub": "u1", "type": REFRESH_TOKEN_TYPE}
        assert (
            token_type_allowed(payload, (ACCESS_TOKEN_TYPE,), legacy_claims=("type",))
            is False
        )
        assert (
            token_type_allowed(payload, (REFRESH_TOKEN_TYPE,), legacy_claims=("type",))
            is True
        )

    def test_typ_still_wins_over_a_legacy_claim(self) -> None:
        payload = {"sub": "u1", "typ": ACCESS_TOKEN_TYPE, "type": REFRESH_TOKEN_TYPE}
        assert (
            token_type_allowed(payload, (ACCESS_TOKEN_TYPE,), legacy_claims=("type",))
            is True
        )

    def test_claims_are_read_in_order(self) -> None:
        payload = {"sub": "u1", "token_type": REFRESH_TOKEN_TYPE}
        assert (
            token_type_allowed(
                payload,
                (REFRESH_TOKEN_TYPE,),
                legacy_claims=("type", "token_type"),
            )
            is True
        )

    def test_non_string_legacy_claim_is_ignored(self) -> None:
        payload = {"sub": "u1", "type": 7}
        assert (
            token_type_allowed(
                payload, (ACCESS_TOKEN_TYPE,), strict=True, legacy_claims=("type",)
            )
            is False
        )
