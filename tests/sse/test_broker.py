"""Tests for SSEBroker (local fan-out + Redis pub/sub bridge)."""

from __future__ import annotations

import asyncio
import logging

import fakeredis
import fakeredis.aioredis as fakeredis_async
import pytest

from tempest_fastapi_sdk import SSEBroker
from tempest_fastapi_sdk.sse import BROADCAST_CHANNEL


async def _first_frame(stream_iter: object, timeout: float = 1.0) -> bytes:
    """Read one encoded SSE frame from a stream iterator."""
    return await asyncio.wait_for(stream_iter.__anext__(), timeout)  # type: ignore[attr-defined]


async def _until_subscribed(
    redis: fakeredis_async.FakeRedis,
    timeout: float = 10.0,
) -> None:
    """Wait until the broker's ``PSUBSCRIBE`` has landed on Redis.

    ``run`` subscribes in a background task, and a ``PUBLISH`` sent
    before the pattern exists reaches nobody. Polling ``PUBSUB NUMPAT``
    waits on the subscription itself, where a fixed sleep only bets on
    how fast the runner is.

    Args:
        redis (fakeredis_async.FakeRedis): The client the broker uses.
        timeout (float): Seconds before the wait fails.
    """
    async with asyncio.timeout(timeout):
        while await redis.pubsub_numpat() < 1:
            await asyncio.sleep(0.01)


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
        await _until_subscribed(redis)

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
        await _until_subscribed(redis)

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


