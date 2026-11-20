"""Game rooms over raw TCP, for a client that does not speak WebSocket.

Same protocol as the WebSocket router, framed for a byte stream, because a
stream has no message boundaries and the room server must know where one
message ends to relay it whole::

    [u32 length, big-endian][u8 kind][payload: length bytes]

``kind`` :data:`FRAME_JSON` (``0``) carries a UTF-8 JSON control message;
``kind`` :data:`FRAME_BINARY` (``1``) carries a binary relay frame,
``[u8 slot][payload]``. ``length`` counts the payload only.

The server pings with ``{"type": "ping"}`` every ``heartbeat_seconds``; any
inbound frame proves the client alive (a ``{"type": "pong"}`` is swallowed),
and a client silent for ``heartbeat_timeout_seconds`` is closed without a
word. A raw stream has no close code, so a connection the server ends on
purpose — today only one whose player resumed on another connection — first
gets ``{"type": "close", "code": 4409, "reason": "replaced"}``. Sockets get
``TCP_NODELAY``: a lockstep game sends a small frame every tick, and Nagle's
algorithm would hold it back waiting for more.

This runs outside FastAPI, next to it (from the app's lifespan) or alone; it
has no TLS, so put it behind a TCP proxy that terminates TLS, or use it on a
network you trust.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
import struct
from typing import Any, Final

from tempest_fastapi_sdk.rooms.errors import BadRequestError
from tempest_fastapi_sdk.rooms.registry import RoomRegistry
from tempest_fastapi_sdk.rooms.session import RoomSession, hello_message

logger: logging.Logger = logging.getLogger(__name__)

FRAME_HEADER: Final[struct.Struct] = struct.Struct("!IB")
"""``[u32 payload length][u8 kind]``, big-endian."""

FRAME_JSON: Final[int] = 0
"""Frame kind of a UTF-8 JSON control message."""

FRAME_BINARY: Final[int] = 1
"""Frame kind of a binary relay frame."""

DEFAULT_MAX_FRAME_BYTES: Final[int] = 65_536
"""Largest payload :func:`serve_game_rooms_tcp` accepts by default."""


class FrameTooLargeError(ValueError):
    """A frame announced a payload above the server's limit."""


def encode_frame(kind: int, payload: bytes) -> bytes:
    """Frame one message for the TCP protocol.

    Args:
        kind (int): :data:`FRAME_JSON` or :data:`FRAME_BINARY`.
        payload (bytes): The message.

    Returns:
        bytes: ``header + payload``.
    """
    return FRAME_HEADER.pack(len(payload), kind) + payload


async def read_frame(
    reader: asyncio.StreamReader, *, max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES
) -> tuple[int, bytes]:
    """Read one frame.

    Args:
        reader (asyncio.StreamReader): The stream.
        max_frame_bytes (int): Largest payload accepted.

    Returns:
        tuple[int, bytes]: ``(kind, payload)``.

    Raises:
        asyncio.IncompleteReadError: When the stream ends mid-frame.
        FrameTooLargeError: When the header announces more than
            ``max_frame_bytes``; nothing more is read.
    """
    header = await reader.readexactly(FRAME_HEADER.size)
    length, kind = FRAME_HEADER.unpack(header)
    if length > max_frame_bytes:
        raise FrameTooLargeError(f"frame of {length} bytes over {max_frame_bytes}")
    return kind, await reader.readexactly(length)


class TCPRoomPeer:
    """A :class:`~tempest_fastapi_sdk.rooms.RoomPeer` over a TCP stream.

    Attributes:
        writer (asyncio.StreamWriter): The stream.
    """

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        """Wrap a stream.

        Args:
            writer (asyncio.StreamWriter): The stream.
        """
        self.writer: asyncio.StreamWriter = writer
        self._lock: asyncio.Lock = asyncio.Lock()

    async def _write(self, frame: bytes) -> None:
        """Write one frame whole, never interleaved with another.

        Args:
            frame (bytes): The framed message.
        """
        async with self._lock:
            self.writer.write(frame)
            await self.writer.drain()

    async def send_json(self, message: dict[str, Any], /) -> None:
        """Send one control message.

        Args:
            message (dict[str, Any]): The message.
        """
        payload = json.dumps(message, separators=(",", ":")).encode()
        await self._write(encode_frame(FRAME_JSON, payload))

    async def send_bytes(self, data: bytes, /) -> None:
        """Send one binary relay frame.

        Args:
            data (bytes): The frame.
        """
        await self._write(encode_frame(FRAME_BINARY, data))

    async def close(self, code: int, reason: str, /) -> None:
        """Say why, then close the stream.

        Sends ``{"type": "close", "code": code, "reason": reason}`` as a JSON
        frame, because a raw stream has no close code of its own, and then
        closes; a write failure is ignored, the stream closes anyway.

        Args:
            code (int): Close code, sent in a ``{"type": "close"}`` message.
            reason (str): Short reason.
        """
        with contextlib.suppress(Exception):
            await self.send_json({"type": "close", "code": code, "reason": reason})
        self.writer.close()


