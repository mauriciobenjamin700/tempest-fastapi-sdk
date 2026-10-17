"""``tempest user create --require-mfa`` and ``set-password`` with MFA (#565).

The bootstrap of the first privileged account: the row, its second factor
and its audit event land in one transaction, or nothing lands at all. And
the operator-side password change asks for the factor of an account that
has one, through ``UserAuthService.change_password_with_mfa``.
"""

from __future__ import annotations

import asyncio
import sys
import time
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyotp
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine
from typer.testing import CliRunner

from tempest_fastapi_sdk import (
    AuditRequestMixin,
    BaseAuditLogModel,
    BaseModel,
    BaseUserModel,
    MFAMixin,
    PasswordUtils,
    make_user_recovery_code_model,
    make_user_refresh_token_model,
)
from tempest_fastapi_sdk.cli.main import app
from tempest_fastapi_sdk.utils.totp import TOTPHelper

runner = CliRunner()

SECRET: str = "JBSWY3DPEHPK3PXPJBSWY3DP"
PASSWORD: str = "secret-pass-12"
NEW_PASSWORD: str = "brand-new-pass-42"
MODEL: str = "cli_mfa_user_model:_CLIMfaUserModel"
CODES: str = "cli_mfa_user_model:_CLIMfaRecoveryCode"
AUDIT: str = "cli_mfa_user_model:_CLIMfaAuditLog"
TOKENS: str = "cli_mfa_user_model:_CLIMfaRefreshToken"


class _CLIMfaUserModel(MFAMixin, BaseUserModel):
    """User table with the TOTP columns."""

    __tablename__ = "cli_mfa_users"


class _CLIPlainUserModel(BaseUserModel):
    """User table without the TOTP columns."""

    __tablename__ = "cli_mfa_plain_users"


class _CLIMfaAuditLog(AuditRequestMixin, BaseAuditLogModel):
    """Audit table with the ``event`` column."""

    __tablename__ = "cli_mfa_audit_log"


class _CLIBareAuditLog(BaseAuditLogModel):
    """Audit table without ``AuditRequestMixin``."""

    __tablename__ = "cli_mfa_bare_audit_log"


_CLIMfaRecoveryCode = make_user_recovery_code_model(
    user_table="cli_mfa_users",
    tablename="cli_mfa_recovery_codes",
    class_name="_CLIMfaRecoveryCode",
)
_CLIMfaRefreshToken = make_user_refresh_token_model(
    user_table="cli_mfa_users",
    tablename="cli_mfa_refresh_tokens",
    class_name="_CLIMfaRefreshToken",
)

_module = types.ModuleType("cli_mfa_user_model")
for _name, _value in {
    "_CLIMfaUserModel": _CLIMfaUserModel,
    "_CLIPlainUserModel": _CLIPlainUserModel,
    "_CLIMfaAuditLog": _CLIMfaAuditLog,
    "_CLIBareAuditLog": _CLIBareAuditLog,
    "_CLIMfaRecoveryCode": _CLIMfaRecoveryCode,
    "_CLIMfaRefreshToken": _CLIMfaRefreshToken,
}.items():
    setattr(_module, _name, _value)
sys.modules["cli_mfa_user_model"] = _module


@pytest.fixture
def project_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[str]:
    """Fresh on-disk SQLite, a fixed TOTP secret and the kill-switch on.

    Args:
        tmp_path (Path): Per-test directory, used as the project root.
        monkeypatch (pytest.MonkeyPatch): Environment patcher.

    Yields:
        str: The database URL.
    """
    monkeypatch.chdir(tmp_path)
    url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("AUTH_MFA_ENABLED", "true")
    monkeypatch.setattr(TOTPHelper, "generate_secret", lambda self: SECRET)

    async def _create_schema() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(BaseModel.metadata.create_all)
        await engine.dispose()

    asyncio.run(_create_schema())
    yield url


def _scalar(url: str, statement: Any) -> Any:
    """Run one scalar query against the test database.

    Args:
        url (str): The database URL.
        statement (Any): The ``select`` to run.

    Returns:
        Any: The scalar result.
    """

    async def _run() -> Any:
        engine = create_async_engine(url)
        async with engine.connect() as conn:
            value = (await conn.execute(statement)).scalar()
        await engine.dispose()
        return value

    return asyncio.run(_run())


