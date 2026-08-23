"""The hand-written half of the Mercado Pago integration.

Money and webhook verification — the two things the specification does not
give us, and therefore the two that need tests of their own.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    DEFAULT_BASE_URL,
    DEFAULT_MANIFEST_TEMPLATE,
    MercadoPagoEvent,
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
    """Parsing what arrives in ``x-signature``."""

    def test_parses_both_components(self) -> None:
        """The documented shape is ``ts=...,v1=...``."""
        assert parse_signature_header("ts=1771891200,v1=abc123") == (
            "1771891200",
            "abc123",
        )

    def test_tolerates_whitespace_and_order(self) -> None:
        """Header whitespace is not a reason to reject a real delivery."""
        assert parse_signature_header(" v1=abc123 , ts=1771891200 ") == (
            "1771891200",
            "abc123",
        )

    def test_missing_component_comes_back_empty(self) -> None:
        """A malformed header is an untrusted caller, not an exception."""
        assert parse_signature_header("ts=1771891200") == ("1771891200", "")
        assert parse_signature_header("garbage") == ("", "")


class TestVerifySignature:
    """What the HMAC does and does not prove.

    These exercise the cryptography and the parsing against a signature
    this package produced. They do **not** prove the manifest matches the
    one Mercado Pago signs — that needs one real delivery, and the module
    docstring says so.
    """

    def _header(self, digest: str) -> str:
        """Build a signature header carrying ``digest``.

        Args:
            digest (str): The hex digest.

        Returns:
            str: The header value.
        """
        return f"ts={TIMESTAMP},v1={digest}"

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

    def test_custom_manifest_template_is_honoured(self) -> None:
        """The template is overridable because it is the unmeasured part.

        A caller who measures a different manifest against a live delivery
        can use it immediately, without waiting for a release here.
        """
        template = "ts:{ts};id:{data_id};rid:{request_id};"
        digest = sign_manifest(
            secret=SECRET,
            data_id=DATA_ID,
            request_id=REQUEST_ID,
            timestamp=TIMESTAMP,
            manifest_template=template,
        )

        assert verify_signature(
            secret=SECRET,
            signature_header=self._header(digest),
            data_id=DATA_ID,
            request_id=REQUEST_ID,
            manifest_template=template,
        )
        assert not verify_signature(
            secret=SECRET,
            signature_header=self._header(digest),
            data_id=DATA_ID,
            request_id=REQUEST_ID,
        )

    def test_default_template_is_the_documented_one(self) -> None:
        """Pinned so a change to it is a deliberate, reviewed change."""
        assert DEFAULT_MANIFEST_TEMPLATE == (
            "id:{data_id};request-id:{request_id};ts:{ts};"
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
