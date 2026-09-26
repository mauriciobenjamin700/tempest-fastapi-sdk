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
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from tempest_fastapi_sdk.genai.embeddings import cosine_similarity
from tempest_fastapi_sdk.genai.rag.schemas import Chunk, _unique_batch

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager

AnnIndex = Literal["hnsw", "ivfflat"]
"""The approximate index methods :meth:`PgVectorStore.ensure_schema` builds."""

_HNSW_MIN_PGVECTOR: tuple[int, int, int] = (0, 5, 0)
"""The first pgvector release that ships the ``hnsw`` access method.

Taken from pgvector's CHANGELOG, entry ``0.5.0``: "Added HNSW index type".
"""

_ANN_OPTIONS: dict[str, frozenset[str]] = {
    "hnsw": frozenset({"m", "ef_construction"}),
    "ivfflat": frozenset({"lists"}),
}
"""The build parameters each index method accepts, by pgvector's ``WITH`` name."""

_PG_IDENTIFIER: str = r"[A-Za-z_][A-Za-z0-9_]{0,62}"
_PG_TABLE_RE: re.Pattern[str] = re.compile(
    rf"^{_PG_IDENTIFIER}(\.{_PG_IDENTIFIER})?$",
)
"""A bare or ``schema.table`` Postgres identifier, unquoted, at most 63 bytes.

The table name is interpolated into DDL and DML (identifiers cannot be bound
parameters), so anything outside this shape is refused at construction.
"""


def _positive_int(name: str, value: int | None) -> None:
    """Refuse anything but ``None`` or a positive ``int`` for ``name``.

    The value is interpolated into DDL (index build parameters) or sent to
    ``set_config``, so ``bool`` — an ``int`` subclass — is refused too.

    Args:
        name (str): The parameter name, for the error message.
        value (int | None): The value to check.

    Raises:
        ValueError: When ``value`` is set and not a positive integer.
    """
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")


def _parse_version(raw: str) -> tuple[int, ...]:
    """Parse the numeric prefix of a ``pg_extension.extversion`` string.

    Args:
        raw (str): The version as Postgres reports it (``"0.8.0"``).

    Returns:
        tuple[int, ...]: The dotted numbers, stopping at the first
        component that is not all digits (``"0.5.0-dev"`` -> ``(0, 5)``).
    """
    parts: list[int] = []
    for piece in raw.split("."):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return tuple(parts)


