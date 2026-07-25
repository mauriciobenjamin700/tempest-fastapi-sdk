"""Guard that ``llms.txt`` keeps covering the whole site.

The index is emitted by ``mkdocs_hooks/llmstxt.py`` for LLM consumers per the
https://llmstxt.org convention. It used to be built from a hard-coded page
list, which drifted badly: 25 of 73 nav pages were missing, meaning every
feature shipped after the list was written (generative AI, geolocation, chat,
reviews, vision, SSR, the OpenAPI tooling) was invisible to a model reading
the site — while still being perfectly visible on the site itself.

The hook now derives its sections from the MkDocs ``nav``. These tests assert
that derivation actually holds end to end, against the built file.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


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


@pytest.fixture(scope="session")
def built_site() -> Path:
    """Build the docs once and return the site directory.

    Returns:
        Path: The built ``site/`` directory.
    """
    if shutil.which("uv") is None:  # pragma: no cover - environment-dependent
        pytest.skip("uv is required to build the documentation")
    site = ROOT / "site"
    if not (site / "llms.txt").exists():
        completed = subprocess.run(
            ["uv", "run", "--group", "docs", "mkdocs", "build", "--strict", "-q"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:  # pragma: no cover - build failure
            pytest.fail(f"mkdocs build failed:\n{completed.stdout}{completed.stderr}")
    if not (site / "llms.txt").exists():  # pragma: no cover - hook disabled
        pytest.skip("llms.txt was not emitted")
    return site


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
        stem = page.removesuffix(".md")
        if stem == "index":
            continue
        stem = stem.removesuffix("/index")
        if f"/{stem}/" not in llms:
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
