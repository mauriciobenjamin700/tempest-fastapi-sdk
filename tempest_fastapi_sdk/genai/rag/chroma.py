"""ChromaDB-backed vector storage — a swappable store plus chat memory.

Two independent building blocks live here, both persisted by ChromaDB and
both keeping the ``chromadb`` import lazy so this module (and the whole
``rag`` package) imports without the ``[genai-chroma]`` extra installed:

- :class:`ChromaVectorStore` implements the :class:`VectorStore` protocol
  (``add`` / ``search``) so it drops into :class:`Retriever` in place of
  the in-memory or pgvector stores.
- :class:`ChatMemory` is a purpose-built, recency-aware memory for chat
  messages: per-message embedding + upsert with metadata, recall scoped
  to a single user (optionally excluding the current chat), a similarity
  floor, a recency-decay re-rank, and a soft per-user quota. It is *not* a
  ``VectorStore`` — it needs metadata-filtered queries the protocol does
  not express.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from tempest_fastapi_sdk.schemas.base import BaseSchema

if TYPE_CHECKING:
    from tempest_fastapi_sdk.genai.rag.retriever import SupportsEmbed
    from tempest_fastapi_sdk.genai.rag.schemas import Chunk


def _require_chromadb() -> Any:
    """Import ``chromadb`` or raise a helpful error.

    Returns:
        Any: The imported ``chromadb`` module.

    Raises:
        ImportError: When the ``[genai-chroma]`` extra is missing.
    """
    try:
        import chromadb
    except ImportError as exc:
        raise ImportError(
            "ChromaDB support requires the optional [genai-chroma] extra. "
            "Install with: pip install tempest-fastapi-sdk[genai-chroma]",
        ) from exc
    return chromadb


def _open_collection(
    *,
    collection_name: str,
    client: Any | None,
    persist_directory: str | None,
    distance: str,
) -> Any:
    """Build (or fetch) a Chroma collection from the given configuration.

    When ``client`` is provided it is used as-is. Otherwise an ephemeral
    (in-memory) client is created when ``persist_directory`` is ``None``, or
    a :class:`chromadb.PersistentClient` rooted at ``persist_directory``.

    Args:
        collection_name (str): The collection to get-or-create.
        client (Any | None): A pre-built Chroma client, or ``None`` to build one.
        persist_directory (str | None): Filesystem path for a persistent client.
        distance (str): HNSW space (``"cosine"``, ``"l2"``, ``"ip"``).

    Returns:
        Any: The Chroma collection handle.
    """
    chromadb = _require_chromadb()
    if client is None:
        chroma_settings = chromadb.config.Settings(anonymized_telemetry=False)
        if persist_directory is not None:
            client = chromadb.PersistentClient(
                path=persist_directory,
                settings=chroma_settings,
            )
        else:
            client = chromadb.EphemeralClient(settings=chroma_settings)
    return client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": distance},
    )


class ChromaVectorStore:
    """A :class:`VectorStore` backed by ChromaDB.

    Mirrors :class:`InMemoryVectorStore` / :class:`PgVectorStore`: ``add``
    upserts chunks with their aligned vectors, ``search`` returns the
    nearest chunks with ``score`` = ``1 - distance``. Chunk fields ride
    along in Chroma metadata and are reconstructed on read. The blocking
    ``chromadb`` calls run inside :func:`asyncio.to_thread`.

    Requires the ``[genai-chroma]`` extra (``chromadb``). The collection is
    opened lazily on first use, so constructing the store never touches the
    filesystem or imports ``chromadb``.

    Attributes:
        collection_name (str): The Chroma collection name.
    """

    def __init__(
        self,
        *,
        collection_name: str = "genai_rag",
        client: Any | None = None,
        persist_directory: str | None = None,
        distance: str = "cosine",
    ) -> None:
        """Initialize the store.

        Args:
            collection_name (str): Collection to get-or-create.
            client (Any | None): Pre-built Chroma client. When ``None`` a
                client is built lazily (ephemeral unless ``persist_directory``
                is set).
            persist_directory (str | None): Path for a persistent client;
                ``None`` uses an in-memory ephemeral client.
            distance (str): HNSW distance space. Defaults to ``"cosine"``.
        """
        self.collection_name = collection_name
        self._client = client
        self._persist_directory = persist_directory
        self._distance = distance
        self._collection: Any | None = None

    def _get_collection(self) -> Any:
        """Return the collection, opening it on first use."""
        if self._collection is None:
            self._collection = _open_collection(
                collection_name=self.collection_name,
                client=self._client,
                persist_directory=self._persist_directory,
                distance=self._distance,
            )
        return self._collection

    async def add(
        self,
        chunks: Sequence[Chunk],
        vectors: Sequence[list[float]],
    ) -> None:
        """Upsert ``chunks`` with their aligned ``vectors``.

        Args:
            chunks (Sequence[Chunk]): The chunks to store.
            vectors (Sequence[list[float]]): One vector per chunk, aligned.

        Raises:
            ValueError: When the counts differ.
        """
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        if not chunks:
            return
        collection = self._get_collection()
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for chunk in chunks:
            ids.append(f"{chunk.source}::{chunk.index}")
            documents.append(chunk.text)
            meta: dict[str, Any] = {"source": chunk.source, "index": chunk.index}
            if chunk.page is not None:
                meta["page"] = chunk.page
            metadatas.append(meta)
        embeddings: list[list[float]] = [list(v) for v in vectors]

        def _upsert() -> None:
            collection.upsert(
                ids=ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
            )

        await asyncio.to_thread(_upsert)

    async def search(self, vector: list[float], *, top_k: int = 5) -> list[Chunk]:
        """Return the ``top_k`` chunks most similar to ``vector``.

        Args:
            vector (list[float]): The query vector.
            top_k (int): How many chunks to return.

        Returns:
            list[Chunk]: Nearest chunks, each with ``score`` = ``1 - distance``.
        """
        from tempest_fastapi_sdk.genai.rag.schemas import Chunk

        if top_k <= 0:
            return []
        collection = self._get_collection()

        def _query() -> dict[str, Any]:
            result: dict[str, Any] = collection.query(
                query_embeddings=[vector],
                n_results=top_k,
                include=["documents", "metadatas", "distances"],
            )
            return result

        raw: dict[str, Any] = await asyncio.to_thread(_query)
        ids_outer: list[list[str]] = raw.get("ids") or []
        if not ids_outer or not ids_outer[0]:
            return []
        documents: list[str] = (raw.get("documents") or [[]])[0]
        metadatas: list[dict[str, Any]] = (raw.get("metadatas") or [[]])[0]
        distances: list[float] = (raw.get("distances") or [[]])[0]
        results: list[Chunk] = []
        for idx in range(len(ids_outer[0])):
            meta: dict[str, Any] = metadatas[idx] if idx < len(metadatas) else {}
            distance: float = float(distances[idx]) if idx < len(distances) else 1.0
            page_value: Any = meta.get("page")
            results.append(
                Chunk(
                    text=documents[idx] if idx < len(documents) else "",
                    source=str(meta.get("source", "")),
                    index=int(meta.get("index", idx)),
                    page=int(page_value) if page_value is not None else None,
                    score=1.0 - distance,
                )
            )
        return results


class MemoryHit(BaseSchema):
    """One recalled chat message, ranked for relevance and recency.

    Attributes:
        content (str): The message text.
        role (str): The originating role (``"user"``, ``"assistant"``, …).
        chat_id (str): The chat the message belongs to.
        created_at (datetime | None): When the message was created (UTC),
            or ``None`` when the stored timestamp could not be parsed.
        similarity (float): Raw cosine similarity in ``[0, 1]`` (the value
            the similarity floor is applied to).
        score (float): Final ranking score after the recency-decay blend
            (equals ``similarity`` when recency re-ranking is disabled).
    """

    content: str
    role: str
    chat_id: str
    created_at: datetime | None = None
    similarity: float
    score: float


class ChatMemory:
    """Recency-aware long-term memory for chat messages, backed by ChromaDB.

    Ports the algorithm the leviathan ``llm-api`` hand-rolls: each message
    is embedded and upserted with metadata (``user_id``, ``chat_id``,
    ``role``, ``created_at``); recall pulls the top-K for a single user
    (optionally excluding the active chat), drops hits below a similarity
    floor, then re-ranks by blending similarity with an exponential recency
    decay so recently-said things can outrank semantically-equal older
    ones. A soft per-user quota evicts the oldest entries when exceeded.

    The embedder is injected (any :class:`SupportsEmbed` — ``Embedder`` or
    ``OllamaEmbedder`` both fit), so this class is embedder-agnostic.

    Scope note: this deliberately does **not** port the app-lifecycle
    retry queue / background retry loop from ``llm-api``. :meth:`index`
    embeds and upserts directly and surfaces failures to the caller
    (embedding/Chroma exceptions propagate); retry policy is a caller
    concern.

    Requires the ``[genai-chroma]`` extra (``chromadb``). The collection is
    opened lazily on first use.

    Attributes:
        collection_name (str): The Chroma collection name.
        top_k (int): Default number of hits returned by :meth:`search`.
        min_similarity (float): Default similarity floor.
        recency_halflife_days (float): Age (days) at which the decay factor
            halves.
        recency_weight (float): Blend weight for the recency decay in
            ``[0, 1]``; ``0`` disables recency re-ranking.
        max_entries_per_user (int): Soft per-user quota; ``0`` disables
            eviction.
        min_content_chars (int): Messages shorter than this (stripped) are
            skipped by :meth:`index`.
    """

    def __init__(
        self,
        embedder: SupportsEmbed,
        *,
        client: Any | None = None,
        persist_directory: str | None = None,
        collection_name: str = "chat_memory",
        top_k: int = 5,
        min_similarity: float = 0.55,
        recency_halflife_days: float = 14.0,
        recency_weight: float = 0.5,
        max_entries_per_user: int = 50_000,
        min_content_chars: int = 6,
    ) -> None:
        """Initialize chat memory.

        Args:
            embedder (SupportsEmbed): Turns text into vectors.
            client (Any | None): Pre-built Chroma client. When ``None`` a
                client is built lazily (ephemeral unless ``persist_directory``
                is set).
            persist_directory (str | None): Path for a persistent client;
                ``None`` uses an in-memory ephemeral client.
            collection_name (str): Collection to get-or-create.
            top_k (int): Default hit count for :meth:`search`.
            min_similarity (float): Default similarity floor in ``[0, 1]``.
            recency_halflife_days (float): Half-life of the recency decay.
            recency_weight (float): Recency blend weight in ``[0, 1]``.
            max_entries_per_user (int): Soft per-user quota (``0`` disables).
            min_content_chars (int): Minimum stripped length to index.
        """
        self._embedder = embedder
        self.collection_name = collection_name
        self._client = client
        self._persist_directory = persist_directory
        self.top_k = top_k
        self.min_similarity = min_similarity
        self.recency_halflife_days = recency_halflife_days
        self.recency_weight = recency_weight
        self.max_entries_per_user = max_entries_per_user
        self.min_content_chars = min_content_chars
        self._collection: Any | None = None

    def _get_collection(self) -> Any:
        """Return the collection, opening it on first use (cosine space)."""
        if self._collection is None:
            self._collection = _open_collection(
                collection_name=self.collection_name,
                client=self._client,
                persist_directory=self._persist_directory,
                distance="cosine",
            )
        return self._collection

    async def index(
        self,
        *,
        user_id: str | UUID,
        chat_id: str | UUID,
        message_id: str | UUID,
        role: str,
        content: str,
        created_at: datetime,
    ) -> bool:
        """Embed a chat message and upsert it into the collection.

        Short messages (stripped length below ``min_content_chars``) are
        skipped. Embedding is done via the injected embedder; the upsert is
        keyed by ``message_id`` so re-indexing the same message is
        idempotent. When the per-user quota is exceeded, the oldest entries
        for that user are evicted first.

        Args:
            user_id (str | UUID): Owner of the message.
            chat_id (str | UUID): Chat the message belongs to.
            message_id (str | UUID): Unique id (used as the Chroma id).
            role (str): The message role.
            content (str): The message text.
            created_at (datetime): Creation timestamp (UTC recommended).

        Returns:
            bool: ``True`` when the message was indexed, ``False`` when it
            was skipped for being too short.

        Raises:
            Exception: Embedding or Chroma failures propagate to the caller
                (no internal retry — see the class scope note).
        """
        text = content.strip()
        if len(text) < self.min_content_chars:
            return False

        vectors: list[list[float]] = await self._embedder.embed([text])
        if not vectors or not vectors[0]:
            return False
        embedding: list[float] = vectors[0]

        metadata: dict[str, Any] = {
            "user_id": str(user_id),
            "chat_id": str(chat_id),
            "message_id": str(message_id),
            "role": role,
            "created_at": _to_iso(created_at),
        }
        collection = self._get_collection()
        await asyncio.to_thread(
            self._evict_over_quota, str(user_id), str(message_id)
        )

        def _upsert() -> None:
            collection.upsert(
                ids=[str(message_id)],
                documents=[text],
                embeddings=[embedding],
                metadatas=[metadata],
            )

        await asyncio.to_thread(_upsert)
        return True

    def _evict_over_quota(self, user_id: str, incoming_message_id: str) -> int:
        """Evict oldest entries beyond ``max_entries_per_user`` for a user.

        Sync helper (Chroma's API is sync) meant to run inside
        :func:`asyncio.to_thread`. The incoming message id is excluded from
        the existing count so idempotent re-indexing never evicts a row it
        is about to overwrite. Eviction is by ``created_at`` ascending.

        Args:
            user_id (str): The user whose entries to bound.
            incoming_message_id (str): The message about to be upserted.

        Returns:
            int: The number of entries actually evicted.
        """
        quota = self.max_entries_per_user
        if quota <= 0:
            return 0
        collection = self._get_collection()
        current = collection.get(where={"user_id": user_id}, include=["metadatas"])
        ids: list[str] = list(current.get("ids") or [])
        metas: list[dict[str, Any]] = list(current.get("metadatas") or [])
        existing: list[tuple[str, dict[str, Any]]] = [
            (entry_id, meta)
            for entry_id, meta in zip(ids, metas, strict=False)
            if entry_id != incoming_message_id
        ]
        overflow = max(0, len(existing) + 1 - quota)
        if overflow == 0:
            return 0
        existing.sort(key=lambda pair: str(pair[1].get("created_at") or ""))
        evict_ids = [entry_id for entry_id, _meta in existing[:overflow]]
        if evict_ids:
            collection.delete(ids=evict_ids)
        return len(evict_ids)

    async def search(
        self,
        *,
        user_id: str | UUID,
        query: str,
        exclude_chat_id: str | UUID | None = None,
        top_k: int | None = None,
        min_similarity: float | None = None,
    ) -> list[MemoryHit]:
        """Return the most relevant past messages for a user.

        Embeds ``query``, runs a metadata-filtered Chroma query scoped to
        ``user_id`` (and excluding ``exclude_chat_id`` when given), drops
        hits below the similarity floor, then re-ranks by the recency-decay
        blend before returning at most ``top_k`` hits.

        Args:
            user_id (str | UUID): Whose memory to search.
            query (str): The natural-language query.
            exclude_chat_id (str | UUID | None): Chat to exclude (typically
                the active conversation, so it does not match itself).
            top_k (int | None): Override for the default hit count.
            min_similarity (float | None): Override for the similarity floor.

        Returns:
            list[MemoryHit]: Up to ``top_k`` hits, ordered by descending
            final score. Empty when the query is blank, ``top_k <= 0`` or
            nothing clears the floor.
        """
        resolved_top_k = self.top_k if top_k is None else top_k
        if resolved_top_k <= 0:
            return []
        floor = self.min_similarity if min_similarity is None else min_similarity
        text = query.strip()
        if not text:
            return []

        vectors: list[list[float]] = await self._embedder.embed([text])
        if not vectors or not vectors[0]:
            return []
        query_embedding: list[float] = vectors[0]

        where: dict[str, Any] = {"user_id": str(user_id)}
        if exclude_chat_id is not None:
            where = {
                "$and": [
                    {"user_id": str(user_id)},
                    {"chat_id": {"$ne": str(exclude_chat_id)}},
                ]
            }

        collection = self._get_collection()

        def _query() -> dict[str, Any]:
            result: dict[str, Any] = collection.query(
                query_embeddings=[query_embedding],
                n_results=resolved_top_k,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
            return result

        raw: dict[str, Any] = await asyncio.to_thread(_query)
        return self._rank(raw, floor, resolved_top_k)

    def _rank(
        self,
        raw: dict[str, Any],
        floor: float,
        top_k: int,
    ) -> list[MemoryHit]:
        """Parse a Chroma query result, apply the floor, and recency re-rank.

        Chroma's ``query`` returns lists-of-lists (one per query embedding);
        a single embedding is always passed, so index 0 is read throughout.
        The similarity floor uses the *raw* similarity so freshness can
        never drag a junk match above the threshold.

        Args:
            raw (dict[str, Any]): The raw Chroma query response.
            floor (float): Minimum raw similarity to keep a hit.
            top_k (int): Maximum number of hits to return.

        Returns:
            list[MemoryHit]: Hits ordered by descending final score.
        """
        ids_outer: list[list[str]] = raw.get("ids") or []
        if not ids_outer or not ids_outer[0]:
            return []
        documents: list[str] = (raw.get("documents") or [[]])[0]
        metadatas: list[dict[str, Any]] = (raw.get("metadatas") or [[]])[0]
        distances: list[float] = (raw.get("distances") or [[]])[0]

        now = datetime.now(UTC)
        scored: list[tuple[float, MemoryHit]] = []
        for idx in range(len(ids_outer[0])):
            doc: str = documents[idx] if idx < len(documents) else ""
            meta: dict[str, Any] = metadatas[idx] if idx < len(metadatas) else {}
            distance: float = float(distances[idx]) if idx < len(distances) else 1.0
            similarity: float = max(0.0, min(1.0, 1.0 - distance))
            if similarity < floor:
                continue
            created_at: datetime | None = _parse_iso(meta.get("created_at"))
            if self.recency_weight > 0 and self.recency_halflife_days > 0:
                decay = _recency_decay(created_at, self.recency_halflife_days, now)
                effective = (
                    (1.0 - self.recency_weight) * similarity
                    + self.recency_weight * similarity * decay
                )
            else:
                effective = similarity
            scored.append(
                (
                    effective,
                    MemoryHit(
                        content=doc,
                        role=str(meta.get("role", "user")),
                        chat_id=str(meta.get("chat_id", "")),
                        created_at=created_at,
                        similarity=similarity,
                        score=effective,
                    ),
                )
            )

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [hit for _, hit in scored[:top_k]]

    async def delete_for_chat(self, *, chat_id: str | UUID) -> None:
        """Remove every vector belonging to a chat.

        Args:
            chat_id (str | UUID): The chat whose entries to delete.
        """
        collection = self._get_collection()

        def _delete() -> None:
            collection.delete(where={"chat_id": str(chat_id)})

        await asyncio.to_thread(_delete)


def _to_iso(value: datetime) -> str:
    """Serialize a datetime to ISO-8601, defaulting naive values to UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _parse_iso(value: Any) -> datetime | None:
    """Best-effort ISO-8601 → datetime; returns ``None`` on failure."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _recency_decay(
    created_at: datetime | None,
    halflife_days: float,
    now: datetime,
) -> float:
    """Exponential decay factor in ``[0, 1]`` from a message's age.

    ``0.5 ** (age_days / halflife_days)`` — a hit created now returns 1.0,
    one exactly a half-life old returns 0.5. Missing/invalid timestamps
    return 1.0 (treat as fresh rather than penalise on a parser bug).

    Args:
        created_at (datetime | None): When the message was created.
        halflife_days (float): Age in days at which the factor halves.
        now (datetime): Reference "now" (UTC).

    Returns:
        float: The decay factor.
    """
    if created_at is None or halflife_days <= 0:
        return 1.0
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    age_seconds = max((now - created_at).total_seconds(), 0.0)
    age_days = age_seconds / 86400.0
    decay: float = 0.5 ** (age_days / halflife_days)
    return decay


__all__: list[str] = [
    "ChatMemory",
    "ChromaVectorStore",
    "MemoryHit",
]
