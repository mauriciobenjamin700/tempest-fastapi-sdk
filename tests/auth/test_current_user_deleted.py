"""A valid token whose account was deleted answers 401, not 404 (#690).

``get_user`` raises ``NotFoundException`` when the token's ``sub`` resolves
to no account. ``current_user_dependency`` let it through, so every
protected route answered ``404 NOT_FOUND`` on a URL that exists, and a front
end that sends the user back to login on ``401`` kept the dead session.
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
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings
from tempest_fastapi_sdk.utils.token_types import ACCESS_TOKEN_TYPE

PASSWORD: str = "strong-pass-12-chars"


class _DeletedUser(BaseUserModel):
    """User model for the deleted-account tests."""

    __tablename__ = "deleted_check_users"


_DeletedUserToken = make_user_token_model(
    user_table="deleted_check_users",
    tablename="deleted_check_user_tokens",
    class_name="_DeletedUserToken",
)


@pytest.fixture
async def stack() -> AsyncIterator[tuple[UserAuthService, str]]:
    """Yield a service and an access token whose account no longer exists.

    Yields:
        tuple[UserAuthService, str]: The service and the bearer token,
        issued before the account was deleted.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    service = UserAuthService(
        db=db,
        user_model=_DeletedUser,
        token_model=_DeletedUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(_env_file=None, AUTH_AUTO_ACTIVATE=True),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
    )
    async with db.get_session_context() as session:
        user, _ = await service.signup(
            session, email="ana@example.com", password=PASSWORD
        )
        access, _ = service.issue_jwt_pair(user)
        await session.delete(user)
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
    """``UserAuthService.current_user_dependency`` with a deleted account."""

    async def test_default_is_401(self, stack: tuple[UserAuthService, str]) -> None:
        service, access = stack

        status, body = await _get_me(service.current_user_dependency(), access)

        assert status == 401
        assert body["code"] == "UNAUTHORIZED"

    async def test_require_active_false_is_401(
        self, stack: tuple[UserAuthService, str]
    ) -> None:
        """The reactivation path declines a missing account the same way."""
        service, access = stack

        status, _ = await _get_me(
            service.current_user_dependency(require_active=False), access
        )

        assert status == 401

    async def test_soft_yields_none(self, stack: tuple[UserAuthService, str]) -> None:
        """Soft mode treats a deleted account like an invalid token."""
        service, access = stack

        status, body = await _get_me(service.current_user_dependency(soft=True), access)

        assert status == 200
        assert body == {"email": None}

    async def test_malformed_subject_is_401(
        self, stack: tuple[UserAuthService, str]
    ) -> None:
        """A correctly signed token whose ``sub`` is not a UUID is declined too."""
        service, _ = stack
        token = service.jwt.encode(
            {"sub": "not-a-uuid", "typ": ACCESS_TOKEN_TYPE, "amr": ["pwd"]}
        )

        status, _ = await _get_me(service.current_user_dependency(), token)

        assert status == 401


class TestBundledRouter:
    """``make_auth_router``'s authenticated routes use the same check."""

    async def test_me_is_401(self, stack: tuple[UserAuthService, str]) -> None:
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

        assert response.status_code == 401
