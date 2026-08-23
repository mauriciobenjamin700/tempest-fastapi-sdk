"""The OpenPix adapter, exercised against fixed provider answers.

No network and no mock of our own code: the stub below answers with the
**generated** models, so the mapping is tested against the same shapes the
real client produces.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatus,
    PixChargeRequest,
    PixEventType,
    PixPayer,
)
from tempest_fastapi_sdk.integrations.payment.adapters.openpix import (
    OpenPixPixProvider,
)
from tempest_fastapi_sdk.integrations.payment.openpix import (
    Charge,
    ChargePayload,
    ChargeStatus,
    DeleteApiV1ChargeByIdResponse,
    GetApiV1ChargeByIdResponse,
    OpenPixEvent,
    OpenPixWebhookEvent,
    PostApiV1ChargeResponse,
)


class StubOpenPixClient:
    """An OpenPix client that answers with fixed generated models.

    Attributes:
        created_with (ChargePayload | None): The payload the adapter built,
            kept so a test can assert on what would go over the wire.
        charge (Charge | None): What the read and create routes answer.
        top_level_br_code (str | None): The ``brCode`` OpenPix puts beside
            the charge on create.
        deleted (DeleteApiV1ChargeByIdResponse): What the cancel route
            answers.
    """

    def __init__(
        self,
        *,
        charge: Charge | None = None,
        top_level_br_code: str | None = None,
        deleted: DeleteApiV1ChargeByIdResponse | None = None,
    ) -> None:
        """Store the canned answers.

        Args:
            charge (Charge | None): The charge to answer with.
            top_level_br_code (str | None): The create route's own
                ``brCode`` field.
            deleted (DeleteApiV1ChargeByIdResponse | None): The cancel
                answer.
        """
        self.created_with: ChargePayload | None = None
        self.charge: Charge | None = charge
        self.top_level_br_code: str | None = top_level_br_code
        self.deleted: DeleteApiV1ChargeByIdResponse = (
            deleted or DeleteApiV1ChargeByIdResponse(status="OK", id="ch_1")
        )

    async def post_api_v1_charge(
        self, *, body: ChargePayload, return_existing: bool | None = None
    ) -> PostApiV1ChargeResponse:
        """Record the payload and answer with the canned charge."""
        self.created_with = body
        return PostApiV1ChargeResponse(
            charge=self.charge,
            correlation_id=body.correlation_id,
            br_code=self.top_level_br_code,
        )

    async def get_api_v1_charge_by_id(self, id: str) -> GetApiV1ChargeByIdResponse:
        """Answer the read route with the canned charge."""
        return GetApiV1ChargeByIdResponse(charge=self.charge)

    async def delete_api_v1_charge_by_id(
        self, id: str
    ) -> DeleteApiV1ChargeByIdResponse:
        """Answer the cancel route."""
        return self.deleted


def make_charge(**overrides: Any) -> Charge:
    """Build a generated ``Charge`` with sane defaults.

    Args:
        **overrides (Any): Fields to override.

    Returns:
        Charge: The charge.
    """
    fields: dict[str, Any] = {
        "value": 1990.0,
        "status": ChargeStatus.ACTIVE,
        "correlation_id": "order-1042",
        "global_id": "ch_global_1",
        "br_code": "00020126...5802BR",
        "qr_code_image": "https://api.openpix.com.br/openpix/charge/brcode/image/x.png",
        "expires_date": "2026-09-01T17:28:51.882Z",
    }
    fields.update(overrides)
    return Charge(**fields)


@pytest.mark.asyncio
async def test_create_sends_cents_seconds_and_our_reference() -> None:
    """The request is translated into OpenPix's own vocabulary.

    Three translations at once: ``amount_cents`` goes across unchanged
    because OpenPix already states cents, ``expires_in`` becomes seconds,
    and ``reference`` becomes ``correlationID``.
    """
    client = StubOpenPixClient(charge=make_charge())
    provider = OpenPixPixProvider(client)

    await provider.create_pix_charge(
        PixChargeRequest(
            amount_cents=1990,
            reference="order-1042",
            description="Pedido 1042",
            expires_in=timedelta(hours=1),
        )
    )

    assert client.created_with is not None
    assert client.created_with.value == 1990
    assert client.created_with.correlation_id == "order-1042"
    assert client.created_with.comment == "Pedido 1042"
    assert client.created_with.expires_in == 3600


@pytest.mark.asyncio
async def test_create_maps_the_charge_into_the_contract() -> None:
    """The answer comes back in canonical shape, cents exact."""
    provider = OpenPixPixProvider(StubOpenPixClient(charge=make_charge()))

    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=1990, reference="order-1042")
    )

    assert charge.provider == "openpix"
    assert charge.provider_charge_id == "ch_global_1"
    assert charge.reference == "order-1042"
    assert charge.amount_cents == 1990
    assert isinstance(charge.amount_cents, int)
    assert charge.status is PaymentStatus.PENDING
    assert charge.provider_status == "ACTIVE"
    assert charge.br_code == "00020126...5802BR"
    assert charge.qr_code_image_url is not None
    assert charge.qr_code_base64 is None
    assert charge.expires_at == datetime(2026, 9, 1, 17, 28, 51, 882000, tzinfo=UTC)


@pytest.mark.asyncio
async def test_create_falls_back_to_the_top_level_brcode() -> None:
    """OpenPix repeats ``brCode`` beside the charge; either place works."""
    client = StubOpenPixClient(
        charge=make_charge(br_code=None), top_level_br_code="00020126...FALLBACK"
    )
    provider = OpenPixPixProvider(client)

    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=500, reference="order-2")
    )

    assert charge.br_code == "00020126...FALLBACK"


@pytest.mark.asyncio
async def test_create_without_a_charge_body_raises() -> None:
    """A 2xx with no charge is refused where it happens, not later."""
    provider = OpenPixPixProvider(StubOpenPixClient(charge=None))

    with pytest.raises(ValueError, match="no charge body"):
        await provider.create_pix_charge(
            PixChargeRequest(amount_cents=500, reference="order-3")
        )


@pytest.mark.asyncio
async def test_payer_without_a_name_is_not_sent() -> None:
    """OpenPix requires ``name`` inside the customer block.

    A payer known only by tax ID cannot be sent, and inventing a name would
    put made-up data on the payer's receipt.
    """
    client = StubOpenPixClient(charge=make_charge())
    provider = OpenPixPixProvider(client)

    await provider.create_pix_charge(
        PixChargeRequest(
            amount_cents=100,
            reference="order-4",
            payer=PixPayer(tax_id="12345678909"),
        )
    )

    assert client.created_with is not None
    assert client.created_with.customer is None


@pytest.mark.asyncio
async def test_payer_with_a_name_is_forwarded() -> None:
    """A full payer reaches OpenPix's customer block."""
    client = StubOpenPixClient(charge=make_charge())
    provider = OpenPixPixProvider(client)

    await provider.create_pix_charge(
        PixChargeRequest(
            amount_cents=100,
            reference="order-5",
            payer=PixPayer(
                name="Maria Souza", tax_id="12345678909", email="m@example.com"
            ),
        )
    )

    customer = client.created_with.customer if client.created_with else None
    assert customer is not None
    assert customer.name == "Maria Souza"
    assert customer.tax_id == "12345678909"


