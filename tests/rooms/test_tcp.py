"""serve_game_rooms_tcp: framing, relay, heartbeat, and TCP + WebSocket together."""

from __future__ import annotations

import asyncio
import json
import socket
import struct
from collections.abc import AsyncIterator
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from websockets.asyncio.client import connect

from tempest_fastapi_sdk.rooms import (
    BROADCAST_SLOT,
    FRAME_BINARY,
    FRAME_JSON,
    RoomIdentity,
    RoomPolicy,
    RoomRegistry,
    encode_frame,
    make_game_room_router,
    read_frame,
    serve_game_rooms_tcp,
)
from tempest_fastapi_sdk.settings import WebSocketSettings


class TcpPlayer:
    """A raw TCP client speaking the framed room protocol."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.reader = reader
        self.writer = writer

    @classmethod
    async def open(cls, port: int) -> TcpPlayer:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        return cls(reader, writer)

    async def send(self, message: dict[str, Any]) -> None:
        self.writer.write(encode_frame(FRAME_JSON, json.dumps(message).encode()))
        await self.writer.drain()

    async def send_bytes(self, data: bytes) -> None:
        self.writer.write(encode_frame(FRAME_BINARY, data))
        await self.writer.drain()

    async def frame(self) -> tuple[int, Any]:
        kind, payload = await asyncio.wait_for(read_frame(self.reader), timeout=5)
        return kind, json.loads(payload) if kind == FRAME_JSON else payload

    async def until(self, kind: str) -> dict[str, Any]:
        while True:
            frame_kind, message = await self.frame()
            if frame_kind == FRAME_JSON and message["type"] == kind:
                return dict(message)

    async def close(self) -> None:
        self.writer.close()
        await self.writer.wait_closed()


@pytest.fixture
async def tcp_port() -> AsyncIterator[tuple[RoomRegistry, int]]:
    registry = RoomRegistry(RoomPolicy(protocol_version="6", reconnect_grace_seconds=5))
    server = await serve_game_rooms_tcp(
        registry, port=0, heartbeat_seconds=0.2, heartbeat_timeout_seconds=0.6
    )
    port = server.sockets[0].getsockname()[1]
    try:
        yield registry, port
    finally:
        server.close()
        await server.wait_closed()


class TestTcp:
    async def test_hello_create_join_and_binary_relay(
        self, tcp_port: tuple[RoomRegistry, int]
    ) -> None:
        _, port = tcp_port
        host, guest = await TcpPlayer.open(port), await TcpPlayer.open(port)
        hello = await host.until("hello")
        assert (hello["protocol_version"], hello["heartbeat_seconds"]) == ("6", 0.2)
        await host.send({"type": "create", "name": "Ana", "version": "6"})
        code = (await host.until("welcome"))["room"]["code"]
        await guest.send({"type": "join", "code": code, "version": "6"})
        assert (await guest.until("welcome"))["slot"] == 1
        await guest.send_bytes(bytes([BROADCAST_SLOT]) + struct.pack("!ih", 7, 16))
        while True:
            kind, payload = await host.frame()
            if kind == FRAME_BINARY:
                break
        assert payload == b"\x01" + struct.pack("!ih", 7, 16)
        await host.close()
        await guest.close()

    async def test_server_pings_and_drops_a_silent_client(
        self, tcp_port: tuple[RoomRegistry, int]
    ) -> None:
        registry, port = tcp_port
        player = await TcpPlayer.open(port)
        await player.send({"type": "create", "version": "6"})
        welcome = await player.until("welcome")
        assert (await player.until("ping")) == {"type": "ping"}
        await asyncio.sleep(1.0)
        room = registry.get(welcome["room"]["code"])
        assert room is not None
        assert room.members[welcome["player_id"]].connected is False

    async def test_oversized_frame_closes_the_connection(
        self, tcp_port: tuple[RoomRegistry, int]
    ) -> None:
        _, port = tcp_port
        player = await TcpPlayer.open(port)
        await player.until("hello")
        player.writer.write(struct.pack("!IB", 10_000_000, FRAME_BINARY))
        await player.writer.drain()
        with pytest.raises(asyncio.IncompleteReadError):
            while True:
                await player.frame()

    async def test_bad_frames_are_refused_without_closing(
        self, tcp_port: tuple[RoomRegistry, int]
    ) -> None:
        _, port = tcp_port
        player = await TcpPlayer.open(port)
        await player.until("hello")
        player.writer.write(encode_frame(7, b"x") + encode_frame(FRAME_JSON, b"{"))
        await player.writer.drain()
        assert (await player.until("error"))["message"] == "unknown frame kind 7"
        assert (await player.until("error"))["message"] == "frame is not UTF-8 JSON"
        await player.send({"type": "echo", "t": 1})
        assert (await player.until("echo"))["t"] == 1
        await player.close()

    async def test_token_is_read_from_the_create_message(self) -> None:
        async def resolve(token: str) -> RoomIdentity | None:
            return RoomIdentity("user-7", "Ana") if token == "good" else None

        registry = RoomRegistry(identity_resolver=resolve)
        server = await serve_game_rooms_tcp(registry, port=0)
        port = server.sockets[0].getsockname()[1]
        try:
            player = await TcpPlayer.open(port)
            await player.send({"type": "create"})
            assert (await player.until("error"))["code"] == "unauthorized"
            await player.send({"type": "create", "token": "good"})
            assert (await player.until("welcome"))["player_id"] == "user-7"
            await player.close()
        finally:
            server.close()
            await server.wait_closed()

    def test_timeout_must_exceed_the_heartbeat(self) -> None:
        with pytest.raises(ValueError, match="exceed"):
            asyncio.run(
                serve_game_rooms_tcp(
                    RoomRegistry(), heartbeat_seconds=5, heartbeat_timeout_seconds=5
                )
            )


async def test_tcp_and_websocket_players_share_a_room() -> None:
    """One registry, two transports: a phone on TCP and a browser on WebSocket."""
    registry = RoomRegistry()
    tcp = await serve_game_rooms_tcp(registry, port=0)
    tcp_port = tcp.sockets[0].getsockname()[1]
    app = FastAPI()
    app.include_router(
        make_game_room_router(
            registry,
            settings=WebSocketSettings(_env_file=None),  # type: ignore[call-arg]
        )
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    ws_port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="warning", ws="websockets-sansio")
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    try:
        phone = await TcpPlayer.open(tcp_port)
        await phone.send({"type": "create", "name": "Android"})
        phone_welcome = await phone.until("welcome")
        code = phone_welcome["room"]["code"]
        async with connect(f"ws://127.0.0.1:{ws_port}/rooms/ws") as browser:
            await browser.recv()
            await browser.send(json.dumps({"type": "join", "code": code}))
            while json.loads(await browser.recv())["type"] != "welcome":
                pass
            assert (await phone.until("player_joined"))["player"]["slot"] == 1
            await browser.send(bytes([0]) + b"input")
            while True:
                kind, payload = await phone.frame()
                if kind == FRAME_BINARY:
                    break
            assert payload == b"\x01input"
            await phone.send_bytes(bytes([1]) + b"frame")
            reply = await browser.recv()
            while not isinstance(reply, bytes):
                reply = await browser.recv()
            assert reply == b"\x00frame"
        seat = phone_welcome
        await phone.close()
        async with connect(f"ws://127.0.0.1:{ws_port}/rooms/ws") as again:
            await again.recv()
            await again.send(
                json.dumps(
                    {
                        "type": "resume",
                        "code": code,
                        "player_id": seat["player_id"],
                        "resume_token": seat["resume_token"],
                    }
                )
            )
            message = json.loads(await again.recv())
            while message["type"] != "welcome":
                message = json.loads(await again.recv())
            assert (message["resumed"], message["slot"]) == (True, 0)
    finally:
        server.should_exit = True
        await task
        sock.close()
        tcp.close()
        await tcp.wait_closed()
