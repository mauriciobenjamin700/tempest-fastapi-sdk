"""Tests for :func:`make_traffic_router` and the live router's ``traffic=``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI, Header
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.exceptions import ForbiddenException
from tempest_fastapi_sdk.geo import (
    InMemoryTrafficSnapshotStore,
    LivePositionStore,
    MatchQualityOptions,
    TrafficAggregator,
    TrafficPrivacy,
    make_live_position_router,
    make_match_quality_router,
    make_traffic_router,
)
from tests.geo.traffic_helpers import T0, Clock, at, drive, east, straight_street

fakeredis = pytest.importorskip("fakeredis")


async def _authorize(x_user: str = Header(default="")) -> None:
    """Let only ``ops`` read traffic.

    Args:
        x_user (str): The ``X-User`` header.

    Raises:
        ForbiddenException: For anyone else.
    """
    if x_user != "ops":
        raise ForbiddenException()


def _box(x0: float, x1: float) -> dict[str, float]:
    """Query string of a box over the street from ``x0`` to ``x1`` metres.

    Args:
        x0 (float): West edge, metres east of the base.
        x1 (float): East edge.

    Returns:
        dict[str, float]: The four bounds.
    """
    a, b = at(x0, -50), at(x1, 50)
    return {
        "min_latitude": a.latitude,
        "max_latitude": b.latitude,
        "min_longitude": a.longitude,
        "max_longitude": b.longitude,
    }


async def _app_with_traffic() -> tuple[FastAPI, InMemoryTrafficSnapshotStore]:
    """An app serving a published snapshot of two devices on the street.

    Returns:
        tuple[FastAPI, InMemoryTrafficSnapshotStore]: App and store.
    """
    clock = Clock()
    store = InMemoryTrafficSnapshotStore()
    graph = straight_street()
    agg = TrafficAggregator(
        graph, privacy=TrafficPrivacy(min_devices=2), store=store, clock=clock
    )
    for n in range(2):
        drive(agg, clock, f"bus-{n}", east(0.0), 150, start=T0 + n * 5)
    await agg.flush()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(make_traffic_router(store, authorize=_authorize, graph=graph))
    return app, store


async def _get(
    app: FastAPI, path: str, params: dict[str, Any], user: str = "ops"
) -> Any:
    """GET with the ``X-User`` header.

    Args:
        app (FastAPI): The app.
        path (str): Path.
        params (dict[str, Any]): Query.
        user (str): Caller.

    Returns:
        Any: The ``httpx.Response``.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path, params=params, headers={"X-User": user})


class TestTrafficRouter:
    """``GET /traffic`` and ``GET /traffic/graph``."""

    async def test_box_filters_the_edges(self) -> None:
        """Only edges with an end inside the box come back."""
        app, _ = await _app_with_traffic()

        response = await _get(app, "/traffic", _box(450, 650))

        assert response.status_code == 200
        ids = {e["edge_id"] for e in response.json()["edges"]}
        assert ids == {"s-4", "s-5", "s-6"}
        assert response.json()["min_devices"] == 2

    async def test_no_snapshot_is_an_empty_list(self) -> None:
        """Before the first flush the answer is 200 with no edges."""
        app = FastAPI()
        app.include_router(
            make_traffic_router(InMemoryTrafficSnapshotStore(), authorize=_authorize)
        )

        response = await _get(app, "/traffic", _box(0, 100))

        assert response.status_code == 200
        assert response.json()["edges"] == []
        assert response.json()["generated_at"] is None

    async def test_inverted_box_is_422(self) -> None:
        """``min_latitude`` above ``max_latitude`` is refused."""
        app, _ = await _app_with_traffic()
        params = _box(0, 100)
        params["min_latitude"], params["max_latitude"] = (
            params["max_latitude"],
            params["min_latitude"],
        )

        response = await _get(app, "/traffic", params)

        assert response.status_code == 422

    async def test_authorize_guards_every_route(self) -> None:
        """Someone else gets 403 on both routes."""
        app, _ = await _app_with_traffic()

        assert (await _get(app, "/traffic", _box(0, 100), "x")).status_code == 403
        assert (await _get(app, "/traffic/graph", _box(0, 100), "x")).status_code == 403

    async def test_graph_tile_in_react_shape(self) -> None:
        """The tile uses ``from``/``to``/``twoWay`` and keeps edge ids."""
        app, _ = await _app_with_traffic()

        body = (await _get(app, "/traffic/graph", _box(150, 250))).json()

        assert {e["id"] for e in body["edges"]} == {"s-1", "s-2"}
        assert set(body["edges"][0]) == {"from", "to", "twoWay", "id"}
        assert body["twoWay"] is False

    def test_authorize_is_mandatory(self) -> None:
        """No authorize, no router."""
        with pytest.raises(ValueError, match="authorize"):
            make_traffic_router(InMemoryTrafficSnapshotStore(), authorize=None)  # type: ignore[arg-type]

    def test_graph_route_only_with_a_graph(self) -> None:
        """Without ``graph`` only the traffic route is mounted."""
        router = make_traffic_router(
            InMemoryTrafficSnapshotStore(), authorize=_authorize
        )

        assert [r.path for r in router.routes] == ["/traffic"]  # type: ignore[attr-defined]


