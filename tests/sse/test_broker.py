"""Tests for SSEBroker (local fan-out + Redis pub/sub bridge)."""

from __future__ import annotations

import asyncio

import fakeredis.aioredis as fakeredis_async
import pytest

from tempest_fastapi_sdk import SSEBroker


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
