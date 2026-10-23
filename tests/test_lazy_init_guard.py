"""Lazy package ``__init__`` files stay lazy, complete and invisible to checkers (#611).

The root package and ``tempest_fastapi_sdk.utils`` load their re-exports on
first access (PEP 562). Three things can silently undo that:

* a module-level import creeping back into the CLI's path, which makes every
  ``tempest`` command pay for SQLAlchemy and FastAPI again;
* the name map, the ``TYPE_CHECKING`` imports and ``__all__`` drifting apart,
  so a name is typed but not importable, or importable but untyped;
* ``__getattr__`` becoming visible to type checkers, which then type every
  unknown attribute as ``Any`` — a consumer's typo stops being an error.
"""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT: Path = Path(__file__).resolve().parent.parent
"""Repository root."""

LAZY_INITS: tuple[str, ...] = (
    "tempest_fastapi_sdk/__init__.py",
    "tempest_fastapi_sdk/utils/__init__.py",
)
"""Package ``__init__`` files that re-export lazily."""

HEAVY_MODULES: tuple[str, ...] = (
    "sqlalchemy",
    "fastapi",
    "alembic",
    "jinja2",
    "bcrypt",
)
"""Dependencies the CLI must not load just to start."""


def _probe(statement: str) -> list[str]:
    """Run ``statement`` in a fresh interpreter and list what it loaded.

    Args:
        statement (str): Python to run first, typically an import.

    Returns:
        list[str]: The :data:`HEAVY_MODULES` present afterwards, then the
        count of ``tempest_fastapi_sdk`` modules as the last item.
    """
    code = (
        f"import sys\n{statement}\n"
        f"print(','.join(m for m in {HEAVY_MODULES!r} if m in sys.modules))\n"
        "print(sum(1 for m in sys.modules if m.startswith('tempest_fastapi_sdk')))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    heavy, count = completed.stdout.rstrip("\n").split("\n")
    return [*filter(None, heavy.split(",")), count]


class TestStartup:
    """Measured in a subprocess: one interpreter, nothing imported before."""

    def test_the_cli_starts_without_the_heavy_dependencies(self) -> None:
        *heavy, _count = _probe("import tempest_fastapi_sdk.cli.main")
        assert heavy == []

    def test_the_root_package_loads_only_itself(self) -> None:
        *heavy, count = _probe("import tempest_fastapi_sdk")
        assert heavy == []
        assert count == "1"

    def test_a_name_still_imports_on_first_access(self) -> None:
        *heavy, _count = _probe("from tempest_fastapi_sdk import BaseRepository")
        assert "sqlalchemy" in heavy


def _shape(source: str) -> tuple[set[str], set[str], set[str], bool]:
    """Read the three name lists of a lazy ``__init__`` and where its hook is.

    Args:
        source (str): The module source.

    Returns:
        tuple[set[str], set[str], set[str], bool]: Names imported under
        ``TYPE_CHECKING``, keys of ``_LAZY_ATTRIBUTES``, ``__all__`` minus
        names defined in the module itself, and whether a ``__getattr__``
        sits at module level where a type checker sees it.
    """
    tree = ast.parse(source)
    typed: set[str] = set()
    lazy: set[str] = set()
    exported: set[str] = set()
    local: set[str] = set()
    visible_hook = False
    for node in tree.body:
        if isinstance(node, ast.If) and ast.unparse(node.test) == "TYPE_CHECKING":
            for inner in node.body:
                if isinstance(inner, ast.ImportFrom):
                    typed |= {alias.name for alias in inner.names}
        elif isinstance(node, ast.FunctionDef) and node.name == "__getattr__":
            visible_hook = True
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            local.add(name)
            if name == "_LAZY_ATTRIBUTES" and isinstance(node.value, ast.Dict):
                lazy = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
            elif name == "__all__" and isinstance(node.value, ast.List):
                exported = {
                    e.value for e in node.value.elts if isinstance(e, ast.Constant)
                }
    return typed, lazy, exported - local, visible_hook


@pytest.mark.parametrize("path", LAZY_INITS)
class TestShape:
    def test_typed_lazy_and_exported_names_agree(self, path: str) -> None:
        typed, lazy, exported, _hook = _shape((ROOT / path).read_text(encoding="utf-8"))
        assert typed == lazy == exported

    def test_the_hook_is_hidden_from_type_checkers(self, path: str) -> None:
        *_names, visible_hook = _shape((ROOT / path).read_text(encoding="utf-8"))
        assert not visible_hook

    def test_every_exported_name_resolves(self, path: str) -> None:
        module_name = path.removesuffix("/__init__.py").replace("/", ".")
        module = importlib.import_module(module_name)
        unresolved = [
            name
            for name in module.__all__
            if name != "__version__" and not hasattr(module, name)
        ]
        assert unresolved == []


class TestSubmoduleAttributes:
    """What a bare ``import`` used to make reachable stays reachable."""

    def test_a_subpackage_is_an_attribute_of_the_root(self) -> None:
        *_heavy, _count = _probe(
            "import tempest_fastapi_sdk as t\n"
            "assert t.db.BaseModel.__name__ == 'BaseModel'"
        )

    def test_a_submodule_is_an_attribute_of_utils(self) -> None:
        *_heavy, _count = _probe(
            "import tempest_fastapi_sdk as t\n"
            "assert t.utils.retry.__name__.endswith('retry')"
        )

    def test_an_unknown_name_still_raises(self) -> None:
        import tempest_fastapi_sdk

        with pytest.raises(AttributeError, match="no attribute 'not_a_module'"):
            tempest_fastapi_sdk.not_a_module  # noqa: B018


CONSUMER: str = """
import tempest_fastapi_sdk as sdk
from tempest_fastapi_sdk.utils import PasswordUtils

reveal_type(sdk.BaseRepository)
reveal_type(PasswordUtils)
missing = sdk.NotARealName
"""
"""What a consumer's checker reads through the lazy packages."""


def _mypy_strict(source: str, workdir: Path) -> str:
    """Run ``mypy --strict`` over ``source`` and return its output.

    Args:
        source (str): The consumer module.
        workdir (Path): Where to write it and keep the cache.

    Returns:
        str: mypy's stdout.
    """
    from mypy import api as mypy_api

    module = workdir / "consumer.py"
    module.write_text(source, encoding="utf-8")
    stdout, _stderr, _status = mypy_api.run(
        [
            "--strict",
            "--no-error-summary",
            "--no-color-output",
            "--cache-dir",
            str(workdir / ".mypy_cache"),
            str(module),
        ]
    )
    return stdout


class TestTypeCheckers:
    """The lazy packages type the same as the eager ones did.

    The shape checks above read the file; this runs a checker the way a
    consumer does, because a visible ``__getattr__`` was measured to make
    ``sdk.NotARealName`` pass and real names resolve to ``Any``.
    """

    def test_an_unknown_name_is_an_error_and_real_names_are_typed(
        self, tmp_path: Path
    ) -> None:
        output = _mypy_strict(CONSUMER, tmp_path)
        assert 'has no attribute "NotARealName"' in output
        revealed = [line for line in output.splitlines() if "Revealed type" in line]
        assert len(revealed) == 2
        assert all('"Any"' not in line for line in revealed)


class TestGuardFires:
    """The shape checks catch the forms that would ship broken."""

    LAZY: str = """
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from pkg.a import A as A
    from pkg.b import B as B
_LAZY_ATTRIBUTES: dict[str, str] = {"A": "pkg.a", "B": "pkg.b"}
if not TYPE_CHECKING:
    def __getattr__(name: str) -> Any: ...
__all__: list[str] = ["A", "B"]
"""

    def test_a_correct_module_passes(self) -> None:
        typed, lazy, exported, hook = _shape(self.LAZY)
        assert typed == lazy == exported == {"A", "B"}
        assert not hook

    def test_a_visible_hook_is_caught(self) -> None:
        """The form that turned every unknown attribute into ``Any``."""
        source = self.LAZY.replace(
            "if not TYPE_CHECKING:\n    def __getattr__", "def __getattr__"
        )
        assert _shape(source)[3]

    def test_a_name_missing_from_the_map_is_caught(self) -> None:
        source = self.LAZY.replace(', "B": "pkg.b"', "")
        typed, lazy, exported, _hook = _shape(source)
        assert not typed == lazy == exported