class TestMatchQualityRouter:
    """``GET /traffic/quality``: the last closed window and the references."""

    @staticmethod
    async def _app(*, quality: bool = True) -> FastAPI:
        """An app whose store holds a snapshot with a closed quality window.

        Args:
            quality (bool): Whether the aggregator measures quality.

        Returns:
            FastAPI: The app, with the traffic and the quality routers.
        """
        clock = Clock(T0)
        store = InMemoryTrafficSnapshotStore()
        agg = TrafficAggregator(
            straight_street(),
            privacy=TrafficPrivacy(min_devices=2),
            quality=MatchQualityOptions(window_seconds=3600) if quality else None,
            store=store,
            clock=clock,
        )
        for n in range(2):
            drive(agg, clock, f"bus-{n}", east(0.0), 150, start=T0 + n * 300)
        clock.now = T0 + 3601
        await agg.flush()
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(make_traffic_router(store, authorize=_authorize))
        app.include_router(make_match_quality_router(store, authorize=_authorize))
        return app

    async def test_answers_the_window_and_the_references(self) -> None:
        """The aggregate of the closed window comes with Kubicka and Brazil."""
        app = await self._app()

        response = await _get(app, "/traffic/quality", {})

        assert response.status_code == 200
        body = response.json()
        assert body["snapshot"]["overall"]["devices"] == 2
        assert body["snapshot"]["overall"]["matched_share"] == 1.0
        assert [r["name"] for r in body["references"]] == ["kubicka", "brazil-osm"]

    async def test_traffic_route_carries_no_quality(self) -> None:
        """The box answer leaves the quality out."""
        app = await self._app()

        body = (await _get(app, "/traffic", _box(0, 3000))).json()

        assert body["quality"] is None

    async def test_without_quality_the_snapshot_is_empty(self) -> None:
        """An aggregator without ``quality=`` reads as an empty snapshot."""
        app = await self._app(quality=False)

        body = (await _get(app, "/traffic/quality", {})).json()

        assert body["snapshot"]["window_start"] is None
        assert body["snapshot"]["overall"] is None
        assert len(body["references"]) == 2

    async def test_authorize_guards_the_route(self) -> None:
        """Someone else gets 403."""
        app = await self._app()

        assert (await _get(app, "/traffic/quality", {}, "x")).status_code == 403

    async def test_store_factory_is_called_per_request(self) -> None:
        """A callable store is resolved with the request."""
        app = FastAPI()
        store = InMemoryTrafficSnapshotStore()
        app.include_router(
            make_match_quality_router(lambda _request: store, authorize=_authorize)
        )

        body = (await _get(app, "/traffic/quality", {})).json()

        assert body["snapshot"]["window_start"] is None

    def test_authorize_is_mandatory(self) -> None:
        """No authorize, no router."""
        with pytest.raises(ValueError, match="authorize"):
            make_match_quality_router(InMemoryTrafficSnapshotStore(), authorize=None)  # type: ignore[arg-type]


