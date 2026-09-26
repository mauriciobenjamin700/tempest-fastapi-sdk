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
    replaces every chunk stored for the sources in the batch and writes the
    batch, ``search`` returns the nearest chunks with
    ``score`` = ``1 - distance``. Each chunk's Chroma id is the SHA-256 of
    ``(source, index, text)`` — ``source::index`` collided whenever
    :func:`chunk_text` was called twice for one source. Chunk fields ride
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
        """Store ``chunks``, replacing what their sources held before.

        The batch is upserted first; then every other row whose ``source``
        metadata matches a source in ``chunks`` is deleted (rows written by
        older SDK versions, keyed ``source::index``, included). Chroma has
        no transactions, so this order means a failed upsert leaves the
        previous version in place rather than an empty source. Identical
        chunks in the batch are written once.

        Args:
            chunks (Sequence[Chunk]): The chunks to store — every chunk of a
                source in one call.
            vectors (Sequence[list[float]]): One vector per chunk, aligned.

        Raises:
            ValueError: When the counts differ.
        """
        from tempest_fastapi_sdk.genai.rag.schemas import (
            _chunk_identity,
            _unique_batch,
        )

        pairs = _unique_batch(chunks, vectors)
        if not pairs:
            return
        collection = self._get_collection()
        sources: list[str] = sorted({chunk.source for chunk, _ in pairs})
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        embeddings: list[list[float]] = []
        for chunk, vector in pairs:
            ids.append(_chunk_identity(chunk))
            documents.append(chunk.text)
            meta: dict[str, Any] = {"source": chunk.source, "index": chunk.index}
            if chunk.page is not None:
                meta["page"] = chunk.page
            metadatas.append(meta)
            embeddings.append(vector)

        def _replace() -> None:
            collection.upsert(
                ids=ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
            )
            fresh = set(ids)
            stored = collection.get(where={"source": {"$in": sources}}, include=[])
            stale = [i for i in stored.get("ids") or [] if i not in fresh]
            if stale:
                collection.delete(ids=stale)

        await asyncio.to_thread(_replace)

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
    ones. Recall over-fetches ``top_k * candidate_multiplier`` nearest
    messages before the re-rank, so a recent message just outside the raw
    top-K can still surface. A soft per-user quota evicts the oldest
    entries (by UTC instant) when exceeded.

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
        candidate_multiplier (int): How many nearest messages per returned
            hit :meth:`search` asks Chroma for before the recency re-rank.
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
        candidate_multiplier: int = 4,
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
            candidate_multiplier (int): Over-fetch factor for :meth:`search`:
                Chroma is asked for ``top_k * candidate_multiplier`` nearest
                messages, and the recency re-rank picks ``top_k`` of them.
                ``1`` re-ranks only the raw top-K, where a recent message
                ranked just below it can never surface.

        Raises:
            ValueError: When ``candidate_multiplier`` is below 1.
        """
        if candidate_multiplier < 1:
            raise ValueError("candidate_multiplier must be >= 1")
        self.candidate_multiplier = candidate_multiplier
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
            "created_at_ts": _to_epoch(created_at),
        }
        collection = self._get_collection()
        await asyncio.to_thread(self._evict_over_quota, str(user_id), str(message_id))

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
        is about to overwrite. The count reads ids only; metadata is fetched
        just when the quota is actually exceeded. Eviction is oldest UTC
        instant first — ``created_at_ts`` (epoch seconds), falling back to
        parsing ``created_at`` for rows written before that field existed.
        Sorting the ISO strings instead ranks ``10:00+05:00`` after
        ``08:00+00:00`` although it is three hours earlier.

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
        where: dict[str, Any] = {"user_id": user_id}
        counted = collection.get(where=where, include=[])
        others = [i for i in counted.get("ids") or [] if i != incoming_message_id]
        overflow = max(0, len(others) + 1 - quota)
        if overflow == 0:
            return 0
        current = collection.get(where=where, include=["metadatas"])
        ids: list[str] = list(current.get("ids") or [])
        metas: list[dict[str, Any]] = list(current.get("metadatas") or [])
        existing: list[tuple[str, dict[str, Any]]] = [
            (entry_id, meta or {})
            for entry_id, meta in zip(ids, metas, strict=False)
            if entry_id != incoming_message_id
        ]
        existing.sort(key=lambda pair: _stored_epoch(pair[1]))
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
        ``user_id`` (and excluding ``exclude_chat_id`` when given) for the
        ``top_k * candidate_multiplier`` nearest messages, drops hits below
        the similarity floor, then re-ranks by the recency-decay blend
        before returning at most ``top_k`` hits.

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
                n_results=resolved_top_k * self.candidate_multiplier,
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
                    1.0 - self.recency_weight
                ) * similarity + self.recency_weight * similarity * decay
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
    """Serialize a datetime to ISO-8601 in UTC, treating naive values as UTC.

    Args:
        value (datetime): The instant to serialize.

    Returns:
        str: The instant converted to UTC, as ISO-8601 with ``+00:00``.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _to_epoch(value: datetime) -> float:
    """Return a datetime as UTC epoch seconds, treating naive values as UTC.

    Args:
        value (datetime): The instant to convert.

    Returns:
        float: Seconds since the Unix epoch.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _stored_epoch(meta: dict[str, Any]) -> float:
    """Return the UTC instant a stored message was created, for ordering.

    Reads ``created_at_ts`` when present and falls back to parsing the ISO
    ``created_at`` (rows indexed by SDK versions without the epoch field).
    A row with neither sorts first — oldest — which matches how the
    previous string sort ordered an empty timestamp.

    Args:
        meta (dict[str, Any]): The Chroma metadata of one message.

    Returns:
        float: Epoch seconds, or ``-inf`` when no timestamp is readable.
    """
    stamp: Any = meta.get("created_at_ts")
    if isinstance(stamp, int | float) and not isinstance(stamp, bool):
        return float(stamp)
    parsed = _parse_iso(meta.get("created_at"))
    if parsed is None:
        return float("-inf")
    return _to_epoch(parsed)


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
