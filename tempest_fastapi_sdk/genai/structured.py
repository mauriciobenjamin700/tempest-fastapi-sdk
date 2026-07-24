"""Schema-constrained structured output for the genai backends.

Turns a free-text completion into a validated Pydantic instance. Two layers:

* :func:`parse_structured` — extract a JSON object out of a model completion
  (tolerating Markdown fences and surrounding prose) and validate it against a
  Pydantic schema. Pure, no optional dependency.
* :func:`build_prefix_allowed_tokens_fn` — build a ``transformers``
  ``prefix_allowed_tokens_fn`` from a schema via ``lm-format-enforcer`` so the
  local :class:`~tempest_fastapi_sdk.genai.text.TextGenerator` can only emit
  tokens that keep the output schema-valid. Requires the ``[genai-structured]``
  extra.

The Ollama path needs neither helper's constraint machinery — the daemon
accepts a ``format`` JSON schema directly — but both paths finish with
:func:`parse_structured`.
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel

StructuredT = TypeVar("StructuredT", bound=BaseModel)

_FENCE_RE: re.Pattern[str] = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _require_lmfe() -> Any:
    """Import ``lm-format-enforcer`` or raise a helpful error.

    Returns:
        Any: The imported ``lmformatenforcer`` module.

    Raises:
        ImportError: When the ``[genai-structured]`` extra is not installed.
    """
    try:
        import lmformatenforcer
    except ImportError as exc:
        raise ImportError(
            "Constrained structured output requires the optional "
            "[genai-structured] extra. Install with: "
            "pip install tempest-fastapi-sdk[genai-structured]",
        ) from exc
    return lmformatenforcer


def build_prefix_allowed_tokens_fn(tokenizer: Any, schema: type[BaseModel]) -> Any:
    """Build a ``transformers`` token filter that enforces ``schema``.

    Args:
        tokenizer (Any): The model tokenizer.
        schema (type[BaseModel]): The Pydantic schema the output must satisfy.

    Returns:
        Any: A ``prefix_allowed_tokens_fn`` to pass to ``model.generate`` so
        only schema-valid continuations are allowed.

    Raises:
        ImportError: When the ``[genai-structured]`` extra is not installed.

    Note:
        Reimplements ``lm-format-enforcer``'s transformers adapter against the
        library's stable **core** (``JsonSchemaParser`` + ``TokenEnforcer`` +
        ``TokenEnforcerTokenizerData``). The upstream
        ``lmformatenforcer.integrations.transformers`` module imports
        ``PreTrainedTokenizerBase`` from ``transformers.tokenization_utils``,
        which moved in transformers 5.x — importing it raises. Building the
        adapter here from the core avoids that broken import entirely.
    """
    _require_lmfe()
    from lmformatenforcer import JsonSchemaParser
    from lmformatenforcer.tokenenforcer import TokenEnforcer

    parser = JsonSchemaParser(schema.model_json_schema())
    tokenizer_data = _token_enforcer_tokenizer_data(tokenizer)
    enforcer = TokenEnforcer(tokenizer_data, parser)

    def prefix_allowed_tokens_fn(_batch_id: int, sent: Any) -> list[int]:
        allowed = enforcer.get_allowed_tokens(sent.tolist())
        return list(allowed.allowed_tokens)

    return prefix_allowed_tokens_fn


def _token_enforcer_tokenizer_data(tokenizer: Any) -> Any:
    """Build ``TokenEnforcerTokenizerData`` from a HuggingFace tokenizer.

    Mirrors ``lm-format-enforcer``'s
    ``integrations.transformers.build_token_enforcer_tokenizer_data`` but
    without importing that module (see :func:`build_prefix_allowed_tokens_fn`).
    Tolerates the constructor-signature difference across lmfe versions
    (older: 3 args; newer: ``use_bitmask`` + ``vocab_size``).

    Args:
        tokenizer (Any): A HuggingFace tokenizer.

    Returns:
        Any: The ``TokenEnforcerTokenizerData`` for this tokenizer.
    """
    from lmformatenforcer.tokenenforcer import TokenEnforcerTokenizerData

    vocab_size = len(tokenizer)
    token_zero = tokenizer.encode("0")[-1]
    special = set(tokenizer.all_special_ids)
    regular: list[tuple[int, str, bool]] = []
    for token_id in range(vocab_size):
        if token_id in special:
            continue
        decoded_after_zero = tokenizer.decode([token_zero, token_id])[1:]
        decoded = tokenizer.decode([token_id])
        regular.append(
            (token_id, decoded_after_zero, len(decoded_after_zero) > len(decoded))
        )

    def decode_fn(tokens: list[int]) -> str:
        return str(tokenizer.decode(tokens)).rstrip("\N{REPLACEMENT CHARACTER}")

    try:
        return TokenEnforcerTokenizerData(
            regular,
            decode_fn,
            tokenizer.eos_token_id,
            False,
            vocab_size,
        )
    except TypeError:
        return TokenEnforcerTokenizerData(regular, decode_fn, tokenizer.eos_token_id)


def _extract_json(text: str) -> Any:
    """Pull a JSON value out of a model completion.

    Tolerates Markdown code fences and prose around the object: tries the whole
    stripped string first, then falls back to the first ``{`` … last ``}`` span.

    Args:
        text (str): The raw completion.

    Returns:
        Any: The decoded JSON value.

    Raises:
        ValueError: When no JSON object can be decoded.
    """
    stripped = _FENCE_RE.sub("", text.strip())
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(stripped[start : end + 1])
        except ValueError as exc:
            raise ValueError(
                "could not parse a JSON object from the model output",
            ) from exc
    raise ValueError("no JSON object found in the model output")


def parse_structured(text: str, schema: type[StructuredT]) -> StructuredT:
    """Parse and validate a model completion into a ``schema`` instance.

    Args:
        text (str): The raw model completion (may contain fences / prose).
        schema (type[StructuredT]): The Pydantic model to validate against.

    Returns:
        StructuredT: The validated instance.

    Raises:
        ValueError: When no JSON object is present in ``text``.
        pydantic.ValidationError: When the JSON does not satisfy ``schema``.
    """
    return schema.model_validate(_extract_json(text))


__all__: list[str] = [
    "build_prefix_allowed_tokens_fn",
    "parse_structured",
]
