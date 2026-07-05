"""Tests for BatchScheduler and ModelRegistry (no torch)."""

from __future__ import annotations

import asyncio

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
