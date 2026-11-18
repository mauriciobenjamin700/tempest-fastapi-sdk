"""Tests for :class:`LivePositionStore` and :class:`LivePositionSchema`."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from tempest_fastapi_sdk.geo import (
    Coordinate,
    LivePositionSchema,
    LivePositionStore,
    OSRMBackend,
    destination_point,
)
from tempest_fastapi_sdk.sse import SSEBroker, SSEData

fakeredis = pytest.importorskip("fakeredis")

ORIGIN = Coordinate(latitude=-5.0892, longitude=-42.8019)
BOARDING = Coordinate(latitude=-5.0381, longitude=-42.4608)


class _RecordingBroker(SSEBroker):
    """Single-process broker that records every publish.

    Attributes:
        sent (list[tuple[str, Any, str | None]]): ``(channel, data, event)``
            per publish, in order.
    """

    def __init__(self) -> None:
        """Start with nothing sent."""
        super().__init__(heartbeat_seconds=None)
        self.sent: list[tuple[str, Any, str | None]] = []

    async def publish(
        self,
        channel: str,
        data: SSEData = "",
        *,
        event: str | None = None,
        id: str | None = None,
        retry: int | None = None,
    ) -> None:
        """Record the publish instead of fanning out.

        Args:
            channel (str): Target channel.
            data (SSEData): Payload.
            event (str | None): SSE event name.
            id (str | None): Unused.
            retry (int | None): Unused.
        """
        self.sent.append((channel, data, event))


class _Clock:
    """A settable server clock.

    Attributes:
        now (float): Seconds since the epoch the store reads.
    """

    def __init__(self) -> None:
        """Start at the real time."""
        self.now: float = time.time()

    def __call__(self) -> float:
        """Return :attr:`now`.

        Returns:
            float: The current fake time.
        """
        return self.now


def _audience(*recipients: str) -> Callable[[str], Any]:
    """Build an audience callback that answers ``recipients`` for any channel.

    Args:
        *recipients (str): The broker channels to answer.

    Returns:
        Callable[[str], Any]: The async callback.
    """

    async def resolve(channel_id: str) -> Sequence[str]:
        """Answer the fixed audience.

        Args:
            channel_id (str): Ignored.

        Returns:
            Sequence[str]: ``recipients``.
        """
        return list(recipients)

    return resolve


def _position(point: Coordinate, offset_s: float = 0.0) -> LivePositionSchema:
    """Build a position at ``point`` recorded ``offset_s`` seconds from now.

    Args:
        point (Coordinate): Where.
        offset_s (float): Seconds added to a base time a minute ago.

    Returns:
        LivePositionSchema: The position.
    """
    recorded = datetime.now(UTC) - timedelta(seconds=60) + timedelta(seconds=offset_s)
    return LivePositionSchema(
        lat=point.latitude, lng=point.longitude, recorded_at=recorded
    )


def _moved(meters: float) -> Coordinate:
    """Return a point ``meters`` east of :data:`ORIGIN`.

    Args:
        meters (float): Distance.

    Returns:
        Coordinate: The point.
    """
    return destination_point(ORIGIN, 90.0, meters / 1000.0)


@pytest.fixture
def redis() -> Any:
    """Return a fresh fakeredis client.

    Returns:
        Any: The client.
    """
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


class TestLivePositionSchema:
    """Validation of what the device sends."""

    def test_valid_position(self) -> None:
        """A complete position validates and exposes its coordinate."""
        position = LivePositionSchema(
            lat=-5.0892,
            lng=-42.8019,
            accuracy_m=8.0,
            speed_kmh=42.0,
            heading=87.5,
            recorded_at=datetime.now(UTC),
        )
        assert position.coordinate == ORIGIN

    @pytest.mark.parametrize(
        ("field", "value"),
        [("lat", 91.0), ("lng", -181.0), ("heading", 360.0), ("speed_kmh", -1.0)],
    )
    def test_out_of_range_refused(self, field: str, value: float) -> None:
        """Coordinates, heading and speed out of range are refused."""
        data: dict[str, Any] = {
            "lat": 0.0,
            "lng": 0.0,
            "recorded_at": datetime.now(UTC),
        }
        data[field] = value
        with pytest.raises(ValidationError):
            LivePositionSchema(**data)

    def test_future_recorded_at_refused(self) -> None:
        """A fix stamped a minute ahead is refused."""
        with pytest.raises(ValidationError, match="future"):
            LivePositionSchema(
                lat=0.0, lng=0.0, recorded_at=datetime.now(UTC) + timedelta(minutes=1)
            )

    def test_small_clock_skew_accepted(self) -> None:
        """A few seconds ahead is phone clock drift, not an error."""
        position = LivePositionSchema(
            lat=0.0, lng=0.0, recorded_at=datetime.now(UTC) + timedelta(seconds=5)
        )
        assert position.lat == 0.0

    def test_naive_recorded_at_refused(self) -> None:
        """A timestamp without timezone is ambiguous and refused."""
        with pytest.raises(ValidationError):
            LivePositionSchema(
                lat=0.0, lng=0.0, recorded_at=datetime(2026, 1, 1, 12, 0)
            )


class TestThrottleAndDistance:
    """What the store keeps."""

    async def test_first_position_accepted_and_latest(self, redis: Any) -> None:
        """The first position of a channel is always kept."""
        store = LivePositionStore(redis)
        position = _position(ORIGIN)
        assert await store.publish("trip-1", position) is True
        assert await store.latest("trip-1") == position

    async def test_throttled_within_min_interval(self, redis: Any) -> None:
        """A second position inside ``min_interval_seconds`` is refused."""
        clock = _Clock()
        store = LivePositionStore(redis, min_interval_seconds=5.0, clock=clock)
        await store.publish("trip-1", _position(ORIGIN))

        clock.now += 4.0
        assert await store.publish("trip-1", _position(_moved(500), 4)) is False
        clock.now += 1.5
        assert await store.publish("trip-1", _position(_moved(500), 6)) is True
        latest = await store.latest("trip-1")
        assert latest is not None
        assert latest.lng == pytest.approx(_moved(500).longitude)

    async def test_too_close_refused(self, redis: Any) -> None:
        """Moving less than ``min_distance_m`` is refused."""
        clock = _Clock()
        store = LivePositionStore(redis, min_distance_m=10.0, clock=clock)
        await store.publish("trip-1", _position(ORIGIN))

        clock.now += 30.0
        assert await store.publish("trip-1", _position(_moved(5), 30)) is False
        assert await store.publish("trip-1", _position(_moved(15), 31)) is True

    async def test_older_fix_refused(self, redis: Any) -> None:
        """A fix not newer than the stored one is a replay and is refused."""
        clock = _Clock()
        store = LivePositionStore(redis, clock=clock)
        await store.publish("trip-1", _position(ORIGIN, 10))

        clock.now += 30.0
        assert await store.publish("trip-1", _position(_moved(500), 5)) is False

    async def test_channels_are_independent(self, redis: Any) -> None:
        """The throttle of one channel does not hold another."""
        store = LivePositionStore(redis)
        assert await store.publish("trip-1", _position(ORIGIN))
        assert await store.publish("trip-2", _position(ORIGIN))


class TestTTL:
    """The latest position expires on its own."""

    async def test_ttl_is_set(self, redis: Any) -> None:
        """An accepted position is written with ``ttl_seconds``."""
        store = LivePositionStore(redis, ttl_seconds=120)
        await store.publish("trip-1", _position(ORIGIN))
        ttl = await redis.ttl("geo:live:trip-1")
        assert 0 < ttl <= 120

    async def test_too_close_renews_ttl(self, redis: Any) -> None:
        """A sender standing still keeps its marker alive."""
        clock = _Clock()
        store = LivePositionStore(redis, ttl_seconds=120, clock=clock)
        await store.publish("trip-1", _position(ORIGIN))
        await redis.expire("geo:live:trip-1", 3)

        clock.now += 30.0
        assert await store.publish("trip-1", _position(_moved(1), 30)) is False
        assert await redis.ttl("geo:live:trip-1") > 3

    async def test_position_expires(self, redis: Any) -> None:
        """Past the TTL, ``latest`` answers ``None``."""
        store = LivePositionStore(redis, ttl_seconds=1)
        await store.publish("trip-1", _position(ORIGIN))
        assert await store.latest("trip-1") is not None

        deadline = time.monotonic() + 10.0
        while await store.latest("trip-1") is not None:
            assert time.monotonic() < deadline, "position did not expire"
            await asyncio.sleep(0.1)


class TestAudience:
    """Delivery only to whom the service names."""

    def test_broker_without_audience_refused(self, redis: Any) -> None:
        """A broker without an audience callback is a construction error."""
        with pytest.raises(ValueError, match="audience"):
            LivePositionStore(redis, broker=_RecordingBroker())

    def test_audience_without_broker_refused(self, redis: Any) -> None:
        """An audience with nowhere to deliver is a construction error too."""
        with pytest.raises(ValueError, match="audience"):
            LivePositionStore(redis, audience=_audience("alice"))

    async def test_delivers_only_to_audience(self, redis: Any) -> None:
        """Each audience member gets one event; nobody else does."""
        broker = _RecordingBroker()
        store = LivePositionStore(
            redis, broker=broker, audience=_audience("alice", "bob", "alice")
        )
        await store.publish("trip-1", _position(ORIGIN))

        assert [channel for channel, _, _ in broker.sent] == ["alice", "bob"]
        _, data, event = broker.sent[0]
        assert event == "live_position"
        assert data["channel_id"] == "trip-1"
        assert data["position"]["lat"] == ORIGIN.latitude
        assert data["eta"] is None

    async def test_empty_audience_delivers_nothing(self, redis: Any) -> None:
        """An empty answer stores the position and sends nothing."""
        broker = _RecordingBroker()
        store = LivePositionStore(redis, broker=broker, audience=_audience())
        assert await store.publish("trip-1", _position(ORIGIN)) is True
        assert broker.sent == []

    async def test_refused_position_not_delivered(self, redis: Any) -> None:
        """A throttled position reaches nobody."""
        broker = _RecordingBroker()
        clock = _Clock()
        store = LivePositionStore(
            redis, broker=broker, audience=_audience("alice"), clock=clock
        )
        await store.publish("trip-1", _position(ORIGIN))
        clock.now += 1.0
        await store.publish("trip-1", _position(_moved(500), 1))
        assert len(broker.sent) == 1

    async def test_real_broker_stream_receives_frame(self, redis: Any) -> None:
        """Through a real single-process broker, the subscriber gets the frame."""
        broker = SSEBroker(heartbeat_seconds=None)
        alice = broker.register("alice")
        carol = broker.register("carol")
        store = LivePositionStore(redis, broker=broker, audience=_audience("alice"))
        await store.publish("trip-1", _position(ORIGIN))

        frame = await asyncio.wait_for(anext(alice.stream()), timeout=2.0)
        assert b"event: live_position" in frame
        assert b'"channel_id": "trip-1"' in frame or b'"channel_id":"trip-1"' in frame
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(carol.stream()), timeout=0.2)


class TestClear:
    """Ending the tracking."""

    async def test_clear_forgets_and_notifies(self, redis: Any) -> None:
        """``clear`` deletes the position and tells the audience."""
        broker = _RecordingBroker()
        store = LivePositionStore(redis, broker=broker, audience=_audience("alice"))
        await store.publish("trip-1", _position(ORIGIN))
        await store.clear("trip-1")

        assert await store.latest("trip-1") is None
        assert broker.sent[-1] == (
            "alice",
            {"channel_id": "trip-1"},
            "live_position_cleared",
        )

    async def test_clear_without_broker(self, redis: Any) -> None:
        """A silent store just forgets."""
        store = LivePositionStore(redis)
        await store.publish("trip-1", _position(ORIGIN))
        await store.clear("trip-1")
        assert await store.latest("trip-1") is None


def _osrm_table(
    calls: list[httpx.Request], *, status: int = 200
) -> httpx.MockTransport:
    """Build a fake OSRM ``/table`` server: 2 km and 120 s per destination.

    Args:
        calls (list[httpx.Request]): Collects every request.
        status (int): HTTP status to answer.

    Returns:
        httpx.MockTransport: The transport.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        """Answer one row with ``2 km * (j + 1)`` per destination.

        Args:
            request (httpx.Request): The request.

        Returns:
            httpx.Response: The OSRM-shaped table.
        """
        calls.append(request)
        if status != 200:
            return httpx.Response(status)
        count = len(request.url.params["destinations"].split(";"))
        return httpx.Response(
            200,
            json={
                "code": "Ok",
                "distances": [[2000.0 * (j + 1) for j in range(count)]],
                "durations": [[120.0 * (j + 1) for j in range(count)]],
            },
        )

    return httpx.MockTransport(handler)