def _count(url: str, model: type[BaseModel]) -> int:
    """Count the rows of ``model``.

    Args:
        url (str): The database URL.
        model (type[BaseModel]): The table to count.

    Returns:
        int: The row count.
    """
    return int(_scalar(url, select(func.count()).select_from(model)))


def _create(*extra: str, code_input: str) -> Any:
    """Run ``tempest user create --require-mfa`` with ``code_input`` on stdin.

    Args:
        *extra (str): Extra CLI arguments.
        code_input (str): What the operator types at the code prompt.

    Returns:
        Any: The ``CliRunner`` result.
    """
    return runner.invoke(
        app,
        [
            "user",
            "create",
            "--email",
            "staff@example.com",
            "--password",
            PASSWORD,
            "--admin",
            "--model",
            MODEL,
            "--recovery-code-model",
            CODES,
            "--require-mfa",
            *extra,
        ],
        input=code_input,
    )


def _code(offset_steps: int = 0) -> str:
    """Return the TOTP code of :data:`SECRET` ``offset_steps`` steps from now.

    Args:
        offset_steps (int): How many 30 s steps to move forward.

    Returns:
        str: The 6-digit code.
    """
    return pyotp.TOTP(SECRET).at(int(time.time()) + 30 * offset_steps)


class TestCreateRequireMfa:
    def test_valid_code_commits_the_enrolled_user(self, project_db: str) -> None:
        result = _create("--audit-model", AUDIT, code_input=f"{_code()}\n")

        assert result.exit_code == 0, result.stdout + result.stderr
        assert "otpauth://totp/" in result.stdout
        assert SECRET in result.stdout
        assert "Recovery codes" in result.stdout
        enabled = _scalar(project_db, select(_CLIMfaUserModel.totp_enabled_at))
        assert enabled is not None
        assert _count(project_db, _CLIMfaRecoveryCode) > 0

    def test_three_wrong_codes_write_nothing(self, project_db: str) -> None:
        result = _create(
            "--audit-model",
            AUDIT,
            code_input="000000\n111111\n222222\n",
        )

        assert result.exit_code == 1
        assert "nothing was written" in result.stderr
        assert _count(project_db, _CLIMfaUserModel) == 0
        assert _count(project_db, _CLIMfaRecoveryCode) == 0
        assert _count(project_db, _CLIMfaAuditLog) == 0

    def test_a_wrong_code_then_the_right_one_succeeds(self, project_db: str) -> None:
        result = _create(code_input=f"000000\n{_code()}\n")

        assert result.exit_code == 0, result.stdout + result.stderr
        assert "2 attempt(s) left" in result.stderr
        assert _count(project_db, _CLIMfaUserModel) == 1

    def test_event_is_recorded_with_the_user(self, project_db: str) -> None:
        result = _create(
            "--audit-model",
            AUDIT,
            "--event",
            "staff.bootstrapped",
            code_input=f"{_code()}\n",
        )

        assert result.exit_code == 0, result.stdout + result.stderr
        user_id = _scalar(project_db, select(_CLIMfaUserModel.id))
        assert _scalar(project_db, select(_CLIMfaAuditLog.event)) == (
            "staff.bootstrapped"
        )
        assert _scalar(project_db, select(_CLIMfaAuditLog.entity_id)) == str(user_id)
        assert _scalar(project_db, select(_CLIMfaAuditLog.actor)) == "cli"

    def test_without_audit_model_it_says_so(self, project_db: str) -> None:
        result = _create(code_input=f"{_code()}\n")

        assert result.exit_code == 0, result.stdout + result.stderr
        assert "no audit event was recorded" in result.stderr

    def test_event_without_audit_model_is_refused(self, project_db: str) -> None:
        result = _create("--event", "x", code_input="")

        assert result.exit_code == 2
        assert "--event needs --audit-model" in result.stderr

    def test_audit_model_without_event_column_is_refused(
        self,
        project_db: str,
    ) -> None:
        result = _create(
            "--audit-model",
            "cli_mfa_user_model:_CLIBareAuditLog",
            code_input="",
        )

        assert result.exit_code == 2
        assert "AuditRequestMixin" in result.stderr
        assert _count(project_db, _CLIMfaUserModel) == 0

    def test_model_without_mfa_columns_is_refused(self, project_db: str) -> None:
        result = runner.invoke(
            app,
            [
                "user",
                "create",
                "--email",
                "staff@example.com",
                "--password",
                PASSWORD,
                "--no-admin",
                "--model",
                "cli_mfa_user_model:_CLIPlainUserModel",
                "--require-mfa",
            ],
        )

        assert result.exit_code == 2
        assert "MFAMixin" in result.stderr

    def test_missing_recovery_code_model_names_the_flag(
        self,
        project_db: str,
    ) -> None:
        result = runner.invoke(
            app,
            [
                "user",
                "create",
                "--email",
                "staff@example.com",
                "--password",
                PASSWORD,
                "--no-admin",
                "--model",
                MODEL,
                "--require-mfa",
            ],
        )

        assert result.exit_code == 2
        assert "--recovery-code-model" in result.stderr


