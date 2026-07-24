"""Tests for TaskQueue reliability + observability (retries, DLQ, metrics)."""

from __future__ import annotations

from prometheus_client import CollectorRegistry
from taskiq import InMemoryBroker

from tempest_fastapi_sdk.tasks import (
    DeadLetter,
    RetryPolicy,
    TaskMetrics,
    TaskQueue,
)
from tempest_fastapi_sdk.tasks.observability import _is_terminal_failure


class _CollectingSink:
    """A dead-letter sink that records every call, for assertions."""

    def __init__(self) -> None:
        self.letters: list[DeadLetter] = []

    async def __call__(self, dead_letter: DeadLetter) -> None:
        self.letters.append(dead_letter)


class TestRetryPolicy:
    def test_as_labels(self) -> None:
        assert RetryPolicy(max_retries=5).as_labels() == {
            "retry_on_error": True,
            "max_retries": 5,
        }

    def test_disabled(self) -> None:
        assert RetryPolicy(on_error=False).as_labels()["retry_on_error"] is False


class TestIsTerminalFailure:
    def test_no_retry_is_terminal_immediately(self) -> None:
        assert _is_terminal_failure({}, 3) is True

    def test_retry_not_exhausted_is_not_terminal(self) -> None:
        labels = {"retry_on_error": True, "max_retries": 3, "_retries": 0}
        assert _is_terminal_failure(labels, 3) is False

    def test_retry_exhausted_is_terminal(self) -> None:
        labels = {"retry_on_error": True, "max_retries": 3, "_retries": 2}
        assert _is_terminal_failure(labels, 3) is True

    def test_string_label_coerced(self) -> None:
        assert _is_terminal_failure({"retry_on_error": "false"}, 3) is True


class TestTaskMetrics:
    async def test_records_success_run(self) -> None:
        registry = CollectorRegistry()
        metrics = TaskMetrics(registry=registry)
        tq = TaskQueue(InMemoryBroker())
        tq.enable_metrics(metrics)

        @tq.task(name="jobs:ok")
        async def ok() -> str:
            return "done"

        await tq.connect()
        await (await ok.enqueue()).wait_result()
        await tq.disconnect()

        assert (
            registry.get_sample_value(
                "tasks_runs_total", {"task": "jobs:ok", "status": "success"}
            )
            == 1.0
        )
        assert (
            registry.get_sample_value(
                "tasks_duration_seconds_count", {"task": "jobs:ok"}
            )
            == 1.0
        )

    async def test_records_error_status(self) -> None:
        registry = CollectorRegistry()
        metrics = TaskMetrics(registry=registry)
        tq = TaskQueue(InMemoryBroker())
        tq.enable_metrics(metrics)

        @tq.task(name="jobs:boom")
        async def boom() -> None:
            raise ValueError("nope")

        await tq.connect()
        await (await boom.enqueue()).wait_result()
        await tq.disconnect()

        assert (
            registry.get_sample_value(
                "tasks_runs_total", {"task": "jobs:boom", "status": "error"}
            )
            == 1.0
        )


class TestDeadLetter:
    async def test_no_retry_dead_letters_on_first_failure(self) -> None:
        sink = _CollectingSink()
        tq = TaskQueue(InMemoryBroker())
        tq.dead_letter(sink)

        @tq.task(name="jobs:fail")
        async def fail(x: int) -> None:
            raise RuntimeError("kaput")

        await tq.connect()
        await (await fail.enqueue(7)).wait_result()
        await tq.disconnect()

        assert len(sink.letters) == 1
        letter = sink.letters[0]
        assert letter.task_name == "jobs:fail"
        assert letter.args == [7]
        assert isinstance(letter.exception, RuntimeError)

    async def test_retries_then_dead_letters_once(self) -> None:
        sink = _CollectingSink()
        attempts: list[int] = []
        tq = TaskQueue(InMemoryBroker())
        tq.enable_retries(default_max_retries=3)
        tq.dead_letter(sink, default_max_retries=3)

        @tq.task(name="jobs:flaky", retry=RetryPolicy(max_retries=3))
        async def flaky() -> None:
            attempts.append(1)
            raise ValueError("still failing")

        await tq.connect()
        await (await flaky.enqueue()).wait_result()
        await tq.disconnect()

        assert len(attempts) == 3
        assert len(sink.letters) == 1
        assert sink.letters[0].retries == 3

    async def test_sink_failure_does_not_crash(self) -> None:
        class _BrokenSink:
            async def __call__(self, dead_letter: DeadLetter) -> None:
                raise OSError("sink down")

        tq = TaskQueue(InMemoryBroker())
        tq.dead_letter(_BrokenSink())

        @tq.task(name="jobs:fail2")
        async def fail() -> None:
            raise RuntimeError("boom")

        await tq.connect()
        result = await (await fail.enqueue()).wait_result()
        await tq.disconnect()
        assert result.is_err is True


class TestEnableRetriesSuccess:
    async def test_retry_recovers_before_exhaustion(self) -> None:
        sink = _CollectingSink()
        attempts: list[int] = []
        tq = TaskQueue(InMemoryBroker())
        tq.enable_retries(default_max_retries=5)
        tq.dead_letter(sink, default_max_retries=5)

        @tq.task(name="jobs:recovers", retry=RetryPolicy(max_retries=5))
        async def recovers() -> str:
            attempts.append(1)
            if len(attempts) < 3:
                raise ValueError("not yet")
            return "ok"

        await tq.connect()
        await (await recovers.enqueue()).wait_result()
        await tq.disconnect()

        assert len(attempts) == 3
        assert sink.letters == []