async def serve_game_rooms_tcp(
    registry: RoomRegistry,
    *,
    host: str = "127.0.0.1",
    port: int = 47_200,
    heartbeat_seconds: float = 15.0,
    heartbeat_timeout_seconds: float = 45.0,
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
) -> asyncio.Server:
    """Start a TCP game room server.

    Args:
        registry (RoomRegistry): The rooms; share it with
            :func:`~tempest_fastapi_sdk.rooms.make_game_room_router` to put
            TCP and WebSocket players in the same rooms.
        host (str): Interface to bind; ``"0.0.0.0"`` to accept phones on the
            network.
        port (int): TCP port; ``0`` picks a free one (read it from
            ``server.sockets[0].getsockname()``).
        heartbeat_seconds (float): How often the server pings.
        heartbeat_timeout_seconds (float): Silence tolerated before closing.
        max_frame_bytes (int): Largest payload accepted; a larger header
            closes the connection.

    Returns:
        asyncio.Server: The listening server; ``close()`` it on shutdown.

    Raises:
        ValueError: When the timeout is not above the heartbeat interval.
    """
    if heartbeat_timeout_seconds <= heartbeat_seconds:
        raise ValueError("heartbeat_timeout_seconds must exceed heartbeat_seconds")

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Serve one connection.

        Args:
            reader (asyncio.StreamReader): Inbound stream.
            writer (asyncio.StreamWriter): Outbound stream.
        """
        raw = writer.get_extra_info("socket")
        if raw is not None:
            with contextlib.suppress(OSError):
                raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        peer = TCPRoomPeer(writer)
        session = RoomSession(registry, peer)
        pinger = asyncio.create_task(_ping(peer, heartbeat_seconds))
        try:
            await peer.send_json(hello_message(registry, heartbeat_seconds))
            while True:
                kind, payload = await asyncio.wait_for(
                    read_frame(reader, max_frame_bytes=max_frame_bytes),
                    timeout=heartbeat_timeout_seconds,
                )
                await _dispatch(session, kind, payload)
        except (TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            pass
        except FrameTooLargeError as error:
            logger.info("closing TCP room connection: %s", error)
        finally:
            pinger.cancel()
            await session.close()
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    return await asyncio.start_server(handle, host, port)


async def _dispatch(session: RoomSession, kind: int, payload: bytes) -> None:
    """Hand one frame to the session.

    Args:
        session (RoomSession): The connection's session.
        kind (int): The frame kind.
        payload (bytes): The frame payload.
    """
    if kind == FRAME_BINARY:
        await session.handle_binary(payload)
        return
    if kind != FRAME_JSON:
        await session.refuse(BadRequestError(f"unknown frame kind {kind}"), None)
        return
    try:
        message = json.loads(payload.decode())
    except (UnicodeDecodeError, ValueError):
        await session.refuse(BadRequestError("frame is not UTF-8 JSON"), None)
        return
    if isinstance(message, dict) and message.get("type") == "pong":
        return
    await session.handle_json(message)


async def _ping(peer: TCPRoomPeer, interval: float) -> None:
    """Ping the client every ``interval`` seconds until cancelled.

    Args:
        peer (TCPRoomPeer): The connection.
        interval (float): Seconds between pings.
    """
    with contextlib.suppress(Exception):
        while True:
            await asyncio.sleep(interval)
            await peer.send_json({"type": "ping"})


__all__: list[str] = [
    "DEFAULT_MAX_FRAME_BYTES",
    "FRAME_BINARY",
    "FRAME_HEADER",
    "FRAME_JSON",
    "FrameTooLargeError",
    "TCPRoomPeer",
    "encode_frame",
    "read_frame",
    "serve_game_rooms_tcp",
]
