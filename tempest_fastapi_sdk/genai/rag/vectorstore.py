"""Vector stores — persist chunk embeddings and retrieve by similarity.

The missing half of RAG over your own knowledge: index chunks once, then
answer questions cheaply by nearest-neighbor search instead of re-embedding
everything each request. `VectorStore` is a Protocol so the store is
swappable; the SDK ships an in-memory one (dev/tests) and a Postgres
`PgVectorStore` (pgvector) that reuses the database the service already
has.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from tempest_fastapi_sdk.genai.embeddings import cosine_similarity
from tempest_fastapi_sdk.genai.rag.schemas import Chunk

if TYPE_CHECKING:
    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager


@runtime_checkable
class VectorStore(Protocol):
    """Persist chunk vectors and search them by similarity."""

    async def add(
        self,
        chunks: Sequence[Chunk],
        vectors: Sequence[list[float]],
    ) -> None:
        """Store ``chunks`` with their aligned ``vectors``."""
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
        """Append ``chunks`` with their ``vectors``.

        Args:
            chunks (Sequence[Chunk]): The chunks to store.
            vectors (Sequence[list[float]]): One vector per chunk, aligned.

        Raises:
            ValueError: When the counts differ.
        """
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        self._chunks.extend(chunks)
        self._vectors.extend(list(v) for v in vectors)

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

    Reuses the service's existing database (no new infra). The table is
    created on demand; search uses pgvector's cosine-distance operator
    (``<=>``). Requires the ``[genai-rag]`` extra (``pgvector`` package)
    plus a Postgres with ``CREATE EXTENSION vector``.

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
            table (str): Table name. Defaults to ``"rag_chunks"``.
        """
        self._db = db
        self.dim = dim
        self.table = table
        self._ready = False

    async def ensure_schema(self) -> None:  # pragma: no cover - needs Postgres+pgvector
        """Create the pgvector extension and the chunk table if missing."""
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
        self._ready = True

    async def add(  # pragma: no cover - needs Postgres+pgvector
        self,
        chunks: Sequence[Chunk],
        vectors: Sequence[list[float]],
    ) -> None:
        """Insert ``chunks`` with their ``vectors`` into the table.

        Args:
            chunks (Sequence[Chunk]): The chunks to store.
            vectors (Sequence[list[float]]): One vector per chunk.

        Raises:
            ValueError: When the counts differ.
        """
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        if not self._ready:
            await self.ensure_schema()
        from sqlalchemy import text

        async with self._db.get_session_context() as session:
            for chunk, vector in zip(chunks, vectors, strict=True):
                await session.execute(
                    text(
                        f"INSERT INTO {self.table} "
                        "(text, source, chunk_index, page, embedding) "
                        "VALUES (:text, :source, :idx, :page, :embedding)",
                    ),
                    {
                        "text": chunk.text,
                        "source": chunk.source,
                        "idx": chunk.index,
                        "page": chunk.page,
                        "embedding": str(vector),
                    },
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
