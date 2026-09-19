"""Tests for ``tempest queue`` and ``tempest tasks``.

The task side runs against a real in-memory ``TaskQueue``, so the name
resolution, the enqueue and the "no such task" path are measured rather
than mocked. The broker side uses a recording stand-in: what the command
owns is the connect / publish / disconnect order, not AMQP.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()

_QUEUE_MODULE = '''
"""Recording stand-in for a FastStream broker."""

from typing import Any


class _Subscriber:
    def __init__(self, queue: str) -> None:
        self.queue = queue
        self.calls: list[Any] = []


class _Broker:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.published: list[tuple[Any, tuple[Any, ...]]] = []
        self.subscribers: list[_Subscriber] = [_Subscriber("orders.paid")]

    async def start(self) -> None:
        self.events.append("start")

    async def stop(self) -> None:
        self.events.append("stop")

    async def publish(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self.published.append((message, args))
        self.events.append("publish")


broker = _Broker()
'''

_TASKS_MODULE = '''
"""In-memory TaskQueue with one registered task."""

from tempest_fastapi_sdk.tasks import TaskQueue

tq: TaskQueue = TaskQueue.memory()


@tq.task
async def send_welcome(email: str, retries: int = 0) -> str:
    return f"{email}:{retries}"
'''


@pytest.fixture(autouse=True)
def forget_project_modules() -> None:
    """Drop the tmp project's package around every test.

    Both commands import ``src.queue`` / ``src.tasks`` by name after
    putting the working directory on ``sys.path``. Without restoring
    both, a test standing in an empty directory still resolves the
    previous test's project — out of ``sys.modules``, or out of the
    ``sys.path`` entry the previous command left behind — and reports a
    broker that is not there.
    """
    original_path = list(sys.path)
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]
    yield
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]
    sys.path[:] = original_path


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a project exposing ``src.queue:broker`` and ``src.tasks:tq``."""
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "queue.py").write_text(_QUEUE_MODULE, encoding="utf-8")
    (tmp_path / "src" / "tasks.py").write_text(_TASKS_MODULE, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]


class TestQueuePublish:
    def test_connects_publishes_and_closes(self, service: Path) -> None:
        """The forgotten step is the close, which is what flushes the publish."""
        result = runner.invoke(app, ["queue", "publish", "orders.paid", "hello"])
        assert result.exit_code == 0, result.stdout + result.stderr

        import src.queue  # type: ignore[import-not-found]

        assert src.queue.broker.events == ["start", "publish", "stop"]
        assert src.queue.broker.published[0][0] == "hello"

    def test_json_payload_is_decoded(self, service: Path) -> None:
        result = runner.invoke(
            app,
            ["queue", "publish", "orders.paid", '{"id": 7}', "--json"],
        )
        assert result.exit_code == 0, result.stdout + result.stderr

        import src.queue  # type: ignore[import-not-found]

        assert src.queue.broker.published[0][0] == {"id": 7}

    def test_invalid_json_is_a_usage_error(self, service: Path) -> None:
        result = runner.invoke(
            app,
            ["queue", "publish", "orders.paid", "{not json", "--json"],
        )
        assert result.exit_code == 2
        assert "does not parse" in result.stderr

    def test_handlers_lists_the_channel(self, service: Path) -> None:
        result = runner.invoke(app, ["queue", "handlers"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "orders.paid" in result.stdout

    def test_missing_broker_lists_what_was_tried(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["queue", "publish", "x", "y"])
        assert result.exit_code == 2
        assert "no broker found" in result.stderr


class TestTasks:
    def test_list_prints_the_registered_name(self, service: Path) -> None:
        """TaskIQ registers under '<module>:<function>', not the bare name."""
        result = runner.invoke(app, ["tasks", "list"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "send_welcome" in result.stdout
        assert "src.tasks:send_welcome" in result.stdout

    def test_run_enqueues(self, service: Path) -> None:
        result = runner.invoke(
            app,
            [
                "tasks",
                "run",
                "src.tasks:send_welcome",
                "--arg",
                "ana@example.com",
                "--kwarg",
                "retries=2",
            ],
        )
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "Enqueued src.tasks:send_welcome" in result.stdout

    def test_json_values_are_decoded(self, service: Path) -> None:
        result = runner.invoke(
            app,
            [
                "tasks",
                "run",
                "src.tasks:send_welcome",
                "--arg",
                '"ana@example.com"',
                "--kwarg",
                "retries=3",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.stdout + result.stderr

    def test_unknown_task_exits_one(self, service: Path) -> None:
        result = runner.invoke(app, ["tasks", "run", "src.tasks:absent"])
        assert result.exit_code == 1
        assert "tempest tasks list" in result.stderr

    def test_kwarg_without_equals_is_a_usage_error(self, service: Path) -> None:
        result = runner.invoke(
            app,
            ["tasks", "run", "src.tasks:send_welcome", "--kwarg", "retries"],
        )
        assert result.exit_code == 2
        assert "NAME=VALUE" in result.stderr

    def test_missing_queue_lists_what_was_tried(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["tasks", "list"])
        assert result.exit_code == 2
        assert "no TaskQueue found" in result.stderr
