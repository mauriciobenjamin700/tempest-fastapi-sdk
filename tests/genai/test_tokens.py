"""Tests for token counting and context truncation (no model)."""

from __future__ import annotations

from typing import Any

from tempest_fastapi_sdk.genai import (
    count_message_tokens,
    count_tokens,
    truncate_messages,
)


class _WordTokenizer:
    """A stand-in tokenizer: one token per whitespace-separated word."""

    def encode(self, text: str) -> list[str]:
        return text.split()


class TestCountTokens:
    def test_counts_words(self) -> None:
        assert count_tokens("a b c d", _WordTokenizer()) == 4

    def test_empty(self) -> None:
        assert count_tokens("", _WordTokenizer()) == 0

    def test_message_tokens_add_overhead(self) -> None:
        messages = [{"role": "user", "content": "a b"}]
        assert count_message_tokens(messages, _WordTokenizer()) == 6

    def test_message_overhead_configurable(self) -> None:
        messages = [{"role": "user", "content": "a b"}]
        assert (
            count_message_tokens(messages, _WordTokenizer(), per_message_overhead=0)
            == 2
        )


class TestTruncateMessages:
    def test_empty_returns_empty(self) -> None:
        assert truncate_messages([], 100, _WordTokenizer()) == []

    def test_no_op_when_fits(self) -> None:
        messages = [{"role": "user", "content": "a b"}]
        assert truncate_messages(messages, 100, _WordTokenizer()) == messages

    def test_drops_oldest_keeps_system_and_last(self) -> None:
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old one two three"},
            {"role": "assistant", "content": "mid one two three"},
            {"role": "user", "content": "latest"},
        ]
        result = truncate_messages(
            messages, max_tokens=6, tokenizer=_WordTokenizer(), per_message_overhead=0
        )
        roles = [m["role"] for m in result]
        assert roles[0] == "system"
        assert result[-1]["content"] == "latest"
        assert len(result) < len(messages)

    def test_always_keeps_last_even_if_over_budget(self) -> None:
        messages = [
            {"role": "user", "content": "a"},
            {"role": "user", "content": "way too many words here indeed"},
        ]
        result = truncate_messages(
            messages, max_tokens=1, tokenizer=_WordTokenizer(), per_message_overhead=0
        )
        assert len(result) == 1
        assert result[0]["content"].startswith("way too many")

    def test_system_dropped_when_keep_system_false(self) -> None:
        messages = [
            {"role": "system", "content": "sys one two three four five"},
            {"role": "user", "content": "latest"},
        ]
        result = truncate_messages(
            messages,
            max_tokens=1,
            tokenizer=_WordTokenizer(),
            keep_system=False,
            per_message_overhead=0,
        )
        assert result == [{"role": "user", "content": "latest"}]


class TestTruncateKeepsToolCallsWithResults:
    """A tool call and its results are dropped together or kept together."""

    def _history(self) -> list[dict[str, Any]]:
        """Build a chat whose middle is one call answered by two results."""
        return [
            {"role": "user", "content": "one two three four"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "a"}, {"id": "b"}],
            },
            {"role": "tool", "tool_call_id": "a", "content": "r1"},
            {"role": "tool", "tool_call_id": "b", "content": "r2"},
            {"role": "user", "content": "last"},
        ]

    def test_never_starts_with_an_orphan_tool_result(self) -> None:
        """Dropping one turn at a time left ``tool`` turns with no call."""
        history = self._history()
        for budget in range(0, 40):
            result = truncate_messages(
                history, budget, _WordTokenizer(), per_message_overhead=0
            )
            assert result[0].get("role") != "tool", budget

    def test_group_is_dropped_whole(self) -> None:
        """A budget that fits only the last turn drops the call and results."""
        result = truncate_messages(
            self._history(), 1, _WordTokenizer(), per_message_overhead=0
        )
        assert result == [{"role": "user", "content": "last"}]

    def test_group_is_kept_whole(self) -> None:
        """A budget for the group plus the last turn keeps all of it."""
        result = truncate_messages(
            self._history(), 3, _WordTokenizer(), per_message_overhead=0
        )
        assert [m["role"] for m in result] == ["assistant", "tool", "tool", "user"]

    def test_trailing_tool_result_keeps_its_call(self) -> None:
        """When the newest turn is a tool result, its call is kept with it."""
        history = self._history()[:4]
        result = truncate_messages(history, 0, _WordTokenizer(), per_message_overhead=0)
        assert [m["role"] for m in result] == ["assistant", "tool", "tool"]
