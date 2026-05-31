"""Extract a single H3 recipe section from README.md by its title.

Usage:
    python scripts/extract_recipe.py "Audit & soft-delete mixins recipe"

Prints the content (between the matching `### <title>` and the next `### `
header or `## ` if the recipes section ends).
"""

from __future__ import annotations

import sys
from pathlib import Path


def extract(title: str, readme: Path) -> str:
    """Return the markdown body of one H3 recipe in ``readme``.

    Args:
        title (str): The exact H3 title (without ``### `` prefix).
        readme (Path): Path to the README.

    Returns:
        str: The recipe body (without the H3 header line).
    """
    text = readme.read_text(encoding="utf-8")
    needle = f"### {title}"
    start = text.find(needle)
    if start < 0:
        raise SystemExit(f"recipe not found: {title!r}")
    body_start = text.find("\n", start) + 1
    rest = text[body_start:]
    next_h3 = rest.find("\n### ")
    next_h2 = rest.find("\n## ")
    candidates = [n for n in (next_h3, next_h2) if n >= 0]
    if not candidates:
        return rest.rstrip() + "\n"
    end = min(candidates)
    return rest[:end].rstrip() + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: extract_recipe.py <recipe title>")
    print(extract(sys.argv[1], Path(__file__).parent.parent / "README.md"))
