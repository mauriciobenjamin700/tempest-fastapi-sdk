"""Guard the project rule: the documentation stays organized and in order.

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

On top of ordering, the structural half of the rule is asserted too, because
each of these has already drifted at least once:

* every page exists in **both languages** (``docs/<page>.md`` +
  ``docs/<page>.en.md``) — a missing mirror silently falls back to the other
  language on the built site;
* every page file is **reachable from its language's nav** — a page nobody
  links is a page nobody reads (MkDocs warns for the default nav; the EN nav
  is ours to check);
* every recipe in the nav is **listed on the Recipes landing** — twelve
  recipes were missing from that table when it was last audited.

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


def _page_paths(docs: Path) -> list[Path]:
    """Return every default-language page file on disk, in path order.

    ``docs/CLAUDE.md`` is excluded: it lives in the docs tree so Claude Code
    loads it when a file there is opened, but it is agent instruction rather
    than a page — ``mkdocs.yml`` drops it from the build via ``exclude_docs``,
    so it deliberately has no ``.en.md`` mirror and no ``nav`` entry.

    Args:
        docs (Path): The ``docs/`` directory.

    Returns:
        list[Path]: Every ``*.md`` that is a real page in the default language.
    """
    return [
        path
        for path in sorted(docs.rglob("*.md"))
        if not path.name.endswith(".en.md") and path.name != "CLAUDE.md"
    ]


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


def _recipe_pages() -> list[str]:
    """Return every recipe page in the default language.

    Returns:
        list[str]: Source-relative paths, sorted.
    """
    return sorted(
        f"recipes/{path.name}"
        for path in (ROOT / "docs" / "recipes").glob("*.md")
        if not path.name.endswith(".en.md")
    )


def _table_targets(page: str) -> set[str]:
    """Return the pages a landing table links to.

    Args:
        page (str): The landing page, relative to ``docs/``.

    Returns:
        set[str]: Link targets normalized to ``docs/``-relative paths.
    """
    text = (ROOT / "docs" / page).read_text(encoding="utf-8")
    targets: set[str] = set()
    for row in text.splitlines():
        if not row.startswith("| **["):
            continue
        for match in re.finditer(r"\]\(([^)]+\.md)\)", row):
            target = match.group(1)
            if target.startswith("../"):
                targets.add(target[3:])
            else:
                targets.add(f"recipes/{target}")
    return targets


class TestBilingualMirrors:
    def test_every_page_has_an_english_mirror(self) -> None:
        docs = ROOT / "docs"
        missing = sorted(
            str(path.relative_to(docs))
            for path in _page_paths(docs)
            if not path.with_name(f"{path.stem}.en.md").exists()
        )
        assert not missing, f"pages with no .en.md mirror: {missing}"

    def test_no_orphan_english_page(self) -> None:
        docs = ROOT / "docs"
        orphans = sorted(
            str(path.relative_to(docs))
            for path in docs.rglob("*.en.md")
            if not path.with_name(f"{path.name.removesuffix('.en.md')}.md").exists()
        )
        assert not orphans, f".en.md pages with no default-language page: {orphans}"


class TestEveryPageIsReachable:
    def test_default_nav_lists_every_page(self, pt_nav: list[Any]) -> None:
        pages: list[str] = []
        _pages(pt_nav, pages)
        docs = ROOT / "docs"
        on_disk = {str(path.relative_to(docs)) for path in _page_paths(docs)}
        assert not on_disk - set(pages), (
            f"pages absent from the PT nav: {sorted(on_disk - set(pages))}"
        )

    def test_english_nav_lists_every_page(self, en_nav: list[Any]) -> None:
        pages: list[str] = []
        _pages(en_nav, pages)
        docs = ROOT / "docs"
        on_disk = {str(path.relative_to(docs)) for path in _page_paths(docs)}
        assert not on_disk - set(pages), (
            f"pages absent from the EN nav: {sorted(on_disk - set(pages))}"
        )


class TestRecipeIndexCoverage:
    @pytest.mark.parametrize("landing", ["recipes/index.md", "recipes/index.en.md"])
    def test_landing_lists_every_recipe(self, landing: str, pt_nav: list[Any]) -> None:
        nav_pages: list[str] = []
        _pages(pt_nav, nav_pages)
        recipes = {
            p for p in nav_pages if p.startswith("recipes/") and p != "recipes/index.md"
        }
        listed = _table_targets(landing)
        missing = sorted(recipes - listed)
        assert not missing, f"{landing} does not list: {missing}"

    def test_every_recipe_file_is_in_the_nav(self, pt_nav: list[Any]) -> None:
        nav_pages: list[str] = []
        _pages(pt_nav, nav_pages)
        missing = sorted(set(_recipe_pages()) - set(nav_pages))
        assert not missing, f"recipe files absent from the nav: {missing}"
