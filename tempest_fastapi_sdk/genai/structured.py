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
    """
    _require_lmfe()
    from lmformatenforcer import JsonSchemaParser

    try:
        from lmformatenforcer.integrations.transformers import (
            build_transformers_prefix_allowed_tokens_fn,
        )
    except ImportError as exc:
        raise ImportError(
            "lm-format-enforcer is installed but its transformers integration "
            "failed to import — this usually means a version skew with the "
            "installed transformers. Pin a compatible pair, pass "
            "constrained=False for best-effort parsing, or use the Ollama "
            "backend (its generate_structured needs no constraint library).",
        ) from exc

    parser = JsonSchemaParser(schema.model_json_schema())
    return build_transformers_prefix_allowed_tokens_fn(tokenizer, parser)


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