class TestLiveRouterFeedsTraffic:
    """``make_live_position_router(traffic=...)``."""

    async def test_every_active_position_reaches_the_aggregator(self) -> None:
        """Throttled by the store or not, the aggregator sees each position."""
        clock = Clock(datetime.now(UTC).timestamp())
        agg = TrafficAggregator(straight_street(), clock=clock)
        store = LivePositionStore(fakeredis.aioredis.FakeRedis(decode_responses=True))

        async def allow(channel_id: str) -> None:
            """Anyone may publish.

            Args:
                channel_id (str): The channel.
            """

        async def active(channel_id: str) -> bool:
            """Every channel is active.

            Args:
                channel_id (str): The channel.

            Returns:
                bool: Always ``True``.
            """
            return True

        app = FastAPI()
        app.include_router(
            make_live_position_router(
                store, authorize=allow, channel_active=active, traffic=agg
            )
        )
        base = datetime.now(UTC) - timedelta(seconds=60)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            for s in range(3):
                point = at(300 + 10 * s, 0)
                await c.post(
                    "/live/bus-9/position",
                    json={
                        "lat": point.latitude,
                        "lng": point.longitude,
                        "recorded_at": (base + timedelta(seconds=s)).isoformat(),
                    },
                )

        assert agg.active_trips == 1
        trip = next(iter(agg._trips.values()))
        assert trip.last.t == pytest.approx((base + timedelta(seconds=2)).timestamp())

    @staticmethod
    async def _post_with_os(header: str) -> TrafficAggregator:
        """Post three positions with an ``X-OS`` header through the live router.

        Args:
            header (str): The ``X-OS`` value.

        Returns:
            TrafficAggregator: The aggregator the router fed.
        """
        clock = Clock(datetime.now(UTC).timestamp())
        agg = TrafficAggregator(
            straight_street(),
            quality=MatchQualityOptions(dimensions=("os",)),
            clock=clock,
        )
        store = LivePositionStore(fakeredis.aioredis.FakeRedis(decode_responses=True))

        async def allow(channel_id: str) -> None:
            """Anyone may publish.

            Args:
                channel_id (str): The channel.
            """

        async def active(channel_id: str) -> bool:
            """Every channel is active.

            Args:
                channel_id (str): The channel.

            Returns:
                bool: Always ``True``.
            """
            return True

        async def os_of(x_os: str = Header(default="")) -> dict[str, str] | None:
            """The OS family the app reports.

            Args:
                x_os (str): The ``X-OS`` header.

            Returns:
                dict[str, str] | None: The dimension, or ``None``.
            """
            return {"os": x_os} if x_os else None

        app = FastAPI()
        app.include_router(
            make_live_position_router(
                store,
                authorize=allow,
                channel_active=active,
                traffic=agg,
                traffic_dimensions=os_of,
            )
        )
        base = datetime.now(UTC) - timedelta(seconds=60)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            for s in range(3):
                point = at(300 + 10 * s, 0)
                response = await c.post(
                    "/live/bus-9/position",
                    json={
                        "lat": point.latitude,
                        "lng": point.longitude,
                        "recorded_at": (base + timedelta(seconds=s)).isoformat(),
                    },
                    headers={"X-OS": header},
                )
                assert response.status_code == 200
        return agg

    async def test_dimensions_reach_the_trip(self) -> None:
        """``traffic_dimensions`` sets the trip's quality cell."""
        agg = await self._post_with_os("ios")

        trip = next(iter(agg._trips.values()))
        assert trip.cell == ("ios",)

    async def test_refused_dimensions_still_aggregate_the_position(self) -> None:
        """A value too long is dropped; the position is not lost."""
        agg = await self._post_with_os("x" * 40)

        trip = next(iter(agg._trips.values()))
        assert trip.cell == ()
        assert trip.last.t > 0

    def test_dimensions_need_traffic(self) -> None:
        """A resolver without an aggregator is refused."""

        async def nothing() -> None:
            """Neither authorizes nor resolves anything."""

        async def os_of() -> dict[str, str] | None:
            """No dimensions.

            Returns:
                dict[str, str] | None: ``None``.
            """
            return None

        with pytest.raises(ValueError, match="traffic"):
            make_live_position_router(
                InMemoryTrafficSnapshotStore,  # type: ignore[arg-type]
                authorize=nothing,
                channel_active=nothing,  # type: ignore[arg-type]
                traffic_dimensions=os_of,
            )
