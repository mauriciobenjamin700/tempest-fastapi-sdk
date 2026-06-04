"""Tests for ``WebSocketHub`` and ``make_websocket_router``."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import (
    WebSocketConnection,
    WebSocketHub,
    WebSocketSettings,
    WSEnvelope,
    make_websocket_router,
)

# ---------------------------------------------------------------------------
# Hub unit tests
# ---------------------------------------------------------------------------


class _FakeWebSocket:
    """Minimal stand-in for ``fastapi.WebSocket`` for hub-level assertions."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed_with: int | None = None
        self.dead: bool = False

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self.dead:
            raise RuntimeError("peer gone")
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


class TestWebSocketHub:
    async def test_register_assigns_unique_id_and_indexes_by_user(self) -> None:
        hub = WebSocketHub()
        user = uuid4()
        ws_a = _FakeWebSocket()
        ws_b = _FakeWebSocket()
        conn_a = await hub.register(user, ws_a)  # type: ignore[arg-type]
        conn_b = await hub.register(user, ws_b)  # type: ignore[arg-type]
        assert conn_a.connection_id != conn_b.connection_id
        assert hub.connection_count() == 2
        assert hub.online_users() == {user}

    async def test_max_per_user_evicts_oldest_connection(self) -> None:
        hub = WebSocketHub(max_per_user=2)
        user = uuid4()
        first = _FakeWebSocket()
        second = _FakeWebSocket()
        third = _FakeWebSocket()
        await hub.register(user, first)  # type: ignore[arg-type]
        await hub.register(user, second)  # type: ignore[arg-type]
        await hub.register(user, third)  # type: ignore[arg-type]
        assert first.closed_with == 4429
        assert second.closed_with is None
        assert third.closed_with is None
        assert hub.connection_count() == 2

    async def test_send_to_delivers_only_to_target_user(self) -> None:
        hub = WebSocketHub()
        user_a = uuid4()
        user_b = uuid4()
        ws_a = _FakeWebSocket()
        ws_b = _FakeWebSocket()
        await hub.register(user_a, ws_a)  # type: ignore[arg-type]
        await hub.register(user_b, ws_b)  # type: ignore[arg-type]
        envelope = WSEnvelope(type="ping", data={})
        delivered = await hub.send_to(user_a, envelope)
        assert delivered == 1
        assert len(ws_a.sent) == 1
        assert ws_b.sent == []

    async def test_broadcast_topic_filters_subscribers(self) -> None:
        hub = WebSocketHub()
        user_a = uuid4()
        user_b = uuid4()
        ws_a = _FakeWebSocket()
        ws_b = _FakeWebSocket()
        conn_a = await hub.register(user_a, ws_a)  # type: ignore[arg-type]
        await hub.register(user_b, ws_b)  # type: ignore[arg-type]
        await hub.subscribe(conn_a.connection_id, "orders")
        envelope = WSEnvelope(type="order.paid", data={"id": "1"})
        delivered = await hub.broadcast(envelope, topic="orders")
        assert delivered == 1
        assert hub.topic_count("orders") == 1

    async def test_broadcast_without_topic_hits_every_connection(self) -> None:
        hub = WebSocketHub()
        user_a = uuid4()
        user_b = uuid4()
        ws_a = _FakeWebSocket()
        ws_b = _FakeWebSocket()
        await hub.register(user_a, ws_a)  # type: ignore[arg-type]
        await hub.register(user_b, ws_b)  # type: ignore[arg-type]
        envelope = WSEnvelope(type="announce", data={})
        delivered = await hub.broadcast(envelope)
        assert delivered == 2

    async def test_send_to_evicts_dead_peer(self) -> None:
        hub = WebSocketHub()
        user = uuid4()
        ws_dead = _FakeWebSocket()
        ws_dead.dead = True
        ws_live = _FakeWebSocket()
        await hub.register(user, ws_dead)  # type: ignore[arg-type]
        await hub.register(user, ws_live)  # type: ignore[arg-type]
        envelope = WSEnvelope(type="ping", data={})
        delivered = await hub.send_to(user, envelope)
        assert delivered == 1
        assert hub.connection_count() == 1

    async def test_unregister_clears_topic_indexes(self) -> None:
        hub = WebSocketHub()
        user = uuid4()
        ws = _FakeWebSocket()
        conn = await hub.register(user, ws)  # type: ignore[arg-type]
        await hub.subscribe(conn.connection_id, "orders")
        assert hub.topic_count("orders") == 1
        await hub.unregister(conn.connection_id)
        assert hub.topic_count("orders") == 0
        assert hub.connection_count() == 0


