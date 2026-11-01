"""``PgChatMemory`` against a real PostgreSQL with the pgvector extension.

Opt-in (``make test-docker``): reuses the per-process ``pgvector/pgvector``
container fixture of ``test_vectorstore_pg_live`` and runs the memory end to
end — per-user isolation, exclusion of the active chat, the recency re-rank
over the over-fetched candidates, the quota's oldest-instant eviction, the
upsert keyed by ``message_id`` (and its refusal across users), both deletes
inside and outside the caller's transaction, and the HNSW index.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager
from tempest_fastapi_sdk.genai.rag import PgChatMemory
from tests.genai.test_chat_memory import _FakeEmbedder
from tests.genai.test_vectorstore_pg_live import db, postgres_url

__all__: list[str] = ["db", "postgres_url"]

NOW: datetime = datetime.now(UTC)


def _memory(
    db: AsyncDatabaseManager,
    embedder: _FakeEmbedder,
    *,
    table: str,
    **options: float | int,
) -> PgChatMemory:
    """Build a 2-dimensional ``PgChatMemory`` over ``table``.

    Args:
        db (AsyncDatabaseManager): The container's manager.
        embedder (_FakeEmbedder): The canned-vector embedder.
        table (str): The table name for this test.
        **options (float | int): Forwarded constructor keywords.

    Returns:
        PgChatMemory: The memory under test.
    """
    return PgChatMemory(embedder, db, dim=2, table=table, **options)  # type: ignore[arg-type]


async def _rows(db: AsyncDatabaseManager, table: str) -> list[tuple[str, str, str]]:
    """Return every stored ``(user_id, chat_id, message_id)``, sorted.

    Args:
        db (AsyncDatabaseManager): The manager.
        table (str): The memory table.

    Returns:
        list[tuple[str, str, str]]: The rows.
    """
    async with db.get_session_context() as session:
        result = await session.execute(
            text(f"SELECT user_id, chat_id, message_id FROM {table}"),
        )
        return sorted((row.user_id, row.chat_id, row.message_id) for row in result)


@pytest.mark.docker
async def test_search_never_returns_another_users_rows(
    db: AsyncDatabaseManager,
) -> None:
    embedder = _FakeEmbedder(
        {
            "alice likes postgres": [1.0, 0.0],
            "bob likes postgres too": [1.0, 0.0],
            "bob secret about postgres": [1.0, 0.001],
            "postgres": [1.0, 0.0],
        }
    )
    memory = _memory(db, embedder, table="mem_isolation")
    await memory.index(
        user_id="alice",
        chat_id="c1",
        message_id="a1",
        role="user",
        content="alice likes postgres",
        created_at=NOW,
    )
    for message_id, content in (
        ("b1", "bob likes postgres too"),
        ("b2", "bob secret about postgres"),
    ):
        await memory.index(
            user_id="bob",
            chat_id="c1",
            message_id=message_id,
            role="user",
            content=content,
            created_at=NOW,
        )

    hits = await memory.search(user_id="alice", query="postgres", top_k=10)

    assert [hit.content for hit in hits] == ["alice likes postgres"]
    assert await memory.search(user_id="carol", query="postgres") == []


@pytest.mark.docker
async def test_exclude_chat_id_drops_the_active_conversation(
    db: AsyncDatabaseManager,
) -> None:
    embedder = _FakeEmbedder(
        {
            "said in the active chat": [1.0, 0.0],
            "said in an older chat": [1.0, 0.05],
            "query": [1.0, 0.0],
        }
    )
    memory = _memory(db, embedder, table="mem_exclude")
    await memory.index(
        user_id="u",
        chat_id="active",
        message_id="m1",
        role="user",
        content="said in the active chat",
        created_at=NOW,
    )
    await memory.index(
        user_id="u",
        chat_id="old",
        message_id="m2",
        role="assistant",
        content="said in an older chat",
        created_at=NOW,
    )

    hits = await memory.search(user_id="u", query="query", exclude_chat_id="active")

    assert [(hit.chat_id, hit.role) for hit in hits] == [("old", "assistant")]
    assert len(await memory.search(user_id="u", query="query")) == 2


@pytest.mark.docker
async def test_recent_message_outranks_older_stronger_matches(
    db: AsyncDatabaseManager,
) -> None:
    embedder = _FakeEmbedder(
        {
            "old strong match one": [1.0, 0.0],
            "old strong match two": [1.0, 0.01],
            "old strong match three": [1.0, 0.02],
            "new slightly weaker match": [0.9, 0.4358898943540674],
            "query": [1.0, 0.0],
        }
    )
    memory = _memory(db, embedder, table="mem_recency", top_k=1)
    for idx, content in enumerate(
        ("old strong match one", "old strong match two", "old strong match three"),
    ):
        await memory.index(
            user_id="u",
            chat_id="c",
            message_id=f"old{idx}",
            role="user",
            content=content,
            created_at=NOW - timedelta(days=60),
        )
    await memory.index(
        user_id="u",
        chat_id="c",
        message_id="new",
        role="user",
        content="new slightly weaker match",
        created_at=NOW,
    )

    hits = await memory.search(user_id="u", query="query")

    assert [hit.content for hit in hits] == ["new slightly weaker match"]
    assert hits[0].similarity == pytest.approx(0.9, abs=1e-6)
    assert hits[0].score == pytest.approx(0.9, abs=1e-3)

    no_overfetch = _memory(
        db,
        embedder,
        table="mem_recency",
        top_k=1,
        candidate_multiplier=1,
    )
    raw_top = await no_overfetch.search(user_id="u", query="query")
    assert [hit.content for hit in raw_top] == ["old strong match one"]

    similarity_only = _memory(
        db,
        embedder,
        table="mem_recency",
        top_k=1,
        recency_weight=0.0,
    )
    flat = await similarity_only.search(user_id="u", query="query")
    assert [hit.content for hit in flat] == ["old strong match one"]
    assert flat[0].score == flat[0].similarity


@pytest.mark.docker
async def test_similarity_floor_uses_raw_similarity(
    db: AsyncDatabaseManager,
) -> None:
    embedder = _FakeEmbedder({"orthogonal message": [0.0, 1.0], "query": [1.0, 0.0]})
    memory = _memory(db, embedder, table="mem_floor")
    await memory.index(
        user_id="u",
        chat_id="c",
        message_id="m",
        role="user",
        content="orthogonal message",
        created_at=NOW,
    )

    assert await memory.search(user_id="u", query="query") == []
    assert len(await memory.search(user_id="u", query="query", min_similarity=0.0)) == 1


@pytest.mark.docker
async def test_quota_evicts_the_oldest_instant_of_that_user_only(
    db: AsyncDatabaseManager,
) -> None:
    embedder = _FakeEmbedder({})
    memory = _memory(db, embedder, table="mem_quota", max_entries_per_user=2)
    plus_five = timezone(timedelta(hours=5))
    await memory.index(
        user_id="u",
        chat_id="c",
        message_id="utc-0800",
        role="user",
        content="eight o'clock UTC",
        created_at=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
    )
    await memory.index(
        user_id="u",
        chat_id="c",
        message_id="plus5-1000",
        role="user",
        content="ten o'clock at +05:00",
        created_at=datetime(2026, 1, 1, 10, 0, tzinfo=plus_five),
    )
    await memory.index(
        user_id="other",
        chat_id="c",
        message_id="other-old",
        role="user",
        content="another user's oldest row",
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    await memory.index(
        user_id="u",
        chat_id="c",
        message_id="utc-0800",
        role="user",
        content="re-indexed, same id",
        created_at=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
    )
    assert await _rows(db, "mem_quota") == [
        ("other", "c", "other-old"),
        ("u", "c", "plus5-1000"),
        ("u", "c", "utc-0800"),
    ]

    await memory.index(
        user_id="u",
        chat_id="c",
        message_id="utc-0900",
        role="user",
        content="nine o'clock UTC",
        created_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
    )

    assert await _rows(db, "mem_quota") == [
        ("other", "c", "other-old"),
        ("u", "c", "utc-0800"),
        ("u", "c", "utc-0900"),
    ]


@pytest.mark.docker
async def test_upsert_is_keyed_by_message_id_and_refused_across_users(
    db: AsyncDatabaseManager,
) -> None:
    embedder = _FakeEmbedder(
        {"first version": [1.0, 0.0], "edited version": [0.0, 1.0]}
    )
    memory = _memory(db, embedder, table="mem_upsert", max_entries_per_user=1)
    await memory.index(
        user_id="u",
        chat_id="c",
        message_id="m",
        role="user",
        content="first version",
        created_at=NOW,
    )
    await memory.index(
        user_id="u",
        chat_id="c2",
        message_id="m",
        role="assistant",
        content="  edited version  ",
        created_at=NOW,
    )

    async with db.get_session_context() as session:
        stored = (
            await session.execute(
                text(
                    "SELECT chat_id, role, content, embedding::text AS v "
                    "FROM mem_upsert"
                ),
            )
        ).all()
    assert [tuple(row) for row in stored] == [
        ("c2", "assistant", "edited version", "[0,1]")
    ]

    await memory.index(
        user_id="victim",
        chat_id="c",
        message_id="v1",
        role="user",
        content="first version",
        created_at=NOW,
    )
    with pytest.raises(ValueError, match="already indexed for another user"):
        await memory.index(
            user_id="u",
            chat_id="c",
            message_id="v1",
            role="user",
            content="edited version",
            created_at=NOW,
        )
    assert await _rows(db, "mem_upsert") == [("u", "c2", "m"), ("victim", "c", "v1")]


@pytest.mark.docker
async def test_deletes_are_scoped_and_join_the_callers_transaction(
    db: AsyncDatabaseManager,
) -> None:
    memory = _memory(db, _FakeEmbedder({}), table="mem_delete")
    for user_id, chat_id, message_id in (
        ("u", "shared", "u1"),
        ("u", "keep", "u2"),
        ("v", "shared", "v1"),
        ("v", "keep", "v2"),
    ):
        await memory.index(
            user_id=user_id,
            chat_id=chat_id,
            message_id=message_id,
            role="user",
            content=f"message {message_id}",
            created_at=NOW,
        )

    with pytest.raises(RuntimeError, match="caller failed"):
        async with db.get_session_context() as session:
            await memory.delete_for_chat(user_id="u", chat_id="shared", session=session)
            raise RuntimeError("caller failed after deleting")
    assert len(await _rows(db, "mem_delete")) == 4

    async with db.get_session_context() as session:
        await memory.delete_for_chat(user_id="u", chat_id="shared", session=session)
    assert await _rows(db, "mem_delete") == [
        ("u", "keep", "u2"),
        ("v", "keep", "v2"),
        ("v", "shared", "v1"),
    ]

    await memory.delete_for_user(user_id="v")
    assert await _rows(db, "mem_delete") == [("u", "keep", "u2")]

    with pytest.raises(RuntimeError, match="caller failed"):
        async with db.get_session_context() as session:
            await memory.delete_for_user(user_id="u", session=session)
            raise RuntimeError("caller failed after erasing")
    assert await _rows(db, "mem_delete") == [("u", "keep", "u2")]


@pytest.mark.docker
async def test_hnsw_index_is_built_on_the_memory_table(
    db: AsyncDatabaseManager,
) -> None:
    memory = _memory(db, _FakeEmbedder({}), table="public.mem_hnsw")
    await memory.ensure_schema(ann_index="hnsw", m=8)
    await memory.ensure_schema(ann_index="hnsw", m=8)

    async with db.get_session_context() as session:
        result = await session.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = 'mem_hnsw'"
            ),
        )
        indexes = {row.indexname: row.indexdef for row in result}
    assert (
        "USING hnsw (embedding vector_cosine_ops)" in indexes["mem_hnsw_embedding_idx"]
    )
    assert "(user_id, created_at)" in indexes["mem_hnsw_user_created_idx"]
    assert "(user_id, chat_id)" in indexes["mem_hnsw_user_chat_idx"]
    with pytest.raises(ValueError, match="already exists as hnsw"):
        await memory.ensure_schema(ann_index="hnsw", m=16)


class _ConstantEmbedder:
    """Embedder that maps every text to the all-ones vector of ``dim``."""

    def __init__(self, dim: int) -> None:
        """Store the dimension.

        Args:
            dim (int): Components per vector.
        """
        self.dim = dim

    async def embed(
        self,
        texts: str | list[str],
        *,
        batch_size: int = 32,
    ) -> list[list[float]]:
        """Return one all-ones vector per text.

        Args:
            texts (str | list[str]): The texts.
            batch_size (int): Ignored.

        Returns:
            list[list[float]]: The vectors.
        """
        items = [texts] if isinstance(texts, str) else list(texts)
        return [[1.0] * self.dim for _ in items]


@pytest.mark.docker
async def test_hnsw_search_keeps_scanning_until_the_users_rows_are_found(
    db: AsyncDatabaseManager,
) -> None:
    """A user owning 1% of the rows still gets hits through the HNSW index.

    Without iterative scans the HNSW scan stops after ``ef_search`` rows of
    any user and the ``user_id`` filter runs on those; the second half
    switches them off to show the planner really used the index and the
    failure the setting prevents.
    """
    memory = PgChatMemory(
        _ConstantEmbedder(8),
        db,
        dim=8,
        table="mem_hnsw_recall",
        min_similarity=-1.0,
        recency_weight=0.0,
        max_entries_per_user=0,
    )
    await memory.ensure_schema(ann_index="hnsw")
    rng = random.Random(11)
    async with db.get_session_context() as session:
        await session.execute(
            text(
                "INSERT INTO mem_hnsw_recall VALUES "
                "(:m, :u, 'c', 'user', 'x', now(), CAST(:v AS vector))",
            ),
            [
                {
                    "m": f"m{i}",
                    "u": "small" if i < 500 else "big",
                    "v": str([rng.gauss(0.0, 1.0) for _ in range(8)]),
                }
                for i in range(50_000)
            ],
        )
        await session.execute(text("ANALYZE mem_hnsw_recall"))
        plan = await session.execute(
            text(
                "EXPLAIN SELECT content FROM mem_hnsw_recall "
                "WHERE user_id = 'small' "
                "ORDER BY embedding <=> CAST(:q AS vector) LIMIT 20",
            ),
            {"q": str([1.0] * 8)},
        )
        plan_text = "\n".join(row[0] for row in plan)
    assert "mem_hnsw_recall_embedding_idx" in plan_text

    assert len(await memory.search(user_id="small", query="q", top_k=5)) == 5

    memory._iterative_scan = False
    assert len(await memory.search(user_id="small", query="q", top_k=5)) < 5
