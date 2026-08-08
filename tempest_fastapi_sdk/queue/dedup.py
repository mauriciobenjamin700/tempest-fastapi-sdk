"""Consume-side deduplication for an at-least-once transport.

RabbitMQ redelivers. Not rarely — on worker restart, on a nack with
requeue, on an ack lost to a network blip. The facade has always said so:

> "Delivery is at-least-once, so handlers should be idempotent."

and then offered nothing to make one. Every service ended up writing the
same "have I seen this id?" check, or not writing it and charging the
customer twice.

This module is that check, in two phases, so a worker dying mid-handler
does not turn "processed twice" into "processed never":

1. **claim** — the first delivery marks the key ``in_flight`` and runs.
2. **complete** — success marks it ``done``; the next delivery of the
   same id is skipped.
3. **release** — a failure clears the key, so a retry actually retries.

!!! danger "This is not exactly-once, and nothing here is"
    The mark and the handler's side effect are not atomic. A crash
    between them leaves an ``in_flight`` key that expires, after which
    the message runs again — at-least-once, with a much smaller window.
    Exactly-once across a broker and a database needs the effect and the
    mark in **one transaction**, which is what
    :class:`~tempest_fastapi_sdk.db.BaseOutboxModel` does on the producer
    side and what an ``INSERT ... ON CONFLICT DO NOTHING`` on a natural
    key does on the consumer side.

**Prefer the database when you can.** If the handler's effect is already
keyed by something the domain owns — an order id, a payment reference —
then a unique constraint plus ``ON CONFLICT DO NOTHING`` is idempotent
with no extra moving part, no TTL to tune and no second store to run.
Reach for this module when the effect is *not* a row: sending an email,
calling a third party, publishing downstream.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

from tempest_fastapi_sdk.core.enums import BaseStrEnum

if TYPE_CHECKING:
    from collections.abc import MutableMapping

DEFAULT_DEDUP_TTL_SECONDS: Final[int] = 86_400
"""How long a processed message id is remembered.

