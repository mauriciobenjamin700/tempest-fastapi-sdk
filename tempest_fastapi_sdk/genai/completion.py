"""A structured completion that reports what happened, not only what parsed.

:meth:`~tempest_fastapi_sdk.genai.AnthropicGenerator.chat_structured` and
its OpenAI-compatible twin answer one question — "give me the instance" —
and raise on anything else. A service that pays for every call needs three
more answers, and needs them even when there is no instance:

* **what the call cost**, because a refusal, a reply cut at ``max_tokens``
  or a reply that fails the schema was still billed;
* **why the model stopped**, normalized across providers, because a refusal
  is a signal about the input (block it) while a truncated reply is a signal
  about the budget (raise ``max_tokens``);
* **what it said**, for the log line that explains a failed validation.

:class:`StructuredCompletion` carries all of it, and ``complete_structured``
on both hosted generators returns one instead of raising for an outcome the
model produced. Only transport and HTTP errors still raise, as the typed
:class:`~tempest_fastapi_sdk.genai.HostedAPIError` family.

:func:`image_part` builds the image content part both generators accept, in
the OpenAI shape the SDK's message lists already use; the Anthropic
generator translates it to a Messages API ``image`` block on the way out.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, Literal, Protocol

from tempest_fastapi_sdk.genai.structured import StructuredT, parse_structured
from tempest_fastapi_sdk.genai.tokens import TokenUsage

if TYPE_CHECKING:
    from tempest_fastapi_sdk.genai.schemas import GenerationConfig

Effort = Literal["low", "medium", "high", "xhigh", "max"]
"""How much reasoning a call asks for, from cheapest to most thorough.

The Anthropic spelling (``output_config.effort``). The OpenAI-compatible
generator forwards it under its ``effort_field``, translated through its
``effort_values`` when the provider documents a narrower set.
"""

StopReason = Literal["end", "max_tokens", "refusal", "tool_use", "other"]
"""Why a call stopped, normalized across providers.

