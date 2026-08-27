"""Async Redis connection manager mirroring AsyncDatabaseManager."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)


def _require_redis() -> Any:
    """Import the ``redis.asyncio`` module or raise a helpful error.

    Returns:
        Any: The ``redis.asyncio`` module.

    Raises:
        ImportError: When the optional ``[cache]`` extra was not
            installed (``pip install tempest-fastapi-sdk[cache]``).
    """
    try:
        from redis import asyncio as redis_async
    except ImportError as exc:
        raise ImportError(
            "Redis support requires the optional [cache] extra. "
            "Install with: pip install tempest-fastapi-sdk[cache]",
        ) from exc
    return redis_async


class _LiveRedisClientProxy:
    """Forward every attribute to whichever client is live right now.

    Exists because the two lifecycles do not line up: FastAPI wants
    middleware registered at import time, and ``add_middleware`` after
    startup raises -- but :meth:`AsyncRedisManager.connect` only runs in
    the lifespan. A store handed ``manager.client`` at import time would
    hit the guard; one handed the raw client object would go stale, since
    :meth:`AsyncRedisManager.disconnect` drops it and the next
    :meth:`AsyncRedisManager.connect` builds a **new** one.

    Resolving the client per attribute access solves both: the handle is
    constructible before the first connect and keeps working across a
    reconnect, because it holds the manager rather than the client.

    Nothing is cached here on purpose. The cost is one extra attribute
    lookup per command, against a network round trip.
    """

    def __init__(self, manager: AsyncRedisManager) -> None:
        """Bind the proxy to the manager that owns the client.

        Args:
            manager (AsyncRedisManager): The manager to resolve against
                on every attribute access.
        """
        self._manager: AsyncRedisManager = manager

    def __getattr__(self, name: str) -> Any:
        """Resolve ``name`` on the live client.

        Args:
            name (str): The attribute being read.

        Returns:
            Any: The attribute of the currently live client.

        Raises:
            RuntimeError: When no client is live -- that is, before the
                first :meth:`AsyncRedisManager.connect` or after
                :meth:`AsyncRedisManager.disconnect`. Stores built with
                ``fail_open=True`` already treat a raising backend as a
                degraded one.
        """
        return getattr(self._manager.client, name)

    def __repr__(self) -> str:
        """Return a debug representation naming the target URL.

        Returns:
            str: Something like ``<redis client proxy redis://...>``.
        """
        return f"<redis client proxy {self._manager.url}>"


class AsyncRedisManager:
    """Manage the lifecycle of a single async Redis client.

    Mirrors the public surface of
    :class:`tempest_fastapi_sdk.AsyncDatabaseManager` so application
    bootstrapping stays uniform across backends. The actual client is
    created on first :meth:`connect` call; in-process callers can use
    :meth:`get_client_context` from a FastAPI dependency or any async
    context manager.

    Attributes:
        url (str): The Redis connection URL.
        decode_responses (bool): Whether the underlying client
            decodes responses to ``str``.
    """

    def __init__(
        self,
        url: str,
        *,
        decode_responses: bool = True,
        **client_kwargs: Any,
    ) -> None:
        """Initialize the manager (no connection opened yet).

        Args:
            url (str): The Redis URL (``redis://...`` or
                ``rediss://...`` for TLS).
            decode_responses (bool): Whether to decode bytes to
                strings on every command.
            **client_kwargs (Any): Extra kwargs forwarded to
                ``redis.asyncio.Redis.from_url``.
        """
        self.url: str = url
        self.decode_responses: bool = decode_responses
        self._client_kwargs: dict[str, Any] = client_kwargs
        self._client: Redis | None = None
        self._proxy: _LiveRedisClientProxy = _LiveRedisClientProxy(self)

    async def connect(self) -> None:
        """Open the underlying Redis client.

        Safe to call multiple times — subsequent calls are no-ops
        while the same client is alive.
        """
        if self._client is not None:
            return
        redis_async = _require_redis()
        self._client = redis_async.Redis.from_url(
            self.url,
            decode_responses=self.decode_responses,
            **self._client_kwargs,
        )

    async def disconnect(self) -> None:
        """Close the underlying client and release its connection pool."""
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None

    @property
    def client(self) -> Redis:
        """Return the live client.

        Returns:
            Redis: The connected Redis client.

        Raises:
            RuntimeError: When :meth:`connect` was not called yet.
        """
        if self._client is None:
            raise RuntimeError(
                "AsyncRedisManager.connect() must be called before "
                "accessing the client.",
            )
        return self._client

    @property
    def client_proxy(self) -> Redis:
        """Return a stable handle that stays valid across reconnects.

        Use this -- not :attr:`client` -- wherever a Redis-backed store
        has to be built **before** the lifespan runs, which is every
        store passed to ``add_middleware``:

        .. code-block:: python

            redis = AsyncRedisManager(settings.REDIS_URL)

            app.add_middleware(
                RateLimitMiddleware,
                store=RedisRateLimitStore(redis.client_proxy),
            )

        The same object comes back on every call, and each command it
        forwards resolves the client that is live at that moment, so a
        :meth:`disconnect` / :meth:`connect` cycle does not leave the
        store holding a dead client.

        It is a forwarding handle, not a ``Redis`` instance: the return
        type is declared as ``Redis`` so it type-checks into every store
        parameter, but ``isinstance(manager.client_proxy, Redis)`` is
        ``False`` and dunder protocols looked up on the type (``async
        with proxy``) do not forward. Reading an attribute before the
        first :meth:`connect` raises ``RuntimeError``, same as
        :attr:`client` -- what changes is that *building* the store no
        longer does.

        Returns:
            Redis: The forwarding handle onto the live client.
        """
        return cast("Redis", self._proxy)

    @asynccontextmanager
    async def get_client_context(self) -> AsyncGenerator[Redis, None]:
        """Yield the live client inside an ``async with`` block.

        The manager owns the lifecycle — exiting the context does
        NOT close the underlying client. Use :meth:`disconnect`
        during application shutdown instead.

        Yields:
            Redis: The connected client.
        """
        yield self.client

    async def client_dependency(self) -> AsyncIterator[Redis]:
        """Async generator dependency suitable for FastAPI ``Depends``.

        Yields:
            Redis: The connected client.
        """
        yield self.client

    async def health_check(self) -> bool:
        """Return ``True`` when ``PING`` succeeds.

        Errors are caught and logged at WARNING level — the health
        router treats exceptions as a failed check.

        Returns:
            bool: ``True`` when the server responded with ``PONG``.
        """
        try:
            result: Any = await self.client.ping()
        except Exception as exc:
            logger.warning("Redis health check failed: %s", exc)
            return False
        return bool(result)


__all__: list[str] = [
    "AsyncRedisManager",
]
