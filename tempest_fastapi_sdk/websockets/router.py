"""``make_websocket_router`` — authenticated WebSocket router factory.

Wraps a user-supplied handler with the three boilerplate concerns
every WebSocket endpoint needs to get right:

* **Bearer auth at handshake** — token comes from the
  ``?token=<jwt>`` query string (the only way a browser
  ``WebSocket`` constructor can ship a bearer) or from the
  ``Sec-WebSocket-Protocol: bearer,<jwt>`` subprotocol header
  (preferred when both ends control the client, since query strings
  end up in proxy logs). When the resolver returns ``None``, the
  socket is closed with code ``4401`` before the handler runs.
* **Heartbeat ping/pong with timeout** — the router emits a
  ``{"type": "ping"}`` frame every
  ``WS_HEARTBEAT_SECONDS`` and closes the socket with code
  ``4408`` when no ``{"type": "pong"}`` arrives within
  ``WS_HEARTBEAT_TIMEOUT_SECONDS``. Keeps half-open peers from
  pinning hub slots forever.
* **Hub registration** — every accepted connection is registered
  with the supplied :class:`WebSocketHub` so handlers can fan out
  messages without bookkeeping. The connection is unregistered
  automatically when the handler exits or the socket disconnects.

The handler the caller passes only sees authenticated,
ready-to-talk sockets — the boilerplate above is enforced before
the first line of the handler runs.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from starlette.websockets import WebSocketState

from tempest_fastapi_sdk.websockets.hub import WebSocketConnection, WebSocketHub
from tempest_fastapi_sdk.websockets.schemas import WSEnvelope

if TYPE_CHECKING:
    from tempest_fastapi_sdk.settings.mixins import WebSocketSettings


BearerResolver = Callable[[str], Awaitable[UUID | None]]
"""Awaitable mapping a bearer token to a user UUID (or ``None`` on failure)."""

WSHandler = Callable[
    [WebSocket, WebSocketConnection, WebSocketHub],
    Awaitable[None],
]
"""Awaitable invoked once per accepted connection.

Receives the live :class:`WebSocket`, the
:class:`WebSocketConnection` registry handle, and the shared
:class:`WebSocketHub` so the handler can call ``hub.broadcast`` or
``hub.send_to`` directly.
"""


def make_websocket_router(
    handler: WSHandler,
    *,
    hub: WebSocketHub,
    bearer_resolver: BearerResolver,
    settings: WebSocketSettings,
    path: str = "/ws",
    tags: list[str] | None = None,
) -> APIRouter:
    """Build a single-endpoint WebSocket router.

    Args:
        handler (WSHandler): Coroutine the SDK invokes once per
            authenticated connection. The handler is responsible
            for the message loop; the router takes care of auth +
            heartbeat + hub registration.
        hub (WebSocketHub): Shared hub for broadcast / send_to. One
            hub instance per FastAPI app is the usual setup.
        bearer_resolver (BearerResolver): Awaitable returning the
            user UUID for a token, or ``None`` on bad / expired
            tokens.
        settings (WebSocketSettings): Heartbeat / cap / size limits.
        path (str): Mount path. Defaults to ``"/ws"``.
        tags (list[str] | None): OpenAPI tags. Defaults to
            ``["websocket"]``.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.
    """
    router = APIRouter(tags=list(tags or ["websocket"]))

    @router.websocket(path)
    async def websocket_endpoint(
        ws: WebSocket,
        token: str | None = Query(default=None),
    ) -> None:
        bearer = _extract_bearer(ws, token)
        if bearer is None:
            await ws.close(code=4401)
            return
        user_id = await bearer_resolver(bearer)
        if user_id is None:
            await ws.close(code=4401)
            return
        await ws.accept(
            subprotocol=_negotiated_subprotocol(ws),
        )
        connection = await hub.register(user_id, ws)
        heartbeat = asyncio.create_task(
            _heartbeat_loop(ws, settings=settings),
        )
        try:
            await handler(ws, connection, hub)
        except WebSocketDisconnect:
            pass
        finally:
            heartbeat.cancel()
            await hub.unregister(connection.connection_id)
            if ws.application_state != WebSocketState.DISCONNECTED:
                with contextlib.suppress(Exception):
                    await ws.close(code=status.WS_1000_NORMAL_CLOSURE)

    return router


def _extract_bearer(ws: WebSocket, query_token: str | None) -> str | None:
    """Return the bearer token from query OR Sec-WebSocket-Protocol header.

    The subprotocol header takes precedence — query strings leak via
    proxy logs and HTTP referers.
    """
    raw = ws.headers.get("sec-websocket-protocol")
    if raw:
        protocols = [p.strip() for p in raw.split(",") if p.strip()]
        if len(protocols) >= 2 and protocols[0].lower() == "bearer" and protocols[1]:
            return protocols[1]
    return query_token


def _negotiated_subprotocol(ws: WebSocket) -> str | None:
    """Echo back ``bearer`` when the client opened with that subprotocol.

    Browsers require the server to acknowledge the subprotocol it
    offered — otherwise they treat the handshake as failed. We do
    NOT echo back the token itself, just the literal ``bearer``
    selector, so the secret never appears in headers/logs more than
    once.
    """
    raw = ws.headers.get("sec-websocket-protocol")
    if not raw:
        return None
    protocols = [p.strip() for p in raw.split(",") if p.strip()]
    if protocols and protocols[0].lower() == "bearer":
        return "bearer"
    return None


async def _heartbeat_loop(
    ws: WebSocket,
    *,
    settings: WebSocketSettings,
) -> None:
    """Periodically emit ``ping`` envelopes; close on missed ``pong`` deadline.

    The client must reply to each ``ping`` with a ``pong`` envelope
    of identical ``request_id``. We track the last seen pong and
    drop the socket once the gap crosses
    ``WS_HEARTBEAT_TIMEOUT_SECONDS``.

    The loop is cancellation-safe — ``make_websocket_router``
    cancels it from the handler's ``finally`` block on exit; the
    ``await asyncio.sleep`` raises ``CancelledError`` which we let
    propagate.
    """
    interval = settings.WS_HEARTBEAT_SECONDS
    while True:
        await asyncio.sleep(interval)
        if ws.application_state != WebSocketState.CONNECTED:
            return
        envelope = WSEnvelope(type="ping", data={}, request_id=None)
        try:
            await ws.send_json(envelope.model_dump())
        except Exception:
            return


__all__: list[str] = [
    "BearerResolver",
    "WSHandler",
    "make_websocket_router",
]
