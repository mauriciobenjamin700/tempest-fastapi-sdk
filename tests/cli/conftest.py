"""Fixtures shared by the CLI tests."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_PACKAGES: frozenset[str] = frozenset({"src", "app"})
"""Top-level packages a scaffolded project imports from."""


def _is_project_module(name: str) -> bool:
    """Whether ``name`` belongs to a project package rather than a library.

    Args:
        name (str): A ``sys.modules`` key.

    Returns:
        bool: ``True`` for ``src``/``app`` and anything under them.
    """
    return name.split(".", 1)[0] in PROJECT_PACKAGES


def _under(entry: str, base: Path) -> bool:
    """Whether a ``sys.path`` entry points inside ``base``.

    The empty entry means "the working directory" and is kept: it moves
    with ``chdir`` rather than pinning an earlier project.

    Args:
        entry (str): A ``sys.path`` entry.
        base (Path): The resolved base temporary directory.

    Returns:
        bool: ``True`` when the entry is a directory under ``base``.
    """
    return bool(entry) and Path(entry).resolve().is_relative_to(base)


@pytest.fixture
def isolated_project_imports(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Make ``src.*`` resolve only against the current test's project.

    The CLI puts the working directory at the front of ``sys.path`` and
    never takes it off (``ensure_project_on_path``), which is right for a
    process that runs one command from one directory and wrong for a test
    worker that runs hundreds from as many temporary projects. A test
    whose project has no ``src/db/models.py`` then resolved the
    conventional ``src.db.models`` from a project an **earlier** test
    left on the path, and the "no refresh-token model" branch never ran
    (#575): it failed whenever xdist ordered a seeding test before it on
    the same worker.

    Every entry under pytest's base temporary directory is dropped from
    ``sys.path`` and every ``src``/``app`` module is forgotten, on entry
    and again on exit. ``sys.path`` is restored to its previous list
    object afterwards, so whatever the test itself adds goes too.

    Args:
        tmp_path_factory (pytest.TempPathFactory): Locates the base
            temporary directory every test project lives under.
        monkeypatch (pytest.MonkeyPatch): Restores ``sys.path``.

    Yields:
        None: Control to the test.
    """
    base = tmp_path_factory.getbasetemp().resolve()

    def _forget() -> None:
        """Drop the project modules any earlier import left cached."""
        for name in [name for name in sys.modules if _is_project_module(name)]:
            del sys.modules[name]

    monkeypatch.setattr(
        sys,
        "path",
        [entry for entry in sys.path if not _under(entry, base)],
    )
    _forget()
    yield
    _forget()
