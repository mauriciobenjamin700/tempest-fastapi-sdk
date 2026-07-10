"""Tests for ChromaVectorStore + ChatMemory (fake Chroma + real ephemeral).

Every store/memory test runs twice: once against a self-contained fake
Chroma client (so the suite passes without the ``[genai-chroma]`` extra)
and once against a real in-memory ``chromadb.EphemeralClient`` when the
extra is installed. The fake computes real cosine distances, so behaviour
matches the real client and tests stay deterministic via the injected
embedder's vectors.
"""

from __future__ import annotations

import importlib.util
import math
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tempest_fastapi_sdk.genai.rag import (
    ChatMemory,
    ChromaVectorStore,
    Chunk,
    MemoryHit,
)

_HAS_CHROMA: bool = importlib.util.find_spec("chromadb") is not None


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #
class _FakeEmbedder:
    """Deterministic embedder: maps text to a canned vector."""

    def __init__(self, table: dict[str, list[float]]) -> None:
        self.table = table

    async def embed(
        self,
        texts: str | list[str],
        *,
        batch_size: int = 32,
    ) -> list[list[float]]:
        items = [texts] if isinstance(texts, str) else list(texts)
        return [list(self.table.get(t, [0.001, 0.001])) for t in items]


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0
    return 1.0 - dot / (norm_a * norm_b)


def _match(meta: dict[str, Any], where: dict[str, Any] | None) -> bool:
    """Evaluate the subset of Chroma ``where`` syntax the SDK emits."""
    if not where:
        return True
    if "$and" in where:
        return all(_match(meta, clause) for clause in where["$and"])
    for key, cond in where.items():
        value = meta.get(key)
        if isinstance(cond, dict):
            if "$ne" in cond and value == cond["$ne"]:
                return False
        elif value != cond:
            return False
    return True


