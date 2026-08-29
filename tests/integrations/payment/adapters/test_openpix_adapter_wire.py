"""``OpenPixPixProvider`` driven over the wire, on bodies OpenPix really sent.

Every other adapter test in this package answers through
``StubOpenPixClient``, which returns ``CreateChargeResponse(...)`` and
``GetChargeResponse(...)`` **built in Python**. The client's
``_validate(GetChargeResponse, response.json())`` therefore never runs, and
the entire JSON-to-model layer sits outside the suite: the aliases
(``brCode``, ``globalID``, ``correlationID``), the status enum, the type of
``value``, the type of ``expiresIn``. Twelve green tests — the count
``pytest --collect-only`` reports for that module today, where issue #245
says thirteen — are compatible with a charge that cannot be read at all,
which is exactly what shipped in issues #236 and #238.

This module closes that boundary. The transport is an
``httpx.MockTransport``, so every assertion is on bytes: bytes the adapter
put on the wire, or bytes a provider put on it.

None of the payloads are invented:

- ``CAPTURED_CHARGE`` is the body read off ``api.woovi-sandbox.com`` on
  2026-08-29 while reporting #238, pinned verbatim in
  ``tests/integrations/payment/openpix/test_real_payload_shapes.py`` and
  reproduced in ``vendor/openpix-evidence.md`` section 7.
- ``COMPLETED_DELIVERY`` is the ``OPENPIX:CHARGE_COMPLETED`` request-body
  example of ``vendor/openpix-openapi.json``. The document's *response*
  examples are not trustworthy — the one on ``GET /api/v1/charge`` is
  malformed — but the webhook examples are the source
  ``vendor/openpix-evidence.md`` section 7 already reads ``paidAt`` and
  ``expiresIn: integer`` from, and they are the only published bodies for a
  path that has no sandbox capture.

Two tests go further and mount a route, because a status code is a fact
about a service and not about a function: the webhook half signs the raw
bytes and lets ``make_openpix_webhook_dependency`` verify and decode them
before ``parse_webhook`` ever sees an object, and the read half puts
``get_pix_charge`` behind ``register_exception_handlers`` so the answer a
consumer receives is the thing asserted. The stub suite builds the
``OpenPixWebhookEvent`` by hand and never runs either.

Every test here was first written ``xfail(strict=True)``, naming the issue
that owned the fix: the evidence that the defect was reachable through the
class the recipe tells a consumer to use. Issues #239 through #244 closed
in v0.270.0 and the markers came off with them, which is what
``strict=True`` was for — an unexpected pass is a failure, so none of them
could be quietly left behind.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import Depends, FastAPI

from tempest_fastapi_sdk import HTTPClient, register_exception_handlers
from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatus,
    PixCharge,
    PixChargeRequest,
    PixEventType,
    PixPayer,
    PixPaymentEvent,
)
from tempest_fastapi_sdk.integrations.payment.adapters.openpix import (
    OpenPixPixProvider,
)
from tempest_fastapi_sdk.integrations.payment.openpix import (
    OpenPixClient,
    OpenPixEnvironment,
    OpenPixEvent,
    OpenPixWebhookEvent,
    make_openpix_webhook_dependency,
    webhook_verifier,
)

BASE_URL: str = OpenPixEnvironment.SANDBOX.base_url
"""The host every request in this module is addressed to."""

CHARGE_PATH: str = "/api/v1/charge"
"""``POST``/``GET``/``DELETE`` route the adapter uses, from the document."""

APP_ID: str = "Q2xpZW50X0lkX3Rlc3Q6Q2xpZW50X1NlY3JldF90ZXN0"
"""A stand-in AppID, shaped like the base64 blob OpenPix issues."""

CAPTURED_CHARGE: dict[str, Any] = {
    "value": 1190,
    "identifier": "5400e12faa5b4dd2a1b7f7f0e0a3a0c1",
    "correlationID": "f2dc576d-a6db-4677-9cb7-4de54964cc87",
    "status": "ACTIVE",
    "expiresIn": 3600,
    "fee": 50,
    "expiresDate": "2026-08-29T15:17:16.060Z",
    "brCode": "00020101021226980014br.gov.bcb.pix",
    "comment": "Purchase of 100 coins",
}
"""One charge as the sandbox returned it with HTTP 200, on 2026-08-29.

