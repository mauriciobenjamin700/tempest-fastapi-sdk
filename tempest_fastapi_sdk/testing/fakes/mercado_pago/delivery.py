"""The signed notification Mercado Pago sends about an order.

:func:`build_mercado_pago_delivery` signs through
:func:`~tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks.sign_manifest`,
the function the SDK's verifier recomputes, so there is no second copy of the
manifest rules to drift.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlencode

import httpx

from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
    ORDER_TOPIC,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks import (
    MERCADO_PAGO_DATA_ID_QUERY,
    MERCADO_PAGO_REQUEST_ID_HEADER,
    MERCADO_PAGO_SIGNATURE_HEADER,
    sign_manifest,
)

FAKE_WEBHOOK_SECRET: Final[str] = "tempest-fake-mercado-pago-webhook-secret"
"""Secret :class:`FakeMercadoPagoOrdersAPI` signs its deliveries with by default.

A fixed, public value: it only ever authenticates the simulator to a service
under test. Configure the service with the same value, or pass your own to
the constructor.
"""

FAKE_APPLICATION_ID: Final[str] = "2000000002"
"""``application_id`` written into a simulated order and delivery.

The redacted value the sandbox fixtures carry, not a real application.
"""

FAKE_USER_ID: Final[str] = "1000000001"
"""``user_id`` written into a simulated order and delivery (redacted value)."""


def order_action(order: Mapping[str, Any]) -> str:
    """Name the ``action`` a delivery about this order carries.

    ``order.<status>`` — the rule the one observed live action fits: a
    ``canceled`` order arrived as ``order.canceled`` (production, 2026-11-13).

    Provenance of each name this produces, so a test that branches on the
    action knows what it rests on:

    - ``order.canceled`` — **observed** on a live delivery.
    - ``order.processed``, ``order.refunded``, ``order.action_required``,
      ``order.failed`` — **not observed**, and **not in the vendored
      document** either, whose ``WebhookNotification.action`` lists only
      ``order.created`` and ``order.updated``. They are this simulator's
      extrapolation of the observed rule.

    Pass ``action=`` to :func:`build_mercado_pago_delivery` to send
    ``order.created`` / ``order.updated`` (in the vendored document, not
    observed) or anything else. The SDK's delivery dependency does not branch
    on the action: it re-reads the order and decides on its status.

    Args:
        order (Mapping[str, Any]): The order the delivery is about.

    Returns:
        str: ``order.<status>``, or ``order.updated`` when the order carries
        no status.
    """
    status = order.get("status")
    if isinstance(status, str) and status:
        return f"{ORDER_TOPIC}.{status}"
    return f"{ORDER_TOPIC}.updated"


def _summary(order: Mapping[str, Any]) -> dict[str, Any]:
    """Cut an order down to what a delivery's ``data`` carried.

    Observed in production: ``id``, ``external_reference``, ``status``,
    ``status_detail``, ``total_amount``, ``total_paid_amount``, ``type``,
    ``version`` and ``transactions.payments[]`` with the payment method.
    Keys the order lacks are left out rather than invented.

    Args:
        order (Mapping[str, Any]): The full order.

    Returns:
        dict[str, Any]: The summary.
    """
    keys = (
        "id",
        "type",
        "external_reference",
        "status",
        "status_detail",
        "total_amount",
        "total_paid_amount",
        "version",
    )
    summary: dict[str, Any] = {key: order[key] for key in keys if key in order}
    transactions = order.get("transactions")
    payments = (
        transactions.get("payments") if isinstance(transactions, Mapping) else None
    )
    if isinstance(payments, list):
        summary["transactions"] = {
            "payments": [
                {
                    key: payment[key]
                    for key in ("id", "amount", "status", "status_detail")
                    if key in payment
                }
                | {
                    "payment_method": {
                        key: payment["payment_method"][key]
                        for key in ("id", "type")
                        if key in payment.get("payment_method", {})
                    }
                }
                for payment in payments
                if isinstance(payment, Mapping)
            ]
        }
    return summary


@dataclass(frozen=True, slots=True)
class MercadoPagoDelivery:
    """One signed notification, ready to send to a webhook route.

    Attributes:
        query (dict[str, str]): The query string — ``data.id``, ``type`` and,
            when the order has one, ``data.external_reference``.
        headers (dict[str, str]): ``x-signature``, ``x-request-id`` and
            ``content-type``.
        payload (dict[str, Any]): The decoded body.
        body (bytes): The body exactly as it goes on the wire.
        data_id (str): The order id the signature covers.
        request_id (str): The ``x-request-id`` the signature covers.
        timestamp (str): The ``ts`` the signature covers, in seconds.
    """

    query: dict[str, str]
    headers: dict[str, str]
    payload: dict[str, Any]
    body: bytes
    data_id: str
    request_id: str
    timestamp: str

    def url(self, endpoint: str) -> str:
        """Join the route's URL and this delivery's query string.

        Args:
            endpoint (str): The webhook route, absolute or relative.

        Returns:
            str: ``endpoint?data.id=…&type=order…``.
        """
        separator = "&" if "?" in endpoint else "?"
        return f"{endpoint}{separator}{urlencode(self.query)}"

    async def send(self, client: httpx.AsyncClient, endpoint: str) -> httpx.Response:
        """Post this delivery with an async client.

        Args:
            client (httpx.AsyncClient): Any async client — one over
                ``httpx.ASGITransport(app=app)`` reaches a FastAPI app
                in-process.
            endpoint (str): The webhook route.

        Returns:
            httpx.Response: What the route answered.
        """
        return await client.post(
            self.url(endpoint), headers=self.headers, content=self.body
        )

    def send_sync(self, client: httpx.Client, endpoint: str) -> httpx.Response:
        """Post this delivery with a sync client.

        Args:
            client (httpx.Client): Any sync client, ``fastapi.testclient.TestClient``
                included.
            endpoint (str): The webhook route.

        Returns:
            httpx.Response: What the route answered.
        """
        return client.post(self.url(endpoint), headers=self.headers, content=self.body)


def build_mercado_pago_delivery(
    order: Mapping[str, Any],
    *,
    secret: str,
    action: str | None = None,
    request_id: str | None = None,
    ts: int | None = None,
    live_mode: bool = False,
    application_id: str = FAKE_APPLICATION_ID,
    user_id: str = FAKE_USER_ID,
) -> MercadoPagoDelivery:
    """Build the notification Mercado Pago sends about an order.

    The shape is the production delivery of 2026-11-13
    (``vendor/mercadopago-evidence.md`` section 11). The signature is
    :func:`sign_manifest` over the order id **as delivered**, the request id
    and the timestamp — the same call the SDK's verifier makes, which
    lowercases the id into the manifest.

    Args:
        order (Mapping[str, Any]): The order, at least ``{"id": "ORD…"}``.
            Its ``external_reference`` goes into the query and a summary of
            it into ``data``, as observed.
        secret (str): The webhook secret the receiving service verifies
            with.
        action (str | None): The body's ``action``. ``None`` takes
            :func:`order_action` of the order — read its docstring for which
            names were observed.
        request_id (str | None): The ``x-request-id``. ``None`` draws a
            UUID4.
        ts (int | None): The signature's timestamp, in seconds since the
            epoch. ``None`` takes the current time; pass an old one to test
            the verifier's tolerance window.
        live_mode (bool): The body's ``live_mode``. ``True`` on the observed
            production delivery; ``False`` by default because this delivery
            is not one.
        application_id (str): The body's ``application_id``.
        user_id (str): The body's ``user_id``.

    Returns:
        MercadoPagoDelivery: The delivery.

    Raises:
        ValueError: If the order carries no id, or ``secret`` is empty —
            the SDK's verifier rejects every delivery under an empty secret,
            so building one would only produce a ``401`` that looks like a
            signing bug.
    """
    data_id = order.get("id")
    if not isinstance(data_id, str) or not data_id:
        raise ValueError("A delivery needs an order with a non-empty string 'id'.")
    if not secret:
        raise ValueError(
            "An empty secret signs nothing the SDK's verifier accepts; pass the "
            "secret the receiving service is configured with."
        )
    request = request_id or str(uuid.uuid4())
    timestamp = str(int(time.time()) if ts is None else ts)
    digest = sign_manifest(
        secret=secret, data_id=data_id, request_id=request, timestamp=timestamp
    )
    query: dict[str, str] = {}
    reference = order.get("external_reference")
    if isinstance(reference, str) and reference:
        query["data.external_reference"] = reference
    query[MERCADO_PAGO_DATA_ID_QUERY] = data_id
    query["type"] = ORDER_TOPIC
    payload: dict[str, Any] = {
        "action": action or order_action(order),
        "api_version": "v1",
        "application_id": application_id,
        "data": _summary(order),
        "live_mode": live_mode,
        "type": ORDER_TOPIC,
        "user_id": user_id,
    }
    return MercadoPagoDelivery(
        query=query,
        headers={
            MERCADO_PAGO_SIGNATURE_HEADER: f"ts={timestamp},v1={digest}",
            MERCADO_PAGO_REQUEST_ID_HEADER: request,
            "content-type": "application/json",
        },
        payload=payload,
        body=json.dumps(payload).encode("utf-8"),
        data_id=data_id,
        request_id=request,
        timestamp=timestamp,
    )


__all__: list[str] = [
    "FAKE_APPLICATION_ID",
    "FAKE_USER_ID",
    "FAKE_WEBHOOK_SECRET",
    "MercadoPagoDelivery",
    "build_mercado_pago_delivery",
    "order_action",
]
