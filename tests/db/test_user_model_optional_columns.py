"""``BaseUserModel`` only promises the columns it maps (#835).

Up to 0.372.0 the base declared ``totp_secret``, ``totp_enabled_at``,
``totp_last_step`` and ``name`` under ``TYPE_CHECKING``. The runtime columns
come from :class:`~tempest_fastapi_sdk.MFAMixin` and
:class:`~tempest_fastapi_sdk.NameMixin`, so ``mypy --strict`` accepted
``user.name`` on a model without ``NameMixin`` and the line raised
``AttributeError``. These tests pin both halves: the checker now refuses the
read, and the SDK's own readers of those columns work on a model that has
none of them.
"""

from __future__ import annotations

import textwrap
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from mypy import api as mypy_api
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tempest_fastapi_sdk import (
    AuthSettings,
    BaseModel,
    BaseUserModel,
    JWTSettings,
    MFAMixin,
    NameMixin,
    UserAuthService,
    ValidationException,
    make_user_recovery_code_model,
    make_user_token_model,
)
from tempest_fastapi_sdk.admin import UserModelAuthBackend


class PlainOptionalColumnsUser(BaseUserModel):
    """A user model that adopts neither ``MFAMixin`` nor ``NameMixin``."""

    __tablename__ = "optional_columns_plain_users"


PlainOptionalColumnsUserToken = make_user_token_model(
    user_table="optional_columns_plain_users",
    tablename="optional_columns_plain_user_tokens",
    class_name="PlainOptionalColumnsUserToken",
)

PlainOptionalColumnsRecoveryCode = make_user_recovery_code_model(
    user_table="optional_columns_plain_users",
    tablename="optional_columns_plain_recovery_codes",
    class_name="PlainOptionalColumnsRecoveryCode",
)

PASSWORD: str = "strong-pass-12-chars"


def _plain_user() -> PlainOptionalColumnsUser:
    """Build an unsaved user without the optional columns.

    Returns:
        PlainOptionalColumnsUser: The user.
    """
    return PlainOptionalColumnsUser(email="plain@example.com", hashed_password="x")


def _service() -> UserAuthService:
    """Build an auth service over the plain model with MFA switched on.

    Returns:
        UserAuthService: The service.
    """
    return UserAuthService(
        user_model=PlainOptionalColumnsUser,
        token_model=PlainOptionalColumnsUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(AUTH_MFA_ENABLED=True),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
    )


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield a session over an in-memory SQLite with the plain tables.

    Yields:
        AsyncSession: The session.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        PlainOptionalColumnsUser.__table__,
        PlainOptionalColumnsUserToken.__table__,
        PlainOptionalColumnsRecoveryCode.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all, tables=tables)
    async with AsyncSession(engine, expire_on_commit=False) as opened:
        yield opened
    await engine.dispose()


async def _stored_plain_user(session: AsyncSession) -> PlainOptionalColumnsUser:
    """Persist an active plain user with a known password.

    Args:
        session (AsyncSession): The session to flush into.

    Returns:
        PlainOptionalColumnsUser: The flushed user.
    """
    user = PlainOptionalColumnsUser(email="stored@example.com", is_active=True)
    user.set_password(PASSWORD, rounds=4)
    session.add(user)
    await session.flush()
    return user


async def _recovery_code_count(session: AsyncSession) -> int:
    """Count the recovery-code rows.

    Args:
        session (AsyncSession): The session to read through.

    Returns:
        int: How many rows the table holds.
    """
    statement = select(func.count()).select_from(PlainOptionalColumnsRecoveryCode)
    return int((await session.execute(statement)).scalar_one())


class TestServiceMFAWithoutMixin:
    """The MFA writes refuse a model that has no column to write."""

    @pytest.mark.asyncio
    async def test_enroll_raises_before_any_write(self, session: AsyncSession) -> None:
        user = await _stored_plain_user(session)
        with pytest.raises(TypeError, match="compose MFAMixin"):
            await _service().mfa_enroll(
                session,
                user=user,
                recovery_code_model=PlainOptionalColumnsRecoveryCode,
            )
        assert await _recovery_code_count(session) == 0

    @pytest.mark.asyncio
    async def test_confirm_reports_nothing_staged(self, session: AsyncSession) -> None:
        user = await _stored_plain_user(session)
        with pytest.raises(ValidationException, match="MFA not initialized"):
            await _service().mfa_confirm(session, user=user, code="123456")

    @pytest.mark.asyncio
    async def test_disable_reports_not_active(self, session: AsyncSession) -> None:
        user = await _stored_plain_user(session)
        with pytest.raises(ValidationException, match="MFA not active"):
            await _service().mfa_disable(
                session,
                user=user,
                password=PASSWORD,
                code="123456",
                recovery_code_model=PlainOptionalColumnsRecoveryCode,
            )

    @pytest.mark.asyncio
    async def test_reset_reports_not_active(self, session: AsyncSession) -> None:
        user = await _stored_plain_user(session)
        with pytest.raises(ValidationException, match="MFA not active"):
            await _service().mfa_reset(
                session,
                user=user,
                recovery_code_model=PlainOptionalColumnsRecoveryCode,
            )