Kept byte-identical to the copy in
``tests/integrations/payment/openpix/test_real_payload_shapes.py``. That
module proves the **models** accept it; this one proves the **adapter**
does, which is a different claim — the models are reached through
``_validate``, and nothing before today's suite ran that call on the
adapter's behalf.
"""

COMPLETED_DELIVERY: dict[str, Any] = {
    "event": "OPENPIX:CHARGE_COMPLETED",
    "charge": {
        "value": 1,
        "comment": "",
        "identifier": "d983a07836cf48ed9a65764d3c184273",
        "transactionID": "d983a07836cf48ed9a65764d3c184273",
        "status": "COMPLETED",
        "additionalInfo": [],
        "fee": 85,
        "discount": 0,
        "valueWithDiscount": 1,
        "expiresDate": "2025-09-25T15:08:12.278Z",
        "type": "DYNAMIC",
        "correlationID": "3f2a2690-8224-4aae-a1ba-ed26d4d61f81",
        "paymentLinkID": "788c8d0d-182b-468e-942e-546be6a621c2",
        "createdAt": "2025-09-24T15:07:47.334Z",
        "updatedAt": "2025-09-24T15:08:13.578Z",
        "customer": {
            "name": "Cliente Teste",
            "taxID": {"taxID": "44720743000101", "type": "BR:CNPJ"},
            "correlationID": "ecd41c3b-487c-4719-b9f7-53b6dd6759cb",
        },
        "paidAt": "2025-09-24T15:07:50.891Z",
        "payer": None,
        "ensureSameTaxID": False,
        "brCode": "00020101021226980014br.gov.bcb.pix",
        "expiresIn": 86424,
        "pixKey": "67856db0-ac6e-4276-8309-503a22a896dc",
        "paymentLinkUrl": "https://woovi-sandbox.com/pay/788c8d0d-182b",
        "qrCodeImage": "https://api.woovi-sandbox.com/openpix/charge/x.png",
        "globalID": "Q2hhcmdlOjY4ZDQwOTQzMDY5YTI4ZjgzMTEzOTVkZA==",
    },
    "company": {"id": "6810ce3b892866f103d77ef2", "name": "Lucas Aprigio"},
    "account": {"environment": "TESTING"},
}
"""The settlement delivery, from the document's own webhook example.

``vendor/openpix-openapi.json``, ``webhooks["OPENPIX:CHARGE_COMPLETED"]``.
Two long values — ``brCode`` and ``paymentLinkUrl`` — are truncated to fit
the line budget; nothing here reads their length, and every field an
assertion touches is verbatim.
"""


class Wire:
    """An ``httpx`` handler that answers a fixed body and records the calls.

    Attributes:
        body (Any): The JSON body every answer carries.
        status (int): The status code every answer carries.
        requests (list[httpx.Request]): Every request that reached it, in
            order, kept so a test can read the bytes that left the process.
    """

    def __init__(self, body: Any, *, status: int = 200) -> None:
        """Script the answer.

        Args:
            body (Any): The JSON body to return.
            status (int): The status code to return.
        """
        self.body: Any = body
        self.status: int = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record the request and answer with the scripted body.

        Args:
            request (httpx.Request): The outbound request.

        Returns:
            httpx.Response: The scripted answer.
        """
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)

    @property
    def sent(self) -> httpx.Request:
        """Return the single request that was made.

        Returns:
            httpx.Request: The first recorded request.

        Raises:
            AssertionError: If no request was made, which would otherwise
                surface as an ``IndexError`` far from the cause.
        """
        assert self.requests, "no request reached the transport"
        return self.requests[0]

    @property
    def sent_body(self) -> dict[str, Any]:
        """Return the decoded JSON body of the single request.

        Returns:
            dict[str, Any]: The body as OpenPix would parse it.
        """
        decoded: dict[str, Any] = json.loads(self.sent.content)
        return decoded


def provider_over(wire: Wire) -> OpenPixPixProvider:
    """Build the adapter the recipe tells a consumer to build.

    Args:
        wire (Wire): The handler standing in for OpenPix.

    Returns:
        OpenPixPixProvider: The adapter, over the generated client, over an
        ``HTTPClient`` whose transport is the handler. Nothing between the
        adapter and the socket is replaced, which is the whole point: the
        generated client's ``_dump`` and ``_validate`` both run.
    """
    return OpenPixPixProvider(
        OpenPixClient(
            HTTPClient(
                base_url=BASE_URL,
                default_headers={"Authorization": APP_ID},
                transport=httpx.MockTransport(wire),
            )
        )
    )


def refuse_network(request: httpx.Request) -> httpx.Response:
    """Fail rather than answer, so an unasked round trip is visible.

    Args:
        request (httpx.Request): The request that should not exist.

    Returns:
        httpx.Response: Never returns.

    Raises:
        AssertionError: Always. ``parse_webhook`` must decide from the
            delivery alone, and a hidden refetch would otherwise cost
            latency on every event without failing anything.
    """
    raise AssertionError(f"an HTTP call was made: {request.url}")


def offline_provider() -> OpenPixPixProvider:
    """Build an adapter whose transport fails if anything calls it.

    Returns:
        OpenPixPixProvider: An adapter for the delivery tests, wired to a
        transport that raises on any request.
    """
    return OpenPixPixProvider(
        OpenPixClient(
            HTTPClient(
                base_url=BASE_URL,
                default_headers={"Authorization": APP_ID},
                transport=httpx.MockTransport(refuse_network),
            )
        )
    )


def charge_read_app(wire: Wire) -> FastAPI:
    """Mount ``get_pix_charge`` behind a route, with the SDK's handlers.

    Args:
        wire (Wire): The handler standing in for OpenPix.

    Returns:
        FastAPI: An app whose ``GET /charges/{charge_id}`` returns the
        canonical charge. ``register_exception_handlers`` is installed
        because the status code a consumer sees is decided there, and an
        exception the adapter lets escape is only visible as a status code
        once something is listening for it.
    """
    app = FastAPI()
    register_exception_handlers(app)
    adapter = provider_over(wire)

    @app.get("/charges/{charge_id}")
    async def read(charge_id: str) -> PixCharge:
        """Read one charge back from OpenPix.

        Args:
            charge_id (str): The charge's identifier.

        Returns:
            PixCharge: The charge as OpenPix currently reports it.
        """
        return await adapter.get_pix_charge(charge_id)

    return app


