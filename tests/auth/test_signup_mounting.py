"""``AUTH_SIGNUP_ENABLED`` / ``allow_signup`` — the closed-system switch.

A service where accounts are created by an administrator does not want
``POST /auth/signup`` reachable at all. Before v0.272.0 the only way out
was filtering ``router.routes`` by path string after the fact, which
matched a literal, said nothing to the OpenAPI schema, and went quiet the
day the path changed.

These tests pin the three things that make the switch worth having: the
route is gone from the application, it is gone from the schema, and
turning it off does not take activation down with it — an admin-created
account still has a way to become active.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
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


class _ClosedUser(BaseUserModel):
    __tablename__ = "closed_users"


_ClosedUserToken = make_user_token_model(
    user_table="closed_users",
    tablename="closed_user_tokens",
    class_name="_ClosedUserToken",
)

SIGNUP_PATH = "/auth/signup"
ACTIVATE_PATH = "/auth/activate/{token}"


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a session factory over one shared in-memory database."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _service(*, signup_enabled: bool = True) -> UserAuthService:
    """Build an auth service whose settings carry the signup switch.

    Args:
        signup_enabled (bool): The value of ``AUTH_SIGNUP_ENABLED``.

    Returns:
        UserAuthService: A service over the local closed-system model.
    """
    return UserAuthService(
        user_model=_ClosedUser,
        token_model=_ClosedUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            AUTH_SIGNUP_ENABLED=signup_enabled,
            AUTH_AUTO_ACTIVATE=True,
            AUTH_RETURN_TOKEN_IN_RESPONSE=True,
        ),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
    )


def _router(
    service: UserAuthService,
    factory: async_sessionmaker[AsyncSession],
    **router_kwargs: object,
) -> APIRouter:
    """Build the auth router over a per-request session factory."""

    async def sessions() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session
            await session.commit()

    return make_auth_router(service, session_factory=sessions, **router_kwargs)  # type: ignore[arg-type]


def _app(
    service: UserAuthService,
    factory: async_sessionmaker[AsyncSession],
    **router_kwargs: object,
) -> FastAPI:
    """Mount the auth router on a fresh application."""
    app = FastAPI()
    app.include_router(_router(service, factory, **router_kwargs))
    return app


def _paths(
    service: UserAuthService,
    factory: async_sessionmaker[AsyncSession],
    **router_kwargs: object,
) -> set[str]:
    """Return every path the auth router registers.

    Read off the router rather than ``app.routes``: FastAPI 0.141.1 keeps
    an included router as a single ``_IncludedRouter`` entry instead of
    flattening its routes into the application, so walking ``app.routes``
    finds no ``/auth/*`` path at all and every assertion built on it
    passes vacuously.
    """
    router = _router(service, factory, **router_kwargs)
    return {getattr(route, "path", "") for route in router.routes}


def _schema_paths(app: FastAPI) -> set[str]:
    """Return the paths the generated OpenAPI schema advertises."""
    return set(app.openapi()["paths"])


class TestSignupMountsByDefault:
    """Nothing changes for a project that never touches the switch."""

    def test_route_and_schema_carry_signup(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        assert SIGNUP_PATH in _paths(_service(), factory)
        assert SIGNUP_PATH in _schema_paths(_app(_service(), factory))

    async def test_the_endpoint_answers(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        app = _app(_service(), factory)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post(
                SIGNUP_PATH,
                json={"email": "open@example.com", "password": "strong-pass-12"},
            )

        assert response.status_code == 201, response.text


class TestSettingRemovesSignup:
    """``AUTH_SIGNUP_ENABLED=False`` takes the route out of the app."""

    def test_route_is_absent(self, factory: async_sessionmaker[AsyncSession]) -> None:
        assert SIGNUP_PATH not in _paths(_service(signup_enabled=False), factory)

    def test_openapi_schema_is_absent(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """The workaround this replaces filtered routes after mounting.

        Filtering ``router.routes`` leaves the operation in the generated
        schema unless the caller also rebuilds it, so the documented API
        keeps advertising a door that is no longer there.
        """
        app = _app(_service(signup_enabled=False), factory)

        assert SIGNUP_PATH not in _schema_paths(app)

    async def test_posting_to_it_is_404(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        app = _app(_service(signup_enabled=False), factory)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post(
                SIGNUP_PATH,
                json={"email": "closed@example.com", "password": "strong-pass-12"},
            )

        assert response.status_code == 404, response.text


class TestArgumentOverridesTheSetting:
    """``allow_signup`` wins over ``AUTH_SIGNUP_ENABLED``, in both directions."""

    def test_false_argument_beats_enabled_setting(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        paths = _paths(_service(signup_enabled=True), factory, allow_signup=False)

        assert SIGNUP_PATH not in paths

    def test_true_argument_beats_disabled_setting(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        paths = _paths(_service(signup_enabled=False), factory, allow_signup=True)

        assert SIGNUP_PATH in paths

    def test_none_argument_defers_to_the_setting(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        paths = _paths(_service(signup_enabled=False), factory, allow_signup=None)

        assert SIGNUP_PATH not in paths


class TestActivationSurvives:
    """Turning signup off must not strand accounts an admin created."""

    def test_activation_stays_mounted(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        assert ACTIVATE_PATH in _paths(_service(signup_enabled=False), factory)

    def test_every_other_route_is_untouched(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        open_paths = _paths(_service(), factory)
        closed_paths = _paths(_service(signup_enabled=False), factory)

        assert open_paths - closed_paths == {SIGNUP_PATH}
