"""The Pix QR the specification omits, and the drop it used to cause.

The generated ``Payment`` model has no ``point_of_interaction`` because the
vendored specification never declares one, and ``BaseSchema`` is
``extra="ignore"`` — so the QR the API really returns was discarded during
validation, with no error anywhere. Every test here pins one half of that:
the defect itself, and the typed path out of it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    DEFAULT_BASE_URL,
    PAYMENTS_PATH,
    PixPayment,
    PixPointOfInteraction,
    PixTransactionData,
    create_pix_payment,
    get_pix_payment,
    parse_pix_payment,
    to_cents,
)

QR_CODE: str = (
    "00020126580014br.gov.bcb.pix0136a629532e-7693-4846-b028-f142a1dd1b2b"
    "5204000053039865802BR5913Fulano de Tal6008BRASILIA62070503***63041D3D"
)
"""An EMV BR Code shaped like the one Mercado Pago returns."""

QR_BASE64: str = "iVBORw0KGgoAAAANSUhEUg=="
"""A base64 stub standing in for the PNG the provider encodes."""


def pix_response(**overrides: Any) -> dict[str, Any]:
    """Build a ``POST /v1/payments`` body for a pending Pix payment.

    Args:
        **overrides (Any): Top-level keys to replace in the body.

    Returns:
        dict[str, Any]: The response body, shaped after Mercado Pago's own
        Node SDK types (``PaymentResponse`` + ``PointOfInteraction``).
    """
    body: dict[str, Any] = {
        "id": 1316064835,
        "status": "pending",
        "status_detail": "pending_waiting_transfer",
        "payment_method_id": "pix",
        "payment_type_id": "bank_transfer",
        "currency_id": "BRL",
        "transaction_amount": 19.9,
        "date_of_expiration": "2026-08-24T12:00:00.000-03:00",
        "transaction_details": {
            "external_resource_url": "https://www.mercadopago.com.br/payments/1316064835/ticket",
        },
        "point_of_interaction": {
            "type": "CHECKOUT",
            "sub_type": None,
            "linked_to": None,
            "transaction_data": {
                "qr_code": QR_CODE,
                "qr_code_base64": QR_BASE64,
                "ticket_url": "https://www.mercadopago.com.br/sandbox/payments/1316064835/ticket",
                "transaction_id": "01HZ0X6M1S8T5Q",
                "bank_transfer_id": 987654321,
                "financial_institution": 323,
            },
        },
    }
    body.update(overrides)
    return body


def mock_client(handler: Any) -> HTTPClient:
    """Build an ``HTTPClient`` that answers from a scripted handler.

    Args:
        handler (Any): Callable taking an ``httpx.Request`` and returning
            an ``httpx.Response``.

    Returns:
        HTTPClient: A client wired to an ``httpx.MockTransport``, so the
        request built by the module under test is inspectable without
        network access.
    """
    return HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={"Authorization": "Bearer a-test-token"},
        transport=httpx.MockTransport(handler),
    )


class TestTheGeneratedModelStillHasNoFieldForTheQr:
    """The defect this module exists for, and how far it now goes.

    The specification does not declare ``point_of_interaction``, so the
    generated model has no typed place for the QR — that half has not
    changed and is why :func:`parse_pix_payment` exists.

    What changed is the consequence. Until v0.259.0 the object was
    *discarded*: no exception, no warning, and a
    ``transaction_details.external_resource_url`` that looked like an
    answer while the copy-and-paste string was already gone. Response
    models now carry what the specification did not predict, so the QR
    survives validation even without a field of its own.
    """

    def test_the_generated_payment_still_has_no_field_for_it(self) -> None:
        """The specification is what is missing, and it still is."""
        from tempest_fastapi_sdk.integrations.payment.mercado_pago import Payment

        assert "point_of_interaction" not in Payment.model_fields

    def test_a_response_model_keeps_what_the_spec_omitted(self) -> None:
        """``extra="allow"`` is the difference between absent and lost."""
        from tempest_fastapi_sdk.integrations.payment.mercado_pago import Payment

        assert Payment.model_config["extra"] == "allow"

        body = pix_response()
        assert body["point_of_interaction"]["transaction_data"]["qr_code"] == QR_CODE

        payment = Payment.model_validate(body)
        extra = payment.model_extra or {}
        assert extra["point_of_interaction"]["transaction_data"]["qr_code"] == QR_CODE
        assert QR_CODE in payment.model_dump_json()

    def test_the_pix_view_is_still_how_you_read_it_typed(self) -> None:
        """Surviving untyped is not the same as being reachable typed."""
        payment = parse_pix_payment(pix_response())
        assert payment.qr_code == QR_CODE
        assert payment.qr_code_base64 == QR_BASE64


class TestPixSchemas:
    """The field set is ported, so upstream drift has to fail a test.

    Ported from ``mercadopago/sdk-nodejs``,
    ``src/clients/payment/commonTypes.ts`` at commit ``c2d3c6ae``
    (2026-07-27). A field added or renamed there shows up here as a failing
    assertion rather than as a value quietly missing from a payment.
    """

    def test_transaction_data_fields(self) -> None:
        """Exactly the Pix-relevant half of the upstream ``TransactionData``."""
        assert set(PixTransactionData.model_fields) == {
            "qr_code",
            "qr_code_base64",
            "ticket_url",
            "transaction_id",
            "bank_transfer_id",
            "financial_institution",
        }

    def test_point_of_interaction_fields(self) -> None:
        """The upstream ``PointOfInteraction``, minus the card-only halves."""
        assert set(PixPointOfInteraction.model_fields) == {
            "type",
            "sub_type",
            "linked_to",
            "transaction_data",
        }

    def test_payment_view_fields(self) -> None:
        """A view, not a copy: only what a Pix flow reads, plus the QR."""
        assert set(PixPayment.model_fields) == {
            "id",
            "status",
            "status_detail",
            "payment_method_id",
            "transaction_amount",
            "date_of_expiration",
            "point_of_interaction",
        }

    def test_financial_institution_takes_a_number_or_a_string(self) -> None:
        """The two sources disagree on the type, so both are accepted.

        The Node SDK types it ``number``; the vendored specification types
        the same concept ``string`` on the Orders API. Raising over it would
        lose the whole payment for a field nobody reconciles on.
        """
        assert (
            PixTransactionData(financial_institution=323).financial_institution == 323
        )
        assert (
            PixTransactionData(financial_institution="323").financial_institution
            == "323"
        )

    def test_unknown_fields_still_do_not_raise(self) -> None:
        """A provider that adds a field must not break an existing caller."""
        data = PixTransactionData.model_validate(
            {"qr_code": QR_CODE, "something_new": "value"}
        )
        assert data.qr_code == QR_CODE

    def test_amount_stays_in_reais(self) -> None:
        """The unit is the provider's, and the conversion is explicit."""
        payment = parse_pix_payment(pix_response())
        assert payment.transaction_amount == 19.9
        assert to_cents(payment.transaction_amount or 0.0) == 1990


