"""``tempest user set-password`` deletes the user's passkeys by default (#659).

The operator rotates a credential after a report that the account was
compromised. A passkey signs in without the password, so one registered by
whoever held the account would survive the new hash: the command treats the
rotation as account recovery, like ``mfa-reset``, and deletes every passkey
of the user in the same transaction unless ``--keep-passkeys`` is given.
"""

from __future__ import annotations

import asyncio
import sys
import types
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine
from typer.testing import CliRunner

from tempest_fastapi_sdk import (
    BaseModel,
    BaseUserModel,
    PasswordUtils,
    make_web_authn_credential_model,
)
from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()

EMAIL: str = "owner@example.com"
NEW_PASSWORD: str = "rotated-pass-42"
MODEL: str = "cli_set_password_passkeys:_CLIRotateUserModel"
PASSKEYS: str = "cli_set_password_passkeys:_CLIRotatePasskey"


class _CLIRotateUserModel(BaseUserModel):
    """User table for the rotation tests."""

    __tablename__ = "cli_rotate_users"


_CLIRotatePasskey = make_web_authn_credential_model(
    user_table="cli_rotate_users",
    tablename="cli_rotate_passkeys",
    class_name="_CLIRotatePasskey",
)

_module = types.ModuleType("cli_set_password_passkeys")
for _name, _value in {
    "_CLIRotateUserModel": _CLIRotateUserModel,
    "_CLIRotatePasskey": _CLIRotatePasskey,
}.items():
    setattr(_module, _name, _value)
sys.modules["cli_set_password_passkeys"] = _module


@pytest.fixture
def project_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_project_imports: None,
) -> Iterator[str]:
    """Fresh on-disk SQLite with one user holding two passkeys.

    Args:
        tmp_path (Path): Per-test directory, used as the project root.
        monkeypatch (pytest.MonkeyPatch): Environment patcher.
        isolated_project_imports (None): Keeps an earlier test's project
            off ``sys.path``.

    Yields:
        str: The database URL.
    """
    monkeypatch.chdir(tmp_path)
    url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    monkeypatch.setenv("DATABASE_URL", url)

    async def _seed() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(BaseModel.metadata.create_all)
            user_id = uuid.uuid4()
            await conn.execute(
                _CLIRotateUserModel.__table__.insert().values(
                    id=user_id,
                    email=EMAIL,
                    hashed_password=PasswordUtils().hash("old-pass-123456"),
                    is_active=True,
                    is_admin=False,
                ),
            )
            for _ in range(2):
                await conn.execute(
                    _CLIRotatePasskey.__table__.insert().values(
                        id=uuid.uuid4(),
                        user_id=user_id,
                        credential_id=uuid.uuid4().bytes,
                        credential_data=b"attested-credential-data",
                        sign_count=0,
                        backed_up=False,
                    ),
                )
        await engine.dispose()

    asyncio.run(_seed())
    yield url


def _passkeys(url: str) -> int:
    """Count the passkey rows left in the database.

    Args:
        url (str): The database URL.

    Returns:
        int: The row count.
    """

    async def _run() -> int:
        engine = create_async_engine(url)
        async with engine.connect() as conn:
            value = (
                await conn.execute(select(func.count()).select_from(_CLIRotatePasskey))
            ).scalar()
        await engine.dispose()
        return int(value or 0)

    return asyncio.run(_run())


def _set_password(*extra: str) -> Any:
    """Run ``tempest user set-password`` against the test models.

    Args:
        *extra (str): Extra CLI arguments.

    Returns:
        Any: The ``CliRunner`` result.
    """
    return runner.invoke(
        app,
        [
            "user",
            "set-password",
            "--email",
            EMAIL,
            "--password",
            NEW_PASSWORD,
            "--model",
            MODEL,
            "--keep-sessions",
            *extra,
        ],
    )


class TestSetPasswordPasskeys:
    """The rotation is account recovery, so the passkeys go with it."""

    def test_passkeys_are_deleted_by_default(self, project_db: str) -> None:
        result = _set_password("--webauthn-credential-model", PASSKEYS)

        assert result.exit_code == 0, result.stdout + result.stderr
        assert "Deleted 2 passkey(s)." in result.stdout
        assert _passkeys(project_db) == 0

    def test_keep_passkeys_leaves_them(self, project_db: str) -> None:
        result = _set_password("--keep-passkeys")

        assert result.exit_code == 0, result.stdout + result.stderr
        assert "--keep-passkeys given" in result.stderr
        assert _passkeys(project_db) == 2

    def test_keep_passkeys_contradicts_the_model(self, project_db: str) -> None:
        result = _set_password(
            "--keep-passkeys",
            "--webauthn-credential-model",
            PASSKEYS,
        )

        assert result.exit_code == 2
        assert "contradict" in result.stderr
        assert _passkeys(project_db) == 2

    def test_without_a_model_it_says_so(self, project_db: str) -> None:
        result = _set_password()

        assert result.exit_code == 0, result.stdout + result.stderr
        assert "so no passkey was deleted" in result.stderr
        assert _passkeys(project_db) == 2

    def test_the_conventional_model_is_found_without_the_flag(
        self,
        project_db: str,
        tmp_path: Path,
    ) -> None:
        models = tmp_path / "src" / "db"
        models.mkdir(parents=True)
        (tmp_path / "src" / "__init__.py").write_text("")
        (models / "__init__.py").write_text("")
        (models / "models.py").write_text(
            "from cli_set_password_passkeys import _CLIRotatePasskey "
            "as UserWebAuthnCredentialModel\n"
        )

        result = _set_password()

        assert result.exit_code == 0, result.stdout + result.stderr
        assert "Deleted 2 passkey(s)." in result.stdout
        assert _passkeys(project_db) == 0

    def test_a_typed_spec_that_is_not_a_credential_model_is_refused(
        self, project_db: str
    ) -> None:
        result = _set_password("--webauthn-credential-model", MODEL)

        assert result.exit_code == 2
        assert "not a BaseWebAuthnCredentialModel subclass" in result.stderr
        assert _passkeys(project_db) == 2

    def test_the_password_still_changes(self, project_db: str) -> None:
        result = _set_password("--webauthn-credential-model", PASSKEYS)

        assert result.exit_code == 0, result.stdout + result.stderr

        async def _hash() -> str:
            engine = create_async_engine(project_db)
            async with engine.connect() as conn:
                value = (
                    await conn.execute(select(_CLIRotateUserModel.hashed_password))
                ).scalar()
            await engine.dispose()
            return str(value)

        assert PasswordUtils().verify(NEW_PASSWORD, asyncio.run(_hash()))
