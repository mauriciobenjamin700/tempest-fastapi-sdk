"""Back-office account management with no e-mail link (#778).

A product without public sign-up creates accounts and replaces passwords
from an admin panel, and nobody receives a mail. Composing ``signup`` +
``activate`` and ``request_password_reset`` + ``confirm_password_reset`` to
get there stopped working in 0.342.0, when the link flows began answering
``503 INTEGRATION_DISABLED`` without a mailer in production (``ENV`` unset
included). ``admin_create_user`` and ``admin_set_password`` carry no link,
so the mail gate does not apply, while the password policy, the e-mail
conflict and the session revocation stay the ones the link flows use.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    BaseModel,
    BaseUserModel,
    EmailUtils,
    IntegrationDisabledException,
    UserAuthService,
    make_user_refresh_token_model,
    make_user_token_model,
    make_web_authn_credential_model,
)
from tempest_fastapi_sdk.exceptions import (
    ConflictException,
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

PASSWORD: str = "strong-pass-12-chars"
NEW_PASSWORD: str = "brand-new-pass-12-chars"


class _AdminMgmtUser(BaseUserModel):
    __tablename__ = "admin_mgmt_test_users"


_AdminMgmtToken = make_user_token_model(
    user_table="admin_mgmt_test_users",
    tablename="admin_mgmt_test_user_tokens",
    class_name="_AdminMgmtToken",
)
_AdminMgmtRefresh = make_user_refresh_token_model(
    user_table="admin_mgmt_test_users",
    tablename="admin_mgmt_test_refresh_tokens",
    class_name="_AdminMgmtRefresh",
)
_AdminMgmtCredential = make_web_authn_credential_model(
    user_table="admin_mgmt_test_users",
    tablename="admin_mgmt_test_credentials",
    class_name="_AdminMgmtCredential",
)


@pytest.fixture(autouse=True)
def _production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test with ``ENV`` unset, which the mail gate reads as production.

    Args:
        monkeypatch (pytest.MonkeyPatch): Removes ``ENV`` for the test.
    """
    monkeypatch.delenv("ENV", raising=False)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield a session over a fresh in-memory database.

    Yields:
        AsyncSession: The session every call of a test shares.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        yield opened
    await engine.dispose()


def _service(
    *, mailer: EmailUtils | None = None, complexity: bool = False
) -> UserAuthService:
    """Build the service the way famacha-api runs it: no SMTP, link flows gated.

    Args:
        mailer (EmailUtils | None): The mailer. ``None`` wires none, which in
            production refuses every link flow just like a disabled one.
        complexity (bool): ``AUTH_PASSWORD_REQUIRE_COMPLEXITY``.

    Returns:
        UserAuthService: The configured service.
    """
    return UserAuthService(
        user_model=_AdminMgmtUser,
        token_model=_AdminMgmtToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            _env_file=None,
            AUTH_AUTO_ACTIVATE=False,
            AUTH_PASSWORD_REQUIRE_COMPLEXITY=complexity,
        ),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        email=mailer,
        refresh_token_model=_AdminMgmtRefresh,  # type: ignore[arg-type]
        webauthn_credential_model=_AdminMgmtCredential,  # type: ignore[arg-type]
    )


def _disabled_mailer() -> EmailUtils:
    """Build the mailer ``email_kwargs()`` yields under ``EMAIL_ENABLED=false``.

    Returns:
        EmailUtils: A mailer built with ``enabled=False``.
    """
    return EmailUtils("localhost", 1025, from_addr="noreply@localhost", enabled=False)


async def _count(session: AsyncSession, model: type[BaseModel]) -> int:
    """Count the rows of ``model`` visible to ``session``.

    Args:
        session (AsyncSession): The test session.
        model (type[BaseModel]): The table to count.

    Returns:
        int: How many rows exist.
    """
    result = await session.execute(select(func.count()).select_from(model))
    return int(result.scalar_one())


class TestPremise:
    """The link flows the issue composed are refused in this configuration."""

    @pytest.mark.parametrize("mailer", [None, _disabled_mailer()])
    async def test_signup_is_refused(
        self, session: AsyncSession, mailer: EmailUtils | None
    ) -> None:
        """Without the admin methods, account creation answers 503."""
        with pytest.raises(IntegrationDisabledException):
            await _service(mailer=mailer).signup(
                session, email="a@x.com", password=PASSWORD
            )

    async def test_password_reset_is_refused(self, session: AsyncSession) -> None:
        """Without the admin methods, the password change answers 503."""
        with pytest.raises(IntegrationDisabledException):
            await _service().request_password_reset(session, email="a@x.com")


