"""Mercado Pago mapped into the canonical Pix contract.

Everything provider-specific about Mercado Pago that the contract hides is
decided here: money in **reais** on the wire and cents in the contract, nine
payment states folded into the canonical ones, the QR code arriving as
Base64 inside an object the specification never declares, and a webhook
that carries **no payment state at all**.

That last one shapes the module. A Mercado Pago notification names a topic
and a resource id (``data.id``) and nothing else the signature covers; the
status and the amount are not in it. A synchronous ``parse_webhook`` over
the bare notification could only ever answer
:attr:`~tempest_fastapi_sdk.integrations.payment.base.PixEventType.UNKNOWN`
— and a service that settles on ``CHARGE_PAID``, which is how the contract
is meant to be used, would then never settle a single Mercado Pago charge,
with no error anywhere. So the webhook path here re-reads the payment
**before** the event is built: :func:`make_mercado_pago_pix_webhook_dependency`
verifies the signature, fetches the payment by the signed ``data.id``, and
hands :meth:`MercadoPagoPixProvider.parse_webhook` a
:class:`MercadoPagoPixDelivery` that carries the charge. ``parse_webhook``
refuses the bare notification instead of answering ``UNKNOWN`` for it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from fastapi import Depends

from tempest_fastapi_sdk.integrations.payment.base import (
    PaymentStatus,
    PixCharge,
    PixChargeRequest,
    PixEventType,
    PixPaymentEvent,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.events import (
    MercadoPagoEvent,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.money import (
    from_cents,
    to_cents,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.pix import (
    PAYMENTS_PATH,
    parse_pix_payment,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks import (
    DEFAULT_SIGNATURE_VERSIONS,
    MercadoPagoWebhookEvent,
    make_mercado_pago_webhook_dependency,
)
from tempest_fastapi_sdk.utils.http_client import HTTPClient

PROVIDER_NAME: Final[str] = "mercado_pago"
"""Value written into :attr:`PixCharge.provider` by this adapter."""

PIX_PAYMENT_METHOD_ID: Final[str] = "pix"
"""``payment_method_id`` that makes ``POST /v1/payments`` a Pix charge."""

EXPIRED_STATUS_DETAIL: Final[str] = "expired"
"""``status_detail`` that turns a ``cancelled`` payment into an expired one.

Mercado Pago's integration guide describes a Pix whose window closed as
``status: cancelled`` with ``status_detail: expired``. Folding that into
:attr:`PaymentStatus.CANCELLED` would tell a service the merchant withdrew
a charge that simply ran out of time — two cases a service handles
differently (one is a decision, the other is a retry).

**Not yet observed in the sandbox**: no expired payment has been read back
here. If the provider spells it differently, the payment maps to
:attr:`PaymentStatus.CANCELLED`, the provider's ``status_detail`` stays in
``raw``, and nothing is reported as paid.
"""

STATUS_MAP: Final[dict[str, PaymentStatus]] = {
    "pending": PaymentStatus.PENDING,
    "approved": PaymentStatus.PAID,
    "authorized": PaymentStatus.PENDING,
    "in_process": PaymentStatus.IN_ANALYSIS,
    "in_mediation": PaymentStatus.IN_ANALYSIS,
    "rejected": PaymentStatus.FAILED,
    "cancelled": PaymentStatus.CANCELLED,
    "refunded": PaymentStatus.REFUNDED,
    "charged_back": PaymentStatus.CHARGED_BACK,
}
"""Every value of the generated ``PaymentStatus`` enum, mapped.

Keyed by the wire string rather than the generated enum so that importing
the adapter does not build Mercado Pago's generated schemas; the test suite
walks the generated enum and fails on any member missing here.

``authorized`` is an authorized-but-not-captured card payment. Money has
not moved, so it is pending, not paid. ``in_mediation`` is a dispute the
payer opened; the money is held, which is what
:attr:`PaymentStatus.IN_ANALYSIS` means. A string not in this map becomes
:attr:`PaymentStatus.UNKNOWN`, with the provider's own value kept in
``provider_status``.
"""

STATUS_EVENT_MAP: Final[dict[PaymentStatus, PixEventType]] = {
    PaymentStatus.PAID: PixEventType.CHARGE_PAID,
    PaymentStatus.EXPIRED: PixEventType.CHARGE_EXPIRED,
    PaymentStatus.CANCELLED: PixEventType.CHARGE_CANCELLED,
    PaymentStatus.REFUNDED: PixEventType.CHARGE_REFUNDED,
}
"""Canonical event type read off the re-read payment's state.

