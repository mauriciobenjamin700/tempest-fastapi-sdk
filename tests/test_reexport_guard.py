"""Guard that every public re-export uses the PEP 484 ``as`` form.

``CLAUDE.md`` has required ``from x import Y as Y`` inside ``__init__.py``
for a long time, for a concrete reason: basedpyright and Pylance in
strict mode flag the plain form as "private import usage", so a consumer
importing a documented symbol sees a diagnostic the SDK put there.

The rule was written and never enforced. On the day somebody counted, it
was violated **794 times across 17 files** — which is the whole argument
for this file existing: a rule nobody checks is a rule that drifts to
wherever the last edit left it.

Scope is deliberately the names in ``__all__``. A helper an
``__init__.py`` imports for its own use is not a re-export, and aliasing
it would say the opposite.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT: Path = Path(__file__).resolve().parent.parent / "tempest_fastapi_sdk"

SKIP_MARKER: str = "reexport-guard: skip"
"""Line marker for an import that is genuinely not a re-export."""


def _init_files() -> list[Path]:
    """Collect every package ``__init__.py``.

    Returns:
        list[Path]: The files, sorted for a stable test id.
    """
    return sorted(PACKAGE_ROOT.rglob("__init__.py"))


def _exported_names(tree: ast.Module) -> set[str]:
    """Read the names listed in the module's ``__all__``.

    Handles the annotated form the SDK uses (``__all__: list[str] = [...]``)
    as well as a plain assignment.

    Args:
        tree (ast.Module): The parsed module.

    Returns:
        set[str]: The exported names, empty when there is no ``__all__``.
    """
    names: set[str] = set()
    for node in tree.body:
        target: ast.expr | None = None
        if isinstance(node, ast.AnnAssign):
            target = node.target
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id != "__all__":
            continue
        if isinstance(node.value, ast.List):
            names |= {
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
    return names


def _plain_reexports(path: Path) -> list[str]:
    """Find public re-exports written without an explicit alias.

    Args:
        path (Path): The ``__init__.py`` to inspect.

    Returns:
        list[str]: One ``file:line: name`` entry per violation.
    """
    source = path.read_text(encoding="utf-8")
    lines = source.split("\n")
    tree = ast.parse(source)
    public = _exported_names(tree)
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module == "__future__":
            continue
        for alias in node.names:
            if alias.asname is not None or alias.name == "*":
                continue
            if alias.name not in public:
                continue
            if SKIP_MARKER in lines[alias.lineno - 1]:
                continue
            problems.append(f"{_label(path)}:{alias.lineno}: {alias.name}")
    return problems


def _label(path: Path) -> str:
    """Render a path for the failure message.

    Falls back to the absolute path for a file outside the package, so
    the guard's own tests can point it at a temporary directory.

    Args:
        path (Path): The inspected file.

    Returns:
        str: A repo-relative path when possible.
    """
    try:
        return str(path.relative_to(PACKAGE_ROOT.parent))
    except ValueError:
        return str(path)


@pytest.mark.parametrize("path", _init_files(), ids=lambda p: p.parent.name)
def test_public_reexports_are_aliased(path: Path) -> None:
    """Every name in ``__all__`` is imported as ``Y as Y``."""
    problems = _plain_reexports(path)
    assert not problems, (
        "public re-exports must use `from x import Y as Y` so strict "
        "type-checkers accept them:\n  " + "\n  ".join(problems)
    )


@pytest.mark.parametrize("path", _init_files(), ids=lambda p: p.parent.name)
def test_every_exported_name_resolves(path: Path) -> None:
    """``__all__`` never promises a name the module does not have.

    The alias rewrite touched 794 import lines at once; this is the
    assertion that the rewrite did not lose one.
    """
    import importlib

    module_path = path.relative_to(PACKAGE_ROOT.parent).with_suffix("")
    parts = [part for part in module_path.parts if part != "__init__"]
    module_name = ".".join(parts)
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:  # pragma: no cover - optional extra absent
        pytest.skip(f"{module_name} needs an extra that is not installed: {exc}")
    missing = [
        name for name in getattr(module, "__all__", []) if not hasattr(module, name)
    ]
    assert not missing, f"{module_name}.__all__ names absent symbols: {missing}"


def test_the_guard_fires_on_the_shape_that_shipped(tmp_path: Path) -> None:
    """A guard that cannot fail is one nobody should trust.

    Reproduces the exact form that was in the tree 794 times.
    """
    offender = tmp_path / "__init__.py"
    offender.write_text(
        'from pkg.mod import Thing\n\n__all__: list[str] = ["Thing"]\n',
        encoding="utf-8",
    )
    assert _plain_reexports(offender), "the guard missed a plain public re-export"

    fixed = tmp_path / "fixed" / "__init__.py"
    fixed.parent.mkdir()
    fixed.write_text(
        'from pkg.mod import Thing as Thing\n\n__all__: list[str] = ["Thing"]\n',
        encoding="utf-8",
    )
    assert not _plain_reexports(fixed)


def test_private_imports_are_left_alone(tmp_path: Path) -> None:
    """A helper the module imports for itself is not a re-export.

    Aliasing it would claim the opposite, so the guard must not ask for
    it — otherwise the fix for a false positive is to make the file lie.
    """
    path = tmp_path / "__init__.py"
    path.write_text(
        "from pkg.mod import helper\nfrom pkg.mod import Thing as Thing\n\n"
        '__all__: list[str] = ["Thing"]\n',
        encoding="utf-8",
    )
    assert not _plain_reexports(path)
