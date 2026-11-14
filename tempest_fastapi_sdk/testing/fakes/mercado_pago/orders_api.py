"""Mercado Pago's ``/v1/orders``, with state, as an ``httpx`` handler."""

from __future__ import annotations

import copy
import json
import re
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx

from tempest_fastapi_sdk.integrations.payment.mercado_pago.environment import (
    DEFAULT_BASE_URL,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.money import (
    from_cents,
    to_cents,
)
from tempest_fastapi_sdk.testing.fakes.mercado_pago.delivery import (
    FAKE_APPLICATION_ID,
    FAKE_USER_ID,
    FAKE_WEBHOOK_SECRET,
    MercadoPagoDelivery,
    build_mercado_pago_delivery,
)
from tempest_fastapi_sdk.utils.http_client import HTTPClient
from tempest_fastapi_sdk.utils.retry import RetryPolicy

DEFAULT_PIX_EXPIRATION: Final[timedelta] = timedelta(hours=24)
"""How long a Pix order lives without ``expiration_time``.

Observed in the sandbox (``pix_get_paid.json``): ``date_of_expiration`` is
the creation plus 24 hours when the create sends no ``expiration_time``.
"""

_DURATION: Final[re.Pattern[str]] = re.compile(
    r"^PT(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?$"
)
"""ISO 8601 durations, as a Pix ``expiration_time`` was accepted (``PT60S``)."""

_FAKE_QR_PNG_BASE64: Final[str] = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA"
    "60e6kgAAAABJRU5ErkJggg=="
)
"""A one-pixel PNG standing in for the QR image (the sandbox fixture's value)."""

TRANSITIONS: Final[frozenset[str]] = frozenset(
    {"pay", "expire", "fail", "cancel", "capture", "refund"}
)
"""Names :meth:`FakeMercadoPagoOrdersAPI.apply` accepts."""


class _APIError(Exception):
    """An error answer the simulated API gives.

    Attributes:
        status (int): The HTTP status.
        code (str): ``errors[0].code``.
        message (str): ``errors[0].message``.
    """

    def __init__(self, status: int, code: str, message: str) -> None:
        """Store the answer.

        Args:
            status (int): The HTTP status.
            code (str): The error code.
            message (str): The error message.
        """
        super().__init__(f"{status} {code}: {message}")
        self.status: int = status
        self.code: str = code
        self.message: str = message

    def response(self) -> httpx.Response:
        """Render the answer as Mercado Pago shapes errors.

        Returns:
            httpx.Response: ``{"errors": [{"code", "message"}]}`` with the
            status.
        """
        return httpx.Response(
            self.status,
            json={"errors": [{"code": self.code, "message": self.message}]},
        )


def _iso(moment: datetime) -> str:
    """Write a timestamp as the API writes one.

    Args:
        moment (datetime): An aware datetime.

    Returns:
        str: ISO 8601 with milliseconds and ``Z``.
    """
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _duration(value: object) -> timedelta:
    """Read a Pix ``expiration_time``.

    Args:
        value (object): The field as sent, or ``None``.

    Returns:
        timedelta: The duration; :data:`DEFAULT_PIX_EXPIRATION` when absent.

    Raises:
        _APIError: ``400`` when present and not a ``PT…`` duration (assumed
            answer; the sandbox was only sent valid ones).
    """
    if value is None:
        return DEFAULT_PIX_EXPIRATION
    found = _DURATION.match(value) if isinstance(value, str) else None
    if found is None or not any(found.groupdict().values()):
        raise _APIError(400, "invalid_expiration_time", f"{value!r}")
    parts = {name: int(amount or 0) for name, amount in found.groupdict().items()}
    return timedelta(hours=parts["h"], minutes=parts["m"], seconds=parts["s"])