class TestRuntime:
    """The SDK reads the optional columns only where the mixin put them."""

    def test_plain_model_has_none_of_the_optional_columns(self) -> None:
        user = _plain_user()
        for column in ("name", "totp_secret", "totp_enabled_at", "totp_last_step"):
            assert not hasattr(user, column)
        assert not isinstance(user, MFAMixin)
        assert not isinstance(user, NameMixin)

    def test_service_treats_plain_user_as_unenrolled(self) -> None:
        assert _service().is_mfa_enrolled(_plain_user()) is False

    @pytest.mark.asyncio
    async def test_service_refuses_a_totp_code_for_plain_user(self) -> None:
        session = MagicMock(spec=AsyncSession)
        session.execute = AsyncMock()
        claimed = await _service()._claim_totp_step(session, _plain_user(), "123456")
        assert claimed is False
        session.execute.assert_not_awaited()

    def test_admin_backend_reads_plain_user(self) -> None:
        backend = UserModelAuthBackend(PlainOptionalColumnsUser)
        user = _plain_user()
        assert backend.credential_fingerprint(user) == "x\x00"
        assert backend.mfa_enabled(user) is False
        assert backend.verify_mfa(user, "123456") is False

    @pytest.mark.asyncio
    async def test_admin_backend_refuses_a_claim_for_plain_user(self) -> None:
        backend = UserModelAuthBackend(PlainOptionalColumnsUser)
        session = MagicMock(spec=AsyncSession)
        session.execute = AsyncMock()
        assert await backend.claim_mfa_step(session, _plain_user(), "123456") is False
        session.execute.assert_not_awaited()


CONSUMER_HEADER: str = textwrap.dedent(
    """
    from datetime import datetime

    from tempest_fastapi_sdk import BaseUserModel, MFAMixin, NameMixin


    class UserModel(BaseUserModel):
        __tablename__ = "consumer_plain_users"


    class NamedUserModel(MFAMixin, NameMixin, BaseUserModel):
        __tablename__ = "consumer_named_users"
    """
)
"""The two models a consumer declares: one plain, one with both mixins."""

CONSUMER_MODULES: dict[str, str] = {
    "plain_name": """

        def label(user: UserModel) -> str:
            return user.name
        """,
    "plain_totp": """

        def secret(user: BaseUserModel) -> str | None:
            return user.totp_secret


        def enabled(user: BaseUserModel) -> datetime | None:
            return user.totp_enabled_at


        def step(user: BaseUserModel) -> int | None:
            return user.totp_last_step
        """,
    "narrowed": """

        def label(user: NamedUserModel) -> str:
            return user.name


        def display(user: BaseUserModel) -> str:
            if isinstance(user, NameMixin):
                return user.name
            return user.email


        def enabled(user: BaseUserModel) -> datetime | None:
            if isinstance(user, MFAMixin):
                return user.totp_enabled_at
            return None
        """,
}
"""What a consumer writes, one module per case, checked in a single run."""


@pytest.fixture(scope="module")
def strict_errors(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[str]]:
    """Run ``mypy --strict`` once over every consumer module.

    One run instead of one per test: each cold run re-checks the SDK, and
    that is the slow part.

    Args:
        tmp_path_factory (pytest.TempPathFactory): Source of the scratch
            directory for the modules and the cache.

    Returns:
        dict[str, list[str]]: The ``error:`` lines, keyed by module name.
    """
    root = tmp_path_factory.mktemp("consumer")
    paths: list[str] = []
    for name, body in CONSUMER_MODULES.items():
        module = root / f"{name}.py"
        module.write_text(CONSUMER_HEADER + textwrap.dedent(body), encoding="utf-8")
        paths.append(str(module))
    stdout, _stderr, _status = mypy_api.run(
        [
            "--strict",
            "--no-error-summary",
            "--no-color-output",
            "--cache-dir",
            str(root / ".mypy_cache"),
            *paths,
        ]
    )
    errors: dict[str, list[str]] = {name: [] for name in CONSUMER_MODULES}
    for line in stdout.splitlines():
        if ": error:" not in line:
            continue
        errors[Path(line.split(":", 1)[0]).stem].append(line)
    return errors


def _attr_defined(errors: list[str]) -> list[str]:
    """Keep the ``[attr-defined]`` findings.

    Under ``--strict`` the unresolved attribute is ``Any``, so each read also
    reports ``no-any-return``; that one follows from the first.

    Args:
        errors (list[str]): The ``error:`` lines of one module.

    Returns:
        list[str]: The lines whose code is ``attr-defined``.
    """
    return [line for line in errors if line.endswith("[attr-defined]")]


class TestTyping:
    """The checker promises a column only where the runtime maps it."""

    def test_name_on_plain_model_is_a_type_error(
        self, strict_errors: dict[str, list[str]]
    ) -> None:
        errors = _attr_defined(strict_errors["plain_name"])
        assert len(errors) == 1
        assert '"UserModel" has no attribute "name"  [attr-defined]' in errors[0]

    def test_totp_columns_on_plain_model_are_type_errors(
        self, strict_errors: dict[str, list[str]]
    ) -> None:
        errors = _attr_defined(strict_errors["plain_totp"])
        assert len(errors) == 3
        for column in ("totp_secret", "totp_enabled_at", "totp_last_step"):
            assert any(f'has no attribute "{column}"' in line for line in errors)

    def test_mixins_and_isinstance_type_check(
        self, strict_errors: dict[str, list[str]]
    ) -> None:
        assert strict_errors["narrowed"] == []
