"""``email_verified_at``: the user row records how its address was proven.

Linking a social identity by e-mail (``AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL``)
used to trust ``is_active`` under a mail-proving configuration. But an
account activated by an operator (admin site, ``tempest user activate``)
is active without its registrant ever reading the mailbox, so a stranger
who pre-registered the address could absorb its owner's first social
login. The column is stamped only where the SDK saw the proof, and the
link requires it.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    SIGNUP_PROTECTED_FIELDS,
    BaseModel,
    BaseUserModel,
    NameMixin,
    OAuthUser,
    UserAuthService,
    make_user_oauth_account_model,
    make_user_token_model,
)
from tempest_fastapi_sdk.auth import AuthUserSchema
from tempest_fastapi_sdk.exceptions import OAuthEmailTakenException
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings
from tempest_fastapi_sdk.testing.fakes import FakeEmailUtils
from tempest_fastapi_sdk.utils.datetime import utcnow
from tempest_fastapi_sdk.utils.email import EmailUtils


class _EvUser(NameMixin, BaseUserModel):
    __tablename__ = "ev_test_users"


_EvUserToken = make_user_token_model(
    user_table="ev_test_users",
    tablename="ev_test_user_tokens",
    class_name="_EvUserToken",
)
_EvAccount = make_user_oauth_account_model(
    user_table="ev_test_users",
    tablename="ev_test_accounts",
    class_name="_EvAccount",
)

_PASSWORD = "Str0ng-pass-12!"
_EMAIL = "ana@example.com"


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _service(*, email: EmailUtils | None = None, **overrides: Any) -> UserAuthService:
    """Build a service that links social identities by verified e-mail.

    Args:
        email (EmailUtils | None): The mailer; ``None`` wires none.
        **overrides (Any): ``AuthSettings`` field overrides.
            ``AUTH_AUTO_ACTIVATE`` defaults to ``False`` here.

    Returns:
        UserAuthService: The configured service.
    """
    overrides.setdefault("AUTH_AUTO_ACTIVATE", False)
    overrides.setdefault("AUTH_OAUTH_ENABLED", True)
    overrides.setdefault("AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL", True)
    return UserAuthService(
        user_model=_EvUser,
        token_model=_EvUserToken,
        auth_settings=AuthSettings(_env_file=None, **overrides),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        email=email,
        oauth_account_model=_EvAccount,
    )


def _profile(**overrides: Any) -> OAuthUser:
    """Build a verified Google identity for :data:`_EMAIL`.

    Args:
        **overrides (Any): Field overrides.

    Returns:
        OAuthUser: The identity a provider would return.
    """
    fields: dict[str, Any] = {
        "provider": "google",
        "subject": "sub-1",
        "email": _EMAIL,
        "email_verified": True,
        "name": "Ana Souza",
    }
    fields.update(overrides)
    return OAuthUser(**fields)


def _mailed_token(mail: FakeEmailUtils) -> str:
    """Pull the token out of the last message the fake accepted.

    Args:
        mail (FakeEmailUtils): The mailer the service was built with.

    Returns:
        str: The plaintext token of the link in the message body.
    """
    found = re.search(r"token=([A-Za-z0-9_\-]+)", mail.outbox[-1].body)
    assert found is not None
    return found.group(1)


async def _account_links(session: AsyncSession) -> int:
    """Count the stored social links.

    Args:
        session (AsyncSession): The test session.

    Returns:
        int: How many link rows exist.
    """
    return len((await session.execute(select(_EvAccount))).scalars().all())


class TestTheColumn:
    """The column lives on the base model and starts empty."""

    def test_is_a_nullable_column_of_the_base_model(self) -> None:
        column = _EvUser.__table__.c.email_verified_at
        assert column.nullable is True
        assert "email_verified_at" in BaseUserModel.__annotations__

    async def test_a_new_row_has_no_proof(self, session: AsyncSession) -> None:
        user = _EvUser(email=_EMAIL, hashed_password="x", name="Ana")
        session.add(user)
        await session.commit()
        assert user.email_verified_at is None

    def test_signup_fields_cannot_set_it(self) -> None:
        """A sign-up form must not declare its own address proven."""
        assert "email_verified_at" in SIGNUP_PROTECTED_FIELDS
        with pytest.raises(ValueError, match="protected"):
            _service(email=FakeEmailUtils())._check_signup_fields(
                {"email_verified_at": utcnow()}
            )

    def test_the_me_schema_exposes_it(self) -> None:
        assert "email_verified_at" in AuthUserSchema.model_fields


class TestOperatorActivationIsNotProof:
    """An account activated without its mailbox is never linked by e-mail."""

    async def test_admin_activated_account_under_a_mailer_is_refused(
        self, session: AsyncSession
    ) -> None:
        """The configuration proves mail; the row's activation did not.

        The registrant never opened the mailed link — an operator flipped
        ``is_active``, as the admin site and ``tempest user activate`` do.
        """
        service = _service(email=FakeEmailUtils())
        user, _ = await service.signup(session, email=_EMAIL, password=_PASSWORD)
        user.is_active = True
        await session.commit()

        with pytest.raises(OAuthEmailTakenException):
            await service.authenticate_oauth(session, _profile(), locale="pt-BR")
        assert await _account_links(session) == 0

    async def test_mail_activated_account_links(self, session: AsyncSession) -> None:
        mail = FakeEmailUtils()
        service = _service(email=mail)
        user, _ = await service.signup(session, email=_EMAIL, password=_PASSWORD)
        await session.commit()
        activated = await service.activate(session, token=_mailed_token(mail))
        await session.commit()
        assert activated.email_verified_at is not None

        linked = await service.authenticate_oauth(session, _profile(), locale="pt-BR")
        await session.commit()
        assert linked.id == user.id
        assert await _account_links(session) == 1

    async def test_proof_survives_a_later_configuration_change(
        self, session: AsyncSession
    ) -> None:
        """The column is a fact about the row, not about today's settings."""
        mail = FakeEmailUtils()
        service = _service(email=mail)
        user, _ = await service.signup(session, email=_EMAIL, password=_PASSWORD)
        await service.activate(session, token=_mailed_token(mail))
        await session.commit()

        relaxed = _service(email=None, AUTH_AUTO_ACTIVATE=True)
        linked = await relaxed.authenticate_oauth(session, _profile(), locale="pt-BR")
        assert linked.id == user.id


