"""Guard that the navigation stays alphabetical and bilingual.

Two failures this catches, both of which degrade navigation silently:

* **A page added out of alphabetical order.** The recipe list is long enough
  that a reader navigates it by scanning, so one entry in the wrong place
  costs a full pass over 50+ items.
* **A page added to one language's nav only.** The site defines the PT-BR
  ``nav`` and, because ``mkdocs-static-i18n`` cannot reorder a shared nav per
  locale, a **second full nav** for EN-US (so English is alphabetical in
  English, not in Portuguese). Two navs drift: a new recipe reaches one and
  not the other, and the EN reader loses the page entirely.

Only the sections that are *meant* to be alphabetical are checked. The
top-level tabs and the learning-project pages follow a deliberate reading
order (install before tutorial, business rules before endpoint map), so
sorting them would be the regression, not the fix.

The same ordering is asserted for the **tables on the Recipes landing**,
which are the other surface a reader scans to find a page — a nav sorted
against an unsorted index is still a bad lookup.

Only the ``nav`` blocks are parsed, never the whole config: ``mkdocs.yml``
carries ``!!python/name:`` tags that PyYAML's SafeLoader rejects, while the
nav itself is plain YAML.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT: Path = Path(__file__).resolve().parents[1]

ALPHABETICAL_SECTIONS: dict[str, str] = {
    "Receitas": "Recipes",
    "Exemplos completos": "Complete examples",
}
"""Sections required to be alphabetical, mapped PT title to EN title."""


def sort_key(label: str) -> str:
    """Return the accent- and case-insensitive key a reader scans by.

    ``Auth flow`` and ``Áudio`` must compare as ``auth flow`` / ``audio``:
    a reader looking for a page does not think about diacritics or case.

    Args:
        label (str): The visible nav label.

    Returns:
        str: The normalized sort key.
    """
    lowered = label.strip().strip('"').lower()
    stripped = "".join(
        char
        for char in unicodedata.normalize("NFD", lowered)
        if not unicodedata.combining(char)
    )
    return re.sub(r"^[`*\[(]+", "", stripped)


def _yaml_block(text: str, header: str) -> Any:
    """Parse one indented YAML block out of ``mkdocs.yml``.

    Args:
        text (str): The whole file.
        header (str): The block's opening line, e.g. ``"nav:"``, including
            its indentation.

    Returns:
        Any: The parsed value of the block's single key.

    Raises:
        AssertionError: If the header is absent.
    """
    lines = text.splitlines()
    indent = len(header) - len(header.lstrip(" "))
    try:
        start = next(
            i for i, line in enumerate(lines) if line.rstrip() == header.rstrip()
        )
    except StopIteration:  # pragma: no cover - both blocks are required
        pytest.fail(f"mkdocs.yml has no {header.strip()!r} block")
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if line.strip() and (len(line) - len(line.lstrip(" "))) <= indent:
            break
        block.append(line)
    dedented = "\n".join(line[indent:] for line in block)
    return next(iter(yaml.safe_load(dedented).values()))


@pytest.fixture(scope="session")
def config_text() -> str:
    """Return the raw ``mkdocs.yml``.

    Returns:
        str: The file contents.
    """
    return (ROOT / "mkdocs.yml").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def pt_nav(config_text: str) -> list[Any]:
    """Return the default-language (PT-BR) nav.

    Args:
        config_text (str): The raw ``mkdocs.yml``.

    Returns:
        list[Any]: The nav tree.
    """
    return _yaml_block(config_text, "nav:")


@pytest.fixture(scope="session")
def en_nav(config_text: str) -> list[Any]:
    """Return the EN-US nav declared on the i18n plugin's locale.

    Args:
        config_text (str): The raw ``mkdocs.yml``.

    Returns:
        list[Any]: The nav tree.
    """
    return _yaml_block(config_text, " " * 10 + "nav:")


def _pages(entry: Any, out: list[str]) -> None:
    """Collect every ``.md`` path reachable from a nav fragment.

    Args:
        entry (Any): A nav entry — a path, a mapping, or a list.
        out (list[str]): Accumulator, in nav order.
    """
    if isinstance(entry, str):
        if entry.endswith(".md"):
            out.append(entry)
    elif isinstance(entry, dict):
        for value in entry.values():
            _pages(value, out)
    elif isinstance(entry, list):
        for item in entry:
            _pages(item, out)


def _section(nav: Any, title: str) -> list[Any] | None:
    """Find a section's item list by title, at any depth.

    Args:
        nav (Any): The nav tree (or a fragment of it).
        title (str): The section title to look for.

    Returns:
        list[Any] | None: The section's items, or ``None`` when absent.
    """
    if isinstance(nav, dict):
        for key, value in nav.items():
            if key == title and isinstance(value, list):
                return value
            found = _section(value, title)
            if found is not None:
                return found
    elif isinstance(nav, list):
        for item in nav:
            found = _section(item, title)
            if found is not None:
                return found
    return None


def _labels(items: list[Any]) -> list[str]:
    """Return the visible labels of a section's entries.

    Bare paths (a section's index page) carry no label and are skipped —
    they are pinned first by convention, not sorted.

    Args:
        items (list[Any]): The section's items.

    Returns:
        list[str]: The labels, in nav order.
    """
    return [next(iter(item)) for item in items if isinstance(item, dict)]


class TestBilingualNav:
    def test_both_navs_cover_the_same_pages(
        self, pt_nav: list[Any], en_nav: list[Any]
    ) -> None:
        pt_pages: list[str] = []
        en_pages: list[str] = []
        _pages(pt_nav, pt_pages)
        _pages(en_nav, en_pages)
        assert set(pt_pages) == set(en_pages), (
            "PT and EN navs list different pages; missing from EN: "
            f"{sorted(set(pt_pages) - set(en_pages))}; missing from PT: "
            f"{sorted(set(en_pages) - set(pt_pages))}"
        )

    def test_no_page_is_listed_twice(
        self, pt_nav: list[Any], en_nav: list[Any]
    ) -> None:
        for language, nav in (("pt", pt_nav), ("en", en_nav)):
            pages: list[str] = []
            _pages(nav, pages)
            duplicates = sorted({p for p in pages if pages.count(p) > 1})
            assert not duplicates, f"{language} nav lists {duplicates} more than once"


class TestAlphabeticalSections:
    @pytest.mark.parametrize(
        ("language", "index"),
        [("pt", 0), ("en", 1)],
    )
    def test_sections_are_alphabetical(
        self,
        language: str,
        index: int,
        pt_nav: list[Any],
        en_nav: list[Any],
    ) -> None:
        nav = (pt_nav, en_nav)[index]
        for pt_title, en_title in ALPHABETICAL_SECTIONS.items():
            title = pt_title if language == "pt" else en_title
            items = _section(nav, title)
            assert items is not None, f"{language} nav has no {title!r} section"
            labels = _labels(items)
            assert labels, f"{language} nav section {title!r} has no labelled entries"
            assert labels == sorted(labels, key=sort_key), (
                f"{language} nav section {title!r} is not alphabetical; expected "
                f"{sorted(labels, key=sort_key)}"
            )


INDEX_TABLES: tuple[tuple[str, str], ...] = (
    ("docs/recipes/index.md", "| Tema | Cobre |"),
    ("docs/recipes/index.md", "| Exemplo | O que junta |"),
    ("docs/recipes/index.en.md", "| Theme | Covers |"),
    ("docs/recipes/index.en.md", "| Example | What it combines |"),
)
"""The landing tables whose first column must stay alphabetical."""


class TestRecipeIndexTables:
    @pytest.mark.parametrize(("page", "header"), INDEX_TABLES)
    def test_table_is_alphabetical(self, page: str, header: str) -> None:
        text = (ROOT / page).read_text(encoding="utf-8")
        assert header in text, f"{page} has no {header!r} table"
        after_header = text.index(header) + len(header)
        body = text[after_header:]
        rows = [
            line
            for line in body.splitlines()
            if line.startswith("| **[")
            or (line.startswith("| ") and not line.startswith("| ---"))
        ]
        block: list[str] = []
        for row in rows:
            if not row.startswith("| **["):
                break
            block.append(row)
        assert block, f"{page} table {header!r} has no rows"
        names = [
            match.group(1).strip()
            for match in (re.match(r"\| \*\*\[([^»\]]+)", row) for row in block)
            if match is not None
        ]
        assert len(names) == len(block), f"{page} table {header!r} has unparsed rows"
        assert names == sorted(names, key=sort_key), (
            f"{page} table {header!r} is not alphabetical; expected "
            f"{sorted(names, key=sort_key)}"
        )
