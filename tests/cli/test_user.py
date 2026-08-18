"""Tests for ``tempest user`` commands."""

from __future__ import annotations

import sys
import textwrap
import types
from collections.abc import Iterator
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import ClassVar
from uuid import UUID

import pytest
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    Numeric,
    String,
    Time,
    Uuid,
)
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Mapped, mapped_column
from typer.testing import CliRunner

from tempest_fastapi_sdk import BaseModel, BaseStrEnum, BaseUserModel
from tempest_fastapi_sdk.cli.main import app
from tempest_fastapi_sdk.db.enums import TempestEnum

runner = CliRunner()


class _CLIUserModel(BaseUserModel):
    """Concrete UserModel discoverable as ``cli_user_model:_CLIUserModel``."""

    __tablename__ = "cli_users"


class _CLILocale(BaseStrEnum):
    """Locale choices for the extended model below."""

    PT_BR = "pt-BR"
    EN_US = "en-US"


class _CLIRichUserModel(BaseUserModel):
    """UserModel that adds columns of its own, like a real service does.

    ``display_name`` is the shape from the issue: ``NOT NULL`` with no
    default, so nothing but an explicit value can seed the row.
    """

    __tablename__ = "cli_rich_users"

    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    seat_count: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    locale: Mapped[_CLILocale | None] = mapped_column(nullable=True, default=None)
    avatar_color: Mapped[str] = mapped_column(
        String(16), nullable=False, default="#7dd3fc"
    )


# Make the models importable via the dotted spec used by the tests.
_module = types.ModuleType("cli_user_model")
_module._CLIUserModel = _CLIUserModel  # type: ignore[attr-defined]
_module._CLIRichUserModel = _CLIRichUserModel  # type: ignore[attr-defined]
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


class TestCoerceColumnValue:
    """The type table behind ``--set``, exercised one column type at a time."""

    @pytest.mark.parametrize(
        ("column_type", "raw", "expected"),
        [
            (String(8), "Ana", "Ana"),
            (Integer(), "3", 3),
            (Float(), "1.5", 1.5),
            (Numeric(), "9.99", Decimal("9.99")),
            (Boolean(), "yes", True),
            (Boolean(), "off", False),
            (Uuid(), "d3b07384-d9a0-4f1e-9f0a-2c1b6a5e7c11", None),
            (Date(), "2026-08-18", date(2026, 8, 18)),
            (Time(), "07:30:00", time(7, 30)),
            (DateTime(), "2026-08-18T07:30:00", datetime(2026, 8, 18, 7, 30)),
            (JSON(), '{"a": 1}', {"a": 1}),
            (TempestEnum(_CLILocale), "pt-BR", _CLILocale.PT_BR),
            (TempestEnum(_CLILocale), "EN_US", _CLILocale.EN_US),
        ],
    )
    def test_converts_to_the_column_type(
        self,
        column_type: object,
        raw: str,
        expected: object,
    ) -> None:
        from sqlalchemy import Column

        from tempest_fastapi_sdk.cli.user import _coerce_column_value

        value = _coerce_column_value(Column("c", column_type), raw)
        if expected is None:
            assert value == UUID(raw)
            return
        assert value == expected

    @pytest.mark.parametrize(
        ("column_type", "raw"),
        [
            (Integer(), "three"),
            (Boolean(), "maybe"),
            (Uuid(), "not-a-uuid"),
            (Date(), "18/08/2026"),
            (JSON(), "{not json}"),
            (TempestEnum(_CLILocale), "fr-FR"),
        ],
    )
    def test_rejects_a_value_the_type_cannot_hold(
        self,
        column_type: object,
        raw: str,
    ) -> None:
        from sqlalchemy import Column

        from tempest_fastapi_sdk.cli.user import _coerce_column_value

        with pytest.raises(ValueError):
            _coerce_column_value(Column("c", column_type), raw)


class TestUserCreateConflict:
    def test_duplicate_email_reports_the_database_message(
        self,
        project_db: str,
    ) -> None:
        """A rejected insert prints the reason, not a traceback."""
        args = [
            "user",
            "create",
            "--email",
            "ana@example.com",
            "--password",
            "secret-pass-12",
            "--no-admin",
            "--model",
            "cli_user_model:_CLIUserModel",
        ]
        first = runner.invoke(app, args)
        assert first.exit_code == 0, first.stdout + first.stderr
        second = runner.invoke(app, args)
        output = second.stdout + second.stderr
        assert second.exit_code == 1, output
        assert "error: could not insert user" in output
        assert "UNIQUE constraint failed" in output


