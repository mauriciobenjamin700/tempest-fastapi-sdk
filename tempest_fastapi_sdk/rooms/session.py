"""The room protocol, shared by the WebSocket router and the TCP server.

One :class:`RoomSession` per connection. It reads control messages (JSON
objects with a ``type``) and binary relay frames, drives the
:class:`~tempest_fastapi_sdk.rooms.RoomRegistry`, and answers a refusal with
``{"type": "error", "code": ..., "message": ..., "request": <type>}``
instead of dropping the connection (``code`` is stable, ``message`` is for
humans, ``request`` is the refused ``type`` or ``None``).

Client → server
    ``create {name?, version?, token?}``, ``join {code, name?, version?,
    token?}``, ``resume {code, player_id, resume_token}``, ``send {data,
    to?}``, ``start``, ``end``, ``leave``, ``echo {t?}``; binary frames
    ``[u8 destination slot | 0xFF for everyone else][payload]``.

Server → client
    ``hello``, ``welcome {player_id, resume_token, slot, resumed, room}``,
    ``left {code}`` (reply to ``leave``), ``player_joined``,
    ``player_left``, ``player_disconnected``, ``player_reconnected``,
    ``host_changed {host_id}``, ``room_state {state}``, ``message {from,
    data}``, ``echo {t}``, ``error {code, message, request}``, ``ping``;
    binary frames ``[u8 sender slot][payload]``. Over TCP, a connection
    replaced by a ``resume`` elsewhere also gets ``close {code, reason}``
    before the stream ends (WebSocket uses the close code instead).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from tempest_fastapi_sdk.rooms.errors import (
    AlreadyInRoomError,
    BadRequestError,
    NotInRoomError,
    RoomError,
)
from tempest_fastapi_sdk.rooms.registry import (
    Room,
    RoomIdentity,
    RoomMember,
    RoomPeer,
    RoomRegistry,
)


def hello_message(registry: RoomRegistry, heartbeat_seconds: float) -> dict[str, Any]:
    """Build the first message a connection receives.

    Args:
        registry (RoomRegistry): The server's registry.
        heartbeat_seconds (float): How often the server pings; the client's
            own watchdog should allow a few of these.

    Returns:
        dict[str, Any]: ``{"type": "hello", ...}`` with the protocol version,
        the room size, the grace window and the heartbeat cadence.
    """
    policy = registry.policy
    return {
        "type": "hello",
        "protocol_version": policy.protocol_version,
        "max_players": policy.max_players,
        "reconnect_grace_seconds": policy.reconnect_grace_seconds,
        "heartbeat_seconds": heartbeat_seconds,
        "requires_identity": registry.requires_identity,
    }


_CLOSING: set[asyncio.Future[None]] = set()
"""Disconnect tasks still running after their caller was cancelled."""


class RoomSession:
    """The protocol state of one connection.

    Attributes:
        registry (RoomRegistry): The server's registry.
        peer (RoomPeer): The connection.
        room (Room | None): The room the connection is in.
        member (RoomMember | None): The connection's seat.
    """

    def __init__(
        self,
        registry: RoomRegistry,
        peer: RoomPeer,
        *,
        identity: RoomIdentity | None = None,
    ) -> None:
        """Start a session.

        Args:
            registry (RoomRegistry): The server's registry.
            peer (RoomPeer): The connection.
            identity (RoomIdentity | None): An identity already resolved at
                the handshake (the WebSocket ``?token=``); ``None`` resolves
                the ``token`` field of ``create`` / ``join`` when the registry
                requires one.
        """
        self.registry: RoomRegistry = registry
        self.peer: RoomPeer = peer
        self.room: Room | None = None
        self.member: RoomMember | None = None
        self._identity: RoomIdentity | None = identity

    async def handle_text(self, raw: str) -> None:
        """Handle one text frame.

        Args:
            raw (str): The frame, expected to be a JSON object.
        """
        try:
            message = json.loads(raw)
        except ValueError:
            await self.refuse(BadRequestError("frame is not JSON"), None)
            return
        await self.handle_json(message)

    async def handle_json(self, message: Any) -> None:
        """Handle one decoded control message.

        Args:
            message (Any): The decoded frame.
        """
        kind = message.get("type") if isinstance(message, dict) else None
        if not isinstance(kind, str):
            await self.refuse(BadRequestError("message needs a string 'type'"), None)
            return
        handler = _HANDLERS.get(kind)
        if handler is None:
            await self.refuse(BadRequestError(f"unknown type {kind!r}"), kind)
            return
        try:
            await handler(self, message)
        except RoomError as error:
            await self.refuse(error, kind)

    async def handle_binary(self, data: bytes) -> None:
        """Handle one binary relay frame.

        Args:
            data (bytes): ``[u8 destination slot][payload]``.
        """
        try:
            room, member = self._seated()
            if not data:
                raise BadRequestError("binary frame needs a destination slot")
            await self.registry.relay_bytes(room, member, data[0], data[1:])
        except RoomError as error:
            await self.refuse(error, "binary")

    async def close(self) -> None:
        """Report that the connection dropped (the seat waits for a resume).

        The bookkeeping runs in its own task behind :func:`asyncio.shield`,
        because transports call this from a ``finally`` that is often
        reached by cancellation (server shutdown, a test client tearing the
        handler down right after the disconnect). Awaited inline, the
        cancellation interrupted it before ``player_disconnected`` went out,
        and the seat stayed marked connected to a dead peer.
        """
        if self.room is None or self.member is None:
            return
        task = asyncio.ensure_future(
            self.registry.disconnect(self.room, self.member, self.peer)
        )
        _CLOSING.add(task)
        task.add_done_callback(_CLOSING.discard)
        await asyncio.shield(task)

    def _seated(self) -> tuple[Room, RoomMember]:
        """Return the session's room and seat.

        Returns:
            tuple[Room, RoomMember]: The room and the seat.

        Raises:
            NotInRoomError: When the connection is not in a room.
        """
        if self.room is None or self.member is None:
            raise NotInRoomError("create, join or resume a room first")
        return self.room, self.member

    def _not_seated(self) -> None:
        """Refuse a second room on the same connection.

        Raises:
            AlreadyInRoomError: When the connection is already in a room.
        """
        if self.room is not None:
            raise AlreadyInRoomError("leave the current room first")

    async def _identity_for(self, message: dict[str, Any]) -> RoomIdentity | None:
        """Return the connection's identity, resolving the message's token.

        Args:
            message (dict[str, Any]): A ``create`` or ``join``.

        Returns:
            RoomIdentity | None: The identity, or ``None`` for a guest.

        Raises:
            UnauthorizedError: When a token is required and not valid.
        """
        if self._identity is None and self.registry.requires_identity:
            token = message.get("token")
            self._identity = await self.registry.identify(
                token if isinstance(token, str) else None
            )
        return self._identity

    async def _welcome(self, room: Room, member: RoomMember, *, resumed: bool) -> None:
        """Bind the seat to the session and greet the player.

        Args:
            room (Room): The room.
            member (RoomMember): The seat.
            resumed (bool): Whether this is a resume.
        """
        self.room = room
        self.member = member
        await self.peer.send_json(
            {
                "type": "welcome",
                "player_id": member.player_id,
                "resume_token": member.resume_token,
                "slot": member.slot,
                "resumed": resumed,
                "room": room.snapshot(),
            }
        )

    async def _create(self, message: dict[str, Any]) -> None:
        """Handle ``create``.

        Args:
            message (dict[str, Any]): The message.
        """
        self._not_seated()
        identity = await self._identity_for(message)
        room, member = await self.registry.create(
            self.peer,
            name=_text(message.get("name")),
            version=_text(message.get("version")),
            identity=identity,
        )
        await self._welcome(room, member, resumed=False)

    async def _join(self, message: dict[str, Any]) -> None:
        """Handle ``join``.

        Args:
            message (dict[str, Any]): The message.

        Raises:
            BadRequestError: When ``code`` is missing.
        """
        self._not_seated()
        code = _text(message.get("code"))
        if not code:
            raise BadRequestError("join needs a 'code'")
        identity = await self._identity_for(message)
        room, member = await self.registry.join(
            code,
            self.peer,
            name=_text(message.get("name")),
            version=_text(message.get("version")),
            identity=identity,
        )
        await self._welcome(room, member, resumed=False)

    async def _resume(self, message: dict[str, Any]) -> None:
        """Handle ``resume``.

        Args:
            message (dict[str, Any]): The message.

        Raises:
            BadRequestError: When a field is missing.
        """
        self._not_seated()
        code = _text(message.get("code"))
        player_id = _text(message.get("player_id"))
        token = _text(message.get("resume_token"))
        if not code or not player_id or not token:
            raise BadRequestError("resume needs 'code', 'player_id', 'resume_token'")
        room, member = await self.registry.resume(code, player_id, token, self.peer)
        await self._welcome(room, member, resumed=True)

    async def _send(self, message: dict[str, Any]) -> None:
        """Handle ``send``.

        Args:
            message (dict[str, Any]): The message.

        Raises:
            BadRequestError: When ``data`` is missing or ``to`` not a string.
        """
        room, member = self._seated()
        if "data" not in message:
            raise BadRequestError("send needs 'data'")
        to = message.get("to")
        if to is not None and not isinstance(to, str):
            raise BadRequestError("'to' must be a player id")
        await self.registry.relay_json(room, member, message["data"], to=to)

    async def _start(self, message: dict[str, Any]) -> None:
        """Handle ``start`` (host only).

        Args:
            message (dict[str, Any]): The message.
        """
        room, member = self._seated()
        await self.registry.set_state(room, member, "playing")

    async def _end(self, message: dict[str, Any]) -> None:
        """Handle ``end`` (host only).

        Args:
            message (dict[str, Any]): The message.
        """
        room, member = self._seated()
        await self.registry.set_state(room, member, "lobby")

    async def _leave(self, message: dict[str, Any]) -> None:
        """Handle ``leave``: free the seat now, keep the connection.

        Args:
            message (dict[str, Any]): The message.
        """
        room, member = self._seated()
        self.room = None
        self.member = None
        await self.registry.leave(room, member)
        await self.peer.send_json({"type": "left", "code": room.code})

    async def _echo(self, message: dict[str, Any]) -> None:
        """Handle ``echo``: answer at once, for the client to measure RTT.

        Args:
            message (dict[str, Any]): The message; ``t`` is returned as is.
        """
        await self.peer.send_json({"type": "echo", "t": message.get("t")})

    async def refuse(self, error: RoomError, request: str | None) -> None:
        """Answer a refusal without closing the connection.

        Args:
            error (RoomError): The refusal.
            request (str | None): The ``type`` that was refused.
        """
        await self.peer.send_json(
            {
                "type": "error",
                "code": error.code,
                "message": error.message,
                "request": request,
            }
        )


def _text(value: Any) -> str | None:
    """Return a string field, or ``None`` when it is not a string.

    Args:
        value (Any): The field.

    Returns:
        str | None: The string, or ``None``.
    """
    return value if isinstance(value, str) else None


_HANDLERS: dict[str, Callable[[RoomSession, dict[str, Any]], Awaitable[None]]] = {
    "create": RoomSession._create,
    "join": RoomSession._join,
    "resume": RoomSession._resume,
    "send": RoomSession._send,
    "start": RoomSession._start,
    "end": RoomSession._end,
    "leave": RoomSession._leave,
    "echo": RoomSession._echo,
}
"""Control ``type`` to the session method that handles it."""


__all__: list[str] = ["RoomSession", "hello_message"]
