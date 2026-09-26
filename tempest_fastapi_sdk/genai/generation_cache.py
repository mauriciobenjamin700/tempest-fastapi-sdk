"""Prompt-to-completion caching for the text generators.

Deterministic generations (greedy, or ``temperature == 0``) always produce the
same text for the same prompt + parameters — so they are safe to cache and skip
the model entirely on a repeat. Non-deterministic (sampling) generations are
**never** cached: returning a stale sample would silently defeat the sampling
the caller asked for.

Mirrors the embedding-cache design: a sync :class:`GenerationCache` and an
async :class:`AsyncGenerationCache` protocol, an in-memory default, and a Redis
store for multi-worker reuse — the generator awaits whichever it is given at one
call site (:func:`cached_generate`).
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from redis.asyncio import Redis


@runtime_checkable
class GenerationCache(Protocol):
    """A synchronous prompt→completion cache (e.g. in-memory)."""

    def get(self, key: str) -> str | None:
        """Return the cached completion for ``key`` or ``None``.

        Args:
            key (str): Cache key derived from the prompt and generation
                settings.

        Returns:
            str | None: The cached completion, or ``None`` on a miss.
        """
        ...

    def set(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``.

        Args:
            key (str): Cache key derived from the prompt and generation
                settings.
            value (str): The completion to store.
        """
        ...


