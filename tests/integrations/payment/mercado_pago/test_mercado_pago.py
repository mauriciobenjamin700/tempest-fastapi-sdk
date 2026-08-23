"""The hand-written half of the Mercado Pago integration.

Money and webhook verification — the two things the specification does not
give us, and therefore the two that need tests of their own.

The webhook half is **ported**, so it is tested as a port: every rule of
``mercadopago/sdk-nodejs``, ``src/utils/webhook/index.ts`` at commit
``99857f33`` has a case here, and the digests are checked against vectors
computed with ``openssl`` rather than with the same :mod:`hmac` call under
test.
"""

from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal

import pytest

from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    DEFAULT_BASE_URL,
    DEFAULT_SIGNATURE_VERSIONS,
    MercadoPagoEvent,
    SignatureHeader,
    build_manifest,
    format_amount,
    from_cents,
    parse_signature_header,
    sign_manifest,
    to_cents,
    verify_signature,
)

SECRET: str = "a-webhook-secret"
DATA_ID: str = "1234567890"
REQUEST_ID: str = "b3f1c0d2-8a41-4d0e-9f5a-2c7e1f9b0a44"
TIMESTAMP: str = "1771891200"


class TestMoney:
    """Reais in, exact cents out.

    The mirror image of OpenPix, which states cents. Getting the unit wrong
    is a factor-of-100 error, so each direction is pinned.
    """

    def test_reais_float_becomes_exact_cents(self) -> None:
        """``19.9`` is 1990 cents, not 1989 and not 1990.0000000001."""
        assert to_cents(19.9) == 1990
        assert isinstance(to_cents(19.9), int)

    def test_the_float_expansion_does_not_leak(self) -> None:
        """A value that has no exact binary form still converts cleanly.

        ``Decimal(0.1)`` is ``0.1000000000000000055511151231257827``; the
        conversion routes floats through ``repr`` precisely so that this
        reads as ``Decimal("0.1")``.
        """
        assert to_cents(0.1) == 10
        assert to_cents(0.29) == 29

    def test_string_and_decimal_are_accepted(self) -> None:
        """A caller holding a ``Decimal`` should not have to downgrade it."""
        assert to_cents("19.90") == 1990
        assert to_cents(Decimal("19.90")) == 1990

    def test_fraction_of_a_cent_is_refused(self) -> None:
        """Rounding here would hide a real mismatch behind a plausible number."""
        with pytest.raises(ValueError, match="whole number of cents"):
            to_cents("19.905")

    def test_negative_is_refused(self) -> None:
        """A negative charge is a bug upstream, not a refund."""
        with pytest.raises(ValueError, match="must not be negative"):
            to_cents(-1)

    def test_cents_become_a_decimal_not_a_float(self) -> None:
        """Serializing a float can produce ``19.900000000000002``."""
        amount = from_cents(1990)

        assert amount == Decimal("19.90")
        assert isinstance(amount, Decimal)

    def test_round_trip_is_exact(self) -> None:
        """Every cent value survives both directions."""
        for cents in (1, 9, 99, 100, 1990, 123456):
            assert to_cents(from_cents(cents)) == cents

    def test_format_amount_has_two_places(self) -> None:
        """A receipt line shows ``19.90``, never ``19.9``."""
        assert format_amount(1990) == "19.90"
        assert format_amount(100) == "1.00"
        assert format_amount(5) == "0.05"


class TestSignatureHeader:
    """Parsing what arrives in ``x-signature``.

    The rules are ported from ``mercadopago/sdk-nodejs``,
    ``src/utils/webhook/index.ts`` at commit ``99857f33``
    (``parseSignatureHeader``).
    """

    def test_parses_both_components(self) -> None:
        """The documented shape is ``ts=...,v1=...``."""
        parsed = parse_signature_header("ts=1771891200,v1=abc123")
        assert parsed.timestamp == "1771891200"
        assert parsed.digest() == "abc123"

    def test_tolerates_whitespace_and_order(self) -> None:
        """Header whitespace is not a reason to reject a real delivery."""
        parsed = parse_signature_header(" v1=abc123 , ts=1771891200 ")
        assert parsed.timestamp == "1771891200"
        assert parsed.digest() == "abc123"

    def test_keys_are_case_insensitive(self) -> None:
        """Upstream lowercases the key before comparing it."""
        parsed = parse_signature_header("TS=1771891200,V1=abc123")
        assert parsed.timestamp == "1771891200"
        assert parsed.digest() == "abc123"

    def test_keeps_every_version_it_finds(self) -> None:
        """A header may carry more than one hash version."""
        parsed = parse_signature_header("ts=1771891200,v1=one,v2=two")
        assert parsed.hashes == {"v1": "one", "v2": "two"}
        assert parsed.digest() == "one"
        assert parsed.digest(("v2", "v1")) == "two"

    def test_unknown_components_are_ignored(self) -> None:
        """A component the provider adds later must not break parsing."""
        parsed = parse_signature_header("ts=1771891200,v1=abc123,foo=bar")
        assert parsed.hashes == {"v1": "abc123"}

    def test_missing_component_comes_back_empty(self) -> None:
        """A malformed header is an untrusted caller, not an exception."""
        assert parse_signature_header("ts=1771891200").digest() == ""
        assert parse_signature_header("garbage") == SignatureHeader()