def delivery_event(charge: dict[str, Any]) -> OpenPixWebhookEvent:
    """Build a verified settlement delivery around a charge object.

    Args:
        charge (dict[str, Any]): The ``charge`` object to deliver.

    Returns:
        OpenPixWebhookEvent: What ``make_openpix_webhook_dependency`` hands a
        route for that body, built the same way the dependency builds it —
        the payload is the decoded JSON, and ``body`` the bytes it came from.
    """
    payload: dict[str, Any] = {
        "event": "OPENPIX:CHARGE_COMPLETED",
        "charge": charge,
    }
    return OpenPixWebhookEvent(
        event_name="OPENPIX:CHARGE_COMPLETED",
        event=OpenPixEvent.CHARGE_COMPLETED,
        payload=payload,
        body=json.dumps(payload).encode(),
    )


class TestTheBodyThatLeavesTheProcess:
    """What ``create_pix_charge`` actually POSTs to ``/api/v1/charge``.

    The stub suite asserts on ``client.created_with``, a ``ChargePayload``
    object — so it cannot see serialization at all. Woovi does not read
    objects; it reads a body, and #236 was a defect purely of that body.
    """

    async def test_the_translation_is_visible_on_the_wire(self) -> None:
        """Cents, seconds and our reference, in OpenPix's own spelling."""
        wire = Wire({"charge": CAPTURED_CHARGE})

        await provider_over(wire).create_pix_charge(
            PixChargeRequest(
                amount_cents=1190,
                reference="order-1042",
                description="Purchase of 100 coins",
                expires_in=timedelta(hours=1),
            )
        )

        assert wire.sent.method == "POST"
        assert wire.sent.url.path == CHARGE_PATH
        assert wire.sent.headers["authorization"] == APP_ID
        assert wire.sent_body == {
            "correlationID": "order-1042",
            "value": 1190,
            "comment": "Purchase of 100 coins",
            "expiresIn": 3600,
        }

    async def test_an_array_nobody_informed_stays_off_the_wire(self) -> None:
        """#236, asserted where the consumer meets it.

        ``tests/integrations/payment/openpix/test_request_body_shape.py``
        pins this on ``OpenPixClient.create_charge``. The adapter is a
        second call site with its own ``ChargePayload`` construction, and
        the recipe points consumers at the adapter — so the guarantee has
        to hold here too, or the fix protects the wrong door.
        """
        wire = Wire({"charge": CAPTURED_CHARGE})

        await provider_over(wire).create_pix_charge(
            PixChargeRequest(amount_cents=500, reference="order-2")
        )

        assert "splits" not in wire.sent_body
        assert "additionalInfo" not in wire.sent_body

    async def test_a_field_the_caller_left_alone_is_not_sent_as_null(self) -> None:
        """A request without a description must not claim an empty one."""
        wire = Wire({"charge": CAPTURED_CHARGE})

        await provider_over(wire).create_pix_charge(
            PixChargeRequest(amount_cents=500, reference="order-3")
        )

        assert wire.sent_body == {"correlationID": "order-3", "value": 500}

    async def test_a_payer_without_a_name_is_not_sent(self) -> None:
        """OpenPix requires ``name`` in all three ``oneOf`` variants."""
        wire = Wire({"charge": CAPTURED_CHARGE})

        await provider_over(wire).create_pix_charge(
            PixChargeRequest(
                amount_cents=100,
                reference="order-4",
                payer=PixPayer(tax_id="12345678909"),
            )
        )

        assert "customer" not in wire.sent_body

    async def test_a_full_payer_reaches_the_customer_block(self) -> None:
        """The keys are OpenPix's, not ours: ``taxID``, not ``tax_id``."""
        wire = Wire({"charge": CAPTURED_CHARGE})

        await provider_over(wire).create_pix_charge(
            PixChargeRequest(
                amount_cents=100,
                reference="order-5",
                payer=PixPayer(
                    name="Maria Souza",
                    tax_id="12345678909",
                    email="maria@example.com",
                ),
            )
        )

        assert wire.sent_body["customer"] == {
            "name": "Maria Souza",
            "email": "maria@example.com",
            "taxID": "12345678909",
        }

    async def test_a_payer_known_only_by_name_is_not_sent(self) -> None:
        """The case between the two the stub suite covers.

        ``CustomerPayload.oneOf`` requires ``name`` together with one of
        ``taxID`` / ``email`` / ``phone``. A payer with a name and nothing
        else satisfies none of the three, and the merged model the codegen
        emits does not enforce ``oneOf`` — so the block goes out as
        ``{"name": "Maria Souza"}``, which the document says is invalid.
        Omitting it is the resolution #240 proposes: the charge is still
        openable, only unidentified.
        """
        wire = Wire({"charge": CAPTURED_CHARGE})

        await provider_over(wire).create_pix_charge(
            PixChargeRequest(
                amount_cents=100,
                reference="order-6",
                payer=PixPayer(name="Maria Souza"),
            )
        )

        assert "customer" not in wire.sent_body


