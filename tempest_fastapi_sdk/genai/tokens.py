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


def _cache_hit_tokens(payload: dict[str, Any]) -> int:
    """Read the cached-prefix token count out of a ``usage`` object.

    Args:
        payload (dict[str, Any]): The provider's ``usage`` mapping.

    Returns:
        int: How many prompt tokens the provider served from cache, in
        whichever of the two OpenAI-family spellings it used; ``0`` when
        neither is present or the value is not an integer.
    """
    flat = payload.get("prompt_cache_hit_tokens")
    if isinstance(flat, int):
        return flat
    details = payload.get("prompt_tokens_details")
    if isinstance(details, dict):
        nested = details.get("cached_tokens")
        if isinstance(nested, int):
            return nested
    return 0


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
        cache_hit_tokens (int): The **slice of** ``input_tokens`` the
            provider served from a cached prefix and billed at a reduced
            rate — not tokens on top of the prompt. Adding it to
            ``input_tokens`` double-counts. ``0`` when the provider
            reported none, which is also what a provider that has no
            prompt cache reports.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: int = 0

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
            cache_hit_tokens=self.cache_hit_tokens + other.cache_hit_tokens,
        )

    @classmethod
    def from_payload(cls, payload: Any) -> TokenUsage | None:
        """Build a usage from a provider's ``usage`` object.

        Reads the OpenAI-compatible spelling (``prompt_tokens`` /
        ``completion_tokens`` / ``total_tokens``), which DeepSeek, vLLM, TGI
        and the OpenAI API itself all emit.

        The cached-prefix count has **two** spellings in that same family,
        so both are read: DeepSeek's flat ``prompt_cache_hit_tokens`` and
        OpenAI's nested ``prompt_tokens_details.cached_tokens``. Reading
        only one of them silently prices a discounted call at full rate on
        the other provider, which is a wrong number rather than a missing
        one.

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
            cache_hit_tokens=_cache_hit_tokens(payload),
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
    most recent turn is always kept even if it alone exceeds the budget. The
    oldest non-system, non-last turns are dropped first.

    An assistant turn carrying ``tool_calls`` and the ``role="tool"`` results
    that answer it are dropped **together**, never split: a history that
    starts with a tool result whose call was trimmed away, or ends a call
    with no result, breaks the pairing the OpenAI chat format requires
    between a call and its results, and strict providers refuse it.
    If the most recent turn is such a tool result, its whole group (the call
    plus every result) is what is always kept.

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

    groups = _tool_call_groups(rest)
    while len(groups) > 1 and total(system + _flatten(groups)) > max_tokens:
        groups.pop(0)
    return system + _flatten(groups)


def _tool_call_groups(
    messages: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Split turns into the units :func:`truncate_messages` may drop.

    Every turn is its own unit, except that the ``role="tool"`` turns right
    after an assistant turn with ``tool_calls`` join that assistant turn's
    unit. A run of tool turns with no call before it (a history already
    trimmed elsewhere) forms a unit of its own.

    Args:
        messages (list[dict[str, Any]]): Non-system turns, in order.

    Returns:
        list[list[dict[str, Any]]]: The units, in order.
    """
    groups: list[list[dict[str, Any]]] = []
    for message in messages:
        if message.get("role") == "tool" and groups:
            head = groups[-1][0]
            if head.get("tool_calls") or head.get("role") == "tool":
                groups[-1].append(message)
                continue
        groups.append([message])
    return groups


def _flatten(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Concatenate the units back into one turn list.

    Args:
        groups (list[list[dict[str, Any]]]): Units from
            :func:`_tool_call_groups`.

    Returns:
        list[dict[str, Any]]: The turns, in order.
    """
    return [message for group in groups for message in group]


__all__: list[str] = [
    "DEFAULT_PER_MESSAGE_OVERHEAD",
    "TokenUsage",
    "count_message_tokens",
    "count_tokens",
    "truncate_messages",
]
