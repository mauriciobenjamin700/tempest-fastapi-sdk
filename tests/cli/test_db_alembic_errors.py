"""An expected Alembic failure reads as one line, not twenty frames.

``CommandError`` is how Alembic reports that the database is not in the
state a command needs — a migration is pending, the history forked, the
revision was deleted. None of that is a bug, and all of it used to reach
Typer's ``pretty_exceptions``: ~20 frames of alembic, asyncio, greenlet and
the project's own ``env.py``, with this package's path at the top, so the
operator reads it as an SDK defect and scrolls to the last line to find the
one sentence that mattered.

The substrings matched here were read from alembic 1.18.4 —
``autogenerate/api.py:601`` and ``script/base.py:214,222,236`` — not
recalled.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()


class _ExplodingHelper:
    """An ``AlembicHelper`` whose every command raises ``CommandError``.

    Attributes:
        message (str): What the raised error carries.
    """

    def __init__(self, message: str) -> None:
        """Store the message every method will raise with.

        Args:
            message (str): The Alembic error text to reproduce.
        """
        self.message: str = message

    def _boom(self, *_args: Any, **_kwargs: Any) -> Any:
        """Raise the configured ``CommandError``.

        Raises:
            CommandError: Always.
        """
        from alembic.util.exc import CommandError

        raise CommandError(self.message)

    revision = _boom
    upgrade = _boom
    downgrade = _boom
    current = _boom
    stamp = _boom
    history = _boom


@pytest.fixture
def failing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Run in an empty project with an ``alembic.ini`` the CLI accepts.

    Args:
        tmp_path (Path): Pytest's per-test directory.
        monkeypatch (pytest.MonkeyPatch): Used to chdir and clear the URL.

    Returns:
        Path: The project root the CLI runs in.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TEMPEST_DEBUG", raising=False)
    (tmp_path / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    return tmp_path


def _fail_with(monkeypatch: pytest.MonkeyPatch, message: str) -> None:
    """Make ``_helper`` hand every command the exploding double.

    Args:
        monkeypatch (pytest.MonkeyPatch): The patcher.
        message (str): The ``CommandError`` text to raise.
    """
    from tempest_fastapi_sdk.cli import db as db_cli

    monkeypatch.setattr(
        db_cli,
        "_helper",
        lambda *_args, **_kwargs: _ExplodingHelper(message),
    )


class TestKnownConditions:
    def test_a_database_behind_head_says_what_to_run(
        self, failing: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The condition in the report."""
        _fail_with(monkeypatch, "Target database is not up to date.")
        result = runner.invoke(app, ["db", "revision", "-m", "x", "--autogenerate"])
        assert result.exit_code == 1
        output = result.stdout + result.stderr
        assert "tempest db upgrade" in output
        assert "Traceback" not in output

    def test_a_forked_history_says_what_to_run(
        self, failing: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fail_with(
            monkeypatch,
            "Multiple head revisions are present for given argument 'head'; "
            "please specify a specific target revision",
        )
        result = runner.invoke(app, ["db", "upgrade"])
        assert result.exit_code == 1
        assert "tempest db history" in result.stdout + result.stderr

    def test_a_missing_revision_says_what_to_run(
        self, failing: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fail_with(monkeypatch, "Can't locate revision identified by 'abc123'")
        result = runner.invoke(app, ["db", "downgrade", "abc123"])
        assert result.exit_code == 1
        assert "tempest db history" in result.stdout + result.stderr

    def test_revisions_off_the_branch_say_what_to_run(
        self, failing: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fail_with(
            monkeypatch,
            "Requested range a:b does not refer to ancestor/descendant "
            "revisions along the same branch",
        )
        result = runner.invoke(app, ["db", "downgrade", "b"])
        assert result.exit_code == 1
        assert "tempest db history" in result.stdout + result.stderr


class TestFallbacks:
    def test_an_unknown_message_is_printed_verbatim(
        self, failing: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A new Alembic message degrades to Alembic's words, not a guess."""
        _fail_with(monkeypatch, "Some future alembic condition")
        result = runner.invoke(app, ["db", "current"])
        assert result.exit_code == 1
        output = result.stdout + result.stderr
        assert "error: Some future alembic condition" in output
        assert "Traceback" not in output

    def test_debug_restores_the_traceback(
        self, failing: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``TEMPEST_DEBUG=1`` is the escape hatch, and it propagates."""
        from alembic.util.exc import CommandError

        monkeypatch.setenv("TEMPEST_DEBUG", "1")
        _fail_with(monkeypatch, "Target database is not up to date.")
        result = runner.invoke(app, ["db", "stamp", "head"])
        assert result.exit_code != 0
        assert isinstance(result.exception, CommandError)

    def test_every_wrapped_command_is_covered(
        self, failing: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All six call sites route through the translator, not just one."""
        invocations: list[list[str]] = [
            ["db", "revision", "-m", "x"],
            ["db", "upgrade"],
            ["db", "downgrade", "base"],
            ["db", "current"],
            ["db", "stamp", "head"],
            ["db", "history"],
        ]
        for argv in invocations:
            _fail_with(monkeypatch, "Target database is not up to date.")
            result = runner.invoke(app, argv)
            assert result.exit_code == 1, argv
            assert "tempest db upgrade" in result.stdout + result.stderr, argv


def _seed_model_module(target: Path) -> None:
    """Seed a ``src/db/models.py`` with one table, so autogenerate has work.

    Args:
        target (Path): The project root to write into.
    """
    (target / "src" / "db").mkdir(parents=True, exist_ok=True)
    (target / "src" / "__init__.py").write_text("", encoding="utf-8")
    (target / "src" / "db" / "__init__.py").write_text("", encoding="utf-8")
    (target / "src" / "db" / "models.py").write_text(
        "from sqlalchemy.orm import Mapped, mapped_column\n"
        "from tempest_fastapi_sdk import BaseModel\n\n\n"
        "class WidgetModel(BaseModel):\n"
        '    __tablename__ = "widget"\n'
        "    name: Mapped[str] = mapped_column()\n",
        encoding="utf-8",
    )


class TestAgainstRealAlembic:
    def test_the_reported_repro_prints_one_line(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No double: a real Alembic refusing a real autogenerate.

        The steps from the report — generate a revision, do **not** apply
        it, generate again. Alembic refuses to diff from a stale head.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("TEMPEST_DEBUG", raising=False)
        monkeypatch.setenv(
            "DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'repro.db'}"
        )
        _seed_model_module(tmp_path)
        runner.invoke(app, ["db", "init", "--metadata-import", "src.db.models"])

        first = runner.invoke(app, ["db", "revision", "-m", "one", "--autogenerate"])
        assert first.exit_code == 0, first.stdout + first.stderr

        second = runner.invoke(app, ["db", "revision", "-m", "two", "--autogenerate"])
        output = second.stdout + second.stderr
        assert second.exit_code == 1, output
        assert "the database is behind head" in output
        assert "tempest db upgrade" in output
        assert "Traceback" not in output
        assert "greenlet" not in output
