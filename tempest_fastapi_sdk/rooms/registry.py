"""Game rooms in memory: codes, slots, host, presence, reconnection and relay.

:class:`RoomRegistry` is the transport-agnostic core of the room server. It
never simulates a game: it groups players into rooms, tells them who the
host is, stamps every relayed message with the sender the server knows
(never one the client claims), and keeps a dropped player's seat for a grace
window so a phone that switched from Wi-Fi to 4G resumes in the same room.
The WebSocket router (:func:`~tempest_fastapi_sdk.rooms.make_game_room_router`)
and the TCP server (:func:`~tempest_fastapi_sdk.rooms.serve_game_rooms_tcp`)
are thin adapters around it.

State lives in this process, like
:class:`~tempest_fastapi_sdk.websockets.WebSocketHub`: every player of a room
must reach the same replica (one replica, or sticky sessions by room code).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import secrets
import time
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol

from tempest_fastapi_sdk.rooms.errors import (
    MessageNotAllowedError,
    NotHostError,
    PlayerNotFoundError,
    ProtocolVersionError,
    RateLimitedError,
    ResumeRejectedError,
    RoomFullError,
    RoomInMatchError,
    RoomNotFoundError,
    ServerFullError,
    UnauthorizedError,
)

logger: logging.Logger = logging.getLogger(__name__)

ROOM_CODE_ALPHABET: Final[str] = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
"""Characters a room code is drawn from: no ``0``/``O`` nor ``1``/``I``.

