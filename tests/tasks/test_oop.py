"""Tests for class-based background tasks."""

from __future__ import annotations

from taskiq import InMemoryBroker

from tempest_fastapi_sdk.tasks import (
    RetryPolicy,
    Task,
    TaskDef,
    TaskQueue,
    task_method,
)


class TestConstructorForm:
    async def test_register_returns_single_task_and_runs(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        class Add(TaskDef):
            def __init__(self) -> None:
                super().__init__(name="math:add")

            async def run(self, a: int, b: int) -> int:
                return a + b

        add = tq.register(Add())
        assert isinstance(add, Task)
        assert add.task_name == "math:add"

        await tq.connect()
        handle = await add.enqueue(2, 3)
        outcome = await handle.wait_result()
        await tq.disconnect()
        assert outcome.return_value == 5

    async def test_run_inline(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        class Double(TaskDef):
            async def run(self, x: int) -> int:
                return x * 2

        double = tq.register(Double())
        assert await double.run(21) == 42


class TestGroupedForm:
    async def test_register_returns_dict_of_tasks(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        class ReportTasks(TaskDef):
            @task_method(name="reports:nightly")
            async def nightly(self, day: str) -> str:
                return f"nightly {day}"

            @task_method()
            async def weekly(self) -> str:
                return "weekly"

        tasks = tq.register(ReportTasks())
        assert isinstance(tasks, dict)
        assert set(tasks) == {"nightly", "weekly"}
        assert tasks["nightly"].task_name == "reports:nightly"

        await tq.connect()
        handle = await tasks["nightly"].enqueue(day="2026-07-05")
        outcome = await handle.wait_result()
        await tq.disconnect()
        assert outcome.return_value == "nightly 2026-07-05"

    def test_is_grouped_flag(self) -> None:
        class Grouped(TaskDef):
            @task_method()
            async def a(self) -> None: ...

        class Single(TaskDef):
            async def run(self) -> None: ...

        assert Grouped().is_grouped is True
        assert Single().is_grouped is False


class TestRetryAndOptions:
    """A ``RetryPolicy`` must become labels, not a label.

    TaskIQ's retry middleware reads ``retry_on_error`` / ``max_retries``.
    A policy object forwarded verbatim lands as a ``retry`` label nothing
    looks at, so the task never retries — and nothing raises. Asserted on
    the registered task's labels, next to the decorator path's, because
    that is where the difference was invisible.
    """

    @staticmethod
    def _labels(tq: TaskQueue, name: str) -> dict[str, object]:
        return dict(tq.broker.find_task(name).labels)

    def test_task_method_retry_matches_the_decorator(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        @tq.task(name="fn:nightly", retry=RetryPolicy(max_retries=5))
        async def nightly() -> None: ...

        class Reports(TaskDef):
            @task_method(name="cls:nightly", retry=RetryPolicy(max_retries=5))
            async def nightly(self) -> None: ...

        tq.register(Reports())
        assert self._labels(tq, "cls:nightly") == self._labels(tq, "fn:nightly")
        assert self._labels(tq, "cls:nightly")["max_retries"] == 5

    def test_the_class_attribute_covers_every_task(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        class Reports(TaskDef):
            retry = RetryPolicy(max_retries=9)

            @task_method(name="cls:nightly")
            async def nightly(self) -> None: ...

            @task_method(name="cls:weekly")
            async def weekly(self) -> None: ...

        tq.register(Reports())
        assert self._labels(tq, "cls:nightly")["max_retries"] == 9
        assert self._labels(tq, "cls:weekly")["max_retries"] == 9

    def test_task_method_overrides_the_class_attribute(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        class Reports(TaskDef):
            retry = RetryPolicy(max_retries=9)

            @task_method(name="cls:nightly", retry=RetryPolicy(max_retries=1))
            async def nightly(self) -> None: ...

            @task_method(name="cls:weekly")
            async def weekly(self) -> None: ...

        tq.register(Reports())
        assert self._labels(tq, "cls:nightly")["max_retries"] == 1
        assert self._labels(tq, "cls:weekly")["max_retries"] == 9

    def test_constructor_form_takes_retry_and_labels(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        class Nightly(TaskDef):
            async def run(self) -> None: ...

        tq.register(
            Nightly(name="cls:ctor", retry=RetryPolicy(max_retries=4), priority=3),
        )
        assert self._labels(tq, "cls:ctor") == {
            "retry_on_error": True,
            "max_retries": 4,
            "priority": 3,
        }

    def test_no_retry_leaves_the_labels_alone(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        class Nightly(TaskDef):
            async def run(self) -> None: ...

        tq.register(Nightly(name="cls:plain"))
        assert self._labels(tq, "cls:plain") == {}

    def test_constructor_labels_are_not_shared_between_definitions(self) -> None:
        """The empty default lives on the class; it must never be mutated."""

        class Nightly(TaskDef):
            async def run(self) -> None: ...

        Nightly(name="a", priority=3)
        assert Nightly(name="b").task_bindings()[0].options == {}
        assert TaskDef.options == {}