class TestCreateAuditWithoutMfa:
    def test_audit_model_alone_records_the_default_event(
        self,
        project_db: str,
    ) -> None:
        result = runner.invoke(
            app,
            [
                "user",
                "create",
                "--email",
                "ana@example.com",
                "--password",
                PASSWORD,
                "--no-admin",
                "--model",
                MODEL,
                "--audit-model",
                AUDIT,
            ],
        )

        assert result.exit_code == 0, result.stdout + result.stderr
        assert _scalar(project_db, select(_CLIMfaAuditLog.event)) == "user.created"


class TestSetPasswordWithMfa:
    def _enrolled(self) -> list[str]:
        """Bootstrap the MFA account and return its recovery codes.

        Returns:
            list[str]: The plaintext recovery codes printed by ``create``.
        """
        result = _create(code_input=f"{_code()}\n")
        assert result.exit_code == 0, result.stdout + result.stderr
        lines = result.stdout.splitlines()
        start = next(i for i, line in enumerate(lines) if "Recovery codes" in line)
        return [line.strip() for line in lines[start + 1 :] if line.startswith("  ")]

    def _set_password(self, *extra: str, code_input: str) -> Any:
        """Run ``set-password`` for the MFA account.

        Args:
            *extra (str): Extra CLI arguments.
            code_input (str): What the operator types at the code prompt.

        Returns:
            Any: The ``CliRunner`` result.
        """
        return runner.invoke(
            app,
            [
                "user",
                "set-password",
                "--email",
                "staff@example.com",
                "--password",
                NEW_PASSWORD,
                "--model",
                MODEL,
                "--refresh-token-model",
                TOKENS,
                "--recovery-code-model",
                CODES,
                *extra,
            ],
            input=code_input,
        )

    def _hash(self, url: str) -> str:
        """Read the stored hash of the MFA account.

        Args:
            url (str): The database URL.

        Returns:
            str: The stored bcrypt hash.
        """
        return str(_scalar(url, select(_CLIMfaUserModel.hashed_password)))

    def test_totp_code_changes_the_password(self, project_db: str) -> None:
        self._enrolled()

        result = self._set_password(code_input=f"{_code(1)}\n")

        assert result.exit_code == 0, result.stdout + result.stderr
        assert "has MFA active" in result.stdout
        assert PasswordUtils().verify(NEW_PASSWORD, self._hash(project_db))

    def test_recovery_code_changes_the_password(self, project_db: str) -> None:
        codes = self._enrolled()

        result = self._set_password(code_input=f"{codes[0]}\n")

        assert result.exit_code == 0, result.stdout + result.stderr
        assert PasswordUtils().verify(NEW_PASSWORD, self._hash(project_db))

    def test_no_valid_code_leaves_the_password(self, project_db: str) -> None:
        self._enrolled()
        before = self._hash(project_db)

        result = self._set_password(code_input="000000\n111111\n222222\n")

        assert result.exit_code == 1
        assert "password was not changed" in result.stderr
        assert self._hash(project_db) == before

    def test_kill_switch_off_asks_for_no_code(
        self,
        project_db: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._enrolled()
        monkeypatch.setenv("AUTH_MFA_ENABLED", "false")

        result = self._set_password(code_input="")

        assert result.exit_code == 0, result.stdout + result.stderr
        assert "has MFA active" not in result.stdout
        assert PasswordUtils().verify(NEW_PASSWORD, self._hash(project_db))
