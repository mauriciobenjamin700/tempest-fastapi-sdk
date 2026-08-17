"""Schema-constrained structured output for the genai backends.

Turns a free-text completion into a validated Pydantic instance. Three
layers:

* :func:`parse_structured` — extract a JSON **object** out of a model
  completion (tolerating Markdown fences and surrounding prose) and validate
  it against a Pydantic schema. Pure, no optional dependency.
* :func:`extract_json_list` / :func:`parse_structured_list` — the same for a
  JSON **array**, which is what a prompt asking for "a list of items"
  actually returns. ``extract_json_list`` answers "is there a list in here?"
  without raising, so a caller can retry the generation instead of failing.
* :func:`build_prefix_allowed_tokens_fn` — build a ``transformers``
  ``prefix_allowed_tokens_fn`` from a schema via ``lm-format-enforcer`` so the
  local :class:`~tempest_fastapi_sdk.genai.text.TextGenerator` can only emit
  tokens that keep the output schema-valid. Requires the ``[genai-structured]``
  extra.

The Ollama path needs neither helper's constraint machinery — the daemon
accepts a ``format`` JSON schema directly — but both paths finish with
:func:`parse_structured`.

Extraction scans for a **balanced** span rather than slicing to the last
closing bracket. A model that appends one stray ``}`` or ``]`` after an
otherwise perfect payload used to break the parse, and retrying reproduced
the same slice because the defect was in the cut, not in the model.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from tempest_fastapi_sdk.genai.schemas import GenerationConfig

StructuredT = TypeVar("StructuredT", bound=BaseModel)


class SupportsGenerate(Protocol):
    """The one method :func:`generate_structured_list` needs from a backend.

    Narrower than
    :class:`~tempest_fastapi_sdk.genai.text.TextBackend` on purpose: this
    module is imported *by* ``text``, so depending on the full protocol
    would close an import cycle. Every backend in the SDK satisfies it.
    """

    async def generate(
        self,
        prompt: str,
        *,
        config: GenerationConfig | None = ...,
        **kwargs: Any,
    ) -> str:
        """Return a completion for ``prompt``."""
        ...


_FENCE_RE: re.Pattern[str] = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

# A fenced block anywhere in the completion, not only wrapping the whole of
# it. ``_FENCE_RE`` is anchored, so it misses the common shape where a model
# writes a sentence, then the fence, then a closing sentence.
_FENCED_BLOCK_RE: re.Pattern[str] = re.compile(
    r"```(?:json)?\s*(.*?)\s*```",
    re.DOTALL | re.IGNORECASE,
)

_CLOSERS: dict[str, str] = {"{": "}", "[": "]"}


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


def _unfence(text: str) -> str:
    """Strip the Markdown fence a model wrapped its payload in.

    Handles both shapes seen in practice: the fence wrapping the whole
    completion (what ``_FENCE_RE`` matches, anchored) and a fence buried
    between two sentences of prose (what ``_FENCED_BLOCK_RE`` finds).

    Args:
        text (str): The raw completion.

    Returns:
        str: The completion with the fence removed, stripped of surrounding
        whitespace. Returns the input unchanged when there is no fence.
    """
    stripped = _FENCE_RE.sub("", text.strip())
    block = _FENCED_BLOCK_RE.search(stripped)
    return block.group(1) if block else stripped


def _balanced_span(text: str, opener: str) -> str | None:
    """Isolate the first balanced ``opener`` … closer span in ``text``.

    Counts depth instead of slicing to the last closing bracket, which is
    what makes a stray trailing bracket survivable: ``[{"a": 1}]]`` yields
    ``[{"a": 1}]`` rather than failing to decode. A non-greedy regex would
    not do either, because it stops at the first closer and so truncates any
    payload containing a nested array or object.

    Characters inside a JSON string are skipped (escapes included), so a
    bracket in a value — ``{"label": "urgent [sic]"}`` — does not throw the
    count off.

    Args:
        text (str): The text to scan.
        opener (str): ``"{"`` or ``"["``.

    Returns:
        str | None: The balanced span, or ``None`` when ``opener`` never
        appears or is never closed (a completion truncated at the token
        ceiling, typically).
    """
    closer = _CLOSERS[opener]
    start = text.find(opener)
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    return None


def _decode_span(text: str, opener: str) -> Any | None:
    """Decode the first balanced ``opener`` span, or ``None`` if undecodable.

    Args:
        text (str): The already-unfenced completion.
        opener (str): ``"{"`` or ``"["``.

    Returns:
        Any | None: The decoded JSON value, or ``None`` when no balanced span
        exists or the span is not valid JSON.
    """
    span = _balanced_span(text, opener)
    if span is None:
        return None
    try:
        return json.loads(span)
    except ValueError:
        return None


def _extract_json(text: str) -> Any:
    """Pull a JSON object out of a model completion.

    Tolerates Markdown code fences and prose around the object: tries the
    whole unfenced string first, then falls back to the first balanced
    ``{`` … ``}`` span.

    Args:
        text (str): The raw completion.

    Returns:
        Any: The decoded JSON value.

    Raises:
        ValueError: When no JSON object can be decoded.
    """
    unfenced = _unfence(text)
    try:
        return json.loads(unfenced)
    except ValueError:
        pass
    if "{" not in unfenced:
        raise ValueError("no JSON object found in the model output")
    decoded = _decode_span(unfenced, "{")
    if decoded is None:
        raise ValueError("could not parse a JSON object from the model output")
    return decoded


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


def extract_json_list(text: str) -> list[Any] | None:
    """Pull a JSON array out of a model completion, without raising.

    A prompt asking for "a list of items" comes back as an array, and the
    ways it arrives malformed differ from an object: a fence the prompt asked
    the model not to add, a sentence before the payload, one stray ``]`` at
    the end. This tolerates all three.

    Returning ``None`` instead of raising is the point: it is the signal to
    **retry the generation**, which is a different response from "the model
    answered, and the answer was an empty list". A caller that cannot tell
    those apart either retries a valid empty result or gives up on a
    recoverable formatting slip.

    Args:
        text (str): The raw model completion.

    Returns:
        list[Any] | None: The decoded list, or ``None`` when the completion
        holds no decodable array (no array at all, malformed JSON, or a valid
        JSON value that is not a list).

    Example:

        >>> extract_json_list('Here you go:\\n```json\\n[{"a": 1}]\\n```')
        [{'a': 1}]
        >>> extract_json_list("not a list") is None
        True
    """
    unfenced = _unfence(text)
    try:
        whole = json.loads(unfenced)
    except ValueError:
        whole = None
    if isinstance(whole, list):
        return whole

    decoded = _decode_span(unfenced, "[")
    return decoded if isinstance(decoded, list) else None


def parse_structured_list(
    text: str,
    schema: type[StructuredT],
    *,
    skip_invalid: bool = False,
) -> list[StructuredT]:
    """Parse a model completion into a list of ``schema`` instances.

    Args:
        text (str): The raw model completion (may contain fences / prose).
        schema (type[StructuredT]): The Pydantic model each item must satisfy.
        skip_invalid (bool): Drop items that fail validation instead of
            raising. Use it when one bad item out of ten is worth keeping the
            other nine — a suggestion list, for instance — and leave it off
            when the caller needs all-or-nothing.

    Returns:
        list[StructuredT]: The validated items, in the order the model
        produced them.

    Raises:
        ValueError: When no JSON array is present in ``text``.
        pydantic.ValidationError: When an item does not satisfy ``schema``
            and ``skip_invalid`` is False.
    """
    items = extract_json_list(text)
    if items is None:
        raise ValueError("no JSON array found in the model output")

    parsed: list[StructuredT] = []
    for item in items:
        try:
            parsed.append(schema.model_validate(item))
        except ValidationError:
            if not skip_invalid:
                raise
    return parsed


class StructuredFormatError(ValueError):
    """The model never produced a usable JSON array within the attempt budget.

    Subclasses ``ValueError`` so code already catching that keeps working.

    Attributes:
        attempts (int): How many generations were spent.
        last_output (str): The final raw completion, truncated, so the log
            line says what the model actually wrote instead of only that it
            was wrong.
    """

    def __init__(self, attempts: int, last_output: str) -> None:
        """Build the error.

        Args:
            attempts (int): Generations spent.
            last_output (str): The final raw completion.
        """
        self.attempts = attempts
        self.last_output = last_output[:500]
        super().__init__(
            f"no JSON array after {attempts} attempt(s); "
            f"last output began: {self.last_output!r}",
        )


async def generate_structured_list(
    backend: SupportsGenerate,
    prompt: str,
    schema: type[StructuredT],
    *,
    config: GenerationConfig | None = None,
    max_attempts: int = 3,
    temperature_step: float = 0.2,
    skip_invalid: bool = True,
) -> list[StructuredT]:
    """Generate a list of ``schema`` items, retrying on unusable output.

    Retrying a failed generation at the **same** temperature is close to
    pointless: greedy decoding is deterministic, so attempt two reproduces
    attempt one. Each retry therefore adds ``temperature_step``, giving
    sampling a real chance to leave the bad state. The first attempt stays
    greedy, because it is the most reliable one.

    Only a **structural** failure costs an attempt — no array in the output
    at all. An array that parses but holds one malformed item is not a
    formatting failure, so it is handled by ``skip_invalid`` rather than by
    burning a generation.

    Args:
        backend (SupportsGenerate): Anything with
            ``async generate(prompt, *, config=...) -> str`` — the local
            ``TextGenerator``, ``OllamaGenerator``, or
            ``OpenAICompatGenerator``.
        prompt (str): The prompt, which should ask for a JSON array.
        schema (type[StructuredT]): The Pydantic model each item must match.
        config (GenerationConfig | None): Base parameters. Its
            ``temperature`` is replaced per attempt; everything else is kept.
        max_attempts (int): Generations to spend before giving up.
        temperature_step (float): Added per retry (attempt 1 at ``0.0``,
            attempt 2 at ``temperature_step``, and so on).
        skip_invalid (bool): Drop items that fail validation instead of
            raising.

    Returns:
        list[StructuredT]: The validated items. An **empty list is a
        success** — the model answered, and the answer is no items.

    Raises:
        StructuredFormatError: No attempt produced a decodable array.
        ValueError: When ``max_attempts`` is below 1.
        pydantic.ValidationError: When an item fails validation and
            ``skip_invalid`` is False.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    raw = ""
    for attempt in range(max_attempts):
        attempt_config = _config_at_temperature(config, temperature_step * attempt)
        raw = await backend.generate(prompt, config=attempt_config)
        items = extract_json_list(raw)
        if items is None:
            continue
        parsed: list[StructuredT] = []
        for item in items:
            try:
                parsed.append(schema.model_validate(item))
            except ValidationError:
                if not skip_invalid:
                    raise
        return parsed

    raise StructuredFormatError(max_attempts, raw)


def _config_at_temperature(
    config: GenerationConfig | None,
    temperature: float,
) -> GenerationConfig:
    """Copy ``config`` with ``temperature`` replaced.

    Copied rather than mutated because a caller's config is commonly built
    once and shared across calls; raising its temperature in place would
    leak this retry into every other use of that object.

    Args:
        config (GenerationConfig | None): The base config, or ``None``.
        temperature (float): The temperature for this attempt.

    Returns:
        GenerationConfig: A config carrying the requested temperature.
    """
    from tempest_fastapi_sdk.genai.schemas import GenerationConfig as _Config

    if config is None:
        return _Config(temperature=temperature)
    return config.model_copy(update={"temperature": temperature})


__all__: list[str] = [
    "StructuredFormatError",
    "build_prefix_allowed_tokens_fn",
    "extract_json_list",
    "generate_structured_list",
    "parse_structured",
    "parse_structured_list",
]
