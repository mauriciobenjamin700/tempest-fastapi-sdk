"""``tempest user set-password --audit-model`` records the rotation.

The operator-side rotation revokes sessions and passkeys, like ``mfa-reset``,
but left no audit row: nothing recorded that an operator replaced the
credential or how many sessions and passkeys went with it. With
``--audit-model`` the event lands in the same transaction as the new hash.
"""

from __future__ import annotations

import asyncio
import sys
import types
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from typer.testing import CliRunner

from tempest_fastapi_sdk import (
    AuditRequestMixin,
    BaseAuditLogModel,
    BaseModel,
    BaseUserModel,
    PasswordUtils,
    make_user_refresh_token_model,
)
from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()

PASSWORD: str = "secret-pass-12"
NEW_PASSWORD: str = "brand-new-pass-42"
EMAIL: str = "staff@example.com"
MODEL: str = "cli_pwaudit_model:_PwAuditUser"
AUDIT: str = "cli_pwaudit_model:_PwAuditLog"
TOKENS: str = "cli_pwaudit_model:_PwAuditRefreshToken"


class _PwAuditUser(BaseUserModel):
    """User table for the set-password audit tests."""

    __tablename__ = "cli_pwaudit_users"


class _PwAuditLog(AuditRequestMixin, BaseAuditLogModel):
    """Audit table with the ``event`` column."""

    __tablename__ = "cli_pwaudit_log"


_PwAuditRefreshToken = make_user_refresh_token_model(
    user_table="cli_pwaudit_users",
    tablename="cli_pwaudit_refresh_tokens",
    class_name="_PwAuditRefreshToken",
)

_module = types.ModuleType("cli_pwaudit_model")
for _name, _value in {
    "_PwAuditUser": _PwAuditUser,
    "_PwAuditLog": _PwAuditLog,
    "_PwAuditRefreshToken": _PwAuditRefreshToken,
}.items():
    setattr(_module, _name, _value)
sys.modules["cli_pwaudit_model"] = _module


@pytest.fixture
def project_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_project_imports: None,
) -> Iterator[str]:
    """Fresh on-disk SQLite with one user and one live refresh token.

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
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            user = _PwAuditUser(email=EMAIL, is_active=True)
            user.set_password(PASSWORD)
            session.add(user)
            await session.flush()
            await session.execute(
                insert(_PwAuditRefreshToken).values(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    token_hash=uuid.uuid4().hex,
                    family_id=uuid.uuid4(),
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                ),
            )
            await session.commit()
        await engine.dispose()

    asyncio.run(_seed())
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


def _set_password(*extra: str) -> Any:
    """Run ``set-password`` for the seeded user.

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
            "--refresh-token-model",
            TOKENS,
            "--keep-passkeys",
            *extra,
        ],
    )


class TestSetPasswordAudit:
    def test_event_is_recorded_with_the_rotation(self, project_db: str) -> None:
        result = _set_password("--audit-model", AUDIT)

        assert result.exit_code == 0, result.stdout + result.stderr
        user_id = _scalar(project_db, select(_PwAuditUser.id))
        assert _scalar(project_db, select(_PwAuditLog.event)) == "user.password_set"
        assert _scalar(project_db, select(_PwAuditLog.entity_id)) == str(user_id)
        assert _scalar(project_db, select(_PwAuditLog.actor)) == "cli"
        changes = _scalar(project_db, select(_PwAuditLog.changes))
        assert changes["password_changed"] is True
        assert changes["sessions_revoked"] == 1
        assert changes["passkeys_revoked"] == 0

    def test_custom_event_name(self, project_db: str) -> None:
        result = _set_password("--audit-model", AUDIT, "--event", "support.rotated")

        assert result.exit_code == 0, result.stdout + result.stderr
        assert _scalar(project_db, select(_PwAuditLog.event)) == "support.rotated"

    def test_without_audit_model_nothing_is_recorded(self, project_db: str) -> None:
        result = _set_password()

        assert result.exit_code == 0, result.stdout + result.stderr
        count = _scalar(project_db, select(func.count()).select_from(_PwAuditLog))
        assert count == 0

    def test_event_without_audit_model_is_refused(self, project_db: str) -> None:
        old_hash = _scalar(project_db, select(_PwAuditUser.hashed_password))

        result = _set_password("--event", "x")

        assert result.exit_code == 2
        assert "--audit-model" in result.stderr
        assert _scalar(project_db, select(_PwAuditUser.hashed_password)) == old_hash

    def test_failed_audit_write_rolls_the_rotation_back(
        self,
        project_db: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from tempest_fastapi_sdk import BaseRepository

        async def _boom(*args: Any, **kwargs: Any) -> Any:
            """Fail the audit write after the rotation was flushed.

            Args:
                *args (Any): Ignored.
                **kwargs (Any): Ignored.

            Raises:
                RuntimeError: Always.
            """
            raise RuntimeError("audit table is down")

        monkeypatch.setattr(BaseRepository, "record_event", _boom)

        result = _set_password("--audit-model", AUDIT)

        assert isinstance(result.exception, RuntimeError)
        stored = _scalar(project_db, select(_PwAuditUser.hashed_password))
        assert PasswordUtils().verify(PASSWORD, stored)
        live = _scalar(
            project_db,
            select(func.count())
            .select_from(_PwAuditRefreshToken)
            .where(_PwAuditRefreshToken.revoked_at.is_(None)),
        )
        assert live == 1