The signed part of a notification — ``data.id`` — says which payment, not
what happened to it, so the event type is derived from the state the API
answered on the re-read. The body's ``action`` (``payment.created`` /
``payment.updated`` in the provider's guide; not yet observed on a live
delivery here) is unsigned and decides one case only: a pending payment on
a ``payment.created`` delivery is :attr:`PixEventType.CHARGE_CREATED`.
Every other state not listed here is :attr:`PixEventType.UNKNOWN`, with
the provider's action kept in ``provider_event_name``.
"""

PAYER_EMAIL_REQUIRED: Final[str] = (
    "Mercado Pago requires payer.email on a Pix payment: set "
    "PixChargeRequest.payer.email."
)
"""Why :meth:`MercadoPagoPixProvider.create_pix_charge` refuses a request.

Measured against the sandbox on 2026-10-09: ``POST /v1/payments`` for Pix
without a ``payer`` — or with a ``payer`` carrying only ``first_name`` —
answers **500** ``fill and validate error list: payer_cannot_be_nil``. A
500 reads as a provider outage, and the ``HTTPClient`` retries it, so the
mistake would surface as three slow failures blamed on Mercado Pago. The
check runs before the request instead. The contract keeps ``email``
optional because other providers do not need it.
"""

CREATED_ACTION: Final[str] = "payment.created"
"""Body ``action`` of the delivery Mercado Pago sends when a payment is created."""


def _parse_datetime(value: object) -> datetime | None:
    """Read an ISO 8601 timestamp Mercado Pago sends as a string.

    Args:
        value (object): The field as decoded from JSON.

    Returns:
        datetime | None: The parsed timestamp, or ``None`` when the field
        is absent or does not parse. A malformed date is cosmetic and not
        worth refusing a charge over; an unreadable amount is not, and
        :func:`_amount_cents` raises for it.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_status(raw_status: object, status_detail: object) -> PaymentStatus:
    """Read Mercado Pago's status pair into the canonical state.

    Args:
        raw_status (object): The payment's ``status``.
        status_detail (object): The payment's ``status_detail``, which is
            what separates an expired Pix from a cancelled one.

    Returns:
        PaymentStatus: The mapped state, or :attr:`PaymentStatus.UNKNOWN`
        for a status :data:`STATUS_MAP` does not name.
    """
    if not isinstance(raw_status, str) or not raw_status:
        return PaymentStatus.UNKNOWN
    status = STATUS_MAP.get(raw_status, PaymentStatus.UNKNOWN)
    if status is PaymentStatus.CANCELLED and status_detail == EXPIRED_STATUS_DETAIL:
        return PaymentStatus.EXPIRED
    return status


def _amount_cents(payload: Mapping[str, Any]) -> int:
    """Read ``transaction_amount`` (reais) into cents, or refuse the payment.

    Args:
        payload (Mapping[str, Any]): The payment body.

    Returns:
        int: The amount in cents.

    Raises:
        ValueError: If the amount is absent, negative or not a whole
            number of cents. A charge read back without a readable amount
            is not a charge of zero: :func:`confirm_pix_payment` compares
            the amount, and a ``0`` there would be a plausible wrong value
            on the path that releases orders.
    """
    value = payload.get("transaction_amount")
    if value is None or isinstance(value, bool):
        raise ValueError(
            "Mercado Pago returned a payment without a readable "
            f"transaction_amount: {value!r} (id={payload.get('id')!r})"
        )
    try:
        return to_cents(value)
    except (ValueError, TypeError, ArithmeticError) as error:
        raise ValueError(
            "Mercado Pago returned a transaction_amount that cannot be read "
            f"as cents: {value!r} (id={payload.get('id')!r}) — {error}"
        ) from error


def _payer(request: PixChargeRequest) -> dict[str, Any] | None:
    """Build the ``payer`` block from what the service knows.

    Args:
        request (PixChargeRequest): The canonical request.

    Returns:
        dict[str, Any] | None: The payer, or ``None`` when nothing is known.

    Only fields the request carries are sent; none is invented. The full
    name goes in ``first_name`` rather than being split, because where a
    Brazilian name splits into first and last is not something a string
    operation knows. ``identification.type`` is decided by digit count —
    11 is a CPF, 14 a CNPJ — and a tax id of any other length is sent
    without a type rather than with a guessed one.
    """
    payer = request.payer
    if payer is None:
        return None
    block: dict[str, Any] = {}
    if payer.email:
        block["email"] = payer.email
    if payer.name:
        block["first_name"] = payer.name
    if payer.tax_id:
        digits = "".join(ch for ch in payer.tax_id if ch.isdigit())
        identification: dict[str, str] = {"number": digits}
        if len(digits) == 11:
            identification["type"] = "CPF"
        elif len(digits) == 14:
            identification["type"] = "CNPJ"
        block["identification"] = identification
    return block or None


