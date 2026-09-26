"""``PgVectorStore`` against a real PostgreSQL with the pgvector extension.

Opt-in (``make test-docker``): starts ``pgvector/pgvector:pg16`` and runs the
store end to end — schema + ``source`` index creation, batched insert,
replace-by-source on re-index, in-batch deduplication and cosine search — so
the store that shipped entirely under ``pragma: no cover`` has one measured
path through a real database.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import text

from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager
from tempest_fastapi_sdk.genai.rag import Chunk, PgVectorStore

IMAGE: str = "pgvector/pgvector:pg16"
CONTAINER: str = "tempest-pgvector-store-probe"
PORT: int = 55437


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
            f"{PORT}:5432",
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
        yield f"postgresql+asyncpg://postgres:probe@127.0.0.1:{PORT}/probe"
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
