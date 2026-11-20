"""make_game_room_router: the room protocol over a real FastAPI WebSocket."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tempest_fastapi_sdk.rooms import (
    BROADCAST_SLOT,
    UNAUTHORIZED_CLOSE_CODE,
    RoomIdentity,
    RoomPolicy,
    RoomRegistry,
    make_game_room_router,
)
from tempest_fastapi_sdk.settings import WebSocketSettings

SETTINGS: WebSocketSettings = WebSocketSettings(  # type: ignore[call-arg]
    _env_file=None,
    WS_HEARTBEAT_SECONDS=30,
    WS_HEARTBEAT_TIMEOUT_SECONDS=60,
    WS_MAX_MESSAGE_BYTES=1024,
)
"""A heartbeat slow enough to stay out of these tests' way."""


def _client(registry: RoomRegistry) -> TestClient:
    app = FastAPI()
    app.include_router(make_game_room_router(registry, settings=SETTINGS))
    return TestClient(app)


def _until(ws: Any, kind: str) -> dict[str, Any]:
    """Read control messages until one of ``kind`` arrives."""
    while True:
        message = ws.receive_json()
        if message["type"] == kind:
            return message


class TestPlay:
    def test_hello_then_create_join_and_relay_both_ways(self) -> None:
        registry = RoomRegistry(RoomPolicy(protocol_version="6", max_players=4))
        with (
            _client(registry) as client,
            client.websocket_connect("/rooms/ws") as host,
            client.websocket_connect("/rooms/ws") as guest,
        ):
            hello = host.receive_json()
            assert hello["type"] == "hello"
            assert (hello["protocol_version"], hello["max_players"]) == ("6", 4)
            guest.receive_json()
            host.send_json({"type": "create", "name": "Ana", "version": "6"})
            welcome = _until(host, "welcome")
            code = welcome["room"]["code"]
            assert welcome["slot"] == 0
            guest.send_json(
                {"type": "join", "code": code, "name": "Bia", "version": "6"}
            )
            joined = _until(guest, "welcome")
            assert joined["slot"] == 1
            assert _until(host, "player_joined")["player"]["name"] == "Bia"

            guest.send_bytes(bytes([BROADCAST_SLOT]) + b"\x00\x00\x00\x07\x00\x10")
            assert host.receive_bytes() == b"\x01\x00\x00\x00\x07\x00\x10"
            host.send_bytes(b"\x01seed")
            assert guest.receive_bytes() == b"\x00seed"

            host.send_json({"type": "send", "data": {"type": "battleStart"}})
            assert _until(guest, "message") == {
                "type": "message",
                "from": welcome["player_id"],
                "data": {"type": "battleStart"},
            }

    def test_echo_answers_with_the_same_t(self) -> None:
        with (
            _client(RoomRegistry()) as client,
            client.websocket_connect("/rooms/ws") as ws,
        ):
            ws.receive_json()
            ws.send_json({"type": "echo", "t": 12345.5})
            assert ws.receive_json() == {"type": "echo", "t": 12345.5}

    def test_refusals_keep_the_connection_open(self) -> None:
        registry = RoomRegistry(RoomPolicy(protocol_version="6"))
        with (
            _client(registry) as client,
            client.websocket_connect("/rooms/ws") as ws,
        ):
            ws.receive_json()
            ws.send_text("not json")
            assert ws.receive_json()["code"] == "bad_request"
            ws.send_json({"type": "send", "data": {}})
            assert ws.receive_json()["code"] == "not_in_room"
            ws.send_bytes(b"\xffhi")
            assert ws.receive_json()["code"] == "not_in_room"
            ws.send_json({"type": "join", "code": "ZZZZ", "version": "6"})
            assert ws.receive_json()["code"] == "room_not_found"
            ws.send_json({"type": "create", "version": "5"})
            refusal = ws.receive_json()
            assert (refusal["code"], refusal["request"]) == (
                "version_mismatch",
                "create",
            )
            ws.send_json({"type": "create", "version": "6"})
            assert _until(ws, "welcome")["slot"] == 0
            ws.send_json({"type": "create", "version": "6"})
            assert ws.receive_json()["code"] == "already_in_room"

    def test_oversized_frame_is_closed_with_1009(self) -> None:
        with (
            _client(RoomRegistry()) as client,
            client.websocket_connect("/rooms/ws") as ws,
        ):
            ws.receive_json()
            ws.send_bytes(b"\xff" + b"x" * 2048)
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_bytes()
        assert closed.value.code == 1009


