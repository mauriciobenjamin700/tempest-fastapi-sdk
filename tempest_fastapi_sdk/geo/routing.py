"""Real road routing via OSRM — free, open-source, no paid API key.

`OSRMBackend` talks to any OSRM HTTP server: the public demo at
``router.project-osrm.org`` (car profile only) or a self-hosted instance.
The ``httpx.AsyncClient`` is injected by the caller (leviathan pattern), so
this module never imports ``httpx`` at import time and works without the
``[geo]`` extra installed until you actually build a backend.

Because the public demo only exposes a car profile, motorcycle and bus
estimates reuse the car's road distance and scale the car's duration by the
mode factor (see :data:`tempest_fastapi_sdk.geo.DEFAULT_MODE_DURATION_FACTORS`).
Self-host OSRM with dedicated profiles if you need true per-mode geometry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from tempest_fastapi_sdk.geo.enums import TravelMode
from tempest_fastapi_sdk.geo.estimate import duration_factor
from tempest_fastapi_sdk.geo.polyline import decode_polyline
from tempest_fastapi_sdk.geo.schemas import (
    Coordinate,
    DistanceMatrix,
    TravelEstimate,
)

if TYPE_CHECKING:
    import httpx

# Public OSRM demo server. Car profile only; rate-limited, best-effort.
DEFAULT_OSRM_BASE_URL: str = "https://router.project-osrm.org"

# Which OSRM profile serves each travel mode. The public demo only ships
# the ``driving`` profile, so every mode maps to it by default and non-car
# modes are derived by scaling the car duration. Self-host OSRM with
# ``foot``/``bike`` profiles and override this map for true per-mode routes.
DEFAULT_MODE_PROFILES: dict[TravelMode, str] = {
    TravelMode.CAR: "driving",
    TravelMode.MOTORCYCLE: "driving",
    TravelMode.BUS: "driving",
    TravelMode.BICYCLE: "driving",
    TravelMode.PEDESTRIAN: "driving",
}


@runtime_checkable
class RoutingBackend(Protocol):
    """A source of real road distance and duration between two points."""

    async def route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        *,
        mode: TravelMode = TravelMode.CAR,
    ) -> TravelEstimate:
        """Return a road :class:`TravelEstimate` for the given trip."""
        ...


class OSRMBackend:
    """Route trips through an OSRM HTTP server.

    Attributes:
        base_url: Root URL of the OSRM server (no trailing slash needed).
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        base_url: str = DEFAULT_OSRM_BASE_URL,
        mode_duration_factors: dict[TravelMode, float] | None = None,
        mode_profiles: dict[TravelMode, str] | None = None,
    ) -> None:
        """Initialize the backend.

        Args:
            http_client: An ``httpx.AsyncClient`` the caller owns and closes.
            base_url: Root URL of the OSRM server.
            mode_duration_factors: Optional per-mode duration multiplier map
                applied to the car duration for non-car modes.
            mode_profiles: Optional per-mode OSRM profile map (override when
                self-hosting ``foot``/``bike`` profiles); falls back to
                :data:`DEFAULT_MODE_PROFILES` (all ``driving``).
        """
        self._http: httpx.AsyncClient = http_client
        self.base_url: str = base_url.rstrip("/")
        self._factors: dict[TravelMode, float] | None = mode_duration_factors
        self._profiles: dict[TravelMode, str] = mode_profiles or DEFAULT_MODE_PROFILES

    def _profile(self, mode: TravelMode) -> str:
        """Return the OSRM profile for ``mode`` (defaults to ``driving``)."""
        return self._profiles.get(mode, "driving")

    async def route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        *,
        mode: TravelMode = TravelMode.CAR,
        with_geometry: bool = False,
    ) -> TravelEstimate:
        """Fetch a real road route and scale it for the requested mode.

        The route is fetched from OSRM; distance is used as-is and the
        duration is multiplied by the mode's duration factor. Pass
        ``with_geometry=True`` to also decode the route polyline into
        :attr:`TravelEstimate.geometry`.

        Args:
            origin: The starting coordinate.
            destination: The ending coordinate.
            mode: The travel mode to estimate for.
            with_geometry: Whether to include the decoded route geometry.

        Returns:
            A :class:`TravelEstimate` with ``source="osrm"``.

        Raises:
            RuntimeError: If the request fails or OSRM finds no route.
        """
        coords = (
            f"{origin.longitude},{origin.latitude};"
            f"{destination.longitude},{destination.latitude}"
        )
        url = f"{self.base_url}/route/v1/{self._profile(mode)}/{coords}"
        params = {
            "overview": "full" if with_geometry else "false",
            "geometries": "polyline",
        }
        try:
            response = await self._http.get(url, params=params)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        except Exception as exc:  # normalize any transport/parse error
            raise RuntimeError(f"OSRM request failed: {exc}") from exc

        if payload.get("code") != "Ok" or not payload.get("routes"):
            raise RuntimeError(
                f"OSRM returned no route (code={payload.get('code')!r})",
            )

        route: dict[str, Any] = payload["routes"][0]
        distance_km = float(route["distance"]) / 1000.0
        car_minutes = float(route["duration"]) / 60.0
        duration_minutes = car_minutes * duration_factor(mode, self._factors)
        geometry = (
            decode_polyline(route["geometry"])
            if with_geometry and route.get("geometry")
            else []
        )

        return TravelEstimate(
            mode=mode,
            distance_km=distance_km,
            duration_minutes=duration_minutes,
            source="osrm",
            geometry=geometry,
        )

    async def matrix(
        self,
        sources: list[Coordinate],
        destinations: list[Coordinate] | None = None,
        *,
        mode: TravelMode = TravelMode.CAR,
    ) -> DistanceMatrix:
        """Fetch a many-to-many distance/duration matrix in one request.

        Uses the OSRM ``table`` service. When ``destinations`` is ``None``
        the matrix is square (every source to every other source), matching
        OSRM's default. Durations are scaled per mode like :meth:`route`.

        Args:
            sources: The origin coordinates (matrix rows).
            destinations: The destination coordinates (matrix columns);
                defaults to ``sources`` (a full square matrix).
            mode: The travel mode to estimate for.

        Returns:
            A :class:`DistanceMatrix` with ``distances_km`` and
            ``durations_minutes`` grids.

        Raises:
            ValueError: If ``sources`` is empty.
            RuntimeError: If the request fails or OSRM returns no table.
        """
        if not sources:
            raise ValueError("sources must not be empty")
        targets = destinations if destinations is not None else sources
        all_points = [*sources, *targets]
        coords = ";".join(f"{p.longitude},{p.latitude}" for p in all_points)
        source_idx = ";".join(str(i) for i in range(len(sources)))
        dest_idx = ";".join(
            str(i) for i in range(len(sources), len(sources) + len(targets))
        )
        params = {
            "annotations": "distance,duration",
            "sources": source_idx,
            "destinations": dest_idx,
        }
        url = f"{self.base_url}/table/v1/{self._profile(mode)}/{coords}"
        try:
            response = await self._http.get(url, params=params)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        except Exception as exc:  # normalize any transport/parse error
            raise RuntimeError(f"OSRM table request failed: {exc}") from exc

        if payload.get("code") != "Ok":
            raise RuntimeError(
                f"OSRM returned no table (code={payload.get('code')!r})",
            )

        factor = duration_factor(mode, self._factors)
        distances_km = [
            [float(value) / 1000.0 for value in row]
            for row in payload.get("distances", [])
        ]
        durations_minutes = [
            [float(value) / 60.0 * factor for value in row]
            for row in payload.get("durations", [])
        ]
        return DistanceMatrix(
            sources=sources,
            destinations=targets,
            distances_km=distances_km,
            durations_minutes=durations_minutes,
            mode=mode,
            source_label="osrm",
        )


__all__: list[str] = [
    "DEFAULT_MODE_PROFILES",
    "DEFAULT_OSRM_BASE_URL",
    "OSRMBackend",
    "RoutingBackend",
]
