"""What ``AUTH_BACKEND_LINKS=True`` needs installed, and when it says so.

The backend-only mode is the one a service without a frontend reaches for,
and the recipe promises it works with ``[auth,email]``. Two things broke
that promise:

* the reset form declared its fields with FastAPI's ``Form(...)``, so
  ``make_auth_router`` raised ``RuntimeError`` at construction whenever
  ``python-multipart`` was absent — which it is under ``[auth,email]``;
* without Jinja2 the router built fine and the first click on the
  activation link consumed the token, activated the account and answered
  500, because the page renders after the commit.

These tests pin the fixed shape: the form reads its urlencoded body with the
standard library, and a missing Jinja2 stops the boot instead of the click.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    BaseModel,
    BaseUserModel,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


class _BlUser(BaseUserModel):
    __tablename__ = "bl_test_users"


_BlUserToken = make_user_token_model(
    user_table="bl_test_users",
    tablename="bl_test_user_tokens",
    class_name="_BlUserToken",
)

PASSWORD: str = "strong-pass-12-chars"
NEW_PASSWORD: str = "brand-new-pass-12"


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield a session over a fresh in-memory schema.

    Yields:
        AsyncSession: The open session.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        yield opened
    await engine.dispose()


def _service() -> UserAuthService:
    """Build a backend-links service whose accounts start active.

    Returns:
        UserAuthService: The configured service.
    """
    return UserAuthService(
        user_model=_BlUser,
        token_model=_BlUserToken,
        auth_settings=AuthSettings(
            AUTH_AUTO_ACTIVATE=True,
            AUTH_BACKEND_LINKS=True,
            AUTH_DEFAULT_LOCALE="en-US",
        ),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
        email=None,
    )


def _app(service: UserAuthService, session: AsyncSession) -> FastAPI:
    """Mount the auth router over one shared session.

    Args:
        service (UserAuthService): The service to expose.
        session (AsyncSession): The session every request reuses.

    Returns:
        FastAPI: The application.
    """

    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    app = FastAPI()
    app.include_router(make_auth_router(service, session_factory=_factory))
    return app


async def _reset_token(service: UserAuthService, session: AsyncSession) -> str:
    """Create an account and issue a password-reset token for it.

    Args:
        service (UserAuthService): The service.
        session (AsyncSession): The session.

    Returns:
        str: The plaintext reset token.
    """
    await service.signup(session, email="ana@example.com", password=PASSWORD)
    await session.commit()
    issued = await service.request_password_reset(session, email="ana@example.com")
    await session.commit()
    assert issued is not None
    return issued.token


def _hide_multipart(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``python-multipart`` unimportable, as under ``[auth,email]``.

    Args:
        monkeypatch (pytest.MonkeyPatch): The fixture that restores
            ``sys.modules`` afterwards.
    """
    monkeypatch.setitem(sys.modules, "python_multipart", None)
    monkeypatch.setitem(sys.modules, "multipart", None)


class TestResetFormWithoutMultipart:
    """The reset form needs no ``python-multipart``."""

    def test_router_builds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Construction used to raise ``RuntimeError`` here."""
        _hide_multipart(monkeypatch)
        router = make_auth_router(_service(), session_factory=lambda: None)  # type: ignore[arg-type,return-value]
        paths: set[str] = {route.path for route in router.routes}  # type: ignore[attr-defined]
        assert "/auth/password-reset/{token}" in paths

    async def test_urlencoded_submit_resets_the_password(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A plain browser form submit changes the password."""
        _hide_multipart(monkeypatch)
        service = _service()
        token = await _reset_token(service, session)
        async with AsyncClient(
            transport=ASGITransport(app=_app(service, session)),
            base_url="http://t",
        ) as client:
            response: Response = await client.post(
                f"/auth/password-reset/{token}",
                data={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
            )
            login: Response = await client.post(
                "/auth/login",
                json={"email": "ana@example.com", "password": NEW_PASSWORD},
            )
        assert response.status_code == 200, response.text
        assert login.status_code == 200, login.text

    async def test_missing_field_rerenders_without_spending_the_token(
        self,
        session: AsyncSession,
    ) -> None:
        """An incomplete body re-renders the form and keeps the link alive."""
        service = _service()
        token = await _reset_token(service, session)
        async with AsyncClient(
            transport=ASGITransport(app=_app(service, session)),
            base_url="http://t",
        ) as client:
            incomplete: Response = await client.post(
                f"/auth/password-reset/{token}",
                data={"new_password": NEW_PASSWORD},
            )
            retry: Response = await client.post(
                f"/auth/password-reset/{token}",
                data={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
            )
        assert incomplete.status_code == 400, incomplete.text
        assert f'action="/auth/password-reset/{token}"' in incomplete.text
        assert retry.status_code == 200, retry.text

    async def test_multipart_submit_still_accepted(
        self,
        session: AsyncSession,
    ) -> None:
        """A template that sets ``enctype`` keeps working where the package is."""
        service = _service()
        token = await _reset_token(service, session)
        async with AsyncClient(
            transport=ASGITransport(app=_app(service, session)),
            base_url="http://t",
        ) as client:
            response: Response = await client.post(
                f"/auth/password-reset/{token}",
                data={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
                files={"unused": ("unused.txt", b"x")},
            )
        assert response.status_code == 200, response.text


class TestBackendLinksWithoutJinja2:
    """A missing Jinja2 fails the boot, not the user's click."""

    def test_router_refuses_to_build(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The error names the extra to install."""
        monkeypatch.setitem(sys.modules, "jinja2", None)
        with pytest.raises(RuntimeError, match=r"\[auth,email\]"):
            make_auth_router(_service(), session_factory=lambda: None)  # type: ignore[arg-type,return-value]

    def test_json_only_router_does_not_need_jinja2(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without backend links nothing renders, so nothing is required."""
        monkeypatch.setitem(sys.modules, "jinja2", None)
        service = UserAuthService(
            user_model=_BlUser,
            token_model=_BlUserToken,
            auth_settings=AuthSettings(AUTH_BACKEND_LINKS=False),
            jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
            email=None,
        )
        router = make_auth_router(service, session_factory=lambda: None)  # type: ignore[arg-type,return-value]
        assert router.routes
