"""Async Redis connection manager mirroring AsyncDatabaseManager."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

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

    @asynccontextmanager
    async def get_client_context(self) -> AsyncIterator[Redis]:
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
            result: Any = await self.client.ping()  # type: ignore[misc]
        except Exception as exc:
            logger.warning("Redis health check failed: %s", exc)
            return False
        return bool(result)


__all__: list[str] = [
    "AsyncRedisManager",
]