def _to_pix_charge(payload: Mapping[str, Any]) -> PixCharge:
    """Map a Payments API body onto the canonical shape.

    Args:
        payload (Mapping[str, Any]): The decoded body of a create, read or
            update of ``/v1/payments``.

    Returns:
        PixCharge: The canonical charge. ``raw`` is the body as decoded,
        so ``raw`` keys are spelled the way Mercado Pago spells them.

    Raises:
        ValueError: If the body carries no ``id`` or no readable amount.
    """
    payment_id = payload.get("id")
    if payment_id is None or isinstance(payment_id, bool):
        raise ValueError("Mercado Pago returned a payment body without an id.")
    view = parse_pix_payment(payload)
    raw_status = payload.get("status")
    reference = payload.get("external_reference")
    return PixCharge(
        provider=PROVIDER_NAME,
        provider_charge_id=str(payment_id),
        reference=reference if isinstance(reference, str) else "",
        amount_cents=_amount_cents(payload),
        currency=str(payload.get("currency_id") or "BRL"),
        status=_to_status(raw_status, payload.get("status_detail")),
        provider_status=raw_status if isinstance(raw_status, str) else "",
        br_code=view.qr_code,
        qr_code_base64=view.qr_code_base64,
        expires_at=_parse_datetime(payload.get("date_of_expiration")),
        paid_at=_parse_datetime(payload.get("date_approved")),
        raw=dict(payload),
    )


@dataclass(frozen=True, slots=True)
class MercadoPagoPixDelivery:
    """A verified notification, plus the payment re-read because of it.

    What :meth:`MercadoPagoPixProvider.parse_webhook` accepts. Built by
    :func:`make_mercado_pago_pix_webhook_dependency`, which is the only
    place that should build one in production: it is the step that turns a
    notification without state into an event with one.

    Attributes:
        notification (MercadoPagoWebhookEvent): The verified notification.
        charge (PixCharge | None): The payment the notification points at,
            as the API answered on the re-read. ``None`` when the topic is
            not ``payment`` — a merchant order or a Point event is not a
            Pix charge, and fetching it from the Payments API would fail.
    """

    notification: MercadoPagoWebhookEvent
    charge: PixCharge | None = None


