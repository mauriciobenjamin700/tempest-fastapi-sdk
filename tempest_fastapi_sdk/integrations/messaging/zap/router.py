"""Opt-in FastAPI router receiving the zap-api webhook.

:func:`make_zap_webhook_router` mounts the route every service writes by
hand: a ``POST`` guarded by :func:`make_zap_webhook_dependency`, dispatch on
``delivery.inbound`` / ``delivery.status``, and a 2xx answer for everything
the signature accepted. The factory owns that path so the consumer writes
only the two handlers.

**A non-2xx is a failed delivery, and the gateway retries it.** Read in the
gateway's code at commit ``d0477d8``: both the inbound fan-out and the
status callbacks go through the durable ``callback_deliveries`` queue, and
``CallbackService.deliver`` marks the row delivered only on ``res.ok``,
otherwise ``recordFailure`` schedules another attempt with exponential
backoff until ``CALLBACK_MAX_ATTEMPTS`` — 3 by default
(``src/config/env.ts``) — and gives up after that. So the router's answers
follow from that one fact, all three read from the code rather than measured
against a running gateway:

* an event with no handler, and an event this SDK does not model, answer
  ``200`` without calling anything, logged at ``debug`` — retrying a body we
  never understood only produces the same ``200`` again. The log names the
  event and lists the fields the body carried, not their values: on a webhook
  the values are somebody's message.
* a handler that raises propagates, so the response is ``500`` and the
  gateway re-delivers the same bytes — which is what lets a handler that
  failed on a cold database recover, and why the consumer deduplicates on
  the ids the delivery carries. Catch the exception inside the handler to
  answer 2xx instead, and the gateway stops retrying;
* the route declares no request body, so a body the SDK does not model is
  parsed and logged rather than rejected with ``422`` — which the gateway
  would read as a failed delivery.

Handlers are plain async callables taking the typed body, **not** FastAPI
dependencies. A dependency cannot receive the parsed body: FastAPI resolves
dependencies before the route body runs, and the SDK invents that type, so
there is nothing to bind it to. The closest form — a dependency *returning*
the handler — resolves the consumer's own dependencies on every request,
including for the event that never arrived, which opens a database session
per delivery for nothing. So a handler closes over what it needs instead:
a service built in the ``lifespan``, a queue client, a module-level
session factory. When the work needs a request-scoped session and no
retry semantics, the hand-written route (with the dependency as
``Depends``) is still the shorter path.

Examples:

    >>> from fastapi import FastAPI
    >>> app: FastAPI = FastAPI()
    >>> async def on_inbound(message: ZapInboundMessage) -> None: ...
    >>> router = make_zap_webhook_router(
    ...     secret="meu-segredo",
    ...     on_inbound=on_inbound,
    ... )
    >>> app.include_router(router)
    >>> [route.path for route in router.routes]
    ['/webhooks/zap']
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Depends

from tempest_fastapi_sdk.integrations.messaging.zap.webhooks import (
    ZapInboundMessage,
    ZapStatusCallback,
    ZapWebhookDelivery,
    make_zap_webhook_dependency,
)
from tempest_fastapi_sdk.schemas import BaseSchema

logger: logging.Logger = logging.getLogger(__name__)

ZapInboundHandler = Callable[[ZapInboundMessage], Awaitable[None]]
"""An async callable receiving one verified ``message.received`` body.

Receives the :class:`ZapInboundMessage` the dependency already validated,
and nothing else — close over the service, queue or session factory it
needs. Raise to have the gateway re-deliver; return to accept.
"""

ZapStatusHandler = Callable[[ZapStatusCallback], Awaitable[None]]
"""An async callable receiving one verified ``message.${status}`` body.

