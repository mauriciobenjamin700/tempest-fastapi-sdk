"""Guard that ``llms.txt`` keeps covering the whole site.

The index is emitted by ``mkdocs_hooks/llmstxt.py`` for LLM consumers per the
https://llmstxt.org convention. It used to be built from a hard-coded page
list, which drifted badly: 25 of 73 nav pages were missing, meaning every
feature shipped after the list was written (generative AI, geolocation, chat,
reviews, vision, SSR, the OpenAPI tooling) was invisible to a model reading
the site — while still being perfectly visible on the site itself.

The hook now derives its sections from the MkDocs ``nav``. These tests assert
that derivation actually holds end to end, against the built file — and, since
#876, that every link carries a description and points at a ``.md`` the site
serves, and that ``llms-core.txt`` stays under :data:`CORE_MAX_BYTES`. Measured
on the 0.374.0 build (2026-11-27): 321 links, none with a description, none to
a ``.md``, and only a 5.6 MB ``llms-full.txt`` for a model to read.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent

CORE_MAX_BYTES: int = 400_000
"""Ceiling of ``llms-core.txt``: about 100k tokens at four bytes per token."""

_LINK_RE: re.Pattern[str] = re.compile(r"^- \[[^\]]+\]\((\S+)\)(.*)$")


def site_url() -> str:
    """Return ``site_url`` from ``mkdocs.yml``, without the trailing slash.

    Returns:
        str: The published root of the site.
    """
    text = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    match = re.search(r"^site_url:\s*(\S+)\s*$", text, re.M)
    assert match, "mkdocs.yml declares no site_url"
    return match.group(1).rstrip("/")


def links_without_description(llms: str) -> list[str]:
    """Return the ``llms.txt`` links that carry no description.

    Args:
        llms (str): Content of ``llms.txt``.

    Returns:
        list[str]: Every ``- [title](url)`` line not followed by
        ``: <description>``.
    """
    bad: list[str] = []
    for line in llms.splitlines():
        match = _LINK_RE.match(line)
        if match and not re.match(r"^: \S", match.group(2)):
            bad.append(line)
    return bad


def links_not_served_as_md(llms: str, site: Path, root_url: str) -> list[str]:
    """Return the ``llms.txt`` links that do not reach a built ``.md``.

    Args:
        llms (str): Content of ``llms.txt``.
        site (Path): The built site directory.
        root_url (str): ``site_url`` without the trailing slash.

    Returns:
        list[str]: Every link URL that is outside ``root_url``, does not end
        in ``.md``, or names a file the build did not write.
    """
    bad: list[str] = []
    for line in llms.splitlines():
        match = _LINK_RE.match(line)
        if match is None:
            continue
        url = match.group(1)
        rel = url.removeprefix(f"{root_url}/")
        if rel == url or not rel.endswith(".md") or not (site / rel).is_file():
            bad.append(url)
    return bad


def _nav_page_paths(nav: object, out: list[str]) -> None:
    """Collect every ``.md`` path reachable from a MkDocs nav fragment.

    Args:
        nav (object): A nav entry — a path string, a mapping, or a list.
        out (list[str]): Accumulator, appended in place.
    """
    if isinstance(nav, str):
        if nav.endswith(".md"):
            out.append(nav)
    elif isinstance(nav, dict):
        for value in nav.values():
            _nav_page_paths(value, out)
    elif isinstance(nav, list):
        for item in nav:
            _nav_page_paths(item, out)


@pytest.fixture(autouse=True)
def _require_llms_index(built_site: Path) -> None:
    """Skip the module when the hook that emits the index is disabled.

    The ``built_site`` fixture itself lives in ``conftest`` and only builds;
    whether the index exists is this module's precondition, not the build's.

    Args:
        built_site (Path): The built ``site/`` directory, from ``conftest``.
    """
    if not (built_site / "llms.txt").exists():  # pragma: no cover - hook off
        pytest.skip("llms.txt was not emitted")


@pytest.fixture(scope="session")
def nav_pages() -> list[str]:
    """Return every page path listed in the MkDocs nav.

    Returns:
        list[str]: Source-relative ``.md`` paths, in nav order.

    Raises:
        AssertionError: If ``mkdocs.yml`` declares no nav.
    """
    raw = (ROOT / "mkdocs.yml").read_text(encoding="utf-8").splitlines()
    # Only the `nav:` block is parsed. Loading the whole config would need
    # MkDocs' own loader: it carries `!!python/name:` tags and regex values
    # that PyYAML's SafeLoader rejects. The nav itself is plain YAML.
    try:
        start = next(i for i, line in enumerate(raw) if line.rstrip() == "nav:")
    except StopIteration:  # pragma: no cover - nav is required
        pytest.fail("mkdocs.yml declares no nav")
    block = [raw[start]]
    for line in raw[start + 1 :]:
        if line and not line[0].isspace():
            break
        block.append(line)
    nav = yaml.safe_load("\n".join(block))["nav"]
    assert nav, "mkdocs.yml declares an empty nav"
    pages: list[str] = []
    _nav_page_paths(nav, pages)
    return pages


@pytest.mark.docs
def test_every_nav_page_is_listed(built_site: Path, nav_pages: list[str]) -> None:
    """No page on the site is missing from the LLM index.

    Args:
        built_site (Path): The built site directory.
        nav_pages (list[str]): Page paths from the nav.
    """
    llms = (built_site / "llms.txt").read_text(encoding="utf-8")
    missing: list[str] = []
    for page in nav_pages:
        if f"/{page})" not in llms:
            missing.append(page)
    assert not missing, (
        f"llms.txt does not link these nav pages: {missing}. The hook derives "
        f"its sections from the nav, so a gap here means the derivation broke."
    )


@pytest.mark.docs
def test_index_has_title_and_summary(built_site: Path) -> None:
    """The index opens with the ``# title`` + ``> summary`` llmstxt shape.

    Args:
        built_site (Path): The built site directory.
    """
    lines = (built_site / "llms.txt").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# "), lines[0]
    assert any(line.startswith("> ") for line in lines[:5]), lines[:5]


@pytest.mark.docs
def test_summary_lists_the_real_extras(built_site: Path) -> None:
    """The advertised extras match ``[project.optional-dependencies]``.

    The previous hard-coded summary named ten extras when the package shipped
    more than twenty, telling a model that shipped features did not exist.

    Args:
        built_site (Path): The built site directory.
    """
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = pyproject.split("[project.optional-dependencies]", 1)[1]
    declared: list[str] = []
    for line in block.splitlines():
        if line.startswith("["):
            break
        match = re.match(r"^([A-Za-z0-9_-]+)\s*=\s*\[", line)
        if match:
            declared.append(match.group(1))
    summary = (built_site / "llms.txt").read_text(encoding="utf-8").split("\n## ", 1)[0]
    missing = [name for name in declared if f"[{name}]" not in summary]
    assert not missing, f"llms.txt summary omits these extras: {missing}"


@pytest.mark.docs
def test_full_text_carries_page_bodies(built_site: Path) -> None:
    """``llms-full.txt`` inlines content, not just links.

    Args:
        built_site (Path): The built site directory.
    """
    full = (built_site / "llms-full.txt").read_text(encoding="utf-8")
    assert len(full) > 100_000, len(full)
    # Only a line *starting* with ``:::`` is a directive — which is the rule
    # the hook itself applies. Prose may mention ``:::`` inline (the changelog
    # does), and that is not something to neutralize.
    directives = [line for line in full.splitlines() if line.lstrip().startswith(":::")]
    assert not directives, f"unresolved mkdocstrings directives: {directives[:5]}"


@pytest.mark.docs
def test_every_link_has_a_description(built_site: Path) -> None:
    """Every ``llms.txt`` link reads ``- [title](url): description``.

    Args:
        built_site (Path): The built site directory.
    """
    llms = (built_site / "llms.txt").read_text(encoding="utf-8")
    bad = links_without_description(llms)
    assert not bad, f"links without a description: {bad[:5]}"
    assert sum(1 for line in llms.splitlines() if _LINK_RE.match(line)) > 100


@pytest.mark.docs
def test_every_link_points_to_a_built_md(built_site: Path) -> None:
    """Every ``llms.txt`` link is a ``.md`` the built site serves.

    Args:
        built_site (Path): The built site directory.
    """
    llms = (built_site / "llms.txt").read_text(encoding="utf-8")
    bad = links_not_served_as_md(llms, built_site, site_url())
    assert not bad, f"links that are not a built .md: {bad[:5]}"


def test_link_guards_fire_on_the_old_format(tmp_path: Path) -> None:
    """Both link checks reject the 0.374.0 line: title only, HTML URL.

    Args:
        tmp_path (Path): Scratch site directory.
    """
    root = site_url()
    old = f"## Architecture\n\n- [Arquitetura]({root}/architecture/)\n"
    assert links_without_description(old) == [f"- [Arquitetura]({root}/architecture/)"]
    assert links_not_served_as_md(old, tmp_path, root) == [f"{root}/architecture/"]
    missing = f"- [Arquitetura]({root}/en/architecture.md): Use to X.\n"
    assert links_without_description(missing) == []
    assert links_not_served_as_md(missing, tmp_path, root) == [
        f"{root}/en/architecture.md"
    ]
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "architecture.md").write_text("# A\n", encoding="utf-8")
    assert links_not_served_as_md(missing, tmp_path, root) == []
    empty = f"- [Arquitetura]({root}/en/architecture.md): \n"
    assert links_without_description(empty) == [empty.rstrip("\n")]


def core_over_ceiling(size: int) -> bool:
    """Tell whether ``llms-core.txt`` of ``size`` bytes breaks the ceiling.

    Args:
        size (int): File size in bytes.

    Returns:
        bool: ``True`` above :data:`CORE_MAX_BYTES`.
    """
    return size > CORE_MAX_BYTES


@pytest.mark.docs
def test_core_is_under_the_ceiling(built_site: Path) -> None:
    """``llms-core.txt`` exists, holds the tutorial and fits the ceiling.

    Args:
        built_site (Path): The built site directory.
    """
    core = built_site / "llms-core.txt"
    size = core.stat().st_size
    assert not core_over_ceiling(size), f"llms-core.txt has {size} bytes"
    text = core.read_text(encoding="utf-8")
    assert "Source: " + site_url() + "/en/tutorial.md" in text
    assert "# Recipe index" in text


def test_core_ceiling_fires_on_the_full_text() -> None:
    """The ceiling rejects the 0.374.0 ``llms-full.txt``, the only bundle then."""
    assert core_over_ceiling(5_448_786)
    assert not core_over_ceiling(CORE_MAX_BYTES)


_DIRECTIVE_RE: re.Pattern[str] = re.compile(
    r'^(:::\s|\{%\s*include-markdown\s+")', re.M
)


def served_with_directives(text: str) -> bool:
    """Tell whether a served ``.md`` still carries build-time syntax.

    Args:
        text (str): Content of the served file.

    Returns:
        bool: ``True`` when it opens with front matter, holds the
        ``reference/top`` marker, or has a ``:::`` / ``include-markdown``
        directive at the start of a line.
    """
    return (
        text.startswith("---\n")
        or "<!-- reference-top-index -->" in text
        or _DIRECTIVE_RE.search(text) is not None
    )


def test_directive_check_fires_on_the_sources() -> None:
    """The check rejects the source of the pages the hook has to resolve.

    The front matter is cut first, so each source is rejected for the
    directive in its body, not only for opening with ``---``.
    """
    docs = ROOT / "docs"
    for rel in ("changelog.md", "reference/top.md", "reference/admin-data-api/db.md"):
        source = (docs / rel).read_text(encoding="utf-8")
        assert served_with_directives(source), rel
        assert served_with_directives(source.split("---\n", 2)[2]), rel
    assert served_with_directives("# Top\n\n<!-- reference-top-index -->\n")
    assert not served_with_directives("# Changelog\n\nUses `:::` inline.\n")


@pytest.mark.docs
def test_per_page_markdown_is_resolved(built_site: Path) -> None:
    """The served ``.md`` files carry content, not build directives.

    Args:
        built_site (Path): The built site directory.
    """
    for rel in ("recipes/genai.md", "en/recipes/genai.md", "en/recipes/genai/rag.md"):
        assert (built_site / rel).is_file(), rel
    unresolved = [
        page.relative_to(built_site).as_posix()
        for page in built_site.rglob("*.md")
        if served_with_directives(page.read_text(encoding="utf-8"))
    ]
    assert len(list(built_site.rglob("*.md"))) > 600
    assert not unresolved, f"pages served with build directives: {unresolved[:5]}"
    changelog = (built_site / "en" / "changelog.md").read_text(encoding="utf-8")
    assert changelog.startswith("# Changelog"), changelog[:80]
    top = (built_site / "reference" / "top.md").read_text(encoding="utf-8")
    assert f"]({site_url()}/reference/admin-data-api/admin/#" in top


@pytest.mark.docs
def test_marker_never_reaches_the_bundles(built_site: Path) -> None:
    """The ``reference/top`` marker is not served raw (it was, in 0.374.0).

    Args:
        built_site (Path): The built site directory.
    """
    for name in ("llms.txt", "llms-core.txt", "llms-full.txt"):
        text = (built_site / name).read_text(encoding="utf-8")
        assert "<!-- reference-top-index -->" not in text, name


def english_nav() -> list[object]:
    """Return the ``nav`` the ``i18n`` plugin declares for the ``en`` locale.

    Only that block is parsed, for the reason :func:`nav_pages` gives: the
    whole config carries tags PyYAML's SafeLoader rejects. The block is the
    indented ``nav:`` that follows ``- locale: en``.

    Returns:
        list[object]: The English nav.
    """
    raw = (ROOT / "mkdocs.yml").read_text(encoding="utf-8").splitlines()
    locale = next(i for i, line in enumerate(raw) if line.strip() == "- locale: en")
    start = next(i for i in range(locale, len(raw)) if raw[i].strip() == "nav:")
    indent = len(raw[start]) - len(raw[start].lstrip())
    block: list[str] = []
    for line in raw[start + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        block.append(line[indent:])
    nav: list[object] = yaml.safe_load("\n".join(block))
    assert nav, "the en locale declares an empty nav"
    return nav


def nav_top_sections(nav: list[object]) -> list[tuple[str, list[str]]]:
    """Group the nav pages by the top-level entry that holds them.

    Args:
        nav (list[object]): A MkDocs nav.

    Returns:
        list[tuple[str, list[str]]]: ``(label, pages)`` per titled top-level
        entry, in nav order.
    """
    sections: list[tuple[str, list[str]]] = []
    for item in nav:
        if isinstance(item, dict):
            for title, value in item.items():
                pages: list[str] = []
                _nav_page_paths(value, pages)
                if pages:
                    sections.append((str(title), pages))
    return sections


def llms_sections(llms: str, root_url: str) -> list[tuple[str, list[str]]]:
    """Read the ``## section`` blocks of ``llms.txt`` back as source paths.

    Args:
        llms (str): Content of ``llms.txt``.
        root_url (str): ``site_url`` without the trailing slash.

    Returns:
        list[tuple[str, list[str]]]: ``(section, pages)`` in file order, each
        link turned back into its default-locale source path.
    """
    sections: list[tuple[str, list[str]]] = []
    for line in llms.splitlines():
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
            continue
        match = _LINK_RE.match(line)
        if match and sections:
            rel = match.group(1).removeprefix(f"{root_url}/").removeprefix("en/")
            sections[-1][1].append(rel)
    return sections


def section_mismatches(llms: str, nav: list[object], root_url: str) -> list[str]:
    """Compare the sections of ``llms.txt`` with the top level of the nav.

    Args:
        llms (str): Content of ``llms.txt``.
        nav (list[object]): The English nav.
        root_url (str): ``site_url`` without the trailing slash.

    Returns:
        list[str]: One message per difference: section labels or order that
        do not match the nav, and each page listed under a section other
        than the top-level nav entry that holds it.
    """
    expected = nav_top_sections(nav)
    found = llms_sections(llms, root_url)
    problems: list[str] = []
    expected_titles = [title for title, _ in expected]
    found_titles = [title for title, _ in found]
    if expected_titles != found_titles:
        problems.append(f"sections {found_titles} != nav {expected_titles}")
    home = {page: title for title, pages in expected for page in pages}
    for title, pages in found:
        problems.extend(
            f"{page} under '{title}', nav puts it under '{home.get(page)}'"
            for page in pages
            if home.get(page) != title
        )
    return problems


@pytest.mark.docs
def test_every_page_sits_in_its_nav_section(built_site: Path) -> None:
    """Each ``llms.txt`` section is a top-level entry of the English nav.

    Until this check the hook pooled every single-page top-level entry into
    one ``Overview`` block at the head of the file, so ``Tutorial``,
    ``Roadmap``, ``Contributing`` and ``Changelog`` were listed before
    ``Installation``, out of the order the nav teaches.

    Args:
        built_site (Path): The built site directory.
    """
    llms = (built_site / "llms.txt").read_text(encoding="utf-8")
    problems = section_mismatches(llms, english_nav(), site_url())
    assert not problems, problems[:10]


def _bullet(src: str) -> str:
    """Format one ``llms.txt`` bullet for ``src``, as the hook writes it.

    Args:
        src (str): Source path.

    Returns:
        str: The bullet line.
    """
    return f"- [T]({site_url()}/en/{src}): Use to X.\n"


def test_section_check_fires_on_misplaced_pages() -> None:
    """The check rejects the pooled ``Overview`` and a page in a wrong section."""
    root = site_url()
    nav: list[object] = [
        {"Home": "index.md"},
        {"Installation": ["installation.md"]},
        {"Architecture": ["architecture.md"]},
        {"Tutorial": "tutorial.md"},
        {"Recipes": ["recipes/index.md", {"GenAI": ["recipes/genai.md"]}]},
    ]
    pooled = (
        f"## Overview\n\n{_bullet('index.md')}{_bullet('tutorial.md')}"
        f"## Installation\n\n{_bullet('installation.md')}"
        f"## Architecture\n\n{_bullet('architecture.md')}"
        f"## Recipes\n\n{_bullet('recipes/index.md')}{_bullet('recipes/genai.md')}"
    )
    assert any(
        "Overview" in problem for problem in section_mismatches(pooled, nav, root)
    )
    misplaced = (
        f"## Home\n\n{_bullet('index.md')}"
        f"## Installation\n\n{_bullet('installation.md')}"
        f"## Architecture\n\n{_bullet('architecture.md')}{_bullet('recipes/genai.md')}"
        f"## Tutorial\n\n{_bullet('tutorial.md')}"
        f"## Recipes\n\n{_bullet('recipes/index.md')}"
    )
    assert section_mismatches(misplaced, nav, root) == [
        "recipes/genai.md under 'Architecture', nav puts it under 'Recipes'"
    ]
    right = (
        f"## Home\n\n{_bullet('index.md')}"
        f"## Installation\n\n{_bullet('installation.md')}"
        f"## Architecture\n\n{_bullet('architecture.md')}"
        f"## Tutorial\n\n{_bullet('tutorial.md')}"
        f"## Recipes\n\n{_bullet('recipes/index.md')}{_bullet('recipes/genai.md')}"
    )
    assert section_mismatches(right, nav, root) == []