def _ann_options(
    ann_index: AnnIndex | None,
    *,
    m: int | None,
    ef_construction: int | None,
    lists: int | None,
) -> dict[str, int]:
    """Validate the build parameters of an approximate index.

    Args:
        ann_index (AnnIndex | None): ``"hnsw"``, ``"ivfflat"`` or ``None``.
        m (int | None): HNSW max connections per layer.
        ef_construction (int | None): HNSW candidate list size at build.
        lists (int | None): IVFFlat inverted list count.

    Returns:
        dict[str, int]: The parameters that were set, keyed by their
        pgvector ``WITH`` name. Empty means every pgvector default (or no
        index, when ``ann_index`` is ``None``).

    Raises:
        ValueError: When ``ann_index`` is not a known method, a parameter
            is not a positive integer, or a parameter belongs to the other
            method (``lists`` with ``"hnsw"``, ``m`` with ``"ivfflat"``, any
            of them without ``ann_index``).
    """
    if ann_index is not None and ann_index not in _ANN_OPTIONS:
        raise ValueError(
            f"ann_index must be 'hnsw', 'ivfflat' or None, got {ann_index!r}",
        )
    given: dict[str, int | None] = {
        "m": m,
        "ef_construction": ef_construction,
        "lists": lists,
    }
    options: dict[str, int] = {}
    for name, value in given.items():
        _positive_int(name, value)
        if value is None:
            continue
        if ann_index is None:
            raise ValueError(f"{name} needs ann_index='hnsw' or 'ivfflat'")
        if name not in _ANN_OPTIONS[ann_index]:
            raise ValueError(f"{name} does not apply to a {ann_index} index")
        options[name] = value
    return options


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
            each with its ``score`` populated. Empty when ``top_k <= 0`` -
            the same contract as ``ChromaVectorStore.search``. A negative
            ``top_k`` used to reach the slice as ``scored[:-3]`` and return
            every chunk but the last three.
        """
        if top_k <= 0:
            return []
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
    B-tree index on ``source`` are created on demand; search orders by
    pgvector's cosine-distance operator (``<=>``). Without an approximate
    index that is an exact sequential scan; ``ensure_schema(ann_index=...)``
    builds an HNSW or IVFFlat index (``vector_cosine_ops``) that trades
    recall for latency, tuned per search with ``ef_search`` / ``probes``.
    Requires the ``[genai-rag]`` extra (``pgvector`` package) plus a
    Postgres with ``CREATE EXTENSION vector``.

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

    async def ensure_schema(
        self,
        *,
        ann_index: AnnIndex | None = None,
        m: int | None = None,
        ef_construction: int | None = None,
        lists: int | None = None,
    ) -> None:
        """Create the pgvector extension, the chunk table and its indexes.

        Always creates the B-tree index on ``source``. With ``ann_index`` it
        also builds an approximate index on ``embedding``, named
        :attr:`ann_index_name`, with the ``vector_cosine_ops`` operator class
        — the one that matches the ``<=>`` distance :meth:`search` orders
        by. A parameter left as ``None`` is omitted from the ``WITH`` clause,
        so pgvector's own default applies.

        An IVFFlat index computes its list centroids from the rows present
        when it is built, so call this with ``ann_index="ivfflat"`` after
        loading the corpus, not before the first ``add``.

        Args:
            ann_index (AnnIndex | None): ``"hnsw"``, ``"ivfflat"`` or
                ``None`` (no approximate index; search stays exact).
            m (int | None): HNSW max connections per layer.
            ef_construction (int | None): HNSW candidate list size while
                building.
            lists (int | None): IVFFlat inverted list count.

        Raises:
            ValueError: When a parameter is not a positive integer, belongs
                to the other method or is given without ``ann_index``; or
                when :attr:`ann_index_name` already exists with another
                method or other parameters — it is never rebuilt silently,
                drop it to change it.
            RuntimeError: When ``ann_index="hnsw"`` and the installed
                pgvector is older than 0.5.0.
        """
        options = _ann_options(
            ann_index,
            m=m,
            ef_construction=ef_construction,
            lists=lists,
        )
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
            if ann_index is not None:
                await self._ensure_ann_index(session, ann_index, options)
        self._ready = True

    @property
    def ann_index_name(self) -> str:
        """The approximate index's name, schema-qualified like ``table``.

        Returns:
            str: ``<table>_embedding_idx``, the bare part truncated so the
            name fits Postgres' 63-byte identifier limit.
        """
        suffix = "_embedding_idx"
        schema, _, bare = self.table.rpartition(".")
        name = f"{bare[: 63 - len(suffix)]}{suffix}"
        return f"{schema}.{name}" if schema else name

    async def _ensure_ann_index(
        self,
        session: AsyncSession,
        ann_index: AnnIndex,
        options: dict[str, int],
    ) -> None:
        """Build the approximate index, or confirm the existing one matches.

        Args:
            session (AsyncSession): The open schema session.
            ann_index (AnnIndex): The requested method.
            options (dict[str, int]): The validated ``WITH`` parameters.

        Raises:
            RuntimeError: When HNSW is requested on pgvector < 0.5.0.
            ValueError: When an index with the same name exists with another
                method or other parameters.
        """
        from sqlalchemy import text

        raw_version = (
            await session.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"),
            )
        ).scalar_one()
        if ann_index == "hnsw" and _parse_version(raw_version) < _HNSW_MIN_PGVECTOR:
            raise RuntimeError(
                f"ann_index='hnsw' needs pgvector >= 0.5.0, but the database "
                f"has {raw_version}; run ALTER EXTENSION vector UPDATE or use "
                f"ann_index='ivfflat'",
            )
        existing = (
            await session.execute(
                text(
                    "SELECT am.amname, c.reloptions FROM pg_class c "
                    "JOIN pg_am am ON am.oid = c.relam "
                    "WHERE c.oid = to_regclass(:name)",
                ),
                {"name": self.ann_index_name},
            )
        ).first()
        wanted = {key: str(value) for key, value in options.items()}
        if existing is not None:
            found = dict(option.split("=", 1) for option in (existing.reloptions or []))
            if existing.amname != ann_index or found != wanted:
                raise ValueError(
                    f"index {self.ann_index_name} already exists as "
                    f"{existing.amname} {found}, not {ann_index} {wanted}; "
                    f"DROP INDEX {self.ann_index_name} to rebuild it",
                )
            return
        with_clause = (
            " WITH (" + ", ".join(f"{k} = {v}" for k, v in options.items()) + ")"
            if options
            else ""
        )
        bare_name = self.ann_index_name.rpartition(".")[2]
        await session.execute(
            text(
                f"CREATE INDEX {bare_name} ON {self.table} "
                f"USING {ann_index} (embedding vector_cosine_ops){with_clause}",
            ),
        )

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
        ef_search: int | None = None,
        probes: int | None = None,
    ) -> list[Chunk]:
        """Return the ``top_k`` nearest chunks by cosine distance.

        ``ef_search`` and ``probes`` are set with
        ``set_config(..., is_local => true)`` — the function form of
        ``SET LOCAL`` — so they last for this search's transaction only and
        never leak to another session sharing the pooled connection.

        Args:
            vector (list[float]): The query vector.
            top_k (int): How many chunks to return.
            ef_search (int | None): ``hnsw.ef_search`` for this search: the
                HNSW candidate list size. Higher raises recall and latency;
                an HNSW scan returns at most this many rows, so keep it
                ``>= top_k``. ``None`` keeps the server setting.
            probes (int | None): ``ivfflat.probes`` for this search: how
                many IVFFlat lists are scanned. ``None`` keeps the server
                setting.

        Returns:
            list[Chunk]: Nearest chunks, each with ``score`` = cosine
            similarity (``1 - distance``). Empty when ``top_k <= 0``,
            without a query, since Postgres rejects a negative ``LIMIT``.

        Raises:
            ValueError: When ``ef_search`` or ``probes`` is set and not a
                positive integer.
        """
        _positive_int("ef_search", ef_search)
        _positive_int("probes", probes)
        if top_k <= 0:
            return []
        if not self._ready:
            await self.ensure_schema()
        from sqlalchemy import text

        settings = {"hnsw.ef_search": ef_search, "ivfflat.probes": probes}
        async with self._db.get_session_context() as session:
            for name, value in settings.items():
                if value is not None:
                    await session.execute(
                        text("SELECT set_config(:name, :value, true)"),
                        {"name": name, "value": str(value)},
                    )
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
    "AnnIndex",
    "InMemoryVectorStore",
    "PgVectorStore",
    "VectorStore",
]
