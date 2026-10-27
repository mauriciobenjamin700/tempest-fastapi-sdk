"""Pick a readable text color for a background, from its luminance.

A configurable brand color is only safe to paint text on when the text
color follows it: white on a pale yellow button is unreadable. The admin
theme and the auth emails both let a project set a background, so both
derive the text from the background's WCAG relative luminance here.
"""

from __future__ import annotations

import re

HEX_COLOR_PATTERN: re.Pattern[str] = re.compile(r"#(?:[0-9a-fA-F]{3}){1,2}")
"""``#rgb`` or ``#rrggbb``."""

LIGHT_BACKGROUND_THRESHOLD: float = 0.5
"""Relative luminance above which a background takes dark text."""


def relative_luminance(color: str) -> float | None:
    """Return the WCAG relative luminance of a hex color.

    Args:
        color (str): ``#rgb`` or ``#rrggbb``.

    Returns:
        float | None: ``0.0`` (black) to ``1.0`` (white), or ``None`` when
        ``color`` is not a hex color (``"rebeccapurple"``, ``rgb(...)``).
    """
    value = color.strip()
    if not HEX_COLOR_PATTERN.fullmatch(value):
        return None
    raw = value.removeprefix("#")
    if len(raw) == 3:
        raw = "".join(c * 2 for c in raw)
    channels = [int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def is_light(color: str) -> bool:
    """Tell whether a background needs dark text.

    Args:
        color (str): The background.

    Returns:
        bool: ``True`` for a hex color above
        :data:`LIGHT_BACKGROUND_THRESHOLD`. A color that is not hex is
        assumed dark, the case a named brand color usually is.
    """
    luminance = relative_luminance(color)
    return luminance is not None and luminance > LIGHT_BACKGROUND_THRESHOLD


__all__: list[str] = [
    "HEX_COLOR_PATTERN",
    "LIGHT_BACKGROUND_THRESHOLD",
    "is_light",
    "relative_luminance",
]
