"""ChatMemory recall over-fetch and timezone-correct quota eviction.

Regression suite for two audit findings: :meth:`ChatMemory.search` asked
Chroma for exactly ``top_k`` rows and re-ranked only those, so a recent
message ranked just below the raw top-K could never outrank older ones (the
promise the class docstring makes); and the quota sorted ``created_at`` as ISO
strings, which orders ``10:00+05:00`` after ``08:00+00:00`` although it is the
earlier instant, evicting the wrong message.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from tempest_fastapi_sdk.genai.rag import ChatMemory
from tests.genai.test_chat_memory import (
    _HAS_CHROMA,
    _FakeChromaClient,
    _FakeEmbedder,
)


@pytest.fixture(params=["fake", "real"])
def chroma_client(request: pytest.FixtureRequest) -> Any:
    """Yield the fake Chroma client, plus a real ephemeral one when installed.

    Args:
        request (pytest.FixtureRequest): The parametrization.

    Returns:
        Any: A Chroma-like client.
    """
    if request.param == "real":
        if not _HAS_CHROMA:
            pytest.skip("chromadb not installed")
        import chromadb

        return chromadb.EphemeralClient(
            settings=chromadb.config.Settings(anonymized_telemetry=False)
        )
    return _FakeChromaClient()


def _embedder() -> _FakeEmbedder:
    """Return an embedder where three old messages beat the new one on similarity.

    Returns:
        _FakeEmbedder: The canned-vector embedder.
    """
    return _FakeEmbedder(
        {
            "old strong match one": [1.0, 0.0],
            "old strong match two": [1.0, 0.01],
            "old strong match three": [1.0, 0.02],
            "new slightly weaker match": [0.9, 0.4358898943540674],
            "query": [1.0, 0.0],
        }
    )


async def _seed(memory: ChatMemory) -> None:
    """Index three 60-day-old strong matches and one fresh weaker match.

    Args:
        memory (ChatMemory): The memory to fill.
    """
    now = datetime.now(UTC)
    for number, content in enumerate(
        ["old strong match one", "old strong match two", "old strong match three"]
    ):
        await memory.index(
            user_id="u1",
            chat_id="old",
            message_id=f"old-{number}",
            role="user",
            content=content,
            created_at=now - timedelta(days=60),
        )
    await memory.index(
        user_id="u1",
        chat_id="new",
        message_id="new-0",
        role="user",
        content="new slightly weaker match",
        created_at=now,
    )


class TestRecallOverFetch:
    async def test_a_recent_message_outside_the_raw_top_k_can_win(
        self, chroma_client: Any
    ) -> None:
        memory = ChatMemory(_embedder(), client=chroma_client, collection_name="of-1")
        await _seed(memory)
        hits = await memory.search(user_id="u1", query="query", top_k=1)
        assert [hit.content for hit in hits] == ["new slightly weaker match"]

    async def test_multiplier_one_reranks_only_the_raw_top_k(
        self, chroma_client: Any
    ) -> None:
        memory = ChatMemory(
            _embedder(),
            client=chroma_client,
            collection_name="of-2",
            candidate_multiplier=1,
        )
        await _seed(memory)
        hits = await memory.search(user_id="u1", query="query", top_k=1)
        assert [hit.content for hit in hits] == ["old strong match one"]

    def test_multiplier_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="candidate_multiplier"):
            ChatMemory(_embedder(), candidate_multiplier=0)


class TestQuotaEvictionAcrossTimezones:
    async def test_the_earliest_instant_is_evicted_whatever_its_offset(
        self, chroma_client: Any
    ) -> None:
        embedder = _FakeEmbedder(
            {
                "earliest, written in +05:00": [1.0, 0.0],
                "later, written in UTC": [1.0, 0.0],
                "latest of the three": [1.0, 0.0],
            }
        )
        memory = ChatMemory(
            embedder,
            client=chroma_client,
            collection_name="tz-1",
            max_entries_per_user=2,
        )
        plus_five = timezone(timedelta(hours=5))
        await memory.index(
            user_id="u1",
            chat_id="c1",
            message_id="earliest",
            role="user",
            content="earliest, written in +05:00",
            created_at=datetime(2026, 1, 1, 10, 0, tzinfo=plus_five),
        )
        await memory.index(
            user_id="u1",
            chat_id="c1",
            message_id="later",
            role="user",
            content="later, written in UTC",
            created_at=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
        )
        await memory.index(
            user_id="u1",
            chat_id="c1",
            message_id="latest",
            role="user",
            content="latest of the three",
            created_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        )
        remaining = set(
            memory._get_collection().get(where={"user_id": "u1"})["ids"],
        )
        assert remaining == {"later", "latest"}

    async def test_rows_without_the_epoch_field_still_order_by_instant(
        self, chroma_client: Any
    ) -> None:
        embedder = _FakeEmbedder({"fresh message": [1.0, 0.0]})
        memory = ChatMemory(
            embedder,
            client=chroma_client,
            collection_name="tz-2",
            max_entries_per_user=2,
        )
        collection = memory._get_collection()
        collection.upsert(
            ids=["legacy-early", "legacy-late"],
            documents=["legacy early", "legacy late"],
            embeddings=[[1.0, 0.0], [1.0, 0.0]],
            metadatas=[
                {
                    "user_id": "u1",
                    "chat_id": "c1",
                    "role": "user",
                    "created_at": "2026-01-01T10:00:00+05:00",
                },
                {
                    "user_id": "u1",
                    "chat_id": "c1",
                    "role": "user",
                    "created_at": "2026-01-01T08:00:00+00:00",
                },
            ],
        )
        await memory.index(
            user_id="u1",
            chat_id="c1",
            message_id="fresh",
            role="user",
            content="fresh message",
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        remaining = set(collection.get(where={"user_id": "u1"})["ids"])
        assert remaining == {"legacy-late", "fresh"}

    async def test_created_at_is_stored_in_utc(self, chroma_client: Any) -> None:
        memory = ChatMemory(
            _FakeEmbedder({"a message in +05:00": [1.0, 0.0], "q": [1.0, 0.0]}),
            client=chroma_client,
            collection_name="tz-3",
        )
        await memory.index(
            user_id="u1",
            chat_id="c1",
            message_id="m",
            role="user",
            content="a message in +05:00",
            created_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone(timedelta(hours=5))),
        )
        meta = memory._get_collection().get(where={"user_id": "u1"})["metadatas"][0]
        assert meta["created_at"] == "2026-01-01T05:00:00+00:00"
        assert meta["created_at_ts"] == datetime(2026, 1, 1, 5, tzinfo=UTC).timestamp()
