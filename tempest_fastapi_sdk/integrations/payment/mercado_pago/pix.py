"""The Pix QR data the specification does not declare.

A Pix payment created through ``POST /v1/payments`` comes back with the
copy-and-paste string and the QR image inside ``point_of_interaction``. The
vendored specification never declares that object on the payment resource,
which is checkable —

```bash
grep -c "point_of_interaction" vendor/mercadopago-openapi.yaml   # 2
```

Both hits sit in the same place: the **request** body of the Payouts
transaction intent, where the object carries a lone ``type`` and no
``transaction_data`` at all. On the payment resource there is nothing — the
``Payment`` schema declares only ``transaction_details``, whose
``external_resource_url`` is a hosted page, not the QR.

That omission is not harmless. ``BaseSchema`` is ``extra="ignore"``, so the
generated ``Payment`` model **drops** the object during validation: the QR
arrives in the HTTP body and vanishes on the way to the caller, with no
error and nothing in a log to notice. This module is the typed path for it.

Field names and types are ported from Mercado Pago's own Node SDK, which
models the response the specification omits:
``mercadopago/sdk-nodejs``, ``src/clients/payment/commonTypes.ts`` at commit
``c2d3c6ae`` (2026-07-27) — ``PointOfInteraction``, ``TransactionData`` and
the ``PaymentResponse`` fields reused in :class:`PixPayment`. Pinned by
``TestPixSchemas`` so a drift upstream shows up as a failing test rather
than a silent divergence.

!!! warning "Two models describe one payment, on purpose"
    Use the generated ``Payment`` for everything the specification declares.
    Reach for :class:`PixPayment` when you need the QR: it is a **view** of
    the same body, carrying the Pix-relevant fields plus the object the
    specification forgot. It deliberately does not import the generated
    schemas, so reading a QR does not pay the 0.76 s the generated models
    cost to build.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from tempest_fastapi_sdk import BaseSchema, HTTPClient

PAYMENTS_PATH: str = "/v1/payments"
"""Collection path for the Payments API, as the specification declares it."""


class PixTransactionData(BaseSchema):
    """The Pix payload of ``point_of_interaction.transaction_data``.

    Attributes:
        qr_code (str | None): The copy-and-paste string (EMV BR Code) the
            payer pastes into their bank app.
        qr_code_base64 (str | None): The same code as a base64-encoded PNG,
            ready for an ``<img src="data:image/png;base64,...">``.
        ticket_url (str | None): Hosted page rendering the QR, for a caller
            that would rather redirect than draw it.
        transaction_id (str | None): Provider-side identifier of the Pix
            transaction.
        bank_transfer_id (int | None): Identifier of the bank transfer once
            the payer pays.
        financial_institution (int | str | None): Code of the payer's
            institution. Widened on measured disagreement: the Node SDK
            types it ``number``, while the vendored specification types the
            same concept ``string`` on the Orders API
            (``vendor/mercadopago-openapi.yaml``, under the Orders payment
            method). Accepting both beats raising a ``ValidationError`` over
            a field nobody reconciles on.

    The Pix end-to-end identifier is **not** here. The specification
    declares ``e2e_id`` on the Orders API payment method, and the Node SDK's
    ``TransactionData`` has no such field — so putting one on this model
    would be a promise about a wire name nothing measured. Read it from the
    Orders API, or from ``extra`` on your own model if the Payments API turns
    out to send it.
    """

    qr_code: str | None = None
    qr_code_base64: str | None = None
    ticket_url: str | None = None
    transaction_id: str | None = None
    bank_transfer_id: int | None = None
    financial_institution: int | str | None = None


class PixPointOfInteraction(BaseSchema):
    """Where and how the payment was initiated.

    Attributes:
        type (str | None): Interaction type, ``"CHECKOUT"`` for a Pix
            created through the Payments API.
        sub_type (str | None): Further classification, when the provider
            sends one.
        linked_to (str | None): Identifier of a resource this payment is
            linked to.
        transaction_data (PixTransactionData | None): The QR payload.
    """

    type: str | None = None
    sub_type: str | None = None
    linked_to: str | None = None
    transaction_data: PixTransactionData | None = None


class PixPayment(BaseSchema):
    """A payment read as a Pix flow needs it, QR included.

    Attributes:
        id (int | None): The payment identifier.
        status (str | None): ``"pending"`` until the payer pays, then
            ``"approved"``. A plain ``str`` rather than the generated
            ``PaymentStatus`` so this module stays independent of the
            generated schemas.
        status_detail (str | None): Granular reason behind ``status``.
        payment_method_id (str | None): ``"pix"`` for a Pix payment.
        transaction_amount (float | None): Gross amount **in reais** — the
            type and unit the provider states. Convert it with
            :func:`~tempest_fastapi_sdk.integrations.payment.mercado_pago.to_cents`
            before doing arithmetic on it.
        date_of_expiration (datetime | None): When the QR stops being
            payable.
        point_of_interaction (PixPointOfInteraction | None): The object the
            specification omits, carrying the QR.
    """

    id: int | None = None
    status: str | None = None
    status_detail: str | None = None
    payment_method_id: str | None = None
    transaction_amount: float | None = None
    date_of_expiration: datetime | None = None
    point_of_interaction: PixPointOfInteraction | None = None

    @property
    def qr_code(self) -> str | None:
        """The copy-and-paste Pix string, or None when the response has none.

        Returns:
            str | None: ``point_of_interaction.transaction_data.qr_code``,
            reached without raising when either level is absent — which is
            every non-Pix payment, and a Pix one that has already been paid.
        """
        return self._transaction_data_field("qr_code")

    @property
    def qr_code_base64(self) -> str | None:
        """The QR image as base64 PNG, or None when the response has none.

        Returns:
            str | None: ``transaction_data.qr_code_base64``, None-safe.
        """
        return self._transaction_data_field("qr_code_base64")

    @property
    def ticket_url(self) -> str | None:
        """The hosted QR page, or None when the response has none.

        Returns:
            str | None: ``transaction_data.ticket_url``, None-safe.
        """
        return self._transaction_data_field("ticket_url")

    def _transaction_data_field(self, name: str) -> str | None:
        """Read one string field from the nested transaction data.

        Args:
            name (str): Attribute of :class:`PixTransactionData` to read.

        Returns:
            str | None: The value, or None when ``point_of_interaction`` or
            ``transaction_data`` is absent.
        """
        interaction = self.point_of_interaction
        if interaction is None or interaction.transaction_data is None:
            return None
        value: str | None = getattr(interaction.transaction_data, name)
        return value


def parse_pix_payment(payload: Mapping[str, Any]) -> PixPayment:
    """Read a payment response body as a :class:`PixPayment`.

    Args:
        payload (Mapping[str, Any]): The decoded JSON body of any Payments
            API response — ``POST /v1/payments``, ``GET
            /v1/payments/{id}``, or the payment a webhook made you fetch.

    Returns:
        PixPayment: The view, with the QR when the body carried one.

    Use this when the call already happened through the generated client
    and you kept the raw body, or when the body reached you from somewhere
    else entirely. It never raises on a non-Pix payment: the QR fields
    simply come back None.
    """
    return PixPayment.model_validate(payload)


async def create_pix_payment(
    http: HTTPClient,
    *,
    body: BaseModel | Mapping[str, Any],
    idempotency_key: UUID | None = None,
) -> PixPayment:
    """Create a payment and keep the QR the generated method drops.

    Args:
        http (HTTPClient): The transport, built with
            ``HTTPClient(base_url=DEFAULT_BASE_URL)`` and the
            ``Authorization`` header — the same one ``MercadoPagoClient``
            takes, so pass the client's own.
        body (BaseModel | Mapping[str, Any]): The request body. A generated
            ``PaymentRequest`` is serialized the way the wire expects; a
            plain mapping is sent as given.
        idempotency_key (UUID | None): Value for ``X-Idempotency-Key``.
            Send one: retrying a payment POST without it can charge twice.

    Returns:
        PixPayment: The created payment, with ``qr_code`` and
        ``qr_code_base64`` populated for Pix.

    Raises:
        httpx.HTTPStatusError: For any non-2xx response, the same way the
            generated client reports one.

    This exists because ``MercadoPagoClient.create_payment`` returns the
    generated ``Payment``, and that model has no ``point_of_interaction``
    to put the QR in. Same request, same headers — only the response model
    differs.
    """
    headers: dict[str, str] = {}
    if idempotency_key is not None:
        headers["X-Idempotency-Key"] = str(idempotency_key)
    payload = (
        body.model_dump(
            by_alias=True,
            mode="json",
            exclude_none=True,
            exclude_unset=True,
        )
        if isinstance(body, BaseModel)
        else body
    )
    response = await http.request(
        "POST",
        PAYMENTS_PATH,
        headers=headers,
        json=payload,
    )
    response.raise_for_status()
    return parse_pix_payment(response.json())


async def get_pix_payment(http: HTTPClient, payment_id: int | str) -> PixPayment:
    """Fetch a payment and keep the QR the generated method drops.

    Args:
        http (HTTPClient): The transport, as in :func:`create_pix_payment`.
        payment_id (int | str): The payment identifier.

    Returns:
        PixPayment: The payment, with the QR while it is still payable.

    Raises:
        httpx.HTTPStatusError: For any non-2xx response.

    The QR is worth re-reading rather than caching: a webhook tells you a
    payment changed, and re-fetching is how you learn whether it was paid
    or the QR expired.
    """
    response = await http.request("GET", f"{PAYMENTS_PATH}/{payment_id}")
    response.raise_for_status()
    return parse_pix_payment(response.json())
