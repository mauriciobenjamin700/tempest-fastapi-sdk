"""Tests for BatchScheduler and ModelRegistry (no torch)."""

from __future__ import annotations

import asyncio
import time

import pytest

from tempest_fastapi_sdk.genai import BatchScheduler, ModelRegistry


class TestBatchScheduler:
    async def test_coalesces_concurrent_submits(self) -> None:
        seen_batches: list[list[int]] = []

        async def handler(batch: list[int]) -> list[int]:
            seen_batches.append(batch)
            return [x * 2 for x in batch]

        sched = BatchScheduler(handler, max_batch=8, max_wait_ms=20)
        results = await asyncio.gather(*[sched.submit(i) for i in range(5)])
        await sched.aclose()

        assert results == [0, 2, 4, 6, 8]
        # all 5 coalesced into a single batch
        assert len(seen_batches) == 1
        assert sorted(seen_batches[0]) == [0, 1, 2, 3, 4]

    async def test_queued_items_coalesce_under_loop_load(self) -> None:
        """The flaky failure, made deterministic.

        The scheduler timed **every** take, including takes of items that
        were already in the queue, so a loop busy enough to burn the 20 ms
        window between two ``asyncio.wait_for`` calls split a batch that had
        nothing to wait for. That is a throughput loss under exactly the load
        batching exists for, and it is why the plain coalescing test failed
        in the full suite and passed alone (issue #176).

        The load here is a task that blocks the loop in 5 ms slices, which is
        what a 6000-test suite does to a 20 ms deadline. Before the fix this
        returned ``[[0, 1, 2], [3, 4]]``.
        """
        seen_batches: list[list[int]] = []

        async def handler(batch: list[int]) -> list[int]:
            seen_batches.append(list(batch))
            return [x * 2 for x in batch]

        async def block_the_loop() -> None:
            for _ in range(20):
                time.sleep(0.005)
                await asyncio.sleep(0)

        sched: BatchScheduler[int, int] = BatchScheduler(
            handler,
            max_batch=8,
            max_wait_ms=20,
        )
        results = await asyncio.gather(
            *[sched.submit(i) for i in range(5)],
            block_the_loop(),
        )
        await sched.aclose()

        assert results[:5] == [0, 2, 4, 6, 8]
        assert len(seen_batches) == 1, seen_batches
        assert sorted(seen_batches[0]) == [0, 1, 2, 3, 4]

    async def test_respects_max_batch(self) -> None:
        async def handler(batch: list[int]) -> list[int]:
            return list(batch)

        sched = BatchScheduler(handler, max_batch=2, max_wait_ms=50)
        results = await asyncio.gather(*[sched.submit(i) for i in range(4)])
        await sched.aclose()
        assert sorted(results) == [0, 1, 2, 3]

    async def test_handler_error_propagates_to_callers(self) -> None:
        async def handler(batch: list[int]) -> list[int]:
            raise ValueError("boom")

        sched = BatchScheduler(handler, max_batch=4, max_wait_ms=10)
        with pytest.raises(ValueError, match="boom"):
            await sched.submit(1)
        await sched.aclose()

    async def test_submit_after_close_raises(self) -> None:
        async def handler(batch: list[int]) -> list[int]:
            return list(batch)

        sched = BatchScheduler(handler)
        await sched.aclose()
        with pytest.raises(RuntimeError):
            await sched.submit(1)

    def test_bad_max_batch(self) -> None:
        async def handler(batch: list[int]) -> list[int]:
            return list(batch)

        with pytest.raises(ValueError):
            BatchScheduler(handler, max_batch=0)


class _Fake:
    def __init__(self) -> None:
        self.unloaded = False

    def unload(self) -> None:
        self.unloaded = True


class TestModelRegistry:
    def test_get_caches_and_reuses(self) -> None:
        reg = ModelRegistry(max_models=2)
        calls = 0

        def factory() -> _Fake:
            nonlocal calls
            calls += 1
            return _Fake()

        a = reg.get("m", factory)
        b = reg.get("m", factory)
        assert a is b
        assert calls == 1

    def test_lru_eviction_unloads(self) -> None:
        reg = ModelRegistry(max_models=2)
        m1 = reg.get("a", _Fake)
        reg.get("b", _Fake)
        reg.get("a", _Fake)  # touch a -> b is LRU
        reg.get("c", _Fake)  # evicts b
        assert "b" not in reg
        assert "a" in reg and "c" in reg
        assert m1.unloaded is False  # a survived

    def test_evict_all(self) -> None:
        reg = ModelRegistry()
        f = reg.get("a", _Fake)
        reg.evict_all()
        assert len(reg) == 0
        assert f.unloaded is True


class TestBatchSchedulerFailurePaths:
    """Every handler outcome resolves every caller; the worker survives."""

    async def test_handler_cancelled_error_does_not_hang_submit(self) -> None:
        """A handler raising ``CancelledError`` used to leave ``submit`` hanging."""
        calls = {"n": 0}

        async def handler(batch: list[int]) -> list[int]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise asyncio.CancelledError
            return [x * 2 for x in batch]

        sched: BatchScheduler[int, int] = BatchScheduler(handler, max_wait_ms=1)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(sched.submit(1), timeout=1.0)
        assert await asyncio.wait_for(sched.submit(2), timeout=1.0) == 4
        await sched.aclose()

    async def test_non_sized_result_fails_callers_and_worker_survives(self) -> None:
        """``None`` from the handler raised ``TypeError`` outside the try.

        The worker died on ``len(None)`` and the callers never resolved.
        """
        calls = {"n": 0}

        async def handler(batch: list[int]) -> list[int]:
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # type: ignore[return-value]
            return [x + 1 for x in batch]

        sched: BatchScheduler[int, int] = BatchScheduler(handler, max_wait_ms=1)
        with pytest.raises(RuntimeError, match="NoneType"):
            await asyncio.wait_for(sched.submit(1), timeout=1.0)
        assert await asyncio.wait_for(sched.submit(1), timeout=1.0) == 2
        await sched.aclose()

    async def test_worker_cancellation_cancels_queued_callers(self) -> None:
        """Cancelling the worker cancels the batch and everything queued."""
        started = asyncio.Event()

        async def handler(batch: list[int]) -> list[int]:
            started.set()
            await asyncio.sleep(10)
            return batch

        sched: BatchScheduler[int, int] = BatchScheduler(
            handler, max_batch=1, max_wait_ms=1
        )
        first = asyncio.ensure_future(sched.submit(1))
        await started.wait()
        second = asyncio.ensure_future(sched.submit(2))
        await asyncio.sleep(0)
        assert sched._worker is not None
        sched._worker.cancel()
        results = await asyncio.wait_for(
            asyncio.gather(first, second, return_exceptions=True),
            timeout=1.0,
        )
        assert all(isinstance(r, asyncio.CancelledError) for r in results)

    async def test_cancel_while_forming_a_batch_cancels_the_caller(self) -> None:
        """A worker cancelled while waiting for more items resolves its batch."""

        async def handler(batch: list[int]) -> list[int]:
            return batch

        sched: BatchScheduler[int, int] = BatchScheduler(
            handler, max_batch=8, max_wait_ms=5000
        )
        pending = asyncio.ensure_future(sched.submit(1))
        for _ in range(5):
            await asyncio.sleep(0)
        assert sched._worker is not None
        sched._worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, timeout=1.0)