class TestManifest:
    """The string that gets signed, byte for byte.

    Ported from ``buildManifest`` in ``mercadopago/sdk-nodejs``, commit
    ``99857f33``, and from the three "manifest omission rule" cases in its
    test suite. This is the half that a fixed template got wrong: an absent
    ``data.id`` used to sign ``id:;``, which the provider never signs.
    """

    def test_every_pair_present(self) -> None:
        """The full manifest, in the provider's order."""
        assert (
            build_manifest(data_id=DATA_ID, request_id=REQUEST_ID, timestamp=TIMESTAMP)
            == f"id:{DATA_ID};request-id:{REQUEST_ID};ts:{TIMESTAMP};"
        )

    def test_absent_data_id_drops_its_pair(self) -> None:
        """No ``id:`` at all — not an empty one."""
        manifest = build_manifest(request_id=REQUEST_ID, timestamp=TIMESTAMP)
        assert manifest == f"request-id:{REQUEST_ID};ts:{TIMESTAMP};"
        assert not manifest.startswith("id:")
        assert ";id:" not in manifest

    def test_absent_request_id_drops_its_pair(self) -> None:
        """Same rule on the other pair."""
        assert (
            build_manifest(data_id=DATA_ID, timestamp=TIMESTAMP)
            == f"id:{DATA_ID};ts:{TIMESTAMP};"
        )

    def test_both_absent_leaves_the_timestamp(self) -> None:
        """The timestamp is unconditional, and the trailing ``;`` stays."""
        assert build_manifest(timestamp=TIMESTAMP) == f"ts:{TIMESTAMP};"

    def test_whitespace_is_an_absent_value(self) -> None:
        """Upstream trims first, and a blank value drops its pair."""
        assert (
            build_manifest(data_id="   ", request_id="\t", timestamp=TIMESTAMP)
            == f"ts:{TIMESTAMP};"
        )

    def test_case_of_the_data_id_is_preserved(self) -> None:
        """Upstream signs what it was given; it does not lowercase.

        Their test suite pins both directions — an uppercase ``data.id``
        verifies against a manifest built with the uppercase value.
        """
        assert "ORD01JQ4S4KY8HWQ6NA5PXB65B3D3" in build_manifest(
            data_id="ORD01JQ4S4KY8HWQ6NA5PXB65B3D3", timestamp=TIMESTAMP
        )


class TestGoldenDigests:
    """Digests measured with a different HMAC implementation.

    Every expected value below came from
    ``printf '%s' '<manifest>' | openssl dgst -sha256 -hmac '<secret>' -hex``
    run on 2026-08-23, using the secret, request id and ``data.id`` from
    upstream's own test suite. Python's :mod:`hmac` agreeing with OpenSSL is
    what makes these vectors evidence rather than a restatement of this
    module's own arithmetic.
    """

    VECTOR_SECRET: str = "your_secret_key_here"
    VECTOR_REQUEST_ID: str = "2066ca19-c6f1-498a-be75-1923005edd06"
    VECTOR_DATA_ID: str = "ord01jq4s4ky8hwq6na5pxb65b3d3"
    VECTOR_TS: str = "1742505638683"

    def test_full_manifest_digest(self) -> None:
        """``id:...;request-id:...;ts:...;``"""
        assert (
            sign_manifest(
                secret=self.VECTOR_SECRET,
                data_id=self.VECTOR_DATA_ID,
                request_id=self.VECTOR_REQUEST_ID,
                timestamp=self.VECTOR_TS,
            )
            == "633f91233312dd391ec75fa0bea539cfc2d6c4918873305b84a96cc1c58db71c"
        )

    def test_digest_without_the_data_id(self) -> None:
        """``request-id:...;ts:...;`` — the case a fixed template broke."""
        assert (
            sign_manifest(
                secret=self.VECTOR_SECRET,
                request_id=self.VECTOR_REQUEST_ID,
                timestamp=self.VECTOR_TS,
            )
            == "8a7b0cc777a8217c3bab41a50c95dc92debbc6f8448f1c967dfe10ac1cb8b894"
        )

    def test_digest_of_the_timestamp_alone(self) -> None:
        """``ts:...;`` with both optional pairs absent."""
        assert (
            sign_manifest(secret=self.VECTOR_SECRET, timestamp=self.VECTOR_TS)
            == "3f075add3ddb923bebf0bc0648457b8244cf4420b98d5b445167d379bec0ca54"
        )


