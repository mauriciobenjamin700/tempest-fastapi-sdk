"""Guard that factory parameter types are importable from the top level.

The SDK's rule is to import public symbols from the package root, not from
the submodule that happens to define them (#494). A factory exported from the
top level whose signature names an SDK class or alias that is *not* also
exported forces the consumer to reach into a private path to annotate their
own call site — which is what the ``7rc/homework_api`` service did for
``RateLimitKeyFunc``.

The guard reads the annotations off the public factories, keeps the names the
SDK defines itself, and asserts each is exported from
``tempest_fastapi_sdk``. External names (``AsyncSession``, ``BaseModel``,
``Callable``) are the caller's dependency, not ours, so they are out of scope.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import tempest_fastapi_sdk
from tempest_fastapi_sdk import RateLimitKeyFunc

PACKAGE_ROOT: Path = Path(__file__).resolve().parent.parent / "tempest_fastapi_sdk"
"""Directory holding the SDK source the scan reads."""

PUBLIC_FACTORIES: tuple[str, ...] = (
    "tempest_fastapi_sdk.make_rate_limit_dependency",
    "tempest_fastapi_sdk.make_auth_router",
)
"""Factories whose parameter types a consumer must be able to import."""


def _sdk_defined_names() -> frozenset[str]:
    """Collect public names the SDK itself defines.

    Scans every module for a top-level ``class`` or a top-level assignment
    target, plus the string entries of any ``__all__`` list. Imported names
    are deliberately ignored: ``AsyncSession`` is re-exported in some modules
    but belongs to SQLAlchemy, and the guard must not demand it at the root.

    Returns:
        frozenset[str]: Public names defined by the package.
    """
    defined: set[str] = set()
    for path in PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    defined.add(node.name)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if not node.target.id.startswith("_"):
                    defined.add(node.target.id)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("_"):
                        defined.add(target.id)
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if isinstance(value, ast.List):
                    defined |= {
                        element.value
                        for element in value.elts
                        if isinstance(element, ast.Constant)
                        and isinstance(element.value, str)
                    }
    return frozenset(defined)


def _annotation_names(annotation: Any) -> set[str]:
    """Extract the bare names named in one signature annotation.

    Args:
        annotation (Any): The annotation, a string under
            ``from __future__ import annotations``.

    Returns:
        set[str]: Every ``ast.Name`` identifier in the annotation.
    """
    if not isinstance(annotation, str):
        return set()
    try:
        tree = ast.parse(annotation, mode="eval")
    except SyntaxError:  # pragma: no cover - defensive
        return set()
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def unexported_param_types(
    factory: Callable[..., Any],
    sdk_names: frozenset[str],
    exported: frozenset[str],
) -> list[str]:
    """List SDK types in ``factory``'s signature missing from ``exported``.

    Args:
        factory (Callable[..., Any]): The public factory to inspect.
        sdk_names (frozenset[str]): Names the SDK defines itself.
        exported (frozenset[str]): Names exported from the package root.

    Returns:
        list[str]: Sorted SDK names the signature uses but the root omits.
    """
    missing: set[str] = set()
    for parameter in inspect.signature(factory).parameters.values():
        for name in _annotation_names(parameter.annotation):
            if name in sdk_names and name not in exported:
                missing.add(name)
    return sorted(missing)


def test_factory_parameter_types_are_exported() -> None:
    """Every SDK type a public factory names is importable from the root."""
    sdk_names = _sdk_defined_names()
    exported = frozenset(getattr(tempest_fastapi_sdk, "__all__", ()))
    problems: list[str] = []
    for dotted in PUBLIC_FACTORIES:
        module_name, _, attr = dotted.rpartition(".")
        factory = getattr(importlib.import_module(module_name), attr)
        missing = unexported_param_types(factory, sdk_names, exported)
        if missing:
            problems.append(f"{dotted}: {missing}")
    assert not problems, (
        "these factories name SDK types the top level does not export, so a "
        "consumer must import the submodule to annotate the call:\n  "
        + "\n  ".join(problems)
        + "\nExport them from tempest_fastapi_sdk (and add a docs/reference.md "
        "stub)."
    )


def test_the_guard_fires_on_the_shape_that_shipped() -> None:
    """The guard flags the exact #494 shape it was written for.

    ``RateLimitKeyFunc`` was a parameter type of an exported factory and
    absent from ``tempest_fastapi_sdk.__all__``, which sent the service to the
    submodule.
    """

    def factory(key: RateLimitKeyFunc) -> None:
        """Stand-in for a public factory whose parameter is an SDK alias."""

    sdk_names = frozenset({"RateLimitKeyFunc"})
    assert unexported_param_types(factory, sdk_names, frozenset()) == [
        "RateLimitKeyFunc",
    ]
    assert unexported_param_types(factory, sdk_names, sdk_names) == []


def test_external_types_are_out_of_scope() -> None:
    """Names the SDK does not define are never demanded at the root."""

    def factory(session: AsyncSession) -> None:  # noqa: F821
        """Stand-in for a factory typed against a third-party class."""

    assert unexported_param_types(factory, frozenset(), frozenset()) == []


def test_the_scan_reaches_beyond_the_factory_modules() -> None:
    """A name defined in another SDK module still counts as SDK-owned."""
    sdk_names = _sdk_defined_names()
    assert "RateLimitKeyFunc" in sdk_names
    assert "UserAuthService" in sdk_names
    assert "AsyncSession" not in sdk_names


@pytest.mark.parametrize("dotted", PUBLIC_FACTORIES)
def test_named_factory_exists(dotted: str) -> None:
    """Keeps the roster honest if a factory is renamed."""
    module_name, _, attr = dotted.rpartition(".")
    assert hasattr(importlib.import_module(module_name), attr)
