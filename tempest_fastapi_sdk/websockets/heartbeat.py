"""``heartbeat`` — liveness for any WebSocket endpoint.

A socket whose peer vanished without a close frame — a phone that lost
signal, entered a tunnel, or was force-quit — stays open until the
kernel's TCP timeout, which is minutes. For that whole window the server
holds a participant it can never reach: the other side of a call keeps a
frozen tile, a hub keeps a slot, a room keeps offering to someone who is
gone. Nothing raises, because sending to a half-open socket succeeds.

This is the primitive that closes that window, and it asks for nothing
else: no bearer at the handshake, no hub registration, no particular
message shape.

.. code-block:: python

    from fastapi import WebSocket

    from tempest_fastapi_sdk.settings import WebSocketSettings
    from tempest_fastapi_sdk.websockets import heartbeat


    async def signaling(ws: WebSocket, settings: WebSocketSettings) -> None:
        \"\"\"Relay frames between anonymous peers, evicting the silent ones.\"\"\"
        await ws.accept()
        async with heartbeat(ws, settings=settings) as live:
            await ws.send_json(
                {"type": "hello", "heartbeat_seconds": live.interval_seconds}
            )
            while True:
                payload = await ws.receive_json()
                await relay(payload)

Until v0.261.0 this lived inside ``make_websocket_router`` as four
private functions, reachable only by accepting that router's bearer auth
and its ``user_id``-keyed hub. An endpoint outside that mould — an
anonymous room addressed by peer id, where the room code is the only
secret — had to reimplement it.

## Any frame is proof of life

The loop counts **every** inbound frame, not only ``pong``. A peer in the
middle of an ICE negotiation is demonstrably present, and demanding the
specific reply disconnects a busy client that simply has not got to it
yet. ``pong`` still counts, and is still swallowed so it never reaches
the application as data.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, MutableMapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, status
from starlette.websockets import WebSocketDisconnect, WebSocketState

from tempest_fastapi_sdk.settings import WebSocketSettings
from tempest_fastapi_sdk.websockets.schemas import WSEnvelope

HEARTBEAT_TIMEOUT_CODE: int = 4408
"""Close code for a peer that went silent past the deadline.

