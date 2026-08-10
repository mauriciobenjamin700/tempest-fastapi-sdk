"""The OpenPix layer over a generated integration.

Every fact pinned here was read from the provider's specification or its
webhook-signature page, never recalled: the 28 event names, the two hosts,
the signature header, and the shape of the published public key. A typo in
any of them fails silently in production — an event stops matching, or a
real delivery is rejected as unsigned.
"""

from __future__ import annotations

import base64
import json
from decimal import Decimal
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.integrations.payment.openpix import (
    OPENPIX_WEBHOOK_PUBLIC_KEY,
    OPENPIX_WEBHOOK_SIGNATURE_HEADER,
    OpenPixEnvironment,
    OpenPixEvent,
    OpenPixWebhookEvent,
    cents_to_reais,
    decode_public_key,
    make_openpix_webhook_dependency,
    reais_to_cents,
    to_cents,
    webhook_verifier,
)


@pytest.fixture(scope="module")
def key_pair() -> tuple[rsa.RSAPrivateKey, str]:
    """Generate a signing key pair for the webhook tests.

    Returns:
        tuple[rsa.RSAPrivateKey, str]: The private key and its PEM public
        half. A generated pair is used rather than OpenPix's real key
        because signing needs the private half, which only they hold.
    """
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private, public_pem


def _sign(private: rsa.RSAPrivateKey, body: bytes) -> str:
    """Sign a body the way OpenPix documents.

    Args:
        private (rsa.RSAPrivateKey): The signing key.
        body (bytes): The exact bytes to sign.

    Returns:
        str: The base64 signature, as the header carries it.
    """
    return base64.b64encode(
        private.sign(body, padding.PKCS1v15(), hashes.SHA256())
    ).decode()


def _client(
    private: rsa.RSAPrivateKey, public_pem: str
) -> tuple[httpx.AsyncClient, FastAPI]:
    """Build an app whose one route is guarded by the dependency.

    Args:
        private (rsa.RSAPrivateKey): Unused here, kept for symmetry with
            the signing helper the caller pairs this with.
        public_pem (str): The public key the dependency verifies against.

    Returns:
        tuple[httpx.AsyncClient, FastAPI]: A transport-bound client and
        the app it drives.
    """
    app = FastAPI()
    verify = make_openpix_webhook_dependency(
        verifier=webhook_verifier(public_key_pem=public_pem)
    )

    @app.post("/hook")
    async def hook(event: OpenPixWebhookEvent = Depends(verify)) -> dict[str, Any]:
        """Echo what the dependency parsed."""
        return {
            "event_name": event.event_name,
            "event": event.event.value if event.event else None,
            "keys": sorted(event.payload),
        }

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://t"), app


class TestPublishedKey:
    """The bundled key is the one the provider publishes."""

    def test_is_a_loadable_rsa_public_key(self) -> None:
        """A truncated paste would only fail on a real delivery."""
        key = serialization.load_pem_public_key(
            OPENPIX_WEBHOOK_PUBLIC_KEY.encode("utf-8")
        )
        assert isinstance(key, rsa.RSAPublicKey)

    def test_is_the_documented_1024_bit_key(self) -> None:
        """Pinned so a silent swap shows up as a failure.

        1024 bits is below the modern floor, which is why the docstring
        tells the reader to re-read the charge from the API rather than
        treat a valid signature as authorization.
        """
        key = serialization.load_pem_public_key(
            OPENPIX_WEBHOOK_PUBLIC_KEY.encode("utf-8")
        )
        assert isinstance(key, rsa.RSAPublicKey)
        assert key.key_size == 1024
        assert key.public_numbers().e == 65537

    def test_decode_public_key_round_trips(self) -> None:
        """The provider prints the key base64-encoded, not as PEM."""
        encoded = base64.b64encode(OPENPIX_WEBHOOK_PUBLIC_KEY.encode()).decode()
        assert decode_public_key(encoded) == OPENPIX_WEBHOOK_PUBLIC_KEY

    @pytest.mark.parametrize("bad", ["not base64!!", "aGVsbG8="])
    def test_decode_public_key_rejects_junk(self, bad: str) -> None:
        """A bad paste fails here, not as a mismatch months later."""
        with pytest.raises(ValueError):
            decode_public_key(bad)


class TestEnvironment:
    """The two hosts are different domains, not variants of one."""

    def test_hosts_match_the_specification_servers(self) -> None:
        """Read from the spec's ``servers`` block."""
        assert OpenPixEnvironment.PRODUCTION.base_url == "https://api.openpix.com.br"
        assert OpenPixEnvironment.SANDBOX.base_url == "https://api.woovi-sandbox.com"

    def test_every_member_resolves(self) -> None:
        """A member added without a URL would raise only in production."""
        for member in OpenPixEnvironment:
            assert member.base_url.startswith("https://")


class TestEvents:
    """The event names are the provider's, verbatim."""

    def test_count_matches_the_specification(self) -> None:
        """``WebhookEventEnum`` declares 28 values."""
        assert len(OpenPixEvent.values()) == 28

    def test_namespacing_is_not_uniform(self) -> None:
        """Charge events are prefixed, Pix-automatic ones are not.

        Pinned because it reads like a transcription slip and would
        "helpfully" get normalized by the next person through here.
        """
        assert OpenPixEvent.CHARGE_COMPLETED.value == "OPENPIX:CHARGE_COMPLETED"
        assert OpenPixEvent.PIX_AUTOMATIC_APPROVED.value == "PIX_AUTOMATIC_APPROVED"

    def test_member_names_drop_the_namespace(self) -> None:
        """``OpenPixEvent.CHARGE_COMPLETED`` reads better than the wire name."""
        assert OpenPixEvent.CHARGE_COMPLETED.name == "CHARGE_COMPLETED"


