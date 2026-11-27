"""Task prefixes on :class:`OllamaEmbedder` (#864).

``nomic-embed-text`` was trained with ``search_query: `` on the question and
``search_document: `` on the passage. The daemon is replaced by
``httpx.MockTransport`` and every test asserts the exact ``input`` the
embedder sent, which is the only place the prefix is observable.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx

from tempest_fastapi_sdk.genai import (
    MULTILINGUAL_E5_PREFIXES,
    NOMIC_EMBED_TEXT_PREFIXES,
    Embedder,
    EmbeddingPrefixes,
    OllamaEmbedder,
)
from tempest_fastapi_sdk.genai.rag import Chunk, InMemoryVectorStore, Retriever


def _recording_handler(
    sent: list[list[str]],
) -> Callable[[httpx.Request], httpx.Response]:
    """Build a transport handler that records every ``input`` batch.

    Args:
        sent (list[list[str]]): Receives the ``input`` list of each request.

    Returns:
        Callable[[httpx.Request], httpx.Response]: The handler, which answers
            one unit vector per input text.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        """Record the batch and answer one vector per text.

        Args:
            request (httpx.Request): The ``/api/embed`` request.

        Returns:
            httpx.Response: ``200`` with one vector per input.
        """
        body: dict[str, list[str]] = json.loads(request.content)
        sent.append(body["input"])
        return httpx.Response(
            200,
            json={"embeddings": [[1.0, 0.0] for _ in body["input"]]},
        )

    return handler


def _embedder(sent: list[list[str]], prefixes: EmbeddingPrefixes) -> OllamaEmbedder:
    """Build an ``OllamaEmbedder`` over the recording transport.

    Args:
        sent (list[list[str]]): Receives the ``input`` list of each request.
        prefixes (EmbeddingPrefixes): The prefixes to configure.

    Returns:
        OllamaEmbedder: The embedder under test.
    """
    return OllamaEmbedder(
        "nomic-embed-text",
        transport=httpx.MockTransport(_recording_handler(sent)),
        **prefixes,
    )


class TestOllamaEmbedderPrefixes:
    """``embed_query``/``embed_passages`` prepend; ``embed`` never does."""

    async def test_embed_query_sends_query_prefix(self) -> None:
        sent: list[list[str]] = []
        emb = _embedder(sent, NOMIC_EMBED_TEXT_PREFIXES)
        await emb.embed_query("como estornar?")
        await emb.aclose()

        assert sent == [["search_query: como estornar?"]]

    async def test_embed_passages_sends_passage_prefix(self) -> None:
        sent: list[list[str]] = []
        emb = _embedder(sent, NOMIC_EMBED_TEXT_PREFIXES)
        await emb.embed_passages(["a", "b", "c"], batch_size=2)
        await emb.aclose()

        assert sent == [
            ["search_document: a", "search_document: b"],
            ["search_document: c"],
        ]

    async def test_embed_stays_unprefixed(self) -> None:
        sent: list[list[str]] = []
        emb = _embedder(sent, NOMIC_EMBED_TEXT_PREFIXES)
        await emb.embed(["raw"])
        await emb.aclose()

        assert sent == [["raw"]]

    async def test_default_has_no_prefix(self) -> None:
        sent: list[list[str]] = []
        emb = OllamaEmbedder(
            "nomic-embed-text",
            transport=httpx.MockTransport(_recording_handler(sent)),
        )
        await emb.embed_query("q")
        await emb.embed_passages("p")
        await emb.aclose()

        assert emb.query_prefix == ""
        assert emb.passage_prefix == ""
        assert sent == [["q"], ["p"]]

    async def test_empty_input_sends_nothing(self) -> None:
        sent: list[list[str]] = []
        emb = _embedder(sent, NOMIC_EMBED_TEXT_PREFIXES)

        assert await emb.embed_query([]) == []
        assert await emb.embed_passages([]) == []
        assert sent == []


class TestRetrieverUsesRoleMethods:
    """Contract: the ``Retriever`` routes each side through its method."""

    async def test_index_and_search_send_their_prefixes(self) -> None:
        sent: list[list[str]] = []
        rag = Retriever(
            _embedder(sent, NOMIC_EMBED_TEXT_PREFIXES), InMemoryVectorStore()
        )
        await rag.index(
            [Chunk(text="O reembolso cai em 7 dias.", source="faq", index=0)]
        )
        hits = await rag.search("Quanto demora o reembolso?", top_k=1)

        assert sent == [
            ["search_document: O reembolso cai em 7 dias."],
            ["search_query: Quanto demora o reembolso?"],
        ]
        assert hits[0].text == "O reembolso cai em 7 dias."


class TestPresets:
    """The presets are the strings the model cards publish, fixed here."""

    def test_nomic_embed_text(self) -> None:
        assert NOMIC_EMBED_TEXT_PREFIXES == {
            "query_prefix": "search_query: ",
            "passage_prefix": "search_document: ",
        }

    def test_multilingual_e5(self) -> None:
        assert MULTILINGUAL_E5_PREFIXES == {
            "query_prefix": "query: ",
            "passage_prefix": "passage: ",
        }

    def test_presets_splat_into_the_torch_embedder(self) -> None:
        emb = Embedder("intfloat/multilingual-e5-large", **MULTILINGUAL_E5_PREFIXES)

        assert emb.query_prefix == "query: "
        assert emb.passage_prefix == "passage: "