A code is read aloud and typed on a phone, so the look-alikes are out.
"""

BROADCAST_SLOT: Final[int] = 0xFF
"""Destination slot of a binary frame meant for every other player."""

REPLACED_CLOSE_CODE: Final[int] = 4409
"""Close code sent to a connection whose player resumed on another one."""

RoomState = Literal["lobby", "playing"]
"""``lobby`` takes new players; ``playing`` refuses them by default."""


@dataclass(frozen=True, slots=True)
class RoomPolicy:
    """The rules every room of a registry follows.

    Attributes:
        max_players (int): Slots per room, ``2`` to ``255``.
        protocol_version (str | None): Version every client must announce on
            ``create`` / ``join``; a mismatch is refused with
            ``version_mismatch``. ``None`` accepts any.
        reconnect_grace_seconds (float): How long a dropped player's seat is
            kept for a ``resume``; ``0`` removes it at once.
        messages_per_second (float): Sustained relay rate allowed per
            player, JSON and binary together (token bucket).
        burst (int): Messages a player may send at once above the rate.
        max_rooms (int): Rooms the registry holds at most.
        code_length (int): Characters in a room code, ``4`` to ``12``.
        lock_when_playing (bool): Refuse ``join`` while the room is
            ``playing``; a ``resume`` is always allowed.
        allowed_message_types (frozenset[str] | None): When set, a relayed
            JSON ``data`` must be an object whose ``type`` is listed here.
        max_name_length (int): Characters kept of a player name.

    Raises:
        ValueError: When a bound is out of range.
    """

    max_players: int = 4
    protocol_version: str | None = None
    reconnect_grace_seconds: float = 10.0
    messages_per_second: float = 120.0
    burst: int = 240
    max_rooms: int = 1000
    code_length: int = 4
    lock_when_playing: bool = True
    allowed_message_types: frozenset[str] | None = None
    max_name_length: int = 24

    def __post_init__(self) -> None:
        """Refuse bounds no room could follow.

        Raises:
            ValueError: When a bound is out of range.
        """
        if not 2 <= self.max_players < BROADCAST_SLOT:
            raise ValueError("max_players must be between 2 and 254")
        if self.reconnect_grace_seconds < 0:
            raise ValueError("reconnect_grace_seconds must not be negative")
        if self.messages_per_second <= 0 or self.burst < 1:
            raise ValueError("messages_per_second must be > 0 and burst >= 1")
        if self.max_rooms < 1:
            raise ValueError("max_rooms must be at least 1")
        if not 4 <= self.code_length <= 12:
            raise ValueError("code_length must be between 4 and 12")
        if self.max_name_length < 1:
            raise ValueError("max_name_length must be at least 1")


@dataclass(frozen=True, slots=True)
class RoomIdentity:
    """Who a connection is, as an identity resolver answers.

    Attributes:
        player_id (str): Stable player id (the user id of an account).
        name (str): Display name.
    """

    player_id: str
    name: str


IdentityResolver = Callable[[str], Awaitable[RoomIdentity | None]]
"""Turn a bearer token into an identity, or ``None`` when it is not valid."""


class RoomPeer(Protocol):
    """One live connection, whatever its transport."""

    async def send_json(self, message: dict[str, Any], /) -> None:
        """Send one control message.

        Args:
            message (dict[str, Any]): The message.
        """
        ...

    async def send_bytes(self, data: bytes, /) -> None:
        """Send one binary relay frame.

        Args:
            data (bytes): ``[u8 sender slot][payload]``.
        """
        ...

    async def close(self, code: int, reason: str, /) -> None:
        """Close the connection.

        Args:
            code (int): Close code.
            reason (str): Short reason.
        """
        ...


@dataclass(eq=False)
class RoomMember:
    """A player seated in a room.

    Attributes:
        player_id (str): Stable player id.
        name (str): Display name.
        slot (int): Seat number, ``0`` to ``max_players - 1``; addresses
            binary frames.
        resume_token (str): Secret a dropped connection presents to resume.
        peer (RoomPeer | None): The live connection; ``None`` while dropped.
        tokens (float): Token-bucket balance of the rate limit.
        refilled_at (float): Clock reading of the last refill.
        warned_at (float): Clock reading of the last ``rate_limited`` sent.
        grace_task (asyncio.Task[None] | None): Pending removal of a
            dropped player.
    """

    player_id: str
    name: str
    slot: int
    resume_token: str = field(repr=False)
    peer: RoomPeer | None = None
    tokens: float = 0.0
    refilled_at: float = 0.0
    warned_at: float = float("-inf")
    grace_task: asyncio.Task[None] | None = None

    @property
    def connected(self) -> bool:
        """Whether the player has a live connection.

        Returns:
            bool: ``True`` while ``peer`` is set.
        """
        return self.peer is not None

    def public(self) -> dict[str, Any]:
        """Describe the player for the other players.

        Returns:
            dict[str, Any]: ``player_id``, ``name``, ``slot``, ``connected``.
        """
        return {
            "player_id": self.player_id,
            "name": self.name,
            "slot": self.slot,
            "connected": self.connected,
        }


@dataclass(eq=False)
class Room:
    """One room.

    Attributes:
        code (str): The code players join with.
        max_players (int): Slots.
        members (dict[str, RoomMember]): Players by id.
        host_id (str | None): The host's player id.
        state (RoomState): ``lobby`` or ``playing``.
    """

    code: str
    max_players: int
    members: dict[str, RoomMember] = field(default_factory=dict)
    host_id: str | None = None
    state: RoomState = "lobby"

    def snapshot(self) -> dict[str, Any]:
        """Describe the room for its players.

        Returns:
            dict[str, Any]: ``code``, ``host_id``, ``state``,
            ``max_players`` and ``players`` by slot.
        """
        players = sorted(self.members.values(), key=lambda member: member.slot)
        return {
            "code": self.code,
            "host_id": self.host_id,
            "state": self.state,
            "max_players": self.max_players,
            "players": [member.public() for member in players],
        }

    def free_slot(self) -> int | None:
        """Return the lowest free slot.

        Returns:
            int | None: The slot, or ``None`` when the room is full.
        """
        taken = {member.slot for member in self.members.values()}
        for slot in range(self.max_players):
            if slot not in taken:
                return slot
        return None

    def by_slot(self, slot: int) -> RoomMember | None:
        """Return the player in a slot.

        Args:
            slot (int): The slot.

        Returns:
            RoomMember | None: The player, or ``None`` for an empty slot.
        """
        for member in self.members.values():
            if member.slot == slot:
                return member
        return None


_CONTROL: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f]")
"""Control characters stripped from a player name."""


class RoomRegistry:
    """Every room of one game server, in this process.

    Attributes:
        policy (RoomPolicy): The rules every room follows.
    """

    def __init__(
        self,
        policy: RoomPolicy | None = None,
        *,
        identity_resolver: IdentityResolver | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Build an empty registry.

        Args:
            policy (RoomPolicy | None): The rules; ``None`` uses the defaults.
            identity_resolver (IdentityResolver | None): Turns a bearer token
                into an identity. When set, every connection needs a valid
                token and its player id comes from it; ``None`` admits
                guests, each given a random player id.
            clock (Callable[[], float]): Monotonic time source for the rate
                limit (tests).
        """
        self.policy: RoomPolicy = policy or RoomPolicy()
        self._identity_resolver: IdentityResolver | None = identity_resolver
        self._clock: Callable[[], float] = clock
        self._rooms: dict[str, Room] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    @property
    def requires_identity(self) -> bool:
        """Whether connections must present a valid token.

        Returns:
            bool: ``True`` when an identity resolver was given.
        """
        return self._identity_resolver is not None

    @property
    def room_count(self) -> int:
        """Return how many rooms are open.

        Returns:
            int: Open rooms.
        """
        return len(self._rooms)

    def get(self, code: str) -> Room | None:
        """Return an open room.

        Args:
            code (str): The room code, any case.

        Returns:
            Room | None: The room, or ``None``.
        """
        return self._rooms.get(code.strip().upper())

    async def identify(self, token: str | None) -> RoomIdentity | None:
        """Resolve a connection's identity.

        Args:
            token (str | None): The bearer token the connection presented.

        Returns:
            RoomIdentity | None: The identity, or ``None`` for a guest when no
            resolver is configured.

        Raises:
            UnauthorizedError: When a resolver is configured and the token is
                missing or not valid.
        """
        if self._identity_resolver is None:
            return None
        identity = await self._identity_resolver(token) if token else None
        if identity is None:
            raise UnauthorizedError("a valid token is required")
        return identity

    def _check_version(self, version: str | None) -> None:
        """Refuse a client that speaks another protocol version.

        Args:
            version (str | None): What the client announced.

        Raises:
            ProtocolVersionError: When the policy fixes a version and the
                client announced another (or none).
        """
        expected = self.policy.protocol_version
        if expected is not None and version != expected:
            raise ProtocolVersionError(
                f"server speaks protocol {expected!r}, client sent {version!r}"
            )

    def _clean_name(self, name: str | None, slot: int) -> str:
        """Normalize a player name.

        Args:
            name (str | None): What the client sent.
            slot (int): The player's slot, for the fallback name.

        Returns:
            str: The name without control characters, whitespace collapsed,
            cut to ``max_name_length``; ``"Player <n>"`` when empty.
        """
        text = unicodedata.normalize("NFC", name or "")
        text = " ".join(_CONTROL.sub(" ", text).split())
        return text[: self.policy.max_name_length] or f"Player {slot + 1}"

    def _new_code(self) -> str:
        """Draw a code no open room uses.

        Returns:
            str: The code.
        """
        while True:
            code = "".join(
                secrets.choice(ROOM_CODE_ALPHABET)
                for _ in range(self.policy.code_length)
            )
            if code not in self._rooms:
                return code

    def _seat(
        self,
        room: Room,
        peer: RoomPeer,
        *,
        name: str | None,
        identity: RoomIdentity | None,
    ) -> RoomMember:
        """Seat a new player in a room (lock held).

        Args:
            room (Room): The room.
            peer (RoomPeer): The player's connection.
            name (str | None): The name the client sent.
            identity (RoomIdentity | None): The resolved identity, if any.

        Returns:
            RoomMember: The seated player.

        Raises:
            RoomFullError: When no slot is free.
        """
        slot = room.free_slot()
        if slot is None:
            raise RoomFullError(f"room {room.code} is full")
        player_id = identity.player_id if identity else secrets.token_hex(8)
        member = RoomMember(
            player_id=player_id,
            name=self._clean_name(identity.name if identity else name, slot),
            slot=slot,
            resume_token=secrets.token_urlsafe(24),
            peer=peer,
            tokens=float(self.policy.burst),
            refilled_at=self._clock(),
        )
        room.members[player_id] = member
        if room.host_id is None:
            room.host_id = player_id
        return member

    async def create(
        self,
        peer: RoomPeer,
        *,
        name: str | None = None,
        version: str | None = None,
        identity: RoomIdentity | None = None,
    ) -> tuple[Room, RoomMember]:
        """Open a room with the caller as host in slot ``0``.

        Args:
            peer (RoomPeer): The caller's connection.
            name (str | None): Display name, for a guest.
            version (str | None): Protocol version the client speaks.
            identity (RoomIdentity | None): Resolved identity, if any.

        Returns:
            tuple[Room, RoomMember]: The room and the caller's seat.

        Raises:
            ProtocolVersionError: On a version mismatch.
            ServerFullError: When ``max_rooms`` rooms are open.
        """
        self._check_version(version)
        async with self._lock:
            if len(self._rooms) >= self.policy.max_rooms:
                raise ServerFullError("the server holds its maximum of rooms")
            room = Room(code=self._new_code(), max_players=self.policy.max_players)
            self._rooms[room.code] = room
            member = self._seat(room, peer, name=name, identity=identity)
        return room, member

    async def join(
        self,
        code: str,
        peer: RoomPeer,
        *,
        name: str | None = None,
        version: str | None = None,
        identity: RoomIdentity | None = None,
    ) -> tuple[Room, RoomMember]:
        """Seat the caller in an open room.

        An identified player already seated there (a second connection of the
        same account) takes the seat over, as a resume would.

        Args:
            code (str): The room code, any case.
            peer (RoomPeer): The caller's connection.
            name (str | None): Display name, for a guest.
            version (str | None): Protocol version the client speaks.
            identity (RoomIdentity | None): Resolved identity, if any.

        Returns:
            tuple[Room, RoomMember]: The room and the caller's seat.

        Raises:
            ProtocolVersionError: On a version mismatch.
            RoomNotFoundError: When no room has that code.
            RoomInMatchError: When the room is playing and locks.
            RoomFullError: When no slot is free.
        """
        self._check_version(version)
        async with self._lock:
            room = self.get(code)
            if room is None:
                raise RoomNotFoundError(f"no room with code {code!r}")
            seated = room.members.get(identity.player_id) if identity else None
            if seated is not None:
                old = self._attach(seated, peer)
                joined = None
            else:
                if room.state == "playing" and self.policy.lock_when_playing:
                    raise RoomInMatchError(f"room {room.code} is playing")
                old = None
                joined = self._seat(room, peer, name=name, identity=identity)
        if old is not None:
            await _close_quietly(old, REPLACED_CLOSE_CODE, "replaced")
        if joined is None:
            assert seated is not None
            await self._emit(
                room,
                {"type": "player_reconnected", "player": seated.public()},
                exclude=seated,
            )
            return room, seated
        await self._emit(
            room, {"type": "player_joined", "player": joined.public()}, exclude=joined
        )
        return room, joined

    def _attach(self, member: RoomMember, peer: RoomPeer) -> RoomPeer | None:
        """Give a seat a new connection (lock held).

        Args:
            member (RoomMember): The seat.
            peer (RoomPeer): The new connection.

        Returns:
            RoomPeer | None: The connection it replaced, to close outside the
            lock.
        """
        if member.grace_task is not None:
            member.grace_task.cancel()
            member.grace_task = None
        old = member.peer if member.peer is not peer else None
        member.peer = peer
        return old

    async def resume(
        self, code: str, player_id: str, resume_token: str, peer: RoomPeer
    ) -> tuple[Room, RoomMember]:
        """Put a dropped (or replaced) player back in their seat.

        Args:
            code (str): The room code.
            player_id (str): The player id from ``welcome``.
            resume_token (str): The resume token from ``welcome``.
            peer (RoomPeer): The new connection.

        Returns:
            tuple[Room, RoomMember]: The room and the seat.

        Raises:
            ResumeRejectedError: When the room, the seat or the token does not
                match (the grace window may have run out).
        """
        async with self._lock:
            room = self.get(code)
            member = room.members.get(player_id) if room else None
            if (
                room is None
                or member is None
                or not secrets.compare_digest(member.resume_token, resume_token)
            ):
                raise ResumeRejectedError("no seat to resume")
            old = self._attach(member, peer)
        if old is not None:
            await _close_quietly(old, REPLACED_CLOSE_CODE, "replaced")
        await self._emit(
            room,
            {"type": "player_reconnected", "player": member.public()},
            exclude=member,
        )
        return room, member

    async def leave(self, room: Room, member: RoomMember) -> None:
        """Remove a player at once, freeing the slot.

        Args:
            room (Room): The room.
            member (RoomMember): The player.
        """
        async with self._lock:
            changed = self._remove(room, member)
        await self._announce_removal(room, member, changed)

    async def disconnect(self, room: Room, member: RoomMember, peer: RoomPeer) -> None:
        """Note that a connection dropped, keeping the seat for the grace window.

        Does nothing when ``peer`` is no longer the seat's connection (the
        player already resumed elsewhere).

        Args:
            room (Room): The room.
            member (RoomMember): The player.
            peer (RoomPeer): The connection that dropped.
        """
        grace = self.policy.reconnect_grace_seconds
        async with self._lock:
            if (
                member.peer is not peer
                or room.members.get(member.player_id) is not member
            ):
                return
            member.peer = None
            if grace <= 0:
                changed = self._remove(room, member)
            else:
                changed = None
                member.grace_task = asyncio.create_task(self._expire(room, member))
        if grace <= 0:
            await self._announce_removal(room, member, changed)
            return
        await self._emit(
            room,
            {
                "type": "player_disconnected",
                "player_id": member.player_id,
                "slot": member.slot,
                "grace_seconds": grace,
            },
        )

    async def _expire(self, room: Room, member: RoomMember) -> None:
        """Remove a dropped player once the grace window runs out.

        Args:
            room (Room): The room.
            member (RoomMember): The player.
        """
        await asyncio.sleep(self.policy.reconnect_grace_seconds)
        async with self._lock:
            if member.connected or room.members.get(member.player_id) is not member:
                return
            member.grace_task = None
            changed = self._remove(room, member)
        await self._announce_removal(room, member, changed)

    def _remove(self, room: Room, member: RoomMember) -> str | None:
        """Drop a seat, close an empty room, hand the host over (lock held).

        Args:
            room (Room): The room.
            member (RoomMember): The player.

        Returns:
            str | None: The new host's id when the host changed, else ``None``.
        """
        if room.members.get(member.player_id) is not member:
            return None
        del room.members[member.player_id]
        if member.grace_task is not None:
            member.grace_task.cancel()
            member.grace_task = None
        if not room.members:
            self._rooms.pop(room.code, None)
            return None
        if room.host_id != member.player_id:
            return None
        remaining = sorted(
            room.members.values(), key=lambda m: (not m.connected, m.slot)
        )
        room.host_id = remaining[0].player_id
        return room.host_id

    async def _announce_removal(
        self, room: Room, member: RoomMember, new_host: str | None
    ) -> None:
        """Tell the room a player left and, if so, who hosts now.

        Args:
            room (Room): The room.
            member (RoomMember): The player who left.
            new_host (str | None): The new host's id, if it changed.
        """
        await self._emit(
            room,
            {"type": "player_left", "player_id": member.player_id, "slot": member.slot},
        )
        if new_host is not None:
            await self._emit(room, {"type": "host_changed", "host_id": new_host})

    async def set_state(self, room: Room, member: RoomMember, state: RoomState) -> None:
        """Move the room between ``lobby`` and ``playing`` (host only).

        Args:
            room (Room): The room.
            member (RoomMember): The caller.
            state (RoomState): The new state.

        Raises:
            NotHostError: When the caller is not the host.
        """
        async with self._lock:
            if room.host_id != member.player_id:
                raise NotHostError("only the host may start or end a match")
            room.state = state
        await self._emit(room, {"type": "room_state", "state": state})

    def _take_token(self, member: RoomMember) -> bool:
        """Spend one token of a player's rate limit.

        Args:
            member (RoomMember): The sender.

        Returns:
            bool: ``True`` when the message may go out.
        """
        now = self._clock()
        member.tokens = min(
            float(self.policy.burst),
            member.tokens
            + (now - member.refilled_at) * self.policy.messages_per_second,
        )
        member.refilled_at = now
        if member.tokens < 1.0:
            return False
        member.tokens -= 1.0
        return True

    def _check_rate(self, member: RoomMember) -> None:
        """Refuse a message over the rate limit.

        The ``rate_limited`` error is raised at most once per second per
        player; the messages in between are dropped without a reply, so a
        flooding client does not turn the server into a flood of errors.

        Args:
            member (RoomMember): The sender.

        Raises:
            RateLimitedError: When the message is over the limit and no error
                went out in the last second.
            _DroppedError: When over the limit and an error already went out.
        """
        if self._take_token(member):
            return
        now = self._clock()
        if now - member.warned_at >= 1.0:
            member.warned_at = now
            raise RateLimitedError("too many messages; slow down")
        raise _DroppedError

    async def relay_json(
        self, room: Room, member: RoomMember, data: object, *, to: str | None = None
    ) -> None:
        """Relay a JSON message, stamped with the sender the server knows.

        Delivered as ``{"type": "message", "from": <player_id>, "data": ...}``
        to every other connected player, or only to ``to``.

        Args:
            room (Room): The room.
            member (RoomMember): The sender.
            data (object): The payload, relayed as is.
            to (str | None): A player id, or ``None`` for everyone else.

        Raises:
            RateLimitedError: Over the rate limit.
            MessageNotAllowedError: When an allow list is set and ``data`` is
                not an object with an allowed ``type``.
            PlayerNotFoundError: When ``to`` is not in the room.
        """
        allowed = self.policy.allowed_message_types
        if allowed is not None and not (
            isinstance(data, dict) and data.get("type") in allowed
        ):
            raise MessageNotAllowedError("message type not allowed")
        try:
            self._check_rate(member)
        except _DroppedError:
            return
        message = {"type": "message", "from": member.player_id, "data": data}
        if to is None:
            await self._emit(room, message, exclude=member)
            return
        target = room.members.get(to)
        if target is None:
            raise PlayerNotFoundError(f"no player {to!r} in the room")
        if target.peer is not None:
            await _send_quietly(target.peer, message)

    async def relay_bytes(
        self, room: Room, member: RoomMember, slot: int, payload: bytes
    ) -> None:
        """Relay a binary frame, prefixed with the sender's slot.

        Args:
            room (Room): The room.
            member (RoomMember): The sender.
            slot (int): Destination slot, or :data:`BROADCAST_SLOT`.
            payload (bytes): Opaque bytes, relayed as is.

        Raises:
            RateLimitedError: Over the rate limit.
            PlayerNotFoundError: When ``slot`` is empty.
        """
        try:
            self._check_rate(member)
        except _DroppedError:
            return
        frame = bytes([member.slot]) + payload
        if slot == BROADCAST_SLOT:
            targets = [
                m.peer
                for m in room.members.values()
                if m is not member and m.peer is not None
            ]
            await asyncio.gather(
                *(_send_bytes_quietly(peer, frame) for peer in targets)
            )
            return
        target = room.by_slot(slot)
        if target is None:
            raise PlayerNotFoundError(f"slot {slot} is empty")
        if target.peer is not None:
            await _send_bytes_quietly(target.peer, frame)

    async def _emit(
        self, room: Room, message: dict[str, Any], *, exclude: RoomMember | None = None
    ) -> None:
        """Send a message to every connected player of a room.

        Args:
            room (Room): The room.
            message (dict[str, Any]): The message.
            exclude (RoomMember | None): A player to skip.
        """
        targets = [
            m.peer
            for m in list(room.members.values())
            if m is not exclude and m.peer is not None
        ]
        await asyncio.gather(*(_send_quietly(peer, message) for peer in targets))