Application range, so it never collides with a protocol-level close.
Clients are expected to treat it as "reconnect", not as an error.
"""


@dataclass
class Liveness:
    """The handle :func:`heartbeat` yields.

    Attributes:
        interval_seconds (int): How often a ping goes out. Read it to
            announce the cadence to the client — a browser cannot tell a
            normal quiet interval from a dead link, so its own watchdog
            has to be calibrated from this number rather than
            hard-coded. Retuning the server would otherwise make every
            client read the new interval as a drop.
        timeout_seconds (int): Silence tolerated before the close.
        last_seen (float): ``time.monotonic()`` of the last inbound
            frame, seeded at entry so a peer that never speaks is still
            evicted one timeout after connecting.
    """

    interval_seconds: int
    timeout_seconds: int
    last_seen: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        """Mark the peer as alive right now.

        Every inbound frame already does this on its own. Call it when
        liveness is proven by something the socket cannot see — a
        message that arrived over another transport for the same
        session, or work the peer demonstrably completed.
        """
        self.last_seen = time.monotonic()


def _frame_size(message: MutableMapping[str, Any]) -> int:
    """Return the byte length of an inbound ``websocket.receive`` frame.

    Args:
        message (MutableMapping[str, Any]): The raw ASGI message.

    Returns:
        int: Payload size in bytes — the text encoded as UTF-8, or the
        binary payload as-is.
    """
    data = message.get("bytes")
    if data is not None:
        return len(data)
    text = message.get("text")
    if text is not None:
        return len(text.encode("utf-8"))
    return 0


def _is_pong(message: MutableMapping[str, Any]) -> bool:
    """Report whether a frame is the client's heartbeat reply.

    Args:
        message (MutableMapping[str, Any]): The raw ASGI message.

    Returns:
        bool: True for a JSON text frame whose ``type`` is ``"pong"``.
        Anything unparseable is application data, not a heartbeat.
    """
    text = message.get("text")
    if not text:
        return False
    with contextlib.suppress(ValueError, TypeError):
        payload = json.loads(text)
        return isinstance(payload, dict) and payload.get("type") == "pong"
    return False


def _install_frame_guard(
    ws: WebSocket,
    *,
    live: Liveness,
    max_message_bytes: int | None,
) -> None:
    """Wrap ``ws.receive`` so this module sees every inbound frame.

    The handler owns the message loop, so wrapping ``receive`` is the
    only place two promises can be kept on the socket's behalf:

    * **liveness** — every frame stamps :attr:`Liveness.last_seen`, and
      a ``pong`` is additionally swallowed, since it answers our own
      ping and is not application data.
    * **the size cap**, when one is given — an oversized frame closes
      the socket with ``1009`` before the handler ever allocates it.

    Every ``receive_text`` / ``receive_bytes`` / ``receive_json`` on the
    socket funnels through ``receive``, so wrapping that single method
    covers all of them.

    Args:
        ws (WebSocket): The accepted socket, mutated in place.
        live (Liveness): Stamped on each inbound frame.
        max_message_bytes (int | None): Size cap, or ``None`` to accept
            any frame the transport delivers.
    """
    original = ws.receive

    async def guarded_receive() -> Any:
        while True:
            message = await original()
            if message.get("type") != "websocket.receive":
                return message
            if (
                max_message_bytes is not None
                and _frame_size(message) > max_message_bytes
            ):
                with contextlib.suppress(Exception):
                    await ws.close(code=status.WS_1009_MESSAGE_TOO_BIG)
                raise WebSocketDisconnect(code=status.WS_1009_MESSAGE_TOO_BIG)
            live.touch()
            if _is_pong(message):
                continue
            return message

    ws.receive = guarded_receive  # type: ignore[method-assign]


async def _beat(ws: WebSocket, *, live: Liveness) -> None:
    """Emit ``ping`` envelopes; close once the peer goes silent too long.

    Args:
        ws (WebSocket): The accepted socket.
        live (Liveness): Read for the deadline, written by the guard.

    The deadline is checked *before* the next ping rather than after, so
    a peer that answered nothing is evicted one timeout after the
    connection was accepted, not one timeout after the first ping.

    Cancellation-safe: :func:`heartbeat` cancels this from its exit
    path, and the ``await asyncio.sleep`` raises ``CancelledError``,
    which is left to propagate.
    """
    while True:
        await asyncio.sleep(live.interval_seconds)
        if ws.application_state != WebSocketState.CONNECTED:
            return
        if time.monotonic() - live.last_seen > live.timeout_seconds:
            with contextlib.suppress(Exception):
                await ws.close(code=HEARTBEAT_TIMEOUT_CODE)
            return
        envelope = WSEnvelope(type="ping", data={}, request_id=None)
        try:
            await ws.send_json(envelope.model_dump())
        except Exception:
            return


@asynccontextmanager
async def heartbeat(
    ws: WebSocket,
    *,
    settings: WebSocketSettings,
    max_message_bytes: int | None = None,
) -> AsyncIterator[Liveness]:
    """Keep an accepted socket honest for as long as the block runs.

    Args:
        ws (WebSocket): A socket that has already been accepted. This
            does not accept it — the handshake, including any auth or
            subprotocol negotiation, belongs to the caller.
        settings (WebSocketSettings): Supplies ``WS_HEARTBEAT_SECONDS``
            and ``WS_HEARTBEAT_TIMEOUT_SECONDS``.
        max_message_bytes (int | None): Reject inbound frames larger
            than this with ``1009``. ``None`` — the default — leaves
            frame size alone, since a size cap is a separate policy and
            an endpoint may already enforce its own.

    Yields:
        Liveness: The handle. Read ``interval_seconds`` to announce the
        cadence; call ``touch()`` to prove life out of band.

    On exit the ping task is cancelled and ``ws.receive`` is restored,
    so a socket that outlives the block behaves as it did before —
    which matters when the block is nested inside another that also
    wraps ``receive``.
    """
    live = Liveness(
        interval_seconds=settings.WS_HEARTBEAT_SECONDS,
        timeout_seconds=settings.WS_HEARTBEAT_TIMEOUT_SECONDS,
    )
    original = ws.receive
    _install_frame_guard(ws, live=live, max_message_bytes=max_message_bytes)
    task = asyncio.create_task(_beat(ws, live=live))
    try:
        yield live
    finally:
        task.cancel()
        ws.receive = original  # type: ignore[method-assign]


__all__: list[str] = ["HEARTBEAT_TIMEOUT_CODE", "Liveness", "heartbeat"]
