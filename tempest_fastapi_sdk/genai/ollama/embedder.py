"""The Ollama embedder."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.genai.generation_cache import (
    AsyncGenerationCache,
    GenerationCache,
)
from tempest_fastapi_sdk.genai.metrics import GenAIMetrics
from tempest_fastapi_sdk.genai.ollama._client import _OllamaClientMixin
from tempest_fastapi_sdk.genai.ollama._constants import DEFAULT_OLLAMA_URL
from tempest_fastapi_sdk.genai.ollama.errors import _raise_on_error
from tempest_fastapi_sdk.genai.tracing import genai_span
from tempest_fastapi_sdk.utils.http_client import HTTPClient, RetryPolicy

if TYPE_CHECKING:
    import httpx


class OllamaEmbedder(_OllamaClientMixin):
    """Embeddings backed by an Ollama daemon.

    Satisfies :class:`~tempest_fastapi_sdk.genai.rag.SupportsEmbed`, so it
    plugs into :class:`~tempest_fastapi_sdk.genai.rag.Retriever` and the
    ``/embed`` endpoint of
    :func:`~tempest_fastapi_sdk.genai.make_genai_router` in place of the
    ``torch``-backed :class:`~tempest_fastapi_sdk.genai.Embedder`. Pull an
    embedding model first (e.g. ``ollama pull nomic-embed-text``).

    Asymmetric models such as ``nomic-embed-text`` were trained with a task
    prefix on each side; pass them as ``query_prefix`` / ``passage_prefix``
    (or splat a preset such as
    :data:`~tempest_fastapi_sdk.genai.NOMIC_EMBED_TEXT_PREFIXES`) and the
    ``Retriever`` applies each one through :meth:`embed_query` and
    :meth:`embed_passages`. Changing the prefixes changes every vector, so
    an existing index has to be rebuilt.

    Attributes:
        model (str): The Ollama embedding model tag.
        base_url (str): The daemon base URL.
        query_prefix (str): Prepended by :meth:`embed_query`.
        passage_prefix (str): Prepended by :meth:`embed_passages`.
    """

    def __init__(
        self,
        model: str,
        *,
        base_url: str = DEFAULT_OLLAMA_URL,
        timeout: float = 120.0,
        keep_alive: str | float | None = None,
        http_client: HTTPClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
        metrics: GenAIMetrics | None = None,
        generation_cache: GenerationCache | AsyncGenerationCache | None = None,
        query_prefix: str = "",
        passage_prefix: str = "",
    ) -> None:
        """Configure the daemon connection and the task prefixes.

        Args:
            model (str): The Ollama embedding model tag (must already be
                pulled, e.g. ``"nomic-embed-text"``).
            base_url (str): Base URL of the Ollama daemon.
            timeout (float): Per-request HTTP timeout in seconds.
            keep_alive (str | float | None): Ollama ``keep_alive`` value;
                ``None`` uses the daemon default.
            http_client (HTTPClient | None): An injected client; when
                ``None`` one is created lazily and owned by this instance.
            transport (httpx.AsyncBaseTransport | None): Explicit transport
                for the lazily-created client (``httpx.MockTransport`` in
                tests). Ignored when ``http_client`` is given.
            retry_policy (RetryPolicy | None): Retry configuration for the
                lazily-created client. Ignored when ``http_client`` is given.
            metrics (GenAIMetrics | None): Optional Prometheus metrics.
            generation_cache (GenerationCache | AsyncGenerationCache | None):
                Accepted for signature compatibility with the shared client
                base; embeddings never read it.
            query_prefix (str): Text prepended to every input of
                :meth:`embed_query`. ``nomic-embed-text`` wants
                ``"search_query: "``. :meth:`embed` never applies it.
            passage_prefix (str): Text prepended to every input of
                :meth:`embed_passages` (the corpus side);
                ``nomic-embed-text`` wants ``"search_document: "``.
        """
        super().__init__(
            model,
            base_url=base_url,
            timeout=timeout,
            keep_alive=keep_alive,
            http_client=http_client,
            transport=transport,
            retry_policy=retry_policy,
            metrics=metrics,
            generation_cache=generation_cache,
        )
        self.query_prefix: str = query_prefix
        self.passage_prefix: str = passage_prefix

    async def embed_query(
        self,
        texts: str | list[str],
        *,
        batch_size: int = 32,
    ) -> list[list[float]]:
        """Embed search queries, prepending ``query_prefix`` to each one.

        :class:`~tempest_fastapi_sdk.genai.rag.Retriever` calls this for the
        query when the embedder has it.

        Args:
            texts (str | list[str]): A single query or a list of queries.
            batch_size (int): Max texts sent to the daemon per request.

        Returns:
            list[list[float]]: One vector per input query, in input order.
        """
        items = [texts] if isinstance(texts, str) else list(texts)
        return await self.embed(
            [f"{self.query_prefix}{text}" for text in items],
            batch_size=batch_size,
        )

    async def embed_passages(
        self,
        texts: str | list[str],
        *,
        batch_size: int = 32,
    ) -> list[list[float]]:
        """Embed corpus passages, prepending ``passage_prefix`` to each one.

        :class:`~tempest_fastapi_sdk.genai.rag.Retriever` calls this when it
        indexes chunks, so stored vectors and query vectors carry the role
        markers the model was trained with.

        Args:
            texts (str | list[str]): A single passage or a list of passages.
            batch_size (int): Max texts sent to the daemon per request.

        Returns:
            list[list[float]]: One vector per input passage, in input order.
        """
        items = [texts] if isinstance(texts, str) else list(texts)
        return await self.embed(
            [f"{self.passage_prefix}{text}" for text in items],
            batch_size=batch_size,
        )

    async def embed(
        self,
        texts: str | list[str],
        *,
        batch_size: int = 32,
    ) -> list[list[float]]:
        """Embed one or many texts into vectors.

        Args:
            texts (str | list[str]): A single text or a list of texts.
            batch_size (int): Max texts sent to the daemon per request.

        Returns:
            list[list[float]]: One vector per input text, in input order.
                Returns an empty list for empty input.
        """
        items = [texts] if isinstance(texts, str) else list(texts)
        if not items:
            return []

        async with genai_span("embed", self.model):
            client = self._http()
            vectors: list[list[float]] = []
            for start in range(0, len(items), batch_size):
                batch = items[start : start + batch_size]
                payload: dict[str, Any] = {"model": self.model, "input": batch}
                if self.keep_alive is not None:
                    payload["keep_alive"] = self.keep_alive
                response = await client.post(f"{self.base_url}/api/embed", json=payload)
                response.raise_for_status()
                data: dict[str, Any] = response.json()
                _raise_on_error(self.model, data)
                embeddings: list[list[float]] = data.get("embeddings") or []
                vectors.extend([float(x) for x in vector] for vector in embeddings)
            return vectors
