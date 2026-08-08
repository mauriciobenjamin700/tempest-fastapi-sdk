"""Tests for tempest_fastapi_sdk.queue.dedup.

The two-phase marking is the point, so the tests drive the real
``consume_scope`` through the sequences that actually happen in
production: a redelivery after success, a redelivery after failure, and
two workers racing on the same id. A test that only proved "the handler
ran once" would pass for an implementation that never retries a genuine
failure — the trade this design exists to avoid.
"""

from typing import Any

import pytest

from tempest_fastapi_sdk.queue import (
    ConcurrentDeliveryError,
    DedupState,
    MemoryDedupStore,
)
from tempest_fastapi_sdk.queue.dedup import (
    DONE,
    IN_FLIGHT,
    make_dedup_middleware,
    message_key,
)


class _Message:
    """Stand-in for a FastStream message."""

    def __init__(
        self,
        *,
        message_id: str | None = "m-1",
        correlation_id: str | None = None,
    ) -> None:
        """Record the identifiers the middleware reads."""
        self.message_id = message_id
        self.correlation_id = correlation_id


def _instantiate(middleware_cls: Any, message: Any) -> Any:
    """Build a middleware the way FastStream does.

    Args:
        middleware_cls (Any): The middleware class under test.
        message (Any): The message being consumed.

    Returns:
        Any: The instantiated middleware.
    """
    from faststream._internal.context.repository import ContextRepo

    return middleware_cls(message, context=ContextRepo())


class _Handler:
    """Counts calls and optionally fails."""

    def __init__(self, *, fail: bool = False) -> None:
        """Start with no calls recorded."""
        self.calls: int = 0
        self.fail: bool = fail

    async def __call__(self, _: Any) -> str:
        """Record one call.

        Returns:
            str: A marker value.

        Raises:
            ValueError: When configured to fail.
        """
        self.calls += 1
        if self.fail:
            raise ValueError("handler bug")
        return "done"


class TestMessageKey:
    def test_message_id_is_preferred(self) -> None:
        assert message_key(_Message(message_id="a", correlation_id="b")) == "a"

    def test_correlation_id_is_the_fallback(self) -> None:
        """For events published by something other than this SDK."""
        assert message_key(_Message(message_id=None, correlation_id="b")) == "b"

    def test_no_identifier_means_no_key(self) -> None:
        """Deriving one from the body would collapse identical events."""
        assert message_key(_Message(message_id=None)) is None


class TestMemoryStore:
    async def test_a_fresh_key_is_new(self) -> None:
        store = MemoryDedupStore()
        assert await store.claim("k", ttl_seconds=60) is DedupState.NEW

    async def test_a_second_claim_sees_it_in_flight(self) -> None:
        store = MemoryDedupStore()
        await store.claim("k", ttl_seconds=60)
        assert await store.claim("k", ttl_seconds=60) is DedupState.IN_FLIGHT

    async def test_a_completed_key_reads_done(self) -> None:
        store = MemoryDedupStore()
        await store.claim("k", ttl_seconds=60)
        await store.complete("k", ttl_seconds=60)
        assert await store.claim("k", ttl_seconds=60) is DedupState.DONE

    async def test_release_makes_it_claimable_again(self) -> None:
        """A failure must not leave the id looking processed."""
        store = MemoryDedupStore()
        await store.claim("k", ttl_seconds=60)
        await store.release("k")
        assert await store.claim("k", ttl_seconds=60) is DedupState.NEW

    async def test_an_expired_claim_is_reclaimable(self) -> None:
        """A worker that died must not block the id forever."""
        store = MemoryDedupStore()
        await store.claim("k", ttl_seconds=60)
        store.entries["k"] = (IN_FLIGHT, 0.0)
        assert await store.claim("k", ttl_seconds=60) is DedupState.NEW

    async def test_an_expired_done_is_reclaimable(self) -> None:
        store = MemoryDedupStore()
        await store.complete("k", ttl_seconds=60)
        store.entries["k"] = (DONE, 0.0)
        assert await store.claim("k", ttl_seconds=60) is DedupState.NEW