Twenty-four hours comfortably outlives every redelivery cause that is not
an outage — a restart, a requeue, a lost ack — while keeping the key
space bounded. Raise it if your retry topology can hold a message longer
than that, because a key that expires before the last retry lets the
message run twice.
"""

IN_FLIGHT: Final[str] = "in_flight"
"""Marker value for a message some worker is processing right now."""

DONE: Final[str] = "done"
"""Marker value for a message that completed successfully."""


class DedupState(BaseStrEnum):
    """What the store knew about a message id when it was claimed.

    Attributes:
        NEW: Never seen. The handler runs.
        IN_FLIGHT: Another delivery of the same id is being processed. The
            handler does **not** run and the message is rejected, so the
            retry topology offers it again later — by which time the
            other worker has either finished (``DONE``, skip) or died
            (key expired, re-run).
        DONE: Already processed successfully. The handler does not run and
            the message is acknowledged.
    """

    NEW = "new"
    IN_FLIGHT = "in_flight"
    DONE = "done"


class ConcurrentDeliveryError(RuntimeError):
    """The same message id is already being processed elsewhere.

    Raised so the broker rejects the duplicate rather than acknowledging
    it. Acknowledging would be the dangerous choice: the in-flight worker
    may still fail, and the copy that could have retried would be gone.
    """


@runtime_checkable
class DedupStore(Protocol):
    """Where processed message ids are remembered.

    Three operations, because two-phase marking needs them. :meth:`claim`
    must be **atomic** — two workers claiming the same id concurrently
    must not both receive :attr:`DedupState.NEW`, which is why it is one
    call rather than a get followed by a set.
    """

    async def claim(self, key: str, *, ttl_seconds: int) -> DedupState:
        """Mark ``key`` in-flight if it is unseen, and report what it was.

        Args:
            key (str): The message id.
            ttl_seconds (int): How long the mark lives.

        Returns:
            DedupState: What the store held before this call.
        """
        ...

    async def complete(self, key: str, *, ttl_seconds: int) -> None:
        """Mark ``key`` as successfully processed.

        Args:
            key (str): The message id.
            ttl_seconds (int): How long to remember it.
        """
        ...

    async def release(self, key: str) -> None:
        """Forget ``key`` so a later delivery processes it again.

        Args:
            key (str): The message id.
        """
        ...


@dataclass
class MemoryDedupStore:
    """In-process :class:`DedupStore`, for one replica and for tests.

    Correct only while a single process consumes the channel — two
    replicas each keep their own table and neither sees the other's
    claims, so a redelivery routed to the sibling runs the handler again.
    Use :class:`RedisDedupStore` for anything with more than one worker.

    Attributes:
        entries (MutableMapping[str, tuple[str, float]]): Key to
            ``(marker, expires_at)``.
    """

    entries: MutableMapping[str, tuple[str, float]] = field(default_factory=dict)

    def _live(self, key: str) -> str | None:
        """Return the marker for ``key`` when it has not expired.

        Args:
            key (str): The message id.

        Returns:
            str | None: The marker, or ``None`` when absent or expired.
        """
        entry = self.entries.get(key)
        if entry is None:
            return None
        marker, expires_at = entry
        if expires_at <= time.monotonic():
            self.entries.pop(key, None)
            return None
        return marker

    async def claim(self, key: str, *, ttl_seconds: int) -> DedupState:
        """Claim ``key``, reporting what the store already held.

        Args:
            key (str): The message id.
            ttl_seconds (int): How long the mark lives.

        Returns:
            DedupState: The prior state.
        """
        marker = self._live(key)
        if marker == DONE:
            return DedupState.DONE
        if marker == IN_FLIGHT:
            return DedupState.IN_FLIGHT
        self.entries[key] = (IN_FLIGHT, time.monotonic() + ttl_seconds)
        return DedupState.NEW

    async def complete(self, key: str, *, ttl_seconds: int) -> None:
        """Mark ``key`` done.

        Args:
            key (str): The message id.
            ttl_seconds (int): How long to remember it.
        """
        self.entries[key] = (DONE, time.monotonic() + ttl_seconds)

    async def release(self, key: str) -> None:
        """Drop ``key``.

        Args:
            key (str): The message id.
        """
        self.entries.pop(key, None)


class RedisDedupStore:
    """:class:`DedupStore` backed by an async ``redis`` client.

    :meth:`claim` is a single ``SET key in_flight NX EX ttl``, so the
    atomicity two-phase marking depends on comes from Redis rather than
    from a lock of our own — two workers racing on the same id cannot
    both be told ``NEW``.
    """

    def __init__(self, client: Any, *, prefix: str = "tempest:dedup:") -> None:
        """Wrap a Redis client.

        Args:
            client (Any): An async ``redis.asyncio.Redis``-compatible
                client exposing ``set``, ``get`` and ``delete``.
            prefix (str): Key namespace, so dedup keys never collide with
                the application's own.
        """
        self.client: Any = client
        self.prefix: str = prefix

    def _key(self, key: str) -> str:
        """Namespace a message id.

        Args:
            key (str): The message id.

        Returns:
            str: The Redis key.
        """
        return f"{self.prefix}{key}"

    async def claim(self, key: str, *, ttl_seconds: int) -> DedupState:
        """Claim ``key`` atomically via ``SET NX``.

        Args:
            key (str): The message id.
            ttl_seconds (int): How long the mark lives.

        Returns:
            DedupState: The prior state. A lost race reads the current
            value to tell ``IN_FLIGHT`` from ``DONE``; a key that expires
            between the two calls reads as ``DONE``, which skips one
            message rather than running it twice — the safer way to be
            wrong for a handler that is not free to repeat.
        """
        stored = await self.client.set(
            self._key(key),
            IN_FLIGHT,
            nx=True,
            ex=ttl_seconds,
        )
        if stored:
            return DedupState.NEW
        current = await self.client.get(self._key(key))
        marker = current.decode() if isinstance(current, bytes) else current
        return DedupState.IN_FLIGHT if marker == IN_FLIGHT else DedupState.DONE

    async def complete(self, key: str, *, ttl_seconds: int) -> None:
        """Mark ``key`` done.

        Args:
            key (str): The message id.
            ttl_seconds (int): How long to remember it.
        """
        await self.client.set(self._key(key), DONE, ex=ttl_seconds)

    async def release(self, key: str) -> None:
        """Drop ``key``.

        Args:
            key (str): The message id.
        """
        await self.client.delete(self._key(key))


def message_key(message: Any) -> str | None:
    """Return the id a message is deduplicated by.

    Prefers the broker's ``message_id``, which
    :meth:`~tempest_fastapi_sdk.queue.MessageBroker.publish` sets on every
    publish. Falls back to ``correlation_id`` for messages published by
    something other than this SDK.

    Args:
        message (Any): The FastStream message.

    Returns:
        str | None: The key, or ``None`` when the message carries neither
        — in which case dedup is skipped rather than guessed at, because
        a key derived from the body would silently collapse two
        legitimately identical events into one.
    """
    for attribute in ("message_id", "correlation_id"):
        value = getattr(message, attribute, None)
        if value:
            return str(value)
    return None


def make_dedup_middleware(
    store: DedupStore,
    *,
    ttl_seconds: int = DEFAULT_DEDUP_TTL_SECONDS,
) -> Any:
    """Build a FastStream middleware that runs each id at most once.

    Args:
        store (DedupStore): Where processed ids are remembered.
        ttl_seconds (int): How long an id is remembered. Must outlive the
            retry topology's total delay, or the last retry of a message
            will run as if it were new.

    Returns:
        Any: A ``faststream.BaseMiddleware`` subclass.

    Raises:
        ImportError: When the ``[queue]`` extra is not installed.
    """
    from tempest_fastapi_sdk.queue.broker import _require

    faststream = _require("faststream", "queue")

    class _DedupMiddleware(faststream.BaseMiddleware):  # type: ignore[misc,name-defined]
        """Claim, run, then mark done — or release so a retry retries."""

        async def consume_scope(self, call_next: Any, msg: Any) -> Any:
            """Run the handler at most once per message id.

            Args:
                call_next (Any): The next middleware or the handler.
                msg (Any): The message being consumed.

            Returns:
                Any: The handler's result, or ``None`` when the message
                was recognized as already processed.

            Raises:
                ConcurrentDeliveryError: When another worker holds the
                    claim, so the broker rejects this copy instead of
                    acknowledging one that may still fail.
                BaseException: Whatever the handler raised, after the
                    claim is released so a retry is not skipped.
            """
            key = message_key(msg)
            if key is None:
                return await call_next(msg)

            state = await store.claim(key, ttl_seconds=ttl_seconds)
            if state is DedupState.DONE:
                return None
            if state is DedupState.IN_FLIGHT:
                raise ConcurrentDeliveryError(
                    f"Message {key} is already being processed; rejecting this "
                    "delivery so it is offered again rather than acknowledged "
                    "on behalf of a worker that may still fail.",
                )

            try:
                result = await call_next(msg)
            except Exception:
                await store.release(key)
                raise
            await store.complete(key, ttl_seconds=ttl_seconds)
            return result

    return _DedupMiddleware


__all__: list[str] = [
    "DEFAULT_DEDUP_TTL_SECONDS",
    "DONE",
    "IN_FLIGHT",
    "ConcurrentDeliveryError",
    "DedupState",
    "DedupStore",
    "MemoryDedupStore",
    "RedisDedupStore",
    "make_dedup_middleware",
    "message_key",
]
