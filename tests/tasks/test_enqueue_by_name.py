"""``TaskQueue.enqueue_by_name``: send a call without importing the task.

TaskIQ's worker reads only the labels a message carries. A sender that
never imported the task sends none, so before the worker-side merge a
task registered with ``RetryPolicy(max_retries=2)`` ran once and gave up
when sent by name, and twice when sent through ``Task.enqueue``.
"""

from __future__ import annotations

import asyncio

import pytest

from tempest_fastapi_sdk.tasks import (
    RetryPolicy,
    TaskQueue,
    make_registered_labels_middleware,
)

FLAKY_TASK_NAME: str = "probe:flaky"
"""Name the flaky task registers under in every test."""


def flaky_queue(attempts: list[int]) -> TaskQueue:
    """Build a memory queue with a task that fails its first attempt.

    Args:
        attempts (list[int]): Grows by one per attempt the task makes.

    Returns:
        TaskQueue: The queue, retries enabled, nothing connected yet.
    """
    tq = TaskQueue.memory()
    tq.enable_retries(default_max_retries=0)

    @tq.task(name=FLAKY_TASK_NAME, retry=RetryPolicy(max_retries=2))
    async def flaky() -> str:
        attempts.append(1)
        if len(attempts) < 2:
            raise RuntimeError("first attempt fails")
        return "ok"

    return tq


async def drain(attempts: list[int], expected: int) -> None:
    """Wait until the task made ``expected`` attempts, or one second passed.

    Args:
        attempts (list[int]): The attempt log the task appends to.
        expected (int): How many attempts to wait for.
    """
    for _ in range(100):
        if len(attempts) >= expected:
            return
        await asyncio.sleep(0.01)


class TestRegisteredLabels:
    async def test_the_retry_policy_applies_to_a_call_sent_by_name(self) -> None:
        attempts: list[int] = []
        tq = flaky_queue(attempts)
        await tq.connect()
        try:
            await tq.enqueue_by_name(FLAKY_TASK_NAME)
            await drain(attempts, 2)
        finally:
            await tq.disconnect()
        assert len(attempts) == 2

    async def test_without_the_middleware_the_policy_is_lost(self) -> None:
        """Proves the test above is not vacuous: the merge is what retries."""
        attempts: list[int] = []
        tq = flaky_queue(attempts)
        tq.broker.middlewares.pop(0)
        await tq.connect()
        try:
            await tq.enqueue_by_name(FLAKY_TASK_NAME)
            await drain(attempts, 2)
        finally:
            await tq.disconnect()
        assert len(attempts) == 1

    def test_the_middleware_runs_first(self) -> None:
        tq = TaskQueue.memory()
        tq.enable_retries()
        first = tq.broker.middlewares[0]
        assert type(first).__name__ == "_RegisteredLabelsMiddleware"
        assert first.broker is tq.broker

    def test_the_message_labels_win(self) -> None:
        tq = TaskQueue.memory()

        @tq.task(name="probe:labelled", timeout=5)
        async def labelled() -> None:
            return None

        middleware = make_registered_labels_middleware()
        middleware.set_broker(tq.broker)
        from taskiq import TaskiqMessage

        message = TaskiqMessage(
            task_id="1",
            task_name="probe:labelled",
            labels={"timeout": 9},
            args=[],
            kwargs={},
        )
        merged = middleware.pre_execute(message)
        assert merged.labels["timeout"] == 9


class TestUnknownName:
    async def test_the_memory_broker_refuses_an_unknown_name(self) -> None:
        """The in-memory broker is its own worker, so it knows on send."""
        from taskiq.exceptions import SendTaskError

        tq = TaskQueue.memory()
        await tq.connect()
        try:
            with pytest.raises(SendTaskError):
                await tq.enqueue_by_name("probe:missing", 1)
        finally:
            await tq.disconnect()
