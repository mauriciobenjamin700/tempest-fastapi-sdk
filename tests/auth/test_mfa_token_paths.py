"""What each emailed-link and social path hands to an MFA account (#543).

#534 (0.313.0) made ``POST /auth/activate/{token}`` and
``POST /auth/password-reset/confirm`` answer ``mfa_required`` for an
account with TOTP enrolled. #543 also named ``activate_html``, the
``GET /auth/activate/{token}`` page that ``AUTH_BACKEND_LINKS=True``
mounts. These tests measure the remaining paths instead of assuming:

* the two backend-link HTML routes (activation and the reset form) render
  a page and issue nothing — no token in the body, no ``Set-Cookie`` even
  under cookie delivery — so the next step is ``/auth/login``, which asks
  for the second factor;
* the pair an activation link hands to a brand-new account (the STAFF
  scenario of the issue: no factor enrolled yet) is refused by
  :func:`~tempest_fastapi_sdk.make_mfa_required_dependency`;
* a social login of an MFA-enrolled account issues a pair without the TOTP
  step, stamped ``amr=["ext"]``, which the same dependency refuses.

``tests/test_mfa_token_paths_guard.py`` keeps the list of issuing routes
closed.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pyotp
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    AMR_EXTERNAL,
    AMR_HARDWARE_KEY,
    AMR_OTP,
    BaseModel,
    BaseUserModel,
    MFAMixin,
    OAuthUser,
    UserAuthService,
    make_auth_router,
    make_mfa_required_dependency,
    make_user_oauth_account_model,
    make_user_recovery_code_model,
    make_user_refresh_token_model,
    make_user_token_model,
    register_exception_handlers,
    token_amr,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

PASSWORD: str = "strong-pass-12-chars"
NEW_PASSWORD: str = "brand-new-pass-12-chars"
JWT_SHAPE: re.Pattern[str] = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")
"""A compact JWS, the shape of every access token the SDK signs."""


class _PathsUser(MFAMixin, BaseUserModel):
    __tablename__ = "mfa_paths_test_users"


_PathsUserToken = make_user_token_model(
    user_table="mfa_paths_test_users",
    tablename="mfa_paths_test_user_tokens",
    class_name="_PathsUserToken",
)
_PathsRecoveryCode = make_user_recovery_code_model(
    user_table="mfa_paths_test_users",
    tablename="mfa_paths_test_recovery_codes",
    class_name="_PathsRecoveryCode",
)
_PathsRefreshToken = make_user_refresh_token_model(
    user_table="mfa_paths_test_users",
    tablename="mfa_paths_test_refresh_tokens",
    class_name="_PathsRefreshToken",
)
_PathsOAuthAccount = make_user_oauth_account_model(
    user_table="mfa_paths_test_users",
    tablename="mfa_paths_test_oauth_accounts",
    class_name="_PathsOAuthAccount",
)


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


def _service(*, auto_activate: bool = False) -> UserAuthService:
    """Build a service with MFA, backend links and both deliveries on.

    Args:
        auto_activate (bool): Whether a signup is born active.

    Returns:
        UserAuthService: The configured service.
    """
    return UserAuthService(
        user_model=_PathsUser,
        token_model=_PathsUserToken,
        auth_settings=AuthSettings(
            _env_file=None,
            AUTH_AUTO_ACTIVATE=auto_activate,
            AUTH_RETURN_TOKEN_IN_RESPONSE=True,
            AUTH_MFA_ENABLED=True,
            AUTH_MFA_ISSUER="Tempest Test",
            AUTH_BACKEND_LINKS=True,
            AUTH_TOKEN_DELIVERY="both",
            AUTH_COOKIE_SECURE=False,
            AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL=True,
        ),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        email=None,
        refresh_token_model=_PathsRefreshToken,
        oauth_account_model=_PathsOAuthAccount,
    )


def _client(service: UserAuthService, session: AsyncSession) -> AsyncClient:
    """Mount the auth router plus one MFA-gated route on a throwaway app.

    Args:
        service (UserAuthService): The configured service.
        session (AsyncSession): The session every request shares.

    Returns:
        AsyncClient: A client bound to the app over ASGI. ``GET /staff``
        answers 200 only to a session that passed a second factor.
    """

    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_auth_router(
            service,
            session_factory=_factory,
            recovery_code_model=_PathsRecoveryCode,
        )
    )
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


def _assert_no_session(response: Response) -> None:
    """Assert ``response`` carries no access token in any form.

    Args:
        response (Response): A backend-link page response.
    """
    assert response.headers.get_list("set-cookie") == []
    assert JWT_SHAPE.search(response.text) is None


async def _active_mfa_account(
    client: AsyncClient, session: AsyncSession, email: str
) -> _PathsUser:
    """Sign up, activate, enroll TOTP and return the row.

    Args:
        client (AsyncClient): The test client.
        session (AsyncSession): The shared session.
        email (str): The account address.

    Returns:
        _PathsUser: The account, with ``totp_enabled_at`` set.
    """
    signed_up = await client.post(
        "/auth/signup", json={"email": email, "password": PASSWORD}
    )
    assert signed_up.status_code == 201, signed_up.text
    token = _token_from_url(signed_up.json()["activation_url"])
    activated = await client.post(f"/auth/activate/{token}")
    assert activated.status_code == 200, activated.text
    bearer = {"Authorization": f"Bearer {activated.json()['access_token']}"}
    enrolled = await client.post("/auth/mfa/enroll", headers=bearer)
    assert enrolled.status_code == 200, enrolled.text
    confirmed = await client.post(
        "/auth/mfa/confirm",
        headers=bearer,
        json={"code": pyotp.TOTP(enrolled.json()["secret"]).now()},
    )
    assert confirmed.status_code == 200, confirmed.text
    user = (
        await session.execute(select(_PathsUser).where(_PathsUser.email == email))
    ).scalar_one()
    assert user.totp_enabled_at is not None
    return user


class TestBackendLinkPagesIssueNothing:
    async def test_html_reactivation_of_an_mfa_account_logs_nobody_in(
        self, session: AsyncSession
    ) -> None:
        """The GET activation page reactivates, renders, and stops there."""
        service = _service()
        async with _client(service, session) as client:
            user = await _active_mfa_account(client, session, "html-act@a.com")
            user.is_active = False
            await session.commit()
            requested = await client.post(
                "/auth/activation/request", json={"email": "html-act@a.com"}
            )
            assert requested.status_code == 202, requested.text
            token = _token_from_url(requested.json()["activation_url"])

            page = await client.get(f"/auth/activate/{token}")
            assert page.status_code == 200, page.text
            assert page.headers["content-type"].startswith("text/html")
            _assert_no_session(page)
            await session.refresh(user)
            assert user.is_active is True

            login = await client.post(
                "/auth/login",
                json={"email": "html-act@a.com", "password": PASSWORD},
            )
        assert login.status_code == 200, login.text
        assert login.json()["mfa_required"] is True
        assert login.json()["access_token"] is None

    async def test_html_activation_of_a_fresh_account_logs_nobody_in(
        self, session: AsyncSession
    ) -> None:
        """Unlike the JSON route, the page issues no pair to a new account."""
        service = _service()
        async with _client(service, session) as client:
            signed_up = await client.post(
                "/auth/signup",
                json={"email": "html-fresh@a.com", "password": PASSWORD},
            )
            token = _token_from_url(signed_up.json()["activation_url"])
            page = await client.get(f"/auth/activate/{token}")
        assert page.status_code == 200, page.text
        _assert_no_session(page)

    async def test_html_password_reset_of_an_mfa_account_logs_nobody_in(
        self, session: AsyncSession
    ) -> None:
        """The reset form rotates the password and renders, no session."""
        service = _service()
        async with _client(service, session) as client:
            await _active_mfa_account(client, session, "html-reset@a.com")
            requested = await client.post(
                "/auth/password-reset/request", json={"email": "html-reset@a.com"}
            )
            assert requested.status_code == 202, requested.text
            token = _token_from_url(requested.json()["reset_url"])

            page = await client.post(
                f"/auth/password-reset/{token}",
                data={
                    "new_password": NEW_PASSWORD,
                    "confirm_password": NEW_PASSWORD,
                },
            )
            assert page.status_code == 200, page.text
            _assert_no_session(page)

            login = await client.post(
                "/auth/login",
                json={"email": "html-reset@a.com", "password": NEW_PASSWORD},
            )
        assert login.json()["mfa_required"] is True


class TestSingleFactorPairsAreRefusedByTheMfaGate:
    async def test_the_pair_of_an_activation_link_is_not_an_mfa_session(
        self, session: AsyncSession
    ) -> None:
        """The STAFF scenario of #543: a new account activated by link.

        No factor is enrolled yet, so ``/activate`` returns the pair. The
        pair carries no second-factor method, and the MFA gate refuses it
        with ``403 MFA_REQUIRED``.
        """
        service = _service()
        async with _client(service, session) as client:
            signed_up = await client.post(
                "/auth/signup",
                json={"email": "staff@a.com", "password": PASSWORD},
            )
            token = _token_from_url(signed_up.json()["activation_url"])
            activated = await client.post(f"/auth/activate/{token}")
            access: str = activated.json()["access_token"]
            amr = token_amr(service.jwt.decode(access)) or ()
            gated = await client.get(
                "/staff", headers={"Authorization": f"Bearer {access}"}
            )
        assert AMR_OTP not in amr
        assert AMR_HARDWARE_KEY not in amr
        assert gated.status_code == 403, gated.text
        assert gated.json()["code"] == "MFA_REQUIRED"

    async def test_a_social_login_of_an_mfa_account_is_not_an_mfa_session(
        self, session: AsyncSession
    ) -> None:
        """OAuth skips the TOTP step, but the pair says so with ``ext``."""
        service = _service()
        async with _client(service, session) as client:
            user = await _active_mfa_account(client, session, "social@a.com")
            assert service.is_mfa_enrolled(user)
            _, access, _ = await service.login_with_oauth(
                session,
                OAuthUser(
                    provider="google",
                    subject="sub-social",
                    email="social@a.com",
                    email_verified=True,
                    name="Social",
                ),
                locale="en",
            )
            await session.commit()
            gated = await client.get(
                "/staff", headers={"Authorization": f"Bearer {access}"}
            )
        assert token_amr(service.jwt.decode(access)) == (AMR_EXTERNAL,)
        assert gated.status_code == 403, gated.text
        assert gated.json()["code"] == "MFA_REQUIRED"
