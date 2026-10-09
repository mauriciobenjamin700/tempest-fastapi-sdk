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
"""

from __future__ import annotations

import sys
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


def _run_mypy(tmp_path: Path) -> list[str]:
    """Run ``mypy --strict`` over the snippet and return its findings.

    Args:
        tmp_path (Path): A scratch directory for the module and the cache.

    Returns:
        list[str]: One line per finding, ``file:line: severity: message``.
    """
    mypy_api = pytest.importorskip("mypy.api", reason="mypy is a dev-group dependency")
    module: Path = tmp_path / "downstream_tasks.py"
    module.write_text(DOWNSTREAM_SNIPPET, encoding="utf-8")
    stdout, stderr, _status = mypy_api.run(
        [
            str(module),
            "--strict",
            "--no-error-summary",
            "--hide-error-context",
            "--no-color-output",
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            "--python-executable",
            sys.executable,
        ]
    )
    assert not stderr.strip() or "unused section" in stderr, stderr
    return [line for line in stdout.splitlines() if "downstream_tasks.py" in line]


def _wrong_line() -> int:
    """Return the 1-based snippet line carrying the deliberate mistake.

    Returns:
        int: The line number mypy reports for the ``arg-type`` error.
    """
    for number, line in enumerate(DOWNSTREAM_SNIPPET.splitlines(), start=1):
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
