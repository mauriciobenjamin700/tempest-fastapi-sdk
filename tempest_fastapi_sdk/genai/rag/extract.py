"""Fetch a page and extract its main text (HTML → clean text).

Search snippets are thin; to give an LLM real ground truth you fetch each
result and pull the article body out of the HTML. Uses ``trafilatura``
(the leviathan choice) for cleaning. Failures never raise — a page that
times out or yields nothing comes back as ``failed=True`` with empty
text, so no source is silently dropped.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx


@dataclass(slots=True)
class ExtractionResult:
    """Outcome of fetching + extracting one URL.

    Attributes:
        text (str): The cleaned page body, or ``""`` on failure.
        failed (bool): ``True`` when the page couldn't be fetched or no
            text could be extracted.
    """

    text: str
    failed: bool


def _require_trafilatura() -> object:
    """Import ``trafilatura`` or raise a helpful error.

    Returns:
        object: The ``trafilatura`` module.

    Raises:
        ImportError: When the ``[genai-rag]`` extra is not installed.
    """
    try:
        import trafilatura
    except ImportError as exc:
        raise ImportError(
            "Content extraction requires the optional [genai-rag] extra. "
            "Install with: pip install tempest-fastapi-sdk[genai-rag]",
        ) from exc
    return trafilatura


class ContentExtractor:
    """Fetch URLs and extract their main text via ``trafilatura``.

    The ``httpx.AsyncClient`` is injected so the connection pool is shared.

    Attributes:
        user_agent (str): ``User-Agent`` header sent with each fetch.
        timeout (float): Per-request timeout in seconds.
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        user_agent: str = "tempest-fastapi-sdk/genai",
        timeout: float = 10.0,
    ) -> None:
        """Initialize the extractor.

        Args:
            http_client (httpx.AsyncClient): Injected async client.
            user_agent (str): ``User-Agent`` for fetches.
            timeout (float): Per-request timeout in seconds.
        """
        self._http = http_client
        self.user_agent = user_agent
        self.timeout = timeout

    async def extract(self, url: str) -> ExtractionResult:
        """Fetch ``url`` and return its extracted main text.

        Never raises: fetch/extraction failures come back as
        ``ExtractionResult(text="", failed=True)``.

        Args:
            url (str): The page to fetch.

        Returns:
            ExtractionResult: The extracted text or a failure marker.
        """
        trafilatura = _require_trafilatura()
        try:
            response = await self._http.get(
                url,
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
                follow_redirects=True,
            )
            response.raise_for_status()
        except Exception:
            return ExtractionResult(text="", failed=True)

        text = trafilatura.extract(response.text) or ""  # type: ignore[attr-defined]
        return ExtractionResult(text=text, failed=not text)

    async def extract_many(
        self,
        urls: Sequence[str],
        *,
        concurrency: int = 5,
    ) -> list[ExtractionResult]:
        """Extract many URLs concurrently, capped at ``concurrency``.

        Results are returned in the same order as ``urls``. Individual
        failures are absorbed into their :class:`ExtractionResult` (never
        raised), so one bad page can't sink the batch.

        Args:
            urls (Sequence[str]): The pages to fetch.
            concurrency (int): Max simultaneous fetches.

        Returns:
            list[ExtractionResult]: One result per URL, in input order.
        """
        semaphore = asyncio.Semaphore(concurrency)

        async def _one(url: str) -> ExtractionResult:
            async with semaphore:
                return await self.extract(url)

        return await asyncio.gather(*[_one(url) for url in urls])


__all__: list[str] = [
    "ContentExtractor",
    "ExtractionResult",
]