class TestNoneSafety:
    """Reading the QR off a payment that has none returns None, never raises."""

    def test_no_point_of_interaction_at_all(self) -> None:
        """A card payment has no interaction object; the properties hold."""
        body = pix_response()
        del body["point_of_interaction"]
        payment = parse_pix_payment(body)
        assert payment.point_of_interaction is None
        assert payment.qr_code is None
        assert payment.qr_code_base64 is None
        assert payment.ticket_url is None

    def test_interaction_without_transaction_data(self) -> None:
        """A paid Pix comes back with the object and no QR inside it."""
        payment = parse_pix_payment(
            pix_response(point_of_interaction={"type": "CHECKOUT"})
        )
        assert payment.point_of_interaction is not None
        assert payment.point_of_interaction.transaction_data is None
        assert payment.qr_code is None

    def test_an_empty_body_validates(self) -> None:
        """Every field is optional, because the provider omits what is unset."""
        payment = PixPayment.model_validate({})
        assert payment.id is None
        assert payment.qr_code is None


class TestCreatePixPayment:
    """The request is the generated one; only the response model differs."""

    @pytest.mark.asyncio
    async def test_posts_the_body_and_returns_the_qr(self) -> None:
        """The call reaches ``POST /v1/payments`` and the QR comes back."""
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            """Record the request and answer with a pending Pix payment.

            Args:
                request (httpx.Request): The outbound request.

            Returns:
                httpx.Response: A 201 carrying the QR.
            """
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.read())
            seen["authorization"] = request.headers.get("authorization")
            seen["idempotency"] = request.headers.get("x-idempotency-key")
            return httpx.Response(201, json=pix_response())

        payment = await create_pix_payment(
            mock_client(handler),
            body={
                "transaction_amount": 19.9,
                "payment_method_id": "pix",
                "payer": {"email": "payer@example.com"},
            },
        )

        assert seen["method"] == "POST"
        assert seen["path"] == PAYMENTS_PATH
        assert seen["authorization"] == "Bearer a-test-token"
        assert seen["body"]["payment_method_id"] == "pix"
        assert seen["idempotency"] is None
        assert payment.qr_code == QR_CODE
        assert payment.status == "pending"

    @pytest.mark.asyncio
    async def test_idempotency_key_becomes_the_header(self) -> None:
        """Retrying a payment POST without one can charge twice."""
        from uuid import UUID

        key = UUID("2f1a0d3c-5b6e-4c7a-9d8f-0e1b2c3d4e5f")
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            """Record the idempotency header and answer.

            Args:
                request (httpx.Request): The outbound request.

            Returns:
                httpx.Response: A 201 carrying the QR.
            """
            seen["idempotency"] = request.headers.get("x-idempotency-key")
            return httpx.Response(201, json=pix_response())

        await create_pix_payment(mock_client(handler), body={}, idempotency_key=key)
        assert seen["idempotency"] == str(key)

    @pytest.mark.asyncio
    async def test_a_generated_request_model_is_serialized_for_the_wire(self) -> None:
        """A ``PaymentRequest`` goes out as JSON, without its None fields."""
        from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
            PaymentPayer,
            PaymentRequest,
        )

        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            """Record the serialized body and answer.

            Args:
                request (httpx.Request): The outbound request.

            Returns:
                httpx.Response: A 201 carrying the QR.
            """
            seen["body"] = json.loads(request.read())
            return httpx.Response(201, json=pix_response())

        await create_pix_payment(
            mock_client(handler),
            body=PaymentRequest(
                transaction_amount=19.9,
                payment_method_id="pix",
                payer=PaymentPayer(email="payer@example.com"),
            ),
        )

        assert seen["body"]["transaction_amount"] == 19.9
        assert seen["body"]["payment_method_id"] == "pix"
        assert seen["body"]["payer"] == {"email": "payer@example.com"}
        assert "installments" not in seen["body"]
        assert None not in seen["body"].values()

    @pytest.mark.asyncio
    async def test_a_rejected_call_raises_like_the_generated_client(self) -> None:
        """Non-2xx is an error, not a payment with empty fields."""

        def handler(request: httpx.Request) -> httpx.Response:
            """Answer with the provider's validation error.

            Args:
                request (httpx.Request): The outbound request.

            Returns:
                httpx.Response: A 400 with an error body.
            """
            return httpx.Response(400, json={"message": "invalid_payment_method"})

        with pytest.raises(httpx.HTTPStatusError):
            await create_pix_payment(mock_client(handler), body={})


