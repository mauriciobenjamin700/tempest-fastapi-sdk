"""The order ruff's ``RUF022`` demands of a generated ``__all__``.

Generated code passes the same lint gate as everything else, so the
generator has to mirror the formatter rather than approximate it. This
lived twice — once in each regeneration script — until the OpenPix refresh
of v0.260.0 broke one copy and not the other: the two-digit disambiguation
counter on truncated class names is the first input where a plain string
sort and ruff's natural sort disagree, and only the OpenPix surface reached
it. One copy is one place to be wrong.
"""

from __future__ import annotations

import re


def natural(name: str) -> tuple[object, ...]:
    """Split a name into text and numeric runs, the way ruff compares them.

    Args:
        name (str): The exported name.

    Returns:
        tuple[object, ...]: Alternating text and integer parts.

    ``RUF022`` sorts a digit run by value, not by character: measured
    against ruff on the v0.260.0 surface, it wants ``...Orig9`` before
    ``...Orig10``, while ``sorted()`` puts ``Orig10`` first.
    """
    return tuple(
        int(part) if part.isdigit() else part
        for part in re.split(r"(\d+)", name)
        if part
    )


def export_sort_key(name: str) -> tuple[int, tuple[object, ...]]:
    """Rank one exported name the way ruff's ``RUF022`` sorts ``__all__``.

    Args:
        name (str): The exported name.

    Returns:
        tuple[int, tuple[object, ...]]: Group first — ``0`` for
        ``SCREAMING_CASE`` constants, ``1`` for ``CamelCase`` classes, ``2``
        for anything else — then the name in natural order.
    """
    if name.isupper():
        return (0, natural(name))
    if name[:1].isupper():
        return (1, natural(name))
    return (2, natural(name))


__all__: list[str] = ["export_sort_key", "natural"]
