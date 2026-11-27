"""Guard: no page of the built docs site grows past a size ceiling (#820).

The API reference used to be one page. By the 0.371.0 release its HTML was
76 676 573 bytes, and the ``make docs-deploy`` push to ``gh-pages`` warned::

    remote: warning: File reference/index.html is 73.12 MB; this is larger
    than GitHub's recommended maximum file size of 50.00 MB

GitHub refuses files above 100 MB, so the page would have kept growing until
a release failed at the push, with nothing in ``make check`` saying so.

The split by theme (#820) still left pages of 8 to 22 MB, too big for a
browser to search comfortably or for an agent to fetch. Since #878 the
reference is one page per module (``docs/reference/<theme>/<module>.md``),
renders without the source listing, and the top-level page links to the
module pages instead of rendering every root export again. Two ceilings
keep it there: :data:`MAX_REFERENCE_PAGE_BYTES` for the reference and
:data:`MAX_PAGE_BYTES` for every other page.

The guard proves it fires: :func:`test_guard_fires_on_an_oversized_page`
runs the same check against a directory holding a page one byte over a
small ceiling, and :func:`test_reference_ceiling_fires_on_a_reference_page`
with reference pages one byte over :data:`MAX_REFERENCE_PAGE_BYTES` (in both
locales) next to a changelog of the same size, which still fits.
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAX_REFERENCE_PAGE_BYTES: int = 3_000_000
"""Ceiling for one built page under ``reference/``.

The per-theme split (#820) shipped ``reference/top/index.html`` at
22 819 038 bytes in the 0.374.0 build. After the per-module split (#878) the
largest reference page, ``reference/ai-vision/genai/index.html``, measured
1 843 002 bytes on 2026-11-27.
"""

MAX_PAGE_BYTES: int = 5_000_000
"""Ceiling for any other built HTML page.

The largest one is the changelog: ``changelog/index.html`` measured
2 196 516 bytes on 2026-11-27, and ``CHANGELOG.md`` grew from 1 139 705 to
1 351 538 bytes over the month before, so this leaves it room for months,
not years. When it trips, split the changelog by minor series instead of
raising the ceiling.
"""


def is_reference_page(relative: Path) -> bool:
    """Tell whether a built page belongs to the API reference.

    Args:
        relative (Path): Page path relative to ``site/``.

    Returns:
        bool: ``True`` for ``reference/...`` and ``en/reference/...``.
    """
    parts = relative.parts
    return parts[:1] == ("reference",) or parts[:2] == ("en", "reference")


def oversized_pages(
    site: Path, limit: int, reference_limit: int | None = None
) -> list[tuple[str, int]]:
    """List the HTML pages of ``site`` larger than their ceiling.

    Args:
        site (Path): A built ``site/`` directory.
        limit (int): Largest size accepted for a page, in bytes.
        reference_limit (int | None): Largest size accepted for a reference
            page; ``None`` applies ``limit`` to every page.

    Returns:
        list[tuple[str, int]]: Path relative to ``site`` and size, largest
        first; empty when every page fits.
    """
    found: list[tuple[str, int]] = []
    for page in site.rglob("*.html"):
        relative = page.relative_to(site)
        size = page.stat().st_size
        ceiling = limit
        if reference_limit is not None and is_reference_page(relative):
            ceiling = reference_limit
        if size > ceiling:
            found.append((relative.as_posix(), size))
    return sorted(found, key=lambda item: item[1], reverse=True)


@pytest.mark.docs
def test_no_built_page_exceeds_the_ceiling(built_site: Path) -> None:
    """Every built page fits under its ceiling.

    Args:
        built_site (Path): The built ``site/``, from ``conftest``.
    """
    pages = oversized_pages(built_site, MAX_PAGE_BYTES, MAX_REFERENCE_PAGE_BYTES)
    assert not pages, (
        f"built pages over the ceiling ({MAX_REFERENCE_PAGE_BYTES} bytes for "
        f"reference/, {MAX_PAGE_BYTES} for the rest): {pages}. Split the page "
        f"(the reference is one file per module under docs/reference/<theme>/)."
    )


@pytest.mark.docs
def test_reference_is_split(built_site: Path) -> None:
    """The reference is one page per module, not one per theme.

    Args:
        built_site (Path): The built ``site/``, from ``conftest``.
    """
    pages = sorted((built_site / "reference").glob("*/*/index.html"))
    assert len(pages) > 50, [str(page) for page in pages]


def test_guard_fires_on_an_oversized_page(tmp_path: Path) -> None:
    """A page one byte over the ceiling is reported; one at it is not.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
    """
    (tmp_path / "reference").mkdir()
    (tmp_path / "reference" / "index.html").write_bytes(b"x" * 101)
    (tmp_path / "index.html").write_bytes(b"x" * 100)
    assert oversized_pages(tmp_path, 100) == [("reference/index.html", 101)]


def test_reference_ceiling_fires_on_a_reference_page(tmp_path: Path) -> None:
    """A reference page over its ceiling fails; the same size elsewhere fits.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
    """
    for relative in ("reference/top/index.html", "en/reference/x/index.html"):
        (tmp_path / relative).parent.mkdir(parents=True)
        (tmp_path / relative).write_bytes(b"x" * (MAX_REFERENCE_PAGE_BYTES + 1))
    (tmp_path / "changelog").mkdir()
    (tmp_path / "changelog" / "index.html").write_bytes(
        b"x" * (MAX_REFERENCE_PAGE_BYTES + 1)
    )
    found = oversized_pages(tmp_path, MAX_PAGE_BYTES, MAX_REFERENCE_PAGE_BYTES)
    assert sorted(path for path, _ in found) == [
        "en/reference/x/index.html",
        "reference/top/index.html",
    ]
