"""Guards that every doc example calls the SDK the way the SDK is written.

``test_docs_api_guard.py`` proves a snippet *parses* and that every
``__all__`` name resolves. Neither catches the failure mode that actually
bites a reader: a syntactically perfect example passing a keyword the
function does not accept, or handing a keyword-only parameter
positionally. Copy-pasting those raises ``TypeError`` on the first run,
and prose written around the invented parameter documents behavior that
does not exist.

Three checks, all static (nothing is executed):

1. **Keywords exist** — every ``kwarg=`` in a call to an SDK symbol is a
   real parameter of that symbol. Skipped when the callable takes
   ``**kwargs``.
2. **Positional arity fits** — a call never passes more positional
   arguments than the signature accepts. This is what catches both a
   keyword-only parameter used positionally and the
   ``f(site, ..., x=1)`` elision, whose literal ``Ellipsis`` is a real
   argument at runtime.
3. **Doc imports resolve** — every
   ``from tempest_fastapi_sdk... import Name`` in a snippet names an
   attribute that exists.

Symbols are resolved **per block, from that block's own imports**, never
from a global table. The SDK exports two different ``RetryPolicy``
classes (``tempest_fastapi_sdk.RetryPolicy`` for the HTTP client takes
``max_attempts``; ``tempest_fastapi_sdk.tasks.RetryPolicy`` takes
``max_retries``), so a table keyed by bare name would report the correct
example as broken. A snippet that uses a symbol without importing it is
simply not checked — silence beats a false accusation.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pathlib
import re
import textwrap
from types import ModuleType
from typing import Any

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_BLOCK_RE = re.compile(r"```(?:python|py)\n(.*?)```", re.DOTALL)
_SDK_ROOT = "tempest_fastapi_sdk"


def _doc_files() -> list[pathlib.Path]:
    """Return every Markdown file whose code blocks are under guard."""
    files = [_ROOT / "CLAUDE.md", _ROOT / "README.md"]
    files.extend(sorted((_ROOT / "docs").rglob("*.md")))
    return [f for f in files if f.exists()]


def _normalize(block: str) -> str:
    """Strip a uniform Markdown blockquote prefix, then dedent."""
    lines = block.split("\n")
    non_empty = [line for line in lines if line.strip()]
    if non_empty and all(line.startswith("> ") or line == ">" for line in non_empty):
        lines = [line[2:] if line.startswith("> ") else "" for line in lines]
    return textwrap.dedent("\n".join(lines))


def _blocks() -> list[tuple[pathlib.Path, int, ast.Module]]:
    """Return every parseable, non-exempt doc block as an AST module."""
    parsed: list[tuple[pathlib.Path, int, ast.Module]] = []
    for path in _doc_files():
        for index, block in enumerate(_BLOCK_RE.findall(path.read_text())):
            if "# docs-guard: skip" in block:
                continue
            try:
                parsed.append((path, index, ast.parse(_normalize(block))))
            except SyntaxError:
                continue
    return parsed


def _import_module(name: str) -> ModuleType | None:
    """Import an SDK module, returning ``None`` when an extra is absent."""
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def _block_symbols(tree: ast.Module) -> dict[str, Any]:
    """Map local names to SDK objects, using only this block's imports.

    Args:
        tree (ast.Module): The parsed doc block.

    Returns:
        dict[str, Any]: Local name (honoring ``as`` aliases) to the
        resolved SDK object. Names whose optional extra is missing in
        this environment are left out, so the check degrades to silence
        instead of failing.
    """
    table: dict[str, Any] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if node.module != _SDK_ROOT and not node.module.startswith(f"{_SDK_ROOT}."):
            continue
        module = _import_module(node.module)
        if module is None:
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            try:
                obj = getattr(module, alias.name)
            except (AttributeError, ImportError):
                continue
            table[alias.asname or alias.name] = obj
    return table


def _signature(obj: Any) -> inspect.Signature | None:
    """Return the callable signature to check a doc call against."""
    target = obj.__init__ if inspect.isclass(obj) else obj
    try:
        return inspect.signature(target)
    except (TypeError, ValueError):
        return None


def _sdk_calls(
    tree: ast.Module,
) -> list[tuple[ast.Call, str, Any]]:
    """Return the calls in a block whose callee is a known SDK symbol."""
    table = _block_symbols(tree)
    calls: list[tuple[ast.Call, str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        obj = table.get(node.func.id)
        if obj is not None and callable(obj):
            calls.append((node, node.func.id, obj))
    return calls


def test_doc_calls_use_real_keywords() -> None:
    """No doc example passes a keyword the SDK signature does not declare."""
    failures: list[str] = []
    for path, index, tree in _blocks():
        for node, name, obj in _sdk_calls(tree):
            signature = _signature(obj)
            if signature is None:
                continue
            params = signature.parameters
            if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
                continue
            for keyword in node.keywords:
                if keyword.arg is None or keyword.arg in params:
                    continue
                accepted = ", ".join(n for n in params if n != "self")
                failures.append(
                    f"{path.relative_to(_ROOT)} block #{index} line {node.lineno}: "
                    f"{name}({keyword.arg}=...) is not a parameter "
                    f"(accepts: {accepted})"
                )
    assert not failures, "doc examples pass unknown keywords:\n" + "\n".join(failures)


def test_doc_calls_respect_positional_arity() -> None:
    """No doc example passes more positional arguments than accepted.

    Also catches the ``f(obj, ..., kwarg=1)`` elision: the literal
    ``Ellipsis`` counts as an argument at runtime, so the snippet raises
    ``TypeError`` while looking like a deliberate omission.
    """
    failures: list[str] = []
    for path, index, tree in _blocks():
        for node, name, obj in _sdk_calls(tree):
            signature = _signature(obj)
            if signature is None:
                continue
            if any(isinstance(arg, ast.Starred) for arg in node.args):
                continue
            positional = [
                p
                for p in signature.parameters.values()
                if p.kind
                in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.VAR_POSITIONAL,
                )
            ]
            if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in positional):
                continue
            allowed = len([p for p in positional if p.name != "self"])
            if len(node.args) > allowed:
                failures.append(
                    f"{path.relative_to(_ROOT)} block #{index} line {node.lineno}: "
                    f"{name}() accepts {allowed} positional argument(s), "
                    f"the example passes {len(node.args)}"
                )
    assert not failures, "doc examples misuse positional arguments:\n" + "\n".join(
        failures
    )


_FLOOR_RE = re.compile(r"tempest-fastapi-sdk\[[^\]]*\]>=(\d+)\.(\d+)\.(\d+)")


def _package_version() -> tuple[int, int, int]:
    """Return the version declared in ``pyproject.toml`` as a tuple."""
    match = re.search(
        r'^version\s*=\s*"(\d+)\.(\d+)\.(\d+)"',
        (_ROOT / "pyproject.toml").read_text(),
        re.MULTILINE,
    )
    assert match is not None, "pyproject.toml declares no parseable version"
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def test_doc_install_floors_are_released() -> None:
    """No install snippet requires a version this package has not reached.

    A floor above the shipped version is unsatisfiable: the reader runs
    the command and pip reports no matching distribution. It happens by
    copying a snippet forward during a release that is later renumbered.
    """
    current = _package_version()
    failures: list[str] = []
    for path in _doc_files():
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            for match in _FLOOR_RE.finditer(line):
                floor = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
                if floor > current:
                    failures.append(
                        f"{path.relative_to(_ROOT)}:{line_number}: floor "
                        f"{'.'.join(str(p) for p in floor)} exceeds the packaged "
                        f"{'.'.join(str(p) for p in current)}"
                    )
    assert not failures, "unsatisfiable install floors:\n" + "\n".join(failures)


def test_doc_imports_resolve() -> None:
    """Every SDK name a doc block imports exists in that module."""
    failures: list[str] = []
    for path, index, tree in _blocks():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            if node.module != _SDK_ROOT and not node.module.startswith(f"{_SDK_ROOT}."):
                continue
            module = _import_module(node.module)
            if module is None:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                try:
                    getattr(module, alias.name)
                except ImportError:
                    continue
                except AttributeError:
                    failures.append(
                        f"{path.relative_to(_ROOT)} block #{index} "
                        f"line {node.lineno}: {node.module} has no {alias.name}"
                    )
    assert not failures, "doc imports that do not resolve:\n" + "\n".join(failures)