class TestEta:
    """ETA per recipient."""

    async def test_eta_from_osrm_matrix(self, redis: Any) -> None:
        """One table request; each recipient with a target gets its ETA."""
        calls: list[httpx.Request] = []
        broker = _RecordingBroker()
        other = Coordinate(latitude=-4.8278, longitude=-42.1686)

        async def targets(channel_id: str) -> dict[str, Coordinate]:
            """Answer the boarding points, including one outside the audience."""
            return {"alice": BOARDING, "bob": other, "mallory": other}

        async with httpx.AsyncClient(transport=_osrm_table(calls)) as client:
            store = LivePositionStore(
                redis,
                broker=broker,
                audience=_audience("alice", "bob", "carol"),
                eta_targets=targets,
                routing=OSRMBackend(http_client=client),
            )
            await store.publish("trip-1", _position(ORIGIN))

        assert len(calls) == 1
        assert "/table/v1/" in calls[0].url.path
        sent = {channel: data for channel, data, _ in broker.sent}
        assert set(sent) == {"alice", "bob", "carol"}
        assert sent["alice"]["eta"]["source"] == "osrm"
        assert sent["alice"]["eta"]["distance_km"] == pytest.approx(2.0)
        assert sent["bob"]["eta"]["duration_minutes"] == pytest.approx(4.0)
        assert sent["carol"]["eta"] is None

    async def test_eta_falls_back_when_osrm_fails(self, redis: Any) -> None:
        """A failing matrix falls back to ``estimate_travel``."""
        calls: list[httpx.Request] = []
        broker = _RecordingBroker()

        async def targets(channel_id: str) -> dict[str, Coordinate]:
            """Answer one boarding point."""
            return {"alice": BOARDING}

        async with httpx.AsyncClient(
            transport=_osrm_table(calls, status=503)
        ) as client:
            store = LivePositionStore(
                redis,
                broker=broker,
                audience=_audience("alice"),
                eta_targets=targets,
                routing=OSRMBackend(http_client=client),
            )
            await store.publish("trip-1", _position(ORIGIN))

        assert broker.sent[0][1]["eta"]["source"] == "heuristic"

    async def test_eta_without_routing_is_heuristic(self, redis: Any) -> None:
        """Without a routing backend, the ETA is the offline estimate."""
        broker = _RecordingBroker()

        async def targets(channel_id: str) -> dict[str, Coordinate]:
            """Answer one boarding point."""
            return {"alice": BOARDING}

        store = LivePositionStore(
            redis, broker=broker, audience=_audience("alice"), eta_targets=targets
        )
        await store.publish("trip-1", _position(ORIGIN))
        assert broker.sent[0][1]["eta"]["source"] == "heuristic"

    def test_routing_without_targets_refused(self, redis: Any) -> None:
        """A routing backend with no targets has nothing to compute."""
        with pytest.raises(ValueError, match="eta_targets"):
            LivePositionStore(
                redis,
                broker=_RecordingBroker(),
                audience=_audience("alice"),
                routing=OSRMBackend(http_client=httpx.AsyncClient()),
            )
