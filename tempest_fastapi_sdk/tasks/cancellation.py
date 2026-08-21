"""Stopping work that is already running, in another process.

A queue hands a call to a worker and forgets it. Nothing in TaskIQ — or in
any other broker the SDK speaks — offers "kill the task with this id": once
the call is running inside a worker process, the only thing that can stop it
is that process. So cancellation is **cooperative**. The request side writes
"cancelled" somewhere durable and answers immediately; the worker reads that
at agreed checkpoints and gives up.

:func:`run_cancellable` is the checkpoint that runs *during* the work rather
than between steps. It races the coroutine against a predicate polled on an
interval, and when the predicate says stop, the coroutine is cancelled for
real — an in-flight HTTP request is aborted and the worker is free within
the poll interval, instead of finishing a call whose result nobody wants.

Pair it with :meth:`~tempest_fastapi_sdk.tasks.JobStore.cancellation_watch`,
which builds the predicate from a job row:

    from tempest_fastapi_sdk.tasks import StageInterruptedError, run_cancellable

    try:
        summary = await run_cancellable(
            summarize(transcript),
            interrupted=store.cancellation_watch(job.id),
        )
    except StageInterruptedError:
        return                      # not a failure: say nothing, write nothing

**It only works on genuinely cancellable awaitables** — async I/O. Work
handed to ``asyncio.to_thread`` is not: cancelling the coroutine abandons
the wrapper while the thread runs on to completion, still holding the CPU
and still competing with the next job. For that shape, check between steps
instead, and check again before writing the result.

``stop_event`` is the exception that proves it. A thread cannot be
cancelled, but it can be *asked*, and some libraries offer the hook: local
generation checks its stopping criteria after every token, so an event set
here reaches a model already decoding
(:meth:`~tempest_fastapi_sdk.genai.TextGenerator.chat_structured` takes the
same event). The event is set before the coroutine is cancelled, so the
thread starts winding down at the moment the decision is made rather than
whenever it happens to finish.

That last check matters even with this helper, and it is a check of
**ownership**, not of cancellation: "this stage is no longer mine" covers
being cancelled *and* being restarted by a second run in the meantime. In
both cases the old run must not write — one would resurrect work the user
stopped, the other would clobber the newer run's result.
:meth:`~tempest_fastapi_sdk.tasks.JobStore.succeed` enforces it for you by
refusing to close a terminal job.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    import threading
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

ResultT = TypeVar("ResultT")

DEFAULT_POLL_SECONDS: float = 2.0
"""How often :func:`run_cancellable` re-reads the cancellation predicate.

Short enough that a cancel feels immediate to whoever clicked (a screen
polling for status is usually on the same order), long enough that the cost
is noise next to work measured in tens of seconds.
"""


class StageInterruptedError(Exception):
    """The work was cancelled, or taken over, before it finished.

    Nothing went wrong. Raised by :func:`run_cancellable` when the predicate
    says to stop, after the wrapped coroutine has already been cancelled.
    The handler's job is to return quietly: do not mark anything ``FAILED``,
    and do not write a partial result.

    The ``Error`` suffix follows the convention rather than the meaning —
    the same trade ``asyncio.CancelledError`` makes, which is also control
    flow rather than a fault.
    """


async def run_cancellable(
    work: Awaitable[ResultT],
    *,
    interrupted: Callable[[], Awaitable[bool]],
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    stop_event: threading.Event | None = None,
) -> ResultT:
    """Run ``work``, aborting it as soon as ``interrupted()`` says to.

    Args:
        work (Awaitable[ResultT]): The coroutine doing the long work. It
            must be genuinely cancellable — async I/O, not a
            ``to_thread`` wrapper (see the module docstring) — unless it
            honours ``stop_event``.
        interrupted (Callable[[], Awaitable[bool]]): Polled between checks;
            ``True`` means stop. Usually
            :meth:`~tempest_fastapi_sdk.tasks.JobStore.cancellation_watch`.
        poll_seconds (float): Seconds between checks.
        stop_event (threading.Event | None): Set before the coroutine is
            cancelled, so work running in a thread that watches this event
            stops too. Without it, a thread keeps going after the await is
            abandoned.

    Returns:
        ResultT: Whatever ``work`` returned, when it finished first.

    Raises:
        StageInterruptedError: The predicate said to stop. ``work`` is already
            cancelled by the time this propagates.
        ValueError: When ``poll_seconds`` is not positive — a zero or
            negative interval would spin on the database.
        Exception: Anything ``work`` itself raises, unchanged.
    """
    if poll_seconds <= 0:
        # The caller already built the coroutine to pass it here, so
        # rejecting the call without closing it would leave it dangling and
        # emit "coroutine was never awaited" from wherever the garbage
        # collector happens to run — a warning pointing at the wrong place.
        if inspect.iscoroutine(work):
            work.close()
        raise ValueError("poll_seconds must be positive")

    task = asyncio.ensure_future(work)

    def stop_the_thread() -> None:
        """Tell work running in a thread to wind down."""
        if stop_event is not None:
            stop_event.set()

    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=poll_seconds)
            if done:
                return task.result()

            try:
                should_stop = await interrupted()
            except Exception:
                # The predicate usually reads a database. A blip there is
                # not a reason to throw away work that may still finish
                # fine, so this round is skipped and the next one retries.
                # The alternative — treating an unreachable database as
                # "cancelled" — would discard good work during precisely
                # the incident where redoing it costs the most.
                logger.warning(
                    "Cancellation check failed; work continues",
                    exc_info=True,
                )
                continue

            if should_stop:
                stop_the_thread()
                raise StageInterruptedError
    finally:
        # Covers both ways of leaving without the result: the raise above,
        # and the worker itself being shut down mid-poll.
        if not task.done():
            stop_the_thread()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


__all__: list[str] = [
    "DEFAULT_POLL_SECONDS",
    "StageInterruptedError",
    "run_cancellable",
]