class MercadoPagoPixProvider:
    """Mercado Pago as a :class:`~...payment.base.PixProvider`.

    Satisfies the protocol structurally, like every provider seam in the
    SDK. Talks to ``/v1/payments`` directly over the ``HTTPClient`` rather
    than through the generated ``MercadoPagoClient``: the generated
    ``Payment`` model drops ``point_of_interaction`` — the specification
    does not declare it — and with it the QR this adapter exists to return.

    Attributes:
        provider_name (str): Always ``"mercado_pago"``.
    """

    provider_name: str = PROVIDER_NAME

    def __init__(
        self,
        http: HTTPClient,
        *,
        notification_url: str | None = None,
        idempotency_key: Callable[[PixChargeRequest], str] | None = None,
    ) -> None:
        """Wrap a transport that already carries the access token.

        Args:
            http (HTTPClient): Built with ``base_url=DEFAULT_BASE_URL`` and
                ``Authorization: Bearer <access token>``. What separates
                sandbox from production is the token, not the host.
            notification_url (str | None): Sent as ``notification_url`` on
                every charge, so Mercado Pago notifies that URL about it.
                ``None`` leaves the account-level webhook configuration in
                charge.
            idempotency_key (Callable[[PixChargeRequest], str] | None):
                Builds the ``X-Idempotency-Key`` for a create. The default
                is a fresh UUID4 per call. That covers the transport's own
                retries — the ``HTTPClient`` merges headers once, before
                its retry loop, so a ``POST`` retried after a 5xx reuses
                the key and cannot create a second payment. It does **not**
                make two calls for the same ``reference`` collapse into one;
                pass ``lambda request: request.reference`` for that, and
                know that a later charge for the same reference will then
                get the earlier payment back while the provider still
                remembers the key.
        """
        self._http: HTTPClient = http
        self._notification_url: str | None = notification_url
        self._idempotency_key: Callable[[PixChargeRequest], str] = idempotency_key or (
            lambda _request: str(uuid.uuid4())
        )

    async def create_pix_charge(self, request: PixChargeRequest) -> PixCharge:
        """Create a Pix payment at Mercado Pago.

        ``amount_cents`` is sent as ``transaction_amount`` in reais, via
        :func:`from_cents` — a ``Decimal`` quantized to two places, turned
        into the ``float`` JSON can carry. A two-decimal value survives
        that round trip exactly: ``float`` repr is the shortest string
        that reads back to the same number.

        ``expires_in`` becomes ``date_of_expiration``, an absolute
        timestamp in UTC with millisecond precision. Mercado Pago's own
        limits on that window are not re-checked here; duplicating a
        provider's validation is how the copy drifts from the original.

        Args:
            request (PixChargeRequest): What to charge, and for whom.

        Returns:
            PixCharge: The created charge, with ``br_code`` and
            ``qr_code_base64`` filled in.

        Raises:
            ValueError: If the request has no payer e-mail — checked
                before anything is sent, see :data:`PAYER_EMAIL_REQUIRED` —
                or the answer is not a payment body with an id and a
                readable amount.
            httpx.HTTPStatusError: For any non-2xx answer.
        """
        if request.payer is None or not request.payer.email:
            raise ValueError(PAYER_EMAIL_REQUIRED)
        body: dict[str, Any] = {
            "transaction_amount": float(from_cents(request.amount_cents)),
            "payment_method_id": PIX_PAYMENT_METHOD_ID,
            "external_reference": request.reference,
        }
        if request.description is not None:
            body["description"] = request.description
        if request.expires_in is not None:
            deadline = datetime.now(UTC) + request.expires_in
            body["date_of_expiration"] = deadline.isoformat(timespec="milliseconds")
        payer = _payer(request)
        if payer is not None:
            body["payer"] = payer
        if self._notification_url is not None:
            body["notification_url"] = self._notification_url
        response = await self._http.request(
            "POST",
            PAYMENTS_PATH,
            headers={"X-Idempotency-Key": self._idempotency_key(request)},
            json=body,
        )
        response.raise_for_status()
        return _to_pix_charge(self._body(response.json()))

    async def get_pix_charge(self, charge_id: str) -> PixCharge:
        """Read a payment back from Mercado Pago.

        Args:
            charge_id (str): :attr:`PixCharge.provider_charge_id` — the
                payment id, which is also the ``data.id`` a payment
                notification carries.

        Returns:
            PixCharge: The payment as Mercado Pago currently reports it.

        Raises:
            httpx.HTTPStatusError: For any non-2xx answer.
            ValueError: If the answer is not a payment body.
        """
        response = await self._http.request("GET", f"{PAYMENTS_PATH}/{charge_id}")
        response.raise_for_status()
        return _to_pix_charge(self._body(response.json()))

    async def cancel_pix_charge(self, charge_id: str) -> PixCharge:
        """Cancel a payment that has not been paid.

        Sends ``PUT /v1/payments/{id}`` with ``{"status": "cancelled"}``.
        The route is one the provider's own SDK calls to update a payment;
        the body is the provider guide's, and is not yet observed against
        the sandbox. The separate ``PUT /v1/payments/{id}/cancellations``
        the vendored document declares answered like an unrouted path in
        the sandbox on 2026-10-09, so it is not used. The charge returned
        is mapped from the body the provider answers with.

        Args:
            charge_id (str): :attr:`PixCharge.provider_charge_id`.

        Returns:
            PixCharge: The payment after cancellation.

        Raises:
            httpx.HTTPStatusError: For any non-2xx answer, including a
                payment that can no longer be cancelled.
            ValueError: If the answer is not a payment body.
        """
        response = await self._http.request(
            "PUT",
            f"{PAYMENTS_PATH}/{charge_id}",
            json={"status": "cancelled"},
        )
        response.raise_for_status()
        return _to_pix_charge(self._body(response.json()))

    def parse_webhook(self, event: Any) -> PixPaymentEvent:
        """Turn a verified, re-read delivery into a canonical event.

        Args:
            event (Any): A :class:`MercadoPagoPixDelivery`, as produced by
                :func:`make_mercado_pago_pix_webhook_dependency`. Typed
                ``Any`` because the protocol cannot name a per-provider
                type.

        Returns:
            PixPaymentEvent: The event, its type read off the re-read
            payment's state (:data:`STATUS_EVENT_MAP`).

        Raises:
            TypeError: If handed anything else — in particular the bare
                :class:`MercadoPagoWebhookEvent`. That notification carries
                no payment state, so the only event it could become is
                ``UNKNOWN``, and a service settling on ``CHARGE_PAID`` would
                silently never settle. Refusing it makes the missing
                re-read an error at the first delivery rather than a
                stuck order found at reconciliation.
        """
        if not isinstance(event, MercadoPagoPixDelivery):
            hint = (
                " Use make_mercado_pago_pix_webhook_dependency, which re-reads "
                "the payment the notification points at."
                if isinstance(event, MercadoPagoWebhookEvent)
                else ""
            )
            raise TypeError(
                "parse_webhook expects a MercadoPagoPixDelivery, "
                f"got {type(event).__name__}.{hint}"
            )
        notification = event.notification
        action = notification.payload.get("action")
        name = action if isinstance(action, str) and action else notification.topic
        event_type = PixEventType.UNKNOWN
        if event.charge is not None:
            event_type = STATUS_EVENT_MAP.get(event.charge.status, PixEventType.UNKNOWN)
            if (
                event_type is PixEventType.UNKNOWN
                and event.charge.status is PaymentStatus.PENDING
                and action == CREATED_ACTION
            ):
                event_type = PixEventType.CHARGE_CREATED
        return PixPaymentEvent(
            provider=PROVIDER_NAME,
            type=event_type,
            provider_event_name=name,
            charge=event.charge,
            raw=notification.payload,
        )

    async def read_delivery(
        self, notification: MercadoPagoWebhookEvent
    ) -> MercadoPagoPixDelivery:
        """Re-read the payment a verified notification points at.

        Args:
            notification (MercadoPagoWebhookEvent): The verified
                notification. Its ``data_id`` is the one value the
                signature covers, so it is the id used for the read.

        Returns:
            MercadoPagoPixDelivery: The notification and the payment, or
            the notification alone when its topic is not ``payment`` or it
            carried no ``data.id``.

        Raises:
            httpx.HTTPStatusError: If the read fails. The webhook route
                then answers non-2xx and Mercado Pago retries the
                delivery, which is the right outcome for a question the
                provider did not answer.
        """
        if notification.event is not MercadoPagoEvent.PAYMENT or not (
            notification.data_id
        ):
            return MercadoPagoPixDelivery(notification=notification)
        charge = await self.get_pix_charge(notification.data_id)
        return MercadoPagoPixDelivery(notification=notification, charge=charge)

    @staticmethod
    def _body(decoded: object) -> Mapping[str, Any]:
        """Narrow a decoded response to the object the Payments API returns.

        Args:
            decoded (object): ``response.json()``.

        Returns:
            Mapping[str, Any]: The body.

        Raises:
            ValueError: If the body is not a JSON object.
        """
        if not isinstance(decoded, dict):
            raise ValueError(
                f"Mercado Pago answered a payment route with {type(decoded).__name__}, "
                "not a JSON object."
            )
        return decoded