class TestReconnect:
    def test_dropped_player_resumes_in_the_same_slot(self) -> None:
        registry = RoomRegistry(RoomPolicy(reconnect_grace_seconds=30))
        with _client(registry) as client, client.websocket_connect("/rooms/ws") as host:
            host.receive_json()
            host.send_json({"type": "create"})
            code = _until(host, "welcome")["room"]["code"]
            with client.websocket_connect("/rooms/ws") as guest:
                guest.receive_json()
                guest.send_json({"type": "join", "code": code})
                seat = _until(guest, "welcome")
            dropped = _until(host, "player_disconnected")
            assert (dropped["player_id"], dropped["slot"]) == (seat["player_id"], 1)
            with client.websocket_connect("/rooms/ws") as again:
                again.receive_json()
                again.send_json(
                    {
                        "type": "resume",
                        "code": code,
                        "player_id": seat["player_id"],
                        "resume_token": seat["resume_token"],
                    }
                )
                resumed = _until(again, "welcome")
                assert (resumed["resumed"], resumed["slot"]) == (True, 1)
                assert (
                    _until(host, "player_reconnected")["player"]["player_id"]
                    == (seat["player_id"])
                )


class TestIdentity:
    def _registry(self) -> RoomRegistry:
        async def resolve(token: str) -> RoomIdentity | None:
            return RoomIdentity("user-9", "Ana") if token == "good" else None

        return RoomRegistry(identity_resolver=resolve)

    def test_missing_or_bad_token_is_closed_with_4401(self) -> None:
        with _client(self._registry()) as client:
            for url in ("/rooms/ws", "/rooms/ws?token=bad"):
                with (
                    pytest.raises(WebSocketDisconnect) as closed,
                    client.websocket_connect(url) as ws,
                ):
                    ws.receive_json()
                assert closed.value.code == UNAUTHORIZED_CLOSE_CODE

    def test_good_token_names_the_player(self) -> None:
        with (
            _client(self._registry()) as client,
            client.websocket_connect("/rooms/ws?token=good") as ws,
        ):
            assert ws.receive_json()["requires_identity"] is True
            ws.send_json({"type": "create", "name": "forged"})
            welcome = _until(ws, "welcome")
        assert welcome["player_id"] == "user-9"
        assert welcome["room"]["players"][0]["name"] == "Ana"


class TestJwtIdentity:
    """``make_jwt_room_identity_resolver`` admits access tokens only."""

    async def test_access_token_maps_to_an_identity(self) -> None:
        from tempest_fastapi_sdk import ACCESS_TOKEN_TYPE, JWTUtils
        from tempest_fastapi_sdk.rooms import make_jwt_room_identity_resolver

        tokens = JWTUtils(secret="s" * 32)
        resolve = make_jwt_room_identity_resolver(tokens)
        user = "11111111-1111-1111-1111-111111111111"
        named = tokens.encode({"sub": user, "typ": ACCESS_TOKEN_TYPE, "name": "Ana"})
        assert await resolve(named) == RoomIdentity(user, "Ana")
        unnamed = tokens.encode({"sub": user, "typ": ACCESS_TOKEN_TYPE})
        assert await resolve(unnamed) == RoomIdentity(user, "")

    @pytest.mark.parametrize("token_type", ["refresh", "mfa", None])
    async def test_other_token_types_are_refused(self, token_type: str | None) -> None:
        from tempest_fastapi_sdk import MFA_TOKEN_TYPE, REFRESH_TOKEN_TYPE, JWTUtils
        from tempest_fastapi_sdk.rooms import make_jwt_room_identity_resolver

        tokens = JWTUtils(secret="s" * 32)
        typ = {"refresh": REFRESH_TOKEN_TYPE, "mfa": MFA_TOKEN_TYPE}.get(
            token_type or ""
        )
        claims: dict[str, Any] = {"sub": "11111111-1111-1111-1111-111111111111"}
        if typ is not None:
            claims["typ"] = typ
        resolve = make_jwt_room_identity_resolver(tokens)
        assert await resolve(tokens.encode(claims)) is None
        assert await resolve("not-a-jwt") is None
