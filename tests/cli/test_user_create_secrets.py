"""``tempest user create`` never prints the password hash (#718).

Before the fix, a database that was never migrated made the insert fail
with ``OperationalError``, and the CLI echoed the driver message, bound
parameters included::

    error: could not insert user: (sqlite3.OperationalError) no such table: users
    [parameters: ('a@b.dev', '$2b$12$Axan0K...', 0, None, ...)]

The same rendering came back on a duplicate email (``IntegrityError``).
"""

from __future__ import annotations

import asyncio
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from typer.testing import CliRunner

from tempest_fastapi_sdk import BaseModel, BaseUserModel
from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()

BCRYPT_PREFIX: str = "$2b$"
"""Every hash ``PasswordUtils`` produces starts with this marker."""


class _SecretsUserModel(BaseUserModel):
    """Concrete UserModel discoverable as ``cli_secrets_user_model``."""

    __tablename__ = "cli_secrets_users"


_module = types.ModuleType("cli_secrets_user_model")
_module._SecretsUserModel = _SecretsUserModel  # type: ignore[attr-defined]
sys.modules["cli_secrets_user_model"] = _module

CREATE_ARGS: list[str] = [
    "user",
    "create",
    "--email",
    "ana@example.com",
    "--password",
    "secret-pass-12",
    "--no-admin",
    "--model",
    "cli_secrets_user_model:_SecretsUserModel",
]
"""One ``user create`` invocation against the model above."""


@pytest.fixture
def database_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_project_imports: None,
) -> Iterator[str]:
    """Point ``DATABASE_URL`` at an empty on-disk SQLite file.

    Args:
        tmp_path (Path): Per-test directory.
        monkeypatch (pytest.MonkeyPatch): Environment patcher.
        isolated_project_imports (None): Keeps ``src.*`` from leaking.

    Yields:
        str: The database URL, with no table created.
    """
    monkeypatch.chdir(tmp_path)
    url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    yield url


def _create_schema(url: str) -> None:
    """Create every table registered on ``BaseModel.metadata``.

    Args:
        url (str): The database URL.
    """

    async def _run() -> None:
        """Run ``create_all`` and dispose of the engine."""
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(BaseModel.metadata.create_all)
        await engine.dispose()

    asyncio.run(_run())


class TestUnmigratedDatabase:
    def test_points_at_db_upgrade_without_the_hash(self, database_url: str) -> None:
        result = runner.invoke(app, CREATE_ARGS)
        output = result.stdout + result.stderr
        assert result.exit_code == 1, output
        assert "cli_secrets_users" in output
        assert "tempest db upgrade" in output
        assert BCRYPT_PREFIX not in output
        assert "Traceback" not in output


class TestRejectedInsert:
    def test_duplicate_email_hides_the_bound_parameters(
        self,
        database_url: str,
    ) -> None:
        _create_schema(database_url)
        first = runner.invoke(app, CREATE_ARGS)
        assert first.exit_code == 0, first.stdout + first.stderr
        second = runner.invoke(app, CREATE_ARGS)
        output = second.stdout + second.stderr
        assert second.exit_code == 1, output
        assert "UNIQUE constraint failed" in output
        assert "hide_parameters=True" in output
        assert BCRYPT_PREFIX not in output
