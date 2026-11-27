"""Guard: no documentation page grows past a line ceiling (#877).

Six pages had grown into references nobody could read in one sitting. On
2026-11-27 (``main`` at 0.374.0) ``docs/recipes/genai.md`` had 4 657 lines,
``docs/migration.md`` 3 461, ``docs/recipes/auth-flow.md`` 3 457,
``docs/recipes/admin.md`` 3 409, ``docs/recipes/database.md`` 3 097 and
``docs/recipes/cli.md`` 3 058. A human scrolled without finding the
section; an agent that fetched the page (it reads them through ``llms.txt``)
spent 40 to 48 thousand tokens of context to reach one section. #877 split
those six, plus ``http``, ``modelops`` and ``queue-tasks``, into a short
index and one subpage per topic under ``docs/<page>/``.

:data:`MAX_LINES` is the ceiling for every page, in both languages. Pages
that were already above it and have not been split yet sit in
:data:`ALLOWED_LONG_PAGES`, each with its own ceiling — the size it had,
rounded up to the next 50 lines — and the reason it is there, so they can be
edited but cannot keep growing. An entry for a page that is back under
:data:`MAX_LINES`, or that no longer exists, fails as stale.

The guard proves it fires: :func:`test_guard_fires_on_a_page_over_the_ceiling`
runs the same check against a directory holding a page one line over the
ceiling (and its English mirror), and
:func:`test_allowlist_ceiling_still_fires` shows an allowlisted page that
grows past its own ceiling is reported too.
"""

from __future__ import annotations

from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[1]

MAX_LINES: int = 800
"""Ceiling, in lines, for one page of ``docs/`` (either language).

The largest page the #877 split produced is ``docs/migration/0-30x.en.md``
(722 lines on 2026-11-27); the largest recipe subpage is
``docs/recipes/auth-flow/endpoints.en.md`` (689).
"""

_NOT_SPLIT_YET: str = "receita ainda não partida em subpáginas; próxima leva da #877"

ALLOWED_LONG_PAGES: dict[str, tuple[int, str]] = {
    "architecture.md": (
        1200,
        "guia de arquitetura lido de ponta a ponta; partir por camada",
    ),
    "recipes/agents-advanced.md": (1050, _NOT_SPLIT_YET),
    "recipes/agents-prompts.md": (900, _NOT_SPLIT_YET),
    "recipes/agents-tools.md": (900, _NOT_SPLIT_YET),
    "recipes/agents.md": (1150, _NOT_SPLIT_YET),
    "recipes/chat.md": (850, _NOT_SPLIT_YET),
    "recipes/fleet-traffic.md": (850, _NOT_SPLIT_YET),
    "recipes/genai-hosted.md": (850, _NOT_SPLIT_YET),
    "recipes/geo.md": (1250, _NOT_SPLIT_YET),
    "recipes/jobs.md": (850, _NOT_SPLIT_YET),
    "recipes/logging.md": (1250, _NOT_SPLIT_YET),
    "recipes/mercado-pago-webhooks.md": (1500, _NOT_SPLIT_YET),
    "recipes/mfa.md": (950, _NOT_SPLIT_YET),
    "recipes/oauth.md": (1000, _NOT_SPLIT_YET),
    "recipes/openapi-errors.md": (1000, _NOT_SPLIT_YET),
    "recipes/openpix.md": (900, _NOT_SPLIT_YET),
    "recipes/pdf.md": (950, _NOT_SPLIT_YET),
    "recipes/pix-protocol.md": (1450, _NOT_SPLIT_YET),
    "recipes/spreadsheets.md": (1350, _NOT_SPLIT_YET),
    "recipes/sse.md": (1050, _NOT_SPLIT_YET),
    "recipes/storage.md": (1200, _NOT_SPLIT_YET),
    "recipes/testing.md": (850, _NOT_SPLIT_YET),
    "recipes/wallet.md": (850, _NOT_SPLIT_YET),
    "recipes/zap-inbound.md": (1150, _NOT_SPLIT_YET),
    "service-standard.md": (
        850,
        "padrão de serviço lido de ponta a ponta; partir por camada",
    ),
    "ssr.md": (900, "página de SSR ainda não partida; próxima leva da #877"),
    "tutorial.md": (
        1150,
        "tutorial linear, um passo depende do anterior; partir por etapa",
    ),
}
"""Pages allowed above :data:`MAX_LINES`, keyed by the PT path under ``docs/``.

Value: ``(ceiling, reason)``. The ceiling covers both ``X.md`` and
``X.en.md`` and is the larger of the two on 2026-11-27, rounded up to the
next 50 lines. Splitting a page removes its entry; raising a ceiling needs
the same justification as adding one.
"""


