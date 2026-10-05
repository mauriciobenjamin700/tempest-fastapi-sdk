"""A token outlives the account it was issued to — unless the dependency checks.

``login`` refuses an inactive account, but a token issued before the
deactivation stayed valid for the whole access TTL: ``current_user_dependency``
and the router's own loader returned the row without reading ``is_active``.
Both now refuse it (#421).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseUserModel,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.exceptions import AppException
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

PASSWORD: str = "strong-pass-12-chars"


class _ActiveUser(BaseUserModel):
    """User model for the deactivation tests."""

    __tablename__ = "active_check_users"


_ActiveUserToken = make_user_token_model(
    user_table="active_check_users",
    tablename="active_check_user_tokens",
    class_name="_ActiveUserToken",
)


class AccountSuspendedError(AppException):
    """A product's own refusal for a suspended account."""

    status_code: int = 423
    message: str = "Account suspended"
    code: str = "ACCOUNT_SUSPENDED"


@pytest.fixture
async def stack() -> AsyncIterator[tuple[UserAuthService, str]]:
    """Yield a service and an access token whose account is now inactive.

    Yields:
        tuple[UserAuthService, str]: The service and the bearer token,
        issued while the account was active.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    service = UserAuthService(
        db=db,
        user_model=_ActiveUser,
        token_model=_ActiveUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(_env_file=None, AUTH_AUTO_ACTIVATE=True),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
    )
    async with db.get_session_context() as session:
        user, _ = await service.signup(
            session, email="ana@example.com", password=PASSWORD
        )
        access, _ = service.issue_jwt_pair(user)
        user.is_active = False
        await session.commit()
    try:
        yield service, access
    finally:
        await db.disconnect()


async def _get_me(
    dependency: Callable[..., Coroutine[Any, Any, Any]],
    access: str,
) -> tuple[int, Any]:
    """Call a ``/me`` route guarded by ``dependency`` with ``access``.

    Args:
        dependency (Callable[..., Coroutine[Any, Any, Any]]): The user
            dependency under test.
        access (str): The bearer token.

    Returns:
        tuple[int, Any]: Status code and JSON body.
    """
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/me")
    async def me(current: Any = Depends(dependency)) -> dict[str, Any]:
        """Echo who the dependency resolved.

        Args:
            current (Any): The resolved user, or ``None``.

        Returns:
            dict[str, Any]: The email, or ``None``.
        """
        return {"email": None if current is None else current.email}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        response = await client.get(
            "/me", headers={"Authorization": f"Bearer {access}"}
        )
    return response.status_code, response.json()


class TestCurrentUserDependency:
    """``UserAuthService.current_user_dependency``."""

    async def test_deactivated_account_is_403(
        self, stack: tuple[UserAuthService, str]
    ) -> None:
        """The token is still valid; the account is not."""
        service, access = stack

        status, body = await _get_me(service.current_user_dependency(), access)

        assert status == 403
        assert body["code"] == "FORBIDDEN"

    async def test_require_active_false_keeps_the_old_behavior(
        self, stack: tuple[UserAuthService, str]
    ) -> None:
        """The reactivation route opts out and still sees the account."""
        service, access = stack

        status, body = await _get_me(
            service.current_user_dependency(require_active=False), access
        )

        assert status == 200
        assert body == {"email": "ana@example.com"}

    async def test_soft_yields_none(self, stack: tuple[UserAuthService, str]) -> None:
        """Soft mode treats an inactive account like an invalid token."""
        service, access = stack

        status, body = await _get_me(service.current_user_dependency(soft=True), access)

        assert status == 200
        assert body == {"email": None}

    async def test_inactive_exception_is_raised(
        self, stack: tuple[UserAuthService, str]
    ) -> None:
        """A product's own refusal replaces the default ``403``."""
        service, access = stack

        status, body = await _get_me(
            service.current_user_dependency(inactive_exception=AccountSuspendedError),
            access,
        )

        assert status == 423
        assert body["code"] == "ACCOUNT_SUSPENDED"


class TestBundledRouter:
    """``make_auth_router``'s authenticated routes use the same check."""

    async def test_me_refuses_a_deactivated_account(
        self, stack: tuple[UserAuthService, str]
    ) -> None:
        """``GET /auth/me`` with a pre-deactivation token is ``403``."""
        service, access = stack
        assert service.db is not None
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(
            make_auth_router(service, session_factory=service.db.session_dependency)
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            response = await client.get(
                "/auth/me", headers={"Authorization": f"Bearer {access}"}
            )

        assert response.status_code == 403
