"""Every router's ``session_factory`` is a real FastAPI dependency (#524).

#493 made ``make_auth_router`` register ``Depends(session_factory)`` when it
is a generator function. The push, web-push, chat, reviews, wallet,
server-side session, voice and app-error routers kept iterating
``session_factory()`` by hand, so a ``get_session(request: Request)`` died
with ``TypeError: get_session() missing 1 required positional argument`` and
``app.dependency_overrides[get_session]`` never reached their routes.

The session never has to be real to prove it: each router hands the session
it resolved to the caller's code (``service_factory``, or a service method
for the routers that take a service), and the stub here raises it back out
as :class:`_ReachedError`, which an exception handler turns into the session's
``source``. What source answers is what FastAPI resolved.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    make_push_router,
    make_session_router,
    make_web_push_router,
)
from tempest_fastapi_sdk.api.dependencies._session import session_provider_for
from tempest_fastapi_sdk.app_errors import make_app_error_router
from tempest_fastapi_sdk.chat import make_chat_router
from tempest_fastapi_sdk.genai.audio import make_voice_router
from tempest_fastapi_sdk.reviews import make_reviews_router
from tempest_fastapi_sdk.wallet import make_wallet_router

USER_ID: UUID = uuid4()


@dataclass
class _FakeSession:
    """Stand-in for an ``AsyncSession`` that names where it came from.

    Attributes:
        source (str): Which provider yielded it.
    """

    source: str


def _fake(source: str) -> AsyncSession:
    """Build a fake session typed as the routers expect it.

    Args:
        source (str): Which provider yielded it.

    Returns:
        AsyncSession: A :class:`_FakeSession`, cast for the type checker.
    """
    return cast(AsyncSession, _FakeSession(source=source))


class _ReachedError(Exception):
    """Raised by the stub service with the session the router resolved."""

    def __init__(self, session: Any) -> None:
        """Keep the session.

        Args:
            session (Any): What the router passed down.
        """
        super().__init__("reached")
        self.session = session


def _reach(session: Any, *_args: Any, **_kwargs: Any) -> Any:
    """Stop the request with the session the router passed down.

    Args:
        session (Any): The resolved session.
        *_args (Any): Ignored.
        **_kwargs (Any): Ignored.

    Raises:
        _ReachedError: Always.
    """
    raise _ReachedError(session)


async def _reach_async(session: Any, *_args: Any, **_kwargs: Any) -> Any:
    """Async variant of :func:`_reach`, for service methods.

    Args:
        session (Any): The resolved session.
        *_args (Any): Ignored.
        **_kwargs (Any): Ignored.

    Raises:
        _ReachedError: Always.
    """
    raise _ReachedError(session)


def _current_user_id() -> UUID:
    """Resolve the fixed caller.

    Returns:
        UUID: The caller.
    """
    return USER_ID


def _anything() -> None:
    """Satisfy a dependency the request never reaches."""
    return None


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """A project's ``get_session`` that needs the request.

    Args:
        request (Request): Filled by FastAPI — and only by FastAPI.

    Yields:
        AsyncSession: A :class:`_FakeSession` tagged with the request's method.
    """
    yield _fake(f"get_session:{request.method}")


async def _legacy_iterator() -> AsyncIterator[AsyncSession]:
    """The async iterator a pre-#524 zero-argument factory returns.

    Yields:
        AsyncSession: A :class:`_FakeSession` tagged ``legacy``.
    """
    yield _fake("legacy")


class _Database:
    """Mimics ``AsyncDatabaseManager``: ``session_dependency`` is a method."""

    async def session_dependency(self) -> AsyncIterator[AsyncSession]:
        """Yield a session the way the manager's bound method does.

        Yields:
            AsyncSession: A :class:`_FakeSession` tagged ``manager``.
        """
        yield _fake("manager")


@dataclass(frozen=True)
class _Case:
    """One router and one request that reaches its session.

    Attributes:
        name (str): Test id.
        build (Callable[[Callable[..., Any]], APIRouter]): Builds the router
            over the given ``session_factory``.
        method (str): HTTP method of the probe request.
        path (str): Path of the probe request.
        json (dict[str, Any] | None): Body of the probe request.
    """

    name: str
    build: Callable[[Callable[..., Any]], APIRouter]
    method: str
    path: str
    json: dict[str, Any] | None = None


def _session_service() -> Any:
    """Stub :class:`SessionAuth`: ``authenticate`` gets the DB session.

    Returns:
        Any: Enough of the service for ``make_session_router``.
    """
    return SimpleNamespace(
        user_model=object(),
        settings=SimpleNamespace(),
        authenticate=_reach_async,
    )


CONVERSATION_ID: UUID = uuid4()

CASES: list[_Case] = [
    _Case(
        name="push",
        build=lambda sf: make_push_router(
            service_factory=_reach,
            session_factory=sf,
            current_user_id=_current_user_id,
        ),
        method="POST",
        path="/api/push/register",
        json={"token": "android-token", "platform": "android"},
    ),
    _Case(
        name="webpush",
        build=lambda sf: make_web_push_router(
            service_factory=_reach,
            session_factory=sf,
            current_user_id=_current_user_id,
        ),
        method="POST",
        path="/api/push/subscribe",
        json={
            "endpoint": "https://push.example/sub",
            "keys": {"p256dh": "p256dh-key", "auth": "auth-secret"},
        },
    ),
    _Case(
        name="chat",
        build=lambda sf: make_chat_router(
            service_factory=_reach,
            session_factory=sf,
            current_user_id=_current_user_id,
            membership_recheck_seconds=None,
        ),
        method="GET",
        path="/api/chat/conversations",
    ),
    _Case(
        name="chat-stream",
        build=lambda sf: make_chat_router(
            service_factory=_reach,
            session_factory=sf,
            current_user_id=_current_user_id,
            membership_recheck_seconds=None,
        ),
        method="GET",
        path=f"/api/chat/conversations/{CONVERSATION_ID}/stream",
    ),
    _Case(
        name="reviews",
        build=lambda sf: make_reviews_router(
            service_factory=_reach,
            session_factory=sf,
            current_user_id=_current_user_id,
        ),
        method="GET",
        path=f"/api/reviews/product/{uuid4()}/comments",
    ),
    _Case(
        name="wallet",
        build=lambda sf: make_wallet_router(
            service_factory=_reach,
            session_factory=sf,
            current_user_id=_current_user_id,
            payout_provider=_anything,
            pix_destination=_anything,
        ),
        method="GET",
        path="/api/wallet/balance",
    ),
    _Case(
        name="app-errors",
        build=lambda sf: make_app_error_router(
            service_factory=_reach,
            session_factory=sf,
        ),
        method="POST",
        path="/api/app-errors",
        json={"code": "E_BOOT", "message": "boom"},
    ),
    _Case(
        name="sessions",
        build=lambda sf: make_session_router(
            _session_service(),
            session_factory=sf,
        ),
        method="POST",
        path="/auth/session/login",
        json={"email": "a@example.com", "password": "secret-password"},
    ),
    _Case(
        name="voice",
        build=lambda sf: make_voice_router(
            session_factory=sf,
            profiles=SimpleNamespace(list_profiles=_reach_async),  # type: ignore[arg-type]
            current_user_id=_current_user_id,
        ),
        method="GET",
        path="/voice/profiles",
    ),
]


def _app(router: APIRouter) -> FastAPI:
    """Mount ``router`` with a handler that reports the reached session.

    Args:
        router (APIRouter): The router under test.

    Returns:
        FastAPI: The app.
    """
    app = FastAPI()

    async def _on_reached(_request: Request, exc: Exception) -> JSONResponse:
        """Answer with the source of the session the router resolved.

        Args:
            _request (Request): Unused.
            exc (Exception): The :class:`_ReachedError` raised by the stub.

        Returns:
            JSONResponse: ``{"source": ...}``.
        """
        assert isinstance(exc, _ReachedError)
        return JSONResponse({"source": exc.session.source})

    app.add_exception_handler(_ReachedError, _on_reached)
    app.include_router(router)
    return app


async def _probe(app: FastAPI, case: _Case) -> Any:
    """Send the case's request.

    Args:
        app (FastAPI): The app.
        case (_Case): Which request.

    Returns:
        Any: The httpx response.
    """
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(case.method, case.path, json=case.json)


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
class TestRouterSessionFactory:
    async def test_a_get_session_that_needs_the_request_works(
        self, case: _Case
    ) -> None:
        """Before #524 this was a 500 from ``TypeError: missing 'request'``."""
        response = await _probe(_app(case.build(get_session)), case)

        assert response.status_code == 200, response.text
        assert response.json() == {"source": f"get_session:{case.method}"}

    async def test_dependency_overrides_reach_the_routes(self, case: _Case) -> None:
        """The override is what the route resolves, not the original."""

        async def overridden() -> AsyncIterator[AsyncSession]:
            yield _fake("override")

        app = _app(case.build(get_session))
        app.dependency_overrides[get_session] = overridden

        response = await _probe(app, case)

        assert response.json() == {"source": "override"}

    async def test_a_bound_session_dependency_still_works(self, case: _Case) -> None:
        """``db.session_dependency`` is passed exactly as before."""
        response = await _probe(_app(case.build(_Database().session_dependency)), case)

        assert response.json() == {"source": "manager"}

    async def test_a_zero_argument_factory_is_still_iterated(self, case: _Case) -> None:
        """A plain callable returning an async iterator keeps working."""
        response = await _probe(_app(case.build(lambda: _legacy_iterator())), case)

        assert response.json() == {"source": "legacy"}


class TestSessionProviderFor:
    def test_a_generator_function_is_registered_as_is(self) -> None:
        """Returned unchanged, so it is the key ``dependency_overrides`` uses."""
        assert session_provider_for(get_session) is get_session

    def test_a_plain_callable_is_wrapped(self) -> None:
        """Wrapped in a generator FastAPI can resolve."""

        def factory() -> AsyncIterator[AsyncSession]:
            return _legacy_iterator()

        assert session_provider_for(factory) is not factory


class TestChatStreamRecheck:
    def test_a_parameterised_session_factory_needs_a_stream_factory(self) -> None:
        """The re-check runs mid-stream, where nobody can fill ``request``."""
        with pytest.raises(ValueError, match="stream_session_factory"):
            make_chat_router(
                service_factory=_reach,
                session_factory=get_session,
                current_user_id=_current_user_id,
            )

    def test_a_stream_factory_lifts_the_restriction(self) -> None:
        """Passing a zero-argument factory for the re-check is enough."""
        router = make_chat_router(
            service_factory=_reach,
            session_factory=get_session,
            current_user_id=_current_user_id,
            stream_session_factory=_Database().session_dependency,
        )

        assert router.routes