class TestVerifySignature:
    """What the HMAC does and does not prove.

    These exercise the ported algorithm end to end. They do **not** prove
    that Mercado Pago's live deliveries follow their own SDK — that still
    needs one real notification, and the module docstring says so.
    """

    def _header(self, digest: str, timestamp: str = TIMESTAMP) -> str:
        """Build a signature header carrying ``digest``.

        Args:
            digest (str): The hex digest.
            timestamp (str): The ``ts`` component.

        Returns:
            str: The header value.
        """
        return f"ts={timestamp},v1={digest}"

    def test_accepts_a_signature_it_produced(self) -> None:
        """The happy path, end to end."""
        digest = sign_manifest(
            secret=SECRET,
            data_id=DATA_ID,
            request_id=REQUEST_ID,
            timestamp=TIMESTAMP,
        )

        assert verify_signature(
            secret=SECRET,
            signature_header=self._header(digest),
            data_id=DATA_ID,
            request_id=REQUEST_ID,
        )

    def test_accepts_a_delivery_with_no_data_id(self) -> None:
        """The regression this release exists for.

        Before the omission rule, this delivery signed ``id:;request-id:...``
        on our side and ``request-id:...`` on the provider's, so a whole
        class of notification never verified.
        """
        digest = sign_manifest(
            secret=SECRET,
            request_id=REQUEST_ID,
            timestamp=TIMESTAMP,
        )

        assert verify_signature(
            secret=SECRET,
            signature_header=self._header(digest),
            request_id=REQUEST_ID,
        )

    def test_a_fixed_template_would_have_rejected_it(self) -> None:
        """Pins the defect itself, not only its fix.

        The old manifest is spelled out here rather than imported: the point
        is that hashing it produces something the provider's algorithm never
        produces, so the two can never agree on this delivery.
        """
        old_manifest = f"id:;request-id:{REQUEST_ID};ts:{TIMESTAMP};"
        new_manifest = build_manifest(request_id=REQUEST_ID, timestamp=TIMESTAMP)
        assert old_manifest != new_manifest

        old_digest = hmac.new(
            SECRET.encode("utf-8"), old_manifest.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        assert not verify_signature(
            secret=SECRET,
            signature_header=self._header(old_digest),
            request_id=REQUEST_ID,
        )

    def test_rejects_a_tampered_data_id(self) -> None:
        """Changing the resource the notification points at breaks it.

        This is the attack the signature exists to stop: a forged delivery
        pointing at somebody else's payment.
        """
        digest = sign_manifest(
            secret=SECRET,
            data_id=DATA_ID,
            request_id=REQUEST_ID,
            timestamp=TIMESTAMP,
        )

        assert not verify_signature(
            secret=SECRET,
            signature_header=self._header(digest),
            data_id="9999999999",
            request_id=REQUEST_ID,
        )

    def test_rejects_a_tampered_timestamp(self) -> None:
        """Replaying with a fresh timestamp invalidates the digest."""
        digest = sign_manifest(
            secret=SECRET,
            data_id=DATA_ID,
            request_id=REQUEST_ID,
            timestamp=TIMESTAMP,
        )

        assert not verify_signature(
            secret=SECRET,
            signature_header=f"ts=1771891999,v1={digest}",
            data_id=DATA_ID,
            request_id=REQUEST_ID,
        )

    def test_rejects_a_different_secret(self) -> None:
        """The secret is the whole trust anchor."""
        digest = sign_manifest(
            secret="another-secret",
            data_id=DATA_ID,
            request_id=REQUEST_ID,
            timestamp=TIMESTAMP,
        )

        assert not verify_signature(
            secret=SECRET,
            signature_header=self._header(digest),
            data_id=DATA_ID,
            request_id=REQUEST_ID,
        )

    def test_empty_secret_never_verifies(self) -> None:
        """ "No secret configured" must not mean "everything is valid".

        That default is how an endpoint ships unauthenticated while looking
        protected.
        """
        assert not verify_signature(
            secret="",
            signature_header=self._header("whatever"),
            data_id=DATA_ID,
            request_id=REQUEST_ID,
        )

    def test_malformed_header_does_not_raise(self) -> None:
        """An untrusted caller gets ``False``, not a traceback."""
        assert not verify_signature(
            secret=SECRET,
            signature_header="not-a-signature",
            data_id=DATA_ID,
            request_id=REQUEST_ID,
        )

    def test_non_numeric_timestamp_is_refused(self) -> None:
        """Upstream requires an all-digit ``ts``."""
        digest = sign_manifest(
            secret=SECRET, data_id=DATA_ID, request_id=REQUEST_ID, timestamp="abc"
        )

        assert not verify_signature(
            secret=SECRET,
            signature_header=f"ts=abc,v1={digest}",
            data_id=DATA_ID,
            request_id=REQUEST_ID,
        )

    def test_an_unsupported_version_is_a_rejection(self) -> None:
        """A migration to ``v2`` fails closed until a caller opts in."""
        digest = sign_manifest(
            secret=SECRET,
            data_id=DATA_ID,
            request_id=REQUEST_ID,
            timestamp=TIMESTAMP,
        )
        header = f"ts={TIMESTAMP},v2={digest}"

        assert not verify_signature(
            secret=SECRET,
            signature_header=header,
            data_id=DATA_ID,
            request_id=REQUEST_ID,
        )
        assert verify_signature(
            secret=SECRET,
            signature_header=header,
            data_id=DATA_ID,
            request_id=REQUEST_ID,
            versions=("v2", "v1"),
        )

    def test_the_first_supported_version_wins(self) -> None:
        """A header carrying both is verified against the caller's choice."""
        good = sign_manifest(
            secret=SECRET,
            data_id=DATA_ID,
            request_id=REQUEST_ID,
            timestamp=TIMESTAMP,
        )
        header = f"ts={TIMESTAMP},v1={good},v2=deadbeef"

        assert verify_signature(
            secret=SECRET,
            signature_header=header,
            data_id=DATA_ID,
            request_id=REQUEST_ID,
        )
        assert not verify_signature(
            secret=SECRET,
            signature_header=header,
            data_id=DATA_ID,
            request_id=REQUEST_ID,
            versions=("v2", "v1"),
        )

    def test_default_versions_are_pinned(self) -> None:
        """Accepting a version the provider has not shipped is not a default."""
        assert DEFAULT_SIGNATURE_VERSIONS == ("v1",)


class TestReplayWindow:
    """The tolerance check, which is what makes ``ts`` do any work."""

    def _signed(self, timestamp: str) -> str:
        """Build a valid header for ``timestamp``.

        Args:
            timestamp (str): The ``ts`` to sign and carry.

        Returns:
            str: The header value.
        """
        digest = sign_manifest(
            secret=SECRET,
            data_id=DATA_ID,
            request_id=REQUEST_ID,
            timestamp=timestamp,
        )
        return f"ts={timestamp},v1={digest}"

    def test_off_by_default(self) -> None:
        """A years-old delivery still verifies when no window is asked for.

        Not an oversight — upstream leaves it opt-in too. It is documented
        so that "the signature covers a timestamp" is not mistaken for
        "the timestamp is checked".
        """
        assert verify_signature(
            secret=SECRET,
            signature_header=self._signed("1000000000"),
            data_id=DATA_ID,
            request_id=REQUEST_ID,
        )

    def test_inside_the_window_passes(self) -> None:
        """Thirty seconds of drift, five minutes of tolerance."""
        assert verify_signature(
            secret=SECRET,
            signature_header=self._signed("1771891200"),
            data_id=DATA_ID,
            request_id=REQUEST_ID,
            tolerance_seconds=300.0,
            now=lambda: 1771891230.0,
        )

    def test_outside_the_window_fails(self) -> None:
        """Ten minutes of drift against a one-minute window."""
        assert not verify_signature(
            secret=SECRET,
            signature_header=self._signed("1771891200"),
            data_id=DATA_ID,
            request_id=REQUEST_ID,
            tolerance_seconds=60.0,
            now=lambda: 1771891800.0,
        )

    def test_a_millisecond_timestamp_is_read_as_milliseconds(self) -> None:
        """The provider's own artifacts disagree about the unit.

        Upstream's issue #458 was a seconds/milliseconds mix-up rejecting
        valid deliveries; their test suite then signs a 13-digit ``ts``. The
        unit is read off the magnitude so both shapes work — a 13-digit
        stamp compared as seconds would sit 53 000 years in the future and
        fail every window.
        """
        assert verify_signature(
            secret=SECRET,
            signature_header=self._signed("1742505638683"),
            data_id=DATA_ID,
            request_id=REQUEST_ID,
            tolerance_seconds=60.0,
            now=lambda: 1742505638.0,
        )


class TestEnvironmentAndEvents:
    """The two small hand-written pieces."""

    def test_base_url_is_the_single_server(self) -> None:
        """There is no sandbox host to switch to."""
        assert DEFAULT_BASE_URL == "https://api.mercadopago.com"

    def test_events_are_the_ones_the_spec_names(self) -> None:
        """Extracted from the vendored spec, not from the portal."""
        assert MercadoPagoEvent.PAYMENT == "payment"
        assert MercadoPagoEvent.MERCHANT_ORDER == "merchant_order"
        assert MercadoPagoEvent.POINT_INTEGRATION == "point_integration_wh"
        assert MercadoPagoEvent.UNKNOWN == "unknown"
