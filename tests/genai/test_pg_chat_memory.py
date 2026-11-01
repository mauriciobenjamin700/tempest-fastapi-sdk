"""``PgChatMemory`` paths that never reach the database.

Construction-time validation, the early returns of ``index`` / ``search``
and the ``ensure_schema`` parameter checks all run before a session opens,
so they are pinned here against a database manager that fails when touched.
The database behaviour lives in ``test_pg_chat_memory_live`` (``docker``).
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager
from tempest_fastapi_sdk.genai.rag import PgChatMemory
from tests.genai.test_chat_memory import _FakeEmbedder


class _UntouchableDatabase:
    """Stand-in manager whose every attribute access fails the test."""

    def __getattr__(self, name: str) -> Any:
        """Fail on any use.

        Args:
            name (str): The attribute asked for.

        Raises:
            AssertionError: Always.
        """
        raise AssertionError(f"database touched: {name}")


def _memory(**options: Any) -> PgChatMemory:
    """Build a memory over the untouchable database.

    Args:
        **options (Any): Forwarded constructor keywords.

    Returns:
        PgChatMemory: The memory.
    """
    db = cast(AsyncDatabaseManager, _UntouchableDatabase())
    embedder = _FakeEmbedder({"three dims": [1.0, 0.0, 0.0]})
    return PgChatMemory(embedder, db, **{"dim": 2, **options})


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"table": "chat; DROP TABLE users"}, "table must be"),
        ({"dim": 0}, "dim must be a positive integer"),
        ({"dim": True}, "dim must be a positive integer"),
        ({"candidate_multiplier": 0}, "candidate_multiplier must be >= 1"),
        ({"ef_search": 0}, "ef_search must be a positive integer"),
        ({"probes": -1}, "probes must be a positive integer"),
    ],
)
def test_invalid_construction_is_refused(options: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _memory(**options)


def test_defaults_match_chat_memory() -> None:
    memory = _memory()

    assert (
        memory.table,
        memory.top_k,
        memory.min_similarity,
        memory.recency_halflife_days,
        memory.recency_weight,
        memory.max_entries_per_user,
        memory.min_content_chars,
        memory.candidate_multiplier,
    ) == ("chat_memory", 5, 0.55, 14.0, 0.5, 50_000, 6, 4)
    assert _memory(table="memory.chat_turns").ann_index_name == (
        "memory.chat_turns_embedding_idx"
    )


async def test_short_message_is_skipped_without_a_session() -> None:
    indexed = await _memory().index(
        user_id="u",
        chat_id="c",
        message_id="m",
        role="user",
        content="  hi  ",
        created_at=datetime.now(UTC),
    )

    assert indexed is False


async def test_vector_of_the_wrong_dimension_is_refused_before_writing() -> None:
    with pytest.raises(ValueError, match="returned 3 dimensions"):
        await _memory().index(
            user_id="u",
            chat_id="c",
            message_id="m",
            role="user",
            content="three dims",
            created_at=datetime.now(UTC),
        )


@pytest.mark.parametrize(("query", "top_k"), [("   ", None), ("question", 0)])
async def test_empty_search_returns_no_hits_without_a_session(
    query: str,
    top_k: int | None,
) -> None:
    assert await _memory().search(user_id="u", query=query, top_k=top_k) == []


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"ann_index": "hnsw", "lists": 10}, "lists does not apply"),
        ({"m": 8}, "needs ann_index"),
        ({"ann_index": "flat"}, "ann_index must be"),
    ],
)
async def test_ensure_schema_checks_options_before_connecting(
    options: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        await _memory().ensure_schema(**options)


def test_importing_pg_chat_memory_does_not_load_chromadb() -> None:
    probe = (
        "import sys\n"
        "from tempest_fastapi_sdk.genai.rag import PgChatMemory\n"
        "print('chromadb' in sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "False"
