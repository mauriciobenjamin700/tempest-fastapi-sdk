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

    The handler is awaited, so it must be a coroutine function taking the
    whole batch. :meth:`Embedder.embed` is that shape already; its
    private ``_embed_many`` is not — it is synchronous, and awaiting it
    raises ``TypeError: object list can't be used in 'await' expression``.

    Example:

        >>> async def embed_batch(texts: list[str]) -> list[list[float]]:
        ...     return await embedder.embed(texts)
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
        """Drain the queue in batches until it is empty.

        Items already queued are taken **without** awaiting: only the wait
        for an item that has not arrived yet is timed. Timing every take
        cost throughput under exactly the load batching exists for — five
        callers whose items were all queued already came back as
        ``[[0, 1, 2], [3, 4]]`` once the loop was busy enough to burn the
        20 ms window between two ``wait_for`` calls.

        A cancellation that lands while a batch is still being formed (the
        worker waiting for the next item) cancels the items already taken
        and everything still queued, so none of their callers waits forever.

        Raises:
            asyncio.CancelledError: When the worker task is cancelled.
        """
        while not self._queue.empty():
            item, future = await self._queue.get()
            batch: list[ItemT] = [item]
            futures: list[asyncio.Future[ResultT]] = [future]
            deadline = asyncio.get_running_loop().time() + self.max_wait
            try:
                while len(batch) < self.max_batch:
                    try:
                        nxt_item, nxt_future = self._queue.get_nowait()
                    except asyncio.QueueEmpty:
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
            except BaseException:
                _cancel_all(futures)
                self._abandon_queue()
                raise
            await self._dispatch(batch, futures)

    async def _dispatch(
        self,
        batch: list[ItemT],
        futures: list[asyncio.Future[ResultT]],
    ) -> None:
        """Run the handler on ``batch`` and resolve each future.

        Every path resolves every future, so no :meth:`submit` is left
        awaiting forever:

        * a handler ``Exception`` is set on each future;
        * a handler that returns something without a length (``None``, a
          generator) or the wrong number of results fails each future with
          ``RuntimeError`` instead of killing the worker with a
          ``TypeError`` raised outside any handler;
        * a ``CancelledError`` the handler raised on its own (the worker
          task was not cancelled) cancels the batch's futures and the worker
          moves on to the next batch;
        * a cancellation of the worker itself, or any other
          ``BaseException`` (``KeyboardInterrupt``, ``SystemExit``), cancels
          or fails the batch's futures **and** every item still queued,
          then propagates.

        Args:
            batch (list[ItemT]): The items handed to the handler.
            futures (list[asyncio.Future[ResultT]]): One future per item,
                in the same order.

        Raises:
            asyncio.CancelledError: When the worker task itself is being
                cancelled.
            BaseException: Any non-``Exception`` raised by the handler other
                than its own ``CancelledError``.
        """
        try:
            results = await self._handler(batch)
        except Exception as exc:
            _fail_all(futures, exc)
            return
        except asyncio.CancelledError:
            _cancel_all(futures)
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                self._abandon_queue()
                raise
            return
        except BaseException as exc:
            _fail_all(futures, RuntimeError(f"handler raised {exc!r}"))
            self._abandon_queue()
            raise
        try:
            count = len(results)
        except TypeError:
            _fail_all(
                futures,
                RuntimeError(
                    "handler must return a list with one result per item, "
                    f"got {type(results).__name__}",
                ),
            )
            return
        if count != len(futures):
            _fail_all(
                futures,
                RuntimeError(
                    f"handler returned {count} results for {len(futures)} items",
                ),
            )
            return
        for future, result in zip(futures, results, strict=True):
            if not future.done():
                future.set_result(result)

    def _abandon_queue(self) -> None:
        """Cancel the future of every item still waiting in the queue.

        Called when the worker is going away for good, so callers whose
        items never reached a batch get a ``CancelledError`` instead of an
        await that never returns.
        """
        while True:
            try:
                _item, future = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if not future.done():
                future.cancel()

    async def aclose(self) -> None:
        """Stop the worker after the current batch; reject new submits."""
        self._closed = True
        if self._worker is not None and not self._worker.done():
            await self._worker


def _fail_all(futures: list[asyncio.Future[ResultT]], error: BaseException) -> None:
    """Set ``error`` on every future that is still pending.

    Args:
        futures (list[asyncio.Future[ResultT]]): The futures to resolve.
        error (BaseException): The exception each caller will see.
    """
    for future in futures:
        if not future.done():
            future.set_exception(error)


def _cancel_all(futures: list[asyncio.Future[ResultT]]) -> None:
    """Cancel every future that is still pending.

    Args:
        futures (list[asyncio.Future[ResultT]]): The futures to cancel.
    """
    for future in futures:
        if not future.done():
            future.cancel()


__all__: list[str] = [
    "BatchScheduler",
]
