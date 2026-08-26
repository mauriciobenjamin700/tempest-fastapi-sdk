"""Tests for SSEBroker (local fan-out + Redis pub/sub bridge)."""

from __future__ import annotations

import asyncio

import fakeredis.aioredis as fakeredis_async
import pytest

from tempest_fastapi_sdk import SSEBroker
from tempest_fastapi_sdk.sse import BROADCAST_CHANNEL


async def _first_frame(stream_iter: object, timeout: float = 1.0) -> bytes:
    """Read one encoded SSE frame from a stream iterator."""
    return await asyncio.wait_for(stream_iter.__anext__(), timeout)  # type: ignore[attr-defined]


class TestMemoryMode:
    async def test_fans_out_to_all_local_streams(self) -> None:
        broker = SSEBroker(heartbeat_seconds=None)
        a = broker.register("room1")
        b = broker.register("room1")
        ai, bi = a.stream(), b.stream()

        await broker.publish("room1", {"msg": "hi"}, event="chat")

        for it in (ai, bi):
            frame = await _first_frame(it)
            assert b"event: chat" in frame
            assert b'"msg": "hi"' in frame

    async def test_channels_are_isolated(self) -> None:
        broker = SSEBroker(heartbeat_seconds=None)
        a = broker.register("room1")
        broker.register("room2")
        await broker.publish("room2", "x")
        with pytest.raises(asyncio.TimeoutError):
            await _first_frame(a.stream(), timeout=0.15)

    async def test_unregister_and_count(self) -> None:
        broker = SSEBroker(heartbeat_seconds=None)
        stream = broker.register("room1")
        assert broker.local_subscribers("room1") == 1
        broker.unregister("room1", stream)
        assert broker.local_subscribers("room1") == 0


class TestResponseHelper:
    async def test_response_unregisters_on_disconnect(self) -> None:
        """broker.response subscribes, streams, and unregisters when done."""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        broker = SSEBroker(heartbeat_seconds=None)
        app = FastAPI()

        @app.get("/feed")
        async def feed() -> object:
            response = broker.response("room1")
            # A subscriber is registered as soon as response() returns.
            assert broker.local_subscribers("room1") == 1
            await broker.publish("room1", {"msg": "hi"}, event="chat")
            # End the stream so the response completes for the test client.
            for stream in tuple(broker._channels["room1"]):
                await stream.close()
            return response

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/feed")
        assert resp.status_code == 200
        assert b"event: chat" in resp.content
        # on_disconnect fired -> the stream was unregistered.
        assert broker.local_subscribers("room1") == 0

    async def test_register_streams_inherit_broker_backpressure(self) -> None:
        broker = SSEBroker(heartbeat_seconds=None, max_queue=3, overflow="drop_newest")
        stream = broker.register("room1")
        assert stream.max_queue == 3
        assert stream.overflow == "drop_newest"


class TestDispatchDecoding:
    async def test_dispatch_raw_handles_bytes(self) -> None:
        broker = SSEBroker(heartbeat_seconds=None, channel_prefix="sse")
        stream = broker.register("u1")
        await broker._dispatch_raw(b"sse:u1", b'{"data": {"n": 1}, "event": "tick"}')
        frame = await _first_frame(stream.stream())
        assert b"event: tick" in frame
        assert b'"n": 1' in frame


class TestRedisMode:
    async def test_publish_round_trips_through_redis(self) -> None:
        redis = fakeredis_async.FakeRedis(decode_responses=True)
        broker = SSEBroker(redis=redis, heartbeat_seconds=None)
        task = asyncio.create_task(broker.run())
        await asyncio.sleep(0.1)  # let PSUBSCRIBE land

        stream = broker.register("u1")
        await broker.publish("u1", {"hello": "world"}, event="greet")

        frame = await _first_frame(stream.stream(), timeout=2.0)
        assert b"event: greet" in frame
        assert b'"hello": "world"' in frame

        await broker.aclose()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_run_without_redis_raises(self) -> None:
        broker = SSEBroker(heartbeat_seconds=None)
        with pytest.raises(RuntimeError, match="requires a Redis client"):
            await broker.run()


class TestBroadcast:
    async def test_reaches_every_channel_locally(self) -> None:
        broker = SSEBroker(heartbeat_seconds=None)
        room = broker.register("room1")
        user = broker.register("user-42")
        lonely = broker.register("room2")

        await broker.broadcast({"type": "MAINTENANCE"}, event="notice")

        for stream in (room, user, lonely):
            frame = await _first_frame(stream.stream())
            assert b"event: notice" in frame
            assert b'"type": "MAINTENANCE"' in frame

    async def test_delivers_once_to_a_stream_in_two_channels(self) -> None:
        broker = SSEBroker(heartbeat_seconds=None)
        stream = broker.register("room1")
        broker._channels.setdefault("room2", set()).add(stream)

        await broker.broadcast("once")

        frame = await _first_frame(stream.stream())
        assert b"data: once" in frame
        with pytest.raises(asyncio.TimeoutError):
            await _first_frame(stream.stream(), timeout=0.15)

    async def test_no_subscribers_is_a_noop(self) -> None:
        broker = SSEBroker(heartbeat_seconds=None)
        await broker.broadcast("nobody home")
        assert broker.local_channels() == []

    async def test_reserved_channel_is_refused(self) -> None:
        broker = SSEBroker(heartbeat_seconds=None)
        with pytest.raises(ValueError, match=r"reserved for SSEBroker\.broadcast"):
            broker.register(BROADCAST_CHANNEL)
        with pytest.raises(ValueError, match=r"reserved for SSEBroker\.broadcast"):
            await broker.publish(BROADCAST_CHANNEL, "x")

    async def test_crosses_the_redis_bridge_to_a_second_broker(self) -> None:
        """Two brokers on one Redis: the other one's streams get the event.

        Same process on purpose — what this measures is the pub/sub bridge
        and the reserved-channel routing, not process isolation.
        """
        redis = fakeredis_async.FakeRedis(decode_responses=True)
        publisher = SSEBroker(redis=redis, heartbeat_seconds=None)
        receiver = SSEBroker(redis=redis, heartbeat_seconds=None)
        task = asyncio.create_task(receiver.run())
        await asyncio.sleep(0.1)

        stream = receiver.register("u1")
        await publisher.broadcast({"type": "PING"}, event="system")

        frame = await _first_frame(stream.stream(), timeout=2.0)
        assert b"event: system" in frame
        assert b'"type": "PING"' in frame

        await receiver.aclose()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class TestLocalChannels:
    async def test_lists_channels_with_open_streams(self) -> None:
        broker = SSEBroker(heartbeat_seconds=None)
        broker.register("b-room")
        stream = broker.register("a-room")
        assert broker.local_channels() == ["a-room", "b-room"]
        broker.unregister("a-room", stream)
        assert broker.local_channels() == ["b-room"]
