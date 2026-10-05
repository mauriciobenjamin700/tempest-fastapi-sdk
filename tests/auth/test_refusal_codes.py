"""Refusal codes a product can keep: ``AuthExceptions``, violation codes, 403.

Three seams that used to need a method override in the consuming service:

* ``exceptions=AuthExceptions(...)`` swaps the class raised at each
  refusal point, so the ``code`` the clients already handle survives the
  adoption of ``UserAuthService`` (#425).
* every password-policy violation carries its own ``message_key``, so a
  catalog says "too long" and "too short" differently (#425).
* ``AUTH_REVEAL_INACTIVE_ACCOUNT`` tells the owner of a deactivated
  account — and only someone who proved the password — why login fails
  (#424).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    AccountInactiveException,
    AuthExceptions,
    BaseModel,
    BaseUserModel,
    PasswordUtils,
    PasswordViolationCode,
    UserAuthService,
    default_message_catalog,
    make_auth_router,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.exceptions import (
    ConflictException,
    ForbiddenException,
    InvalidTokenException,
    UnauthorizedException,
    ValidationException,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

PASSWORD: str = "strong-pass-12-chars"


class _RefusalUser(BaseUserModel):
    """User model for the refusal tests."""

    __tablename__ = "refusal_users"


_RefusalUserToken = make_user_token_model(
    user_table="refusal_users",
    tablename="refusal_user_tokens",
    class_name="_RefusalUserToken",
)


class InvalidCredentialsError(UnauthorizedException):
    """A product's own wrong-credentials refusal."""

    code: str = "ERROR_USER_INVALID_CREDENTIALS"


class UserDeactivatedError(ForbiddenException):
    """A product's own deactivated-account refusal."""

    code: str = "USER_ACCOUNT_DEACTIVATED"


class UserAlreadyExistsError(ConflictException):
    """A product's own duplicate-email refusal."""

    code: str = "USER_ALREADY_EXISTS"


class InvalidPasswordError(ValidationException):
    """A product's own short/weak-password refusal."""

    code: str = "INVALID_PASSWORD"


class PasswordTooLongError(ValidationException):
    """A product's own too-long-password refusal."""

    code: str = "PASSWORD_TOO_LONG"


class InvalidResetTokenError(InvalidTokenException):
    """A product's own invalid-link refusal."""

    code: str = "INVALID_PASSWORD_RESET_TOKEN"


PRODUCT_EXCEPTIONS: AuthExceptions = AuthExceptions(
    invalid_credentials=InvalidCredentialsError,
    account_inactive=UserDeactivatedError,
    email_taken=UserAlreadyExistsError,
    password_too_short=InvalidPasswordError,
    password_too_long=PasswordTooLongError,
    password_too_weak=InvalidPasswordError,
    invalid_token=InvalidResetTokenError,
)


class _CountingPasswords(PasswordUtils):
    """``PasswordUtils`` that counts bcrypt verifications.

    Only :meth:`verify` is wrapped: ``dummy_verify`` spends its bcrypt by
    calling :meth:`verify` against a throwaway hash, so it is counted
    there.
    """

    def __init__(self) -> None:
        """Start the counter at zero."""
        super().__init__()
        self.verifications: int = 0

    def verify(self, plain: str, hashed: str) -> bool:
        """Count, then verify.

        Args:
            plain (str): The plaintext.
            hashed (str): The stored hash.

        Returns:
            bool: Whether they match.
        """
        self.verifications += 1
        return super().verify(plain, hashed)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield one session over a fresh in-memory database.

    Yields:
        AsyncSession: The session.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as active:
        yield active
    await engine.dispose()


def _service(
    *,
    reveal: bool = False,
    complexity: bool = False,
    exceptions: AuthExceptions | None = None,
    passwords: PasswordUtils | None = None,
) -> UserAuthService:
    """Build a service over the refusal model.

    Args:
        reveal (bool): ``AUTH_REVEAL_INACTIVE_ACCOUNT``.
        complexity (bool): ``AUTH_PASSWORD_REQUIRE_COMPLEXITY``.
        exceptions (AuthExceptions | None): The refusal classes.
        passwords (PasswordUtils | None): The hasher.

    Returns:
        UserAuthService: The service under test.
    """
    return UserAuthService(
        user_model=_RefusalUser,
        token_model=_RefusalUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            _env_file=None,
            AUTH_AUTO_ACTIVATE=True,
            AUTH_RETURN_TOKEN_IN_RESPONSE=True,
            AUTH_REVEAL_INACTIVE_ACCOUNT=reveal,
            AUTH_PASSWORD_REQUIRE_COMPLEXITY=complexity,
        ),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        exceptions=exceptions,
        passwords=passwords,
    )


