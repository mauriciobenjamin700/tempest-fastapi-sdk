"""Token counting and context-window management.

Fitting a chat into a model's context window means counting tokens with the
*model's own* tokenizer (never a heuristic — BPE and SentencePiece disagree)
and dropping the oldest turns when it overflows. These helpers do both over a
minimal tokenizer interface (anything with ``encode(text) -> sequence``, which
HuggingFace ``AutoTokenizer`` satisfies), so they work with any local model
and stay pure and testable.
"""

from __future__ import annotations

from typing import Any

DEFAULT_PER_MESSAGE_OVERHEAD: int = 4
"""Rough per-message token overhead (role tags + separators), tiktoken-style."""


def count_tokens(text: str, tokenizer: Any) -> int:
    """Count the tokens in ``text`` using ``tokenizer``.

    Args:
        text (str): The text to measure.
        tokenizer (Any): Anything exposing ``encode(text) -> sequence`` (e.g.
            a HuggingFace ``AutoTokenizer``).

    Returns:
        int: The number of tokens.
    """
    return len(tokenizer.encode(text))


def count_message_tokens(
    messages: list[dict[str, Any]],
    tokenizer: Any,
    *,
    per_message_overhead: int = DEFAULT_PER_MESSAGE_OVERHEAD,
) -> int:
    """Estimate the token cost of a chat ``messages`` list.

    Args:
        messages (list[dict[str, Any]]): Chat turns with a ``content`` string.
        tokenizer (Any): Tokenizer exposing ``encode``.
        per_message_overhead (int): Tokens added per message for role tags and
            separators the chat template injects.

    Returns:
        int: The estimated total token count.
    """
    return sum(
        count_tokens(str(message.get("content", "")), tokenizer) + per_message_overhead
        for message in messages
    )


def truncate_messages(
    messages: list[dict[str, Any]],
    max_tokens: int,
    tokenizer: Any,
    *,
    keep_system: bool = True,
    per_message_overhead: int = DEFAULT_PER_MESSAGE_OVERHEAD,
) -> list[dict[str, Any]]:
    """Drop the oldest turns until the chat fits within ``max_tokens``.

    System messages are kept (when ``keep_system``) and moved to the front; the
    most recent message is always kept even if it alone exceeds the budget. The
    oldest non-system, non-last turns are dropped first.

    Args:
        messages (list[dict[str, Any]]): The full chat history.
        max_tokens (int): The token budget to fit within.
        tokenizer (Any): Tokenizer exposing ``encode``.
        keep_system (bool): Always retain ``system`` messages.
        per_message_overhead (int): Per-message overhead used in the estimate.

    Returns:
        list[dict[str, Any]]: The trimmed messages (system first, then the kept
        tail in order). Empty input returns an empty list.
    """
    if not messages:
        return []
    system = [m for m in messages if keep_system and m.get("role") == "system"]
    rest = [m for m in messages if not (keep_system and m.get("role") == "system")]
    kept = list(rest)

    def total(msgs: list[dict[str, Any]]) -> int:
        return count_message_tokens(
            msgs,
            tokenizer,
            per_message_overhead=per_message_overhead,
        )

    while len(kept) > 1 and total(system + kept) > max_tokens:
        kept.pop(0)
    return system + kept


__all__: list[str] = [
    "DEFAULT_PER_MESSAGE_OVERHEAD",
    "count_message_tokens",
    "count_tokens",
    "truncate_messages",
]