def _default_path(page: Path, docs: Path) -> str:
    """Return the PT-relative key of a page (``X.en.md`` maps to ``X.md``).

    Args:
        page (Path): A markdown file under ``docs``.
        docs (Path): The ``docs/`` directory.

    Returns:
        str: The path relative to ``docs`` in the default language.
    """
    relative = page.relative_to(docs).as_posix()
    if relative.endswith(".en.md"):
        return relative.removesuffix(".en.md") + ".md"
    return relative


def page_lines(docs: Path) -> dict[str, int]:
    """Count the lines of every page under ``docs``, per PT key.

    ``CLAUDE.md`` is agent instruction, not a page, and is skipped. Both
    languages share a key, which keeps the larger count.

    Args:
        docs (Path): The ``docs/`` directory.

    Returns:
        dict[str, int]: PT-relative path to the larger line count of the pair.
    """
    counts: dict[str, int] = {}
    for page in docs.rglob("*.md"):
        if page.name == "CLAUDE.md":
            continue
        key = _default_path(page, docs)
        lines = len(page.read_text(encoding="utf-8").splitlines())
        counts[key] = max(counts.get(key, 0), lines)
    return counts


def oversized_pages(
    docs: Path, limit: int, allowed: dict[str, tuple[int, str]]
) -> list[tuple[str, int, int]]:
    """List the pages above their ceiling.

    Args:
        docs (Path): The ``docs/`` directory.
        limit (int): The ceiling for a page not in ``allowed``.
        allowed (dict[str, tuple[int, str]]): Per-page ceilings and reasons.

    Returns:
        list[tuple[str, int, int]]: ``(page, lines, ceiling)``, largest
        first; empty when every page fits.
    """
    found: list[tuple[str, int, int]] = []
    for page, lines in page_lines(docs).items():
        ceiling = allowed[page][0] if page in allowed else limit
        if lines > ceiling:
            found.append((page, lines, ceiling))
    return sorted(found, key=lambda item: item[1], reverse=True)


def stale_entries(
    docs: Path, limit: int, allowed: dict[str, tuple[int, str]]
) -> list[str]:
    """List allowlist entries that no longer need to be there.

    Args:
        docs (Path): The ``docs/`` directory.
        limit (int): The general ceiling.
        allowed (dict[str, tuple[int, str]]): Per-page ceilings and reasons.

    Returns:
        list[str]: Pages that are gone or back under ``limit``.
    """
    counts = page_lines(docs)
    return sorted(page for page in allowed if counts.get(page, 0) <= limit)


def test_no_page_exceeds_its_ceiling() -> None:
    """Every page fits under :data:`MAX_LINES` or its allowlisted ceiling."""
    pages = oversized_pages(ROOT / "docs", MAX_LINES, ALLOWED_LONG_PAGES)
    assert not pages, (
        f"pages over the ceiling (page, lines, ceiling): {pages}. Split the "
        f"page into an index plus subpages under docs/<page>/ (see "
        f"docs/recipes/genai.md) instead of raising the ceiling."
    )


def test_allowlist_has_no_stale_entry() -> None:
    """An allowlisted page that shrank under the ceiling leaves the list."""
    stale = stale_entries(ROOT / "docs", MAX_LINES, ALLOWED_LONG_PAGES)
    assert not stale, f"remove from ALLOWED_LONG_PAGES (fits or is gone): {stale}"


def test_every_allowlist_entry_has_a_reason() -> None:
    """Each exception says why it exists."""
    missing = sorted(page for page, (_, why) in ALLOWED_LONG_PAGES.items() if not why)
    assert not missing, missing


def test_guard_fires_on_a_page_over_the_ceiling(tmp_path: Path) -> None:
    """A page one line over the ceiling is reported, in either language.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
    """
    (tmp_path / "recipes").mkdir()
    (tmp_path / "recipes" / "big.md").write_text("x\n" * 10, encoding="utf-8")
    (tmp_path / "recipes" / "big.en.md").write_text("x\n" * 11, encoding="utf-8")
    (tmp_path / "small.md").write_text("x\n" * 10, encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("x\n" * 99, encoding="utf-8")
    assert oversized_pages(tmp_path, 10, {}) == [("recipes/big.md", 11, 10)]


def test_allowlist_ceiling_still_fires(tmp_path: Path) -> None:
    """An allowlisted page fits under its own ceiling and fails above it.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
    """
    (tmp_path / "long.md").write_text("x\n" * 15, encoding="utf-8")
    assert oversized_pages(tmp_path, 10, {"long.md": (15, "why")}) == []
    assert oversized_pages(tmp_path, 10, {"long.md": (14, "why")}) == [
        ("long.md", 15, 14)
    ]
    assert stale_entries(tmp_path, 20, {"long.md": (30, "why")}) == ["long.md"]
