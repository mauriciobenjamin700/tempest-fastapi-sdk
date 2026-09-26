"""Vector stores — persist chunk embeddings and retrieve by similarity.

The missing half of RAG over your own knowledge: index chunks once, then
answer questions cheaply by nearest-neighbor search instead of re-embedding
everything each request. `VectorStore` is a Protocol so the store is
swappable; the SDK ships an in-memory one (dev/tests) and a Postgres
`PgVectorStore` (pgvector) that reuses the database the service already
has.

Every SDK store shares one indexing contract: ``add`` **replaces** every chunk
previously stored for the sources named in the batch (so re-indexing an edited
document never leaves its old version, or a stale tail of it, behind), and
chunks are identified by ``(source, index, text)`` — never by
``(source, index)`` alone, which :func:`chunk_text` repeats on every call.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from tempest_fastapi_sdk.genai.embeddings import cosine_similarity
from tempest_fastapi_sdk.genai.rag.schemas import Chunk, _unique_batch

if TYPE_CHECKING:
    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager

_PG_IDENTIFIER: str = r"[A-Za-z_][A-Za-z0-9_]{0,62}"
_PG_TABLE_RE: re.Pattern[str] = re.compile(
    rf"^{_PG_IDENTIFIER}(\.{_PG_IDENTIFIER})?$",
)
"""A bare or ``schema.table`` Postgres identifier, unquoted, at most 63 bytes.

