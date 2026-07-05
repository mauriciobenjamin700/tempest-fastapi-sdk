"""Coalesce concurrent inference calls into batches for throughput.

On a GPU, running one item at a time wastes most of the device: batching
many items into a single forward pass is often 10-50x the throughput.
`BatchScheduler` sits in front of any async batch handler — embeddings,
generation — and merges calls that arrive close together into one batch,
transparently. Each caller still `await`s its own result.

It's model-agnostic and dependency-free (pure asyncio), so it imports and
tests without the ``[genai]`` extra.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

ItemT = TypeVar("ItemT")
ResultT = TypeVar("ResultT")


class BatchScheduler(Generic[ItemT, ResultT]):
    """Merge concurrent :meth:`submit` calls into batched handler calls.

    A background loop drains a queue, forming a batch once either
    ``max_batch`` items are waiting or ``max_wait`` seconds have elapsed
    since the first queued item, then calls ``handler(batch)`` once and
    hands each caller its matching result by position.

    Example:

        >>> async def embed_batch(texts: list[str]) -> list[list[float]]:
        ...     return await embedder._embed_many(texts)
        >>> sched = BatchScheduler(embed_batch, max_batch=32, max_wait_ms=10)
        >>> vec = await sched.submit("hello")   # coalesced with concurrent calls
        >>> await sched.aclose()

    Attributes:
        max_batch (int): Max items per handler call.
        max_wait (float): Max seconds to wait forming a batch.
    """

    def __init__(
        self,
        handler: Callable[[list[ItemT]], Awaitable[list[ResultT]]],
        *,
        max_batch: int = 32,
        max_wait_ms: float = 10.0,
    ) -> None:
        """Initialize the scheduler.

        Args:
            handler (Callable[[list[ItemT]], Awaitable[list[ResultT]]]): Async batch
                function. Must return one output per input, in order.
            max_batch (int): Max items per batch.
            max_wait_ms (float): Max milliseconds to wait for a batch to
                fill before flushing what's queued.

        Raises:
            ValueError: When ``max_batch`` is not positive.
        """
        if max_batch <= 0:
            raise ValueError("max_batch must be positive")
        self._handler = handler
        self.max_batch = max_batch
        self.max_wait = max_wait_ms / 1000.0
        self._queue: asyncio.Queue[tuple[ItemT, asyncio.Future[ResultT]]] = (
            asyncio.Queue()
        )
        self._worker: asyncio.Task[None] | None = None
        self._closed = False

    async def submit(self, item: ItemT) -> ResultT:
        """Submit one item and await its result.

        The item is batched with others submitted around the same time.

        Args:
            item (I): The input to process.

        Returns:
            O: The handler's output for ``item``.

        Raises:
            RuntimeError: When the scheduler has been closed.
        """
        if self._closed:
            raise RuntimeError("BatchScheduler is closed")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ResultT] = loop.create_future()
        await self._queue.put((item, future))
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())
        return await future

    async def _run(self) -> None:
        """Drain the queue in batches until it is empty."""
        while not self._queue.empty():
            item, future = await self._queue.get()
            batch: list[ItemT] = [item]
            futures: list[asyncio.Future[ResultT]] = [future]
            deadline = asyncio.get_running_loop().time() + self.max_wait
            while len(batch) < self.max_batch:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    nxt_item, nxt_future = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=remaining,
                    )
                except TimeoutError:
                    break
                batch.append(nxt_item)
                futures.append(nxt_future)
            await self._dispatch(batch, futures)

    async def _dispatch(
        self,
        batch: list[ItemT],
        futures: list[asyncio.Future[ResultT]],
    ) -> None:
        """Run the handler on ``batch`` and resolve each future."""
        try:
            results = await self._handler(batch)
        except Exception as exc:
            for future in futures:
                if not future.done():
                    future.set_exception(exc)
            return
        if len(results) != len(futures):
            error = RuntimeError(
                f"handler returned {len(results)} results for {len(futures)} items",
            )
            for future in futures:
                if not future.done():
                    future.set_exception(error)
            return
        for future, result in zip(futures, results, strict=True):
            if not future.done():
                future.set_result(result)

    async def aclose(self) -> None:
        """Stop the worker after the current batch; reject new submits."""
        self._closed = True
        if self._worker is not None and not self._worker.done():
            await self._worker


__all__: list[str] = [
    "BatchScheduler",
]