@pytest.mark.asyncio
async def test_completed_charge_maps_to_paid() -> None:
    """The state a service branches on comes from the canonical enum."""
    provider = OpenPixPixProvider(
        StubOpenPixClient(charge=make_charge(status=ChargeStatus.COMPLETED))
    )

    charge = await provider.get_pix_charge("ch_global_1")

    assert charge.status is PaymentStatus.PAID
    assert charge.provider_status == "COMPLETED"


@pytest.mark.asyncio
async def test_cancel_reports_what_openpix_actually_returns() -> None:
    """Cancellation answers with two fields, and the adapter says so.

    OpenPix's delete route returns ``{"status", "id"}`` — no amount, no
    code. The contract fills what it has and leaves the rest empty rather
    than issuing a refetch the caller did not ask for.
    """
    provider = OpenPixPixProvider(
        StubOpenPixClient(
            deleted=DeleteApiV1ChargeByIdResponse(status="OK", id="ch_global_1")
        )
    )

    charge = await provider.cancel_pix_charge("ch_global_1")

    assert charge.status is PaymentStatus.CANCELLED
    assert charge.provider_charge_id == "ch_global_1"
    assert charge.provider_status == "OK"
    assert charge.br_code is None
    assert charge.raw == {"status": "OK", "id": "ch_global_1"}