class _DroppedError(Exception):
    """A message over the rate limit, dropped without a reply."""


async def _send_quietly(peer: RoomPeer, message: dict[str, Any]) -> None:
    """Send a control message, ignoring a connection that already died.

    A dead connection is noticed by its own read loop (or the heartbeat),
    which reports the drop; failing here would let one dead player stop a
    broadcast to the others.

    Args:
        peer (RoomPeer): The connection.
        message (dict[str, Any]): The message.
    """
    try:
        await peer.send_json(message)
    except Exception:
        logger.debug("room send failed", exc_info=True)


async def _send_bytes_quietly(peer: RoomPeer, data: bytes) -> None:
    """Send a binary frame, ignoring a connection that already died.

    Args:
        peer (RoomPeer): The connection.
        data (bytes): The frame.
    """
    try:
        await peer.send_bytes(data)
    except Exception:
        logger.debug("room send failed", exc_info=True)


async def _close_quietly(peer: RoomPeer, code: int, reason: str) -> None:
    """Close a replaced connection, ignoring one that already died.

    Args:
        peer (RoomPeer): The connection.
        code (int): Close code.
        reason (str): Short reason.
    """
    with contextlib.suppress(Exception):
        await peer.close(code, reason)


__all__: list[str] = [
    "BROADCAST_SLOT",
    "REPLACED_CLOSE_CODE",
    "ROOM_CODE_ALPHABET",
    "IdentityResolver",
    "Room",
    "RoomIdentity",
    "RoomMember",
    "RoomPeer",
    "RoomPolicy",
    "RoomRegistry",
    "RoomState",
]
