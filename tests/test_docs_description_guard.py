"""Guard that every docs page declares a one-line ``description:``.

The description is what ``mkdocs_hooks/llmstxt.py`` writes after each link of
``llms.txt`` (``- [title](url): description``), and what Material puts in the
page's ``<meta name="description">``. Before #876 no page had one: the 321
links of ``llms.txt`` carried only the title, so a model had to fetch a page
to learn whether "Arquitetura" or "Arquitetura recomendada" answered its
question.

A page fails when its front matter has no ``description:``, when the value is
empty or not a string, when it spans more than one line (a YAML block scalar
or a value with a line break), or when it is longer than
:data:`DESCRIPTION_MAX_CHARS`. Both locales are checked: ``<page>.md`` and
``<page>.en.md`` each carry their own line.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT: Path = Path(__file__).resolve().parent.parent
DOCS: Path = ROOT / "docs"

DESCRIPTION_MAX_CHARS: int = 200
"""Ceiling of one description: one line in ``llms.txt`` and a search snippet."""

EXCLUDED: frozenset[str] = frozenset({"CLAUDE.md"})
"""Files under ``docs/`` that are not site pages (``exclude_docs``)."""

_FRONT_MATTER_RE: re.Pattern[str] = re.compile(r"\A---[ \t]*\n(.*?\n)---[ \t]*\n", re.S)
_RAW_LINE_RE: re.Pattern[str] = re.compile(r"^description:[ \t]*(.*)$", re.M)


def description_problems(docs: Path) -> list[str]:
    """Return one message per page whose ``description:`` breaks the rule.

    Args:
        docs (Path): The docs directory to scan.

    Returns:
        list[str]: ``"<path>: <problem>"`` for each failing page, sorted by
        path. Empty when every page complies.
    """
    problems: list[str] = []
    for page in sorted(docs.rglob("*.md")):
        if page.name in EXCLUDED:
            continue
        rel = page.relative_to(docs).as_posix()
        text = page.read_text(encoding="utf-8")
        match = _FRONT_MATTER_RE.match(text)
        if match is None:
            problems.append(f"{rel}: no front matter")
            continue
        meta = yaml.safe_load(match.group(1)) or {}
        value = meta.get("description") if isinstance(meta, dict) else None
        raw = _RAW_LINE_RE.search(match.group(1))
        if value is None or raw is None:
            problems.append(f"{rel}: no description")
        elif not isinstance(value, str) or not value.strip():
            problems.append(f"{rel}: empty description")
        elif raw.group(1).strip()[:1] in {"|", ">"} or "\n" in value.strip():
            problems.append(f"{rel}: description spans more than one line")
        elif len(value) > DESCRIPTION_MAX_CHARS:
            problems.append(
                f"{rel}: description has {len(value)} characters, "
                f"above {DESCRIPTION_MAX_CHARS}"
            )
    return problems


def _write(docs: Path, rel: str, text: str) -> None:
    """Write one page under a scratch docs directory.

    Args:
        docs (Path): Scratch docs directory.
        rel (str): Page path relative to it.
        text (str): File content.
    """
    path = docs / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_every_page_declares_a_description() -> None:
    """Every page of ``docs/``, in both locales, has a valid description."""
    problems = description_problems(DOCS)
    assert not problems, (
        "pages without a valid one-line `description:` front matter "
        f"(max {DESCRIPTION_MAX_CHARS} characters):\n" + "\n".join(problems)
    )


def test_the_guard_scans_both_locales() -> None:
    """The scan reaches ``.en.md`` files and the nested sub-pages."""
    pages = {p.relative_to(DOCS).as_posix() for p in DOCS.rglob("*.md")}
    assert "recipes/genai.en.md" in pages
    assert any(p.startswith("recipes/genai/") for p in pages)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# Arquitetura\n\nCorpo.\n", "no front matter"),
        ("---\ntitle: X\n---\n\n# X\n", "no description"),
        ('---\ndescription: ""\n---\n\n# X\n', "empty description"),
        ("---\ndescription:\n---\n\n# X\n", "no description"),
        (
            "---\ndescription: >\n  Use para X;\n  contém Y.\n---\n\n# X\n",
            "more than one line",
        ),
        (
            '---\ndescription: "Use para X;\\ncontém Y."\n---\n\n# X\n',
            "more than one line",
        ),
        (
            f'---\ndescription: "{"x" * (DESCRIPTION_MAX_CHARS + 1)}"\n---\n\n# X\n',
            "above",
        ),
    ],
)
def test_guard_fires(tmp_path: Path, text: str, expected: str) -> None:
    """Each broken shape is reported, including the pre-#876 page with none.

    Args:
        tmp_path (Path): Scratch docs directory.
        text (str): Content of the broken page.
        expected (str): Fragment the report must contain.
    """
    _write(tmp_path, "page.md", '---\ndescription: "Use para X."\n---\n\n# X\n')
    _write(tmp_path, "page.en.md", text)
    problems = description_problems(tmp_path)
    assert len(problems) == 1, problems
    assert problems[0].startswith("page.en.md: ")
    assert expected in problems[0]


def test_guard_passes_a_valid_page(tmp_path: Path) -> None:
    """A page with a short single-line description passes.

    Args:
        tmp_path (Path): Scratch docs directory.
    """
    _write(
        tmp_path,
        "page.md",
        '---\ndescription: "Use para X; contém Y."\n---\n\n# X\n',
    )
    _write(tmp_path, "CLAUDE.md", "# Not a page\n")
    assert description_problems(tmp_path) == []