class TestGetPixPayment:
    """Re-reading a payment is how you learn it was paid, or expired."""

    @pytest.mark.asyncio
    async def test_gets_the_payment_by_id(self) -> None:
        """The path carries the identifier and the QR survives the round trip."""
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            """Record the path and answer with the payment.

            Args:
                request (httpx.Request): The outbound request.

            Returns:
                httpx.Response: A 200 carrying the QR.
            """
            seen["method"] = request.method
            seen["path"] = request.url.path
            return httpx.Response(200, json=pix_response())

        payment = await get_pix_payment(mock_client(handler), 1316064835)

        assert seen["method"] == "GET"
        assert seen["path"] == f"{PAYMENTS_PATH}/1316064835"
        assert payment.id == 1316064835
        assert payment.ticket_url is not None

    @pytest.mark.asyncio
    async def test_an_approved_payment_has_no_qr_left(self) -> None:
        """After payment the provider stops sending the transaction data."""

        def handler(request: httpx.Request) -> httpx.Response:
            """Answer with an approved payment.

            Args:
                request (httpx.Request): The outbound request.

            Returns:
                httpx.Response: A 200 for a settled Pix.
            """
            return httpx.Response(
                200,
                json=pix_response(
                    status="approved",
                    status_detail="accredited",
                    point_of_interaction={"type": "CHECKOUT"},
                ),
            )

        payment = await get_pix_payment(mock_client(handler), "1316064835")
        assert payment.status == "approved"
        assert payment.qr_code is None
