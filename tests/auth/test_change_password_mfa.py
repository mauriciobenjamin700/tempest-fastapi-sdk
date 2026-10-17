"""``UserAuthService.change_password_with_mfa`` (#564).

The public path for the account whose reset e-mail cannot be reached and
whose owner still holds the authenticator app. It wraps the private
helpers a consumer used to subclass by hand — ``_verify_mfa_code``,
``_enforce_password_policy``, ``revoke_user_sessions`` — with the same
refusals ``mfa_verify`` already answers with, and without the side effects
of a login (no token pair, no ``last_login_at``).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import pyotp
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    AttemptThrottle,
    BaseModel,
    BaseUserModel,
    InMemoryThrottleBackend,
    MFAMixin,
    PasswordUtils,
    UserAuthService,
    make_user_recovery_code_model,
    make_user_refresh_token_model,
    make_user_token_model,
)
from tempest_fastapi_sdk.auth import MFANotEnrolledException, MfaThrottledException
from tempest_fastapi_sdk.exceptions import (
    UnauthorizedException,
    ValidationException,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

PASSWORD: str = "strong-pass-12-chars"
NEW_PASSWORD: str = "brand-new-pass-12-chars"


class _ChangeMfaUser(MFAMixin, BaseUserModel):
    __tablename__ = "change_mfa_test_users"


_ChangeMfaUserToken = make_user_token_model(
    user_table="change_mfa_test_users",
    tablename="change_mfa_test_user_tokens",
    class_name="_ChangeMfaUserToken",
)
_ChangeMfaRecoveryCode = make_user_recovery_code_model(
    user_table="change_mfa_test_users",
    tablename="change_mfa_test_recovery_codes",
    class_name="_ChangeMfaRecoveryCode",
)
_ChangeMfaRefreshToken = make_user_refresh_token_model(
    user_table="change_mfa_test_users",
    tablename="change_mfa_test_refresh_tokens",
    class_name="_ChangeMfaRefreshToken",
)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _service() -> UserAuthService:
    """Build an auth service with MFA and DB-backed refresh tokens wired.

    Returns:
        UserAuthService: The configured service.
    """
    return UserAuthService(
        user_model=_ChangeMfaUser,
        token_model=_ChangeMfaUserToken,
        auth_settings=AuthSettings(
            _env_file=None,
            AUTH_AUTO_ACTIVATE=True,
            AUTH_MFA_ENABLED=True,
            AUTH_MFA_ISSUER="Tempest Test",
        ),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        email=None,
        refresh_token_model=_ChangeMfaRefreshToken,
    )


def _next_code(secret: str) -> str:
    """Return the code of the step after the current one.

    Args:
        secret (str): The enrolled Base32 secret.

    Returns:
        str: The 6-digit code for ``now + 30s``.
    """
    return pyotp.TOTP(secret).at(int(time.time()) + 30)


async def _enrolled_user(
    service: UserAuthService,
    session: AsyncSession,
    email: str = "mfa-change@example.com",
) -> tuple[BaseUserModel, list[str]]:
    """Create an account with MFA active and return its recovery codes.

    Args:
        service (UserAuthService): The configured service.
        session (AsyncSession): The session to write through.
        email (str): The account address.

    Returns:
        tuple[BaseUserModel, list[str]]: The user and its plaintext
        recovery codes.
    """
    user, _ = await service.signup(session, email=email, password=PASSWORD)
    secret, _, codes = await service.mfa_enroll(
        session,
        user=user,
        recovery_code_model=_ChangeMfaRecoveryCode,
    )
    await service.mfa_confirm(session, user=user, code=pyotp.TOTP(secret).now())
    await session.commit()
    return user, codes


def _passwords() -> PasswordUtils:
    """Return a hasher to verify the stored hash.

    Returns:
        PasswordUtils: The password helper the SDK hashes with.
    """
    return PasswordUtils()


class TestChangePasswordWithMfa:
    async def test_totp_code_rotates_the_password(self, session: AsyncSession) -> None:
        service = _service()
        user, _ = await _enrolled_user(service, session)

        returned = await service.change_password_with_mfa(
            session,
            user=user,
            code=_next_code(user.totp_secret or ""),
            new_password=NEW_PASSWORD,
            recovery_code_model=_ChangeMfaRecoveryCode,
        )
        await session.commit()

        assert returned.id == user.id
        assert _passwords().verify(NEW_PASSWORD, returned.hashed_password)
        assert not _passwords().verify(PASSWORD, returned.hashed_password)

    async def test_wrong_code_is_refused_and_password_is_untouched(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user, _ = await _enrolled_user(service, session)
        before = user.hashed_password

        with pytest.raises(UnauthorizedException, match="invalid MFA code"):
            await service.change_password_with_mfa(
                session,
                user=user,
                code="999999",
                new_password=NEW_PASSWORD,
                recovery_code_model=_ChangeMfaRecoveryCode,
            )

        await session.refresh(user)
        assert user.hashed_password == before

    async def test_recovery_code_is_consumed(self, session: AsyncSession) -> None:
        service = _service()
        user, codes = await _enrolled_user(service, session)

        await service.change_password_with_mfa(
            session,
            user=user,
            code=codes[0],
            new_password=NEW_PASSWORD,
            recovery_code_model=_ChangeMfaRecoveryCode,
        )
        await session.commit()

        assert _passwords().verify(NEW_PASSWORD, user.hashed_password)
        with pytest.raises(UnauthorizedException):
            await service.change_password_with_mfa(
                session,
                user=user,
                code=codes[0],
                new_password=PASSWORD,
                recovery_code_model=_ChangeMfaRecoveryCode,
            )

    async def test_account_without_mfa_is_refused(self, session: AsyncSession) -> None:
        service = _service()
        user, _ = await service.signup(
            session, email="plain@example.com", password=PASSWORD
        )
        await session.commit()
        before = user.hashed_password

        with pytest.raises(MFANotEnrolledException) as refused:
            await service.change_password_with_mfa(
                session,
                user=user,
                code=_next_code("JBSWY3DPEHPK3PXP"),
                new_password=NEW_PASSWORD,
                recovery_code_model=_ChangeMfaRecoveryCode,
            )

        assert refused.value.code == "MFA_NOT_ENROLLED"
        await session.refresh(user)
        assert user.hashed_password == before

    async def test_policy_rejection_keeps_the_factor_unspent(
        self,
        session: AsyncSession,
    ) -> None:
        """A weak password is refused before the recovery code is spent."""
        service = _service()
        user, codes = await _enrolled_user(service, session)

        with pytest.raises(ValidationException):
            await service.change_password_with_mfa(
                session,
                user=user,
                code=codes[0],
                new_password="short",
                recovery_code_model=_ChangeMfaRecoveryCode,
            )

        await service.change_password_with_mfa(
            session,
            user=user,
            code=codes[0],
            new_password=NEW_PASSWORD,
            recovery_code_model=_ChangeMfaRecoveryCode,
        )
        await session.commit()
        assert _passwords().verify(NEW_PASSWORD, user.hashed_password)

    async def test_sessions_are_revoked_by_default(self, session: AsyncSession) -> None:
        service = _service()
        user, _ = await _enrolled_user(service, session)
        _, refresh = await service.issue_token_pair(session, user)
        await session.commit()

        await service.change_password_with_mfa(
            session,
            user=user,
            code=_next_code(user.totp_secret or ""),
            new_password=NEW_PASSWORD,
            recovery_code_model=_ChangeMfaRecoveryCode,
        )
        await session.commit()

        with pytest.raises(UnauthorizedException):
            await service.refresh_tokens(session, refresh_token=refresh)

    async def test_revoke_sessions_false_keeps_the_session(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user, _ = await _enrolled_user(service, session)
        _, refresh = await service.issue_token_pair(session, user)
        await session.commit()

        await service.change_password_with_mfa(
            session,
            user=user,
            code=_next_code(user.totp_secret or ""),
            new_password=NEW_PASSWORD,
            recovery_code_model=_ChangeMfaRecoveryCode,
            revoke_sessions=False,
        )
        await session.commit()

        _, access, _rotated = await service.refresh_tokens(
            session, refresh_token=refresh
        )
        assert access

    async def test_no_token_is_minted_and_last_login_is_untouched(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user, _ = await _enrolled_user(service, session)
        await service.issue_token_pair(session, user)
        await session.commit()
        before = await session.scalar(
            select(func.count()).select_from(_ChangeMfaRefreshToken)
        )

        await service.change_password_with_mfa(
            session,
            user=user,
            code=_next_code(user.totp_secret or ""),
            new_password=NEW_PASSWORD,
            recovery_code_model=_ChangeMfaRecoveryCode,
            revoke_sessions=False,
        )
        await session.commit()

        after = await session.scalar(
            select(func.count()).select_from(_ChangeMfaRefreshToken)
        )
        assert after == before
        await session.refresh(user)
        assert user.last_login_at is None

    async def test_throttle_blocks_after_the_budget(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user, _ = await _enrolled_user(service, session)
        throttle = AttemptThrottle(
            InMemoryThrottleBackend(),
            max_attempts=2,
            window_seconds=60,
        )

        for _ in range(2):
            with pytest.raises(UnauthorizedException):
                await service.change_password_with_mfa(
                    session,
                    user=user,
                    code="999999",
                    new_password=NEW_PASSWORD,
                    recovery_code_model=_ChangeMfaRecoveryCode,
                    throttle=throttle,
                )
        with pytest.raises(MfaThrottledException):
            await service.change_password_with_mfa(
                session,
                user=user,
                code=_next_code(user.totp_secret or ""),
                new_password=NEW_PASSWORD,
                recovery_code_model=_ChangeMfaRecoveryCode,
                throttle=throttle,
            )

    async def test_without_recovery_model_only_totp_is_accepted(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user, codes = await _enrolled_user(service, session)

        with pytest.raises(UnauthorizedException):
            await service.change_password_with_mfa(
                session,
                user=user,
                code=codes[0],
                new_password=NEW_PASSWORD,
            )

        await service.change_password_with_mfa(
            session,
            user=user,
            code=_next_code(user.totp_secret or ""),
            new_password=NEW_PASSWORD,
        )
        await session.commit()
        assert _passwords().verify(NEW_PASSWORD, user.hashed_password)

    async def test_kill_switch_off_reads_as_not_enrolled(
        self,
        session: AsyncSession,
    ) -> None:
        """With ``AUTH_MFA_ENABLED`` off the factor is not checked at all."""
        service = _service()
        user, _ = await _enrolled_user(service, session)
        service.auth_settings = AuthSettings(
            _env_file=None,
            AUTH_AUTO_ACTIVATE=True,
            AUTH_MFA_ENABLED=False,
        )

        with pytest.raises(MFANotEnrolledException):
            await service.change_password_with_mfa(
                session,
                user=user,
                code=_next_code(user.totp_secret or ""),
                new_password=NEW_PASSWORD,
                recovery_code_model=_ChangeMfaRecoveryCode,
            )

    async def test_budget_is_shared_with_the_login_step(
        self,
        session: AsyncSession,
    ) -> None:
        """Wrong codes here spend the budget ``mfa_verify`` reads too."""
        service = _service()
        user, _ = await _enrolled_user(service, session)
        throttle = AttemptThrottle(
            InMemoryThrottleBackend(),
            max_attempts=1,
            window_seconds=60,
        )
        with pytest.raises(UnauthorizedException):
            await service.change_password_with_mfa(
                session,
                user=user,
                code="999999",
                new_password=NEW_PASSWORD,
                throttle=throttle,
            )

        with pytest.raises(MfaThrottledException):
            await service.mfa_verify(
                session,
                mfa_token=service.issue_mfa_token(user),
                code=_next_code(user.totp_secret or ""),
                recovery_code_model=_ChangeMfaRecoveryCode,
                throttle=throttle,
            )