# ---------------------------------------------------------------------------
# Router integration tests (use FastAPI TestClient — sync only, fine for WS)
# ---------------------------------------------------------------------------


def _build_app(
    *,
    hub: WebSocketHub,
    user_id: UUID | None,
) -> FastAPI:
    """Build a FastAPI app with the websocket router wired."""

    async def resolver(token: str) -> UUID | None:
        if token == "valid-token":
            return user_id
        return None

    async def handler(
        ws: WebSocket,
        connection: WebSocketConnection,
        hub: WebSocketHub,
    ) -> None:
        # Echo loop — every received message bounces back as type=echo.
        while True:
            message = await ws.receive_json()
            envelope = WSEnvelope.model_validate(message)
            if envelope.type == "pong":
                continue
            await ws.send_json(
                WSEnvelope(
                    type="echo",
                    data={"received": envelope.type},
                    request_id=envelope.request_id,
                ).model_dump()
            )

    settings = WebSocketSettings(
        WS_HEARTBEAT_SECONDS=3600,  # disable heartbeats during fast tests
        WS_HEARTBEAT_TIMEOUT_SECONDS=3600,
    )

    app = FastAPI()
    app.include_router(
        make_websocket_router(
            handler,
            hub=hub,
            bearer_resolver=resolver,
            settings=settings,
        )
    )
    return app


class TestWebSocketRouter:
    def test_valid_query_token_handshake_then_echo(self) -> None:
        user_id = uuid4()
        hub = WebSocketHub()
        app = _build_app(hub=hub, user_id=user_id)
        with (
            TestClient(app) as client,
            client.websocket_connect("/ws?token=valid-token") as ws,
        ):
            ws.send_json({"type": "chat.message", "data": {"text": "hi"}})
            received = ws.receive_json()
        assert received["type"] == "echo"
        assert received["data"]["received"] == "chat.message"

    def test_invalid_token_closed_with_4401(self) -> None:
        hub = WebSocketHub()
        app = _build_app(hub=hub, user_id=uuid4())
        with (
            TestClient(app) as client,
            pytest.raises(Exception),  # noqa: B017 — Starlette closes the handshake
            client.websocket_connect("/ws?token=wrong"),
        ):
            pass

    def test_subprotocol_bearer_negotiation(self) -> None:
        user_id = uuid4()
        hub = WebSocketHub()
        app = _build_app(hub=hub, user_id=user_id)
        with (
            TestClient(app) as client,
            client.websocket_connect(
                "/ws",
                subprotocols=["bearer", "valid-token"],
            ) as ws,
        ):
            ws.send_json({"type": "ping", "data": {}})
            ws.receive_json()

    def test_handler_sees_registered_connection(self) -> None:
        user_id = uuid4()
        hub = WebSocketHub()
        app = _build_app(hub=hub, user_id=user_id)
        with (
            TestClient(app) as client,
            client.websocket_connect("/ws?token=valid-token") as ws,
        ):
            # Send something so the handler enters the loop and the
            # registration is visible to the hub.
            ws.send_json({"type": "chat.message", "data": {}})
            ws.receive_json()
            assert hub.connection_count() == 1
            assert user_id in hub.online_users()
        # On close the router must unregister.
        assert hub.connection_count() == 0