def test_webhook_completion_becomes_a_paid_event() -> None:
    """A verified completion delivery maps to ``CHARGE_PAID``."""
    provider = OpenPixPixProvider(StubOpenPixClient())
    event = OpenPixWebhookEvent(
        event_name="OPENPIX:CHARGE_COMPLETED",
        event=OpenPixEvent.CHARGE_COMPLETED,
        payload={
            "charge": {
                "status": "COMPLETED",
                "value": 1990,
                "correlationID": "order-1042",
                "globalID": "ch_global_1",
                "brCode": "00020126...5802BR",
                "paidAt": "2026-08-22T16:44:18.000Z",
            }
        },
    )

    parsed = provider.parse_webhook(event)

    assert parsed.type is PixEventType.CHARGE_PAID
    assert parsed.charge is not None
    assert parsed.charge.status is PaymentStatus.PAID
    assert parsed.charge.amount_cents == 1990
    assert parsed.charge.paid_at == datetime(2026, 8, 22, 16, 44, 18, tzinfo=UTC)


def test_webhook_payload_keeps_fields_the_charge_schema_drops() -> None:
    """``paidAt`` reaches the contract only through the webhook path.

    It appears in OpenPix's webhook examples but not in the ``Charge``
    schema, so the generated model would drop it — ``BaseSchema`` is
    ``extra="ignore"``. Reading the delivery as a dictionary is what keeps
    it, and ``raw`` keeps everything else.
    """
    provider = OpenPixPixProvider(StubOpenPixClient())
    event = OpenPixWebhookEvent(
        event_name="OPENPIX:CHARGE_COMPLETED",
        event=OpenPixEvent.CHARGE_COMPLETED,
        payload={
            "charge": {
                "status": "COMPLETED",
                "value": 100,
                "correlationID": "order-9",
                "undocumentedField": "kept",
            }
        },
    )

    parsed = provider.parse_webhook(event)

    assert parsed.charge is not None
    assert parsed.charge.raw["undocumentedField"] == "kept"


def test_unmapped_event_keeps_its_name() -> None:
    """An event outside the charge lifecycle is visible, not swallowed."""
    provider = OpenPixPixProvider(StubOpenPixClient())
    event = OpenPixWebhookEvent(
        event_name="OPENPIX:DISPUTE_CREATED",
        event=OpenPixEvent.DISPUTE_CREATED,
        payload={},
    )

    parsed = provider.parse_webhook(event)

    assert parsed.type is PixEventType.UNKNOWN
    assert parsed.provider_event_name == "OPENPIX:DISPUTE_CREATED"


def test_unverified_payload_is_refused() -> None:
    """A bare dict never reaches the mapping.

    Signature verification is the only thing standing between the endpoint
    and forged settlement notices, so the adapter refuses anything that did
    not come out of the verifier.
    """
    provider = OpenPixPixProvider(StubOpenPixClient())

    with pytest.raises(TypeError, match="OpenPixWebhookEvent"):
        provider.parse_webhook({"event": "OPENPIX:CHARGE_COMPLETED"})
