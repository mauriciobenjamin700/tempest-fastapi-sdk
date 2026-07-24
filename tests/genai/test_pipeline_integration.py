"""Tests for AIChatPipeline moderation + context-truncation integration."""

from __future__ import annotations

from tempest_fastapi_sdk.genai import RuleModerator
from tempest_fastapi_sdk.genai.pipeline import AIChatPipeline
from tests.genai.conftest import FakeTextBackend


class _WordTokenizer:
    def encode(self, text: str) -> list[str]:
        return text.split()


class TestModeration:
    async def test_flagged_input_short_circuits(self) -> None:
        backend = FakeTextBackend(replies=["should not be used"])
        pipeline = AIChatPipeline(
            backend,  # type: ignore[arg-type]
            moderator=RuleModerator(["badword"]),
            blocked_message="BLOCKED",
        )
        result = await pipeline.respond(
            user_id="u", chat_id="c", content="a badword here"
        )
        assert result.reply == "BLOCKED"
        assert backend.calls == []

    async def test_clean_input_passes_through(self) -> None:
        backend = FakeTextBackend(replies=["clean reply"])
        pipeline = AIChatPipeline(
            backend,  # type: ignore[arg-type]
            moderator=RuleModerator(["badword"]),
        )
        result = await pipeline.respond(user_id="u", chat_id="c", content="hello there")
        assert result.reply == "clean reply"
        assert backend.calls

    async def test_flagged_output_replaced(self) -> None:
        backend = FakeTextBackend(replies=["this contains badword"])
        pipeline = AIChatPipeline(
            backend,  # type: ignore[arg-type]
            moderator=RuleModerator(["badword"]),
            blocked_message="BLOCKED",
        )
        result = await pipeline.respond(
            user_id="u", chat_id="c", content="fine question"
        )
        assert result.reply == "BLOCKED"

    async def test_stream_flagged_input_yields_block(self) -> None:
        backend = FakeTextBackend(replies=["nope"])
        pipeline = AIChatPipeline(
            backend,  # type: ignore[arg-type]
            moderator=RuleModerator(["badword"]),
            blocked_message="BLOCKED",
        )
        pieces = [
            p
            async for p in pipeline.stream(
                user_id="u", chat_id="c", content="a badword"
            )
        ]
        assert pieces == ["BLOCKED"]


class TestTruncation:
    async def test_truncates_history_to_budget(self) -> None:
        backend = FakeTextBackend(replies=["ok"])
        pipeline = AIChatPipeline(
            backend,  # type: ignore[arg-type]
            tokenizer=_WordTokenizer(),
            max_context_tokens=10,
        )
        history = [
            {"role": "user", "content": f"turn number {i} with some words"}
            for i in range(20)
        ]
        await pipeline.respond(
            user_id="u", chat_id="c", content="latest", history=history
        )
        method, sent = backend.calls[0]
        assert method == "chat"
        assert len(sent) < len(history) + 1
        assert sent[-1]["content"] == "latest"

    async def test_no_truncation_without_tokenizer(self) -> None:
        backend = FakeTextBackend(replies=["ok"])
        pipeline = AIChatPipeline(backend)  # type: ignore[arg-type]
        history = [{"role": "user", "content": f"t{i}"} for i in range(5)]
        await pipeline.respond(
            user_id="u", chat_id="c", content="latest", history=history
        )
        _method, sent = backend.calls[0]
        assert len(sent) == len(history) + 1