def make_mercado_pago_pix_webhook_dependency(
    secret: str,
    provider: MercadoPagoPixProvider,
    *,
    tolerance_seconds: float | None = None,
    versions: Sequence[str] = DEFAULT_SIGNATURE_VERSIONS,
) -> Callable[..., Coroutine[Any, Any, MercadoPagoPixDelivery]]:
    """Build a FastAPI dependency yielding a verified, re-read delivery.

    Args:
        secret (str): The webhook secret from the Mercado Pago dashboard.
            An empty secret rejects every delivery.
        provider (MercadoPagoPixProvider): The adapter used to re-read the
            payment.
        tolerance_seconds (float | None): Maximum drift between the
            signature's ``ts`` and the clock. Forwarded to
            :func:`make_mercado_pago_webhook_dependency`.
        versions (Sequence[str]): Hash versions to accept, in preference
            order.

    Returns:
        Callable[..., Coroutine[Any, Any, MercadoPagoPixDelivery]]: The
        dependency. It refuses an unsigned or badly signed delivery with
        401 before the route runs, and for a ``payment`` notification
        re-reads the payment by the signed ``data.id``.
    """
    verified = make_mercado_pago_webhook_dependency(
        secret,
        tolerance_seconds=tolerance_seconds,
        versions=versions,
    )

    async def dependency(
        notification: MercadoPagoWebhookEvent = Depends(verified),
    ) -> MercadoPagoPixDelivery:
        """Re-read the payment behind a verified notification.

        Args:
            notification (MercadoPagoWebhookEvent): The verified
                notification.

        Returns:
            MercadoPagoPixDelivery: The delivery ``parse_webhook`` accepts.
        """
        return await provider.read_delivery(notification)

    return dependency


__all__: list[str] = [
    "CREATED_ACTION",
    "EXPIRED_STATUS_DETAIL",
    "PAYER_EMAIL_REQUIRED",
    "PIX_PAYMENT_METHOD_ID",
    "PROVIDER_NAME",
    "STATUS_EVENT_MAP",
    "STATUS_MAP",
    "MercadoPagoPixDelivery",
    "MercadoPagoPixProvider",
    "make_mercado_pago_pix_webhook_dependency",
]