class TestTheBodyOpenPixReturns:
    """The half no adapter test reached: ``response.json()`` into a charge."""

    async def test_the_captured_charge_becomes_a_canonical_charge(self) -> None:
        """#238 end to end, through the class the recipe names.

        ``expiresIn`` is the field that shipped broken: the document types
        it ``string`` on ``Charge`` and the API answers ``3600``, so every
        charge read raised ``ValidationError`` until the overlay corrected
        it. Reading it here means the whole body went through
        ``_validate`` — which is the one thing thirteen stubbed tests could
        not show.
        """
        wire = Wire({"charge": CAPTURED_CHARGE})

        charge = await provider_over(wire).create_pix_charge(
            PixChargeRequest(amount_cents=1190, reference="order-1042")
        )

        assert charge.provider == "openpix"
        assert charge.amount_cents == 1190
        assert isinstance(charge.amount_cents, int)
        assert charge.status is PaymentStatus.PENDING
        assert charge.provider_status == "ACTIVE"
        assert charge.br_code == "00020101021226980014br.gov.bcb.pix"
        assert charge.expires_at == datetime(2026, 8, 29, 15, 17, 16, 60000, tzinfo=UTC)

    async def test_the_identifier_answers_when_there_is_no_global_id(self) -> None:
        """The captured body carries no ``globalID``, and still addresses.

        A charge whose ``provider_charge_id`` were empty could never be
        read back or cancelled, so the fallback chain is load-bearing —
        and it runs on aliases only a real body exercises.
        """
        wire = Wire({"charge": CAPTURED_CHARGE})

        charge = await provider_over(wire).get_pix_charge("order-1042")

        assert charge.provider_charge_id == "5400e12faa5b4dd2a1b7f7f0e0a3a0c1"
        assert charge.reference == "f2dc576d-a6db-4677-9cb7-4de54964cc87"

    async def test_the_global_id_wins_when_the_body_carries_one(self) -> None:
        """``globalID`` is typed ``Any`` in the document, so it needs a body."""
        body = {**CAPTURED_CHARGE, "globalID": "Q2hhcmdlOjY4ZDQwOTQz"}
        wire = Wire({"charge": body})

        charge = await provider_over(wire).get_pix_charge("order-1042")

        assert charge.provider_charge_id == "Q2hhcmdlOjY4ZDQwOTQz"

    async def test_the_top_level_brcode_is_read_off_the_wire(self) -> None:
        """OpenPix repeats ``brCode`` beside the charge; both are aliases."""
        body = {**CAPTURED_CHARGE}
        del body["brCode"]
        wire = Wire(
            {
                "charge": body,
                "correlationID": "order-1042",
                "brCode": "00020101021226980014br.gov.bcb.pix-TOP",
            }
        )

        charge = await provider_over(wire).create_pix_charge(
            PixChargeRequest(amount_cents=1190, reference="order-1042")
        )

        assert charge.br_code == "00020101021226980014br.gov.bcb.pix-TOP"

    async def test_a_2xx_carrying_no_charge_is_refused(self) -> None:
        """A body without ``charge`` is refused where it arrives."""
        wire = Wire({"correlationID": "order-7"})

        with pytest.raises(ValueError, match="no charge body"):
            await provider_over(wire).create_pix_charge(
                PixChargeRequest(amount_cents=500, reference="order-7")
            )

    async def test_the_provider_rejection_reaches_the_caller(self) -> None:
        """Woovi's own 400 text, and the exception a service must catch.

        This is the body Woovi answered while #236 was open. A rejected
        charge has to raise: a ``PixCharge`` with empty fields would be a
        charge a payer can never pay.
        """
        wire = Wire(
            {"error": "O array de split precisa ter ao menos um item"},
            status=400,
        )

        with pytest.raises(httpx.HTTPStatusError) as caught:
            await provider_over(wire).create_pix_charge(
                PixChargeRequest(amount_cents=1190, reference="order-8")
            )

        assert caught.value.response.status_code == 400


class TestReadingAChargeBack:
    """``get_pix_charge`` addresses a route and reads a real body."""

    async def test_the_id_addresses_the_charge_route(self) -> None:
        """The identifier is a path segment, not a query parameter."""
        wire = Wire({"charge": CAPTURED_CHARGE})

        await provider_over(wire).get_pix_charge("5400e12faa5b4dd2a1b7f7f0e0a3a0c1")

        assert wire.sent.method == "GET"
        assert wire.sent.url.path == (f"{CHARGE_PATH}/5400e12faa5b4dd2a1b7f7f0e0a3a0c1")
        assert not wire.sent.url.params

    async def test_a_completed_charge_reads_as_paid(self) -> None:
        """The state a service branches on, taken from a real status string."""
        body = {**CAPTURED_CHARGE, "status": "COMPLETED"}
        wire = Wire({"charge": body})

        charge = await provider_over(wire).get_pix_charge("ch_1")

        assert charge.status is PaymentStatus.PAID
        assert charge.provider_status == "COMPLETED"

    async def test_an_expired_charge_reads_as_expired(self) -> None:
        """The third and last state the generated enum declares."""
        body = {**CAPTURED_CHARGE, "status": "EXPIRED"}
        wire = Wire({"charge": body})

        charge = await provider_over(wire).get_pix_charge("ch_1")

        assert charge.status is PaymentStatus.EXPIRED

    async def test_an_answer_without_a_charge_is_refused(self) -> None:
        """``{}`` validates into a ``GetChargeResponse`` with no charge."""
        wire = Wire({})

        with pytest.raises(ValueError, match="no charge"):
            await provider_over(wire).get_pix_charge("ch_1")


