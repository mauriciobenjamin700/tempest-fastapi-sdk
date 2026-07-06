"""Geocoding — address <-> coordinate — via Nominatim (OSM), no paid key.

`NominatimBackend` talks to any Nominatim HTTP server: the public
OpenStreetMap instance or a self-hosted one. The ``httpx.AsyncClient`` is
injected by the caller (leviathan pattern), so this module never imports
``httpx`` at import time and works without the ``[geo]`` extra until you
build a backend.

!!! warning "Public instance usage policy"
    The public ``nominatim.openstreetmap.org`` requires a descriptive
    ``User-Agent`` and caps you at ~1 request/second. Pass your own
    ``user_agent`` and self-host for anything at scale.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from tempest_fastapi_sdk.geo.schemas import Coordinate, GeocodeResult

if TYPE_CHECKING:
    import httpx

# Public OSM Nominatim server. Rate-limited (~1 req/s), best-effort.
DEFAULT_NOMINATIM_BASE_URL: str = "https://nominatim.openstreetmap.org"


@runtime_checkable
class GeocodingBackend(Protocol):
    """A source of forward and reverse geocoding."""

    async def geocode(self, query: str) -> GeocodeResult | None:
        """Resolve a free-text address/place to a coordinate, or ``None``."""
        ...

    async def reverse(self, coordinate: Coordinate) -> GeocodeResult | None:
        """Resolve a coordinate to a place, or ``None``."""
        ...


class NominatimBackend:
    """Geocode through a Nominatim (OpenStreetMap) HTTP server.

    Attributes:
        base_url: Root URL of the Nominatim server (no trailing slash needed).
        user_agent: Value sent as the ``User-Agent`` header (required by the
            public instance's usage policy).
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        base_url: str = DEFAULT_NOMINATIM_BASE_URL,
        user_agent: str = "tempest-fastapi-sdk",
        language: str | None = None,
    ) -> None:
        """Initialize the backend.

        Args:
            http_client: An ``httpx.AsyncClient`` the caller owns and closes.
            base_url: Root URL of the Nominatim server.
            user_agent: ``User-Agent`` header (identify your app).
            language: Optional ``Accept-Language`` preference (e.g. ``"pt-BR"``).
        """
        self._http: httpx.AsyncClient = http_client
        self.base_url: str = base_url.rstrip("/")
        self.user_agent: str = user_agent
        self._language: str | None = language

    def _headers(self) -> dict[str, str]:
        """Return the request headers (User-Agent + optional language)."""
        headers = {"User-Agent": self.user_agent}
        if self._language is not None:
            headers["Accept-Language"] = self._language
        return headers

    async def geocode(self, query: str) -> GeocodeResult | None:
        """Resolve a free-text address/place to a coordinate.

        Args:
            query: The address or place text (a street, city, CEP, …).

        Returns:
            The best :class:`~tempest_fastapi_sdk.geo.GeocodeResult`, or
            ``None`` when nothing matches.

        Raises:
            RuntimeError: If the request fails.
        """
        try:
            response = await self._http.get(
                f"{self.base_url}/search",
                params={"q": query, "format": "jsonv2", "limit": 1},
                headers=self._headers(),
            )
            response.raise_for_status()
            payload: list[dict[str, Any]] = response.json()
        except Exception as exc:  # normalize any transport/parse error
            raise RuntimeError(f"Nominatim geocode failed: {exc}") from exc

        if not payload:
            return None
        return self._to_result(payload[0])

    async def reverse(self, coordinate: Coordinate) -> GeocodeResult | None:
        """Resolve a coordinate to the nearest known place.

        Args:
            coordinate: The point to reverse-geocode.

        Returns:
            A :class:`~tempest_fastapi_sdk.geo.GeocodeResult`, or ``None``
            when the server reports no match.

        Raises:
            RuntimeError: If the request fails.
        """
        try:
            response = await self._http.get(
                f"{self.base_url}/reverse",
                params={
                    "lat": coordinate.latitude,
                    "lon": coordinate.longitude,
                    "format": "jsonv2",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        except Exception as exc:  # normalize any transport/parse error
            raise RuntimeError(f"Nominatim reverse failed: {exc}") from exc

        if "lat" not in payload or "lon" not in payload:
            return None
        return self._to_result(payload)

    @staticmethod
    def _to_result(entry: dict[str, Any]) -> GeocodeResult:
        """Map a Nominatim JSON entry to a :class:`GeocodeResult`."""
        return GeocodeResult(
            coordinate=Coordinate(
                latitude=float(entry["lat"]),
                longitude=float(entry["lon"]),
            ),
            display_name=str(entry.get("display_name", "")),
            place_type=entry.get("type") or entry.get("addresstype"),
        )


__all__: list[str] = [
    "DEFAULT_NOMINATIM_BASE_URL",
    "GeocodingBackend",
    "NominatimBackend",
]