@runtime_checkable
class AsyncGenerationCache(Protocol):
    """An asynchronous prompt→completion cache (e.g. Redis-backed)."""

    async def get(self, key: str) -> str | None:
        """Return the cached completion for ``key`` or ``None``.

        Args:
            key (str): Cache key derived from the prompt and generation
                settings.

        Returns:
            str | None: The cached completion, or ``None`` on a miss.
        """
        ...

    async def set(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``.

        Args:
            key (str): Cache key derived from the prompt and generation
                settings.
            value (str): The completion to store.
        """
        ...


_DEFAULT_MAX_ENTRIES: int = 1024
"""Default capacity of :class:`InMemoryGenerationCache` before LRU eviction."""


class InMemoryGenerationCache:
    """A process-local LRU cache. Not shared across workers.

    Bounded by ``max_entries``: once full, storing a new key evicts the
    least-recently-used one (a read counts as a use). Without the bound a
    long-lived worker that sees distinct deterministic prompts grows the
    store forever.

    Attributes:
        max_entries (int | None): Capacity before eviction, or ``None`` for
            an unbounded store.
    """

    def __init__(
        self,
        *,
        max_entries: int | None = _DEFAULT_MAX_ENTRIES,
    ) -> None:
        """Initialize the empty cache.

        Args:
            max_entries (int | None): Maximum number of completions kept.
                ``None`` disables eviction (the pre-bound behaviour).

        Raises:
            ValueError: When ``max_entries`` is lower than 1.
        """
        if max_entries is not None and max_entries < 1:
            raise ValueError("max_entries must be >= 1 or None")
        self.max_entries: int | None = max_entries
        self._store: OrderedDict[str, str] = OrderedDict()

    def __len__(self) -> int:
        """Return the number of cached completions.

        Returns:
            int: How many entries the store currently holds.
        """
        return len(self._store)

    def get(self, key: str) -> str | None:
        """Return the cached completion for ``key`` or ``None``.

        A hit marks the entry as most recently used.

        Args:
            key (str): Cache key derived from the prompt and generation
                settings.

        Returns:
            str | None: The cached completion, or ``None`` on a miss.
        """
        value = self._store.get(key)
        if value is not None:
            self._store.move_to_end(key)
        return value

    def set(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``, evicting the LRU entry when full.

        Args:
            key (str): Cache key derived from the prompt and generation
                settings.
            value (str): The completion to store.
        """
        self._store[key] = value
        self._store.move_to_end(key)
        if self.max_entries is not None:
            while len(self._store) > self.max_entries:
                self._store.popitem(last=False)


class RedisGenerationCache:
    """A Redis-backed async cache — completions shared across workers.

    Example:

        >>> from redis.asyncio import Redis
        >>> cache = RedisGenerationCache(Redis.from_url("redis://localhost"))

    Attributes:
        namespace (str): Key prefix in Redis.
        ttl_seconds (int | None): Optional expiry per entry.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        namespace: str = "genai:gen:",
        ttl_seconds: int | None = None,
    ) -> None:
        """Initialize the cache.

        Args:
            redis (Redis): An ``redis.asyncio.Redis`` client.
            namespace (str): Prefix prepended to every key.
            ttl_seconds (int | None): Per-entry TTL, or ``None`` for no expiry.
        """
        self._redis = redis
        self.namespace = namespace
        self.ttl_seconds = ttl_seconds

    async def get(self, key: str) -> str | None:
        """Return the cached completion for ``key`` or ``None``.

        Args:
            key (str): Cache key derived from the prompt and generation
                settings.

        Returns:
            str | None: The cached completion, or ``None`` on a miss.
        """
        raw = await self._redis.get(self.namespace + key)
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)

    async def set(self, key: str, value: str) -> None:
        """Store ``value`` under ``key`` (with the configured TTL).

        Args:
            key (str): Cache key derived from the prompt and generation
                settings.
            value (str): The completion to store.
        """
        await self._redis.set(self.namespace + key, value, ex=self.ttl_seconds)


def is_deterministic(params: dict[str, Any]) -> bool:
    """Return ``True`` when ``params`` describe a reproducible generation.

    Args:
        params (dict[str, Any]): Merged generation parameters (config +
            per-call overrides).

    Returns:
        bool: ``True`` when ``do_sample is False`` or ``temperature == 0``.
    """
    if params.get("do_sample") is False:
        return True
    return params.get("temperature") == 0 or params.get("temperature") == 0.0


def make_generation_key(
    model_id: str,
    prompt: str,
    params: dict[str, Any],
    *,
    operation: str = "generate",
    identity: Mapping[str, Any] | None = None,
) -> str:
    """Build a stable cache key from the model, prompt and parameters.

    ``operation`` separates call shapes that share a prompt string: a
    ``chat`` keyed on the serialized messages must not answer a
    ``generate`` whose prompt happens to be that same JSON text.
    ``identity`` carries whatever else pins the weights behind
    ``model_id`` (revision, quantization), so two generators of the same
    model id loaded differently never read each other's completions.

    The default ``operation="generate"`` with no ``identity`` produces the
    same digest as before either argument existed, so an existing Redis
    cache of plain ``generate`` calls stays warm.

    Args:
        model_id (str): The model identifier.
        prompt (str): The input prompt (or serialized messages).
        params (dict[str, Any]): Generation parameters that affect the output.
        operation (str): The call shape (``"generate"``, ``"chat"`` …).
        identity (Mapping[str, Any] | None): Extra model identity fields;
            entries whose value is ``None`` are ignored.

    Returns:
        str: A hex SHA-256 digest.
    """
    body: dict[str, Any] = {"model": model_id, "prompt": prompt, "params": params}
    if operation != "generate":
        body["op"] = operation
    pinned = {k: v for k, v in (identity or {}).items() if v is not None}
    if pinned:
        body["identity"] = pinned
    payload = json.dumps(body, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


async def _cache_get(cache: Any, key: str) -> str | None:
    """Await-or-call ``cache.get`` regardless of sync/async."""
    result = cache.get(key)
    if isinstance(result, Awaitable):
        result = await result
    return None if result is None else str(result)


async def _cache_set(cache: Any, key: str, value: str) -> None:
    """Await-or-call ``cache.set`` regardless of sync/async."""
    result = cache.set(key, value)
    if isinstance(result, Awaitable):
        await result


async def cached_generate(
    cache: GenerationCache | AsyncGenerationCache | None,
    model_id: str,
    prompt: str,
    params: dict[str, Any],
    producer: Callable[[], Awaitable[str]],
    *,
    operation: str = "generate",
    identity: Mapping[str, Any] | None = None,
) -> str:
    """Return a cached completion when possible, else produce and cache it.

    Args:
        cache (GenerationCache | AsyncGenerationCache | None): The cache, or
            ``None`` to disable caching.
        model_id (str): The model identifier (part of the key).
        prompt (str): The prompt (or serialized messages).
        params (dict[str, Any]): Generation parameters (key + determinism).
        producer (Callable[[], Awaitable[str]]): Runs the real generation.
        operation (str): The call shape, part of the key (see
            :func:`make_generation_key`).
        identity (Mapping[str, Any] | None): Model identity fields beyond
            ``model_id`` (revision, quantization), part of the key.

    Returns:
        str: The completion — from cache on a deterministic hit, otherwise
        freshly produced (and cached when deterministic).
    """
    if cache is None or not is_deterministic(params):
        return await producer()
    key = make_generation_key(
        model_id, prompt, params, operation=operation, identity=identity
    )
    hit = await _cache_get(cache, key)
    if hit is not None:
        return hit
    result = await producer()
    await _cache_set(cache, key, result)
    return result


__all__: list[str] = [
    "AsyncGenerationCache",
    "GenerationCache",
    "InMemoryGenerationCache",
    "RedisGenerationCache",
    "cached_generate",
    "is_deterministic",
    "make_generation_key",
]
