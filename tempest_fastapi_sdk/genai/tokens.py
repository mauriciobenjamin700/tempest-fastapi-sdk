"""Token counting, context-window management, and reported usage.

Fitting a chat into a model's context window means counting tokens with the
*model's own* tokenizer (never a heuristic — BPE and SentencePiece disagree)
and dropping the oldest turns when it overflows. These helpers do both over a
minimal tokenizer interface (anything with ``encode(text) -> sequence``, which
HuggingFace ``AutoTokenizer`` satisfies), so they work with any local model
and stay pure and testable.

:class:`TokenUsage` is the other half: what the **provider** says a call
cost, rather than what a tokenizer estimates. A hosted API reports it per
response, and it is the number that gets billed — so it is the one worth
persisting when you need per-user accounting, not a local re-count.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_PER_MESSAGE_OVERHEAD: int = 4
"""Rough per-message token overhead (role tags + separators), tiktoken-style."""


@dataclass(frozen=True)
class TokenUsage:
    """What one generation call consumed, as the provider reported it.

    ``total`` is carried rather than recomputed from the two halves. Every
    provider is free to bill something other than ``input + output`` —
    cached-prefix discounts and reasoning tokens both show up that way — so
    the reported total is the authority, and :meth:`from_payload` only falls
    back to the sum when the field is absent.

    Attributes:
        input_tokens (int): Tokens in the prompt.
        output_tokens (int): Tokens generated.
        total_tokens (int): What the provider counts for the call.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        """Add two usages, for a job made of several calls.

        Map-reduce summarization is the case this exists for: one logical
        summary costs N chunk calls plus one reduce call, and what you want
        to record is the job, not each leg.

        Args:
            other (TokenUsage): The usage to add.

        Returns:
            TokenUsage: The summed usage.
        """
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )

    @classmethod
    def from_payload(cls, payload: Any) -> TokenUsage | None:
        """Build a usage from a provider's ``usage`` object.

        Reads the OpenAI-compatible spelling (``prompt_tokens`` /
        ``completion_tokens`` / ``total_tokens``), which DeepSeek, vLLM, TGI
        and the OpenAI API itself all emit.

        Args:
            payload (Any): The ``usage`` object from the response, or
                ``None`` when the response carried none.

        Returns:
            TokenUsage | None: The parsed usage, or ``None`` when ``payload``
            is not a mapping — including when it is ``None``. Callers treat
            that as "nothing to record", which is honest: a zeroed usage
            would claim the call was free.
        """
        if not isinstance(payload, dict):
            return None
        input_tokens = int(payload.get("prompt_tokens", 0) or 0)
        output_tokens = int(payload.get("completion_tokens", 0) or 0)
        reported_total = payload.get("total_tokens")
        total = (
            int(reported_total)
            if reported_total is not None
            else input_tokens + output_tokens
        )
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total,
        )


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
        """Return the token count of ``msgs`` under the outer settings.

        Args:
            msgs (list[dict[str, Any]]): The messages to measure.

        Returns:
            int: Total tokens, including the per-message overhead.
        """
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
    "TokenUsage",
    "count_message_tokens",
    "count_tokens",
    "truncate_messages",
]
