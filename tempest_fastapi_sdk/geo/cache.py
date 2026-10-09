"""Geocoding cache — a pluggable string store plus a backend wrapper.

Reverse geocoding sits on the request path of any service that shows a city
from the phone's GPS, the public Nominatim instance answers ~1 request per
second, and the same house always resolves the same way. This module is
that cache, factored out of the services that each grew their own:

* :class:`GeocodeCacheStore` — the two-method Protocol a store must satisfy.
* :class:`InMemoryGeocodeCacheStore` — a TTL store for tests and dev.
* :class:`RedisGeocodeCacheStore` — the same over an async Redis client,
  with ``redis`` never imported here, so importing :mod:`tempest_fastapi_sdk.geo`
  still costs no optional extra.
* :class:`CachedGeocodingBackend` — the wrapper: rounds, keys, serializes
  and, when the store fails, logs and asks the wrapped backend anyway.

The store is quota protection, not a source of truth. Everything that can
go wrong inside it degrades to a cache miss; the only failure that reaches
the caller is the wrapped backend's own.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Awaitable
from typing import Protocol, runtime_checkable

from tempest_fastapi_sdk.geo.geocoding import GeocodingBackend
from tempest_fastapi_sdk.geo.schemas import Coordinate, GeocodeResult

logger: logging.Logger = logging.getLogger(__name__)

DEFAULT_GEOCODE_CACHE_PRECISION: int = 3
"""Decimal places a ``reverse`` coordinate is rounded to before keying.

Three places is a ~111 m grid (measured with
:func:`~tempest_fastapi_sdk.geo.haversine_km` at 10⁻³ of a degree of
latitude); more places fragment the cache without making the answer
better, since a street resolves to the same building.
"""

DEFAULT_GEOCODE_CACHE_TTL_SECONDS: int = 2_592_000
"""Default entry lifetime: 30 days.

Long enough that a whole device fleet settles into hits, short enough that
a map edit or a corrected place reaches users within a month. Place
resolutions are stable on that horizon — the coordinates of a doorway do
not move — while a week would re-buy quota every month and "forever" would
pin a wrong answer.
"""

DEFAULT_GEOCODE_CACHE_KEY_PREFIX: str = "geocoding"
"""First segment of every key the wrapper builds."""

_KEY_VERSION: str = "v1"
"""Version of the key *format*, bumped when the layout changes.

Bumping it retires every old key at once instead of serving a shape the
new code no longer expects.
"""

_NO_RESULT: str = ""
"""Stored value meaning "the backend answered nothing here".

