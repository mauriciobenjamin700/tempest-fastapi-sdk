"""Guard: a table name registered at import never collides with another.

Every model a test module declares at module level lands on the shared
``BaseModel.metadata`` the moment pytest **collects** that module, in every
xdist worker that collects it. A CLI test that seeds a ``src/db/models.py``
and runs ``tempest db revision`` in-process then declares its own table on
the same metadata, and a seeded name equal to an import-time one raises
``Table '<name>' is already defined for this MetaData instance``. Which
worker collects what depends on the distribution, so the failure was
intermittent: ``widget`` from ``tests/db/test_outbox.py`` against the
``widget`` that ``tests/cli/test_db.py`` and the Alembic repro seeded
(#669, #677).

Two rules, read from source with ``ast``:

1. An import-time ``__tablename__`` is unique across the suite.
2. No seeded model source (a string literal holding
   ``__tablename__ = "<name>"``) reuses an import-time name.

Blind spot: a seeded name built at runtime (an f-string placeholder, a
helper argument) is not resolved. ``tests/cli/test_db.py`` builds its
names that way and keeps them unique per test by construction, and the
``isolated_project_imports`` fixture removes the tables a CLI test
registered, so a later test can never see them.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

TESTS_DIR: Path = Path(__file__).resolve().parent
"""Root of the suite the guard scans."""

SEEDED_TABLE: re.Pattern[str] = re.compile(r"""__tablename__\s*=\s*["'](\w+)["']""")
"""A ``__tablename__`` assignment written inside a string literal."""


def _import_time_tables(tree: ast.Module) -> list[str]:
    """Table names declared by classes at module level.

    Args:
        tree (ast.Module): The parsed test module.

    Returns:
        list[str]: Every literal ``__tablename__`` of a top-level class.
    """
    names: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            target: ast.expr | None = None
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target = statement.targets[0]
            elif isinstance(statement, ast.AnnAssign):
                target = statement.target
            if (
                isinstance(target, ast.Name)
                and target.id == "__tablename__"
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                names.append(statement.value.value)
    return names


def _seeded_tables(tree: ast.Module) -> list[str]:
    """Table names written inside string literals of the module.

    Args:
        tree (ast.Module): The parsed test module.

    Returns:
        list[str]: Every ``__tablename__`` found in a literal string.
    """
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.extend(SEEDED_TABLE.findall(node.value))
    return names


def find_collisions(sources: dict[str, str]) -> list[str]:
    """Report every table-name collision across the given modules.

    Args:
        sources (dict[str, str]): Module path to source text.

    Returns:
        list[str]: One human-readable line per collision; empty when clean.
    """
    declared: defaultdict[str, list[str]] = defaultdict(list)
    seeded: defaultdict[str, list[str]] = defaultdict(list)
    for path, text in sources.items():
        tree = ast.parse(text)
        for name in _import_time_tables(tree):
            declared[name].append(path)
        for name in _seeded_tables(tree):
            seeded[name].append(path)

    problems: list[str] = [
        f"{name!r} declared at import by {sorted(paths)}"
        for name, paths in sorted(declared.items())
        if len(paths) > 1
    ]
    problems.extend(
        f"{name!r} seeded by {sorted(set(seeded[name]))} "
        f"but declared at import by {sorted(declared[name])}"
        for name in sorted(seeded)
        if name in declared
    )
    return problems


def _suite_sources() -> dict[str, str]:
    """Read every Python module under ``tests/`` except this guard.

    This file is skipped because its own fixtures spell a colliding pair on
    purpose.

    Returns:
        dict[str, str]: Repository-relative path to source text.
    """
    this_file: Path = Path(__file__).resolve()
    return {
        str(path.relative_to(TESTS_DIR.parent)): path.read_text(encoding="utf-8")
        for path in sorted(TESTS_DIR.rglob("*.py"))
        if path.resolve() != this_file
    }


def test_suite_has_no_table_name_collision() -> None:
    assert find_collisions(_suite_sources()) == []


def test_the_scan_sees_the_suite() -> None:
    """The scan reads real declarations, so a clean result is not vacuous."""
    declared: int = 0
    seeded: int = 0
    for text in _suite_sources().values():
        tree = ast.parse(text)
        declared += len(_import_time_tables(tree))
        seeded += len(_seeded_tables(tree))
    assert declared > 100
    assert seeded > 5


def test_it_fires_on_the_shape_that_shipped() -> None:
    """``tests/db/test_outbox.py`` against ``tests/cli/test_db.py`` before #677."""
    outbox = (
        "from tempest_fastapi_sdk import BaseModel\n\n\n"
        "class _WidgetModel(BaseModel):\n"
        '    __tablename__ = "widget"\n'
    )
    cli_db = (
        "def _seed_model_module(target):\n"
        "    (target / 'models.py').write_text(\n"
        '        "class WidgetModel(BaseModel):\\n"\n'
        "        '    __tablename__ = \"widget\"\\n'\n"
        "    )\n"
    )
    problems = find_collisions(
        {"tests/db/test_outbox.py": outbox, "tests/cli/test_db.py": cli_db}
    )
    assert problems == [
        "'widget' seeded by ['tests/cli/test_db.py'] "
        "but declared at import by ['tests/db/test_outbox.py']"
    ]


def test_it_fires_on_two_import_time_declarations() -> None:
    source = (
        "from tempest_fastapi_sdk import BaseModel\n\n\n"
        "class A(BaseModel):\n"
        '    __tablename__ = "shared"\n'
    )
    problems = find_collisions({"a.py": source, "b.py": source})
    assert problems == ["'shared' declared at import by ['a.py', 'b.py']"]