class TestCancellingACharge:
    """``cancel_pix_charge`` issues a ``DELETE`` and reports two fields."""

    async def test_the_delete_reaches_the_charge_route(self) -> None:
        """The method matters: a wrong verb here leaves money collectable."""
        wire = Wire({"status": "OK", "id": "ch_global_1"})

        charge = await provider_over(wire).cancel_pix_charge("ch_global_1")

        assert wire.sent.method == "DELETE"
        assert wire.sent.url.path == f"{CHARGE_PATH}/ch_global_1"
        assert charge.status is PaymentStatus.CANCELLED
        assert charge.provider_charge_id == "ch_global_1"
        assert charge.provider_status == "OK"
        assert charge.raw == {"status": "OK", "id": "ch_global_1"}

    async def test_the_answer_carries_no_amount_and_none_is_invented(self) -> None:
        """The route answers two fields; the contract does not guess more."""
        wire = Wire({"status": "OK", "id": "ch_global_1"})

        charge = await provider_over(wire).cancel_pix_charge("ch_global_1")

        assert charge.amount_cents == 0
        assert charge.br_code is None
        assert charge.expires_at is None
        assert len(wire.requests) == 1


class TestTheIdentifierReachesThePathIntact:
    """The ``correlationID`` a consumer chose, arriving where it was sent.

    ``PixChargeRequest.reference`` validates only ``min_length=1``, and
    OpenPix accepts either its own id or the ``correlationID`` on these
    routes — so the identifier in the path is a string the consumer picked.
    Every identifier in the stub suite is ASCII-safe, so nothing measured
    what a reserved character does.
    """

    @staticmethod
    def path_segment(wire: Wire) -> str:
        """Return the charge segment exactly as it went on the wire.

        Args:
            wire (Wire): The handler that recorded the request.

        Returns:
            str: The last path segment of ``raw_path``, still encoded.
            ``httpx.URL.path`` percent-*decodes*, which hides the whole
            defect: a segment that arrived as two segments reads back as
            the original string.
        """
        raw = wire.sent.url.raw_path.decode()
        return raw.removeprefix(f"{CHARGE_PATH}/")

    async def test_an_ascii_safe_identifier_is_unchanged(self) -> None:
        """The common case, pinned so the escaping fix cannot over-reach."""
        wire = Wire({"charge": CAPTURED_CHARGE})

        await provider_over(wire).get_pix_charge("order-1042")

        assert self.path_segment(wire) == "order-1042"

    async def test_a_space_is_encoded_by_the_http_client(self) -> None:
        """Not every character is a problem; httpx already handles this one."""
        wire = Wire({"charge": CAPTURED_CHARGE})

        await provider_over(wire).get_pix_charge("pedido 42")

        assert self.path_segment(wire) == "pedido%2042"

    @pytest.mark.parametrize("identifier", ["order#42", "order/1042", "50%off", "a?b"])
    async def test_a_reserved_character_still_addresses_the_same_charge(
        self, identifier: str
    ) -> None:
        """The read that used to silently answer about another charge.

        Until v0.270.0 ``client.py`` built ``f"/api/v1/charge/{id}"``, so a
        reserved character kept its URL meaning: ``#`` started a fragment
        the client drops (``order#42`` became a request about ``order``),
        ``/`` added a path segment, ``?`` started the query, and ``%``
        opened an escape that was never closed. The generated client's own
        docstring pushed the encoding onto the caller; the adapter is the
        layer the recipe names and it repeated no such warning.

        The emitter now interpolates every path parameter through
        ``_path_param``, so the assertion is ``quote(identifier, safe="")``
        — the only rendering that survives a round trip for all four.

        Args:
            identifier (str): A charge id carrying one reserved character.
        """
        wire = Wire({"charge": CAPTURED_CHARGE})

        await provider_over(wire).get_pix_charge(identifier)

        assert self.path_segment(wire) == quote(identifier, safe="")

    async def test_a_cancellation_never_deletes_a_different_charge(self) -> None:
        """The worst reading of #242, kept as its own test.

        A misaddressed ``GET`` answers about the wrong charge; a
        misaddressed ``DELETE`` **withdraws** one. ``order#42`` and
        ``order`` are both identifiers a consumer can legitimately have
        issued, so this was reachable without anything malicious.
        """
        wire = Wire({"status": "OK", "id": "order#42"})

        await provider_over(wire).cancel_pix_charge("order#42")

        assert self.path_segment(wire) != "order"


