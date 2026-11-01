"""Postgres-backed chat memory — :class:`ChatMemory` without ChromaDB.

:class:`PgChatMemory` keeps the public contract and the recall algorithm of
:class:`~tempest_fastapi_sdk.genai.rag.ChatMemory` (similarity floor,
recency re-rank, over-fetch, soft per-user quota, ``min_content_chars``)
but stores the vectors in a pgvector table of the database the service
already runs. That keeps memory inside the application's backups and
transactions — deleting a conversation and its memory can be one
``COMMIT`` — and drops the ``chromadb`` dependency tree entirely.

Requires a Postgres with ``CREATE EXTENSION vector`` and an async driver
(``asyncpg``, from the ``[postgres]`` extra). Vectors are sent as their
text form and cast by Postgres, so the ``pgvector`` Python package is not
needed. Nothing database-related is imported until first use.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from tempest_fastapi_sdk.genai.rag.memory import (
    MemoryHit,
    _check_candidate_multiplier,
    _MemoryCandidate,
    _rank_hits,
)
from tempest_fastapi_sdk.genai.rag.retriever import _embed_passages, _embed_query
from tempest_fastapi_sdk.genai.rag.vectorstore import (
    AnnIndex,
    _ann_options,
    _check_pg_target,
    _embedding_index_name,
    _ensure_ann_index,
    _parse_version,
    _positive_int,
    _set_search_options,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager
    from tempest_fastapi_sdk.genai.rag.retriever import SupportsEmbed


_ITERATIVE_SCAN_MIN_PGVECTOR: tuple[int, int, int] = (0, 8, 0)
"""The first pgvector release with iterative index scans.

Taken from pgvector's CHANGELOG, entry ``0.8.0``: "Added support for
iterative index scans". Before it, an HNSW scan yields at most
``hnsw.ef_search`` rows and the ``user_id`` filter runs on those, so a
user owning a small share of a large table gets fewer hits than exist —
measured on pgvector 0.8.6 with iterative scans off: a user owning 500 of
50 000 rows got 0 hits.
"""

_ITERATIVE_SCAN_SETTINGS: dict[str, str] = {
    "hnsw.iterative_scan": "strict_order",
    "ivfflat.iterative_scan": "relaxed_order",
}
"""Per-search settings that keep an approximate scan going past the filter.