``None`` already means "no entry" for :class:`GeocodeCacheStore`, so the
absence of an answer needs a value of its own — and no serialized
:class:`~tempest_fastapi_sdk.geo.GeocodeResult` is ever an empty string.
"""


@runtime_checkable
class GeocodeCacheStore(Protocol):
    """Where :class:`CachedGeocodingBackend` keeps serialized answers.

    Both methods deal in strings: the value is a JSON payload, so anything
    that can hold text can be a store — Redis, an in-process dict, the
    cache a service already runs. Implement the two methods and the wrapper
    does the rest.
    """

    async def get(self, key: str) -> str | None:
        """Return the payload stored under ``key``.

        Args:
            key: The cache key the wrapper built.

        Returns:
            The stored payload, or ``None`` when there is no live entry —
            expired counts as no entry.

        Raises:
            Exception: Any store failure. The wrapper logs it and treats it
                as a miss, never as an error to the caller.
        """
        ...

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """Store ``value`` under ``key`` for ``ttl_seconds``.

        Args:
            key: The cache key the wrapper built.
            value: The payload to keep.
            ttl_seconds: How long the entry stays live.

        Raises:
            Exception: Any store failure. The wrapper logs it and drops the
                write, the answer still reaching the caller.
        """
        ...


class InMemoryGeocodeCacheStore:
    """A :class:`GeocodeCacheStore` holding entries in this process.

    Expiry is checked on read against :func:`time.monotonic`, so a wall-clock
    adjustment never keeps an entry alive or kills one early. The methods do
    not await, so under asyncio there is no interleaving point and no lock
    is needed. State is per worker: reach for
    :class:`RedisGeocodeCacheStore` the moment more than one process serves
    traffic.
    """

    def __init__(self) -> None:
        """Start with an empty store."""
        self._entries: dict[str, tuple[float, str]] = {}

    async def get(self, key: str) -> str | None:
        """Return the live payload under ``key``, dropping it when expired.

        Args:
            key: The cache key to read.

        Returns:
            The stored payload, or ``None`` when absent or past its TTL.
        """
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            del self._entries[key]
            return None
        return value

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """Store ``value`` under ``key``, expiring after ``ttl_seconds``.

        Args:
            key: The cache key to write.
            value: The payload to keep.
            ttl_seconds: How long the entry stays live. ``0`` expires it on
                the next read.
        """
        self._entries[key] = (time.monotonic() + ttl_seconds, value)


@runtime_checkable
class _GeocodeRedisClient(Protocol):
    """The subset of ``redis.asyncio.Redis`` :class:`RedisGeocodeCacheStore` reads.

    Positional-only parameters and an ``Awaitable`` return, because the
    client we do not own names its key parameter ``name`` and is typed as
    returning either the value or the awaitable of it: spelling the
    contract our way would reject the very client the docs tell the reader
    to pass. Nothing here imports ``redis``.
    """

    def get(self, key: str, /) -> Awaitable[str | bytes | None]:
        """Return the payload under ``key``, or ``None`` when absent.

        Args:
            key: The cache key to read.

        Returns:
            The stored payload, as text or bytes, or ``None`` on a miss.
        """
        ...

    def set(self, key: str, value: str, /, *, ex: int) -> Awaitable[object]:
        """Store ``value`` under ``key``, expiring after ``ex`` seconds.

        Args:
            key: The cache key to write.
            value: The payload to keep.
            ex: Time-to-live in seconds, the name redis-py uses.

        Returns:
            Whatever the client answers; the store discards it.
        """
        ...


class RedisGeocodeCacheStore:
    """A :class:`GeocodeCacheStore` over an async Redis client.

    Takes the client rather than a manager, like every Redis store the SDK
    ships: pass ``AsyncRedisManager.client_proxy`` when the store is built
    before the lifespan runs, or ``AsyncRedisManager.client`` once inside.
    Requires the ``[cache]`` extra at the call site, never at import time.

    Attributes:
        prefix: Prepended to every key, for services that namespace their
            own Redis keys. The wrapper's keys already start with
            ``geocoding:``, so the default adds nothing.
    """

    def __init__(
        self,
        client: _GeocodeRedisClient,
        *,
        prefix: str = "",
    ) -> None:
        """Wrap a connected (or proxy) async Redis client.

        Args:
            client: Anything with async ``get``/``set`` shaped like
                ``redis.asyncio.Redis`` — typically
                ``AsyncRedisManager.client_proxy``.
            prefix: Optional namespace prepended to every key.
        """
        self._client: _GeocodeRedisClient = client
        self.prefix: str = prefix

    async def get(self, key: str) -> str | None:
        """Read the key, decoding a bytes reply to text.

        Args:
            key: The cache key to read.

        Returns:
            The stored payload, or ``None`` on a miss. A client built with
            ``decode_responses=False`` (the default) answers bytes, which
            are decoded here so callers always see text.
        """
        raw: str | bytes | None = await self._client.get(f"{self.prefix}{key}")
        if raw is None:
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        return raw

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """Write the key with ``ex=ttl_seconds``.

        Args:
            key: The cache key to write.
            value: The payload to keep.
            ttl_seconds: How long the entry stays live.
        """
        await self._client.set(f"{self.prefix}{key}", value, ex=ttl_seconds)


class CachedGeocodingBackend:
    """A :class:`~tempest_fastapi_sdk.geo.GeocodingBackend` that answers from a cache.

    Wraps any backend — :class:`~tempest_fastapi_sdk.geo.NominatimBackend`
    being the one worth wrapping — and consults ``store`` before touching
    the network. Parameters are keyword-only.

    Privacy: ``reverse`` rounds the coordinate to ``precision`` places
    **before** building the key and before calling the wrapped backend, so
    the exact GPS reading never leaves the process — not in the key, not on
    the wire. ``geocode`` keys by a SHA-256 of the trimmed, casefolded
    query, so the text a user typed never appears in a key either.

    Failure policy: any exception from ``store.get``/``store.set`` is logged
    at WARNING and the lookup continues as if the cache were empty — the
    cache is quota protection, not a source of truth. An exception from the
    wrapped backend propagates unchanged.

    Attributes:
        precision: Decimal places used to round the coordinate.
        ttl_seconds: Lifetime given to every entry this backend writes.
        key_prefix: First segment of every key it builds.
        cache_misses: Whether "the backend found nothing" is cached too.
    """

    def __init__(
        self,
        *,
        backend: GeocodingBackend,
        store: GeocodeCacheStore,
        precision: int = DEFAULT_GEOCODE_CACHE_PRECISION,
        ttl_seconds: int = DEFAULT_GEOCODE_CACHE_TTL_SECONDS,
        key_prefix: str = DEFAULT_GEOCODE_CACHE_KEY_PREFIX,
        cache_misses: bool = False,
    ) -> None:
        """Wrap a backend with a cache in front of it.

        Args:
            backend: Where to go on a miss. Its exceptions are the caller's
                to handle, unchanged.
            store: Where entries live. Any object with the two
                :class:`GeocodeCacheStore` methods.
            precision: Decimal places of the coordinate rounding in
                ``reverse`` (3 ≈ 111 m, 4 ≈ 11 m, 5 ≈ 1 m). The rounded
                value is both the key and what the backend is asked.
            ttl_seconds: Lifetime of each entry. Defaults to 30 days
                (:data:`DEFAULT_GEOCODE_CACHE_TTL_SECONDS`), long enough
                that a device fleet settles into hits and short enough that
                a corrected place is not pinned forever.
            key_prefix: Namespace for every key
                (``<prefix>:reverse:v1:…`` / ``<prefix>:search:v1:…``).
            cache_misses: When ``True``, a ``None`` answer is stored as the
                empty string too, so "nobody can find this place" stops
                costing a request. Default ``False``: an answer that may
                change (a new place opening) is re-asked, and the rare miss
                is not worth caching.
        """
        self._backend: GeocodingBackend = backend
        self._store: GeocodeCacheStore = store
        self.precision: int = precision
        self.ttl_seconds: int = ttl_seconds
        self.key_prefix: str = key_prefix
        self.cache_misses: bool = cache_misses

    async def geocode(self, query: str) -> GeocodeResult | None:
        """Resolve free text through the cache, then the wrapped backend.

        Args:
            query: The address or place text. Passed to the backend
                unchanged; only the key is normalized (trimmed and
                casefolded, then hashed).

        Returns:
            The cached or freshly resolved
            :class:`~tempest_fastapi_sdk.geo.GeocodeResult`, or ``None``
            when nothing matched.

        Raises:
            Exception: Whatever the wrapped backend raises — the cache
                never turns a backend failure into a miss or a partial
                result, so a caller can rely on its usual error handling.
        """
        key = self._search_key(query)
        hit, result = await self._from_cache(key)
        if hit:
            return result
        result = await self._backend.geocode(query)
        await self._to_cache(key, result)
        return result

    async def reverse(self, coordinate: Coordinate) -> GeocodeResult | None:
        """Resolve a coordinate through the cache, then the wrapped backend.

        The coordinate is rounded to ``precision`` places first: the rounded
        value is both the cache key and what the backend receives, so two
        readings of the same house share one entry and the exact reading
        stays in this process.

        Args:
            coordinate: The point to reverse-geocode.

        Returns:
            The cached or freshly resolved
            :class:`~tempest_fastapi_sdk.geo.GeocodeResult`, or ``None``
            when nothing matched.

        Raises:
            Exception: Whatever the wrapped backend raises — the cache
                never turns a backend failure into a miss or a partial
                result, so a caller can rely on its usual error handling.
        """
        rounded: Coordinate = self._round(coordinate)
        key = self._reverse_key(rounded)
        hit, result = await self._from_cache(key)
        if hit:
            return result
        result = await self._backend.reverse(rounded)
        await self._to_cache(key, result)
        return result

    def _round(self, coordinate: Coordinate) -> Coordinate:
        """Round a coordinate to :attr:`precision`, never producing ``-0.0``.

        Args:
            coordinate: The point as it arrived.

        Returns:
            The rounded point. ``0.0`` is added after ``round`` because
            ``round(-0.0001, 3)`` is ``-0.0``, which would key as
            ``-0.000`` and split one place into two entries across the
            equator or the prime meridian.
        """
        return Coordinate(
            latitude=round(coordinate.latitude, self.precision) + 0.0,
            longitude=round(coordinate.longitude, self.precision) + 0.0,
        )

    def _reverse_key(self, coordinate: Coordinate) -> str:
        """Build the ``reverse`` key of an already-rounded coordinate.

        Args:
            coordinate: The rounded point.

        Returns:
            ``<prefix>:reverse:v1:<lat>:<lon>`` with the coordinate printed
            at exactly :attr:`precision` places, so one place always spells
            one key.
        """
        return (
            f"{self.key_prefix}:reverse:{_KEY_VERSION}:"
            f"{coordinate.latitude:.{self.precision}f}:"
            f"{coordinate.longitude:.{self.precision}f}"
        )

    def _search_key(self, query: str) -> str:
        """Build the ``search`` key of a query, without the text itself.

        Args:
            query: The free text the caller asked for.

        Returns:
            ``<prefix>:search:v1:<sha256>`` over the trimmed, casefolded
            query — a hex digest, because a cache listing should not
            disclose what people searched for.
        """
        normalized: str = query.strip().casefold()
        digest: str = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return f"{self.key_prefix}:search:{_KEY_VERSION}:{digest}"

    async def _from_cache(self, key: str) -> tuple[bool, GeocodeResult | None]:
        """Read ``key``, telling "nothing cached" from "cached nothing".

        Args:
            key: The key to read.

        Returns:
            ``(hit, result)``. ``hit`` is ``False`` on a miss *and* on a
            payload that no longer parses (logged as a WARNING) — in both
            cases the caller has to ask the wrapped backend. ``hit`` is
            ``True`` with ``result=None`` when the entry records that the
            backend found nothing.
        """
        try:
            raw: str | None = await self._store.get(key)
        except Exception as exc:
            logger.warning("Geocode cache read failed for %s: %s", key, exc)
            return False, None
        if raw is None:
            return False, None
        if raw == _NO_RESULT:
            return True, None
        try:
            return True, GeocodeResult.model_validate_json(raw)
        except ValueError as exc:
            logger.warning(
                "Geocode cache payload for %s failed to parse: %s",
                key,
                exc,
            )
            return False, None

    async def _to_cache(self, key: str, result: GeocodeResult | None) -> None:
        """Write ``result`` under ``key``, swallowing store failures.

        Args:
            key: The key to write.
            result: What the backend answered; ``None`` is written only
                when ``cache_misses`` is on.
        """
        if result is None and not self.cache_misses:
            return
        payload: str = _NO_RESULT if result is None else result.model_dump_json()
        try:
            await self._store.set(key, payload, self.ttl_seconds)
        except Exception as exc:
            logger.warning("Geocode cache write failed for %s: %s", key, exc)


__all__: list[str] = [
    "DEFAULT_GEOCODE_CACHE_KEY_PREFIX",
    "DEFAULT_GEOCODE_CACHE_PRECISION",
    "DEFAULT_GEOCODE_CACHE_TTL_SECONDS",
    "CachedGeocodingBackend",
    "GeocodeCacheStore",
    "InMemoryGeocodeCacheStore",
    "RedisGeocodeCacheStore",
]
