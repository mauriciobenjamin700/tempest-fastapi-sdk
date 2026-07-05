"""Tests for class-based background tasks."""

from __future__ import annotations

from taskiq import InMemoryBroker

from tempest_fastapi_sdk.tasks import Task, TaskDef, TaskQueue, task_method


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
