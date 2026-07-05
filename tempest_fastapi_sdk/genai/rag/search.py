"""Web search backends — pluggable, defaulting to SearXNG.

Mirrors the leviathan pattern: a thin async client over SearXNG's JSON
API (``GET /search?format=json``), returning :class:`SearchResult`s that
downstream code extracts and feeds to an LLM. The backend is a Protocol,
so a project can swap SearXNG for another provider without touching call
sites. The ``httpx.AsyncClient`` is injected so its connection pool is
reused (typically from the FastAPI lifespan).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from tempest_fastapi_sdk.genai.rag.schemas import SearchResult

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger("tempest_fastapi_sdk.genai.rag")


@runtime_checkable
class WebSearchBackend(Protocol):
    """A source of web results for a natural-language query."""

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        """Return up to ``max_results`` sources for ``query``."""
        ...


class SearxngBackend:
    """Web search over a self-hosted SearXNG instance (JSON API).

    Example:

        >>> import httpx
        >>> client = httpx.AsyncClient()
        >>> backend = SearxngBackend("http://localhost:8080", http_client=client)
        >>> results = await backend.search("what is PIX?", max_results=5)

    Attributes:
        base_url (str): The SearXNG base URL (without ``/search``).
        language (str): Query language passed to SearXNG.
    """

    def __init__(
        self,
        base_url: str,
        *,
        http_client: httpx.AsyncClient,
        language: str = "auto",
        timeout: float = 10.0,
    ) -> None:
        """Initialize the backend.

        Args:
            base_url (str): SearXNG base URL, e.g.
                ``"http://localhost:8080"``.
            http_client (httpx.AsyncClient): Injected async client (pool
                reuse). The backend owns no connection state.
            language (str): Search language (``"auto"``, ``"pt-BR"``, …).
            timeout (float): Per-request timeout in seconds.
        """
        self.base_url = base_url.rstrip("/")
        self.language = language
        self.timeout = timeout
        self._http = http_client

    async def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        """Run ``query`` against SearXNG and return parsed results.

        Args:
            query (str): The natural-language query.
            max_results (int): Cap on results returned (post-slice).

        Returns:
            list[SearchResult]: Parsed results in SearXNG order (no
            re-ranking). Empty list when SearXNG returns nothing.

        Raises:
            RuntimeError: When the SearXNG request fails or returns a
                non-JSON / malformed payload (enable ``format: json`` in
                its ``settings.yml``).
        """
        params: dict[str, str] = {
            "q": query,
            "format": "json",
            "language": self.language,
            "safesearch": "0",
        }
        try:
            response = await self._http.get(
                f"{self.base_url}/search",
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            raise RuntimeError(f"SearXNG request failed: {exc}") from exc

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise RuntimeError(
                "SearXNG returned a non-JSON payload (is `format: json` "
                "enabled in settings.yml?).",
            ) from exc

        raw = payload.get("results", [])
        if not isinstance(raw, list):
            raise RuntimeError("SearXNG `results` field is not a list.")

        results: list[SearchResult] = []
        for item in raw[:max_results]:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title", "")),
                    url=str(item["url"]),
                    snippet=str(item.get("content", "")),
                    score=item.get("score"),
                ),
            )
        return results


class WebSearch:
    """Thin facade over a :class:`WebSearchBackend`.

    Wraps any backend so call sites depend on this class, not on a
    specific provider. Defaults to SearXNG when you pass a backend built
    with :class:`SearxngBackend`.

    Attributes:
        backend (WebSearchBackend): The active search backend.
    """

    def __init__(self, backend: WebSearchBackend) -> None:
        """Initialize the facade.

        Args:
            backend (WebSearchBackend): The backend to delegate to.
        """
        self.backend = backend

    async def search(self, query: str, *, max_results: int = 5) -> list[SearchResult]:
        """Search ``query`` through the backend.

        Args:
            query (str): The natural-language query.
            max_results (int): Cap on results.

        Returns:
            list[SearchResult]: The results.
        """
        return await self.backend.search(query, max_results=max_results)


__all__: list[str] = [
    "SearxngBackend",
    "WebSearch",
    "WebSearchBackend",
]
