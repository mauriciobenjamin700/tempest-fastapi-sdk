"""Dependencies declared at import time, resources built in ``create_app`` (#493).

``make_jwt_user_dependency`` / ``make_bearer_token_dependency`` and
``make_rate_limit_dependency`` needed the ``JWTUtils`` and the store ready
when the dependency was declared, so a service with ``get_settings()`` and
``create_app(settings)`` hand-copied the JWT dependency and rebuilt the rate
limit dependency on every request. ``make_auth_router`` called
``session_factory()`` by hand, so a ``get_session`` with sub-dependencies did
not work and its ``dependency_overrides`` never reached the auth routes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    ACCESS_TOKEN_TYPE,
    BaseModel,
    BaseUserModel,
    JWTUtils,
    MemoryRateLimitStore,
    UserAuthService,
    make_auth_router,
    make_jwt_user_dependency,
    make_rate_limit_dependency,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


async def _load_user(subject: str) -> dict[str, str]:
    """Return a stand-in user for the token's subject.

    Args:
        subject (str): The token's ``sub``.

    Returns:
        dict[str, str]: The "user".
    """
    return {"id": subject}


def _jwt_of(request: Request) -> JWTUtils:
    """Read the app's JWT helper, built in ``create_app``.

    Args:
        request (Request): The request being served.

    Returns:
        JWTUtils: The helper on ``app.state``.
    """
    jwt: JWTUtils = request.app.state.jwt
    return jwt


CurrentUser = Annotated[
    dict[str, str],
    Depends(make_jwt_user_dependency(_jwt_of, user_loader=_load_user, strict=True)),
]
"""Declared at import time; no ``JWTUtils`` exists yet."""


def _store_of(request: Request) -> MemoryRateLimitStore:
    """Read the app's rate-limit store, built in ``create_app``.

    Args:
        request (Request): The request being served.

    Returns:
        MemoryRateLimitStore: The store on ``app.state``.
    """
    store: MemoryRateLimitStore = request.app.state.rate_limit_store
    return store


rate_limited = make_rate_limit_dependency(
    _store_of, max_requests=2, window_seconds=60, scope="ping"
)
"""Declared at import time; no store exists yet."""


def create_app(secret: str) -> FastAPI:
    """Build an app whose resources only exist from here on.

    Args:
        secret (str): The app's JWT secret.

    Returns:
        FastAPI: The app.
    """
    app = FastAPI()
    register_exception_handlers(app)
    app.state.jwt = JWTUtils(secret)
    app.state.rate_limit_store = MemoryRateLimitStore()

    @app.get("/me")
    async def me(user: CurrentUser) -> dict[str, str]:
        """Return the authenticated user.

        Args:
            user (dict[str, str]): Resolved by the module-level dependency.

        Returns:
            dict[str, str]: The user.
        """
        return user

    @app.get("/ping", dependencies=[Depends(rate_limited)])
    async def ping() -> dict[str, str]:
        """Answer under the module-level rate limit.

        Returns:
            dict[str, str]: A constant body.
        """
        return {"pong": "ok"}

    return app


def _client(app: FastAPI) -> AsyncClient:
    """Open a client over ``app``.

    Args:
        app (FastAPI): The app.

    Returns:
        AsyncClient: The client.
    """
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


class TestJwtFactory:
    async def test_the_module_level_dependency_uses_each_apps_secret(self) -> None:
        """Two apps, two secrets, one dependency: each refuses the other's token."""
        first = create_app("a" * 32)
        second = create_app("b" * 32)
        token = first.state.jwt.encode({"sub": "42", "typ": ACCESS_TOKEN_TYPE})
        headers = {"Authorization": f"Bearer {token}"}

        async with _client(first) as client:
            accepted = await client.get("/me", headers=headers)
        async with _client(second) as client:
            refused = await client.get("/me", headers=headers)

        assert accepted.status_code == 200
        assert accepted.json() == {"id": "42"}
        assert refused.status_code == 401


class TestRateLimitStoreFactory:
    async def test_each_app_counts_in_its_own_store(self) -> None:
        first = create_app("a" * 32)
        second = create_app("b" * 32)

        async with _client(first) as client:
            first_codes = [(await client.get("/ping")).status_code for _ in range(3)]
        async with _client(second) as client:
            second_code = (await client.get("/ping")).status_code

        assert first_codes == [200, 200, 429]
        assert second_code == 200


class _LazyUser(BaseUserModel):
    """User table for the auth router tests."""

    __tablename__ = "lazy_dependency_users"


_LazyUserToken = make_user_token_model(
    user_table="lazy_dependency_users",
    tablename="lazy_dependency_user_tokens",
    class_name="_LazyUserToken",
)

PASSWORD: str = "strong-pass-12-chars"


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a session factory over a fresh in-memory database.

    Yields:
        async_sessionmaker[AsyncSession]: The factory.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class TestAuthRouterSessionDependency:
    def _app(self, factory: async_sessionmaker[AsyncSession]) -> tuple[FastAPI, Any]:
        """Mount the auth router over a ``get_session`` with a sub-dependency.

        Args:
            factory (async_sessionmaker[AsyncSession]): The session factory.

        Returns:
            tuple[FastAPI, Any]: The app and its ``get_session``.
        """

        def get_database() -> async_sessionmaker[AsyncSession]:
            """Return the session factory, as a service's resource provider.

            Returns:
                async_sessionmaker[AsyncSession]: The factory.
            """
            return factory

        async def get_session(
            database: async_sessionmaker[AsyncSession] = Depends(get_database),
        ) -> AsyncIterator[AsyncSession]:
            """Yield a session from the database dependency.

            Args:
                database (async_sessionmaker[AsyncSession]): The factory.

            Yields:
                AsyncSession: The request's session.
            """
            async with database() as session:
                yield session

        service = UserAuthService(
            user_model=_LazyUser,
            token_model=_LazyUserToken,  # type: ignore[arg-type]
            auth_settings=AuthSettings(_env_file=None, AUTH_AUTO_ACTIVATE=True),
            jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(make_auth_router(service, session_factory=get_session))
        return app, get_session

    async def test_a_get_session_with_sub_dependencies_works(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        app, _ = self._app(factory)
        body = {"email": "ana@example.com", "password": PASSWORD}
        async with _client(app) as client:
            signup = await client.post("/auth/signup", json=body)
            login = await client.post("/auth/login", json=body)

        assert signup.status_code == 201, signup.text
        assert login.status_code == 200, login.text

    async def test_dependency_overrides_reach_the_auth_routes(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        app, get_session = self._app(factory)
        seen: list[str] = []

        async def overridden() -> AsyncIterator[AsyncSession]:
            """Record the call and yield a session.

            Yields:
                AsyncSession: The request's session.
            """
            seen.append("override")
            async with factory() as session:
                yield session

        app.dependency_overrides[get_session] = overridden
        async with _client(app) as client:
            await client.post(
                "/auth/login", json={"email": "ghost@example.com", "password": PASSWORD}
            )

        assert seen == ["override"]
