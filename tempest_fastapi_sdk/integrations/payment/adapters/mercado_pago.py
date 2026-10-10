"""Mercado Pago mapped into the canonical Pix and card contracts.

Both adapters talk to the **Orders API** (``/v1/orders``). The Payments API
(``/v1/payments``) is labelled *"Esta API será descontinuada em breve"* on the
provider's dashboard and is not modelled by this package any more; every
behaviour below was observed against the sandbox on 2026-10-09
(``vendor/mercadopago-evidence.md`` section 9) unless its docstring says
otherwise.

What the contracts hide, and is decided here:

- **Money as decimal strings.** Orders states ``total_amount`` as
  ``"19.90"``; the contract states cents. :func:`from_cents` /
  :func:`to_cents` convert at the boundary, exactly.
- **One order, one payment.** An order carries ``transactions.payments``;
  these adapters create orders with a single payment and read the first.
  The order id (``ORD…``) is the charge id; the payment id (``PAY…``) is
  what a partial refund addresses, and is kept in ``raw``.
- **A declined card is HTTP 402.** The body carries the reason in
  ``errors`` and the whole order in ``data``. :class:`MercadoPagoCardProvider`
  reads it as a :attr:`PaymentStatus.FAILED` charge instead of raising.
- **A notification carries no state.** It names the order by ``data.id``,
  the one value the signature covers.
  :func:`make_mercado_pago_webhook_delivery_dependency` verifies it and
  re-reads the order before anything is decided.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from typing import Any, Final

import httpx
from fastapi import Depends

from tempest_fastapi_sdk.integrations.payment.base import (
    CardCharge,
    CardChargeRequest,
    PaymentStatus,
    PixCharge,
    PixChargeRequest,
    PixEventType,
    PixPayer,
    PixPaymentEvent,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.money import (
    from_cents,
    to_cents,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks import (
    DEFAULT_SIGNATURE_VERSIONS,
    MercadoPagoWebhookEvent,
    make_mercado_pago_webhook_dependency,
)
from tempest_fastapi_sdk.utils.http_client import HTTPClient

PROVIDER_NAME: Final[str] = "mercado_pago"
"""Value written into ``provider`` by both adapters."""

ORDERS_PATH: Final[str] = "/v1/orders"
"""Collection path of the Orders API."""

ORDER_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9]+$")
"""Shape of an order id (``ORDTST01M4HR…`` in the sandbox).

A notification's ``data.id`` is interpolated into a path only when it
matches, so a value that is not an id never reaches ``/v1/orders/<…>``.
"""

STATUS_MAP: Final[dict[str, PaymentStatus]] = {
    "created": PaymentStatus.PENDING,
    "action_required": PaymentStatus.PENDING,
    "processing": PaymentStatus.IN_ANALYSIS,
    "processed": PaymentStatus.PAID,
    "canceled": PaymentStatus.CANCELLED,
    "failed": PaymentStatus.FAILED,
    "refunded": PaymentStatus.REFUNDED,
}
"""An order's ``status``, mapped to the canonical state.

The first five are the values the document declares (``OrderStatus``); a
test walks the generated enum and fails on a member missing here.
``failed`` (a declined card) and ``refunded`` were observed in the sandbox
outside that list. Anything else — an expired Pix has not been observed —
becomes :attr:`PaymentStatus.UNKNOWN`, with the provider's string kept.

``action_required`` is refined by ``status_detail``: see
:data:`AUTHORIZED_STATUS_DETAIL`.
"""

AUTHORIZED_STATUS_DETAIL: Final[str] = "waiting_capture"
"""``status_detail`` that makes an ``action_required`` order an authorization.

Observed on a card order created with ``capture_mode: manual``. Without it,
``action_required`` waits on the payer (``waiting_transfer`` on a Pix).
"""

STATUS_EVENT_MAP: Final[dict[PaymentStatus, PixEventType]] = {
    PaymentStatus.PENDING: PixEventType.CHARGE_CREATED,
    PaymentStatus.PAID: PixEventType.CHARGE_PAID,
    PaymentStatus.EXPIRED: PixEventType.CHARGE_EXPIRED,
    PaymentStatus.CANCELLED: PixEventType.CHARGE_CANCELLED,
    PaymentStatus.REFUNDED: PixEventType.CHARGE_REFUNDED,
}
"""Canonical Pix event read off the re-read order's state.

