"""Tests for the cached geocoding backend, its stores and its keys.

The wrapper runs against a stub backend that records every call and the
coordinate it was handed, so a hit reads as "the backend was not asked
again" and the rounding claim is asserted on the value the backend
actually received. Key formats are asserted as literal strings: they are
what separates a new entry from an old one across releases, and a test is
the only thing that notices a format change.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from tempest_fastapi_sdk.geo import (
    DEFAULT_GEOCODE_CACHE_TTL_SECONDS,
    CachedGeocodingBackend,
    Coordinate,
    GeocodeAddress,
    GeocodeCacheStore,
    GeocodeResult,
    GeocodingBackend,
    InMemoryGeocodeCacheStore,
    RedisGeocodeCacheStore,
    haversine_km,
)

_LOGGER_NAME: str = "tempest_fastapi_sdk.geo.cache"


def _sample_result() -> GeocodeResult:
    """Build a result carrying every field, address included.

    Returns:
        A resolved place the cache has to round-trip without losing
        anything, structured address first.
    """
    return GeocodeResult(
        coordinate=Coordinate(latitude=-5.089, longitude=-42.801),
        display_name="Teresina, Piauí, Brasil",
        place_type="city",
        address=GeocodeAddress(
            city="Teresina",
            state="Piauí",
            state_code="PI",
            country="Brasil",
            country_code="BR",
            postcode="64001-490",
        ),
    )


class _StubBackend:
    """A ``GeocodingBackend`` answering from one canned result.

    Attributes:
        result (GeocodeResult | None): What every call resolves to.
        error (BaseException | None): Raised by the next call when set.
        calls (list[str]): Method names in order.
        coordinates (list[Coordinate]): Points ``reverse`` received.
        queries (list[str]): Texts ``geocode`` received, verbatim.
    """

    def __init__(self, result: GeocodeResult | None = None) -> None:
        """Answer both directions with ``result`` until told otherwise.

        Args:
            result: What each call resolves to. ``None`` is the place
                nobody can find.
        """
        self.result: GeocodeResult | None = result
        self.error: BaseException | None = None
        self.calls: list[str] = []
        self.coordinates: list[Coordinate] = []
        self.queries: list[str] = []

    async def geocode(self, query: str) -> GeocodeResult | None:
        """Record ``query`` and answer from the canned result.

        Args:
            query: The text received, whatever normalization the wrapper
                did to the cache key.

        Returns:
            The canned result.

        Raises:
            BaseException : Whatever :attr:`error` holds.
        """
        self.calls.append("geocode")
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.result

    async def reverse(self, coordinate: Coordinate) -> GeocodeResult | None:
        """Record ``coordinate`` and answer from the canned result.

        Args:
            coordinate: The point received.

        Returns:
            The canned result.

        Raises:
            BaseException: Whatever :attr:`error` holds.
        """
        self.calls.append("reverse")
        self.coordinates.append(coordinate)
        if self.error is not None:
            raise self.error
        return self.result


class _FakeStore:
    """The smallest class that satisfies ``GeocodeCacheStore``, logging I/O.

    Attributes:
        data (dict[str, str]): The stored payloads.
        reads (list[str]): Keys read, in order.
        writes (list[tuple[str, str, int]]): ``(key, value, ttl)`` per write.
    """

    def __init__(self) -> None:
        """Start empty, with empty read and write logs."""
        self.data: dict[str, str] = {}
        self.reads: list[str] = []
        self.writes: list[tuple[str, str, int]] = []

    async def get(self, key: str) -> str | None:
        """Record the read and answer from memory.

        Args:
            key: The key read.

        Returns:
            The stored payload, or ``None`` on a miss.
        """
        self.reads.append(key)
        return self.data.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """Record the write and keep the payload.

        Args:
            key: The key written.
            value: The payload to keep.
            ttl_seconds: The TTL asked for.
        """
        self.writes.append((key, value, ttl_seconds))
        self.data[key] = value


class _BrokenReadStore(_FakeStore):
    """A store whose reads raise, like Redis with the network down."""

    async def get(self, key: str) -> str | None:
        """Raise instead of reading.

        Args:
            key: The key read.

        Raises:
            RuntimeError: Always — the simulated outage.
        """
        raise RuntimeError("redis is down")


class _BrokenWriteStore(_FakeStore):
    """A store whose writes raise, like Redis with the network down."""

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """Raise instead of writing.

        Args:
            key: The key written.
            value: The payload that never lands.
            ttl_seconds: The TTL that never lands.

        Raises:
            RuntimeError: Always — the simulated outage.
        """
        raise RuntimeError("redis is down")


class TestGeocodeCacheStore:
    def test_a_plain_class_satisfies_the_protocol(self) -> None:
        """Two methods are the whole contract."""
        assert isinstance(_FakeStore(), GeocodeCacheStore)
        assert isinstance(InMemoryGeocodeCacheStore(), GeocodeCacheStore)

    def test_something_else_does_not(self) -> None:
        """The protocol is not a rubber stamp."""
        assert not isinstance(object(), GeocodeCacheStore)


class TestCachedGeocodingBackend:
    async def test_is_a_geocoding_backend(self) -> None:
        """The wrapper can stand in for any backend, fakes included."""
        cached = CachedGeocodingBackend(backend=_StubBackend(), store=_FakeStore())
        assert isinstance(cached, GeocodingBackend)

    async def test_miss_then_hit_asks_the_backend_once(self) -> None:
        """The first call goes to the backend, the second to the store."""
        store = _FakeStore()
        backend = _StubBackend(result=_sample_result())
        cached = CachedGeocodingBackend(backend=backend, store=store)

        first = await cached.reverse(Coordinate(latitude=-5.0892, longitude=-42.8019))
        second = await cached.reverse(Coordinate(latitude=-5.0892, longitude=-42.8019))

        assert first is not None
        assert second is not None
        assert backend.calls == ["reverse"]
        assert store.reads == [
            "geocoding:reverse:v1:-5.089:-42.802",
            "geocoding:reverse:v1:-5.089:-42.802",
        ]
        assert store.writes[0][0] == "geocoding:reverse:v1:-5.089:-42.802"

    async def test_a_hit_keeps_the_structured_address(self) -> None:
        """The JSON round trip does not drop what Nominatim structured."""
        store = _FakeStore()
        backend = _StubBackend(result=_sample_result())
        cached = CachedGeocodingBackend(backend=backend, store=store)

        await cached.geocode("Teresina")
        hit = await cached.geocode("Teresina")

        assert hit is not None
        assert hit.display_name == "Teresina, Piauí, Brasil"
        assert hit.place_type == "city"
        assert hit.address is not None
        assert hit.address.city == "Teresina"
        assert hit.address.state_code == "PI"
        assert hit.address.postcode == "64001-490"

    async def test_default_ttl_is_thirty_days(self) -> None:
        """Every entry the wrapper writes expires on the documented date."""
        store = _FakeStore()
        cached = CachedGeocodingBackend(
            backend=_StubBackend(result=_sample_result()),
            store=store,
        )

        await cached.geocode("Teresina")

        assert store.writes[0][2] == DEFAULT_GEOCODE_CACHE_TTL_SECONDS
        assert store.writes[0][2] == 2_592_000

    async def test_prefix_precision_and_ttl_reach_the_key(self) -> None:
        """The knobs change the key and the write, not the behaviour."""
        store = _FakeStore()
        cached = CachedGeocodingBackend(
            backend=_StubBackend(result=_sample_result()),
            store=store,
            precision=4,
            ttl_seconds=60,
            key_prefix="app",
        )

        await cached.reverse(Coordinate(latitude=-5.08924, longitude=-42.80194))

        key, _payload, ttl = store.writes[0]
        assert key == "app:reverse:v1:-5.0892:-42.8019"
        assert ttl == 60


class TestReverseRounding:
    async def test_the_backend_receives_the_rounded_coordinate(self) -> None:
        """The exact reading stays in this process."""
        backend = _StubBackend(result=_sample_result())
        cached = CachedGeocodingBackend(backend=backend, store=_FakeStore())

        await cached.reverse(Coordinate(latitude=-5.08924, longitude=-42.80194))

        assert backend.coordinates == [Coordinate(latitude=-5.089, longitude=-42.802)]

    async def test_points_under_fifty_metres_share_one_entry(self) -> None:
        """Two readings of one house are one cache entry."""
        close_a = Coordinate(latitude=-5.0890, longitude=-42.8010)
        close_b = Coordinate(latitude=-5.0893, longitude=-42.8012)
        assert haversine_km(close_a, close_b) * 1000 < 50

        store = _FakeStore()
        backend = _StubBackend(result=_sample_result())
        cached = CachedGeocodingBackend(backend=backend, store=store)

        await cached.reverse(close_a)
        await cached.reverse(close_b)

        assert backend.calls == ["reverse"]
        assert len(store.writes) == 1
        assert store.writes[0][0] == "geocoding:reverse:v1:-5.089:-42.801"

    async def test_negative_zero_does_not_split_the_entry(self) -> None:
        """A point either side of the equator keys once, not twice."""
        store = _FakeStore()
        backend = _StubBackend(result=_sample_result())
        cached = CachedGeocodingBackend(backend=backend, store=store)

        await cached.reverse(Coordinate(latitude=-0.0001, longitude=-0.0001))
        await cached.reverse(Coordinate(latitude=0.0001, longitude=0.0001))

        assert backend.calls == ["reverse"]
        assert store.writes[0][0] == "geocoding:reverse:v1:0.000:0.000"


class TestSearchKeys:
    async def test_the_key_carries_no_query_text(self) -> None:
        """A cache listing does not disclose what people searched for."""
        store = _FakeStore()
        cached = CachedGeocodingBackend(
            backend=_StubBackend(result=_sample_result()),
            store=store,
        )

        await cached.geocode("Av. Paulista, 1578 - São Paulo")

        key = store.writes[0][0]
        assert key.startswith("geocoding:search:v1:")
        assert "Paulista" not in key
        assert "São" not in key
        assert len(key) == len("geocoding:search:v1:") + 64

    async def test_case_and_padding_share_one_entry(self) -> None:
        """The key normalizes; the backend still gets the original text."""
        store = _FakeStore()
        backend = _StubBackend(result=_sample_result())
        cached = CachedGeocodingBackend(backend=backend, store=store)

        await cached.geocode("  Av. Paulista, 1578  ")
        await cached.geocode("AV. PAULISTA, 1578")

        assert backend.calls == ["geocode"]
        assert backend.queries == ["  Av. Paulista, 1578  "]


class TestStoreFailures:
    async def test_a_read_failure_falls_back_to_the_backend(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The cache being down costs a request, not an answer."""
        backend = _StubBackend(result=_sample_result())
        cached = CachedGeocodingBackend(backend=backend, store=_BrokenReadStore())

        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            hit = await cached.reverse(Coordinate(latitude=-5.0892, longitude=-42.8019))

        assert hit is not None
        assert backend.calls == ["reverse"]
        assert "cache read failed" in caplog.text

    async def test_a_write_failure_keeps_the_answer(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A store that cannot be written still returns the fresh result."""
        store = _BrokenWriteStore()
        backend = _StubBackend(result=_sample_result())
        cached = CachedGeocodingBackend(backend=backend, store=store)

        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            hit = await cached.reverse(Coordinate(latitude=-5.0892, longitude=-42.8019))

        assert hit is not None
        assert hit.address is not None
        assert store.writes == []
        assert "cache write failed" in caplog.text

    async def test_a_backend_failure_propagates_unchanged(self) -> None:
        """The wrapper does not turn the backend's error into a miss."""
        store = _FakeStore()
        backend = _StubBackend()
        backend.error = RuntimeError("Nominatim reverse failed: timeout")
        cached = CachedGeocodingBackend(backend=backend, store=store)

        with pytest.raises(RuntimeError, match="Nominatim reverse failed"):
            await cached.reverse(Coordinate(latitude=-5.0892, longitude=-42.8019))

        assert store.writes == []

    async def test_a_corrupt_payload_is_logged_and_refetched(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Garbage in the store is a miss, not an exception to the caller."""
        store = _FakeStore()
        backend = _StubBackend(result=_sample_result())
        cached = CachedGeocodingBackend(backend=backend, store=store)

        await cached.reverse(Coordinate(latitude=-5.0892, longitude=-42.8019))
        store.data[store.writes[0][0]] = "{not a geocode result"

        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            hit = await cached.reverse(Coordinate(latitude=-5.0892, longitude=-42.8019))

        assert hit is not None
        assert backend.calls == ["reverse", "reverse"]
        assert "failed to parse" in caplog.text


class TestCacheMisses:
    async def test_a_miss_is_not_cached_by_default(self) -> None:
        """A ragged miss stays under the 1 req/s budget until opted in."""
        store = _FakeStore()
        backend = _StubBackend(result=None)
        cached = CachedGeocodingBackend(backend=backend, store=store)

        first = await cached.reverse(Coordinate(latitude=-5.0892, longitude=-42.8019))
        second = await cached.reverse(Coordinate(latitude=-5.0892, longitude=-42.8019))

        assert first is None
        assert second is None
        assert backend.calls == ["reverse", "reverse"]
        assert store.writes == []

    async def test_cache_misses_stores_the_absence(self) -> None:
        """With the flag on, the second ask costs no request."""
        store = _FakeStore()
        backend = _StubBackend(result=None)
        cached = CachedGeocodingBackend(
            backend=backend,
            store=store,
            cache_misses=True,
        )

        first = await cached.reverse(Coordinate(latitude=-5.0892, longitude=-42.8019))
        second = await cached.reverse(Coordinate(latitude=-5.0892, longitude=-42.8019))

        assert first is None
        assert second is None
        assert backend.calls == ["reverse"]
        assert store.writes[0][1] == ""

    async def test_a_hit_result_is_stored_whether_or_not_the_flag_is_on(self) -> None:
        """``cache_misses`` only governs the ``None`` answer."""
        for cache_misses in (False, True):
            store = _FakeStore()
            backend = _StubBackend(result=_sample_result())
            cached = CachedGeocodingBackend(
                backend=backend,
                store=store,
                cache_misses=cache_misses,
            )
            await cached.geocode("Teresina")
            assert store.writes[0][1]


class TestInMemoryGeocodeCacheStore:
    async def test_value_comes_back_within_the_ttl(self) -> None:
        """A live entry is served as written."""
        store = InMemoryGeocodeCacheStore()
        await store.set("k", "v", 60)
        assert await store.get("k") == "v"

    async def test_value_expires_after_the_ttl(self) -> None:
        """Expiry is decided at read time against a monotonic clock."""
        store = InMemoryGeocodeCacheStore()
        await store.set("k", "v", 1)
        await asyncio.sleep(1.05)
        assert await store.get("k") is None

    async def test_ttl_zero_expires_at_once(self) -> None:
        """Zero seconds is a legal TTL and means "do not keep this"."""
        store = InMemoryGeocodeCacheStore()
        await store.set("k", "v", 0)
        assert await store.get("k") is None

    async def test_unknown_key_is_a_miss(self) -> None:
        """Absence answers ``None``, never an exception."""
        store = InMemoryGeocodeCacheStore()
        assert await store.get("missing") is None


class TestRedisGeocodeCacheStore:
    async def test_round_trips_text_and_applies_the_ttl(self) -> None:
        """The store speaks Redis ``GET``/``SET ... EX`` and answers text."""
        fakeredis = pytest.importorskip("fakeredis")
        client = fakeredis.aioredis.FakeRedis()
        store = RedisGeocodeCacheStore(client)

        await store.set("geocoding:reverse:v1:0.000:0.000", "payload", 60)

        assert await store.get("geocoding:reverse:v1:0.000:0.000") == "payload"
        ttl: int = await client.ttl("geocoding:reverse:v1:0.000:0.000")
        assert 0 < ttl <= 60
        raw: bytes | None = await client.get("geocoding:reverse:v1:0.000:0.000")
        assert raw == b"payload"
        await client.aclose()

    async def test_miss_returns_none(self) -> None:
        """An absent key is ``None``, the shape the wrapper expects."""
        fakeredis = pytest.importorskip("fakeredis")
        client = fakeredis.aioredis.FakeRedis()
        store = RedisGeocodeCacheStore(client)
        assert await store.get("missing") is None
        await client.aclose()

    async def test_prefix_namespaces_the_redis_key(self) -> None:
        """The prefix lands on the wire, not only in the wrapper's key."""
        fakeredis = pytest.importorskip("fakeredis")
        client = fakeredis.aioredis.FakeRedis()
        store = RedisGeocodeCacheStore(client, prefix="app:")

        await store.set("k", "v", 60)

        assert await store.get("k") == "v"
        assert await client.get("app:k") == b"v"
        await client.aclose()

    async def test_the_wrapper_serves_from_it(self) -> None:
        """End to end: backend, wrapper, Redis store, second call a hit."""
        fakeredis = pytest.importorskip("fakeredis")
        client = fakeredis.aioredis.FakeRedis()
        backend = _StubBackend(result=_sample_result())
        cached = CachedGeocodingBackend(
            backend=backend,
            store=RedisGeocodeCacheStore(client),
        )

        first = await cached.geocode("Teresina")
        second = await cached.geocode("Teresina")

        assert first is not None
        assert second is not None
        assert second.address is not None
        assert second.address.city == "Teresina"
        assert backend.calls == ["geocode"]
        await client.aclose()