class TestAdminCreateUser:
    @pytest.mark.parametrize("mailer", [None, _disabled_mailer()])
    async def test_creates_an_active_account_without_a_mailer(
        self, session: AsyncSession, mailer: EmailUtils | None
    ) -> None:
        """The account is born active, with no token, and logs in."""
        service = _service(mailer=mailer)
        user = await service.admin_create_user(
            session, email="  New@X.com ", password=PASSWORD, name="Nova"
        )
        await session.commit()

        assert user.is_active is True
        assert user.is_admin is False
        assert user.email == "new@x.com"
        assert user.email_verified_at is None
        assert await _count(session, _AdminMgmtToken) == 0
        logged = await service.login(session, email="new@x.com", password=PASSWORD)
        assert logged.id == user.id

    async def test_is_admin_is_set_when_asked(self, session: AsyncSession) -> None:
        """``is_admin`` is a parameter, since ``fields`` refuses the column."""
        user = await _service().admin_create_user(
            session, email="boss@x.com", password=PASSWORD, is_admin=True
        )
        assert user.is_admin is True

    async def test_is_admin_is_refused_in_fields(self, session: AsyncSession) -> None:
        """``fields`` keeps signup's protected columns."""
        with pytest.raises(ValueError, match="protected"):
            await _service().admin_create_user(
                session,
                email="boss@x.com",
                password=PASSWORD,
                fields={"is_admin": True},
            )

    async def test_taken_email_raises_the_signup_conflict(
        self, session: AsyncSession
    ) -> None:
        """A taken address raises the same class ``signup`` raises."""
        service = _service()
        await service.admin_create_user(session, email="dup@x.com", password=PASSWORD)
        await session.commit()

        with pytest.raises(ConflictException) as caught:
            await service.admin_create_user(
                session, email="DUP@x.com", password=PASSWORD
            )
        assert caught.value.details == {"email": "dup@x.com"}

    async def test_password_policy_applies(self, session: AsyncSession) -> None:
        """A weak password is refused before any row is written."""
        service = _service(complexity=True)
        with pytest.raises(ValidationException):
            await service.admin_create_user(
                session, email="weak@x.com", password="alllowercase12"
            )
        assert await _count(session, _AdminMgmtUser) == 0


class TestAdminSetPassword:
    async def _active_user_with_session(
        self, service: UserAuthService, session: AsyncSession
    ) -> tuple[_AdminMgmtUser, str]:
        """Create an account, open a session and register a passkey.

        Args:
            service (UserAuthService): The configured service.
            session (AsyncSession): The test session.

        Returns:
            tuple[_AdminMgmtUser, str]: The user and its refresh token.
        """
        user = await service.admin_create_user(
            session, email="owner@x.com", password=PASSWORD
        )
        _access, refresh = await service.issue_token_pair(session, user)
        session.add(
            _AdminMgmtCredential(
                user_id=user.id,
                credential_id=b"cred-1",
                credential_data=b"data",
                sign_count=0,
            )
        )
        await session.commit()
        assert isinstance(user, _AdminMgmtUser)
        return user, refresh

    @pytest.mark.parametrize("mailer", [None, _disabled_mailer()])
    async def test_replaces_the_password_without_a_mailer(
        self, session: AsyncSession, mailer: EmailUtils | None
    ) -> None:
        """The new password logs in and the old one no longer does."""
        service = _service(mailer=mailer)
        user, _refresh = await self._active_user_with_session(service, session)

        await service.admin_set_password(
            session, user_id=user.id, new_password=NEW_PASSWORD
        )
        await session.commit()

        logged = await service.login(
            session, email="owner@x.com", password=NEW_PASSWORD
        )
        assert logged.id == user.id
        with pytest.raises(UnauthorizedException):
            await service.login(session, email="owner@x.com", password=PASSWORD)

    async def test_default_revokes_sessions_and_passkeys(
        self, session: AsyncSession
    ) -> None:
        """Someone other than the owner chose the password: old access ends."""
        service = _service()
        user, refresh = await self._active_user_with_session(service, session)

        await service.admin_set_password(
            session, user_id=user.id, new_password=NEW_PASSWORD
        )
        await session.commit()

        with pytest.raises(UnauthorizedException):
            await service.refresh_tokens(session, refresh_token=refresh)
        assert await _count(session, _AdminMgmtCredential) == 0

    async def test_opt_outs_keep_sessions_and_passkeys(
        self, session: AsyncSession
    ) -> None:
        """``revoke_sessions=False`` and ``revoke_passkeys=False`` keep both."""
        service = _service()
        user, refresh = await self._active_user_with_session(service, session)

        await service.admin_set_password(
            session,
            user_id=user.id,
            new_password=NEW_PASSWORD,
            revoke_sessions=False,
            revoke_passkeys=False,
        )
        await session.commit()

        refreshed, _access, _rotated = await service.refresh_tokens(
            session, refresh_token=refresh
        )
        assert refreshed.id == user.id
        assert await _count(session, _AdminMgmtCredential) == 1

    async def test_password_policy_applies(self, session: AsyncSession) -> None:
        """A weak password is refused and the hash is left untouched."""
        service = _service(complexity=True)
        user = await service.admin_create_user(
            session, email="owner@x.com", password="Strong-Pass-12"
        )
        await session.commit()

        with pytest.raises(ValidationException):
            await service.admin_set_password(
                session, user_id=user.id, new_password="alllowercase12"
            )
        logged = await service.login(
            session, email="owner@x.com", password="Strong-Pass-12"
        )
        assert logged.id == user.id

    async def test_unknown_user_is_not_found(self, session: AsyncSession) -> None:
        """A missing id is a ``404``, not a silent no-op."""
        with pytest.raises(NotFoundException):
            await _service().admin_set_password(
                session, user_id=uuid4(), new_password=NEW_PASSWORD
            )