The event type comes from the API's answer, never from the notification
body, which is unsigned. States without a canonical event —
:attr:`PaymentStatus.FAILED`, :attr:`PaymentStatus.CHARGED_BACK`,
:attr:`PaymentStatus.UNKNOWN` — become :attr:`PixEventType.UNKNOWN`; the
state itself is on ``event.charge.status``.
"""

RETRYABLE_ACTION_ERRORS: Final[dict[int, frozenset[str]]] = {
    HTTPStatus.CONFLICT: frozenset(
        {"processor_communication_error", "post_processing_operation_pending"}
    ),
}
"""Answers to an order action that mean "not yet", by HTTP status and code.

Measured on 2026-10-09, right after the order was created:

* cancelling a ``capture_mode: manual`` order answered ``409``
  ``processor_communication_error`` (*"Try again shortly."*) in 3 of 10
  attempts; all 3 cancelled on a retry two seconds later;
* refunding an approved ``automatic_async`` order answered ``409``
  ``post_processing_operation_pending`` in 1 of 10; it refunded on retry.

The ``HTTPClient`` does not retry a ``409`` — it usually means a real
conflict — so the adapter retries these codes, and only these, with
:data:`DEFAULT_ACTION_RETRY_DELAYS`.
"""

REFUND_RETRYABLE_ERRORS: Final[dict[int, frozenset[str]]] = {
    HTTPStatus.UNPROCESSABLE_ENTITY: frozenset({"unprocessable_entity"}),
}
"""Answers worth retrying on a refund only.

Measured on 2026-10-09: refunding an approved card order at once answered
``422 unprocessable_entity`` in 7 of 10 attempts — the order already reads
``processed`` while the asynchronous capture is still finishing — and every
one refunded within about five seconds on retry with the same key. The code
is generic, so a refund that is genuinely invalid also carries it; it then
surfaces after the last delay instead of at once, which is the price of not
failing every immediate refund.
"""

DEFAULT_ACTION_RETRY_DELAYS: Final[tuple[float, ...]] = (1.0, 2.0, 4.0)
"""Seconds to wait before each retry of a :data:`RETRYABLE_ACTION_ERROR`.

Three retries over seven seconds; the measured recoveries all happened by
about five seconds.
"""

ORDER_TOPIC: Final[str] = "order"
"""Notification topic an Orders delivery is expected to carry.

The vendored document lists ``order.created`` / ``order.updated`` as
notification actions; a live Orders delivery has not been observed here.
:meth:`MercadoPagoPixProvider.read_delivery` therefore accepts either the
topic or an ``order.``-prefixed action, and still only acts on the signed
id.
"""


def _as_optional_str(value: object) -> str | None:
    """Narrow a decoded JSON value to a non-empty string.

    Args:
        value (object): The value as decoded.

    Returns:
        str | None: The value when it is a non-empty string, else ``None``.
    """
    return value if isinstance(value, str) and value else None


def _parse_datetime(value: object) -> datetime | None:
    """Read an ISO 8601 timestamp leniently.

    Args:
        value (object): The field as decoded.

    Returns:
        datetime | None: The timestamp, or ``None`` when absent or malformed
        — a date is cosmetic, and refusing an order over it after the order
        exists would invite a retry that creates another.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _cents(source: Mapping[str, Any], key: str) -> int:
    """Read a decimal-string amount into cents, or refuse it.

    Args:
        source (Mapping[str, Any]): The object carrying the amount.
        key (str): Its key — ``total_amount``, ``amount``.

    Returns:
        int: The amount in cents.

    Raises:
        ValueError: If the amount is absent or not a whole number of cents.
            An unreadable amount is never read as zero: the settlement check
            compares amounts, and a plausible wrong one is how an order is
            released for the wrong price.
    """
    value = source.get(key)
    if value is None or isinstance(value, bool):
        raise ValueError(
            f"Mercado Pago returned no readable {key}: "
            f"{value!r} (id={source.get('id')!r})"
        )
    try:
        return to_cents(value)
    except (ValueError, TypeError, ArithmeticError) as error:
        raise ValueError(
            f"Mercado Pago returned a {key} that cannot be read as cents: "
            f"{value!r} (id={source.get('id')!r}) — {error}"
        ) from error


