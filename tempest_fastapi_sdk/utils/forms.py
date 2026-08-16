"""Form encoding for APIs that refuse a JSON request body.

Plenty of third parties still take ``application/x-www-form-urlencoded``
writes and answer JSON — Stripe is the loudest example: every write in
its API is form-encoded, and nesting is expressed by **bracket notation**
rather than by structure:

.. code-block:: text

    metadata[user_id]=42
    items[0][price]=price_123
    expand[]=customer

:func:`form_encode` is the flattening those APIs expect, in one place, so
neither the OpenAPI generator nor a hand-written integration re-derives
it — and so the awkward parts (booleans, ``None``, empty containers,
``Enum`` members, dates) are decided once.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any


def _encode_scalar(value: Any) -> str:
    """Render one leaf value the way a form-encoded API reads it.

    Args:
        value (Any): The leaf to render.

    Returns:
        str: The wire spelling. Booleans become ``"true"`` / ``"false"``
        (Python's ``str(True)`` would send ``"True"``, which these APIs
        reject or, worse, read as a truthy string); ``Enum`` members
        become their ``value``; dates and datetimes become ISO-8601;
        ``Decimal`` keeps its exact text rather than going through
        ``float``.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Enum):
        return _encode_scalar(value.value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _flatten(prefix: str, value: Any, out: dict[str, str]) -> None:
    """Flatten one value into ``out`` under its bracketed key.

    Args:
        prefix (str): The key built so far (``""`` at the root).
        value (Any): The value to flatten.
        out (dict[str, str]): Accumulator, mutated in place.
    """
    if value is None:
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{prefix}[{key}]" if prefix else str(key)
            _flatten(child, item, out)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            _flatten(f"{prefix}[{index}]", item, out)
        return
    out[prefix] = _encode_scalar(value)


def form_encode(payload: Mapping[str, Any] | None) -> dict[str, str]:
    """Flatten a nested payload into bracket-notation form fields.

    Args:
        payload (Mapping[str, Any] | None): The request body as a nested
            mapping. ``None`` yields an empty mapping, so a caller can
            forward an optional body without branching.

    Returns:
        dict[str, str]: Flat ``field -> value`` pairs, ready to hand to
        ``httpx``' ``data=``.

    ``None`` values are **dropped rather than sent empty**: on these APIs
    an empty string is a real value (it clears a field), so serializing
    ``None`` as ``""`` would silently erase data the caller never meant to
    touch. Empty mappings and lists disappear for the same reason — they
    have no bracket path to occupy.

    Examples:
        >>> form_encode({"amount": 1000, "metadata": {"user_id": 42}})
        {'amount': '1000', 'metadata[user_id]': '42'}
        >>> form_encode({"items": [{"price": "price_123", "quantity": 2}]})
        {'items[0][price]': 'price_123', 'items[0][quantity]': '2'}
        >>> form_encode({"paid": True, "note": None})
        {'paid': 'true'}
    """
    out: dict[str, str] = {}
    if payload is None:
        return out
    _flatten("", payload, out)
    return out


__all__: list[str] = [
    "form_encode",
]
