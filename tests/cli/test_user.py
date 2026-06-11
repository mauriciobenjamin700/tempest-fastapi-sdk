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


class TestUserCreateAdminPrompt:
    def _create_no_flag(self, with_input: str | None) -> object:
        return runner.invoke(
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
            input=with_input,
        )

    def test_non_interactive_defaults_to_regular(self, project_db: str) -> None:
        # CliRunner stdin is not a tty -> no prompt, defaults to non-admin.
        result = self._create_no_flag(None)
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "Created user: ana@example.com" in result.stdout

    def test_prompts_when_interactive(
        self,
        project_db: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import tempest_fastapi_sdk.cli.user as user_mod

        monkeypatch.setattr(user_mod, "_stdin_is_interactive", lambda: True)
        result = runner.invoke(
            app,
            [
                "user",
                "create",
                "--email",
                "boss@example.com",
                "--password",
                "secret-pass-12",
                "--model",
                "cli_user_model:_CLIUserModel",
            ],
            input="y\n",
        )
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "administrator" in result.stdout
        assert "Created admin: boss@example.com" in result.stdout

    def test_no_admin_flag_skips_prompt(self, project_db: str) -> None:
        result = runner.invoke(
            app,
            [
                "user",
                "create",
                "--email",
                "ana@example.com",
                "--password",
                "secret-pass-12",
                "--no-admin",
                "--model",
                "cli_user_model:_CLIUserModel",
            ],
        )
        assert result.exit_code == 0
        assert "Created user: ana@example.com" in result.stdout


class TestUserPromoteRevoke:
    def _create(self, email: str, *, admin: bool) -> None:
        args = [
            "user",
            "create",
            "--email",
            email,
            "--password",
            "secret-pass-12",
            "--model",
            "cli_user_model:_CLIUserModel",
        ]
        args.append("--admin" if admin else "--no-admin")
        runner.invoke(app, args)

    def test_promote_existing_user(self, project_db: str) -> None:
        self._create("ana@example.com", admin=False)
        result = runner.invoke(
            app,
            [
                "user",
                "promote",
                "--email",
                "ana@example.com",
                "--model",
                "cli_user_model:_CLIUserModel",
            ],
        )
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "Promoted ana@example.com" in result.stdout
        listed = runner.invoke(
            app,
            ["user", "list", "--admin", "--model", "cli_user_model:_CLIUserModel"],
        )
        assert "ana@example.com" in listed.stdout

    def test_revoke_existing_admin(self, project_db: str) -> None:
        self._create("admin@example.com", admin=True)
        result = runner.invoke(
            app,
            [
                "user",
                "revoke",
                "--email",
                "admin@example.com",
                "--model",
                "cli_user_model:_CLIUserModel",
            ],
        )
        assert result.exit_code == 0
        assert "Revoked admin from admin@example.com" in result.stdout
        listed = runner.invoke(
            app,
            ["user", "list", "--admin", "--model", "cli_user_model:_CLIUserModel"],
        )
        assert "admin@example.com" not in listed.stdout

    def test_promote_unknown_email_exits_1(self, project_db: str) -> None:
        result = runner.invoke(
            app,
            [
                "user",
                "promote",
                "--email",
                "ghost@example.com",
                "--model",
                "cli_user_model:_CLIUserModel",
            ],
        )
        assert result.exit_code == 1
        assert "no user found" in (result.stdout + result.stderr)

    def test_promote_is_case_insensitive(self, project_db: str) -> None:
        self._create("mixed@example.com", admin=False)
        result = runner.invoke(
            app,
            [
                "user",
                "promote",
                "--email",
                "MIXED@example.com",
                "--model",
                "cli_user_model:_CLIUserModel",
            ],
        )
        assert result.exit_code == 0


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
