"""``TaskQueue`` type-checks the way the recipe uses it, under ``mypy --strict``.

Two defects shipped together (issue #449), and neither was visible to the
gate, because ``make type`` checks the package and the package never calls
itself the way a service does:

* ``TaskIQSettingsLike`` declared its four fields as bare annotations. In a
  ``Protocol`` a bare annotation is a *settable* attribute, while
  ``BaseAppSettings`` is ``frozen=True`` — so mypy rejected
  ``TaskQueue.from_settings(Settings())`` for the very ``Settings`` the
  mixins build (``expected settable variable, got read-only attribute``).
* ``TaskQueue.task`` returned ``Any``. Under ``--strict``
  (``disallow_untyped_decorators``) ``@tq.task(name=...)`` was an
  ``untyped-decorator`` error and the decorated name lost its signature.

The test runs mypy over a snippet shaped like a downstream service and
asserts the exact diagnostics: none where the recipe is followed, and an
``arg-type`` error where a call contradicts the decorated signature — which
is the proof that the signature survived, since ``Any`` accepts that call.

A second snippet pins the return types the rest of ``tasks`` hands a
consumer (issue #550): ``TaskQueue.scheduler`` was ``-> Any``, so the
recipe's ``scheduler = tq.scheduler`` came out untyped. ``assert_type`` is
an *exact* match, so a return that slides back to ``Any`` fails it; the
snippet carries one deliberate ``assert_type`` on an ``Any`` value as the
control that the check is not vacuous.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

import pytest

DOWNSTREAM_SNIPPET: str = '''
from dataclasses import dataclass

from tempest_fastapi_sdk.settings import BaseAppSettings, TaskIQSettings
from tempest_fastapi_sdk.tasks import TaskQueue


class Settings(TaskIQSettings, BaseAppSettings):
    """Service settings composed from the SDK mixins (frozen)."""


@dataclass
class PlainSettings:
    """A plain test double with ordinary, writable attributes."""

    TASKIQ_BROKER_URL: str = ""
    TASKIQ_RESULT_BACKEND_URL: str | None = None
    TASKIQ_STORE_RESULTS: bool = False
    TASKIQ_RESULT_TTL_SECONDS: int = 0


tq: TaskQueue = TaskQueue.from_settings(Settings())
plain: TaskQueue = TaskQueue.from_settings(PlainSettings())


@tq.task
async def add(a: int, b: int) -> int:
    """Bare decorator."""
    return a + b


@tq.task(name="reports:mul")
async def mul(a: int, b: int) -> int:
    """Decorator with options."""
    return a * b


@tq.on_startup
async def opened() -> None:
    """Bare lifecycle hook."""


@tq.on_shutdown(scope="both")
async def closed() -> None:
    """Lifecycle hook with options."""


async def caller() -> int:
    """Call the decorated tasks the way a handler would."""
    total: int = await add.run(1, 2) + await mul.run(a=3, b=4)
    return total


async def wrong() -> None:
    """Contradict the signature: only a preserved signature rejects this."""
    await mul.run("three", 4)  # MARK-WRONG


reveal_type(add)
reveal_type(mul)
reveal_type(opened)
'''
"""Downstream code written the way the ``tasks`` recipe tells services to."""

RETURN_TYPES_SNIPPET: str = '''
from collections.abc import Awaitable, Callable
from typing import Any, assert_type

from taskiq import AsyncTaskiqTask, InMemoryBroker, TaskiqMiddleware, TaskiqScheduler

from tempest_fastapi_sdk.admin import AdminActionContext, AdminActionResult, AdminModel
from tempest_fastapi_sdk.tasks import (
    AsyncTaskBrokerManager,
    AsyncTaskScheduler,
    BaseJobModel,
    DeadLetter,
    TaskMetrics,
    TaskQueue,
    make_dead_letter_middleware,
    make_job_admin_model,
    make_requeue_action,
)

tq: TaskQueue = TaskQueue.memory()
scheduler = tq.scheduler
assert_type(scheduler, TaskiqScheduler)


@tq.task
async def add(a: int, b: int) -> int:
    """A queue task."""
    return a + b


async def enqueue() -> None:
    """Enqueue and keep the result type on the handle."""
    handle = await add.enqueue(1, 2)
    assert_type(handle, AsyncTaskiqTask[int])


broker: InMemoryBroker = InMemoryBroker()
manager: AsyncTaskBrokerManager = AsyncTaskBrokerManager(broker)
periodic: AsyncTaskScheduler = AsyncTaskScheduler(broker)


@manager.task
async def bare(x: int) -> str:
    """Bare manager decorator."""
    return str(x)


@manager.task(task_name="named")
async def named(x: int) -> str:
    """Manager decorator with options."""
    return str(x)


@periodic.cron("* * * * *")
async def tick(n: int) -> None:
    """Cron task."""


@periodic.interval(seconds=5)
async def every(n: int) -> None:
    """Interval task."""


@periodic.schedule([{"cron": "* * * * *"}])
async def raw(n: int) -> None:
    """Raw-schedule task."""


async def sink(dead_letter: DeadLetter) -> None:
    """A dead-letter sink."""


def wiring(metrics: TaskMetrics, model: type[BaseJobModel]) -> None:
    """The factories whose results a service passes on."""
    assert_type(make_dead_letter_middleware(sink), TaskiqMiddleware)
    assert_type(metrics.middleware(), TaskiqMiddleware)
    assert_type(make_job_admin_model(model), AdminModel[Any])
    assert_type(
        make_requeue_action(tq),
        Callable[[AdminActionContext], Awaitable[AdminActionResult | None]],
    )


untyped: Any = None
assert_type(untyped, TaskiqScheduler)  # MARK-WRONG

reveal_type(bare)
reveal_type(named)
reveal_type(tick)
reveal_type(every)
reveal_type(raw)
'''
"""Downstream code reading every typed return of ``tasks`` (issue #550)."""


def _shared_mypy_cache(tag: str) -> Path:
    """Return a mypy cache directory that survives between runs.

    A cache under ``tmp_path`` started cold on every run and re-checked the
    whole SDK: 38 s for this test alone in the 2026-10-09 ``--durations``
    report. mypy's incremental mode is sound -- a changed module is
    re-analysed -- so a persistent cache only skips work whose inputs did not
    move. The directory is keyed on the repository path (a worktree gets its
    own) and on ``tag`` (no two tests write the same cache concurrently),
    the same scheme ``tests/test_docs_type_guard.py`` uses.

    Args:
        tag (str): Name of the test owning the cache.

    Returns:
        Path: The cache directory.
    """
    root: str = str(Path(__file__).resolve().parents[2])
    digest: str = hashlib.sha256(root.encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"tempest-mypy-{digest}-{tag}"


def _run_mypy(
    tmp_path: Path,
    snippet: str = DOWNSTREAM_SNIPPET,
    tag: str = "queue-typing",
) -> list[str]:
    """Run ``mypy --strict`` over a snippet and return its findings.

    Args:
        tmp_path (Path): A scratch directory for the module and the cache.
        snippet (str): The downstream module to check.
        tag (str): Cache name; distinct per test so no two runs share one.

    Returns:
        list[str]: One line per finding, ``file:line: severity: message``.
    """
    mypy_api = pytest.importorskip("mypy.api", reason="mypy is a dev-group dependency")
    module: Path = tmp_path / "downstream_tasks.py"
    module.write_text(snippet, encoding="utf-8")
    stdout, stderr, _status = mypy_api.run(
        [
            str(module),
            "--strict",
            "--no-error-summary",
            "--hide-error-context",
            "--no-color-output",
            "--cache-dir",
            str(_shared_mypy_cache(tag)),
            "--python-executable",
            sys.executable,
        ]
    )
    assert not stderr.strip() or "unused section" in stderr, stderr
    return [line for line in stdout.splitlines() if "downstream_tasks.py" in line]


def _wrong_line(snippet: str = DOWNSTREAM_SNIPPET) -> int:
    """Return the 1-based snippet line carrying the deliberate mistake.

    Args:
        snippet (str): The snippet holding a ``MARK-WRONG`` line.

    Returns:
        int: The line number mypy reports for the deliberate error.
    """
    for number, line in enumerate(snippet.splitlines(), start=1):
        if "MARK-WRONG" in line:
            return number
    raise AssertionError("snippet lost its MARK-WRONG line")


def test_recipe_type_checks_and_keeps_the_task_signature(tmp_path: Path) -> None:
    """Frozen and plain settings pass; decorated tasks keep their types.

    Args:
        tmp_path (Path): Pytest's scratch directory.
    """
    findings: list[str] = _run_mypy(tmp_path)
    errors: list[str] = [line for line in findings if ": error:" in line]
    reveals: list[str] = [line for line in findings if "Revealed type" in line]

    assert len(errors) == 1, "\n".join(errors)
    assert f":{_wrong_line()}:" in errors[0]
    assert "[arg-type]" in errors[0]

    assert len(reveals) == 3, "\n".join(findings)
    assert 'Task[[a: int, b: int], int]"' in reveals[0]
    assert 'Task[[a: int, b: int], int]"' in reveals[1]
    assert "def () -> typing.Coroutine[Any, Any, None]" in reveals[2]


def test_tasks_returns_are_concrete_not_any(tmp_path: Path) -> None:
    """Every narrowed ``tasks`` return reaches the consumer as its real type.

    The only error is the control on the ``MARK-WRONG`` line, which shows
    ``assert_type`` rejects ``Any``; a narrowed return that regresses to
    ``Any`` adds an ``assert-type`` error of its own. The decorators are read
    through ``reveal_type`` because ``assert_type`` cannot spell a
    ``ParamSpec`` with parameter names.

    Args:
        tmp_path (Path): Pytest's scratch directory.
    """
    findings: list[str] = _run_mypy(tmp_path, RETURN_TYPES_SNIPPET, "tasks-returns")
    errors: list[str] = [line for line in findings if ": error:" in line]
    reveals: list[str] = [line for line in findings if "Revealed type" in line]

    assert len(errors) == 1, "\n".join(errors)
    assert f":{_wrong_line(RETURN_TYPES_SNIPPET)}:" in errors[0]
    assert 'Expression is of type "Any"' in errors[0]

    decorated: str = (
        "taskiq.decor.AsyncTaskiqDecoratedTask"
        "[[{}: int], typing.Coroutine[Any, Any, {}]]"
    )
    assert len(reveals) == 5, "\n".join(findings)
    assert decorated.format("x", "str") in reveals[0]
    assert decorated.format("x", "str") in reveals[1]
    for line in reveals[2:]:
        assert decorated.format("n", "None") in line, line
