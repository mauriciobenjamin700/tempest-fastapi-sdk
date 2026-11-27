"""Normalize what a model hands back as a tool call.

Two shapes reach the tool loops of :class:`~tempest_fastapi_sdk.agents.Agent`
and :class:`~tempest_fastapi_sdk.genai.AIChatPipeline`: the backend's native
``tool_calls`` list, and — from models that pick the right tool and the right
arguments but never use the native channel — the same call written as JSON in
the reply's ``content``. :func:`parse_text_tool_calls` turns the second shape
into the first, so both loops dispatch it the same way.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from typing import Any

_FENCE: re.Pattern[str] = re.compile(
    r"\A```[A-Za-z0-9_-]*[ \t]*\r?\n(?P<body>.*?)\r?\n?```\Z",
    re.DOTALL,
)
"""A reply that is one markdown code fence and nothing else.

The info string (``json``, ``JSON``, none) is ignored. A fence surrounded by
prose does not match: a call is recognized only when it is the whole reply.
"""


def parse_arguments(raw: object) -> tuple[dict[str, Any], str | None]:
    """Normalize a call's ``arguments`` into a dict.

    Ollama and the SDK's ``TextGenerator`` hand over a dict; the OpenAI wire
    format (vLLM, TGI, hosted APIs) hands over a JSON **string**. Both are
    accepted. Anything that is not an object — malformed JSON, an array, a
    number — is reported as an error rather than silently replaced by ``{}``,
    because a tool run with empty arguments fails somewhere far from the real
    cause.

    Args:
        raw (object): The ``arguments`` value from the call.

    Returns:
        tuple[dict[str, Any], str | None]: The arguments, and the reason they
        are unusable (``None`` when they are fine).
    """
    if raw is None or raw == "":
        return {}, None
    if isinstance(raw, dict):
        return raw, None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            return {}, f"arguments are not valid JSON: {exc}"
        if isinstance(parsed, dict):
            return parsed, None
        return {}, "arguments must be a JSON object"
    return {}, f"arguments must be a JSON object, got {type(raw).__name__}"


def _decode_values(text: str) -> list[object]:
    """Decode whitespace-separated JSON values, flattening top-level lists.

    Args:
        text (str): The trimmed reply text.

    Returns:
        list[object]: Every decoded entry, or ``[]`` when anything in the
        text is not JSON (prose before, between or after the values).
    """
    decoder = json.JSONDecoder()
    entries: list[object] = []
    position = 0
    while position < len(text):
        if text[position] not in "{[":
            return []
        try:
            value, position = decoder.raw_decode(text, position)
        except ValueError:
            return []
        if isinstance(value, list):
            entries.extend(value)
        else:
            entries.append(value)
        while position < len(text) and text[position].isspace():
            position += 1
    return entries


def parse_text_tool_calls(
    content: str,
    tool_names: Collection[str],
) -> list[dict[str, Any]]:
    """Read tool calls a model wrote as JSON in its reply text.

    The whole reply — after trimming whitespace and, when the reply is a
    single markdown code fence, the fence — must be JSON calls and nothing
    else: one object ``{"name": ..., "arguments": {...}}``, a non-empty list
    of them, or several such values one after the other separated only by
    whitespace (``qwen2.5-coder:14b`` writes a two-call turn as two objects
    on separate lines). ``arguments`` may be an object or a string holding a
    JSON object. Every ``name`` must be one of ``tool_names``; a single entry
    that is not a registered tool, or whose arguments are not an object,
    makes the whole reply plain text. All or nothing, because running half
    of what the model wrote and answering with the rest would be worse than
    either reading.

    Args:
        content (str): The model's reply text.
        tool_names (Collection[str]): The tools the model may call in this
            turn.

    Returns:
        list[dict[str, Any]]: The calls in the native ``tool_calls`` shape
        (``{"function": {"name", "arguments"}}``, with ``arguments`` a
        dict), or ``[]`` when the reply is not a call to a registered tool.
    """
    text = content.strip()
    if not text or not tool_names:
        return []
    fenced = _FENCE.match(text)
    if fenced is not None:
        text = fenced.group("body").strip()
    entries = _decode_values(text)
    if not entries:
        return []
    calls: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or "arguments" not in entry:
            return []
        name = entry.get("name")
        if not isinstance(name, str) or name not in tool_names:
            return []
        arguments, problem = parse_arguments(entry["arguments"])
        if problem is not None or not isinstance(entry["arguments"], dict | str):
            return []
        calls.append({"function": {"name": name, "arguments": arguments}})
    return calls
