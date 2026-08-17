"""Cancelling stops the work, not just the waiting.

A cancellation that only stops *waiting* for a coroutine is barely worth
having: the call keeps running in the worker, keeps its connection open and
keeps competing with the next job, and the user who clicked "cancel" gets a
screen that lies. So the assertions here are about the wrapped coroutine —
whether it was actually cancelled, and whether it was left running — not
only about what ``run_cancellable`` returned or raised.
"""

from __future__ import annotations

import asyncio

import pytest

from tempest_fastapi_sdk.tasks import (
    DEFAULT_POLL_SECONDS,
    StageInterruptedError,
    run_cancellable,
)

# Far below the 2s default so the tests finish quickly; the behaviour under
# test is the polling, not the interval.
FAST_POLL = 0.01


class _Work:
    """A cancellable unit of work that records how it ended.

    Attributes:
        started (asyncio.Event): Set once the coroutine is running.
        cancelled (bool): True when the coroutine received a
            ``CancelledError`` — which is the thing worth asserting.
        completed (bool): True when it ran to the end.
    """

    def __init__(self, duration: float) -> None:
        """Build the work.

        Args:
            duration (float): How long it sleeps before finishing.
        """
        self.started: asyncio.Event = asyncio.Event()
        self.cancelled: bool = False
        self.completed: bool = False
        self._duration = duration

    async def run(self) -> str:
        """Sleep, then report success.

        Returns:
            str: A sentinel result.

        Raises:
            asyncio.CancelledError: Re-raised after recording, as any
                well-behaved coroutine does.
        """
        self.started.set()
        try:
            await asyncio.sleep(self._duration)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.completed = True
        return "done"


def _never() -> object:
    """Build a predicate that never asks to stop.

    Returns:
        object: The coroutine function.
    """

    async def _check() -> bool:
        return False

    return _check


def _after(calls: int) -> object:
    """Build a predicate that asks to stop after ``calls`` checks.

    Args:
        calls (int): Polls to allow before returning True.

    Returns:
        object: The coroutine function.
    """
    seen = {"n": 0}

    async def _check() -> bool:
        seen["n"] += 1
        return seen["n"] > calls

    return _check


async def test_returns_the_result_when_work_finishes_first() -> None:
    """The uncancelled path is just a passthrough."""
    work = _Work(0.01)

    result = await run_cancellable(
        work.run(),
        interrupted=_never(),  # type: ignore[arg-type]
        poll_seconds=FAST_POLL,
    )

    assert result == "done"
    assert work.completed is True
    assert work.cancelled is False


async def test_cancelling_actually_cancels_the_coroutine() -> None:
    """The work receives CancelledError — it is not left running.

    This is the assertion that matters. Stopping the wait while the call
    runs on would leave the worker busy on a result nobody wants.
    """
    work = _Work(10.0)

    with pytest.raises(StageInterruptedError):
        await run_cancellable(
            work.run(),
            interrupted=_after(0),  # type: ignore[arg-type]
            poll_seconds=FAST_POLL,
        )

    assert work.cancelled is True
    assert work.completed is False


async def test_cancel_is_seen_on_a_later_poll() -> None:
    """A cancel arriving mid-flight is picked up by the next check."""
    work = _Work(10.0)

    with pytest.raises(StageInterruptedError):
        await run_cancellable(
            work.run(),
            interrupted=_after(2),  # type: ignore[arg-type]
            poll_seconds=FAST_POLL,
        )

    assert work.cancelled is True


async def test_work_errors_propagate_unchanged() -> None:
    """A failure inside the work is not turned into a cancellation."""

    async def _boom() -> str:
        raise RuntimeError("the model refused")

    with pytest.raises(RuntimeError, match="the model refused"):
        await run_cancellable(
            _boom(),
            interrupted=_never(),  # type: ignore[arg-type]
            poll_seconds=FAST_POLL,
        )


async def test_a_failing_predicate_does_not_discard_the_work() -> None:
    """A database blip must not throw away work that still finishes.

    Treating an unreachable database as "cancelled" would discard good work
    during exactly the incident where redoing it costs the most.
    """
    calls = {"n": 0}

    async def _flaky() -> bool:
        calls["n"] += 1
        raise ConnectionError("database unreachable")

    work = _Work(0.05)
    result = await run_cancellable(
        work.run(),
        interrupted=_flaky,
        poll_seconds=FAST_POLL,
    )

    assert result == "done"
    assert work.completed is True
    assert calls["n"] >= 1


async def test_outer_cancellation_also_cancels_the_work() -> None:
    """Shutting the worker down does not leak the inner coroutine.

    Without the ``finally``, killing the poller would leave the wrapped
    call running with nothing awaiting it.
    """
    work = _Work(10.0)

    task = asyncio.ensure_future(
        run_cancellable(
            work.run(),
            interrupted=_never(),  # type: ignore[arg-type]
            poll_seconds=FAST_POLL,
        ),
    )
    await work.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert work.cancelled is True


async def test_non_positive_poll_is_refused() -> None:
    """A zero interval would spin on the predicate's database."""
    work = _Work(0.01)

    with pytest.raises(ValueError, match="poll_seconds"):
        await run_cancellable(
            work.run(),
            interrupted=_never(),  # type: ignore[arg-type]
            poll_seconds=0,
        )


def test_default_poll_is_two_seconds() -> None:
    """Pinned because it is a user-visible latency, not an implementation
    detail: it bounds how long after clicking "cancel" the work keeps
    going."""
    assert DEFAULT_POLL_SECONDS == 2.0
