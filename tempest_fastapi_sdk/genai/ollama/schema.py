"""Strict JSON schema for the Ollama ``format`` constraint."""

from __future__ import annotations

import copy
from typing import Any


def _require_every_property(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``schema`` whose every object requires all its properties.

    Pydantic lists in ``required`` only the fields without a default, and the
    daemon's constrained decoder lets the model omit every other one. The
    reply then validates — the default fills the gap — and is wrong, with no
    signal to the caller. Measured in the 2026-11-26/27 local-LLM study
    (Ollama 0.30.11, 33 extraction cases over 3 schemas, temperature 0):
    under the plain schema ``gpt-oss:20b`` never emitted a
    ``Field(default_factory=list)`` field and answered ``[]`` for it in all
    11 orders; requiring every property took per-field accuracy from 70.8
    to 95.4 on that model, and from 69.2 to 87.3 (``qwen2.5:3b``), 56.6 to
    74.3 (``lfm2.5-thinking:1.2b``), 90.0 to 95.7 (``qwen2.5-coder:14b``)
    and 84.4 to 89.9 (``ministral-3:14b``).

    An optional field keeps the ``anyOf [..., {"type": "null"}]`` Pydantic
    already emits for ``X | None``, so requiring it only makes the model
    state ``null``. A field with a non-null default becomes required on the
    wire; the reply is still validated against the original model.

    The walk follows only the keywords that hold sub-schemas, so a field
    literally named ``properties`` is treated as a field.

    Args:
        schema (dict[str, Any]): A schema from ``model_json_schema()``.

    Returns:
        dict[str, Any]: The tightened copy; ``schema`` is not modified.
    """
    tightened: dict[str, Any] = copy.deepcopy(schema)
    _require_node_properties(tightened)
    return tightened


def _require_node_properties(node: dict[str, Any]) -> None:
    """Apply :func:`_require_every_property` to one schema node, in place.

    Args:
        node (dict[str, Any]): A schema node.
    """
    properties = node.get("properties")
    if isinstance(properties, dict):
        node["required"] = list(properties)
        for child in properties.values():
            if isinstance(child, dict):
                _require_node_properties(child)
    for keyword in ("$defs", "definitions"):
        holder = node.get(keyword)
        if isinstance(holder, dict):
            for child in holder.values():
                if isinstance(child, dict):
                    _require_node_properties(child)
    for keyword in ("anyOf", "allOf", "oneOf", "prefixItems"):
        holder = node.get(keyword)
        if isinstance(holder, list):
            for child in holder:
                if isinstance(child, dict):
                    _require_node_properties(child)
    for keyword in ("items", "additionalProperties", "not"):
        child = node.get(keyword)
        if isinstance(child, dict):
            _require_node_properties(child)
