"""Only the newest link of each flow opens the account.

The property this module pins is the one that makes the victim's own
correct reaction effective. An attacker requests a password reset for
someone else; that person receives a recovery email they never asked
for, gets suspicious, and resets the password themselves. Without
superseding, that does **not** close the window — the attacker's token
stays valid until ``AUTH_PASSWORD_RESET_TTL_SECONDS``, so a token leaked
through any side channel still resets the password after the incident
looked handled.

Superseding lives in ``_issue_token``, so it covers every purpose rather
than password reset alone: a pending email change to an address the
attacker controls has exactly the same shape.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

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
    UserAuthService,
    make_user_token_model,
)
from tempest_fastapi_sdk.db.user_token_model import UserTokenPurpose
from tempest_fastapi_sdk.exceptions import InvalidTokenException
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


class _SupersedeUser(BaseUserModel):
    __tablename__ = "supersede_users"


_SupersedeToken = make_user_token_model(
    user_table="supersede_users",
    tablename="supersede_user_tokens",
    class_name="_SupersedeToken",
)

_PASSWORD = "Str0ng-pass-12!"
_NEW_PASSWORD = "An0ther-pass-12!"


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _service(**overrides: Any) -> UserAuthService:
    """Build a service with activation off and the link echoed back.

    Args:
        **overrides (Any): ``AuthSettings`` field overrides.

    Returns:
        UserAuthService: The configured service.
    """
    overrides.setdefault("AUTH_AUTO_ACTIVATE", True)
    overrides.setdefault("AUTH_RETURN_TOKEN_IN_RESPONSE", True)
    auth = AuthSettings(_env_file=None, **overrides)
    return UserAuthService(
        user_model=_SupersedeUser,
        token_model=_SupersedeToken,  # type: ignore[arg-type]
        auth_settings=auth,
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        email=None,
    )


async def _account(service: UserAuthService, session: AsyncSession, email: str) -> Any:
    """Create an active account.

    Args:
        service (UserAuthService): The service under test.
        session (AsyncSession): The active session.
        email (str): Address for the new account.

    Returns:
        Any: The persisted user.
    """
    user, _ = await service.signup(session, email=email, password=_PASSWORD)
    await session.commit()
    return user


class TestPasswordResetSupersedes:
    """The scenario the issue describes, end to end."""

    async def test_the_older_link_stops_working(self, session: AsyncSession) -> None:
        service = _service()
        await _account(service, session, "ana@example.com")

        attacker_request = await service.request_password_reset(
            session, email="ana@example.com"
        )
        await session.commit()
        victim_request = await service.request_password_reset(
            session, email="ana@example.com"
        )
        await session.commit()
        assert attacker_request is not None
        assert victim_request is not None

        await service.confirm_password_reset(
            session, token=victim_request.token, new_password=_NEW_PASSWORD
        )
        await session.commit()

        with pytest.raises(InvalidTokenException):
            await service.confirm_password_reset(
                session,
                token=attacker_request.token,
                new_password="Attacker-pass-12!",
            )

    async def test_the_newest_link_still_works(self, session: AsyncSession) -> None:
        """Superseding must not break the ordinary "resend the link" case."""
        service = _service()
        await _account(service, session, "ana@example.com")

        await service.request_password_reset(session, email="ana@example.com")
        await session.commit()
        second = await service.request_password_reset(session, email="ana@example.com")
        await session.commit()
        assert second is not None

        user = await service.confirm_password_reset(
            session, token=second.token, new_password=_NEW_PASSWORD
        )
        await session.commit()
        assert user.check_password(_NEW_PASSWORD)

    async def test_rows_are_kept_for_audit_not_deleted(
        self, session: AsyncSession
    ) -> None:
        """Marking ``used_at`` rather than deleting keeps the trail."""
        service = _service()
        await _account(service, session, "ana@example.com")
        for _ in range(3):
            await service.request_password_reset(session, email="ana@example.com")
            await session.commit()

        total = await session.scalar(select(func.count()).select_from(_SupersedeToken))
        spent = await session.scalar(
            select(func.count())
            .select_from(_SupersedeToken)
            .where(_SupersedeToken.used_at.is_not(None))
        )
        assert total == 3
        assert spent == 2


class TestTheBurnIsScoped:
    """Superseding must not reach past the flow or the user it belongs to."""

    async def test_another_purpose_survives(self, session: AsyncSession) -> None:
        """Asking for a password reset does not kill a pending email change."""
        service = _service()
        user = await _account(service, session, "ana@example.com")

        change = await service.request_email_change(
            session,
            user=user,
            new_email="nova@example.com",
            current_password=_PASSWORD,
        )
        await session.commit()
        await service.request_password_reset(session, email="ana@example.com")
        await session.commit()
        assert change is not None

        confirmed = await service.confirm_email_change(session, token=change.token)
        await session.commit()
        assert confirmed.email == "nova@example.com"

    async def test_another_users_tokens_survive(self, session: AsyncSession) -> None:
        service = _service()
        await _account(service, session, "ana@example.com")
        await _account(service, session, "bob@example.com")

        ana = await service.request_password_reset(session, email="ana@example.com")
        await session.commit()
        await service.request_password_reset(session, email="bob@example.com")
        await session.commit()
        assert ana is not None

        user = await service.confirm_password_reset(
            session, token=ana.token, new_password=_NEW_PASSWORD
        )
        await session.commit()
        assert user.email == "ana@example.com"


class TestEveryPurposeIsCovered:
    """The fix lives in ``_issue_token``, so it is not reset-only."""

    async def test_email_change_supersedes(self, session: AsyncSession) -> None:
        service = _service()
        user = await _account(service, session, "ana@example.com")

        first = await service.request_email_change(
            session, user=user, new_email="um@example.com", current_password=_PASSWORD
        )
        await session.commit()
        second = await service.request_email_change(
            session, user=user, new_email="dois@example.com", current_password=_PASSWORD
        )
        await session.commit()
        assert first is not None
        assert second is not None

        with pytest.raises(InvalidTokenException):
            await service.confirm_email_change(session, token=first.token)

    async def test_activation_supersedes(self, session: AsyncSession) -> None:
        """Re-issuing an activation link retires the one already mailed.

        There is no public "resend activation" method, so the resend is
        driven through ``_issue_token`` — the same call every public
        flow funnels into, which is where superseding lives.
        """
        service = _service(AUTH_AUTO_ACTIVATE=False)
        user, mailed = await service.signup(
            session, email="ana@example.com", password=_PASSWORD
        )
        await session.commit()
        assert mailed is not None

        resent, _url, _expires = await service._issue_token(
            session,
            user_id=user.id,
            purpose=UserTokenPurpose.ACTIVATION,
            ttl_seconds=600,
            url_template="http://localhost/activate?token={token}",
        )
        await session.commit()

        with pytest.raises(InvalidTokenException):
            await service.activate(session, token=mailed.token)
        activated = await service.activate(session, token=resent)
        await session.commit()
        assert activated.is_active is True


class TestTheEscapeHatch:
    """``AUTH_SINGLE_ACTIVE_TOKEN=False`` restores the pre-v0.274.0 behaviour."""

    async def test_both_links_stay_valid(self, session: AsyncSession) -> None:
        service = _service(AUTH_SINGLE_ACTIVE_TOKEN=False)
        await _account(service, session, "ana@example.com")

        first = await service.request_password_reset(session, email="ana@example.com")
        await session.commit()
        second = await service.request_password_reset(session, email="ana@example.com")
        await session.commit()
        assert first is not None
        assert second is not None

        await service.confirm_password_reset(
            session, token=second.token, new_password=_NEW_PASSWORD
        )
        await session.commit()
        user = await service.confirm_password_reset(
            session, token=first.token, new_password="Third-pass-12!"
        )
        await session.commit()
        assert user.check_password("Third-pass-12!")