class TestRedisStore:
    class _FakeRedis:
        """Enough of redis.asyncio for SET NX / GET / DELETE."""

        def __init__(self) -> None:
            """Start empty."""
            self.data: dict[str, str] = {}

        async def set(
            self,
            key: str,
            value: str,
            *,
            nx: bool = False,
            ex: int | None = None,
        ) -> bool | None:
            """Set a key, honouring NX.

            Returns:
                bool | None: ``True`` when stored, ``None`` when NX lost.
            """
            if nx and key in self.data:
                return None
            self.data[key] = value
            return True

        async def get(self, key: str) -> str | None:
            """Read a key.

            Returns:
                str | None: The value, or ``None``.
            """
            return self.data.get(key)

        async def delete(self, key: str) -> int:
            """Drop a key.

            Returns:
                int: How many keys were removed.
            """
            return 1 if self.data.pop(key, None) is not None else 0

    async def test_the_first_claim_wins(self) -> None:
        from tempest_fastapi_sdk.queue import RedisDedupStore

        store = RedisDedupStore(self._FakeRedis())
        assert await store.claim("k", ttl_seconds=60) is DedupState.NEW

    async def test_a_lost_race_reads_in_flight(self) -> None:
        """Atomicity comes from SET NX, not from a lock of our own."""
        from tempest_fastapi_sdk.queue import RedisDedupStore

        store = RedisDedupStore(self._FakeRedis())
        await store.claim("k", ttl_seconds=60)
        assert await store.claim("k", ttl_seconds=60) is DedupState.IN_FLIGHT

    async def test_a_completed_key_reads_done(self) -> None:
        from tempest_fastapi_sdk.queue import RedisDedupStore

        store = RedisDedupStore(self._FakeRedis())
        await store.complete("k", ttl_seconds=60)
        assert await store.claim("k", ttl_seconds=60) is DedupState.DONE

    async def test_bytes_values_are_decoded(self) -> None:
        """redis-py returns bytes unless decode_responses is on."""
        from tempest_fastapi_sdk.queue import RedisDedupStore

        redis = self._FakeRedis()
        store = RedisDedupStore(redis)
        await store.claim("k", ttl_seconds=60)
        redis.data[store._key("k")] = IN_FLIGHT.encode()  # type: ignore[assignment]
        assert await store.claim("k", ttl_seconds=60) is DedupState.IN_FLIGHT

    async def test_keys_are_namespaced(self) -> None:
        from tempest_fastapi_sdk.queue import RedisDedupStore

        redis = self._FakeRedis()
        await RedisDedupStore(redis).claim("k", ttl_seconds=60)
        assert list(redis.data) == ["tempest:dedup:k"]


class TestMiddleware:
    async def _run(self, cls: Any, handler: _Handler, message: _Message) -> Any:
        """Drive one consume through the middleware.

        Returns:
            Any: The middleware's return value.
        """
        return await _instantiate(cls, message).consume_scope(handler, message)

    async def test_the_first_delivery_runs(self) -> None:
        cls = make_dedup_middleware(MemoryDedupStore())
        handler = _Handler()
        assert await self._run(cls, handler, _Message()) == "done"
        assert handler.calls == 1

    async def test_a_redelivery_after_success_is_skipped(self) -> None:
        cls = make_dedup_middleware(MemoryDedupStore())
        handler = _Handler()
        await self._run(cls, handler, _Message())
        await self._run(cls, handler, _Message())
        assert handler.calls == 1

    async def test_a_redelivery_after_failure_runs_again(self) -> None:
        """The trade this design exists to avoid: a failure must retry."""
        store = MemoryDedupStore()
        cls = make_dedup_middleware(store)
        failing = _Handler(fail=True)
        with pytest.raises(ValueError):
            await self._run(cls, failing, _Message())

        succeeding = _Handler()
        assert await self._run(cls, succeeding, _Message()) == "done"
        assert succeeding.calls == 1

    async def test_a_concurrent_delivery_is_rejected(self) -> None:
        """Acking it would discard the copy that could still retry."""
        store = MemoryDedupStore()
        cls = make_dedup_middleware(store)
        await store.claim("m-1", ttl_seconds=60)
        with pytest.raises(ConcurrentDeliveryError, match="m-1"):
            await self._run(cls, _Handler(), _Message())

    async def test_a_message_without_a_key_still_runs(self) -> None:
        """No key is no dedup — never a dropped message."""
        cls = make_dedup_middleware(MemoryDedupStore())
        handler = _Handler()
        await self._run(cls, handler, _Message(message_id=None))
        await self._run(cls, handler, _Message(message_id=None))
        assert handler.calls == 2

    async def test_different_ids_are_independent(self) -> None:
        cls = make_dedup_middleware(MemoryDedupStore())
        handler = _Handler()
        await self._run(cls, handler, _Message(message_id="a"))
        await self._run(cls, handler, _Message(message_id="b"))
        assert handler.calls == 2

    async def test_the_handler_exception_reaches_the_broker(self) -> None:
        cls = make_dedup_middleware(MemoryDedupStore())
        with pytest.raises(ValueError, match="handler bug"):
            await self._run(cls, _Handler(fail=True), _Message())


class TestPublishGeneratesAnId:
    def test_publish_defaults_a_message_id(self) -> None:
        """Without a stable id there is no key to deduplicate on."""
        import inspect

        from tempest_fastapi_sdk.queue.broker import MessageBroker

        source = inspect.getsource(MessageBroker.publish)
        assert 'options.setdefault("message_id"' in source

    async def test_an_explicit_id_is_kept(self) -> None:
        from tempest_fastapi_sdk.queue import MessageBroker

        seen: dict[str, Any] = {}

        class _Recorder:
            async def publish(self, message: Any, channel: Any, **options: Any) -> None:
                seen.update(options)

        mq = MessageBroker(_Recorder())  # type: ignore[arg-type]
        mq._started = True
        await mq.publish("orders.paid", {"a": 1}, message_id="mine")
        assert seen["message_id"] == "mine"

    async def test_a_missing_id_is_generated(self) -> None:
        from tempest_fastapi_sdk.queue import MessageBroker

        seen: dict[str, Any] = {}

        class _Recorder:
            async def publish(self, message: Any, channel: Any, **options: Any) -> None:
                seen.update(options)

        mq = MessageBroker(_Recorder())  # type: ignore[arg-type]
        mq._started = True
        await mq.publish("orders.paid", {"a": 1})
        assert seen["message_id"]