class TestMoney:
    """``value`` is cents typed as a JSON number."""

    def test_narrows_the_float_a_generated_model_produces(self) -> None:
        """The spec says cents and types it ``number``, so it arrives 1990.0."""
        assert to_cents(1990.0) == 1990
        assert isinstance(to_cents(1990.0), int)

    def test_refuses_a_fractional_value(self) -> None:
        """A fraction means the caller passed reais, and rounding would hide it."""
        with pytest.raises(ValueError, match="whole number of cents"):
            to_cents(19.9)

    def test_refuses_a_negative_value(self) -> None:
        """Negative money in a charge is a bug upstream, not a discount."""
        with pytest.raises(ValueError, match="negative"):
            to_cents(-1)

    def test_reais_rounds_half_up_not_half_to_even(self) -> None:
        """``round(0.005 * 100)`` is 0 in Python; money expects 1."""
        assert reais_to_cents("0.005") == 1
        assert reais_to_cents(19.90) == 1990

    def test_cents_to_reais_stays_exact(self) -> None:
        """A Decimal so the value is still exact at the formatting call."""
        assert cents_to_reais(1990) == Decimal("19.90")
        assert str(cents_to_reais(5)) == "0.05"


class TestWebhookDependency:
    """Verification, then parsing — and neither one 500s on surprises."""

    @pytest.mark.asyncio
    async def test_valid_signature_yields_a_parsed_event(
        self, key_pair: tuple[rsa.RSAPrivateKey, str]
    ) -> None:
        """The happy path: verified, decoded, event resolved."""
        private, public_pem = key_pair
        body = json.dumps(
            {"event": "OPENPIX:CHARGE_COMPLETED", "charge": {"value": 1990}}
        ).encode()
        client, _ = _client(private, public_pem)
        async with client:
            response = await client.post(
                "/hook",
                content=body,
                headers={OPENPIX_WEBHOOK_SIGNATURE_HEADER: _sign(private, body)},
            )
        assert response.status_code == 200
        assert response.json() == {
            "event_name": "OPENPIX:CHARGE_COMPLETED",
            "event": "OPENPIX:CHARGE_COMPLETED",
            "keys": ["charge", "event"],
        }

    @pytest.mark.asyncio
    async def test_bad_signature_is_rejected(
        self, key_pair: tuple[rsa.RSAPrivateKey, str]
    ) -> None:
        """An unsigned or wrongly-signed delivery never reaches the route."""
        private, public_pem = key_pair
        body = b'{"event": "OPENPIX:CHARGE_COMPLETED"}'
        client, _ = _client(private, public_pem)
        async with client:
            tampered = await client.post(
                "/hook",
                content=body,
                headers={OPENPIX_WEBHOOK_SIGNATURE_HEADER: _sign(private, b"other")},
            )
            missing = await client.post("/hook", content=body)
        assert tampered.status_code == 401
        assert missing.status_code == 401

    @pytest.mark.asyncio
    async def test_unknown_event_does_not_fail_the_request(
        self, key_pair: tuple[rsa.RSAPrivateKey, str]
    ) -> None:
        """OpenPix adds events; a 500 turns their release into our outage."""
        private, public_pem = key_pair
        body = json.dumps({"event": "OPENPIX:INVENTED_TOMORROW"}).encode()
        client, _ = _client(private, public_pem)
        async with client:
            response = await client.post(
                "/hook",
                content=body,
                headers={OPENPIX_WEBHOOK_SIGNATURE_HEADER: _sign(private, body)},
            )
        assert response.status_code == 200
        assert response.json()["event_name"] == "OPENPIX:INVENTED_TOMORROW"
        assert response.json()["event"] is None

    @pytest.mark.asyncio
    async def test_non_json_body_that_verified_is_still_delivered(
        self, key_pair: tuple[rsa.RSAPrivateKey, str]
    ) -> None:
        """It verified, so it came from OpenPix — dropping it loses a delivery."""
        private, public_pem = key_pair
        body = b"not json at all"
        client, _ = _client(private, public_pem)
        async with client:
            response = await client.post(
                "/hook",
                content=body,
                headers={OPENPIX_WEBHOOK_SIGNATURE_HEADER: _sign(private, body)},
            )
        assert response.status_code == 200
        assert response.json() == {"event_name": "", "event": None, "keys": []}

    def test_event_stays_an_enum_not_a_string(self) -> None:
        """``event is OpenPixEvent.X`` must work — the documented idiom.

        The first version made this a ``BaseSchema``, whose
        ``use_enum_values=True`` stores the bare value. The identity check
        in the module's own example then returned ``False`` on every
        delivery, with nothing raised anywhere.
        """
        parsed = OpenPixWebhookEvent(
            event_name="OPENPIX:CHARGE_COMPLETED",
            event=OpenPixEvent.CHARGE_COMPLETED,
        )
        assert parsed.event is OpenPixEvent.CHARGE_COMPLETED
        assert isinstance(parsed.event, OpenPixEvent)

    def test_default_verifier_is_configured_for_openpix(self) -> None:
        """Header and algorithm come from the provider's own page."""
        verifier = webhook_verifier()
        assert verifier.header_name == "x-webhook-signature"
        assert verifier.algorithm == "sha256"
        assert verifier.public_key_pem == OPENPIX_WEBHOOK_PUBLIC_KEY.encode("utf-8")
