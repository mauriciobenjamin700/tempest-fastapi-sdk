"""Web search with no Searxng instance behind it.

A retrieval flow is mostly assembly — search, extract, rank, build context —
and asserting on it needs results that do not move between runs. The real
backend gives neither: it needs an instance, and the live web changes what it
returns.
"""

from __future__ import annotations

from tempest_fastapi_sdk.genai.rag.schemas import SearchResult
from tempest_fastapi_sdk.testing.fakes._control import _Steerable


class FakeWebSearchBackend(_Steerable):
    """A ``WebSearchBackend`` answering from a table you fill.

    Example:

        >>> backend = FakeWebSearchBackend()
        >>> backend.add_results(
        ...     "pix",
        ...     [SearchResult(
        ...         title="Pix",
        ...         url="https://example.test/pix",
        ...         snippet="Pagamento instantâneo",
        ...         content="Pix é o pagamento instantâneo brasileiro.",
        ...     )],
        ... )
        >>> results = await backend.search("Pix", max_results=5)
        >>> results[0].title
        'Pix'

    Attributes:
        queries (list[str]): Every query this backend saw, in order.
        calls (list[str]): Methods that ran, in order.
    """

    def __init__(self) -> None:
        """Start with an empty table, which returns no results."""
        super().__init__()
        self._results: dict[str, list[SearchResult]] = {}
        self.queries: list[str] = []

    def add_results(self, query: str, results: list[SearchResult]) -> None:
        """Register the results one query returns.

        Args:
            query (str): The query, matched case-insensitively.
            results (list[SearchResult]): What to return for it, in order.
        """
        self._results[query.casefold()] = list(results)

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        """Search.

        Args:
            query (str): What to search for.
            max_results (int): Ceiling on how many come back. Honoured, so a
                consumer that assumes the backend respects it is exercised
                the same way the real one exercises it.

        Returns:
            list[SearchResult]: The registered results, truncated to
            ``max_results``. Empty when the query was never registered —
            an empty result set is a valid search, not an error.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("search")
        self.queries.append(query)
        return self._results.get(query.casefold(), [])[:max_results]
