"""The pair an emailed link opens says ``amr=["email"]``, not ``["pwd"]``.

Through 0.313.0 ``POST /auth/activate/{token}`` and
``POST /auth/password-reset/confirm`` stamped :data:`PASSWORD_AMR` on the
pair they issue, but neither request checks a password: the activation
presents none, and the reset proves the mailbox before it *sets* one. Both
now stamp :data:`AMR_EMAIL_LINK`, which ``make_mfa_required_dependency``
keeps refusing. With TOTP enrolled the ``mfa_token`` carries ``email`` as
the first step, so ``/auth/mfa/verify`` opens ``["email", "otp"]``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import tempest_fastapi_sdk
import tempest_fastapi_sdk.utils
from tempest_fastapi_sdk import (
    AMR_EMAIL_LINK,
    AMR_PASSWORD,
    MFA_AMR_METHODS,
    PASSWORD_AMR,
    BaseModel,
    BaseUserModel,
    UserAuthService,
    make_auth_router,
    make_mfa_required_dependency,
    make_user_refresh_token_model,
    make_user_token_model,
    register_exception_handlers,
    token_amr,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

PASSWORD: str = "strong-pass-12-chars"
NEW_PASSWORD: str = "brand-new-pass-12-chars"


class _LinkUser(BaseUserModel):
    __tablename__ = "email_amr_test_users"


_LinkUserToken = make_user_token_model(
    user_table="email_amr_test_users",
    tablename="email_amr_test_user_tokens",
    class_name="_LinkUserToken",
)
_LinkRefreshToken = make_user_refresh_token_model(
    user_table="email_amr_test_users",
    tablename="email_amr_test_refresh_tokens",
    class_name="_LinkRefreshToken",
)


class _LegacyPairService(UserAuthService):
    """An ``issue_token_pair`` override written before ``amr`` existed."""

    async def issue_token_pair(  # type: ignore[override]
        self,
        session: AsyncSession,
        user: BaseUserModel,
        *,
        family_id: UUID | None = None,
    ) -> tuple[str, str]:
        """Delegate without ``amr``.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user (BaseUserModel): The authenticated user.
            family_id (UUID | None): Rotation lineage.

        Returns:
            tuple[str, str]: Whatever the base class issues by default.
        """
        return await super().issue_token_pair(session, user, family_id=family_id)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield a session over a fresh in-memory database.

    Yields:
        AsyncSession: The session every request of a test shares.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _service(
    *,
    auto_activate: bool = False,
    stateless: bool = False,
    service_class: type[UserAuthService] = UserAuthService,
) -> UserAuthService:
    """Build an auth service that returns its links in the body.

    Args:
        auto_activate (bool): Whether a signup is born active.
        stateless (bool): Whether to leave the refresh-token model out, so
            the refresh token is a JWT that carries ``amr`` itself.
        service_class (type[UserAuthService]): The class to instantiate.

    Returns:
        UserAuthService: The configured service.
    """
    return service_class(
        user_model=_LinkUser,
        token_model=_LinkUserToken,
        auth_settings=AuthSettings(
            _env_file=None,
            AUTH_AUTO_ACTIVATE=auto_activate,
            AUTH_RETURN_TOKEN_IN_RESPONSE=True,
        ),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        email=None,
        refresh_token_model=None if stateless else _LinkRefreshToken,
    )


def _client(service: UserAuthService, session: AsyncSession) -> AsyncClient:
    """Mount the auth router plus one MFA-gated route.

    Args:
        service (UserAuthService): The configured service.
        session (AsyncSession): The session every request shares.

    Returns:
        AsyncClient: A client bound to the app.
    """

    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(make_auth_router(service, session_factory=_factory))
    require_mfa = make_mfa_required_dependency(service.jwt)

    @app.get("/staff")
    async def staff(claims: dict[str, Any] = Depends(require_mfa)) -> dict[str, str]:
        """Answer only an MFA session.

        Args:
            claims (dict[str, Any]): The verified access-token claims.

        Returns:
            dict[str, str]: The subject.
        """
        return {"sub": str(claims["sub"])}

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _token_from_url(url: str) -> str:
    """Pull the ``token`` query value out of a link the SDK built.

    Args:
        url (str): The activation or reset URL.

    Returns:
        str: The plaintext single-use token.
    """
    return parse_qs(urlparse(url).query)["token"][0]


def _amr(service: UserAuthService, token: str) -> tuple[str, ...] | None:
    """Decode ``token`` and return its ``amr``.

    Args:
        service (UserAuthService): Holds the JWT helper.
        token (str): An access token.

    Returns:
        tuple[str, ...] | None: The claim, or ``None`` when absent.
    """
    return token_amr(service.jwt.decode(token))


async def _activate(client: AsyncClient, email: str) -> dict[str, Any]:
    """Sign up and activate through the JSON route.

    Args:
        client (AsyncClient): The test client.
        email (str): The account address.

    Returns:
        dict[str, Any]: The activation response body.
    """
    signed_up = await client.post(
        "/auth/signup", json={"email": email, "password": PASSWORD}
    )
    assert signed_up.status_code == 201, signed_up.text
    token = _token_from_url(signed_up.json()["activation_url"])
    activated = await client.post(f"/auth/activate/{token}")
    assert activated.status_code == 200, activated.text
    body: dict[str, Any] = activated.json()
    return body


class TestTheConstant:
    def test_it_is_exported_from_the_top_and_from_utils(self) -> None:
        """Both public paths carry it, with ``__all__``."""
        assert tempest_fastapi_sdk.AMR_EMAIL_LINK == "email"
        assert tempest_fastapi_sdk.utils.AMR_EMAIL_LINK == "email"
        assert "AMR_EMAIL_LINK" in tempest_fastapi_sdk.__all__
        assert "AMR_EMAIL_LINK" in tempest_fastapi_sdk.utils.__all__

    def test_it_does_not_count_as_a_second_factor(self) -> None:
        """Out of the default methods, so the gate keeps refusing it."""
        assert AMR_EMAIL_LINK not in MFA_AMR_METHODS


class TestActivationStampsEmail:
    async def test_the_activation_pair_says_email(self, session: AsyncSession) -> None:
        """No password was presented, so ``pwd`` would be false."""
        service = _service()
        async with _client(service, session) as client:
            body = await _activate(client, "act@a.com")
            gated = await client.get(
                "/staff", headers={"Authorization": f"Bearer {body['access_token']}"}
            )
        assert _amr(service, body["access_token"]) == (AMR_EMAIL_LINK,)
        assert gated.status_code == 403
        assert gated.json()["code"] == "MFA_REQUIRED"

    @pytest.mark.parametrize("stateless", [False, True], ids=["db", "stateless"])
    async def test_refresh_repeats_the_origin(
        self, session: AsyncSession, stateless: bool
    ) -> None:
        """Rotation never relabels the session as a password one."""
        service = _service(stateless=stateless)
        async with _client(service, session) as client:
            body = await _activate(client, "refresh@a.com")
            refreshed = await client.post(
                "/auth/refresh", json={"refresh_token": body["refresh_token"]}
            )
        assert refreshed.status_code == 200, refreshed.text
        assert _amr(service, refreshed.json()["access_token"]) == (AMR_EMAIL_LINK,)

    async def test_a_legacy_issue_token_pair_override_keeps_working(
        self, session: AsyncSession
    ) -> None:
        """Without ``amr=`` the override's own default applies, ``pwd``."""
        service = _service(service_class=_LegacyPairService)
        async with _client(service, session) as client:
            body = await _activate(client, "legacy@a.com")
        assert _amr(service, body["access_token"]) == PASSWORD_AMR


class TestPasswordResetStampsEmail:
    async def test_the_reset_pair_says_email(self, session: AsyncSession) -> None:
        """The reset proved the mailbox, then set the password."""
        service = _service(auto_activate=True)
        async with _client(service, session) as client:
            signed_up = await client.post(
                "/auth/signup", json={"email": "reset@a.com", "password": PASSWORD}
            )
            assert _amr(service, signed_up.json()["access_token"]) == PASSWORD_AMR
            requested = await client.post(
                "/auth/password-reset/request", json={"email": "reset@a.com"}
            )
            token = _token_from_url(requested.json()["reset_url"])
            confirmed = await client.post(
                "/auth/password-reset/confirm",
                json={"token": token, "new_password": NEW_PASSWORD},
            )
            login = await client.post(
                "/auth/login",
                json={"email": "reset@a.com", "password": NEW_PASSWORD},
            )
        assert confirmed.status_code == 200, confirmed.text
        assert _amr(service, confirmed.json()["access_token"]) == (AMR_EMAIL_LINK,)
        assert _amr(service, login.json()["access_token"]) == (AMR_PASSWORD,)
