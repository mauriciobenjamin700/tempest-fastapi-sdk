"""Tests for ``tempest user`` commands."""

from __future__ import annotations

import sys
import textwrap
import types
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from typer.testing import CliRunner

from tempest_fastapi_sdk import BaseModel, BaseUserModel
from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()


class _CLIUserModel(BaseUserModel):
    """Concrete UserModel discoverable as ``cli_user_model:_CLIUserModel``."""

    __tablename__ = "cli_users"


# Make the model importable via the dotted spec used by the tests.
_module = types.ModuleType("cli_user_model")
_module._CLIUserModel = _CLIUserModel  # type: ignore[attr-defined]
sys.modules["cli_user_model"] = _module


@pytest.fixture
def project_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[str]:
    """Fresh on-disk SQLite + the URL pointing at it."""
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "app.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    import asyncio

    async def _create_schema() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(BaseModel.metadata.create_all)
        await engine.dispose()

    asyncio.run(_create_schema())
    yield url


class TestUserCreate:
    def test_create_regular_user(self, project_db: str) -> None:
        result = runner.invoke(
            app,
            [
                "user",
                "create",
                "--email",
                "ana@example.com",
                "--password",
                "secret-pass-12",
                "--model",
                "cli_user_model:_CLIUserModel",
            ],
        )
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "Created user: ana@example.com" in result.stdout

    def test_create_admin(self, project_db: str) -> None:
        result = runner.invoke(
            app,
            [
                "user",
                "create",
                "--email",
                "admin@example.com",
                "--password",
                "another-pass-12",
                "--admin",
                "--model",
                "cli_user_model:_CLIUserModel",
            ],
        )
        assert result.exit_code == 0
        assert "Created admin: admin@example.com" in result.stdout

    def test_short_password_rejected(self, project_db: str) -> None:
        result = runner.invoke(
            app,
            [
                "user",
                "create",
                "--email",
                "x@y",
                "--password",
                "short",
                "--model",
                "cli_user_model:_CLIUserModel",
            ],
        )
        assert result.exit_code == 2
        assert "at least 8" in (result.stdout + result.stderr)

    def test_invalid_model_spec_rejected(self, project_db: str) -> None:
        result = runner.invoke(
            app,
            [
                "user",
                "create",
                "--email",
                "x@y",
                "--password",
                "good-pass-12",
                "--model",
                "no_module:Missing",
            ],
        )
        assert result.exit_code == 2
        assert "cannot import" in (result.stdout + result.stderr)

    def test_model_must_be_baseusermodel_subclass(
        self,
        project_db: str,
    ) -> None:
        # Inject a class that is NOT a BaseUserModel subclass.
        rogue = types.ModuleType("rogue_module")

        class Rogue:
            pass

        rogue.Rogue = Rogue  # type: ignore[attr-defined]
        sys.modules["rogue_module"] = rogue

        result = runner.invoke(
            app,
            [
                "user",
                "create",
                "--email",
                "x@y",
                "--password",
                "good-pass-12",
                "--model",
                "rogue_module:Rogue",
            ],
        )
        assert result.exit_code == 2
        assert "BaseUserModel subclass" in (result.stdout + result.stderr)


class TestUserList:
    def test_list_empty(self, project_db: str) -> None:
        result = runner.invoke(
            app,
            ["user", "list", "--model", "cli_user_model:_CLIUserModel"],
        )
        assert result.exit_code == 0
        assert "(no users)" in result.stdout

    def test_list_after_create(self, project_db: str) -> None:
        runner.invoke(
            app,
            [
                "user",
                "create",
                "--email",
                "ana@example.com",
                "--password",
                "good-pass-12",
                "--admin",
                "--model",
                "cli_user_model:_CLIUserModel",
            ],
        )
        result = runner.invoke(
            app,
            ["user", "list", "--model", "cli_user_model:_CLIUserModel"],
        )
        assert result.exit_code == 0
        assert "ana@example.com" in result.stdout
        assert "+admin" in result.stdout

    def test_list_admin_only(self, project_db: str) -> None:
        # Create one regular + one admin.
        for email, admin_flag in [
            ("ana@example.com", []),
            ("admin@example.com", ["--admin"]),
        ]:
            runner.invoke(
                app,
                [
                    "user",
                    "create",
                    "--email",
                    email,
                    "--password",
                    "good-pass-12",
                    *admin_flag,
                    "--model",
                    "cli_user_model:_CLIUserModel",
                ],
            )
        result = runner.invoke(
            app,
            [
                "user",
                "list",
                "--admin",
                "--model",
                "cli_user_model:_CLIUserModel",
            ],
        )
        assert result.exit_code == 0
        assert "admin@example.com" in result.stdout
        assert "ana@example.com" not in result.stdout


def _seed_settings_module(target: Path, database_url: str) -> None:
    """Write a minimal ``src/core/settings.py`` for resolver tests."""
    (target / "src" / "core").mkdir(parents=True, exist_ok=True)
    (target / "src" / "core" / "__init__.py").write_text("", encoding="utf-8")
    (target / "src" / "__init__.py").write_text("", encoding="utf-8")
    (target / "src" / "core" / "settings.py").write_text(
        textwrap.dedent(
            f"""
            from types import SimpleNamespace
            settings = SimpleNamespace(DATABASE_URL="{database_url}")
            """
        ).strip(),
        encoding="utf-8",
    )