class TestLinksThatDoNotProveTheMailbox:
    """Activation that bypasses the mailbox activates but stamps nothing."""

    @pytest.mark.parametrize(
        ("overrides", "mailer"),
        [
            pytest.param({"AUTH_RETURN_TOKEN_IN_RESPONSE": True}, True, id="return"),
            pytest.param({}, False, id="no-mailer"),
        ],
    )
    async def test_returned_link_activates_without_proof(
        self,
        session: AsyncSession,
        overrides: dict[str, Any],
        mailer: bool,
    ) -> None:
        service = _service(email=FakeEmailUtils() if mailer else None, **overrides)
        _, activation = await service.signup(session, email=_EMAIL, password=_PASSWORD)
        assert activation is not None and hasattr(activation, "token")
        user = await service.activate(session, token=activation.token)
        assert user.is_active is True
        assert user.email_verified_at is None

    async def test_auto_activate_stamps_nothing(self, session: AsyncSession) -> None:
        service = _service(email=FakeEmailUtils(), AUTH_AUTO_ACTIVATE=True)
        user, _ = await service.signup(session, email=_EMAIL, password=_PASSWORD)
        assert user.is_active is True
        assert user.email_verified_at is None

    async def test_a_link_returned_then_confirmed_under_a_mailer_stamps_nothing(
        self, session: AsyncSession
    ) -> None:
        """The token remembers it was not mail-only when it was issued."""
        mail = FakeEmailUtils()
        loose = _service(email=mail, AUTH_RETURN_TOKEN_IN_RESPONSE=True)
        _, activation = await loose.signup(session, email=_EMAIL, password=_PASSWORD)
        assert activation is not None and hasattr(activation, "token")

        strict = _service(email=mail)
        user = await strict.activate(session, token=activation.token)
        assert user.is_active is True
        assert user.email_verified_at is None

    async def test_a_link_to_an_address_edited_since_stamps_nothing(
        self, session: AsyncSession
    ) -> None:
        """The click proves the mailbox the link went to, not the current one."""
        mail = FakeEmailUtils()
        service = _service(email=mail)
        user, _ = await service.signup(session, email=_EMAIL, password=_PASSWORD)
        await session.commit()
        user.email = "other@example.com"
        await session.commit()

        activated = await service.activate(session, token=_mailed_token(mail))
        assert activated.is_active is True
        assert activated.email_verified_at is None


