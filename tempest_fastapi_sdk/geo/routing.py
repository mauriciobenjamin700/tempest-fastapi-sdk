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
from tempest_fastapi_sdk.geo.schemas import Coordinate, TravelEstimate

if TYPE_CHECKING:
    import httpx

# Public OSRM demo server. Car profile only; rate-limited, best-effort.
DEFAULT_OSRM_BASE_URL: str = "https://router.project-osrm.org"


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
    ) -> None:
        """Initialize the backend.

        Args:
            http_client: An ``httpx.AsyncClient`` the caller owns and closes.
            base_url: Root URL of the OSRM server.
            mode_duration_factors: Optional per-mode duration multiplier map
                applied to the car duration for non-car modes.
        """
        self._http: httpx.AsyncClient = http_client
        self.base_url: str = base_url.rstrip("/")
        self._factors: dict[TravelMode, float] | None = mode_duration_factors

    async def route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        *,
        mode: TravelMode = TravelMode.CAR,
    ) -> TravelEstimate:
        """Fetch a real road route and scale it for the requested mode.

        The car route is fetched from OSRM; distance is used as-is and the
        duration is multiplied by the mode's duration factor.

        Args:
            origin: The starting coordinate.
            destination: The ending coordinate.
            mode: The travel mode to estimate for.

        Returns:
            A :class:`TravelEstimate` with ``source="osrm"``.

        Raises:
            RuntimeError: If the request fails or OSRM finds no route.
        """
        coords = (
            f"{origin.longitude},{origin.latitude};"
            f"{destination.longitude},{destination.latitude}"
        )
        url = f"{self.base_url}/route/v1/driving/{coords}"
        try:
            response = await self._http.get(url, params={"overview": "false"})
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

        return TravelEstimate(
            mode=mode,
            distance_km=distance_km,
            duration_minutes=duration_minutes,
            source="osrm",
        )
