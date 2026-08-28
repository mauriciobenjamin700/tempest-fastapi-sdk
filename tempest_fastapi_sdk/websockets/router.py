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
* **Heartbeat with timeout** — the router emits a ``{"type": "ping"}``
  frame every ``WS_HEARTBEAT_SECONDS`` and closes the socket with code
  ``4408`` when nothing arrives for ``WS_HEARTBEAT_TIMEOUT_SECONDS``.
  Keeps half-open peers from pinning hub slots forever. **Any** inbound
  frame counts as proof of life, not only ``pong``: a peer mid-exchange
  is demonstrably present, and demanding the specific reply disconnects
  a busy client that has not got to it yet. The machinery is
  :func:`tempest_fastapi_sdk.websockets.heartbeat`, usable on its own by
  an endpoint that wants neither the auth nor the hub.
* **A ``hello`` frame announcing the cadence** — the first frame the
  router sends is ``{"type": "hello", "data": {"heartbeat_seconds": N}}``.
  A browser cannot tell a normal quiet interval from a dead link, so its
  watchdog has to be calibrated from that number; hard-coding it means
  retuning the server turns every client's normal interval into a
  perceived drop.
* **Hub registration** — every accepted connection is registered
  with the supplied :class:`WebSocketHub` so handlers can fan out
  messages without bookkeeping. The connection is unregistered
  automatically when the handler exits or the socket disconnects.

The handler the caller passes only sees authenticated,
ready-to-talk sockets — the boilerplate above is enforced before
the first line of the handler runs.

!!! warning "Install the ``[websocket]`` extra"
    A bare ``uvicorn`` speaks no WebSocket protocol: the handshake
    answers **404** and the reason (``No supported WebSocket library
    detected``) only shows up in the server log. Worse, Starlette's
    ``TestClient`` implements WS itself, so a whole test suite passes
    while the real server rejects every connection. Install
    ``tempest-fastapi-sdk[websocket]`` — it pulls ``websockets``,
    which uvicorn auto-detects.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from starlette.websockets import WebSocketState

from tempest_fastapi_sdk.websockets.heartbeat import Liveness, heartbeat
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
        async with heartbeat(
            ws,
            settings=settings,
            max_message_bytes=settings.WS_MAX_MESSAGE_BYTES,
        ) as live:
            await _send_hello(ws, live=live)
            connection = await hub.register(user_id, ws)
            try:
                await handler(ws, connection, hub)
            except WebSocketDisconnect:
                pass
            finally:
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


async def _send_hello(ws: WebSocket, *, live: Liveness) -> None:
    """Announce the heartbeat cadence as the first frame.

    Args:
        ws (WebSocket): The accepted socket.
        live (Liveness): Supplies the interval actually in use.

    Sent before the handler runs, so a client can calibrate its own
    silence watchdog from the server's real cadence instead of a
    hard-coded guess. Additive on the wire: a client that dispatches on
    ``type`` and ignores what it does not know is unaffected.

    A failure here is swallowed. The socket is already accepted and the
    handler has not run; dropping the connection over a courtesy frame
    would trade a calibrated client for no client.
    """
    envelope = WSEnvelope(
        type="hello",
        data={"heartbeat_seconds": live.interval_seconds},
        request_id=None,
    )
    with contextlib.suppress(Exception):
        await ws.send_json(envelope.model_dump())


__all__: list[str] = [
    "BearerResolver",
    "WSHandler",
    "make_websocket_router",
]
