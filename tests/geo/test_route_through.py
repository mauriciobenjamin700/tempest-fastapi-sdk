"""Tests for routes through stops: OSRM, the offline fallback and the fake."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from tempest_fastapi_sdk.geo import (
    DEFAULT_OSRM_MAX_POINTS,
    Coordinate,
    OSRMBackend,
    RouteThroughResult,
    RoutingBackend,
    TravelModeEnum,
    decode_polyline,
    encode_polyline,
    estimate_route_through,
    estimate_travel,
)
from tempest_fastapi_sdk.testing.fakes import FakeRoutingBackend

TERESINA = Coordinate(latitude=-5.0892, longitude=-42.8019)
ALTOS = Coordinate(latitude=-5.0381, longitude=-42.4608)
CAMPO_MAIOR = Coordinate(latitude=-4.8278, longitude=-42.1686)
POINTS: list[Coordinate] = [TERESINA, ALTOS, CAMPO_MAIOR]


def _osrm_route_handler(
    seen: list[httpx.Request],
) -> Callable[[httpx.Request], httpx.Response]:
    """Build a fake OSRM ``/route`` server that answers one leg per stretch.

    Every leg is 10 km and 600 s per stretch index plus one (leg ``i`` is
    ``10 * (i + 1)`` km), and the total is the sum, as a real OSRM answers.
    The geometry is the requested points encoded as a polyline.

    Args:
        seen (list[httpx.Request]): Collects every request served.

    Returns:
        Callable[[httpx.Request], httpx.Response]: The handler for
        ``httpx.MockTransport``.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        """Answer a ``/route`` request like OSRM does.

        Args:
            request (httpx.Request): The request.

        Returns:
            httpx.Response: The OSRM-shaped answer.
        """
        seen.append(request)
        raw = request.url.path.rsplit("/", 1)[-1]
        pairs = [chunk.split(",") for chunk in raw.split(";")]
        points = [
            Coordinate(latitude=float(lat), longitude=float(lon)) for lon, lat in pairs
        ]
        legs: list[dict[str, Any]] = [
            {"distance": 10_000.0 * (i + 1), "duration": 600.0 * (i + 1)}
            for i in range(len(points) - 1)
        ]
        route: dict[str, Any] = {
            "distance": sum(leg["distance"] for leg in legs),
            "duration": sum(leg["duration"] for leg in legs),
            "legs": legs,
        }
        if request.url.params.get("overview") == "full":
            route["geometry"] = encode_polyline(points)
        return httpx.Response(200, json={"code": "Ok", "routes": [route]})

    return handler


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    """Build an AsyncClient served by ``handler``.

    Args:
        handler (Callable[[httpx.Request], httpx.Response]): The fake server.

    Returns:
        httpx.AsyncClient: The client.
    """
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestOSRMRouteThrough:
    """Behavior of :meth:`OSRMBackend.route_through`."""

    async def test_three_points_one_request_two_legs(self) -> None:
        """A -> B -> C is one request; the two legs add up to the total."""
        seen: list[httpx.Request] = []
        async with _client(_osrm_route_handler(seen)) as client:
            result = await OSRMBackend(http_client=client).route_through(POINTS)

        assert len(seen) == 1
        assert isinstance(result, RouteThroughResult)
        assert result.source == "osrm"
        assert len(result.legs) == 2
        assert [leg.distance_km for leg in result.legs] == pytest.approx([10.0, 20.0])
        assert result.distance_km == pytest.approx(
            sum(leg.distance_km for leg in result.legs)
        )
        assert result.duration_minutes == pytest.approx(
            sum(leg.duration_minutes for leg in result.legs)
        )
        assert all(leg.source == "osrm" for leg in result.legs)
        assert result.geometry == []

    async def test_sends_every_point_in_lon_lat_order(self) -> None:
        """The path carries all points, ``lon,lat`` separated by ``;``."""
        seen: list[httpx.Request] = []
        async with _client(_osrm_route_handler(seen)) as client:
            await OSRMBackend(http_client=client).route_through(POINTS)

        assert seen[0].url.path.endswith(
            "/route/v1/driving/-42.8019,-5.0892;-42.4608,-5.0381;-42.1686,-4.8278"
        )
        assert seen[0].url.params["overview"] == "false"

    async def test_with_geometry_decodes_the_whole_route(self) -> None:
        """``with_geometry=True`` asks for the full overview and decodes it."""
        seen: list[httpx.Request] = []
        async with _client(_osrm_route_handler(seen)) as client:
            result = await OSRMBackend(http_client=client).route_through(
                POINTS, with_geometry=True
            )

        assert seen[0].url.params["overview"] == "full"
        assert len(result.geometry) == 3
        assert result.geometry[0].latitude == pytest.approx(TERESINA.latitude)
        assert result.geometry[-1].longitude == pytest.approx(CAMPO_MAIOR.longitude)

    async def test_mode_factor_scales_total_and_legs(self) -> None:
        """Bus keeps distances and scales every duration like ``route``."""
        seen: list[httpx.Request] = []
        async with _client(_osrm_route_handler(seen)) as client:
            backend = OSRMBackend(http_client=client)
            car = await backend.route_through(POINTS, mode=TravelModeEnum.CAR)
            bus = await backend.route_through(POINTS, mode=TravelModeEnum.BUS)

        assert bus.distance_km == pytest.approx(car.distance_km)
        assert bus.duration_minutes == pytest.approx(car.duration_minutes * 1.6)
        assert bus.legs[1].duration_minutes == pytest.approx(
            car.legs[1].duration_minutes * 1.6
        )

    async def test_fewer_than_two_points_refused_before_request(self) -> None:
        """One point is a ``ValueError`` and no request leaves."""
        seen: list[httpx.Request] = []
        async with _client(_osrm_route_handler(seen)) as client:
            with pytest.raises(ValueError, match="at least two points"):
                await OSRMBackend(http_client=client).route_through([TERESINA])

        assert seen == []

    async def test_above_max_points_refused_before_request(self) -> None:
        """More points than ``max_points`` is a ``ValueError`` naming the cap."""
        seen: list[httpx.Request] = []
        async with _client(_osrm_route_handler(seen)) as client:
            backend = OSRMBackend(http_client=client, max_points=3)
            with pytest.raises(ValueError, match="at most 3"):
                await backend.route_through([*POINTS, TERESINA])

        assert seen == []

    def test_default_cap_matches_public_demo(self) -> None:
        """The default cap is the 500 points the public demo accepts."""
        backend = OSRMBackend(http_client=_client(lambda r: httpx.Response(200)))
        assert DEFAULT_OSRM_MAX_POINTS == 500
        assert backend.max_points == DEFAULT_OSRM_MAX_POINTS

    def test_max_points_below_two_refused(self) -> None:
        """A cap under two could never route anything."""
        with pytest.raises(ValueError, match="max_points"):
            OSRMBackend(
                http_client=_client(lambda r: httpx.Response(200)), max_points=1
            )

    async def test_too_big_answer_is_runtime_error(self) -> None:
        """The server's ``TooBig`` refusal surfaces as ``RuntimeError``."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"code": "TooBig", "message": "Number of entries 501 is higher"},
            )

        async with _client(handler) as client:
            with pytest.raises(RuntimeError, match="OSRM request failed"):
                await OSRMBackend(http_client=client).route_through(POINTS)

    async def test_leg_count_mismatch_is_runtime_error(self) -> None:
        """An answer without one leg per stretch is refused, not trusted."""

        def handler(request: httpx.Request) -> httpx.Response:
            route = {"distance": 1000.0, "duration": 60.0, "legs": []}
            return httpx.Response(200, json={"code": "Ok", "routes": [route]})

        async with _client(handler) as client:
            with pytest.raises(RuntimeError, match="0 legs for 3 points"):
                await OSRMBackend(http_client=client).route_through(POINTS)

    @pytest.mark.parametrize(
        "route",
        [
            {"legs": [{}, {}]},
            {"distance": 1.0, "duration": 1.0, "legs": [{"distance": 1.0}, {}]},
        ],
    )
    async def test_ok_answer_missing_fields_is_runtime_error(
        self, route: dict[str, Any]
    ) -> None:
        """An ``"Ok"`` route without distance/duration is the backend's error."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": "Ok", "routes": [route]})

        async with _client(handler) as client:
            with pytest.raises(RuntimeError, match="unexpected route"):
                await OSRMBackend(http_client=client).route_through(POINTS)

    async def test_route_ok_answer_missing_fields_is_runtime_error(self) -> None:
        """``route`` normalises the same malformed answer."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": "Ok", "routes": [{}]})

        async with _client(handler) as client:
            with pytest.raises(RuntimeError, match="unexpected route"):
                await OSRMBackend(http_client=client).route(POINTS[0], POINTS[1])


class TestEstimateRouteThrough:
    """Behavior of the offline fallback :func:`estimate_route_through`."""

    def test_legs_are_estimate_travel_and_sum_to_total(self) -> None:
        """Each leg is ``estimate_travel`` for its pair; totals are sums."""
        result = estimate_route_through(POINTS)

        first = estimate_travel(TERESINA, ALTOS)
        assert result.source == "heuristic"
        assert len(result.legs) == 2
        assert result.legs[0].distance_km == pytest.approx(first.distance_km)
        assert result.distance_km == pytest.approx(
            sum(leg.distance_km for leg in result.legs)
        )
        assert result.duration_minutes == pytest.approx(
            sum(leg.duration_minutes for leg in result.legs)
        )

    def test_geometry_is_straight_lines_through_points(self) -> None:
        """The offline geometry is the points themselves, only on request."""
        assert estimate_route_through(POINTS).geometry == []
        assert estimate_route_through(POINTS, with_geometry=True).geometry == POINTS

    def test_mode_is_forwarded(self) -> None:
        """The mode reaches every leg."""
        bus = estimate_route_through(POINTS, TravelModeEnum.BUS)
        car = estimate_route_through(POINTS, TravelModeEnum.CAR)
        assert bus.duration_minutes == pytest.approx(car.duration_minutes * 1.6)
        assert all(leg.mode == TravelModeEnum.BUS for leg in bus.legs)

    def test_single_point_refused(self) -> None:
        """A route needs at least two points offline too."""
        with pytest.raises(ValueError, match="at least two points"):
            estimate_route_through([TERESINA])

    def test_polyline_of_offline_geometry_round_trips(self) -> None:
        """The straight-line geometry encodes like a routed one."""
        result = estimate_route_through(POINTS, with_geometry=True)
        decoded = decode_polyline(encode_polyline(result.geometry))
        assert decoded[1].latitude == pytest.approx(ALTOS.latitude)


class TestFakeRouteThrough:
    """The fake keeps the protocol whole."""

    def test_fake_satisfies_protocol(self) -> None:
        """``FakeRoutingBackend`` still passes the ``RoutingBackend`` check."""
        assert isinstance(FakeRoutingBackend(), RoutingBackend)

    async def test_fake_route_through_honours_pins(self) -> None:
        """A pinned pair is used as a leg inside a longer route."""
        fake = FakeRoutingBackend()
        pinned = estimate_travel(TERESINA, ALTOS).model_copy(
            update={"distance_km": 99.0}
        )
        fake.add_route(TERESINA, ALTOS, pinned)

        result = await fake.route_through(POINTS)

        assert result.source == "fake"
        assert result.legs[0].distance_km == 99.0
        assert fake.routes == [(TERESINA, ALTOS), (ALTOS, CAMPO_MAIOR)]

    async def test_fake_matrix_one_cell_per_pair(self) -> None:
        """The fake matrix answers one row per source, one cell per target."""
        matrix = await FakeRoutingBackend().matrix([TERESINA], [ALTOS, CAMPO_MAIOR])
        assert len(matrix.durations_minutes) == 1
        assert len(matrix.durations_minutes[0]) == 2
        assert matrix.source_label == "fake"