class TestRawHasOneSpelling:
    """``PixCharge.raw``, which the recipe teaches consumers to read.

    ``docs/recipes/pix-protocol.md`` shows ``charge.raw.get("paymentLinkUrl")``
    as the way to reach a field the contract does not model. Until v0.270.0
    whether that worked depended on which path produced the charge, and
    nothing asserted it on the API path before this module.
    """

    async def test_the_api_path_writes_the_wire_spelling(self) -> None:
        """The exact call the recipe publishes, on a charge read by API.

        ``_to_pix_charge`` used to call ``model_dump(mode="json")`` without
        ``by_alias=True``. Every declared field therefore landed under its
        Python name while every field the specification does not declare
        kept the wire name — so ``raw`` was a mixture, and the one spelling
        a consumer can predict from OpenPix's documentation was the one
        missing. It now dumps ``by_alias=True``.
        """
        body = {**CAPTURED_CHARGE, "paymentLinkUrl": "https://openpix.com/pl_1"}
        wire = Wire({"charge": body})

        charge = await provider_over(wire).get_pix_charge("ch_1")

        assert charge.raw.get("paymentLinkUrl") == "https://openpix.com/pl_1"
        assert "payment_link_url" not in charge.raw

    async def test_a_field_the_specification_omits_survives_validation(self) -> None:
        """The response models are ``extra="allow"``, and it shows here.

        Worth pinning because the adapter's own docstring used to say the
        opposite — that the API path's ``raw`` was the payload *after*
        validation dropped everything undeclared. ``paidAt`` is undeclared
        on ``Charge`` and reaches ``raw`` intact; the docstring was
        corrected in v0.270.0.
        """
        body = {**CAPTURED_CHARGE, "paidAt": "2026-08-29T15:20:00.000Z"}
        wire = Wire({"charge": body})

        charge = await provider_over(wire).get_pix_charge("ch_1")

        assert charge.raw["paidAt"] == "2026-08-29T15:20:00.000Z"

    async def test_paid_at_stays_empty_on_the_api_path(self) -> None:
        """The behaviour is right even where the stated reason is not.

        ``_to_pix_charge`` never reads ``paidAt``, so the typed field is
        empty on a charge read from the API even though the value survived
        into ``raw``. Pinned as the current contract: a service that needs
        the settlement instant reads it from the delivery.
        """
        body = {**CAPTURED_CHARGE, "paidAt": "2026-08-29T15:20:00.000Z"}
        wire = Wire({"charge": body})

        charge = await provider_over(wire).get_pix_charge("ch_1")

        assert charge.paid_at is None


class TestAStatusTheGeneratedEnumDoesNotKnow:
    """One status the enum lacks, on both paths.

    The document declares an enum of three values on ``Charge.status`` and
    leaves ``WebhookCharge.status`` an unconstrained ``string`` — the same
    charge object, two different commitments — so by the source the SDK
    vendors a fourth state is legitimate. Until v0.270.0 the two paths
    answered opposite things and neither was true: the API refused the read
    (a ``500``), the delivery reported ``PENDING``. Both now answer
    :attr:`PaymentStatus.UNKNOWN` and keep the provider's word.
    """

    async def test_the_api_path_does_not_lose_the_provider_value(self) -> None:
        """The value OpenPix sent has to survive somewhere.

        Until v0.270.0 a bare ``pydantic.ValidationError`` escaped
        ``get_pix_charge`` and the app answered ``500
        INTERNAL_SERVER_ERROR``, which told nobody which state arrived.
        #241 settled on the permissive read: the charge comes back and
        ``provider_status`` keeps the provider's own word.
        """
        body = {**CAPTURED_CHARGE, "status": "CANCELLED"}
        wire = Wire({"charge": body})

        charge = await provider_over(wire).get_pix_charge("ch_1")

        assert charge.provider_status == "CANCELLED"

    async def test_the_route_a_service_mounts_does_not_answer_500(self) -> None:
        """The failure mode reproduced where a consumer meets it.

        Reading the charge inside a route with the SDK's own handlers
        installed answered, until v0.270.0::

            500 {"detail":"Internal server error",
                 "code":"INTERNAL_SERVER_ERROR","details":{}}

        The value OpenPix sent was nowhere in it — so a support ticket for
        a state the SDK had not seen before started with no information at
        all. The permissive read #241 settled on answers ``200`` carrying
        ``provider_status``.
        """
        body = {**CAPTURED_CHARGE, "status": "CANCELLED"}
        app = charge_read_app(Wire({"charge": body}))
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

        async with httpx.AsyncClient(
            transport=transport, base_url="http://service"
        ) as client:
            response = await client.get("/charges/ch_1")

        assert response.status_code != 500
        assert "CANCELLED" in response.text

    def test_the_delivery_path_does_not_call_an_unknown_status_pending(
        self,
    ) -> None:
        """The silent half, which is the dangerous one.

        The API path at least fails loudly. Here the delivery is accepted,
        ``provider_status`` keeps ``"CANCELLED"``, and ``status`` reads
        ``PENDING`` — a charge reported as awaiting payment that the
        provider just said is not. ``EVENT_MAP`` already answers "I do not
        know" for an unmapped event name; this asserts only that the status
        stops claiming ``PENDING``, whichever value #241 chooses instead.
        """
        event = delivery_event({"status": "CANCELLED", "value": 1990})

        parsed = offline_provider().parse_webhook(event)

        assert parsed.charge is not None
        assert parsed.charge.provider_status == "CANCELLED"
        assert parsed.charge.status is not PaymentStatus.PENDING


