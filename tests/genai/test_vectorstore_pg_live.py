"""``PgVectorStore`` against a real PostgreSQL with the pgvector extension.

Opt-in (``make test-docker``): starts ``pgvector/pgvector:pg16`` and runs the
store end to end — schema + ``source`` index creation, batched insert,
replace-by-source on re-index, in-batch deduplication and cosine search — so
the store that shipped entirely under ``pragma: no cover`` has one measured
path through a real database — plus the approximate indexes: HNSW and
IVFFlat built by ``ensure_schema(ann_index=...)``, used by the planner for
the exact statement ``search`` sends, and tuned per search without leaking
the setting to the next session.

The container name and host port are per process, so two checkouts running
``make test-docker`` at once do not remove each other's database.
"""

from __future__ import annotations

import os
import random
import shutil
import socket
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event, text

from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager
from tempest_fastapi_sdk.genai.rag import Chunk, PgVectorStore

IMAGE: str = "pgvector/pgvector:pg16"
CONTAINER: str = f"tempest-pgvector-store-probe-{os.getpid()}"


def _free_port() -> int:
    """Return a host TCP port nothing is listening on right now.

    Returns:
        int: A port the kernel picked for an ephemeral bind.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


@pytest.fixture
def postgres_url() -> Iterator[str]:
    """Start a pgvector Postgres container and yield its URL.

    Yields:
        str: An async SQLAlchemy URL for the container.
    """
    pytest.importorskip("asyncpg")
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not reachable")
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    port = _free_port()
    started = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            CONTAINER,
            "-e",
            "POSTGRES_PASSWORD=probe",
            "-e",
            "POSTGRES_DB=probe",
            "-p",
            f"127.0.0.1:{port}:5432",
            IMAGE,
        ],
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start {IMAGE}: {started.stderr.strip()}")
    try:
        for _ in range(60):
            ready = subprocess.run(
                ["docker", "exec", CONTAINER, "pg_isready", "-U", "postgres"],
                capture_output=True,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.skip("postgres never became ready")
        time.sleep(1)
        yield f"postgresql+asyncpg://postgres:probe@127.0.0.1:{port}/probe"
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


@pytest_asyncio.fixture
async def db(postgres_url: str) -> AsyncIterator[AsyncDatabaseManager]:
    """Yield a connected database manager over the container.

    Args:
        postgres_url (str): URL from the container fixture.

    Yields:
        AsyncDatabaseManager: The manager, disconnected afterwards.
    """
    manager = AsyncDatabaseManager(postgres_url)
    await manager.connect()
    yield manager
    await manager.disconnect()


async def _rows(db: AsyncDatabaseManager, table: str) -> list[tuple[str, str]]:
    """Return every stored ``(source, text)`` pair, sorted.

    Args:
        db (AsyncDatabaseManager): The manager.
        table (str): The store's table.

    Returns:
        list[tuple[str, str]]: The rows.
    """
    async with db.get_session_context() as session:
        result = await session.execute(text(f"SELECT source, text FROM {table}"))
        return sorted((row.source, row.text) for row in result)


@pytest.mark.docker
async def test_round_trip_replace_dedupe_and_index(db: AsyncDatabaseManager) -> None:
    store = PgVectorStore(db, dim=2, table="public.rag_live")
    await store.add(
        [
            Chunk(text="alpha 0", source="kb", index=0, page=1),
            Chunk(text="alpha 1", source="kb", index=1),
            Chunk(text="alpha 2", source="kb", index=2),
            Chunk(text="keep", source="other", index=0),
            Chunk(text="keep", source="other", index=0),
        ],
        [[1.0, 0.0], [1.0, 0.1], [1.0, 0.2], [0.0, 1.0], [0.0, 1.0]],
    )
    assert await _rows(db, store.table) == [
        ("kb", "alpha 0"),
        ("kb", "alpha 1"),
        ("kb", "alpha 2"),
        ("other", "keep"),
    ]

    await store.add([Chunk(text="beta 0", source="kb", index=0)], [[1.0, 0.0]])
    assert await _rows(db, store.table) == [("kb", "beta 0"), ("other", "keep")]

    hits = await store.search([1.0, 0.0], top_k=2)
    assert [hit.text for hit in hits] == ["beta 0", "keep"]
    assert hits[0].score == pytest.approx(1.0)

    async with db.get_session_context() as session:
        indexes = await session.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'rag_live'"),
        )
        names = {row.indexname for row in indexes}
    assert "rag_live_source_idx" in names


def _random_vectors(count: int, dim: int, seed: int) -> list[list[float]]:
    """Return ``count`` seeded Gaussian vectors of ``dim`` components.

    Args:
        count (int): How many vectors.
        dim (int): Components per vector.
        seed (int): The RNG seed, so a failure reproduces.

    Returns:
        list[list[float]]: The vectors.
    """
    rng = random.Random(seed)
    return [[rng.gauss(0.0, 1.0) for _ in range(dim)] for _ in range(count)]


async def _seed(db: AsyncDatabaseManager, store: PgVectorStore, count: int) -> None:
    """Fill ``store`` with ``count`` random chunks and refresh statistics.

    Args:
        db (AsyncDatabaseManager): The manager behind ``store``.
        store (PgVectorStore): The store to fill.
        count (int): How many chunks.
    """
    vectors = _random_vectors(count, store.dim, seed=7)
    chunks = [Chunk(text=f"c{i}", source="corpus", index=i) for i in range(count)]
    await store.add(chunks, vectors)
    async with db.get_session_context() as session:
        await session.execute(text(f"ANALYZE {store.table}"))


async def _search_plan(
    db: AsyncDatabaseManager,
    store: PgVectorStore,
    *,
    ef_search: int | None = None,
    probes: int | None = None,
) -> str:
    """Run ``store.search`` and return ``EXPLAIN`` of the statement it sent.

    The statements and parameters are captured off the engine, so the plan
    is the one Postgres builds for exactly what ``search`` executes — its
    ``set_config`` calls replayed first — not for a hand-copied query that
    could drift from it.

    Args:
        db (AsyncDatabaseManager): The manager behind ``store``.
        store (PgVectorStore): The store under test.
        ef_search (int | None): Forwarded to ``store.search``.
        probes (int | None): Forwarded to ``store.search``.

    Returns:
        str: The plan text, one line per node.
    """
    captured: list[tuple[str, Any]] = []

    def _capture(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        """Record every statement the engine sends.

        Args:
            conn (Any): The connection.
            cursor (Any): The DBAPI cursor.
            statement (str): The SQL.
            parameters (Any): Its parameters.
            context (Any): The execution context.
            executemany (bool): Whether it is batched.
        """
        captured.append((statement, parameters))

    engine = db.engine.sync_engine
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        await store.search(
            [1.0] * store.dim,
            top_k=5,
            ef_search=ef_search,
            probes=probes,
        )
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
    async with db.engine.begin() as conn:
        for statement, parameters in captured:
            if "set_config" in statement:
                await conn.exec_driver_sql(statement, parameters)
        search_sql, search_params = next(
            (sql, params) for sql, params in captured if "ORDER BY embedding" in sql
        )
        plan = await conn.exec_driver_sql(f"EXPLAIN {search_sql}", search_params)
        return "\n".join(row[0] for row in plan)


async def _indexdef(db: AsyncDatabaseManager, name: str) -> str:
    """Return the ``CREATE INDEX`` statement Postgres reports for ``name``.

    Args:
        db (AsyncDatabaseManager): The manager.
        name (str): The bare index name.

    Returns:
        str: The ``pg_indexes.indexdef`` text.
    """
    async with db.get_session_context() as session:
        result = await session.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"),
            {"n": name},
        )
        indexdef: str = result.scalar_one()
    return indexdef


@pytest.mark.docker
async def test_hnsw_index_is_built_and_used_by_search(
    db: AsyncDatabaseManager,
) -> None:
    store = PgVectorStore(db, dim=8, table="public.rag_hnsw")
    await store.ensure_schema(ann_index="hnsw", m=8, ef_construction=32)
    await _seed(db, store, 2000)

    indexdef = await _indexdef(db, "rag_hnsw_embedding_idx")
    assert "USING hnsw (embedding vector_cosine_ops)" in indexdef
    assert "m='8'" in indexdef
    assert "ef_construction='32'" in indexdef
    assert "Index Scan using rag_hnsw_embedding_idx" in await _search_plan(db, store)

    exact = PgVectorStore(db, dim=8, table="public.rag_exact")
    await _seed(db, exact, 2000)
    assert "Seq Scan on rag_exact" in await _search_plan(db, exact)


@pytest.mark.docker
async def test_ivfflat_index_is_built_after_loading_and_used(
    db: AsyncDatabaseManager,
) -> None:
    store = PgVectorStore(db, dim=8, table="rag_ivf")
    await _seed(db, store, 2000)
    await store.ensure_schema(ann_index="ivfflat", lists=20)

    indexdef = await _indexdef(db, "rag_ivf_embedding_idx")
    assert "USING ivfflat (embedding vector_cosine_ops)" in indexdef
    assert "lists='20'" in indexdef
    plan = await _search_plan(db, store, probes=4)
    assert "Index Scan using rag_ivf_embedding_idx" in plan
    assert len(await store.search([1.0] * 8, top_k=5, probes=20)) == 5


@pytest.mark.docker
async def test_existing_index_is_kept_or_refused_never_rebuilt(
    db: AsyncDatabaseManager,
) -> None:
    store = PgVectorStore(db, dim=4, table="rag_same")
    await store.ensure_schema(ann_index="hnsw", m=8)
    await store.ensure_schema(ann_index="hnsw", m=8)
    with pytest.raises(ValueError, match="already exists as hnsw"):
        await store.ensure_schema(ann_index="hnsw", m=16)
    with pytest.raises(ValueError, match="DROP INDEX rag_same_embedding_idx"):
        await store.ensure_schema(ann_index="ivfflat")

    defaults = PgVectorStore(db, dim=4, table="rag_defaults")
    await defaults.ensure_schema(ann_index="hnsw")
    await defaults.ensure_schema(ann_index="hnsw")
    with pytest.raises(ValueError, match="already exists"):
        await defaults.ensure_schema(ann_index="hnsw", m=16)


@pytest.mark.docker
async def test_search_settings_are_local_to_the_search(
    db: AsyncDatabaseManager,
) -> None:
    store = PgVectorStore(db, dim=8, table="rag_local")
    await store.ensure_schema(ann_index="hnsw")
    await _seed(db, store, 2000)

    assert len(await store.search([1.0] * 8, top_k=20, ef_search=5)) == 5
    assert len(await store.search([1.0] * 8, top_k=20, ef_search=64)) == 20

    async with db.get_session_context() as session:
        ef_search = (await session.execute(text("SHOW hnsw.ef_search"))).scalar_one()
        probes = (await session.execute(text("SHOW ivfflat.probes"))).scalar_one()
    assert (ef_search, probes) == ("40", "1")
