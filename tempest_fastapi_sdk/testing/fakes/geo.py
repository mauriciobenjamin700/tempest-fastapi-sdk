"""Geocoding and routing without Nominatim or OSRM.

Both real backends are HTTP clients against public services with usage
policies, so a test suite that hits them is slow, rate-limited and offline-
hostile. The routing fake does **not** invent its own arithmetic: it delegates
to :func:`~tempest_fastapi_sdk.geo.estimate_travel`, the offline estimator the
SDK already ships, so the numbers a test asserts on are the SDK's own.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

from tempest_fastapi_sdk.geo.enums import TravelModeEnum
from tempest_fastapi_sdk.geo.estimate import estimate_travel
from tempest_fastapi_sdk.geo.schemas import (
    Coordinate,
    DistanceMatrix,
    GeocodeResult,
    RouteThroughResult,
    TravelEstimate,
)
from tempest_fastapi_sdk.testing.fakes._control import _Steerable


class FakeGeocodingBackend(_Steerable):
    """A ``GeocodingBackend`` answering from a table you fill.

    Example:

        >>> backend = FakeGeocodingBackend()
        >>> backend.add_place("Recife", Coordinate(latitude=-8.05, longitude=-34.9))
        >>> result = await backend.geocode("recife")
        >>> result.coordinate.latitude
        -8.05

    Attributes:
        queries (list[str]): Every query this backend saw, in order.
        calls (list[str]): Methods that ran, in order.
    """

    def __init__(self) -> None:
        """Start with an empty table, which resolves nothing."""
        super().__init__()
        self._places: dict[str, GeocodeResult] = {}
        self.queries: list[str] = []

    def add_place(
        self,
        query: str,
        coordinate: Coordinate,
        *,
        display_name: str | None = None,
        place_type: str | None = None,
    ) -> None:
        """Teach the backend one place.

        Args:
            query (str): The text that resolves to it, matched
                case-insensitively.
            coordinate (Coordinate): Where it is.
            display_name (str | None): Name to report back. Defaults to
                ``query``.
            place_type (str | None): Nominatim-style type, when a test
                asserts on it.
        """
        self._places[query.casefold()] = GeocodeResult(
            coordinate=coordinate,
            display_name=display_name or query,
            place_type=place_type,
        )

    async def geocode(self, query: str) -> GeocodeResult | None:
        """Resolve a query to a place.

        Args:
            query (str): The text to resolve.

        Returns:
            GeocodeResult | None: The registered place, or ``None`` when the
            table has no entry — which is the same answer the real backend
            gives for an address nobody can find, and the branch a service
            usually forgets to handle.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("geocode")
        self.queries.append(query)
        return self._places.get(query.casefold())

    async def reverse(self, coordinate: Coordinate) -> GeocodeResult | None:
        """Resolve a coordinate back to the nearest registered place.

        Args:
            coordinate (Coordinate): Where to look.

        Returns:
            GeocodeResult | None: The registered place closest to
            ``coordinate``, or ``None`` when the table is empty. Nearest wins
            rather than exact-match, because a service reverse-geocodes a GPS
            reading, and a GPS reading never lands on a stored decimal.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("reverse")
        self.queries.append(f"{coordinate.latitude},{coordinate.longitude}")
        if not self._places:
            return None
        return min(
            self._places.values(),
            key=lambda place: estimate_travel(coordinate, place.coordinate).distance_km,
        )


class FakeRoutingBackend(_Steerable):
    """A ``RoutingBackend`` that estimates offline.

    Example:

        >>> backend = FakeRoutingBackend()
        >>> estimate = await backend.route(
        ...     Coordinate(latitude=-8.05, longitude=-34.9),
        ...     Coordinate(latitude=-8.06, longitude=-34.91),
        ... )
        >>> estimate.source
        'fake'

    Attributes:
        routes (list[tuple[Coordinate, Coordinate]]): Every pair asked for.
        calls (list[str]): Methods that ran, in order.
    """

    def __init__(self, *, source: str = "fake") -> None:
        """Start with no canned answers.

        Args:
            source (str): Value stamped on :attr:`TravelEstimate.source`, so
                an assertion can tell a faked estimate from an OSRM one.
        """
        super().__init__()
        self._canned: dict[tuple[float, float, float, float], TravelEstimate] = {}
        self._source: str = source
        self.routes: list[tuple[Coordinate, Coordinate]] = []

    def add_route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        estimate: TravelEstimate,
    ) -> None:
        """Pin one pair to an exact estimate.

        Args:
            origin (Coordinate): Where the trip starts.
            destination (Coordinate): Where it ends.
            estimate (TravelEstimate): What to return for that pair,
                verbatim — for the test that needs a specific duration
                rather than a plausible one.
        """
        self._canned[self._key(origin, destination)] = estimate

    async def route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        *,
        mode: TravelModeEnum = TravelModeEnum.CAR,
    ) -> TravelEstimate:
        """Estimate a trip.

        Args:
            origin (Coordinate): Where the trip starts.
            destination (Coordinate): Where it ends.
            mode (TravelModeEnum): How it travels.

        Returns:
            TravelEstimate: The pinned estimate for this pair when there is
            one; otherwise the SDK's own offline estimate, restamped with
            :attr:`source`.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("route")
        self.routes.append((origin, destination))
        pinned = self._canned.get(self._key(origin, destination))
        if pinned is not None:
            return pinned
        estimate = estimate_travel(origin, destination, mode)
        return estimate.model_copy(update={"source": self._source})

    async def route_through(
        self,
        points: Sequence[Coordinate],
        *,
        mode: TravelModeEnum = TravelModeEnum.CAR,
        with_geometry: bool = False,
    ) -> RouteThroughResult:
        """Estimate a route through several points, one :meth:`route` per leg.

        Each leg goes through :meth:`route`, so a pair pinned with
        :meth:`add_route` is honoured inside a longer route too, and every
        leg lands in :attr:`routes`.

        Args:
            points (Sequence[Coordinate]): The ordered points. At least two.
            mode (TravelModeEnum): How it travels.
            with_geometry (bool): Whether to fill the geometry with the
                points themselves (straight lines).

        Returns:
            RouteThroughResult: The legs and their sums, stamped with
            :attr:`source`.

        Raises:
            ValueError: If fewer than two points are given.
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("route_through")
        if len(points) < 2:
            raise ValueError("a route needs at least two points")
        legs = [
            await self.route(origin, destination, mode=mode)
            for origin, destination in pairwise(points)
        ]
        return RouteThroughResult(
            mode=mode,
            distance_km=sum(leg.distance_km for leg in legs),
            duration_minutes=sum(leg.duration_minutes for leg in legs),
            source=self._source,
            geometry=list(points) if with_geometry else [],
            legs=legs,
        )

    async def matrix(
        self,
        sources: list[Coordinate],
        destinations: list[Coordinate] | None = None,
        *,
        mode: TravelModeEnum = TravelModeEnum.CAR,
    ) -> DistanceMatrix:
        """Estimate every (source, destination) pair through :meth:`route`.

        Args:
            sources (list[Coordinate]): The origin coordinates (rows).
            destinations (list[Coordinate] | None): The destination
                coordinates (columns); ``None`` means ``sources``.
            mode (TravelModeEnum): How it travels.

        Returns:
            DistanceMatrix: One cell per pair, ``source_label`` stamped with
            :attr:`source`.

        Raises:
            ValueError: If ``sources`` is empty.
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("matrix")
        if not sources:
            raise ValueError("sources must not be empty")
        targets = destinations if destinations is not None else sources
        rows = [
            [await self.route(origin, target, mode=mode) for target in targets]
            for origin in sources
        ]
        return DistanceMatrix(
            sources=sources,
            destinations=targets,
            distances_km=[[cell.distance_km for cell in row] for row in rows],
            durations_minutes=[[cell.duration_minutes for cell in row] for row in rows],
            mode=mode,
            source_label=self._source,
        )

    @staticmethod
    def _key(
        origin: Coordinate,
        destination: Coordinate,
    ) -> tuple[float, float, float, float]:
        """Build the lookup key for a coordinate pair.

        Args:
            origin (Coordinate): Where the trip starts.
            destination (Coordinate): Where it ends.

        Returns:
            tuple[float, float, float, float]: The four coordinates, in
            order.
        """
        return (
            origin.latitude,
            origin.longitude,
            destination.latitude,
            destination.longitude,
        )
