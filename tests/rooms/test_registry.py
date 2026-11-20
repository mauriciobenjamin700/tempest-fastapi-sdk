"""RoomRegistry: codes, slots, host, presence, reconnection, relay and limits."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tempest_fastapi_sdk.rooms import (
    BROADCAST_SLOT,
    REPLACED_CLOSE_CODE,
    ROOM_CODE_ALPHABET,
    MessageNotAllowedError,
    NotHostError,
    PlayerNotFoundError,
    ProtocolVersionError,
    RateLimitedError,
    ResumeRejectedError,
    RoomFullError,
    RoomIdentity,
    RoomInMatchError,
    RoomNotFoundError,
    RoomPolicy,
    RoomRegistry,
    ServerFullError,
    UnauthorizedError,
)


class FakePeer:
    """A connection that records what the server sent it."""

    def __init__(self) -> None:
        self.json: list[dict[str, Any]] = []
        self.bytes: list[bytes] = []
        self.closed: tuple[int, str] | None = None

    async def send_json(self, message: dict[str, Any], /) -> None:
        self.json.append(message)

    async def send_bytes(self, data: bytes, /) -> None:
        self.bytes.append(data)

    async def close(self, code: int, reason: str, /) -> None:
        self.closed = (code, reason)

    def types(self) -> list[str]:
        return [message["type"] for message in self.json]


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now: float = 100.0

    def __call__(self) -> float:
        return self.now


class TestCreateAndJoin:
    async def test_code_slots_and_host(self) -> None:
        registry = RoomRegistry()
        host, guest = FakePeer(), FakePeer()
        room, owner = await registry.create(host, name="Ana")
        assert len(room.code) == 4
        assert set(room.code) <= set(ROOM_CODE_ALPHABET)
        assert (owner.slot, room.host_id) == (0, owner.player_id)
        joined_room, member = await registry.join(room.code.lower(), guest, name="Bia")
        assert joined_room is room
        assert member.slot == 1
        assert host.json[-1] == {"type": "player_joined", "player": member.public()}

    async def test_name_is_cleaned_and_has_a_fallback(self) -> None:
        registry = RoomRegistry(RoomPolicy(max_name_length=5))
        room, owner = await registry.create(FakePeer(), name="  Ana\x00\n  Maria ")
        _, guest = await registry.join(room.code, FakePeer(), name="   ")
        assert owner.name == "Ana M"
        assert guest.name == "Player 2"

    async def test_unknown_code_is_refused(self) -> None:
        with pytest.raises(RoomNotFoundError):
            await RoomRegistry().join("ZZZZ", FakePeer())

    async def test_full_room_is_refused(self) -> None:
        registry = RoomRegistry(RoomPolicy(max_players=2))
        room, _ = await registry.create(FakePeer())
        await registry.join(room.code, FakePeer())
        with pytest.raises(RoomFullError):
            await registry.join(room.code, FakePeer())

    async def test_version_must_match(self) -> None:
        registry = RoomRegistry(RoomPolicy(protocol_version="6"))
        with pytest.raises(ProtocolVersionError, match="'6'"):
            await registry.create(FakePeer(), version="5")
        room, _ = await registry.create(FakePeer(), version="6")
        with pytest.raises(ProtocolVersionError):
            await registry.join(room.code, FakePeer())

    async def test_playing_room_refuses_new_players(self) -> None:
        registry = RoomRegistry()
        room, owner = await registry.create(FakePeer())
        await registry.set_state(room, owner, "playing")
        with pytest.raises(RoomInMatchError):
            await registry.join(room.code, FakePeer())

    async def test_unlocked_policy_lets_them_in(self) -> None:
        registry = RoomRegistry(RoomPolicy(lock_when_playing=False))
        room, owner = await registry.create(FakePeer())
        await registry.set_state(room, owner, "playing")
        _, member = await registry.join(room.code, FakePeer())
        assert member.slot == 1

    async def test_room_cap(self) -> None:
        registry = RoomRegistry(RoomPolicy(max_rooms=1))
        await registry.create(FakePeer())
        with pytest.raises(ServerFullError):
            await registry.create(FakePeer())


class TestHost:
    async def test_only_the_host_starts(self) -> None:
        registry = RoomRegistry()
        host = FakePeer()
        room, owner = await registry.create(host)
        _, member = await registry.join(room.code, FakePeer())
        with pytest.raises(NotHostError):
            await registry.set_state(room, member, "playing")
        await registry.set_state(room, owner, "playing")
        assert host.json[-1] == {"type": "room_state", "state": "playing"}

    async def test_host_leaving_hands_over_to_the_lowest_connected_slot(self) -> None:
        registry = RoomRegistry()
        room, owner = await registry.create(FakePeer())
        second_peer, third_peer = FakePeer(), FakePeer()
        _, second = await registry.join(room.code, second_peer)
        _, third = await registry.join(room.code, third_peer)
        await registry.disconnect(room, second, second_peer)
        await registry.leave(room, owner)
        assert room.host_id == third.player_id
        assert third_peer.json[-2:] == [
            {"type": "player_left", "player_id": owner.player_id, "slot": 0},
            {"type": "host_changed", "host_id": third.player_id},
        ]

    async def test_last_player_leaving_closes_the_room(self) -> None:
        registry = RoomRegistry()
        room, owner = await registry.create(FakePeer())
        await registry.leave(room, owner)
        assert registry.get(room.code) is None


class TestReconnection:
    async def test_dropped_seat_resumes_within_the_grace(self) -> None:
        registry = RoomRegistry(RoomPolicy(reconnect_grace_seconds=5))
        host = FakePeer()
        room, _ = await registry.create(host)
        old = FakePeer()
        _, member = await registry.join(room.code, old)
        await registry.disconnect(room, member, old)
        assert host.json[-1]["type"] == "player_disconnected"
        assert host.json[-1]["grace_seconds"] == 5
        new = FakePeer()
        _, resumed = await registry.resume(
            room.code, member.player_id, member.resume_token, new
        )
        assert resumed is member
        assert (resumed.peer, resumed.slot) == (new, 1)
        assert host.json[-1]["type"] == "player_reconnected"

    async def test_seat_is_freed_after_the_grace(self) -> None:
        registry = RoomRegistry(RoomPolicy(reconnect_grace_seconds=0.05))
        host = FakePeer()
        room, _ = await registry.create(host)
        peer = FakePeer()
        _, member = await registry.join(room.code, peer)
        await registry.disconnect(room, member, peer)
        await asyncio.sleep(0.15)
        assert member.player_id not in room.members
        assert host.json[-1] == {
            "type": "player_left",
            "player_id": member.player_id,
            "slot": 1,
        }
        with pytest.raises(ResumeRejectedError):
            await registry.resume(
                room.code, member.player_id, member.resume_token, FakePeer()
            )

    async def test_wrong_token_is_rejected(self) -> None:
        registry = RoomRegistry()
        room, owner = await registry.create(FakePeer())
        with pytest.raises(ResumeRejectedError):
            await registry.resume(room.code, owner.player_id, "guess", FakePeer())

    async def test_resume_over_a_live_connection_closes_the_old_one(self) -> None:
        registry = RoomRegistry()
        old = FakePeer()
        room, owner = await registry.create(old)
        await registry.resume(
            room.code, owner.player_id, owner.resume_token, FakePeer()
        )
        assert old.closed == (REPLACED_CLOSE_CODE, "replaced")

    async def test_late_drop_of_a_replaced_connection_is_ignored(self) -> None:
        registry = RoomRegistry()
        old, new = FakePeer(), FakePeer()
        room, owner = await registry.create(old)
        await registry.resume(room.code, owner.player_id, owner.resume_token, new)
        await registry.disconnect(room, owner, old)
        assert owner.peer is new

    async def test_zero_grace_removes_at_once(self) -> None:
        registry = RoomRegistry(RoomPolicy(reconnect_grace_seconds=0))
        room, _ = await registry.create(FakePeer())
        peer = FakePeer()
        _, member = await registry.join(room.code, peer)
        await registry.disconnect(room, member, peer)
        assert member.player_id not in room.members


class TestRelay:
    async def _room(
        self, registry: RoomRegistry, players: int = 3
    ) -> tuple[Any, list[Any], list[FakePeer]]:
        peers = [FakePeer() for _ in range(players)]
        room, owner = await registry.create(peers[0])
        members = [owner]
        for peer in peers[1:]:
            members.append((await registry.join(room.code, peer))[1])
        for peer in peers:
            peer.json.clear()
        return room, members, peers

    async def test_json_is_stamped_with_the_real_sender(self) -> None:
        registry = RoomRegistry()
        room, members, peers = await self._room(registry)
        await registry.relay_json(room, members[1], {"type": "hit", "from": "forged"})
        expected = {
            "type": "message",
            "from": members[1].player_id,
            "data": {"type": "hit", "from": "forged"},
        }
        assert peers[0].json == [expected]
        assert peers[2].json == [expected]
        assert peers[1].json == []

    async def test_json_to_one_player_reaches_only_them(self) -> None:
        registry = RoomRegistry()
        room, members, peers = await self._room(registry)
        await registry.relay_json(room, members[0], {"x": 1}, to=members[2].player_id)
        assert peers[1].json == []
        assert peers[2].json[0]["data"] == {"x": 1}

    async def test_json_to_a_stranger_is_refused(self) -> None:
        registry = RoomRegistry()
        room, members, _ = await self._room(registry)
        with pytest.raises(PlayerNotFoundError):
            await registry.relay_json(room, members[0], {}, to="nobody")

    async def test_allow_list(self) -> None:
        registry = RoomRegistry(
            RoomPolicy(allowed_message_types=frozenset({"battleSnapshot"}))
        )
        room, members, peers = await self._room(registry)
        await registry.relay_json(room, members[0], {"type": "battleSnapshot"})
        for refused in ({"type": "cheat"}, ["battleSnapshot"], "battleSnapshot"):
            with pytest.raises(MessageNotAllowedError):
                await registry.relay_json(room, members[0], refused)
        assert len(peers[1].json) == 1

    async def test_binary_broadcast_is_prefixed_with_the_sender_slot(self) -> None:
        registry = RoomRegistry()
        room, members, peers = await self._room(registry)
        await registry.relay_bytes(room, members[2], BROADCAST_SLOT, b"\x04\x00")
        assert peers[0].bytes == [b"\x02\x04\x00"]
        assert peers[1].bytes == [b"\x02\x04\x00"]
        assert peers[2].bytes == []

    async def test_binary_to_one_slot(self) -> None:
        registry = RoomRegistry()
        room, members, peers = await self._room(registry)
        await registry.relay_bytes(room, members[1], 0, b"in")
        assert peers[0].bytes == [b"\x01in"]
        assert peers[2].bytes == []
        with pytest.raises(PlayerNotFoundError):
            await registry.relay_bytes(room, members[1], 3, b"in")

    async def test_rate_limit_warns_once_per_second_and_drops(self) -> None:
        clock = Clock()
        registry = RoomRegistry(
            RoomPolicy(messages_per_second=10, burst=2), clock=clock
        )
        room, members, peers = await self._room(registry, players=2)
        sender = members[0]
        await registry.relay_bytes(room, sender, BROADCAST_SLOT, b"a")
        await registry.relay_bytes(room, sender, BROADCAST_SLOT, b"b")
        with pytest.raises(RateLimitedError):
            await registry.relay_bytes(room, sender, BROADCAST_SLOT, b"c")
        await registry.relay_bytes(room, sender, BROADCAST_SLOT, b"d")
        assert peers[1].bytes == [b"\x00a", b"\x00b"]
        clock.now += 0.2
        await registry.relay_bytes(room, sender, BROADCAST_SLOT, b"e")
        assert peers[1].bytes[-1] == b"\x00e"


class TestIdentity:
    async def test_resolver_sets_the_player_id_and_refuses_bad_tokens(self) -> None:
        async def resolve(token: str) -> RoomIdentity | None:
            return RoomIdentity("user-1", "Ana") if token == "good" else None

        registry = RoomRegistry(identity_resolver=resolve)
        assert registry.requires_identity
        for token in (None, "bad"):
            with pytest.raises(UnauthorizedError):
                await registry.identify(token)
        identity = await registry.identify("good")
        _, owner = await registry.create(FakePeer(), name="ignored", identity=identity)
        assert (owner.player_id, owner.name) == ("user-1", "Ana")

    async def test_same_account_joining_again_takes_its_seat_over(self) -> None:
        identity = RoomIdentity("user-2", "Bia")
        registry = RoomRegistry()
        room, _ = await registry.create(FakePeer())
        old, new = FakePeer(), FakePeer()
        _, first = await registry.join(room.code, old, identity=identity)
        _, second = await registry.join(room.code, new, identity=identity)
        assert second is first
        assert (second.peer, len(room.members)) == (new, 2)
        assert old.closed == (REPLACED_CLOSE_CODE, "replaced")


def test_policy_bounds() -> None:
    for kwargs in (
        {"max_players": 1},
        {"max_players": 255},
        {"reconnect_grace_seconds": -1},
        {"messages_per_second": 0},
        {"burst": 0},
        {"max_rooms": 0},
        {"code_length": 3},
        {"max_name_length": 0},
    ):
        with pytest.raises(ValueError):
            RoomPolicy(**kwargs)  # type: ignore[arg-type]