class TestUserCreateExtraColumns:
    """``create`` must seed a UserModel that adds its own columns.

    Before ``--set``, the insert went out with ``NULL`` in every column
    the SDK does not know about, and the database rejected it -- the CLI
    exists to avoid exactly that hand-written SQL.
    """

    _RICH: ClassVar[list[str]] = ["--model", "cli_user_model:_CLIRichUserModel"]

    def _read_row(self, url: str, email: str) -> tuple[str, int | None, str, str]:
        """Return the seeded row's own columns, read back from the DB.

        Args:
            url (str): The database URL.
            email (str): Email of the row to read.

        Returns:
            tuple[str, int | None, str, str]: ``display_name``,
            ``seat_count``, ``avatar_color`` and ``locale`` as stored.
        """
        import asyncio

        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        async def _read() -> tuple[str, int | None, str, str]:
            engine = create_async_engine(url)
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                result = await session.execute(
                    select(_CLIRichUserModel).where(
                        _CLIRichUserModel.email == email,
                    )
                )
                row = result.scalar_one()
                values = (
                    row.display_name,
                    row.seat_count,
                    row.avatar_color,
                    str(row.locale),
                )
            await engine.dispose()
            return values

        return asyncio.run(_read())

    def test_missing_required_column_is_a_named_error(self, project_db: str) -> None:
        result = runner.invoke(
            app,
            [
                "user",
                "create",
                "--email",
                "novato@example.com",
                "--password",
                "secret-pass-12",
                "--no-admin",
                *self._RICH,
            ],
        )
        output = result.stdout + result.stderr
        assert result.exit_code == 2, output
        assert "requires a value for: display_name" in output
        assert "--set <column>=<value>" in output

    def test_set_fills_the_extra_columns(self, project_db: str) -> None:
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
                "--set",
                "display_name=Ana",
                "--set",
                "seat_count=3",
                "--set",
                "locale=pt-BR",
                *self._RICH,
            ],
        )
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "Created user: ana@example.com" in result.stdout
        display_name, seat_count, avatar_color, locale = self._read_row(
            project_db, "ana@example.com"
        )
        assert display_name == "Ana"
        assert seat_count == 3
        assert avatar_color == "#7dd3fc"
        assert locale == "pt-BR"

    def test_unknown_column_lists_the_accepted_ones(self, project_db: str) -> None:
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
                "--set",
                "display_nam=Ana",
                *self._RICH,
            ],
        )
        output = result.stdout + result.stderr
        assert result.exit_code == 2, output
        assert "has no column 'display_nam'" in output
        assert "display_name" in output
        assert "hashed_password" not in output

    def test_column_owned_by_a_flag_is_refused(self, project_db: str) -> None:
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
                "--set",
                "email=other@example.com",
                "--set",
                "display_name=Ana",
                *self._RICH,
            ],
        )
        output = result.stdout + result.stderr
        assert result.exit_code == 2, output
        assert "use --email instead" in output

    def test_malformed_pair_is_rejected(self, project_db: str) -> None:
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
                "--set",
                "display_name",
                *self._RICH,
            ],
        )
        output = result.stdout + result.stderr
        assert result.exit_code == 2, output
        assert "--set expects 'column=value'" in output

    def test_value_rejected_by_the_column_type(self, project_db: str) -> None:
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
                "--set",
                "display_name=Ana",
                "--set",
                "seat_count=three",
                *self._RICH,
            ],
        )
        output = result.stdout + result.stderr
        assert result.exit_code == 2, output
        assert "--set seat_count='three'" in output

    def test_unknown_enum_value_lists_the_members(self, project_db: str) -> None:
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
                "--set",
                "display_name=Ana",
                "--set",
                "locale=fr-FR",
                *self._RICH,
            ],
        )
        output = result.stdout + result.stderr
        assert result.exit_code == 2, output
        assert "expected one of pt-BR, en-US" in output

    def test_prompts_for_missing_columns_when_interactive(
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
                "--no-admin",
                *self._RICH,
            ],
            input="Boss Person\n",
        )
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "display_name" in result.stdout
        display_name, _seat, _color, _locale = self._read_row(
            project_db, "boss@example.com"
        )
        assert display_name == "Boss Person"

    def test_blank_answer_to_a_required_column_fails(
        self,
        project_db: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A whitespace-only answer is not a value.

        Click re-prompts on an empty line, so the case that reaches the
        CLI is an answer that only looks filled.
        """
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
                "--no-admin",
                *self._RICH,
            ],
            input="   \n",
        )
        output = result.stdout + result.stderr
        assert result.exit_code == 2, output
        assert "display_name is required" in output

    def test_plain_user_model_needs_no_set(self, project_db: str) -> None:
        result = runner.invoke(
            app,
            [
                "user",
                "create",
                "--email",
                "plain@example.com",
                "--password",
                "secret-pass-12",
                "--no-admin",
                "--model",
                "cli_user_model:_CLIUserModel",
            ],
        )
        assert result.exit_code == 0, result.stdout + result.stderr


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