``end`` is a natural stop (Anthropic ``end_turn`` / ``stop_sequence``, OpenAI
``stop``); ``max_tokens`` is a reply cut at the budget (``max_tokens`` /
``length``); ``refusal`` is a declined request (Anthropic ``refusal``, OpenAI
``content_filter`` or a non-null ``message.refusal``); ``tool_use`` is a tool
call; ``other`` is anything the mapping does not name — the raw value is kept
in :attr:`StructuredCompletion.provider_stop_reason`.
"""


@dataclass(frozen=True)
class StructuredCompletion(Generic[StructuredT]):
    """The outcome of one ``complete_structured`` call.

    Attributes:
        output (StructuredT | None): The validated instance, or ``None`` when
            the model did not stop naturally or its reply failed the schema.
        text (str): The reply text as received, ``""`` when there was none.
        usage (TokenUsage | None): What the provider reported the call cost,
            present whatever the outcome; ``None`` only when the response
            carried no ``usage``.
        stop_reason (StopReason): Why the model stopped, normalized.
        provider_stop_reason (str | None): The provider's own value
            (``end_turn``, ``length``...), for logs and for values
            :data:`StopReason` folds into ``other``.
        error (ValueError | None): Why ``output`` is ``None`` after a
            natural stop: a reply with no JSON object (``ValueError``) or one
            that failed the schema (``pydantic.ValidationError``, a
            ``ValueError`` subclass). ``None`` when the output parsed, and
            when the stop was not natural — ``stop_reason`` already says why.
    """

    output: StructuredT | None
    text: str
    usage: TokenUsage | None
    stop_reason: StopReason
    provider_stop_reason: str | None
    error: ValueError | None = None

    @property
    def ok(self) -> bool:
        """Whether the call produced a validated instance.

        Returns:
            bool: ``True`` when :attr:`output` is set.
        """
        return self.output is not None


class StructuredCompletionBackend(Protocol):
    """A hosted backend that returns a :class:`StructuredCompletion`.

    Both :class:`~tempest_fastapi_sdk.genai.AnthropicGenerator` and
    :class:`~tempest_fastapi_sdk.genai.OpenAICompatGenerator` implement it,
    and :func:`~tempest_fastapi_sdk.genai.hosted_generator` returns one of
    them — so a service typed against this protocol changes provider by
    changing the model id.
    """

    async def complete_structured(
        self,
        messages: list[dict[str, Any]],
        schema: type[StructuredT],
        *,
        effort: Effort | None = ...,
        cache_system: bool = ...,
        max_tokens: int | None = ...,
        config: GenerationConfig | None = ...,
        **kwargs: Any,
    ) -> StructuredCompletion[StructuredT]:
        """Generate a reply constrained to ``schema`` and report the outcome.

        Args:
            messages (list[dict[str, Any]]): Chat turns; images as
                :func:`image_part` content parts.
            schema (type[StructuredT]): The Pydantic model to produce.
            effort (Effort | None): Reasoning effort, or ``None`` for the
                provider default.
            cache_system (bool): Ask the provider to cache the system turn.
            max_tokens (int | None): Output budget for this call.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Per-call body fields.

        Returns:
            StructuredCompletion[StructuredT]: The outcome.
        """
        ...


def image_part(data: bytes, media_type: str) -> dict[str, Any]:
    """Build an image content part for a message's ``content`` list.

    The shape is the OpenAI ``image_url`` part with a base64 ``data:`` URL,
    which is the message shape every generator in this SDK reads; the
    Anthropic generator translates it to a Messages API ``image`` block.

    Args:
        data (bytes): The image bytes.
        media_type (str): Its media type (``image/png``, ``image/jpeg``...).

    Returns:
        dict[str, Any]: ``{"type": "image_url", "image_url": {"url":
        "data:<media_type>;base64,<data>"}}``.

    Example:
        >>> part = image_part(b"\\x89PNG...", "image/png")
        >>> text = {"type": "text", "text": "Read it."}
        >>> messages = [{"role": "user", "content": [part, text]}]
    """
    encoded = base64.standard_b64encode(data).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{encoded}"},
    }


def build_structured_completion(
    text: str,
    schema: type[StructuredT],
    *,
    usage: TokenUsage | None,
    stop_reason: StopReason,
    provider_stop_reason: str | None,
) -> StructuredCompletion[StructuredT]:
    """Assemble a :class:`StructuredCompletion`, parsing only after a natural stop.

    A refusal or a truncated reply is never parsed: a partial JSON object can
    validate by accident when the schema's trailing fields have defaults, and
    an instance built from a cut reply is worse than none.

    Args:
        text (str): The reply text.
        schema (type[StructuredT]): The Pydantic model to produce.
        usage (TokenUsage | None): The reported usage.
        stop_reason (StopReason): Why the model stopped, normalized.
        provider_stop_reason (str | None): The provider's own value.

    Returns:
        StructuredCompletion[StructuredT]: The outcome; ``error`` set when a
        natural stop carried no valid instance.
    """
    if stop_reason != "end":
        return StructuredCompletion(
            output=None,
            text=text,
            usage=usage,
            stop_reason=stop_reason,
            provider_stop_reason=provider_stop_reason,
        )
    try:
        output = parse_structured(text, schema)
    except ValueError as exc:
        return StructuredCompletion(
            output=None,
            text=text,
            usage=usage,
            stop_reason=stop_reason,
            provider_stop_reason=provider_stop_reason,
            error=exc,
        )
    return StructuredCompletion(
        output=output,
        text=text,
        usage=usage,
        stop_reason=stop_reason,
        provider_stop_reason=provider_stop_reason,
    )


__all__: list[str] = [
    "Effort",
    "StopReason",
    "StructuredCompletion",
    "StructuredCompletionBackend",
    "build_structured_completion",
    "image_part",
]
