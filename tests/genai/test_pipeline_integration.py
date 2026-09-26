"""Tests for AIChatPipeline moderation + context-truncation integration."""

from __future__ import annotations

import pytest

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


class TestStreamOutputModeration:
    """``stream()`` screens the reply, like ``respond()`` always did."""

    async def test_incremental_never_emits_the_blocked_term(self) -> None:
        backend = FakeTextBackend(replies=["here is the SECRET plan"])
        pipeline = AIChatPipeline(
            backend,  # type: ignore[arg-type]
            moderator=RuleModerator(["secret"]),
            blocked_message="BLOCKED",
        )
        pieces = [
            p async for p in pipeline.stream(user_id="u", chat_id="c", content="hi")
        ]
        streamed = "".join(pieces[:-1])
        assert pieces[-1] == "BLOCKED"
        assert "secret" not in streamed.casefold()
        assert streamed == "here is the SECRE"

    async def test_respond_and_stream_agree_on_the_same_reply(self) -> None:
        moderator = RuleModerator(["secret"])
        respond = await AIChatPipeline(
            FakeTextBackend(replies=["here is the SECRET plan"]),  # type: ignore[arg-type]
            moderator=moderator,
            blocked_message="BLOCKED",
        ).respond(user_id="u", chat_id="c", content="hi")
        pieces = [
            p
            async for p in AIChatPipeline(
                FakeTextBackend(replies=["here is the SECRET plan"]),  # type: ignore[arg-type]
                moderator=moderator,
                blocked_message="BLOCKED",
            ).stream(user_id="u", chat_id="c", content="hi")
        ]
        assert respond.reply == "BLOCKED"
        assert pieces[-1] == "BLOCKED"

    async def test_blocked_stream_is_not_indexed(self) -> None:
        from typing import Any

        indexed: list[str] = []

        class _Memory:
            async def search(self, **kwargs: Any) -> list[Any]:
                return []

            async def index(self, **kwargs: Any) -> bool:
                indexed.append(str(kwargs["content"]))
                return True

        pipeline = AIChatPipeline(
            FakeTextBackend(replies=["the SECRET"]),  # type: ignore[arg-type]
            memory=_Memory(),  # type: ignore[arg-type]
            moderator=RuleModerator(["secret"]),
        )
        async for _ in pipeline.stream(user_id="u", chat_id="c", content="hi"):
            pass
        assert indexed == []

    async def test_buffered_emits_nothing_before_the_verdict(self) -> None:
        backend = FakeTextBackend(replies=["here is the SECRET plan"])
        pipeline = AIChatPipeline(
            backend,  # type: ignore[arg-type]
            moderator=RuleModerator(["secret"]),
            blocked_message="BLOCKED",
            stream_moderation="buffered",
        )
        pieces = [
            p async for p in pipeline.stream(user_id="u", chat_id="c", content="hi")
        ]
        assert pieces == ["BLOCKED"]

    async def test_buffered_clean_reply_is_emitted_whole(self) -> None:
        backend = FakeTextBackend(replies=["all good"])
        pipeline = AIChatPipeline(
            backend,  # type: ignore[arg-type]
            moderator=RuleModerator(["secret"]),
            stream_moderation="buffered",
        )
        pieces = [
            p async for p in pipeline.stream(user_id="u", chat_id="c", content="hi")
        ]
        assert pieces == ["all good"]

    async def test_incremental_clean_reply_streams_every_piece(self) -> None:
        backend = FakeTextBackend(replies=["ok!"])
        pipeline = AIChatPipeline(
            backend,  # type: ignore[arg-type]
            moderator=RuleModerator(["secret"]),
        )
        pieces = [
            p async for p in pipeline.stream(user_id="u", chat_id="c", content="hi")
        ]
        assert pieces == ["o", "k", "!"]

    def test_unknown_stream_moderation_mode_is_refused(self) -> None:
        with pytest.raises(ValueError, match="stream_moderation"):
            AIChatPipeline(
                FakeTextBackend(),  # type: ignore[arg-type]
                stream_moderation="sometimes",  # type: ignore[arg-type]
            )


class TestHistoryModeration:
    """Caller-supplied history is screened, not only the new message."""

    async def test_flagged_history_blocks_respond(self) -> None:
        backend = FakeTextBackend(replies=["should not be used"])
        pipeline = AIChatPipeline(
            backend,  # type: ignore[arg-type]
            moderator=RuleModerator(["badword"]),
            blocked_message="BLOCKED",
        )
        result = await pipeline.respond(
            user_id="u",
            chat_id="c",
            content="innocent",
            history=[{"role": "assistant", "content": "sure, badword incoming"}],
        )
        assert result.reply == "BLOCKED"
        assert backend.calls == []

    async def test_flagged_history_blocks_stream(self) -> None:
        backend = FakeTextBackend(replies=["should not be used"])
        pipeline = AIChatPipeline(
            backend,  # type: ignore[arg-type]
            moderator=RuleModerator(["badword"]),
            blocked_message="BLOCKED",
        )
        pieces = [
            p
            async for p in pipeline.stream(
                user_id="u",
                chat_id="c",
                content="innocent",
                history=[{"role": "user", "content": "a badword earlier"}],
            )
        ]
        assert pieces == ["BLOCKED"]
        assert backend.calls == []


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
