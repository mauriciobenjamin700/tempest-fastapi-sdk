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
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from redis.asyncio import Redis


@runtime_checkable
class GenerationCache(Protocol):
    """A synchronous prompt→completion cache (e.g. in-memory)."""

    def get(self, key: str) -> str | None:
        """Return the cached completion for ``key`` or ``None``."""
        ...

    def set(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``."""
        ...


@runtime_checkable
class AsyncGenerationCache(Protocol):
    """An asynchronous prompt→completion cache (e.g. Redis-backed)."""

    async def get(self, key: str) -> str | None:
        """Return the cached completion for ``key`` or ``None``."""
        ...

    async def set(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``."""
        ...


class InMemoryGenerationCache:
    """A process-local dict cache. Not shared across workers."""

    def __init__(self) -> None:
        """Initialize the empty cache."""
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        """Return the cached completion for ``key`` or ``None``."""
        return self._store.get(key)

    def set(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``."""
        self._store[key] = value


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
        """Return the cached completion for ``key`` or ``None``."""
        raw = await self._redis.get(self.namespace + key)
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)

    async def set(self, key: str, value: str) -> None:
        """Store ``value`` under ``key`` (with the configured TTL)."""
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


def make_generation_key(model_id: str, prompt: str, params: dict[str, Any]) -> str:
    """Build a stable cache key from the model, prompt and parameters.

    Args:
        model_id (str): The model identifier.
        prompt (str): The input prompt (or serialized messages).
        params (dict[str, Any]): Generation parameters that affect the output.

    Returns:
        str: A hex SHA-256 digest.
    """
    payload = json.dumps(
        {"model": model_id, "prompt": prompt, "params": params},
        sort_keys=True,
        default=str,
    )
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
) -> str:
    """Return a cached completion when possible, else produce and cache it.

    Args:
        cache (GenerationCache | AsyncGenerationCache | None): The cache, or
            ``None`` to disable caching.
        model_id (str): The model identifier (part of the key).
        prompt (str): The prompt (or serialized messages).
        params (dict[str, Any]): Generation parameters (key + determinism).
        producer (Callable[[], Awaitable[str]]): Runs the real generation.

    Returns:
        str: The completion — from cache on a deterministic hit, otherwise
        freshly produced (and cached when deterministic).
    """
    if cache is None or not is_deterministic(params):
        return await producer()
    key = make_generation_key(model_id, prompt, params)
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
