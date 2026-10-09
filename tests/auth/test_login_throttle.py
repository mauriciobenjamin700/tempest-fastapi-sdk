"""Login and signup throttles on ``make_auth_router`` (#456).

Before this, ``POST /auth/login`` answered ``401`` to every wrong
password forever and ``POST /auth/signup`` created accounts without a
ceiling; only ``/auth/mfa/verify`` had a budget. These tests pin the
contract of ``login_throttle`` / ``login_ip_throttle`` /
``signup_throttle``: the attempt past the budget is a ``429`` with
``Retry-After`` even with the right password, a successful login clears
the e-mail budget, an e-mail without an account is throttled exactly
like one with an account, and the routes that do not authenticate by
password are untouched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    AttemptThrottle,
    BaseModel,
    BaseUserModel,
    InMemoryThrottleBackend,
    TokenDelivery,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


class _ThrottleUser(BaseUserModel):
    __tablename__ = "throttle_test_users"


_ThrottleUserToken = make_user_token_model(
    user_table="throttle_test_users",
    tablename="throttle_test_user_tokens",
    class_name="_ThrottleUserToken",
)

PASSWORD = "strong-pass-12-chars"
WRONG = "wrong-pass-12-chars!"
EMAIL = "ana@example.com"


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a session factory over one shared in-memory database."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _service(delivery: TokenDelivery = "bearer") -> UserAuthService:
    """Build an auto-activating auth service.

    Args:
        delivery (TokenDelivery): The token delivery mode.

    Returns:
        UserAuthService: The service.
    """
    return UserAuthService(
        user_model=_ThrottleUser,
        token_model=_ThrottleUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            AUTH_AUTO_ACTIVATE=True,
            AUTH_TOKEN_DELIVERY=delivery,
            AUTH_COOKIE_SECURE=False,
        ),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
        email=None,
    )


def _throttle(max_attempts: int) -> AttemptThrottle:
    """Build an in-memory throttle.

    Args:
        max_attempts (int): The budget.

    Returns:
        AttemptThrottle: The throttle.
    """
    return AttemptThrottle(
        InMemoryThrottleBackend(),
        max_attempts=max_attempts,
        window_seconds=900,
    )


def _app(
    factory: async_sessionmaker[AsyncSession],
    *,
    delivery: TokenDelivery = "bearer",
    **router_kwargs: Any,
) -> FastAPI:
    """Mount the auth router on a fresh application.

    Args:
        factory (async_sessionmaker[AsyncSession]): Session factory.
        delivery (TokenDelivery): The token delivery mode.
        **router_kwargs (Any): Forwarded to ``make_auth_router``.

    Returns:
        FastAPI: The app.
    """

    async def sessions() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_auth_router(_service(delivery), session_factory=sessions, **router_kwargs)
    )
    return app


def _client(app: FastAPI) -> AsyncClient:
    """Open an HTTP client over the app."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _signup(client: AsyncClient, email: str = EMAIL) -> Response:
    """Create an account through the router."""
    return await client.post(
        "/auth/signup", json={"email": email, "password": PASSWORD}
    )


async def _login(
    client: AsyncClient,
    password: str,
    *,
    email: str = EMAIL,
    path: str = "/auth/login",
    headers: dict[str, str] | None = None,
) -> Response:
    """Attempt a login."""
    return await client.post(
        path,
        json={"email": email, "password": password},
        headers=headers,
    )