class _FakeCollection:
    """In-memory stand-in for a Chroma collection (records + real cosine)."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self.upserts: list[dict[str, Any]] = []

    def upsert(
        self,
        *,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> None:
        self.upserts.append({"ids": list(ids)})
        for entry_id, doc, vec, meta in zip(
            ids, documents, embeddings, metadatas, strict=True
        ):
            self._store[entry_id] = {
                "document": doc,
                "embedding": list(vec),
                "metadata": dict(meta),
            }

    def get(
        self,
        *,
        where: dict[str, Any] | None = None,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        ids: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for entry_id, row in self._store.items():
            if _match(row["metadata"], where):
                ids.append(entry_id)
                metadatas.append(row["metadata"])
        return {"ids": ids, "metadatas": metadatas}

    def query(
        self,
        *,
        query_embeddings: list[list[float]],
        n_results: int,
        where: dict[str, Any] | None = None,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        query_vec = query_embeddings[0]
        scored: list[tuple[float, str, dict[str, Any]]] = []
        for entry_id, row in self._store.items():
            if not _match(row["metadata"], where):
                continue
            distance = _cosine_distance(query_vec, row["embedding"])
            scored.append((distance, entry_id, row))
        scored.sort(key=lambda item: item[0])
        scored = scored[:n_results]
        return {
            "ids": [[entry_id for _, entry_id, _ in scored]],
            "documents": [[row["document"] for _, _, row in scored]],
            "metadatas": [[row["metadata"] for _, _, row in scored]],
            "distances": [[dist for dist, _, _ in scored]],
        }

    def delete(
        self,
        *,
        where: dict[str, Any] | None = None,
        ids: list[str] | None = None,
    ) -> None:
        if ids is not None:
            for entry_id in ids:
                self._store.pop(entry_id, None)
            return
        for entry_id in [
            eid for eid, row in self._store.items() if _match(row["metadata"], where)
        ]:
            self._store.pop(entry_id, None)

    def count(self) -> int:
        return len(self._store)


class _FakeChromaClient:
    """Returns a shared fake collection per name."""

    def __init__(self) -> None:
        self._collections: dict[str, _FakeCollection] = {}

    def get_or_create_collection(
        self,
        *,
        name: str,
        metadata: dict[str, Any] | None = None,
    ) -> _FakeCollection:
        return self._collections.setdefault(name, _FakeCollection())


@pytest.fixture(params=["fake", "real"])
def chroma_client(request: pytest.FixtureRequest) -> Any:
    """Yield a fake client, plus a real ephemeral one when chromadb is present."""
    if request.param == "real":
        if not _HAS_CHROMA:
            pytest.skip("chromadb not installed")
        import chromadb

        return chromadb.EphemeralClient(
            settings=chromadb.config.Settings(anonymized_telemetry=False)
        )
    return _FakeChromaClient()


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# ChromaVectorStore                                                            #
# --------------------------------------------------------------------------- #
class TestChromaVectorStore:
    async def test_add_and_search_round_trip(self, chroma_client: Any) -> None:
        store = ChromaVectorStore(client=chroma_client, collection_name="vectors")
        await store.add(
            [
                Chunk(text="alpha", source="kb", index=0, page=3),
                Chunk(text="beta", source="kb", index=1),
            ],
            [[1.0, 0.0], [0.0, 1.0]],
        )
        results = await store.search([1.0, 0.0], top_k=1)
        assert len(results) == 1
        assert results[0].text == "alpha"
        assert results[0].source == "kb"
        assert results[0].index == 0
        assert results[0].page == 3
        assert results[0].score == pytest.approx(1.0)

    async def test_add_length_mismatch(self, chroma_client: Any) -> None:
        store = ChromaVectorStore(client=chroma_client, collection_name="vectors2")
        with pytest.raises(ValueError):
            await store.add([Chunk(text="a", source="kb", index=0)], [[1.0], [2.0]])

    @pytest.mark.skipif(
        _HAS_CHROMA,
        reason="chromadb installed; the missing-extra path can't be exercised",
    )
    async def test_add_without_extra_raises(self) -> None:
        store = ChromaVectorStore()
        with pytest.raises(ImportError, match=r"\[genai-chroma\]"):
            await store.add([Chunk(text="a", source="kb", index=0)], [[1.0, 0.0]])


# --------------------------------------------------------------------------- #
# ChatMemory                                                                   #
# --------------------------------------------------------------------------- #
class TestChatMemory:
    async def test_index_skips_short_content(self, chroma_client: Any) -> None:
        memory = ChatMemory(
            _FakeEmbedder({}),
            client=chroma_client,
            collection_name="m-short",
            min_content_chars=6,
        )
        indexed = await memory.index(
            user_id="u1",
            chat_id="c1",
            message_id="msg1",
            role="user",
            content="hi",
            created_at=_now(),
        )
        assert indexed is False

    async def test_index_and_search_round_trip(self, chroma_client: Any) -> None:
        embedder = _FakeEmbedder(
            {
                "the cat sat on the mat": [1.0, 0.0],
                "dogs bark loudly": [0.0, 1.0],
                "cat": [1.0, 0.0],
            }
        )
        memory = ChatMemory(embedder, client=chroma_client, collection_name="m-rt")
        assert await memory.index(
            user_id="u1",
            chat_id="c1",
            message_id="msg1",
            role="user",
            content="the cat sat on the mat",
            created_at=_now(),
        )
        assert await memory.index(
            user_id="u1",
            chat_id="c2",
            message_id="msg2",
            role="user",
            content="dogs bark loudly",
            created_at=_now(),
        )
        hits = await memory.search(user_id="u1", query="cat", top_k=1)
        assert len(hits) == 1
        assert isinstance(hits[0], MemoryHit)
        assert hits[0].content == "the cat sat on the mat"
        assert hits[0].chat_id == "c1"
        assert hits[0].role == "user"

    async def test_search_scopes_to_user(self, chroma_client: Any) -> None:
        embedder = _FakeEmbedder({"topic text one": [1.0, 0.0], "topic": [1.0, 0.0]})
        memory = ChatMemory(embedder, client=chroma_client, collection_name="m-scope")
        await memory.index(
            user_id="owner",
            chat_id="c1",
            message_id="mine",
            role="user",
            content="topic text one",
            created_at=_now(),
        )
        await memory.index(
            user_id="stranger",
            chat_id="c9",
            message_id="theirs",
            role="user",
            content="topic text one",
            created_at=_now(),
        )
        hits = await memory.search(user_id="owner", query="topic", top_k=5)
        assert [h.chat_id for h in hits] == ["c1"]

    async def test_search_excludes_chat(self, chroma_client: Any) -> None:
        embedder = _FakeEmbedder({"same topic here": [1.0, 0.0], "topic": [1.0, 0.0]})
        memory = ChatMemory(embedder, client=chroma_client, collection_name="m-excl")
        await memory.index(
            user_id="u1",
            chat_id="active",
            message_id="a",
            role="user",
            content="same topic here",
            created_at=_now(),
        )
        await memory.index(
            user_id="u1",
            chat_id="other",
            message_id="b",
            role="user",
            content="same topic here",
            created_at=_now(),
        )
        hits = await memory.search(
            user_id="u1", query="topic", exclude_chat_id="active", top_k=5
        )
        assert [h.chat_id for h in hits] == ["other"]

    async def test_min_similarity_floor(self, chroma_client: Any) -> None:
        embedder = _FakeEmbedder(
            {"weakly related text": [0.9, 0.4358898943540674], "query": [1.0, 0.0]}
        )
        memory = ChatMemory(embedder, client=chroma_client, collection_name="m-floor")
        await memory.index(
            user_id="u1",
            chat_id="c1",
            message_id="m",
            role="user",
            content="weakly related text",
            created_at=_now(),
        )
        # Similarity is ~0.9; a 0.95 floor drops it.
        hits = await memory.search(user_id="u1", query="query", min_similarity=0.95)
        assert hits == []
        # A permissive floor keeps it.
        kept = await memory.search(user_id="u1", query="query", min_similarity=0.5)
        assert len(kept) == 1

    async def test_recency_reranks_over_similarity(self, chroma_client: Any) -> None:
        embedder = _FakeEmbedder(
            {
                "older stronger match": [1.0, 0.0],
                "newer weaker match": [0.9, 0.4358898943540674],
                "query": [1.0, 0.0],
            }
        )
        memory = ChatMemory(
            embedder,
            client=chroma_client,
            collection_name="m-recency",
            recency_halflife_days=14.0,
            recency_weight=0.5,
        )
        now = _now()
        await memory.index(
            user_id="u1",
            chat_id="c1",
            message_id="old",
            role="user",
            content="older stronger match",
            created_at=now - timedelta(days=60),
        )
        await memory.index(
            user_id="u1",
            chat_id="c2",
            message_id="new",
            role="user",
            content="newer weaker match",
            created_at=now,
        )
        hits = await memory.search(user_id="u1", query="query", top_k=5)
        assert len(hits) == 2
        # Newer, lower-similarity hit wins after the recency blend.
        assert hits[0].content == "newer weaker match"
        assert hits[1].content == "older stronger match"
        assert hits[0].score > hits[1].score
        # Raw similarity is still recorded (older one is the stronger match).
        assert hits[1].similarity > hits[0].similarity

    async def test_eviction_over_quota(self, chroma_client: Any) -> None:
        embedder = _FakeEmbedder(
            {
                "message number one": [1.0, 0.0],
                "message number two": [1.0, 0.0],
                "message number three": [1.0, 0.0],
                "query": [1.0, 0.0],
            }
        )
        memory = ChatMemory(
            embedder,
            client=chroma_client,
            collection_name="m-evict",
            max_entries_per_user=2,
        )
        base = _now()
        for offset, (mid, content) in enumerate(
            [
                ("m1", "message number one"),
                ("m2", "message number two"),
                ("m3", "message number three"),
            ]
        ):
            await memory.index(
                user_id="u1",
                chat_id="c1",
                message_id=mid,
                role="user",
                content=content,
                created_at=base + timedelta(minutes=offset),
            )
        collection = memory._get_collection()
        assert collection.count() == 2
        remaining = set(collection.get(where={"user_id": "u1"})["ids"])
        assert remaining == {"m2", "m3"}

    async def test_delete_for_chat(self, chroma_client: Any) -> None:
        embedder = _FakeEmbedder({"content to delete": [1.0, 0.0], "query": [1.0, 0.0]})
        memory = ChatMemory(embedder, client=chroma_client, collection_name="m-del")
        await memory.index(
            user_id="u1",
            chat_id="doomed",
            message_id="m1",
            role="user",
            content="content to delete",
            created_at=_now(),
        )
        await memory.delete_for_chat(chat_id="doomed")
        hits = await memory.search(user_id="u1", query="query", top_k=5)
        assert hits == []

    @pytest.mark.skipif(
        _HAS_CHROMA,
        reason="chromadb installed; the missing-extra path can't be exercised",
    )
    async def test_index_without_extra_raises(self) -> None:
        memory = ChatMemory(_FakeEmbedder({"long enough content": [1.0, 0.0]}))
        with pytest.raises(ImportError, match=r"\[genai-chroma\]"):
            await memory.index(
                user_id="u1",
                chat_id="c1",
                message_id="m1",
                role="user",
                content="long enough content",
                created_at=_now(),
            )