class TestEmailVerificationFlow:
    async def test_mailed_verification_stamps_the_current_address(
        self, session: AsyncSession
    ) -> None:
        mail = FakeEmailUtils()
        service = _service(email=mail)
        user, _ = await service.signup(session, email=_EMAIL, password=_PASSWORD)
        user.is_active = True
        await session.commit()
        assert user.email_verified_at is None

        await service.request_email_verification(session, user=user)
        verified = await service.confirm_email_verification(
            session, token=_mailed_token(mail)
        )
        assert verified.email_verified_at is not None


class TestEmailChange:
    """The proof follows the address it was given for."""

    async def _verified_user(
        self, service: UserAuthService, mail: FakeEmailUtils, session: AsyncSession
    ) -> Any:
        """Sign up and activate through the mailed link.

        Args:
            service (UserAuthService): A mail-proving service.
            mail (FakeEmailUtils): Its mailer.
            session (AsyncSession): The test session.

        Returns:
            Any: The verified user.
        """
        await service.signup(session, email=_EMAIL, password=_PASSWORD)
        user = await service.activate(session, token=_mailed_token(mail))
        await session.commit()
        assert user.email_verified_at is not None
        return user

    async def test_mailed_change_stamps_the_new_address(
        self, session: AsyncSession
    ) -> None:
        mail = FakeEmailUtils()
        service = _service(email=mail, AUTH_EMAIL_CHANGE_NOTIFY_OLD=False)
        user = await self._verified_user(service, mail, session)
        before = user.email_verified_at

        await service.request_email_change(
            session, user=user, current_password=_PASSWORD, new_email="new@example.com"
        )
        changed = await service.confirm_email_change(session, token=_mailed_token(mail))
        assert changed.email == "new@example.com"
        assert changed.email_verified_at is not None
        assert changed.email_verified_at >= before

    async def test_returned_change_link_clears_the_proof(
        self, session: AsyncSession
    ) -> None:
        mail = FakeEmailUtils()
        service = _service(email=mail, AUTH_EMAIL_CHANGE_NOTIFY_OLD=False)
        user = await self._verified_user(service, mail, session)

        loose = _service(
            email=mail,
            AUTH_RETURN_TOKEN_IN_RESPONSE=True,
            AUTH_EMAIL_CHANGE_NOTIFY_OLD=False,
        )
        bundle = await loose.request_email_change(
            session, user=user, current_password=_PASSWORD, new_email="new@example.com"
        )
        assert bundle is not None
        changed = await loose.confirm_email_change(session, token=bundle.token)
        assert changed.email == "new@example.com"
        assert changed.email_verified_at is None

    async def test_an_operator_editing_the_email_clears_the_proof(
        self, session: AsyncSession
    ) -> None:
        """The admin site and scripts write ``email`` straight on the row."""
        mail = FakeEmailUtils()
        service = _service(email=mail)
        user = await self._verified_user(service, mail, session)

        user.email = "attacker@example.com"
        await session.commit()
        await session.refresh(user)
        assert user.email_verified_at is None

        user.is_admin = True
        await session.commit()
        assert inspect(user).attrs.email_verified_at.value is None

    async def test_other_edits_keep_the_proof(self, session: AsyncSession) -> None:
        mail = FakeEmailUtils()
        service = _service(email=mail)
        user = await self._verified_user(service, mail, session)

        user.name = "Ana Maria"
        await session.commit()
        await session.refresh(user)
        assert user.email_verified_at is not None


class TestSocialAccountCreation:
    async def test_created_account_is_born_verified(
        self, session: AsyncSession
    ) -> None:
        service = _service(email=FakeEmailUtils(), AUTH_SIGNUP_ENABLED=True)
        user = await service.authenticate_oauth(session, _profile(), locale="pt-BR")
        assert user.email_verified_at is not None
