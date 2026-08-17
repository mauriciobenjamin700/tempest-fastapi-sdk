"""Keep the agent-facing instruction files honest about this repository.

The rules an agent reads live in several files now — the root ``CLAUDE.md``,
``LESSONS.md``, one ``CLAUDE.md`` per area (``tests/``, ``docs/``,
``tempest_fastapi_sdk/integrations/``), and the skill/agent definitions under
``.claude/``. None of them is a MkDocs page, so ``mkdocs build --strict`` never
looks at them, and ``test_docs_organization`` only knows about ``docs/``. That
leaves three things able to rot in silence:

1. **The guard roster.** ``tests/CLAUDE.md`` tables which guard covers what. A
   new ``test_*_guard.py`` that never reaches the table is invisible to the next
   reader, and a row naming a deleted guard promises a check that no longer
   runs. Prose drift of exactly this kind already shipped twice (the admin
   tiers, the genai roadmap).
2. **Cross-file pointers.** Splitting a 315-line file into pointers is only an
   improvement while the pointers resolve — including the ``#anchor`` half,
   which MkDocs itself downgrades to a mere ``INFO``.
3. **Paths and targets quoted as fact.** ``make docs-build``,
   ``scripts/regen_openpix.py``, ``tests/test_alias_guard.py``: an instruction
   naming something that no longer exists sends the reader down a dead end.

Every check is a helper that returns the problems it found, so the suite can
assert both that the real files are clean **and** that the helper fires on the
shape that would break — a guard that cannot fail is one nobody should trust.

What this cannot check is the *content* of the prose — whether a sentence
describing behavior is true. That stays a human / ``docs-prose-auditor`` job.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parents[1]

_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_CODE_RE = re.compile(r"`([^`\n]+)`")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
_MAKE_RE = re.compile(r"`make ([a-z][a-z-]*)")
_EXTERNAL = ("http://", "https://", "mailto:")
_PATH_PREFIXES = ("tests/", "scripts/", "docs/", "tempest_fastapi_sdk/", ".github/")
_PLACEHOLDER = ("<", ">", "*", "{", "}", "?", "…", "$")


def _agent_docs() -> list[pathlib.Path]:
    """Return every agent-facing Markdown file in the repository.

    Returns:
        list[pathlib.Path]: The instruction files that exist, in a stable order.
    """
    paths = [
        ROOT / "CLAUDE.md",
        ROOT / "LESSONS.md",
        ROOT / "tests" / "CLAUDE.md",
        ROOT / "docs" / "CLAUDE.md",
        ROOT / "tempest_fastapi_sdk" / "integrations" / "CLAUDE.md",
    ]
    paths.extend(sorted((ROOT / ".claude").rglob("*.md")))
    return [path for path in paths if path.exists()]


def _slug(heading: str) -> str:
    """Return the GitHub-style anchor for a Markdown heading.

    Args:
        heading (str): Heading text, without the leading hashes.

    Returns:
        str: The anchor slug — accents preserved, punctuation dropped.
    """
    text = heading.strip().replace("`", "")
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s+", "-", text.strip()).lower()


def _anchors(path: pathlib.Path) -> set[str]:
    """Return every anchor a Markdown file defines through its headings.

    Args:
        path (pathlib.Path): The Markdown file.

    Returns:
        set[str]: Anchor slugs.
    """
    return {_slug(match) for match in _HEADING_RE.findall(path.read_text("utf-8"))}


def _dead_links(path: pathlib.Path) -> list[str]:
    """Return the relative links in ``path`` whose target file is missing.

    Args:
        path (pathlib.Path): The Markdown file to scan.

    Returns:
        list[str]: Link targets that do not resolve on disk.
    """
    dead: list[str] = []
    for target in _LINK_RE.findall(path.read_text("utf-8")):
        if target.startswith(_EXTERNAL) or target.startswith("#"):
            continue
        file_part = target.split("#", 1)[0]
        if file_part and not (path.parent / file_part).resolve().exists():
            dead.append(target)
    return dead


def _dead_anchors(path: pathlib.Path) -> list[str]:
    """Return the links in ``path`` whose ``#anchor`` matches no heading.

    Args:
        path (pathlib.Path): The Markdown file to scan.

    Returns:
        list[str]: Link targets with an anchor that the destination lacks.
    """
    dead: list[str] = []
    for target in _LINK_RE.findall(path.read_text("utf-8")):
        if "#" not in target or target.startswith(_EXTERNAL):
            continue
        file_part, anchor = target.split("#", 1)
        destination = (path.parent / file_part).resolve() if file_part else path
        if not destination.exists() or destination.suffix != ".md":
            continue
        if anchor not in _anchors(destination):
            dead.append(target)
    return dead


def _missing_paths(path: pathlib.Path) -> list[str]:
    """Return the repository paths quoted in ``path`` that do not exist.

    Placeholders (``docs/<page>.md``) and globs (``*.tmpl``) are skipped: they
    describe a shape rather than a file.

    Args:
        path (pathlib.Path): The Markdown file to scan.

    Returns:
        list[str]: Quoted paths with nothing behind them.
    """
    missing: list[str] = []
    for token in _CODE_RE.findall(path.read_text("utf-8")):
        candidate = token.strip()
        if not candidate.startswith(_PATH_PREFIXES):
            continue
        if any(char in candidate for char in _PLACEHOLDER):
            continue
        if not (ROOT / candidate).exists():
            missing.append(candidate)
    return missing


def _unknown_make_targets(path: pathlib.Path) -> list[str]:
    """Return the ``make <target>`` mentions in ``path`` with no Makefile rule.

    Args:
        path (pathlib.Path): The Markdown file to scan.

    Returns:
        list[str]: Target names, sorted, that the Makefile does not declare.
    """
    makefile = (ROOT / "Makefile").read_text("utf-8")
    declared = set(re.findall(r"^([a-z][a-z-]*):", makefile, re.MULTILINE))
    return sorted(
        {
            target
            for target in _MAKE_RE.findall(path.read_text("utf-8"))
            if target not in declared
        }
    )


def _documented_guards(table_text: str) -> set[str]:
    """Return the ``test_*_guard`` names a guard table documents.

    Args:
        table_text (str): Markdown holding the guard table.

    Returns:
        set[str]: Guard module names named in backticks.
    """
    return {
        token.strip()
        for token in _CODE_RE.findall(table_text)
        if re.fullmatch(r"test_\w+_guard", token.strip())
    }


@pytest.mark.parametrize("path", _agent_docs(), ids=lambda p: str(p.name))
def test_relative_links_resolve(path: pathlib.Path) -> None:
    """Every relative link in an agent doc points at a file that exists.

    Args:
        path (pathlib.Path): The instruction file under check.
    """
    assert not _dead_links(path), f"{path.relative_to(ROOT)}: dead links"


@pytest.mark.parametrize("path", _agent_docs(), ids=lambda p: str(p.name))
def test_link_anchors_exist(path: pathlib.Path) -> None:
    """Every ``#anchor`` in an agent doc matches a heading in the target file.

    Args:
        path (pathlib.Path): The instruction file under check.
    """
    assert not _dead_anchors(path), f"{path.relative_to(ROOT)}: anchors with no heading"


@pytest.mark.parametrize("path", _agent_docs(), ids=lambda p: str(p.name))
def test_quoted_repository_paths_exist(path: pathlib.Path) -> None:
    """Every repository path quoted in backticks exists on disk.

    Args:
        path (pathlib.Path): The instruction file under check.
    """
    assert not _missing_paths(path), (
        f"{path.relative_to(ROOT)}: paths that do not exist"
    )


@pytest.mark.parametrize("path", _agent_docs(), ids=lambda p: str(p.name))
def test_quoted_make_targets_exist(path: pathlib.Path) -> None:
    """Every ``make <target>`` quoted in an agent doc is a real Makefile target.

    Args:
        path (pathlib.Path): The instruction file under check.
    """
    assert not _unknown_make_targets(path), (
        f"{path.relative_to(ROOT)}: unknown make targets"
    )


def test_guard_roster_matches_disk() -> None:
    """The guard table in ``tests/CLAUDE.md`` matches the guards on disk.

    Both directions: a new ``test_*_guard.py`` must be documented, and a
    documented guard must exist. The table is how a reader learns which rules
    are enforced, so a stale one is worse than none.
    """
    documented = _documented_guards((ROOT / "tests" / "CLAUDE.md").read_text("utf-8"))
    on_disk = {path.stem for path in (ROOT / "tests").glob("test_*_guard.py")}
    assert not on_disk - documented, (
        f"guards missing from the table in tests/CLAUDE.md: "
        f"{sorted(on_disk - documented)}"
    )
    assert not documented - on_disk, (
        f"table rows naming guards that do not exist: {sorted(documented - on_disk)}"
    )


class TestTheGuardFires:
    """Each check must fail on the shape it exists to catch."""

    def test_dead_link_is_reported(self, tmp_path: pathlib.Path) -> None:
        """A link to a missing sibling file is reported.

        Args:
            tmp_path (pathlib.Path): Pytest temporary directory.
        """
        page = tmp_path / "CLAUDE.md"
        page.write_text("Ver [`LESSONS.md`](LESSONS.md) para o resto.\n", "utf-8")
        assert _dead_links(page) == ["LESSONS.md"]

    def test_dead_anchor_is_reported(self, tmp_path: pathlib.Path) -> None:
        """An anchor absent from an existing target file is reported.

        Args:
            tmp_path (pathlib.Path): Pytest temporary directory.
        """
        (tmp_path / "LESSONS.md").write_text("# Uma seção\n", "utf-8")
        page = tmp_path / "CLAUDE.md"
        page.write_text("Ver [medição](LESSONS.md#outra-secao).\n", "utf-8")
        assert _dead_anchors(page) == ["LESSONS.md#outra-secao"]
        assert _dead_links(page) == []

    def test_live_anchor_is_accepted(self, tmp_path: pathlib.Path) -> None:
        """An anchor that matches a heading, accents included, passes.

        Args:
            tmp_path (pathlib.Path): Pytest temporary directory.
        """
        (tmp_path / "LESSONS.md").write_text(
            "## `Field(alias=...)` quebra o consumidor, não o runtime (v0.234.0)\n",
            "utf-8",
        )
        page = tmp_path / "CLAUDE.md"
        page.write_text(
            "Ver [medição](LESSONS.md"
            "#fieldalias-quebra-o-consumidor-não-o-runtime-v02340).\n",
            "utf-8",
        )
        assert _dead_anchors(page) == []

    def test_missing_path_is_reported(self, tmp_path: pathlib.Path) -> None:
        """A quoted repository path with nothing behind it is reported.

        Args:
            tmp_path (pathlib.Path): Pytest temporary directory.
        """
        page = tmp_path / "CLAUDE.md"
        page.write_text(
            "O guard é `tests/test_nonexistent_guard.py`, e o gerador "
            "`scripts/regen_openpix.py`. Forma: `tests/<pkg>/test_x.py`.\n",
            "utf-8",
        )
        assert _missing_paths(page) == ["tests/test_nonexistent_guard.py"]

    def test_unknown_make_target_is_reported(self, tmp_path: pathlib.Path) -> None:
        """A ``make`` target the Makefile does not declare is reported.

        Args:
            tmp_path (pathlib.Path): Pytest temporary directory.
        """
        page = tmp_path / "CLAUDE.md"
        page.write_text("Rode `make check` e depois `make deploy-prod`.\n", "utf-8")
        assert _unknown_make_targets(page) == ["deploy-prod"]

    def test_undocumented_guard_is_reported(self) -> None:
        """A guard absent from the table is not in the documented set."""
        table = "| `test_alias_guard` | `Field(alias=...)` voltando | — |"
        documented = _documented_guards(table)
        assert documented == {"test_alias_guard"}
        assert "test_kwargs_guard" not in documented