class FakeMercadoPagoOrdersAPI:
    """Mercado Pago's ``/v1/orders`` with state, as an ``httpx`` handler.

    Wire it under the real adapter and nothing else changes::

        api = FakeMercadoPagoOrdersAPI()
        provider = MercadoPagoPixProvider(api.http_client())

    Routes answered (anything else is ``404``):

    - ``POST /v1/orders`` — Pix (``payment_method.id == "pix"``) or card.
      Idempotent on ``X-Idempotency-Key``: the same key with the same body
      replays the first answer, with another body answers ``409
      idempotency_key_already_used``.
    - ``GET /v1/orders/{id}`` — ``404`` for an id it never created.
    - ``POST /v1/orders/{id}/cancel`` — an ``action_required`` order.
    - ``POST /v1/orders/{id}/capture`` — a ``waiting_capture`` card order.
    - ``POST /v1/orders/{id}/refund`` — a ``processed`` order, in full (no
      body) or in part (``transactions[0].amount``), until nothing is left.

    The provider-side moves are methods: :meth:`pay`, :meth:`expire`,
    :meth:`fail`, plus :meth:`cancel`, :meth:`capture` and :meth:`refund`
    for driving those without the adapter. Each returns the signed
    :class:`MercadoPagoDelivery` announcing the new state.

    Attributes:
        webhook_secret (str): The secret deliveries are signed with.
        calls (list[tuple[str, str]]): ``(method, path)`` of every request
            answered, in order.
    """

    def __init__(
        self,
        *,
        webhook_secret: str = FAKE_WEBHOOK_SECRET,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Start with no orders.

        Args:
            webhook_secret (str): Signs every delivery the transitions
                return. Defaults to :data:`FAKE_WEBHOOK_SECRET`.
            clock (Callable[[], datetime] | None): Source of ``created_date``
                and ``date_of_expiration``. Defaults to the current UTC time.
        """
        self.webhook_secret: str = webhook_secret
        self.calls: list[tuple[str, str]] = []
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._orders: dict[str, dict[str, Any]] = {}
        self._replies: dict[str, tuple[str, bytes, int, Any]] = {}
        self._declines: deque[str] = deque()
        self._lose_next_answer: bool = False
        self._instance: str = uuid.uuid4().hex[:8].upper()
        self._minted: int = 0

    def _mint(self, prefix: str) -> str:
        """Mint an id shaped like the provider's (``ORD`` + 26 characters).

        Args:
            prefix (str): ``ORD``, ``PAY`` or ``REF``.

        Returns:
            str: ``<prefix>FAKE``, eight hex digits drawn per instance and a
            zero-padded counter — 29 characters, alphanumeric so
            ``ORDER_ID_PATTERN`` accepts it. The per-instance part keeps two
            simulators from minting the same id, so a delivery built by one
            is unknown to the other, as a made-up id is to Mercado Pago.
        """
        self._minted += 1
        return f"{prefix}FAKE{self._instance}{self._minted:014d}"

    @property
    def order_ids(self) -> list[str]:
        """Every order id created, oldest first.

        Returns:
            list[str]: The ids.
        """
        return list(self._orders)

    def order(self, order_id: str) -> dict[str, Any]:
        """Read an order as ``GET /v1/orders/{id}`` would answer it.

        Args:
            order_id (str): The order id.

        Returns:
            dict[str, Any]: A copy; changing it changes nothing here.

        Raises:
            KeyError: If no such order was created.
        """
        return copy.deepcopy(self._orders[order_id])

    def decline_next_card(self, status_detail: str = "rejected_by_issuer") -> None:
        """Make the next card create answer ``402`` with a failed order.

        Args:
            status_detail (str): The payment's ``status_detail``.
                ``rejected_by_issuer`` is what the sandbox answered for the
                ``OTHE`` holder.
        """
        self._declines.append(status_detail)

    def lose_next_answer(self) -> None:
        """Apply the next ``POST``, then raise ``httpx.ReadTimeout``.

        The failure a retry under the same idempotency key has to survive.
        Works on the in-process transport; through ``make_fake_orders_app`` the
        exception becomes a ``500`` instead.
        """
        self._lose_next_answer = True

    def transport(self) -> httpx.MockTransport:
        """Wrap this handler in an ``httpx`` transport.

        Returns:
            httpx.MockTransport: Pass it as ``transport=`` to an
            ``HTTPClient`` or an ``httpx.AsyncClient``.
        """
        return httpx.MockTransport(self)

    def http_client(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        retry_policy: RetryPolicy | None = None,
    ) -> HTTPClient:
        """Build the ``HTTPClient`` the Mercado Pago adapters take.

        Args:
            base_url (str): Any host; requests never leave the process.
            retry_policy (RetryPolicy | None): Forwarded to the client.
                ``RetryPolicy(max_attempts=1)`` surfaces a
                :meth:`lose_next_answer` to the caller instead of retrying it.

        Returns:
            HTTPClient: The client, over :meth:`transport`.
        """
        return HTTPClient(
            base_url=base_url, transport=self.transport(), retry_policy=retry_policy
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one request.

        Args:
            request (httpx.Request): The request.

        Returns:
            httpx.Response: The answer.

        Raises:
            httpx.ReadTimeout: After applying a ``POST``, when
                :meth:`lose_next_answer` asked for it.
        """
        path = request.url.path
        self.calls.append((request.method, path))
        segments = [part for part in path.split("/") if part]
        if segments[:2] != ["v1", "orders"] or len(segments) > 4:
            return _APIError(404, "not_found", f"No route for {path}").response()
        if request.method == "GET" and len(segments) == 3:
            stored = self._orders.get(segments[2])
            if stored is None:
                return _APIError(
                    404, "order_not_found", f"Order {segments[2]} not found"
                ).response()
            return httpx.Response(200, json=stored)
        if request.method != "POST" or len(segments) == 3:
            return _APIError(405, "method_not_allowed", path).response()
        key = request.headers.get("X-Idempotency-Key", "")
        route = f"{request.method} {path}"
        if key and key in self._replies:
            stored_route, content, status, answer = self._replies[key]
            if stored_route != route or content != request.content:
                return _APIError(
                    409,
                    "idempotency_key_already_used",
                    "The idempotency key was already used with another request.",
                ).response()
            return httpx.Response(status, json=answer)
        try:
            status, answer = self._apply(segments, request.content)
        except _APIError as error:
            return error.response()
        if key:
            self._replies[key] = (route, request.content, status, answer)
        if self._lose_next_answer:
            self._lose_next_answer = False
            raise httpx.ReadTimeout("answer lost", request=request)
        return httpx.Response(status, json=answer)

    def _apply(self, segments: list[str], content: bytes) -> tuple[int, Any]:
        """Run a ``POST`` route.

        Args:
            segments (list[str]): The path split on ``/``.
            content (bytes): The request body.

        Returns:
            tuple[int, Any]: The status and the JSON answer.

        Raises:
            _APIError: For any answer that is an error.
        """
        try:
            body: Any = json.loads(content) if content else None
        except ValueError as error:
            raise _APIError(400, "invalid_json", str(error)) from error
        if len(segments) == 2:
            return self._create(body)
        order = self._stored(segments[2])
        action = segments[3]
        if action == "cancel":
            self._cancel(order)
            return 200, copy.deepcopy(order)
        if action == "capture":
            self._capture(order)
            payment = order["transactions"]["payments"][0]
            return 200, {
                "id": order["id"],
                "status": order["status"],
                "status_detail": order["status_detail"],
                "transactions": {
                    "payments": [
                        {
                            key: payment[key]
                            for key in (
                                "id",
                                "amount",
                                "paid_amount",
                                "status",
                                "status_detail",
                                "reference_id",
                            )
                            if key in payment
                        }
                    ]
                },
            }
        if action == "refund":
            self._refund(order, self._refund_amount(order, body))
            return 201, {
                "id": order["id"],
                "status": order["status"],
                "status_detail": order["status_detail"],
                "transactions": {
                    "refunds": copy.deepcopy(order["transactions"]["refunds"])
                },
            }
        raise _APIError(404, "not_found", f"No order action {action!r}")

    def _stored(self, order_id: str) -> dict[str, Any]:
        """Find an order or answer ``404``.

        Args:
            order_id (str): The order id.

        Returns:
            dict[str, Any]: The stored order, mutable.

        Raises:
            _APIError: ``404`` for an id never created.
        """
        order = self._orders.get(order_id)
        if order is None:
            raise _APIError(404, "order_not_found", f"Order {order_id} not found")
        return order

    def _create(self, body: Any) -> tuple[int, Any]:
        """Create an order from a request body.

        Args:
            body (Any): The decoded request.

        Returns:
            tuple[int, Any]: ``201`` and the order, or ``402`` and the failed
            order under ``data`` for a queued card decline.

        Raises:
            _APIError: ``400`` for a body without a payer (measured message)
                or without a readable payment (assumed answer).
        """
        if not isinstance(body, Mapping):
            raise _APIError(400, "invalid_request", "The body must be an object.")
        payer = body.get("payer")
        if not isinstance(payer, Mapping) or not payer:
            raise _APIError(
                400,
                "required_properties",
                "'$.payer' - minimum 1 properties allowed, but found 0 properties",
            )
        transactions = body.get("transactions")
        payments = (
            transactions.get("payments") if isinstance(transactions, Mapping) else None
        )
        if not isinstance(payments, list) or not payments:
            raise _APIError(400, "invalid_request", "No transactions.payments[0].")
        sent = payments[0]
        method = sent.get("payment_method") if isinstance(sent, Mapping) else None
        if not isinstance(method, Mapping) or not method.get("id"):
            raise _APIError(400, "invalid_request", "No payment_method.id.")
        try:
            total_cents = to_cents(body["total_amount"])
            amount = str(from_cents(to_cents(sent["amount"])))
        except (KeyError, ValueError, TypeError, ArithmeticError) as error:
            raise _APIError(
                400, "invalid_request", f"Unreadable amount: {error}"
            ) from error
        now = self._clock()
        payment: dict[str, Any] = {
            "id": self._mint("PAY"),
            "amount": amount,
            "reference_id": "0000000000",
        }
        order: dict[str, Any] = {
            "id": self._mint("ORD"),
            "type": body.get("type", "online"),
            "processing_mode": body.get("processing_mode", "automatic"),
            "external_reference": body.get("external_reference", ""),
            "total_amount": str(from_cents(total_cents)),
            "total_paid_amount": str(from_cents(total_cents)),
            "country_code": "BRA",
            "user_id": FAKE_USER_ID,
            "capture_mode": body.get("capture_mode", "automatic_async"),
            "currency": "BRL",
            "created_date": _iso(now),
            "last_updated_date": _iso(now),
            "integration_data": {"application_id": FAKE_APPLICATION_ID},
            "transactions": {"payments": [payment]},
        }
        if "description" in body:
            order["description"] = body["description"]
        status = 201
        if method["id"] == "pix":
            expiration = sent.get("expiration_time")
            deadline = now + _duration(expiration)
            if expiration is not None:
                payment["expiration_time"] = expiration
            payment["date_of_expiration"] = deadline.isoformat(timespec="milliseconds")
            payment["payment_method"] = {
                "id": "pix",
                "type": "bank_transfer",
                "ticket_url": f"https://fake.mercadopago.invalid/ticket/{order['id']}",
                "qr_code": f"00020126580014br.gov.bcb.pix0136{order['id']}6304FAKE",
                "qr_code_base64": _FAKE_QR_PNG_BASE64,
            }
            self._set(order, "action_required", "waiting_transfer")
        else:
            installments = method.get("installments", 1)
            count = (
                installments
                if isinstance(installments, int) and installments > 0
                else 1
            )
            payment["paid_amount"] = amount
            payment["payment_method"] = {
                "id": method["id"],
                "type": method.get("type", "credit_card"),
                "token": method.get("token", ""),
                "installments": count,
                "installment_amount": str(from_cents(to_cents(amount) // count)),
            }
            if self._declines:
                self._set(
                    order, "failed", "failed", payment_detail=self._declines.popleft()
                )
                status = 402
            elif order["capture_mode"] == "manual":
                self._set(order, "action_required", "waiting_capture")
            else:
                self._set(order, "processed", "accredited")
        self._orders[order["id"]] = order
        if status == 402:
            return 402, {
                "errors": [
                    {
                        "code": "failed",
                        "message": "The following transactions failed",
                        "details": [f"{payment['id']}: {payment['status_detail']}"],
                    }
                ],
                "data": copy.deepcopy(order),
            }
        return status, copy.deepcopy(order)

    def _set(
        self,
        order: dict[str, Any],
        status: str,
        detail: str,
        *,
        payment_status: str | None = None,
        payment_detail: str | None = None,
    ) -> None:
        """Move an order and its payment to a state.

        Args:
            order (dict[str, Any]): The stored order.
            status (str): The order's ``status``.
            detail (str): The order's ``status_detail``.
            payment_status (str | None): The payment's ``status``; the
                order's when ``None``.
            payment_detail (str | None): The payment's ``status_detail``;
                the order's when ``None``.
        """
        order["status"] = status
        order["status_detail"] = detail
        order["last_updated_date"] = _iso(self._clock())
        payment = order["transactions"]["payments"][0]
        payment["status"] = payment_status or status
        payment["status_detail"] = payment_detail or detail

    @staticmethod
    def _drop_qr(order: dict[str, Any]) -> None:
        """Remove the QR fields, as a paid or expired Pix order reads.

        Args:
            order (dict[str, Any]): The stored order.
        """
        method = order["transactions"]["payments"][0]["payment_method"]
        for key in ("ticket_url", "qr_code", "qr_code_base64"):
            method.pop(key, None)

    @staticmethod
    def _is_pix(order: Mapping[str, Any]) -> bool:
        """Tell a Pix order from a card one.

        Args:
            order (Mapping[str, Any]): The order.

        Returns:
            bool: Whether its payment method is ``pix``.
        """
        return bool(
            order["transactions"]["payments"][0]["payment_method"]["id"] == "pix"
        )

    def _cancel(self, order: dict[str, Any]) -> None:
        """Cancel an order still waiting on someone.

        Args:
            order (dict[str, Any]): The stored order.

        Raises:
            _APIError: ``409 cannot_cancel_order`` outside ``action_required``
                (assumed code; the sandbox was not asked).
        """
        if order["status"] != "action_required":
            raise _APIError(
                409,
                "cannot_cancel_order",
                f"The order cannot be canceled in status {order['status']!r}.",
            )
        self._set(order, "canceled", "canceled", payment_detail="canceled_transaction")
        if self._is_pix(order):
            self._drop_qr(order)

    def _capture(self, order: dict[str, Any]) -> None:
        """Capture a card authorization.

        Args:
            order (dict[str, Any]): The stored order.

        Raises:
            _APIError: ``409 cannot_capture_order`` unless the order waits on
                capture (assumed code).
        """
        if order["status_detail"] != "waiting_capture":
            raise _APIError(
                409,
                "cannot_capture_order",
                f"The order cannot be captured in status {order['status']!r}.",
            )
        self._set(order, "processed", "accredited")

    @staticmethod
    def _refunded(order: Mapping[str, Any]) -> int:
        """Sum the cents already refunded.

        Args:
            order (Mapping[str, Any]): The order.

        Returns:
            int: Cents refunded.
        """
        return sum(
            to_cents(refund["amount"])
            for refund in order["transactions"].get("refunds", [])
        )

    def _refund_amount(self, order: Mapping[str, Any], body: Any) -> int | None:
        """Read the amount a refund request asks for.

        Args:
            order (Mapping[str, Any]): The order.
            body (Any): The decoded request; ``None`` for a full refund.

        Returns:
            int | None: Cents, or ``None`` for what is left.

        Raises:
            _APIError: ``400`` for a body that names another payment or no
                amount (assumed answer).
        """
        if body is None:
            return None
        entries = body.get("transactions") if isinstance(body, Mapping) else None
        entry = entries[0] if isinstance(entries, list) and entries else None
        payment_id = order["transactions"]["payments"][0]["id"]
        if not isinstance(entry, Mapping) or entry.get("id") != payment_id:
            raise _APIError(
                400, "invalid_request", "transactions[0].id is not the payment."
            )
        try:
            return to_cents(entry["amount"])
        except (KeyError, ValueError, TypeError, ArithmeticError) as error:
            raise _APIError(
                400, "invalid_request", f"Unreadable amount: {error}"
            ) from error

    def _refund(self, order: dict[str, Any], amount_cents: int | None) -> None:
        """Refund a processed order in full or in part.

        Args:
            order (dict[str, Any]): The stored order.
            amount_cents (int | None): Cents to refund; ``None`` for what is
                left.

        Raises:
            _APIError: ``409 cannot_refund_order`` outside ``processed``
                (measured, message included); ``400 invalid_refund_amount``
                for an amount that is not positive or exceeds what is left
                (assumed code).
        """
        if order["status"] != "processed":
            raise _APIError(
                409,
                "cannot_refund_order",
                f"the order cannot be refunded because the current status, "
                f"'{order['status']}', doesn't allow refunds. The order must be "
                "in 'processed' status to be refunded.",
            )
        total = to_cents(order["total_amount"])
        left = total - self._refunded(order)
        amount = left if amount_cents is None else amount_cents
        if amount <= 0 or amount > left:
            raise _APIError(
                400,
                "invalid_refund_amount",
                f"Cannot refund {amount} cents; {left} left.",
            )
        payment = order["transactions"]["payments"][0]
        refunds: list[dict[str, Any]] = order["transactions"].setdefault("refunds", [])
        refunds.append(
            {
                "id": self._mint("REF"),
                "transaction_id": payment["id"],
                "reference_id": "0000000000",
                "amount": str(from_cents(amount)),
                "status": "processed",
            }
        )
        refunded = self._refunded(order)
        payment["refunded_amount"] = str(from_cents(refunded))
        if refunded == total:
            self._set(order, "refunded", "refunded")
        else:
            self._set(order, "processed", "partially_refunded")

    def delivery(
        self,
        order_id: str,
        *,
        action: str | None = None,
        request_id: str | None = None,
        ts: int | None = None,
    ) -> MercadoPagoDelivery:
        """Build the signed delivery about an order's current state.

        Args:
            order_id (str): The order id.
            action (str | None): The body's ``action``; :func:`order_action`
                when ``None``.
            request_id (str | None): The ``x-request-id``; a UUID4 when
                ``None``.
            ts (int | None): The signature's timestamp in seconds; now when
                ``None``.

        Returns:
            MercadoPagoDelivery: Signed with :attr:`webhook_secret`.

        Raises:
            KeyError: If no such order was created.
        """
        return build_mercado_pago_delivery(
            self._orders[order_id],
            secret=self.webhook_secret,
            action=action,
            request_id=request_id,
            ts=ts,
        )

    def _transition(
        self, order_id: str, move: Callable[[dict[str, Any]], None]
    ) -> MercadoPagoDelivery:
        """Apply a provider-side move and announce it.

        Args:
            order_id (str): The order id.
            move (Callable[[dict[str, Any]], None]): Mutates the stored order.

        Returns:
            MercadoPagoDelivery: The delivery about the new state.

        Raises:
            KeyError: If no such order was created.
            ValueError: If the order is in a state the move does not apply
                to.
        """
        order = self._orders[order_id]
        try:
            move(order)
        except _APIError as error:
            raise ValueError(str(error)) from error
        return self.delivery(order_id)

    def pay(self, order_id: str) -> MercadoPagoDelivery:
        """Mark a pending Pix order paid, as the payer's bank would.

        The order becomes ``processed`` / ``accredited``, its payment gains
        ``paid_amount`` and an ``e2e_id``, and the QR fields go — the shape
        of a paid order read from the sandbox (``pix_get_paid.json``).

        Args:
            order_id (str): A Pix order in ``action_required`` /
                ``waiting_transfer``.

        Returns:
            MercadoPagoDelivery: ``order.processed`` (not observed live; see
            :func:`order_action`).

        Raises:
            KeyError: If no such order was created.
            ValueError: If the order is not a pending Pix order.
        """

        def move(order: dict[str, Any]) -> None:
            """Apply the payment.

            Args:
                order (dict[str, Any]): The stored order.

            Raises:
                _APIError: If the order is not a pending Pix.
            """
            if not self._is_pix(order) or order["status_detail"] != "waiting_transfer":
                raise _APIError(409, "cannot_pay_order", "Not a pending Pix order.")
            payment = order["transactions"]["payments"][0]
            payment["paid_amount"] = payment["amount"]
            payment["payment_method"]["e2e_id"] = f"E2EFAKE{order['id'][-12:]}"
            self._drop_qr(order)
            self._set(order, "processed", "accredited")

        return self._transition(order_id, move)

    def expire(self, order_id: str) -> MercadoPagoDelivery:
        """Let a pending Pix order run out, as its deadline would.

        ``canceled`` / ``expired`` with the payment ``expired`` /
        ``expired`` — read from the sandbox after a ``PT60S`` order's
        deadline (``pix_get_expired.json``). The adapter reads it as
        :attr:`PaymentStatusEnum.EXPIRED`.

        Args:
            order_id (str): A Pix order in ``action_required``.

        Returns:
            MercadoPagoDelivery: ``order.canceled`` — the one action observed
            live, on a canceled order whose ``status_detail`` the evidence
            does not record.

        Raises:
            KeyError: If no such order was created.
            ValueError: If the order is not a pending Pix order.
        """

        def move(order: dict[str, Any]) -> None:
            """Apply the expiry.

            Args:
                order (dict[str, Any]): The stored order.

            Raises:
                _APIError: If the order is not a pending Pix.
            """
            if not self._is_pix(order) or order["status"] != "action_required":
                raise _APIError(409, "cannot_expire_order", "Not a pending Pix order.")
            self._drop_qr(order)
            self._set(order, "canceled", "expired", payment_status="expired")

        return self._transition(order_id, move)

    def fail(
        self, order_id: str, status_detail: str = "rejected_by_issuer"
    ) -> MercadoPagoDelivery:
        """Fail a pending order, as a declined payment would.

        Args:
            order_id (str): An order in ``action_required``.
            status_detail (str): The payment's ``status_detail``.

        Returns:
            MercadoPagoDelivery: ``order.failed`` (not observed live).

        Raises:
            KeyError: If no such order was created.
            ValueError: If the order is not pending.
        """

        def move(order: dict[str, Any]) -> None:
            """Apply the failure.

            Args:
                order (dict[str, Any]): The stored order.

            Raises:
                _APIError: If the order is not pending.
            """
            if order["status"] != "action_required":
                raise _APIError(409, "cannot_fail_order", "Not a pending order.")
            self._set(order, "failed", "failed", payment_detail=status_detail)

        return self._transition(order_id, move)

    def cancel(self, order_id: str) -> MercadoPagoDelivery:
        """Cancel a pending order, as ``POST /v1/orders/{id}/cancel`` does.

        Args:
            order_id (str): An order in ``action_required``.

        Returns:
            MercadoPagoDelivery: ``order.canceled``, the action name observed
            live (on an order whose cause — cancel or expiry — the evidence
            does not record).

        Raises:
            KeyError: If no such order was created.
            ValueError: If the order is not pending.
        """
        return self._transition(order_id, self._cancel)

    def capture(self, order_id: str) -> MercadoPagoDelivery:
        """Capture a card authorization, as ``POST …/capture`` does.

        Args:
            order_id (str): A card order in ``waiting_capture``.

        Returns:
            MercadoPagoDelivery: ``order.processed`` (not observed live).

        Raises:
            KeyError: If no such order was created.
            ValueError: If the order is not waiting on capture.
        """
        return self._transition(order_id, self._capture)

    def refund(
        self, order_id: str, amount_cents: int | None = None
    ) -> MercadoPagoDelivery:
        """Refund a processed order, as ``POST …/refund`` does.

        Args:
            order_id (str): A ``processed`` order.
            amount_cents (int | None): Cents to refund; ``None`` refunds what
                is left.

        Returns:
            MercadoPagoDelivery: ``order.refunded`` after the last cent,
            ``order.processed`` after a partial refund (neither observed
            live).

        Raises:
            KeyError: If no such order was created.
            ValueError: If the order is not ``processed`` or the amount is
                not positive or exceeds what is left.
        """
        return self._transition(
            order_id, lambda order: self._refund(order, amount_cents)
        )

    def apply(
        self, order_id: str, transition: str, amount_cents: int | None = None
    ) -> MercadoPagoDelivery:
        """Apply a transition by name — what the control route calls.

        Args:
            order_id (str): The order id.
            transition (str): ``pay``, ``expire``, ``fail``, ``cancel``,
                ``capture`` or ``refund``.
            amount_cents (int | None): For ``refund`` only.

        Returns:
            MercadoPagoDelivery: The delivery about the new state.

        Raises:
            KeyError: If no such order was created.
            ValueError: For an unknown transition, or one that does not apply.
        """
        if transition not in TRANSITIONS:
            raise ValueError(
                f"Unknown transition {transition!r}; expected one of "
                f"{sorted(TRANSITIONS)}."
            )
        if transition == "refund":
            return self.refund(order_id, amount_cents)
        move: Callable[[str], MercadoPagoDelivery] = getattr(self, transition)
        return move(order_id)


__all__: list[str] = [
    "DEFAULT_PIX_EXPIRATION",
    "TRANSITIONS",
    "FakeMercadoPagoOrdersAPI",
]