class TestTheDeliveryValueIsNotSilentlyZero:
    """``value`` in a delivery, which is what a service credits on.

    ``_from_webhook_payload`` guards ``to_cents`` with
    ``isinstance(value, (int, float))``, a narrower test than the function
    it protects: ``to_cents`` accepts ``str`` on purpose, because its
    documented input is a raw payload. The guard hides from it exactly the
    case it knows how to handle.
    """

    def test_an_integer_value_is_read_as_cents(self) -> None:
        """The path that works, so the failing ones are not about plumbing."""
        event = delivery_event({"status": "COMPLETED", "value": 1990})

        parsed = offline_provider().parse_webhook(event)

        assert parsed.charge is not None
        assert parsed.charge.amount_cents == 1990

    def test_a_numeric_string_value_is_read_as_cents(self) -> None:
        """A settled charge credited as nothing.

        ``to_cents("1990")`` answers ``1990``; the ``isinstance`` guard
        never lets it try. The provider's own document types ``value`` as
        ``string`` on two schemas, so a string here is not hypothetical —
        and JSON has no way to signal which one a delivery will use.
        """
        event = delivery_event({"status": "COMPLETED", "value": "1990"})

        parsed = offline_provider().parse_webhook(event)

        assert parsed.charge is not None
        assert parsed.charge.amount_cents == 1990

    @pytest.mark.parametrize("value", [None, "abc", ""])
    def test_a_value_that_cannot_be_read_is_not_reported_as_zero(
        self, value: object
    ) -> None:
        """Zero is the wrong answer to a value nothing could read.

        ``PixCharge.amount_cents`` is a required ``int``, so there is no
        empty value to fall back to — refusing is the only resolution that
        does not put a plausible number on a settled charge. Contrast the
        fractional and negative cases, which already raise: ``to_cents``
        knows how to refuse, and only the guard stops it being asked.

        Args:
            value (object): A ``value`` no reading turns into cents.
        """
        event = delivery_event({"status": "COMPLETED", "value": value})

        with pytest.raises(ValueError):
            offline_provider().parse_webhook(event)

    def test_a_fractional_value_already_raises(self) -> None:
        """The asymmetry #239 is about, pinned on the side that is right."""
        event = delivery_event({"status": "COMPLETED", "value": 19.9})

        with pytest.raises(ValueError, match="whole number of cents"):
            offline_provider().parse_webhook(event)

    def test_a_negative_value_already_raises(self) -> None:
        """Same guard, other direction, same conclusion."""
        event = delivery_event({"status": "COMPLETED", "value": -1})

        with pytest.raises(ValueError, match="cannot be negative"):
            offline_provider().parse_webhook(event)


