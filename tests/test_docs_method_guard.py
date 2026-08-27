"""Guard: a documented example must not call a method the class does not have.

The import guard in `test_docs_examples_compile` resolves the *names* a block
imports. It cannot see what the block then does with them, and that is where
two examples shipped broken:

    notifications = WebPushDispatcher(**settings.webpush_kwargs())
    await notifications.notify(...)   # AttributeError: only send/send_many
    notifications.broker.response(...)

Both parse, both import real symbols, and both raise the moment a reader runs
them. They were leftovers of a refactor that introduced a `NotificationService`
wrapper two sections above, and they survived every other guard.

The check is deliberately narrow, because the alternative is a type checker.
It tracks one shape only — a local assigned exactly once from a constructor of
a class the block imports from the Tempest family — and then asserts every
attribute read off that local exists on the class. Anything less certain (a
name assigned twice, a call result, an attribute of an attribute) is dropped
rather than guessed at, so a failure here is always a real missing attribute.

What it does not catch, on purpose: argument *types*. `get_claims("raw token")`
against a parameter typed `HTTPAuthorizationCredentials` shipped in
`introspection-auth.md` and only mypy would have seen it.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from pathlib import Path
from textwrap import dedent
from types import ModuleType
from typing import Any

import pytest

DOCS_ROOT: Path = Path(__file__).resolve().parent.parent

TEMPEST_PACKAGES: tuple[str, ...] = (
    "tempest_fastapi_sdk",
    "tempest_core",
    "tempestweb",
)
"""Packages whose classes are resolved for real, mirroring the import guard."""

FENCE_RE: re.Pattern[str] = re.compile(
    r"^(?P<indent>[ \t]*)```(?:python|py)(?P<attrs>[^\n]*)\n"
    r"(?P<body>.*?)^(?P=indent)```",
    re.DOTALL | re.MULTILINE,
)

SKIP_MARKER: str = "docs-guard: skip"


def _markdown_files() -> list[Path]:
    """Collect every Markdown file whose examples ship to readers.

    Returns:
        list[Path]: The docs tree plus the repository README, sorted for
        stable test ids.
    """
    files: list[Path] = sorted((DOCS_ROOT / "docs").rglob("*.md"))
    files.append(DOCS_ROOT / "README.md")
    return files


def _imported_classes(tree: ast.Module) -> dict[str, str]:
    """Map each locally bound name to the Tempest module it came from.

    Args:
        tree (ast.Module): The parsed example block.

    Returns:
        dict[str, str]: ``{local name: module path}`` for every name imported
        from a package in :data:`TEMPEST_PACKAGES`.
    """
    imported: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module: str = node.module or ""
        if module.split(".", 1)[0] not in TEMPEST_PACKAGES:
            continue
        for alias in node.names:
            imported[alias.asname or alias.name] = module
    return imported


def _single_assignments(tree: ast.Module) -> dict[str, str]:
    """Map each local assigned exactly once from a plain call to its callee.

    A name assigned more than once is dropped: the second value may be a
    different type, and this guard reports only what it is certain about.

    Args:
        tree (ast.Module): The parsed example block.

    Returns:
        dict[str, str]: ``{local name: callee name}``.
    """
    counts: dict[str, int] = {}
    callees: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        counts[target.id] = counts.get(target.id, 0) + 1
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
            callees[target.id] = node.value.func.id
    return {name: callee for name, callee in callees.items() if counts[name] == 1}


def _resolve(module_name: str, symbol: str) -> Any | None:
    """Return the live object for ``symbol``, or ``None`` when unavailable.

    Args:
        module_name (str): The module to import.
        symbol (str): The attribute to read off it.

    Returns:
        Any | None: The resolved object. ``None`` when the module or the
        symbol cannot be reached — a missing optional extra is an environment
        gap, and the import guard already owns a genuinely absent name.
    """
    try:
        module: ModuleType = importlib.import_module(module_name)
    except ImportError:
        return None
    try:
        return getattr(module, symbol)
    except (AttributeError, ImportError):
        return None


def _self_assigned(cls: type) -> frozenset[str]:
    """Collect every ``self.<name>`` a class assigns anywhere in its body.

    This is the case a naive ``hasattr`` check gets wrong, and it is the
    common one here: the house style writes `self.session: AsyncSession =
    session` inside `__init__`, and an annotated assignment to an attribute
    never lands in `cls.__annotations__` — that dict holds class-level
    annotations only. Without this, every documented read of a perfectly
    normal instance attribute would be reported.

    Args:
        cls (type): The class to inspect.

    Returns:
        frozenset[str]: Attribute names the class assigns on ``self``. Empty
        when the source is unavailable (a C extension, a REPL-defined class),
        which makes the caller fall back to reporting nothing for that class.
    """
    names: set[str] = set()
    for base in getattr(cls, "__mro__", ()):
        if base is object:
            continue
        try:
            source: str = inspect.getsource(base)
        except (OSError, TypeError):
            continue
        try:
            tree: ast.Module = ast.parse(dedent(source))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    names.add(target.attr)
    return frozenset(names)


def _declares(cls: type, name: str) -> bool:
    """Report whether instances of ``cls`` are expected to carry ``name``.

    Covers the five ways an attribute legitimately exists: a method or class
    attribute, a class-level annotation anywhere in the MRO, a Pydantic
    field, a dataclass field, and an assignment to ``self`` in the class
    body — see :func:`_self_assigned` for why that last one is not optional.

    Args:
        cls (type): The class the example constructed.
        name (str): The attribute the example reads.

    Returns:
        bool: ``True`` when the attribute is accounted for.
    """
    if hasattr(cls, name):
        return True
    for base in getattr(cls, "__mro__", ()):
        if name in getattr(base, "__annotations__", {}):
            return True
    if name in getattr(cls, "model_fields", {}):
        return True
    if is_dataclass(cls) and any(f.name == name for f in dataclass_fields(cls)):
        return True
    return name in _self_assigned(cls)


def _label(path: Path) -> str:
    """Return the repo-relative name of ``path``, or its own name off-tree.

    Args:
        path (Path): The Markdown file being reported.

    Returns:
        str: A stable label for the message. The fallback exists so the
        guard can be pointed at a temporary file, which is how the
        regression tests below feed it the block that shipped.
    """
    try:
        return str(path.relative_to(DOCS_ROOT))
    except ValueError:
        return path.name


def _missing_attributes(path: Path) -> list[str]:
    """Report attributes read off a constructed instance that do not exist.

    Args:
        path (Path): The Markdown file to scan.

    Returns:
        list[str]: One message per unresolvable attribute.
    """
    problems: list[str] = []
    for match in FENCE_RE.finditer(path.read_text(encoding="utf-8")):
        body: str = match.group("body")
        if SKIP_MARKER in body:
            continue
        try:
            tree: ast.Module = ast.parse(body)
        except SyntaxError:
            continue
        imported: dict[str, str] = _imported_classes(tree)
        assigned: dict[str, str] = _single_assignments(tree)
        instances: dict[str, type] = {}
        for local, callee in assigned.items():
            module_name: str | None = imported.get(callee)
            if module_name is None:
                continue
            resolved: Any | None = _resolve(module_name, callee)
            if isinstance(resolved, type):
                instances[local] = resolved
        if not instances:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if not isinstance(node.value, ast.Name):
                continue
            cls: type | None = instances.get(node.value.id)
            if cls is None or node.attr.startswith("_"):
                continue
            if not _declares(cls, node.attr):
                problems.append(
                    f"{_label(path)}: "
                    f"{cls.__name__} has no {node.attr!r} "
                    f"(read off {node.value.id!r})"
                )
    return problems


@pytest.mark.parametrize("path", _markdown_files(), ids=lambda p: str(p.name))
def test_examples_call_methods_that_exist(path: Path) -> None:
    """Fail when an example reads an attribute its class does not have."""
    problems: list[str] = _missing_attributes(path)
    assert not problems, "\n".join(problems)


SHIPPED_DEFECT: str = '''```python
from tempest_fastapi_sdk.webpush import WebPushDispatcher

from src.core.settings import settings

notifications = WebPushDispatcher(**settings.webpush_kwargs())


async def on_order_paid() -> None:
    """The exact call that shipped in integrated.md and marketplace-local.md."""
    await notifications.notify(
        "user-id",
        event="payment_confirmed",
        title="Pago",
        body="Pedido confirmado.",
    )
```
'''

SELF_ASSIGNED_ATTRIBUTE: str = """```python
from tempest_fastapi_sdk import FileStoreUtils

store = FileStoreUtils(upload_dir="/tmp/uploads")
print(store.uploader)
```
"""


def test_guard_fires_on_the_defect_that_shipped(tmp_path: Path) -> None:
    """Feed it the real leftover and assert it fails.

    A guard that cannot fail is a guard nobody should trust, so this pins the
    exact block that reached readers: a `WebPushDispatcher` built where a
    `NotificationService` was meant, then asked for `.notify()`.
    """
    page: Path = tmp_path / "regression.md"
    page.write_text(SHIPPED_DEFECT, encoding="utf-8")
    problems: list[str] = _missing_attributes(page)
    assert len(problems) == 1
    assert "WebPushDispatcher has no 'notify'" in problems[0]


def test_guard_stays_quiet_on_an_attribute_set_in_init(tmp_path: Path) -> None:
    """The false positive that would have made it noise.

    `FileStoreUtils.uploader` is assigned as `self.uploader: ... = ...` inside
    `__init__`, so it is absent from both `hasattr` and `__annotations__`.
    Reporting it would have flagged four correct pages, and a guard that
    cries wolf stops being read.
    """
    page: Path = tmp_path / "clean.md"
    page.write_text(SELF_ASSIGNED_ATTRIBUTE, encoding="utf-8")
    assert _missing_attributes(page) == []