``ivfflat`` only offers ``relaxed_order``; the recency re-rank re-sorts the
candidates anyway.
"""


class PgChatMemory:
    """Recency-aware long-term chat memory stored in Postgres with pgvector.

    A sibling of :class:`~tempest_fastapi_sdk.genai.rag.ChatMemory` with the
    same ``index`` / ``search`` / ``delete_for_chat`` / ``delete_for_user``
    surface and the same ranking, so either one fits wherever the other
    does (``AIChatPipeline(memory=...)`` accepts both).

    One row per message, keyed by ``message_id``: ``user_id``, ``chat_id``,
    ``role``, ``content``, ``created_at`` (``timestamptz``) and
    ``embedding vector(dim)``. Every search is
    ``WHERE user_id = :user_id`` (plus ``chat_id <> :exclude`` when asked)
    ordered by the cosine distance ``<=>``, so one user's recall never
    reads another user's rows. Without an approximate index the search is
    an exact scan of that user's rows; :meth:`ensure_schema` can build an
    HNSW or IVFFlat index for large tables.

    Sessions come from the service's
    :class:`~tempest_fastapi_sdk.db.connection.AsyncDatabaseManager`. The
    two deletes also accept the caller's ``session``, so erasing a chat or
    an account and its memory commit or roll back together.

    Attributes:
        table (str): The table holding messages + embeddings.
        dim (int): The embedding dimension (must match the model).
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
            hit :meth:`search` reads before the recency re-rank.
        ef_search (int | None): ``hnsw.ef_search`` applied to every search,
            or ``None`` to keep the server setting.
        probes (int | None): ``ivfflat.probes`` applied to every search, or
            ``None`` to keep the server setting.
    """

    def __init__(
        self,
        embedder: SupportsEmbed,
        db: AsyncDatabaseManager,
        *,
        dim: int,
        table: str = "chat_memory",
        top_k: int = 5,
        min_similarity: float = 0.55,
        recency_halflife_days: float = 14.0,
        recency_weight: float = 0.5,
        max_entries_per_user: int = 50_000,
        min_content_chars: int = 6,
        candidate_multiplier: int = 4,
        ef_search: int | None = None,
        probes: int | None = None,
    ) -> None:
        """Initialize chat memory over a pgvector table.

        Args:
            embedder (SupportsEmbed): Turns text into vectors.
            db (AsyncDatabaseManager): The service's database manager.
            dim (int): Embedding dimension (e.g. 768 for
                ``nomic-embed-text``).
            table (str): Table name, bare or ``schema.table``, unquoted.
            top_k (int): Default hit count for :meth:`search`.
            min_similarity (float): Default similarity floor in ``[0, 1]``.
            recency_halflife_days (float): Half-life of the recency decay.
            recency_weight (float): Recency blend weight in ``[0, 1]``.
            max_entries_per_user (int): Soft per-user quota (``0`` disables).
            min_content_chars (int): Minimum stripped length to index.
            candidate_multiplier (int): Over-fetch factor for :meth:`search`:
                the ``top_k * candidate_multiplier`` nearest messages are
                read, and the recency re-rank picks ``top_k`` of them.
            ef_search (int | None): ``hnsw.ef_search`` for every search:
                the HNSW candidate list size. With iterative scans (pgvector
                0.8.0+, which an approximate index here requires) the scan
                keeps going past it until enough of the user's rows match.
            probes (int | None): ``ivfflat.probes`` for every search.

        Raises:
            ValueError: When ``table`` is not a plain Postgres identifier,
                ``dim`` / ``ef_search`` / ``probes`` is not a positive
                integer, or ``candidate_multiplier`` is below 1.
        """
        _check_pg_target(table=table, dim=dim)
        _check_candidate_multiplier(candidate_multiplier)
        _positive_int("ef_search", ef_search)
        _positive_int("probes", probes)
        self._embedder = embedder
        self._db = db
        self.dim = dim
        self.table = table
        self.top_k = top_k
        self.min_similarity = min_similarity
        self.recency_halflife_days = recency_halflife_days
        self.recency_weight = recency_weight
        self.max_entries_per_user = max_entries_per_user
        self.min_content_chars = min_content_chars
        self.candidate_multiplier = candidate_multiplier
        self.ef_search = ef_search
        self.probes = probes
        self._ready = False
        self._iterative_scan = False

    @property
    def ann_index_name(self) -> str:
        """The approximate index's name, schema-qualified like ``table``.

        Returns:
            str: ``<table>_embedding_idx``, truncated to fit Postgres'
            63-byte identifier limit.
        """
        return _embedding_index_name(self.table)

    async def ensure_schema(
        self,
        *,
        ann_index: AnnIndex | None = None,
        m: int | None = None,
        ef_construction: int | None = None,
        lists: int | None = None,
    ) -> None:
        """Create the pgvector extension, the memory table and its indexes.

        Always creates two B-tree indexes: ``(user_id, created_at)`` for the
        per-user filter and the quota's oldest-first eviction, and
        ``(user_id, chat_id)`` for :meth:`delete_for_chat`. With
        ``ann_index`` it also builds an approximate index on ``embedding``
        (``vector_cosine_ops``), exactly as
        :meth:`PgVectorStore.ensure_schema` does. Called lazily, without an
        approximate index, by the first operation that needs the table.

        ``CREATE EXTENSION`` needs a role allowed to create it; where the
        application role is not, run it once as a superuser beforehand.

        An approximate index needs pgvector 0.8.0 or newer: every search
        filters by ``user_id``, and only iterative index scans (which
        :meth:`search` turns on for its own transaction) keep scanning
        until enough of **that user's** rows are found. Without them an
        HNSW scan stops after ``hnsw.ef_search`` rows of any user, and a
        user owning a small share of the table gets fewer hits than exist.

        Args:
            ann_index (AnnIndex | None): ``"hnsw"``, ``"ivfflat"`` or
                ``None`` (search stays an exact scan of the user's rows).
            m (int | None): HNSW max connections per layer.
            ef_construction (int | None): HNSW candidate list size while
                building.
            lists (int | None): IVFFlat inverted list count.

        Raises:
            ValueError: When a parameter is invalid for ``ann_index``, or
                the index already exists with another method/parameters.
            RuntimeError: When ``ann_index`` is set and the installed
                pgvector is older than 0.8.0.
        """
        options = _ann_options(
            ann_index,
            m=m,
            ef_construction=ef_construction,
            lists=lists,
        )
        from sqlalchemy import text

        bare = self.table.rsplit(".", 1)[-1]
        async with self._db.get_session_context() as session:
            await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await session.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {self.table} ("
                    "message_id TEXT PRIMARY KEY, "
                    "user_id TEXT NOT NULL, "
                    "chat_id TEXT NOT NULL, "
                    "role TEXT NOT NULL, "
                    "content TEXT NOT NULL, "
                    "created_at TIMESTAMPTZ NOT NULL, "
                    f"embedding vector({self.dim}) NOT NULL)",
                ),
            )
            for suffix, columns in (
                ("_user_created_idx", "user_id, created_at"),
                ("_user_chat_idx", "user_id, chat_id"),
            ):
                name = f"{bare[: 63 - len(suffix)]}{suffix}"
                await session.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS {name} "
                        f"ON {self.table} ({columns})",
                    ),
                )
            raw_version = (
                await session.execute(
                    text(
                        "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
                    ),
                )
            ).scalar_one()
            supported = _parse_version(raw_version) >= _ITERATIVE_SCAN_MIN_PGVECTOR
            if ann_index is not None and not supported:
                raise RuntimeError(
                    f"ann_index={ann_index!r} on PgChatMemory needs pgvector "
                    f">= 0.8.0 (iterative index scans), but the database has "
                    f"{raw_version}; without them the per-user filter drops "
                    "hits. Run ALTER EXTENSION vector UPDATE or keep the "
                    "exact scan (ann_index=None).",
                )
            if ann_index is not None:
                await _ensure_ann_index(
                    session,
                    table=self.table,
                    index_name=self.ann_index_name,
                    ann_index=ann_index,
                    options=options,
                )
        self._iterative_scan = supported
        self._ready = True

    async def _ensure_ready(self) -> None:
        """Create the schema on first use when :meth:`ensure_schema` was skipped."""
        if not self._ready:
            await self.ensure_schema()

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
        """Embed a chat message and upsert it, keyed by ``message_id``.

        Short messages (stripped length below ``min_content_chars``) are
        skipped. Re-indexing the same ``message_id`` replaces the row. The
        quota eviction and the upsert run in one transaction: when the user
        is over ``max_entries_per_user``, the oldest rows (by ``created_at``,
        a ``timestamptz``, so the UTC instant) are deleted first, never the
        message being written.

        A ``message_id`` already stored for a **different** user is refused
        (the upsert's ``WHERE`` matches nothing) and the transaction rolls
        back, eviction included.

        Args:
            user_id (str | UUID): Owner of the message.
            chat_id (str | UUID): Chat the message belongs to.
            message_id (str | UUID): Unique id (the primary key).
            role (str): The message role.
            content (str): The message text.
            created_at (datetime): Creation timestamp; naive values are
                read as UTC.

        Returns:
            bool: ``True`` when the message was indexed, ``False`` when it
            was skipped for being too short.

        Raises:
            ValueError: When ``message_id`` is already indexed for another
                user, or the embedder returned a vector whose length is not
                ``dim``.
            Exception: Embedding or database failures propagate to the
                caller (no internal retry).
        """
        stripped = content.strip()
        if len(stripped) < self.min_content_chars:
            return False
        vectors: list[list[float]] = await _embed_passages(self._embedder, [stripped])
        if not vectors or not vectors[0]:
            return False
        embedding: list[float] = vectors[0]
        if len(embedding) != self.dim:
            raise ValueError(
                f"embedder returned {len(embedding)} dimensions, table "
                f"{self.table} stores {self.dim}",
            )
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        await self._ensure_ready()
        from sqlalchemy import text

        owner = str(user_id)
        key = str(message_id)
        async with self._db.get_session_context() as session:
            await self._evict_over_quota(session, owner, key)
            written = (
                await session.execute(
                    text(
                        f"INSERT INTO {self.table} AS memory "
                        "(message_id, user_id, chat_id, role, content, "
                        "created_at, embedding) VALUES "
                        "(:message_id, :user_id, :chat_id, :role, :content, "
                        ":created_at, CAST(:embedding AS vector)) "
                        "ON CONFLICT (message_id) DO UPDATE SET "
                        "chat_id = EXCLUDED.chat_id, role = EXCLUDED.role, "
                        "content = EXCLUDED.content, "
                        "created_at = EXCLUDED.created_at, "
                        "embedding = EXCLUDED.embedding "
                        "WHERE memory.user_id = EXCLUDED.user_id "
                        "RETURNING message_id",
                    ),
                    {
                        "message_id": key,
                        "user_id": owner,
                        "chat_id": str(chat_id),
                        "role": role,
                        "content": stripped,
                        "created_at": created_at,
                        "embedding": str(embedding),
                    },
                )
            ).first()
            if written is None:
                raise ValueError(
                    f"message_id {key!r} is already indexed for another "
                    "user; message ids must be unique across users.",
                )
        return True

    async def _evict_over_quota(
        self,
        session: AsyncSession,
        user_id: str,
        incoming_message_id: str,
    ) -> int:
        """Delete a user's oldest rows beyond ``max_entries_per_user``.

        The incoming message is left out of the count and never evicted, so
        re-indexing an existing message does not push out another row.

        Args:
            session (AsyncSession): The ``index`` transaction.
            user_id (str): The user whose rows to bound.
            incoming_message_id (str): The message about to be upserted.

        Returns:
            int: The number of rows deleted.
        """
        quota = self.max_entries_per_user
        if quota <= 0:
            return 0
        from sqlalchemy import text

        params: dict[str, Any] = {"user_id": user_id, "message_id": incoming_message_id}
        others: int = (
            await session.execute(
                text(
                    f"SELECT count(*) FROM {self.table} "
                    "WHERE user_id = :user_id AND message_id <> :message_id",
                ),
                params,
            )
        ).scalar_one()
        overflow = others + 1 - quota
        if overflow <= 0:
            return 0
        deleted = await session.execute(
            text(
                f"DELETE FROM {self.table} WHERE message_id IN ("
                f"SELECT message_id FROM {self.table} "
                "WHERE user_id = :user_id AND message_id <> :message_id "
                "ORDER BY created_at ASC, message_id ASC LIMIT :overflow)",
            ),
            {**params, "overflow": overflow},
        )
        count: int = getattr(deleted, "rowcount", 0)
        return count

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

        Embeds ``query`` and reads the ``top_k * candidate_multiplier``
        nearest rows ``WHERE user_id = :user_id`` (and
        ``chat_id <> :exclude_chat_id`` when given) ordered by ``<=>``, then
        drops hits below the similarity floor and re-ranks by the
        recency-decay blend — the same ranking as ``ChatMemory.search``.

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
        stripped = query.strip()
        if not stripped:
            return []
        vectors: list[list[float]] = await _embed_query(self._embedder, [stripped])
        if not vectors or not vectors[0]:
            return []
        await self._ensure_ready()
        from sqlalchemy import text

        params: dict[str, Any] = {
            "q": str(vectors[0]),
            "user_id": str(user_id),
            "k": resolved_top_k * self.candidate_multiplier,
        }
        where = "user_id = :user_id"
        if exclude_chat_id is not None:
            where += " AND chat_id <> :exclude_chat_id"
            params["exclude_chat_id"] = str(exclude_chat_id)
        async with self._db.get_session_context() as session:
            await _set_search_options(
                session,
                ef_search=self.ef_search,
                probes=self.probes,
            )
            if self._iterative_scan:
                for name, value in _ITERATIVE_SCAN_SETTINGS.items():
                    await session.execute(
                        text("SELECT set_config(:name, :value, true)"),
                        {"name": name, "value": value},
                    )
            rows = (
                await session.execute(
                    text(
                        "SELECT content, role, chat_id, created_at, "
                        "embedding <=> CAST(:q AS vector) AS distance "
                        f"FROM {self.table} WHERE {where} "
                        "ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k",
                    ),
                    params,
                )
            ).all()
        return _rank_hits(
            (
                _MemoryCandidate(
                    content=row.content,
                    role=row.role,
                    chat_id=row.chat_id,
                    created_at=row.created_at,
                    distance=float(row.distance),
                )
                for row in rows
            ),
            floor=floor,
            top_k=resolved_top_k,
            recency_weight=self.recency_weight,
            recency_halflife_days=self.recency_halflife_days,
        )

    async def delete_for_chat(
        self,
        *,
        user_id: str | UUID,
        chat_id: str | UUID,
        session: AsyncSession | None = None,
    ) -> None:
        """Remove one user's rows for a chat.

        ``chat_id`` is not unique across users, so the delete is keyed on
        the owner too: another user's rows that share the id stay.

        Args:
            user_id (str | UUID): The owner whose rows to delete.
            chat_id (str | UUID): The chat whose rows to delete.
            session (AsyncSession | None): The caller's session. When given,
                the ``DELETE`` runs there and commits (or rolls back) with
                the rest of the caller's transaction; when ``None``, it runs
                and commits in a session of its own.
        """
        await self._delete(
            "user_id = :user_id AND chat_id = :chat_id",
            {"user_id": str(user_id), "chat_id": str(chat_id)},
            session,
        )

    async def delete_for_user(
        self,
        *,
        user_id: str | UUID,
        session: AsyncSession | None = None,
    ) -> None:
        """Remove every row belonging to a user (account erasure).

        Args:
            user_id (str | UUID): The owner whose rows to delete.
            session (AsyncSession | None): The caller's session, so the
                erasure joins its transaction; ``None`` commits on its own.
        """
        await self._delete("user_id = :user_id", {"user_id": str(user_id)}, session)

    async def _delete(
        self,
        where: str,
        params: dict[str, Any],
        session: AsyncSession | None,
    ) -> None:
        """Run a ``DELETE`` in the caller's session or in a committed one.

        Args:
            where (str): The SQL condition (constant, built by the caller).
            params (dict[str, Any]): The bound values.
            session (AsyncSession | None): The caller's session, or ``None``.
        """
        await self._ensure_ready()
        from sqlalchemy import text

        statement = text(f"DELETE FROM {self.table} WHERE {where}")
        if session is not None:
            await session.execute(statement, params)
            return
        async with self._db.get_session_context() as own:
            await own.execute(statement, params)


__all__: list[str] = ["PgChatMemory"]