class TestRunSurvivesMalformedMessages:
    """A frame the broker did not write must not end the fan-out loop."""

    @pytest.mark.parametrize(
        "poison",
        ["not-json", "[1]", "null", '"s"', '{"event": 7}', '{"retry": "soon"}'],
    )
    async def test_later_publish_still_delivered(
        self, poison: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Malformed payloads are logged and skipped; delivery continues."""
        redis = fakeredis_async.FakeRedis(decode_responses=True)
        broker = SSEBroker(
            redis=redis, heartbeat_seconds=None, channel_prefix="app-a:sse"
        )
        task = asyncio.create_task(broker.run())
        await _until_subscribed(redis)
        stream = broker.register("42")
        frames = stream.stream()

        with caplog.at_level(logging.WARNING, logger="tempest_fastapi_sdk.sse.broker"):
            await redis.publish("app-a:sse:42", poison)
            await redis.publish("app-a:sse:42", "[1]")
            await broker.publish("42", {"ok": True}, event="after")
            frame = await _first_frame(frames, timeout=2.0)

        assert b"event: after" in frame
        assert b'"ok": true' in frame
        assert not task.done()
        skipped = [r for r in caplog.records if "undeliverable" in r.getMessage()]
        assert len(skipped) == 2
        assert all(poison not in r.getMessage() for r in skipped if poison != "[1]")

        await broker.aclose()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class TestChannelPrefixIsolation:
    """Redis pub/sub ignores the DB index; the prefix is the namespace."""

    async def _pair_on_one_server(
        self,
    ) -> tuple[fakeredis_async.FakeRedis, fakeredis_async.FakeRedis]:
        """Return two clients on separate DB indexes of one fake server.

        Returns:
            tuple[fakeredis_async.FakeRedis, fakeredis_async.FakeRedis]:
            Clients on DB 0 and DB 1 that share the pub/sub bus.
        """
        server = fakeredis.FakeServer()
        return (
            fakeredis_async.FakeRedis(server=server, db=0, decode_responses=True),
            fakeredis_async.FakeRedis(server=server, db=1, decode_responses=True),
        )

    async def test_distinct_prefixes_do_not_cross_deliver(self) -> None:
        """Neither ``publish`` nor ``broadcast`` leaks into the other service."""
        redis_a, redis_b = await self._pair_on_one_server()
        service_a = SSEBroker(
            redis=redis_a, heartbeat_seconds=None, channel_prefix="app-a:sse"
        )
        service_b = SSEBroker(
            redis=redis_b, heartbeat_seconds=None, channel_prefix="app-b:sse"
        )
        task_a = asyncio.create_task(service_a.run())
        task_b = asyncio.create_task(service_b.run())
        async with asyncio.timeout(10.0):
            while await redis_a.pubsub_numpat() < 2:
                await asyncio.sleep(0.01)

        own = service_b.register("42")
        own_frames = own.stream()
        foreign = service_a.register("42")
        foreign_frames = foreign.stream()

        await service_a.publish("42", "a-private", event="a")
        await service_a.broadcast("a-maintenance", event="notice")
        await service_b.publish("42", "b-private", event="b")

        frame = await _first_frame(own_frames, timeout=2.0)
        assert b"data: b-private" in frame
        with pytest.raises(asyncio.TimeoutError):
            await _first_frame(own_frames, timeout=0.3)

        received_by_a = [
            await _first_frame(foreign_frames, timeout=2.0),
            await _first_frame(foreign_frames, timeout=2.0),
        ]
        assert b"data: a-private" in received_by_a[0]
        assert b"data: a-maintenance" in received_by_a[1]
        with pytest.raises(asyncio.TimeoutError):
            await _first_frame(foreign_frames, timeout=0.3)

        for broker, task in ((service_a, task_a), (service_b, task_b)):
            await broker.aclose()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    async def test_nested_prefixes_do_not_cross_deliver(self) -> None:
        """``app:*`` matches ``app:chat:room``; the envelope prefix stops it."""
        redis_a, redis_b = await self._pair_on_one_server()
        outer = SSEBroker(redis=redis_a, heartbeat_seconds=None, channel_prefix="app")
        inner = SSEBroker(
            redis=redis_b, heartbeat_seconds=None, channel_prefix="app:chat"
        )
        task_outer = asyncio.create_task(outer.run())
        task_inner = asyncio.create_task(inner.run())
        async with asyncio.timeout(10.0):
            while await redis_a.pubsub_numpat() < 2:
                await asyncio.sleep(0.01)

        victim = outer.register("chat:room")
        victim_frames = victim.stream()
        own = inner.register("room")
        own_frames = own.stream()

        await inner.publish("room", "inner-private", event="m")
        await inner.broadcast("inner-notice", event="notice")

        assert b"data: inner-private" in await _first_frame(own_frames, timeout=2.0)
        assert b"data: inner-notice" in await _first_frame(own_frames, timeout=2.0)
        with pytest.raises(asyncio.TimeoutError):
            await _first_frame(victim_frames, timeout=0.5)

        for broker, task in ((outer, task_outer), (inner, task_inner)):
            await broker.aclose()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    async def test_envelope_without_prefix_is_still_delivered(self) -> None:
        """A publisher from before the stamp, mid rolling deploy, still reaches."""
        broker = SSEBroker(heartbeat_seconds=None, channel_prefix="app")
        stream = broker.register("u1")
        frames = stream.stream()
        await broker._dispatch_raw("app:u1", '{"data": "old", "event": null}')
        assert b"data: old" in await _first_frame(frames)

    async def test_envelope_carries_the_publishing_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        published: list[tuple[str, str]] = []

        async def _record(channel: str, message: str) -> int:
            published.append((channel, message))
            return 1

        client = fakeredis_async.FakeRedis(decode_responses=True)
        monkeypatch.setattr(client, "publish", _record)
        broker = SSEBroker(
            redis=client, heartbeat_seconds=None, channel_prefix="svc:sse"
        )
        await broker.publish("42", "x")
        assert published[0][0] == "svc:sse:42"
        assert '"prefix": "svc:sse"' in published[0][1]

    def test_default_prefix_with_redis_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The shared default is allowed but never silent in Redis mode."""
        redis = fakeredis_async.FakeRedis(decode_responses=True)
        with caplog.at_level(logging.WARNING, logger="tempest_fastapi_sdk.sse.broker"):
            SSEBroker(redis=redis, heartbeat_seconds=None)
            SSEBroker(redis=redis, heartbeat_seconds=None, channel_prefix="sse")
        warnings = [r for r in caplog.records if "channel_prefix" in r.getMessage()]
        assert len(warnings) == 2

    def test_unique_prefix_or_memory_mode_does_not_warn(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Only the Redis + shared-default combination is flagged."""
        redis = fakeredis_async.FakeRedis(decode_responses=True)
        with caplog.at_level(logging.WARNING, logger="tempest_fastapi_sdk.sse.broker"):
            SSEBroker(redis=redis, heartbeat_seconds=None, channel_prefix="app-a:sse")
            SSEBroker(heartbeat_seconds=None)
        assert [r for r in caplog.records if "channel_prefix" in r.getMessage()] == []