class TestLoginThrottle:
    """The per-e-mail budget."""

    async def test_sixth_attempt_is_refused_even_with_the_right_password(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with _client(_app(factory, login_throttle=_throttle(5))) as client:
            await _signup(client)
            codes = [(await _login(client, WRONG)).status_code for _ in range(5)]
            refused = await _login(client, PASSWORD)
        assert codes == [401] * 5
        assert refused.status_code == 429
        assert refused.json()["code"] == "TOO_MANY_REQUESTS"
        assert int(refused.headers["retry-after"]) > 0
        assert refused.json()["details"]["retry_after_seconds"] == int(
            refused.headers["retry-after"]
        )

    async def test_default_budget_is_five(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with _client(_app(factory)) as client:
            await _signup(client)
            codes = [(await _login(client, WRONG)).status_code for _ in range(6)]
        assert codes == [401] * 5 + [429]

    async def test_success_before_the_limit_clears_the_count(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with _client(_app(factory, login_throttle=_throttle(3))) as client:
            await _signup(client)
            first = [(await _login(client, WRONG)).status_code for _ in range(2)]
            ok = await _login(client, PASSWORD)
            second = [(await _login(client, WRONG)).status_code for _ in range(4)]
        assert first == [401, 401]
        assert ok.status_code == 200
        assert second == [401, 401, 401, 429]

    async def test_unknown_email_is_throttled_the_same_way(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with _client(_app(factory, login_throttle=_throttle(2))) as client:
            await _signup(client)
            known = [(await _login(client, WRONG)).status_code for _ in range(3)]
            unknown = [
                (await _login(client, WRONG, email="ghost@example.com")).status_code
                for _ in range(3)
            ]
            known_refusal = (await _login(client, WRONG)).json()
            unknown_refusal = (
                await _login(client, WRONG, email="ghost@example.com")
            ).json()
        assert known == unknown == [401, 401, 429]
        assert known_refusal["detail"] == unknown_refusal["detail"]
        assert known_refusal["code"] == unknown_refusal["code"]

    async def test_email_is_normalized(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with _client(_app(factory, login_throttle=_throttle(1))) as client:
            await _signup(client)
            await _login(client, WRONG, email="ANA@example.com")
            refused = await _login(client, WRONG, email=" ana@EXAMPLE.com ")
        assert refused.status_code == 429

    async def test_false_turns_it_off(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with _client(_app(factory, login_throttle=False)) as client:
            await _signup(client)
            codes = [(await _login(client, WRONG)).status_code for _ in range(8)]
        assert codes == [401] * 8

    async def test_cookie_login_shares_the_budget(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        app = _app(factory, delivery="both", login_throttle=_throttle(3))
        async with _client(app) as client:
            await _signup(client)
            await _login(client, WRONG)
            await _login(client, WRONG, path="/auth/cookie/login")
            await _login(client, WRONG)
            refused = await _login(client, PASSWORD, path="/auth/cookie/login")
        assert refused.status_code == 429

    async def test_me_and_refresh_are_not_affected(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with _client(_app(factory, login_throttle=_throttle(1))) as client:
            await _signup(client)
            tokens = (await _login(client, PASSWORD)).json()
            await _login(client, WRONG)
            assert (await _login(client, PASSWORD)).status_code == 429
            me = await client.get(
                "/auth/me",
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            refreshed = await client.post(
                "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
            )
        assert me.status_code == 200
        assert refreshed.status_code == 200


class TestLoginIpThrottle:
    """The per-client failure budget, off by default."""

    async def test_many_emails_from_one_ip(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        app = _app(
            factory,
            login_ip_throttle=_throttle(3),
            trusted_ip_header="x-real-ip",
        )
        same_ip = {"x-real-ip": "10.0.0.1"}
        async with _client(app) as client:
            codes = [
                (
                    await _login(client, WRONG, email=f"u{n}@x.com", headers=same_ip)
                ).status_code
                for n in range(4)
            ]
            other_ip = await _login(
                client, WRONG, email="u9@x.com", headers={"x-real-ip": "10.0.0.2"}
            )
        assert codes == [401, 401, 401, 429]
        assert other_ip.status_code == 401

    async def test_success_does_not_clear_it(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        app = _app(factory, login_ip_throttle=_throttle(2))
        async with _client(app) as client:
            await _signup(client)
            await _login(client, WRONG, email="a@x.com")
            assert (await _login(client, PASSWORD)).status_code == 200
            await _login(client, WRONG, email="b@x.com")
            refused = await _login(client, WRONG, email="c@x.com")
        assert refused.status_code == 429


class TestSignupThrottle:
    """The per-client signup budget, off by default."""

    async def test_attempt_past_the_budget_is_refused(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        app = _app(factory, signup_throttle=_throttle(2), trusted_ip_header="x-real-ip")
        async with _client(app) as client:
            codes = [
                (
                    await client.post(
                        "/auth/signup",
                        json={"email": f"s{n}@x.com", "password": PASSWORD},
                        headers={"x-real-ip": "10.0.0.1"},
                    )
                ).status_code
                for n in range(3)
            ]
            other_ip = await client.post(
                "/auth/signup",
                json={"email": "s9@x.com", "password": PASSWORD},
                headers={"x-real-ip": "10.0.0.2"},
            )
        assert codes == [201, 201, 429]
        assert other_ip.status_code == 201

    async def test_off_by_default(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with _client(_app(factory)) as client:
            codes = [
                (await _signup(client, email=f"d{n}@x.com")).status_code
                for n in range(12)
            ]
        assert codes == [201] * 12


class TestOpenAPI:
    """The 429 is declared where it can happen, and only there."""

    def test_login_and_signup_declare_429(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        paths = _app(factory, signup_throttle=_throttle(2)).openapi()["paths"]
        assert "429" in paths["/auth/login"]["post"]["responses"]
        assert "429" in paths["/auth/signup"]["post"]["responses"]
        assert "429" not in paths["/auth/me"]["get"]["responses"]
        assert "429" not in paths["/auth/refresh"]["post"]["responses"]

    def test_no_throttle_no_429(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        paths = _app(factory, login_throttle=False).openapi()["paths"]
        assert "429" not in paths["/auth/login"]["post"]["responses"]
        assert "429" not in paths["/auth/signup"]["post"]["responses"]