async def _deactivated(service: UserAuthService, session: AsyncSession) -> None:
    """Create ``ana@example.com`` and deactivate it.

    Args:
        service (UserAuthService): The service.
        session (AsyncSession): The session.
    """
    user, _ = await service.signup(session, email="ana@example.com", password=PASSWORD)
    user.is_active = False
    await session.commit()


class TestPasswordViolationCodes:
    """Each violation has its own ``message_key``, translated by default."""

    @pytest.mark.parametrize(
        ("password", "complexity", "code", "pt_br"),
        [
            (
                "short",
                False,
                PasswordViolationCode.PASSWORD_TOO_SHORT,
                "A senha precisa ter pelo menos 12 caracteres",
            ),
            (
                "x" * 73,
                False,
                PasswordViolationCode.PASSWORD_TOO_LONG,
                "A senha pode ter no máximo 72 bytes",
            ),
            (
                "alllowercase-but-long",
                True,
                PasswordViolationCode.PASSWORD_TOO_WEAK,
                "A senha precisa ter letra minúscula, letra maiúscula, número e "
                "caractere especial",
            ),
        ],
    )
    async def test_violation_message_key(
        self,
        session: AsyncSession,
        password: str,
        complexity: bool,
        code: PasswordViolationCode,
        pt_br: str,
    ) -> None:
        """The raised key is the violation code, and the catalog renders it."""
        with pytest.raises(ValidationException) as caught:
            await _service(complexity=complexity).signup(
                session, email="ana@example.com", password=password
            )

        assert caught.value.message_key == code.value
        rendered = default_message_catalog().resolve(
            code.value, "pt-BR", caught.value.message_params
        )
        assert rendered == pt_br


class TestResetWithRemovedUser:
    """A reset link whose user is gone is an invalid link, not a 404."""

    async def test_missing_user_is_an_invalid_token(
        self, session: AsyncSession
    ) -> None:
        """``confirm_password_reset`` raises ``InvalidTokenException``."""
        service = _service()
        await service.signup(session, email="ana@example.com", password=PASSWORD)
        issued = await service.request_password_reset(session, email="ana@example.com")
        assert issued is not None
        await session.commit()
        await session.execute(delete(_RefusalUser))
        await session.commit()

        with pytest.raises(InvalidTokenException, match="missing user"):
            await service.confirm_password_reset(
                session, token=issued.token, new_password="another-pass-123"
            )


class TestRevealInactiveAccount:
    """``AUTH_REVEAL_INACTIVE_ACCOUNT`` and the three login refusals."""

    @pytest.mark.parametrize(
        ("email", "password"),
        [
            ("ana@example.com", PASSWORD),
            ("ana@example.com", "wrong-password-1"),
            ("ghost@example.com", PASSWORD),
        ],
    )
    async def test_off_every_refusal_is_the_generic_401(
        self, session: AsyncSession, email: str, password: str
    ) -> None:
        """Default: right password, wrong password, unknown email all 401."""
        service = _service()
        await _deactivated(service, session)

        with pytest.raises(UnauthorizedException, match="invalid email or password"):
            await service.login(session, email=email, password=password)

    async def test_on_right_password_is_403(self, session: AsyncSession) -> None:
        """The owner who proved the password learns the account is inactive."""
        service = _service(reveal=True)
        await _deactivated(service, session)

        with pytest.raises(AccountInactiveException) as caught:
            await service.login(session, email="ana@example.com", password=PASSWORD)

        assert caught.value.status_code == 403
        assert caught.value.code == "ACCOUNT_INACTIVE"

    @pytest.mark.parametrize(
        "email", ["ana@example.com", "ghost@example.com"], ids=["wrong", "unknown"]
    )
    async def test_on_wrong_password_and_unknown_email_stay_401(
        self, session: AsyncSession, email: str
    ) -> None:
        """Without the password nothing is revealed."""
        service = _service(reveal=True)
        await _deactivated(service, session)

        with pytest.raises(UnauthorizedException):
            await service.login(session, email=email, password="wrong-password-1")

    @pytest.mark.parametrize("reveal", [False, True])
    @pytest.mark.parametrize(
        ("email", "password"),
        [
            ("ana@example.com", PASSWORD),
            ("ana@example.com", "wrong-password-1"),
            ("ghost@example.com", PASSWORD),
        ],
        ids=["inactive", "wrong", "unknown"],
    )
    async def test_every_branch_pays_one_bcrypt(
        self, session: AsyncSession, reveal: bool, email: str, password: str
    ) -> None:
        """Exactly one verification per refused login, in every branch."""
        passwords = _CountingPasswords()
        service = _service(reveal=reveal, passwords=passwords)
        await _deactivated(service, session)
        passwords.verifications = 0

        with pytest.raises((UnauthorizedException, ForbiddenException)):
            await service.login(session, email=email, password=password)

        assert passwords.verifications == 1

    async def test_router_answers_403_with_the_code(
        self, session: AsyncSession
    ) -> None:
        """``POST /auth/login`` carries the 403 through the handlers."""
        service = _service(reveal=True)
        await _deactivated(service, session)

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(make_auth_router(service, session_factory=_factory))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            response = await client.post(
                "/auth/login",
                json={"email": "ana@example.com", "password": PASSWORD},
            )

        assert response.status_code == 403
        assert response.json()["code"] == "ACCOUNT_INACTIVE"