def _first_payment(order: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the order's first payment, or an empty mapping.

    Args:
        order (Mapping[str, Any]): The order.

    Returns:
        Mapping[str, Any]: ``transactions.payments[0]``.
    """
    transactions = order.get("transactions")
    if not isinstance(transactions, Mapping):
        return {}
    payments = transactions.get("payments")
    if isinstance(payments, list) and payments and isinstance(payments[0], Mapping):
        return payments[0]
    return {}


def _payment_method(order: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the first payment's ``payment_method``, or an empty mapping.

    Args:
        order (Mapping[str, Any]): The order.

    Returns:
        Mapping[str, Any]: The payment method object.
    """
    method = _first_payment(order).get("payment_method")
    return method if isinstance(method, Mapping) else {}


def _status(order: Mapping[str, Any]) -> PaymentStatus:
    """Read an order's state into the canonical one.

    Args:
        order (Mapping[str, Any]): The order.

    Returns:
        PaymentStatus: The mapped state; :attr:`PaymentStatus.AUTHORIZED`
        for an ``action_required`` order waiting on capture.
    """
    raw = order.get("status")
    if not isinstance(raw, str):
        return PaymentStatus.UNKNOWN
    if raw == "action_required" and (
        order.get("status_detail") == AUTHORIZED_STATUS_DETAIL
    ):
        return PaymentStatus.AUTHORIZED
    return STATUS_MAP.get(raw, PaymentStatus.UNKNOWN)


def _refunded_cents(order: Mapping[str, Any]) -> int:
    """Sum the processed refunds an order reports.

    Args:
        order (Mapping[str, Any]): The order.

    Returns:
        int: Cents refunded, ``0`` when the order lists none. Observed: an
        order refunded in two steps lists both under
        ``transactions.refunds``, each ``status: processed``.
    """
    transactions = order.get("transactions")
    refunds = transactions.get("refunds") if isinstance(transactions, Mapping) else None
    if not isinstance(refunds, list):
        return 0
    return sum(
        _cents(refund, "amount")
        for refund in refunds
        if isinstance(refund, Mapping) and refund.get("status") == "processed"
    )


def _payer(payer: PixPayer | None) -> dict[str, Any]:
    """Build the order's ``payer`` block from what the service knows.

    Args:
        payer (PixPayer | None): The canonical payer.

    Returns:
        dict[str, Any]: The block. Measured: an order without a payer
        answers ``400`` (``'$.payer' - minimum 1 properties allowed``), so an
        empty block is sent as-is and the provider says what is missing.

    Only fields the request carries are sent. The full name goes in
    ``first_name`` rather than being split, and ``identification.type`` is
    decided by digit count — 11 is a CPF, 14 a CNPJ.
    """
    block: dict[str, Any] = {}
    if payer is None:
        return block
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
    return block


def _order_id(order: Mapping[str, Any]) -> str:
    """Read an order's id, or refuse the body.

    Args:
        order (Mapping[str, Any]): The order.

    Returns:
        str: The id.

    Raises:
        ValueError: If the body carries no id.
    """
    order_id = _as_optional_str(order.get("id"))
    if order_id is None:
        raise ValueError("Mercado Pago returned an order without an id.")
    return order_id


def _to_pix_charge(order: Mapping[str, Any]) -> PixCharge:
    """Map an order onto the canonical Pix charge.

    Args:
        order (Mapping[str, Any]): The order, as decoded.

    Returns:
        PixCharge: The charge. ``raw`` is the order verbatim.

    Raises:
        ValueError: If the order has no id or no readable ``total_amount`` —
            the only fields refused, so a malformed field the adapter does
            not use cannot fail a create after the order exists.
    """
    method = _payment_method(order)
    raw_status = order.get("status")
    return PixCharge(
        provider=PROVIDER_NAME,
        provider_charge_id=_order_id(order),
        reference=_as_optional_str(order.get("external_reference")) or "",
        amount_cents=_cents(order, "total_amount"),
        currency=_as_optional_str(order.get("currency")) or "BRL",
        status=_status(order),
        provider_status=raw_status if isinstance(raw_status, str) else "",
        br_code=_as_optional_str(method.get("qr_code")),
        qr_code_base64=_as_optional_str(method.get("qr_code_base64")),
        end_to_end_id=_as_optional_str(method.get("e2e_id")),
        expires_at=_parse_datetime(_first_payment(order).get("date_of_expiration")),
        raw=dict(order),
    )


def _to_card_charge(order: Mapping[str, Any]) -> CardCharge:
    """Map an order onto the canonical card charge.

    Args:
        order (Mapping[str, Any]): The order, as decoded.

    Returns:
        CardCharge: The charge. ``raw`` is the order verbatim.

    Raises:
        ValueError: If the order has no id or no readable ``total_amount``.
    """
    method = _payment_method(order)
    raw_status = order.get("status")
    installments = method.get("installments")
    return CardCharge(
        provider=PROVIDER_NAME,
        provider_charge_id=_order_id(order),
        reference=_as_optional_str(order.get("external_reference")) or "",
        amount_cents=_cents(order, "total_amount"),
        refunded_cents=_refunded_cents(order),
        currency=_as_optional_str(order.get("currency")) or "BRL",
        status=_status(order),
        provider_status=raw_status if isinstance(raw_status, str) else "",
        status_detail=(
            _as_optional_str(_first_payment(order).get("status_detail"))
            or _as_optional_str(order.get("status_detail"))
        ),
        payment_method_id=_as_optional_str(method.get("id")),
        installments=(
            installments
            if isinstance(installments, int) and not isinstance(installments, bool)
            else None
        ),
        raw=dict(order),
    )


def _is_retryable(
    response: httpx.Response, retryable: Mapping[int, frozenset[str]]
) -> bool:
    """Tell whether an action answer means "not yet".

    Args:
        response (httpx.Response): The provider's answer.
        retryable (Mapping[int, frozenset[str]]): Error codes worth a retry,
            by HTTP status.

    Returns:
        bool: ``True`` when the status is listed and one of the answer's
        ``errors`` carries a listed code.
    """
    codes = retryable.get(response.status_code)
    if not codes:
        return False
    try:
        decoded = response.json()
    except ValueError:
        return False
    errors = decoded.get("errors") if isinstance(decoded, Mapping) else None
    return isinstance(errors, list) and any(
        isinstance(error, Mapping) and error.get("code") in codes for error in errors
    )


def _order_body(decoded: object) -> Mapping[str, Any]:
    """Narrow a decoded response to the order object.

    Args:
        decoded (object): ``response.json()``.

    Returns:
        Mapping[str, Any]: The order.

    Raises:
        ValueError: If the body is not a JSON object.
    """
    if not isinstance(decoded, Mapping):
        raise ValueError(
            f"Mercado Pago answered an order route with {type(decoded).__name__}, "
            "not a JSON object."
        )
    return decoded


@dataclass(frozen=True, slots=True)
class MercadoPagoOrderDelivery:
    """A verified notification, plus the order re-read because of it.

    Built by :func:`make_mercado_pago_webhook_delivery_dependency`.

    Attributes:
        notification (MercadoPagoWebhookEvent): The verified notification.
        order (dict[str, Any] | None): The order the notification points at,
            as the API answered on the re-read. ``None`` when the delivery is
            not about an order, its ``data.id`` is not an order id, or the
            API answered ``404`` — the dashboard's "simulate notification"
            signs a made-up id, and raising there would make the provider
            retry forever.
    """

    notification: MercadoPagoWebhookEvent
    order: dict[str, Any] | None = None


class _OrdersClient:
    """The transport both adapters share: create, read and act on an order.

    Not a wrapper for its own sake: both adapters need the same idempotency
    rule, the same 402 handling and the same delivery re-read, and keeping
    them in one place keeps the two from drifting.

    Attributes:
        provider_name (str): Always ``"mercado_pago"``.
    """

    provider_name: str = PROVIDER_NAME

    def __init__(
        self,
        http: HTTPClient,
        *,
        idempotency_key: Callable[[str], str] | None = None,
        action_retry_delays: Sequence[float] = DEFAULT_ACTION_RETRY_DELAYS,
    ) -> None:
        """Wrap a transport that already carries the access token.

        Args:
            http (HTTPClient): Built with ``base_url=DEFAULT_BASE_URL`` and
                ``Authorization: Bearer <access token>``.
            idempotency_key (Callable[[str], str] | None): Builds the
                ``X-Idempotency-Key`` for a create from the charge's
                ``reference``. The default is a fresh UUID4 per call, which
                the ``HTTPClient`` reuses across its own retries (headers are
                merged once, before the retry loop). Pass
                ``lambda reference: reference`` to make two creates for the
                same reference one order — the header's contract, not
                measured against Mercado Pago here.
            action_retry_delays (Sequence[float]): Waits, in seconds, before
                retrying a capture, cancel or refund the provider answered
                with one of :data:`RETRYABLE_ACTION_ERRORS` (or, on a refund,
                :data:`REFUND_RETRYABLE_ERRORS`). Empty disables the retry.
        """
        self._http: HTTPClient = http
        self._action_retry_delays: tuple[float, ...] = tuple(action_retry_delays)
        self._idempotency_key: Callable[[str], str] = idempotency_key or (
            lambda _reference: str(uuid.uuid4())
        )

    async def _create(self, body: dict[str, Any], reference: str) -> Mapping[str, Any]:
        """Create an order.

        Args:
            body (dict[str, Any]): The order request.
            reference (str): Fed to the idempotency key builder.

        Returns:
            Mapping[str, Any]: The order — for a ``402`` decline, the order
            under ``data``.

        Raises:
            httpx.HTTPStatusError: For any other non-2xx answer.
            ValueError: If the body is not an order.
        """
        response = await self._http.request(
            "POST",
            ORDERS_PATH,
            headers={"X-Idempotency-Key": self._idempotency_key(reference)},
            json=body,
        )
        if response.status_code == HTTPStatus.PAYMENT_REQUIRED:
            decoded = response.json()
            data = decoded.get("data") if isinstance(decoded, Mapping) else None
            if isinstance(data, Mapping):
                return data
        response.raise_for_status()
        return _order_body(response.json())

    async def _get(self, order_id: str) -> Mapping[str, Any]:
        """Read an order.

        Args:
            order_id (str): The order id.

        Returns:
            Mapping[str, Any]: The order.

        Raises:
            httpx.HTTPStatusError: For any non-2xx answer.
        """
        response = await self._http.request("GET", f"{ORDERS_PATH}/{order_id}")
        response.raise_for_status()
        return _order_body(response.json())

    async def _action(
        self,
        order_id: str,
        action: str,
        body: dict[str, Any] | None = None,
        *,
        retryable: Mapping[int, frozenset[str]] = RETRYABLE_ACTION_ERRORS,
    ) -> Mapping[str, Any]:
        """Post an action (``cancel``, ``capture``, ``refund``) to an order.

        Args:
            order_id (str): The order id.
            action (str): The action path segment.
            body (dict[str, Any] | None): The request body, if any.
            retryable (Mapping[int, frozenset[str]]): Status and error codes
                that mean "not yet" for this action.

        Returns:
            Mapping[str, Any]: What the provider answered.

        Raises:
            httpx.HTTPStatusError: For any non-2xx answer, including a
                retryable one still answered after the last delay.

        A retryable answer is retried after each delay in
        ``action_retry_delays``, with the **same** idempotency key, so a
        retry cannot apply the action twice. Any other error raises at once.
        """
        headers = {"X-Idempotency-Key": str(uuid.uuid4())}
        delays = iter(self._action_retry_delays)
        while True:
            response = await self._http.request(
                "POST",
                f"{ORDERS_PATH}/{order_id}/{action}",
                headers=headers,
                json=body,
            )
            delay = next(delays, None)
            if delay is None or not _is_retryable(response, retryable):
                break
            await asyncio.sleep(delay)
        response.raise_for_status()
        return _order_body(response.json())

    async def read_delivery(
        self, notification: MercadoPagoWebhookEvent
    ) -> MercadoPagoOrderDelivery:
        """Re-read the order a verified notification points at.

        Args:
            notification (MercadoPagoWebhookEvent): The verified notification.
                Its ``data_id`` is the one value the signature covers, so it
                is the id used for the read.

        Returns:
            MercadoPagoOrderDelivery: The notification, with the order when
            the delivery is about one and the API knows it.

        Raises:
            httpx.HTTPStatusError: If the read fails with anything but
                ``404``, so the route answers non-2xx and the provider
                retries a question it did not answer.
        """
        action = notification.payload.get("action")
        about_order = notification.topic == ORDER_TOPIC or (
            isinstance(action, str) and action.startswith(f"{ORDER_TOPIC}.")
        )
        data_id = notification.data_id
        if not about_order or not ORDER_ID_PATTERN.match(data_id):
            return MercadoPagoOrderDelivery(notification=notification)
        try:
            order = await self._get(data_id)
        except httpx.HTTPStatusError as error:
            if error.response.status_code == HTTPStatus.NOT_FOUND:
                return MercadoPagoOrderDelivery(notification=notification)
            raise
        return MercadoPagoOrderDelivery(notification=notification, order=dict(order))


class MercadoPagoPixProvider(_OrdersClient):
    """Mercado Pago Pix as a :class:`~...payment.base.PixProvider`, over Orders.

    Attributes:
        provider_name (str): Always ``"mercado_pago"``.
    """

    async def create_pix_charge(self, request: PixChargeRequest) -> PixCharge:
        """Create a Pix order.

        ``expires_in`` becomes ``expiration_time`` as an ISO 8601 duration in
        whole seconds (``PT1800S``), which the sandbox accepted. Without it,
        the order expired 24 hours after creation.

        Args:
            request (PixChargeRequest): What to charge, and for whom.

        Returns:
            PixCharge: The created charge, ``PENDING``, with ``br_code`` and
            ``qr_code_base64``.

        Raises:
            httpx.HTTPStatusError: For any non-2xx answer — including ``400``
                for an order without a payer.
            ValueError: If the answer is not an order with an id and amount.
        """
        amount = str(from_cents(request.amount_cents))
        payment: dict[str, Any] = {
            "amount": amount,
            "payment_method": {"id": "pix", "type": "bank_transfer"},
        }
        if request.expires_in is not None:
            payment["expiration_time"] = f"PT{int(request.expires_in.total_seconds())}S"
        body: dict[str, Any] = {
            "type": "online",
            "processing_mode": "automatic",
            "total_amount": amount,
            "external_reference": request.reference,
            "payer": _payer(request.payer),
            "transactions": {"payments": [payment]},
        }
        if request.description is not None:
            body["description"] = request.description
        return _to_pix_charge(await self._create(body, request.reference))

    async def get_pix_charge(self, charge_id: str) -> PixCharge:
        """Read a Pix order back.

        Args:
            charge_id (str): :attr:`PixCharge.provider_charge_id`, the order id.

        Returns:
            PixCharge: The charge as Mercado Pago reports it now.
        """
        return _to_pix_charge(await self._get(charge_id))

    async def cancel_pix_charge(self, charge_id: str) -> PixCharge:
        """Cancel an unpaid Pix order (``POST /v1/orders/{id}/cancel``).

        Args:
            charge_id (str): :attr:`PixCharge.provider_charge_id`.

        Returns:
            PixCharge: The order after cancellation, ``CANCELLED``.
        """
        return _to_pix_charge(await self._action(charge_id, "cancel"))

    def parse_webhook(self, event: Any) -> PixPaymentEvent:
        """Turn a verified, re-read delivery into a canonical Pix event.

        Args:
            event (Any): A :class:`MercadoPagoOrderDelivery`.

        Returns:
            PixPaymentEvent: The event, its type read off the re-read order
            (:data:`STATUS_EVENT_MAP`). ``provider_event_name`` and ``raw``
            come from the unsigned body: log them, do not decide on them.

        Raises:
            TypeError: If handed anything else — in particular the bare
                :class:`MercadoPagoWebhookEvent`, which carries no state and
                could only become ``UNKNOWN``, leaving a service that settles
                on ``CHARGE_PAID`` silently stuck.
        """
        if not isinstance(event, MercadoPagoOrderDelivery):
            hint = (
                " Use make_mercado_pago_webhook_delivery_dependency, which "
                "re-reads the order the notification points at."
                if isinstance(event, MercadoPagoWebhookEvent)
                else ""
            )
            raise TypeError(
                "parse_webhook expects a MercadoPagoOrderDelivery, "
                f"got {type(event).__name__}.{hint}"
            )
        notification = event.notification
        action = notification.payload.get("action")
        name = action if isinstance(action, str) and action else notification.topic
        charge: PixCharge | None = None
        event_type = PixEventType.UNKNOWN
        if event.order is not None and _payment_method(event.order).get("id") == "pix":
            charge = _to_pix_charge(event.order)
            event_type = STATUS_EVENT_MAP.get(charge.status, PixEventType.UNKNOWN)
        return PixPaymentEvent(
            provider=PROVIDER_NAME,
            type=event_type,
            provider_event_name=name,
            charge=charge,
            raw=notification.payload,
        )


class MercadoPagoCardProvider(_OrdersClient):
    """Mercado Pago cards as a :class:`~...payment.base.CardProvider`, over Orders.

    Attributes:
        provider_name (str): Always ``"mercado_pago"``.
    """

    async def create_card_charge(self, request: CardChargeRequest) -> CardCharge:
        """Charge, or only authorize, a tokenized card.

        ``capture=False`` sends ``capture_mode: manual``; the order comes back
        ``action_required`` / ``waiting_capture``
        (:attr:`PaymentStatus.AUTHORIZED`). Otherwise the provider's default
        applies, observed as ``automatic_async``.

        Args:
            request (CardChargeRequest): What to charge.

        Returns:
            CardCharge: ``PAID``, ``AUTHORIZED``, or ``FAILED`` with the
            reason in ``status_detail`` — a decline answers HTTP 402 and is
            returned, not raised.

        Raises:
            httpx.HTTPStatusError: For a non-2xx answer other than a decline
                — ``400 invalid_transaction_amount`` for an installment count
                the card does not offer at this amount, among others.
            ValueError: If the answer is not an order.
        """
        amount = str(from_cents(request.amount_cents))
        body: dict[str, Any] = {
            "type": "online",
            "processing_mode": "automatic",
            "total_amount": amount,
            "external_reference": request.reference,
            "payer": _payer(request.payer),
            "transactions": {
                "payments": [
                    {
                        "amount": amount,
                        "payment_method": {
                            "id": request.payment_method_id,
                            "type": "credit_card",
                            "token": request.card_token,
                            "installments": request.installments,
                        },
                    }
                ]
            },
        }
        if not request.capture:
            body["capture_mode"] = "manual"
        if request.description is not None:
            body["description"] = request.description
        return _to_card_charge(await self._create(body, request.reference))

    async def get_card_charge(self, charge_id: str) -> CardCharge:
        """Read a card order back.

        Args:
            charge_id (str): :attr:`CardCharge.provider_charge_id`.

        Returns:
            CardCharge: The charge as Mercado Pago reports it now.
        """
        return _to_card_charge(await self._get(charge_id))

    async def capture_card_charge(self, charge_id: str) -> CardCharge:
        """Capture an authorization, then read the order back.

        ``POST /v1/orders/{id}/capture`` answers only the order's id, status,
        status detail and transactions — measured, no ``total_amount`` — so
        the order is read again to return a whole charge. The capture has
        already happened when that read runs.

        Args:
            charge_id (str): :attr:`CardCharge.provider_charge_id`.

        Returns:
            CardCharge: The order after capture, ``PAID``.
        """
        await self._action(charge_id, "capture")
        return _to_card_charge(await self._get(charge_id))

    async def cancel_card_charge(self, charge_id: str) -> CardCharge:
        """Release an authorization (``POST /v1/orders/{id}/cancel``).

        Args:
            charge_id (str): :attr:`CardCharge.provider_charge_id`.

        Returns:
            CardCharge: The order after cancellation, ``CANCELLED``.
        """
        return _to_card_charge(await self._action(charge_id, "cancel"))

    async def refund_card_charge(
        self, charge_id: str, amount_cents: int | None = None
    ) -> CardCharge:
        """Refund a card order in full or in part, then read it back.

        A partial refund addresses the order's payment (``PAY…``) with an
        amount; a full one posts no body. The refund answer carries only the
        order's id, status and refunds — measured, no amount — so the order is
        read again to return a whole charge. The refund has already happened when
        that read runs.

        Args:
            charge_id (str): :attr:`CardCharge.provider_charge_id`.
            amount_cents (int | None): How much to refund; ``None`` refunds
                what is left.

        Returns:
            CardCharge: The order after the refund. Observed: ``PAID`` with
            ``status_detail`` ``partially_refunded`` after a partial one,
            ``REFUNDED`` after the rest.

        Raises:
            httpx.HTTPStatusError: For any non-2xx answer.
            ValueError: If a partial refund is asked of an order with no
                payment to address.
        """
        body: dict[str, Any] | None = None
        if amount_cents is not None:
            payment_id = _as_optional_str(
                _first_payment(await self._get(charge_id)).get("id")
            )
            if payment_id is None:
                raise ValueError(f"Order {charge_id!r} has no payment to refund.")
            body = {
                "transactions": [
                    {"id": payment_id, "amount": str(from_cents(amount_cents))}
                ]
            }
        await self._action(
            charge_id,
            "refund",
            body,
            retryable={**RETRYABLE_ACTION_ERRORS, **REFUND_RETRYABLE_ERRORS},
        )
        return _to_card_charge(await self._get(charge_id))

    @staticmethod
    def charge_from_delivery(delivery: MercadoPagoOrderDelivery) -> CardCharge | None:
        """Read the card charge a re-read delivery carries, if any.

        Args:
            delivery (MercadoPagoOrderDelivery): From
                :func:`make_mercado_pago_webhook_delivery_dependency`.

        Returns:
            CardCharge | None: The charge when the re-read order is a card
            order, else ``None``.
        """
        order = delivery.order
        if order is None or _payment_method(order).get("type") not in {
            "credit_card",
            "debit_card",
        }:
            return None
        return _to_card_charge(order)


def make_mercado_pago_webhook_delivery_dependency(
    secret: str,
    client: MercadoPagoPixProvider | MercadoPagoCardProvider,
    *,
    tolerance_seconds: float | None = None,
    versions: Sequence[str] = DEFAULT_SIGNATURE_VERSIONS,
) -> Callable[..., Coroutine[Any, Any, MercadoPagoOrderDelivery]]:
    """Build a FastAPI dependency yielding a verified, re-read delivery.

    Args:
        secret (str): The webhook secret from the Mercado Pago dashboard. An
            empty secret rejects every delivery.
        client (MercadoPagoPixProvider | MercadoPagoCardProvider): Either
            adapter; used to re-read the order.
        tolerance_seconds (float | None): Maximum drift between the
            signature's ``ts`` and the clock.
        versions (Sequence[str]): Hash versions to accept, in preference
            order.

    Returns:
        Callable[..., Coroutine[Any, Any, MercadoPagoOrderDelivery]]: The
        dependency. It refuses an unsigned or badly signed delivery with 401
        before the route runs, and re-reads the order by the signed
        ``data.id``.
    """
    verified = make_mercado_pago_webhook_dependency(
        secret,
        tolerance_seconds=tolerance_seconds,
        versions=versions,
    )

    async def dependency(
        notification: MercadoPagoWebhookEvent = Depends(verified),
    ) -> MercadoPagoOrderDelivery:
        """Re-read the order behind a verified notification.

        Args:
            notification (MercadoPagoWebhookEvent): The verified notification.

        Returns:
            MercadoPagoOrderDelivery: The delivery the adapters read.
        """
        return await client.read_delivery(notification)

    return dependency


__all__: list[str] = [
    "AUTHORIZED_STATUS_DETAIL",
    "DEFAULT_ACTION_RETRY_DELAYS",
    "ORDERS_PATH",
    "ORDER_ID_PATTERN",
    "ORDER_TOPIC",
    "PROVIDER_NAME",
    "REFUND_RETRYABLE_ERRORS",
    "RETRYABLE_ACTION_ERRORS",
    "STATUS_EVENT_MAP",
    "STATUS_MAP",
    "MercadoPagoCardProvider",
    "MercadoPagoOrderDelivery",
    "MercadoPagoPixProvider",
    "make_mercado_pago_webhook_delivery_dependency",
]
