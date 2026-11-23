"""Guard: no page of the built docs site grows past a size ceiling (#820).

The API reference used to be one page. By the 0.371.0 release its HTML was
76 676 573 bytes, and the ``make docs-deploy`` push to ``gh-pages`` warned::

    remote: warning: File reference/index.html is 73.12 MB; this is larger
    than GitHub's recommended maximum file size of 50.00 MB

GitHub refuses files above 100 MB, so the page would have kept growing until
a release failed at the push, with nothing in ``make check`` saying so. The
reference is now split by theme (``docs/reference/``); this guard keeps every
page under :data:`MAX_PAGE_BYTES`, so the next page that grows past it fails
here, while there is still room before GitHub's limits.

The guard proves it fires: :func:`test_guard_fires_on_an_oversized_page`
runs the same check against a directory holding a page one byte over a
small ceiling.
"""

from __future__ import annotations

from pathlib import Path

import pytest

MAX_PAGE_BYTES: int = 35_000_000
"""Ceiling for one built HTML page.

Below GitHub's 50 MB warning with room to grow: after the split the largest
page (the top-level surface) measured 22 676 477 bytes.
"""


def oversized_pages(site: Path, limit: int) -> list[tuple[str, int]]:
    """List the HTML pages of ``site`` larger than ``limit`` bytes.

    Args:
        site (Path): A built ``site/`` directory.
        limit (int): Largest size accepted, in bytes.

    Returns:
        list[tuple[str, int]]: Path relative to ``site`` and size, largest
        first; empty when every page fits.
    """
    found = [
        (str(page.relative_to(site)), page.stat().st_size)
        for page in site.rglob("*.html")
        if page.stat().st_size > limit
    ]
    return sorted(found, key=lambda item: item[1], reverse=True)


@pytest.mark.docs
def test_no_built_page_exceeds_the_ceiling(built_site: Path) -> None:
    """Every page of the built site fits under :data:`MAX_PAGE_BYTES`.

    Args:
        built_site (Path): The built ``site/``, from ``conftest``.
    """
    pages = oversized_pages(built_site, MAX_PAGE_BYTES)
    assert not pages, (
        f"built pages over {MAX_PAGE_BYTES} bytes: {pages}. Split the page "
        f"(the reference is one file per theme under docs/reference/)."
    )


@pytest.mark.docs
def test_reference_is_split(built_site: Path) -> None:
    """The reference is several pages, not one.

    Args:
        built_site (Path): The built ``site/``, from ``conftest``.
    """
    pages = sorted((built_site / "reference").rglob("index.html"))
    assert len(pages) > 2, [str(page) for page in pages]


def test_guard_fires_on_an_oversized_page(tmp_path: Path) -> None:
    """A page one byte over the ceiling is reported; one at it is not.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
    """
    (tmp_path / "reference").mkdir()
    (tmp_path / "reference" / "index.html").write_bytes(b"x" * 101)
    (tmp_path / "index.html").write_bytes(b"x" * 100)
    assert oversized_pages(tmp_path, 100) == [("reference/index.html", 101)]
