"""Tests for :func:`make_live_position_router`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI, Header
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.exceptions import ForbiddenException
from tempest_fastapi_sdk.geo import LivePositionStore, make_live_position_router

fakeredis = pytest.importorskip("fakeredis")

ACTIVE: dict[str, bool] = {}
"""Channel id -> whether it accepts positions, per test."""

SEEN: list[str] = []
"""Channel ids the dependencies received, in order."""


async def _authorize(channel_id: str, x_user: str = Header(default="")) -> None:
    """Allow only the driver of the channel.

    Args:
        channel_id (str): The path parameter.
        x_user (str): Who is calling, from the ``X-User`` header.

    Raises:
        ForbiddenException: When the caller is not ``driver-<channel_id>``.
    """
    SEEN.append(channel_id)
    if x_user != f"driver-{channel_id}":
        raise ForbiddenException()


async def _channel_active(channel_id: str) -> bool:
    """Answer from :data:`ACTIVE`.

    Args:
        channel_id (str): The path parameter.

    Returns:
        bool: Whether the channel is active.
    """
    return ACTIVE.get(channel_id, False)


def _body(offset_s: float = 0.0) -> dict[str, Any]:
    """Build a JSON position.

    Args:
        offset_s (float): Seconds added to a base a minute ago.

    Returns:
        dict[str, Any]: The request body.
    """
    recorded = datetime.now(UTC) - timedelta(seconds=60 - offset_s)
    return {"lat": -5.0892, "lng": -42.8019, "recorded_at": recorded.isoformat()}


@pytest.fixture
def store() -> LivePositionStore:
    """Return a silent store over fakeredis, with state reset.

    Returns:
        LivePositionStore: The store.
    """
    ACTIVE.clear()
    SEEN.clear()
    return LivePositionStore(fakeredis.aioredis.FakeRedis(decode_responses=True))


def _app(store: LivePositionStore) -> FastAPI:
    """Mount the router on an app with the SDK's exception handlers.

    Args:
        store (LivePositionStore): The store.

    Returns:
        FastAPI: The app.
    """
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_live_position_router(
            store, authorize=_authorize, channel_active=_channel_active
        )
    )
    return app


async def _post(app: FastAPI, channel_id: str, user: str, body: dict[str, Any]) -> Any:
    """POST a position.

    Args:
        app (FastAPI): The app.
        channel_id (str): The channel.
        user (str): The ``X-User`` header.
        body (dict[str, Any]): The JSON body.

    Returns:
        Any: The ``httpx.Response``.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            f"/live/{channel_id}/position", json=body, headers={"X-User": user}
        )


class TestLivePositionRouter:
    """Behavior of the bundled route."""

    async def test_active_channel_accepts(self, store: LivePositionStore) -> None:
        """The driver of an active trip publishes; the answer has no position."""
        ACTIVE["trip-1"] = True
        response = await _post(_app(store), "trip-1", "driver-trip-1", _body())

        assert response.status_code == 200
        assert response.json() == {"accepted": True}
        assert await store.latest("trip-1") is not None
        assert SEEN == ["trip-1"]

    async def test_second_position_throttled(self, store: LivePositionStore) -> None:
        """A throttled position is ``accepted: false``, not an error."""
        ACTIVE["trip-1"] = True
        app = _app(store)
        await _post(app, "trip-1", "driver-trip-1", _body())
        response = await _post(app, "trip-1", "driver-trip-1", _body(1))
        assert response.status_code == 200
        assert response.json() == {"accepted": False}

    async def test_inactive_channel_is_409(self, store: LivePositionStore) -> None:
        """An inactive channel answers 409 and stores nothing."""
        response = await _post(_app(store), "trip-1", "driver-trip-1", _body())

        assert response.status_code == 409
        assert await store.latest("trip-1") is None

    async def test_unauthorized_publisher_is_403(
        self, store: LivePositionStore
    ) -> None:
        """Someone else's position is refused before anything is stored."""
        ACTIVE["trip-1"] = True
        response = await _post(_app(store), "trip-1", "passenger", _body())

        assert response.status_code == 403
        assert await store.latest("trip-1") is None

    async def test_clear_then_inactive_stops_delivery(
        self, store: LivePositionStore
    ) -> None:
        """After the trip ends and is cleared, positions are refused."""
        ACTIVE["trip-1"] = True
        app = _app(store)
        await _post(app, "trip-1", "driver-trip-1", _body())
        ACTIVE["trip-1"] = False
        await store.clear("trip-1")

        response = await _post(app, "trip-1", "driver-trip-1", _body(30))
        assert response.status_code == 409
        assert await store.latest("trip-1") is None

    async def test_store_factory(self, store: LivePositionStore) -> None:
        """A factory resolving the store from the request works the same."""
        ACTIVE["trip-1"] = True
        app = FastAPI()
        register_exception_handlers(app)
        app.state.live = store
        app.include_router(
            make_live_position_router(
                lambda request: request.app.state.live,
                authorize=_authorize,
                channel_active=_channel_active,
            )
        )
        response = await _post(app, "trip-1", "driver-trip-1", _body())
        assert response.json() == {"accepted": True}

    def test_authorize_is_mandatory(self, store: LivePositionStore) -> None:
        """Without ``authorize`` the router is not built."""
        with pytest.raises(ValueError, match="authorize"):
            make_live_position_router(
                store,
                authorize=None,  # type: ignore[arg-type]
                channel_active=_channel_active,
            )

    def test_channel_active_is_mandatory(self, store: LivePositionStore) -> None:
        """Without ``channel_active`` the router is not built."""
        with pytest.raises(ValueError, match="channel_active"):
            make_live_position_router(
                store,
                authorize=_authorize,
                channel_active=None,  # type: ignore[arg-type]
            )

    def test_route_is_mounted(self, store: LivePositionStore) -> None:
        """The router carries exactly the publish route."""
        router = make_live_position_router(
            store, authorize=_authorize, channel_active=_channel_active
        )
        assert [route.path for route in router.routes] == [
            "/live/{channel_id}/position"
        ]