class TestProductExceptions:
    """``exceptions=`` swaps the class — and the code — at every point."""

    async def _refusal(self, session: AsyncSession, point: str) -> Exception:
        """Trigger one refusal point and return what it raised.

        Args:
            session (AsyncSession): The session.
            point (str): Which refusal to trigger.

        Returns:
            Exception: The raised exception.
        """
        service = _service(reveal=True, exceptions=PRODUCT_EXCEPTIONS)
        await service.signup(session, email="ana@example.com", password=PASSWORD)
        await service.signup(session, email="off@example.com", password=PASSWORD)
        await session.commit()
        calls: dict[str, Any] = {
            "invalid_credentials": lambda: service.login(
                session, email="ana@example.com", password="wrong-password-1"
            ),
            "email_taken": lambda: service.signup(
                session, email="ana@example.com", password=PASSWORD
            ),
            "password_too_short": lambda: service.signup(
                session, email="new@example.com", password="short"
            ),
            "password_too_long": lambda: service.signup(
                session, email="new@example.com", password="x" * 73
            ),
            "invalid_token": lambda: service.confirm_password_reset(
                session, token="no-such-token", new_password="another-pass-123"
            ),
        }
        with pytest.raises(Exception) as caught:
            await calls[point]()
        return caught.value

    @pytest.mark.parametrize(
        ("point", "cls", "status"),
        [
            ("invalid_credentials", InvalidCredentialsError, 401),
            ("email_taken", UserAlreadyExistsError, 409),
            ("password_too_short", InvalidPasswordError, 422),
            ("password_too_long", PasswordTooLongError, 422),
            ("invalid_token", InvalidResetTokenError, 401),
        ],
    )
    async def test_point_raises_the_product_class(
        self,
        session: AsyncSession,
        point: str,
        cls: type[Exception],
        status: int,
    ) -> None:
        """Class and code change; the status is the default's."""
        raised = await self._refusal(session, point)

        assert type(raised) is cls
        assert raised.code == cls.code  # type: ignore[attr-defined]
        assert raised.status_code == status  # type: ignore[attr-defined]

    async def test_account_inactive_raises_the_product_class(
        self, session: AsyncSession
    ) -> None:
        """The revealed 403 uses the product's class too."""
        service = _service(reveal=True, exceptions=PRODUCT_EXCEPTIONS)
        await _deactivated(service, session)

        with pytest.raises(UserDeactivatedError) as caught:
            await service.login(session, email="ana@example.com", password=PASSWORD)

        assert caught.value.code == "USER_ACCOUNT_DEACTIVATED"
        assert caught.value.status_code == 403

    def test_defaults_are_the_sdk_classes(self) -> None:
        """No ``exceptions=`` keeps every class the SDK raised before."""
        defaults = AuthExceptions()

        assert defaults.invalid_credentials is UnauthorizedException
        assert defaults.account_inactive is AccountInactiveException
        assert defaults.email_taken is ConflictException
        assert defaults.password_too_short is ValidationException
        assert defaults.password_too_long is ValidationException
        assert defaults.password_too_weak is ValidationException
        assert defaults.invalid_token is InvalidTokenException