The table name is interpolated into DDL and DML (identifiers cannot be bound
parameters), so anything outside this shape is refused at construction.
"""


@runtime_checkable
class VectorStore(Protocol):
    """Persist chunk vectors and search them by similarity."""

    async def add(
        self,
        chunks: Sequence[Chunk],
        vectors: Sequence[list[float]],
    ) -> None:
        """Store ``chunks`` with their aligned ``vectors``.

        The SDK's stores replace every chunk previously stored for the
        sources in ``chunks``; a custom store should do the same, or
        re-indexing a source leaves its old chunks searchable.

        Args:
            chunks (Sequence[Chunk]): The chunks to index.
            vectors (Sequence[list[float]]): One embedding per chunk, in the
                same order.
        """
        ...

    async def search(self, vector: list[float], *, top_k: int = 5) -> list[Chunk]:
        """Return the ``top_k`` chunks most similar to ``vector``."""
        ...


class InMemoryVectorStore:
    """A dict-backed vector store — dev, tests, small corpora.

    Cosine similarity over every stored vector (linear scan). Fine up to a
    few thousand chunks; use :class:`PgVectorStore` (or Qdrant, etc.)
    beyond that.
    """

    def __init__(self) -> None:
        """Initialize an empty store."""
        self._chunks: list[Chunk] = []
        self._vectors: list[list[float]] = []

    async def add(
        self,
        chunks: Sequence[Chunk],
        vectors: Sequence[list[float]],
    ) -> None:
        """Store ``chunks``, replacing what their sources held before.

        Every chunk already stored for a source named in ``chunks`` is
        dropped first; identical chunks in the batch are kept once.

        Args:
            chunks (Sequence[Chunk]): The chunks to store — every chunk of a
                source in one call.
            vectors (Sequence[list[float]]): One vector per chunk, aligned.

        Raises:
            ValueError: When the counts differ.
        """
        pairs = _unique_batch(chunks, vectors)
        sources = {chunk.source for chunk in chunks}
        kept = [
            (chunk, vector)
            for chunk, vector in zip(self._chunks, self._vectors, strict=True)
            if chunk.source not in sources
        ]
        kept.extend(pairs)
        self._chunks = [chunk for chunk, _ in kept]
        self._vectors = [vector for _, vector in kept]

    async def search(self, vector: list[float], *, top_k: int = 5) -> list[Chunk]:
        """Return the ``top_k`` most similar chunks (with ``score`` set).

        Args:
            vector (list[float]): The query vector.
            top_k (int): How many chunks to return.

        Returns:
            list[Chunk]: Chunks ordered by descending cosine similarity,
            each with its ``score`` populated.
        """
        scored = [
            chunk.model_copy(update={"score": cosine_similarity(vector, stored)})
            for chunk, stored in zip(self._chunks, self._vectors, strict=True)
        ]
        scored.sort(key=lambda c: c.score or 0.0, reverse=True)
        return scored[:top_k]

    def __len__(self) -> int:
        """Return how many chunks are stored."""
        return len(self._chunks)


class PgVectorStore:
    """A Postgres-backed vector store using the ``pgvector`` extension.

    Reuses the service's existing database (no new infra). The table and a
    B-tree index on ``source`` are created on demand; search uses
    pgvector's cosine-distance operator (``<=>``) as an exact scan — no
    approximate (HNSW/IVFFlat) index is created, so add one yourself once
    the corpus outgrows a sequential scan. Requires the ``[genai-rag]``
    extra (``pgvector`` package) plus a Postgres with
    ``CREATE EXTENSION vector``.

    Attributes:
        table (str): The table holding chunks + embeddings.
        dim (int): The embedding dimension (must match the model).
    """

    def __init__(
        self,
        db: AsyncDatabaseManager,
        *,
        dim: int,
        table: str = "rag_chunks",
    ) -> None:
        """Initialize the store.

        Args:
            db (AsyncDatabaseManager): The database manager (own sessions).
            dim (int): Embedding dimension (e.g. 384 for MiniLM).
            table (str): Table name, bare or ``schema.table``, unquoted.
                Defaults to ``"rag_chunks"``.

        Raises:
            ValueError: When ``table`` is not a plain Postgres identifier
                (it is interpolated into SQL, so it is validated here) or
                ``dim`` is not a positive integer.
        """
        if not _PG_TABLE_RE.fullmatch(table):
            raise ValueError(
                f"table must be a bare or schema-qualified identifier "
                f"([A-Za-z_][A-Za-z0-9_]*, at most 63 chars each), got {table!r}",
            )
        if isinstance(dim, bool) or not isinstance(dim, int) or dim <= 0:
            raise ValueError(f"dim must be a positive integer, got {dim!r}")
        self._db = db
        self.dim = dim
        self.table = table
        self._ready = False

    async def ensure_schema(self) -> None:
        """Create the pgvector extension, the chunk table and its index."""
        from sqlalchemy import text

        async with self._db.get_session_context() as session:
            await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await session.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {self.table} ("
                    "id BIGSERIAL PRIMARY KEY, "
                    "text TEXT NOT NULL, "
                    "source TEXT NOT NULL, "
                    "chunk_index INTEGER NOT NULL, "
                    "page INTEGER, "
                    f"embedding vector({self.dim}) NOT NULL)",
                ),
            )
            index_name = f"{self.table.rsplit('.', 1)[-1]}_source_idx"[:63]
            await session.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS {index_name} ON {self.table} (source)",
                ),
            )
        self._ready = True

    async def add(
        self,
        chunks: Sequence[Chunk],
        vectors: Sequence[list[float]],
    ) -> None:
        """Store ``chunks``, replacing what their sources held before.

        One transaction: the rows of every source named in ``chunks`` are
        deleted, then the batch is inserted in a single ``executemany``.
        Identical chunks in the batch are inserted once.

        Args:
            chunks (Sequence[Chunk]): The chunks to store — every chunk of a
                source in one call.
            vectors (Sequence[list[float]]): One vector per chunk.

        Raises:
            ValueError: When the counts differ.
        """
        pairs = _unique_batch(chunks, vectors)
        if not pairs:
            return
        if not self._ready:
            await self.ensure_schema()
        from sqlalchemy import bindparam, text

        sources = sorted({chunk.source for chunk, _ in pairs})
        async with self._db.get_session_context() as session:
            await session.execute(
                text(f"DELETE FROM {self.table} WHERE source IN :sources").bindparams(
                    bindparam("sources", expanding=True),
                ),
                {"sources": sources},
            )
            await session.execute(
                text(
                    f"INSERT INTO {self.table} "
                    "(text, source, chunk_index, page, embedding) "
                    "VALUES (:text, :source, :idx, :page, :embedding)",
                ),
                [
                    {
                        "text": chunk.text,
                        "source": chunk.source,
                        "idx": chunk.index,
                        "page": chunk.page,
                        "embedding": str(vector),
                    }
                    for chunk, vector in pairs
                ],
            )

    async def search(  # pragma: no cover - needs Postgres+pgvector
        self,
        vector: list[float],
        *,
        top_k: int = 5,
    ) -> list[Chunk]:
        """Return the ``top_k`` nearest chunks by cosine distance.

        Args:
            vector (list[float]): The query vector.
            top_k (int): How many chunks to return.

        Returns:
            list[Chunk]: Nearest chunks, each with ``score`` = cosine
            similarity (``1 - distance``).
        """
        if not self._ready:
            await self.ensure_schema()
        from sqlalchemy import text

        async with self._db.get_session_context() as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT text, source, chunk_index, page, "
                        f"1 - (embedding <=> :q) AS score FROM {self.table} "
                        "ORDER BY embedding <=> :q LIMIT :k",
                    ),
                    {"q": str(vector), "k": top_k},
                )
            ).all()
        return [
            Chunk(
                text=row.text,
                source=row.source,
                index=row.chunk_index,
                page=row.page,
                score=float(row.score),
            )
            for row in rows
        ]


__all__: list[str] = [
    "InMemoryVectorStore",
    "PgVectorStore",
    "VectorStore",
]