Receives the :class:`ZapStatusCallback` the dependency already validated,
which covers ``message.sent``, ``message.delivered``, ``message.read`` and
``message.failed`` — check
:attr:`ZapStatusCallback.status` to tell them apart, and
:func:`~tempest_fastapi_sdk.integrations.messaging.zap.is_forward_transition`
before storing one, since notifications arrive out of order.
"""


class ZapWebhookAckSchema(BaseSchema):
    """The acknowledgement the webhook route answers with.

    The gateway only reads the status code, so the body exists for the
    consumer reading a log or a ``TestClient`` transcript: ``event`` is the
    ``event`` field exactly as delivered, kept even when this SDK does not
    model it.

    Attributes:
        ok (bool): Always ``True`` — anything else arrives as an error
            status, never as a body.
        event (str): The ``event`` name of the delivery, or ``""`` for a
            body that was not JSON.
    """

    ok: bool
    event: str


def _checked(name: str, handler: object) -> None:
    """Refuse a handler that cannot be called.

    Args:
        name (str): The parameter's name, for the message.
        handler (object): What the caller passed.

    Raises:
        TypeError: When ``handler`` is neither ``None`` nor callable.
    """
    if handler is not None and not callable(handler):
        raise TypeError(
            f"make_zap_webhook_router expects `{name}` to be an async callable "
            f"taking the parsed body, got {type(handler).__name__}"
        )


def make_zap_webhook_router(
    *,
    secret: str | bytes | None = None,
    verify: Callable[..., Coroutine[Any, Any, ZapWebhookDelivery]] | None = None,
    on_inbound: ZapInboundHandler | None = None,
    on_status: ZapStatusHandler | None = None,
    path: str = "/webhooks/zap",
    tags: list[str] | None = None,
    include_in_schema: bool = True,
) -> APIRouter:
    """Build a router with the one ``POST`` the zap-api webhook needs.

    One route, ``POST {path}``, dispatching by event: ``on_inbound`` for a
    ``message.received`` delivery and ``on_status`` for the four status
    events. It answers ``200`` with :class:`ZapWebhookAckSchema` once the
    handler returns, and ``401`` when the signature does not verify.

    Both events reach the same URL, so pass ``--events status,inbound`` when
    registering the webhook on the gateway, and one or both handlers
    depending on which half the service subscribes to.

    Args:
        secret (str | bytes | None): The webhook secret, as registered on
            the gateway. Builds the signature dependency with
            :func:`make_zap_webhook_dependency`.
        verify (Callable[..., Coroutine[Any, Any, ZapWebhookDelivery]] | None):
            A dependency verifying the delivery instead of ``secret`` — the
            one :func:`make_zap_webhook_dependency` returns, for a rotated
            secret checked by a custom verifier or a test double.
        on_inbound (ZapInboundHandler | None): Called with the validated
            :class:`ZapInboundMessage` of a ``message.received``
            delivery. ``None`` (the default) leaves those deliveries to a
            ``200`` and a ``debug`` log.
        on_status (ZapStatusHandler | None): Called with the validated
            :class:`ZapStatusCallback` of a status delivery. ``None``
            leaves those deliveries to a ``200`` and a ``debug`` log.
        path (str): The path to mount. Register this same path on the
            gateway (``npm run webhook:set``).
        tags (list[str] | None): OpenAPI tags. Defaults to ``["zap"]``.
        include_in_schema (bool): Whether the route appears in the OpenAPI
            document. Turn it off to keep the webhook out of a public
            schema.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.

    Raises:
        ValueError: If both ``secret`` and ``verify`` are given, or neither
            is, or ``secret`` is empty. The gateway signs a delivery only
            when the webhook was registered with a secret — without one it
            POSTs **unsigned** — and a route that accepted that would look
            authenticated while checking nothing.
        TypeError: If ``on_inbound`` or ``on_status`` is neither ``None``
            nor callable.

    A delivery the signature accepted but no handler claimed answers
    ``200``; a handler that raises propagates, so the response is ``500``
    and the gateway re-delivers. See the module docstring for the gateway
    code both come from.
    """
    if secret is not None and verify is not None:
        raise ValueError("pass `secret` or `verify`, not both")
    if verify is None:
        if not secret:
            raise ValueError("a non-empty `secret` (or a `verify`) is required")
        verify = make_zap_webhook_dependency(secret=secret)
    _checked("on_inbound", on_inbound)
    _checked("on_status", on_status)

    router = APIRouter(tags=list(tags or ["zap"]))

    @router.post(
        path,
        response_model=ZapWebhookAckSchema,
        include_in_schema=include_in_schema,
    )
    async def receive_zap(
        delivery: ZapWebhookDelivery = Depends(verify),
    ) -> ZapWebhookAckSchema:
        """Verify the delivery and hand it to the handler of its event.

        Args:
            delivery (ZapWebhookDelivery): The verified, parsed delivery.

        Returns:
            ZapWebhookAckSchema: The 2xx acknowledgement, carrying the
            ``event`` name as delivered.
        """
        if delivery.inbound is not None and on_inbound is not None:
            await on_inbound(delivery.inbound)
        elif delivery.status is not None and on_status is not None:
            await on_status(delivery.status)
        else:
            logger.debug(
                "zap webhook: no handler for event %r (fields=%s)",
                delivery.event_name,
                sorted(delivery.payload),
            )
        return ZapWebhookAckSchema(ok=True, event=delivery.event_name)

    return router


__all__: list[str] = [
    "ZapInboundHandler",
    "ZapStatusHandler",
    "ZapWebhookAckSchema",
    "make_zap_webhook_router",
]