@pytest.fixture(scope="module")
def key_pair() -> tuple[rsa.RSAPrivateKey, str]:
    """Generate the signing pair the delivery tests sign with.

    Returns:
        tuple[rsa.RSAPrivateKey, str]: The private key and its PEM public
        half. Generated rather than OpenPix's published key because signing
        needs the private half, which only they hold.
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


def sign(private: rsa.RSAPrivateKey, body: bytes) -> str:
    """Sign a body the way OpenPix documents.

    Args:
        private (rsa.RSAPrivateKey): The signing key.
        body (bytes): The exact bytes the header covers.

    Returns:
        str: The base64 signature, as the header carries it.
    """
    return base64.b64encode(
        private.sign(body, padding.PKCS1v15(), hashes.SHA256())
    ).decode()


def webhook_app(public_pem: str) -> FastAPI:
    """Build the route a service mounts, with the adapter behind it.

    Args:
        public_pem (str): The public key the dependency verifies against.

    Returns:
        FastAPI: An app whose ``POST /webhook`` runs the whole documented
        path — verify the signature, decode the body, resolve the event
        name, hand the result to ``parse_webhook`` — and answers with the
        canonical event.
    """
    app = FastAPI()
    verify = make_openpix_webhook_dependency(
        verifier=webhook_verifier(public_key_pem=public_pem)
    )
    adapter = offline_provider()

    @app.post("/webhook")
    async def receive(
        event: OpenPixWebhookEvent = Depends(verify),
    ) -> PixPaymentEvent:
        """Map a verified delivery into the canonical event.

        Args:
            event (OpenPixWebhookEvent): The verified delivery.

        Returns:
            PixPaymentEvent: The canonical event.
        """
        return adapter.parse_webhook(event)

    return app


class TestAVerifiedDeliveryReachesTheAdapter:
    """The delivery path a service really runs, from bytes to event.

    The stub suite constructs ``OpenPixWebhookEvent`` by hand, so the JSON
    decode and the event-name lookup between the socket and the adapter are
    never exercised. Here the body is signed, posted, verified and decoded
    first — the same order production runs in.
    """

    async def test_the_published_delivery_becomes_a_paid_event(
        self, key_pair: tuple[rsa.RSAPrivateKey, str]
    ) -> None:
        """The document's own settlement example, carried end to end.

        Args:
            key_pair (tuple[rsa.RSAPrivateKey, str]): The signing pair.
        """
        private, public_pem = key_pair
        body = json.dumps(COMPLETED_DELIVERY).encode()
        transport = httpx.ASGITransport(app=webhook_app(public_pem))

        async with httpx.AsyncClient(
            transport=transport, base_url="http://service"
        ) as client:
            response = await client.post(
                "/webhook",
                content=body,
                headers={
                    "content-type": "application/json",
                    "x-webhook-signature": sign(private, body),
                },
            )

        assert response.status_code == 200
        parsed = response.json()
        assert parsed["type"] == "charge_paid"
        assert parsed["provider_event_name"] == "OPENPIX:CHARGE_COMPLETED"
        assert parsed["charge"]["status"] == "paid"
        assert parsed["charge"]["amount_cents"] == 1
        assert parsed["charge"]["provider_charge_id"] == (
            "Q2hhcmdlOjY4ZDQwOTQzMDY5YTI4ZjgzMTEzOTVkZA=="
        )
        assert parsed["charge"]["reference"] == ("3f2a2690-8224-4aae-a1ba-ed26d4d61f81")
        assert parsed["charge"]["paid_at"] == "2025-09-24T15:07:50.891000Z"

    async def test_the_undeclared_fields_of_a_delivery_survive_in_raw(
        self, key_pair: tuple[rsa.RSAPrivateKey, str]
    ) -> None:
        """``raw`` on this path is the delivery, in the provider's spelling.

        Args:
            key_pair (tuple[rsa.RSAPrivateKey, str]): The signing pair.
        """
        private, public_pem = key_pair
        body = json.dumps(COMPLETED_DELIVERY).encode()
        transport = httpx.ASGITransport(app=webhook_app(public_pem))

        async with httpx.AsyncClient(
            transport=transport, base_url="http://service"
        ) as client:
            response = await client.post(
                "/webhook",
                content=body,
                headers={
                    "content-type": "application/json",
                    "x-webhook-signature": sign(private, body),
                },
            )

        raw = response.json()["charge"]["raw"]
        assert raw["paidAt"] == "2025-09-24T15:07:50.891Z"
        assert raw["fee"] == 85
        assert raw["customer"]["taxID"]["taxID"] == "44720743000101"

    async def test_an_unsigned_delivery_never_reaches_the_adapter(
        self, key_pair: tuple[rsa.RSAPrivateKey, str]
    ) -> None:
        """The forged settlement notice, refused before any mapping.

        Args:
            key_pair (tuple[rsa.RSAPrivateKey, str]): The signing pair; only
                the public half is used, so nothing signs the body.
        """
        _private, public_pem = key_pair
        body = json.dumps(COMPLETED_DELIVERY).encode()
        transport = httpx.ASGITransport(app=webhook_app(public_pem))

        async with httpx.AsyncClient(
            transport=transport, base_url="http://service"
        ) as client:
            response = await client.post(
                "/webhook",
                content=body,
                headers={"content-type": "application/json"},
            )

        assert response.status_code == 401

    async def test_an_event_outside_the_charge_lifecycle_keeps_its_name(
        self, key_pair: tuple[rsa.RSAPrivateKey, str]
    ) -> None:
        """A dispute delivery is visible as unknown, not swallowed.

        Args:
            key_pair (tuple[rsa.RSAPrivateKey, str]): The signing pair.
        """
        private, public_pem = key_pair
        body = json.dumps({"event": "OPENPIX:DISPUTE_CREATED"}).encode()
        transport = httpx.ASGITransport(app=webhook_app(public_pem))

        async with httpx.AsyncClient(
            transport=transport, base_url="http://service"
        ) as client:
            response = await client.post(
                "/webhook",
                content=body,
                headers={
                    "content-type": "application/json",
                    "x-webhook-signature": sign(private, body),
                },
            )

        parsed = response.json()
        assert parsed["type"] == PixEventType.UNKNOWN.value
        assert parsed["provider_event_name"] == "OPENPIX:DISPUTE_CREATED"
        assert parsed["charge"] is None

    async def test_an_event_name_this_version_does_not_know_is_unknown(
        self, key_pair: tuple[rsa.RSAPrivateKey, str]
    ) -> None:
        """A name OpenPix adds tomorrow must not 500 the route today.

        The dependency leaves ``event`` as ``None`` for a name it cannot
        resolve, and that ``None`` reaches ``parse_webhook``. Only a real
        delivery exercises it: the stub suite always sets the member.

        Args:
            key_pair (tuple[rsa.RSAPrivateKey, str]): The signing pair.
        """
        private, public_pem = key_pair
        body = json.dumps({"event": "OPENPIX:SOMETHING_NEW"}).encode()
        transport = httpx.ASGITransport(app=webhook_app(public_pem))

        async with httpx.AsyncClient(
            transport=transport, base_url="http://service"
        ) as client:
            response = await client.post(
                "/webhook",
                content=body,
                headers={
                    "content-type": "application/json",
                    "x-webhook-signature": sign(private, body),
                },
            )

        assert response.status_code == 200
        assert response.json()["type"] == PixEventType.UNKNOWN.value
        assert response.json()["provider_event_name"] == "OPENPIX:SOMETHING_NEW"
