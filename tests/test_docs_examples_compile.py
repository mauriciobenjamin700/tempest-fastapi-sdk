"""Guard: every Python example in the docs must be a valid module — and
import only names the SDK actually exports.

The failure this catches is `await` (or `async for` / `async with`) at module
level. It reads fine on the page and it is a hard `SyntaxError` the moment a
reader pastes it into a file:

    run = await agent.run("...")
    #     ^ SyntaxError: 'await' outside function

Every example on the site is meant to be copy-pasteable, so an awaited call
belongs inside an `async def` the block itself defines, with the block ending
in `asyncio.run(...)` (or a FastAPI endpoint, which is already async).

Blocks that are deliberately partial — a class body excerpt, a diff, a
snippet with `...` standing in for code — raise other syntax errors and are
skipped: only async-context errors fail the test.

Uses `compile()`, not `ast.parse()`: the "await outside function" rule is
enforced by the symtable pass, so `ast.parse("x = await f()")` succeeds and
would make this guard silently vacuous.

The second failure is a documented import of something that does not exist —
a renamed export, a symbol that never shipped, a module path from an earlier
layout. It costs a reader the same as a syntax error and is invisible to
`mkdocs build`, so every `from tempest_fastapi_sdk… import X` in the docs is
resolved against the installed package here.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path
from types import ModuleType

import pytest

DOCS_ROOT: Path = Path(__file__).resolve().parent.parent

FENCE_RE: re.Pattern[str] = re.compile(
    r"^(?P<indent>[ \t]*)```(?:python|py)(?P<attrs>[^\n]*)\n"
    r"(?P<body>.*?)^(?P=indent)```",
    re.DOTALL | re.MULTILINE,
)

ASYNC_CONTEXT_ERRORS: tuple[str, ...] = (
    "'await' outside function",
    "'await' outside async function",
    "asynchronous comprehension outside of an asynchronous function",
    "'async with' outside async function",
    "'async for' outside async function",
)


def _markdown_files() -> list[Path]:
    """Collect every Markdown file whose examples ship to readers.

    Returns:
        The docs tree plus the repository README, sorted for stable ids.
    """
    files: list[Path] = sorted((DOCS_ROOT / "docs").rglob("*.md"))
    files.append(DOCS_ROOT / "README.md")
    return files


def _async_context_errors(path: Path) -> list[str]:
    """Parse every Python block in a Markdown file and report async misuse.

    Args:
        path: The Markdown file to scan.

    Returns:
        One message per offending block, naming the line inside the file so
        the failure points straight at the fence to fix. Blocks failing for
        unrelated reasons (intentionally partial snippets) are ignored.
    """
    text: str = path.read_text(encoding="utf-8")
    problems: list[str] = []
    for match in FENCE_RE.finditer(text):
        body: str = match.group("body")
        line_offset: int = text[: match.start()].count("\n") + 1
        try:
            compile(body, str(path), "exec")
        except SyntaxError as exc:
            message: str = str(exc.msg)
            if any(known in message for known in ASYNC_CONTEXT_ERRORS):
                block_line: int = line_offset + (exc.lineno or 1)
                problems.append(
                    f"{path.relative_to(DOCS_ROOT)}:{block_line}: {message}"
                )
    return problems


@pytest.mark.parametrize("path", _markdown_files(), ids=lambda p: str(p.name))
def test_examples_have_no_module_level_await(path: Path) -> None:
    """Fail when a documented example awaits outside an async function."""
    problems: list[str] = _async_context_errors(path)
    assert not problems, "\n".join(problems)


def _sdk_import_targets(body: str) -> list[tuple[str, str]]:
    """Collect the ``(module, symbol)`` pairs a block imports from the SDK.

    Args:
        body: The fence's Python source.

    Returns:
        One pair per imported name. Blocks that do not parse are skipped —
        the syntax guard above already owns those.
    """
    try:
        tree: ast.Module = ast.parse(body)
    except SyntaxError:
        return []
    targets: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module: str = node.module or ""
        if not module.startswith("tempest_fastapi_sdk"):
            continue
        targets.extend((module, alias.name) for alias in node.names)
    return targets


def _missing_exports(path: Path) -> list[str]:
    """Report documented imports the installed package cannot satisfy.

    Args:
        path: The Markdown file to scan.

    Returns:
        One message per unresolvable name. An ``ImportError`` from the
        attribute lookup means the symbol exists but its optional extra is
        absent (the SDK's lazy ``__getattr__`` raises with the install
        command) — that is an environment gap, not a docs defect, so it is
        ignored. Only ``AttributeError`` means the name is not there.
    """
    problems: list[str] = []
    for match in FENCE_RE.finditer(path.read_text(encoding="utf-8")):
        for module_name, symbol in _sdk_import_targets(match.group("body")):
            try:
                module: ModuleType = importlib.import_module(module_name)
            except ImportError:
                continue
            try:
                getattr(module, symbol)
            except ImportError:
                continue
            except AttributeError:
                problems.append(
                    f"{path.relative_to(DOCS_ROOT)}: {module_name} has no {symbol!r}"
                )
    return problems


@pytest.mark.parametrize("path", _markdown_files(), ids=lambda p: str(p.name))
def test_examples_import_names_that_exist(path: Path) -> None:
    """Fail when an example imports a symbol the SDK does not export."""
    problems: list[str] = _missing_exports(path)
    assert not problems, "\n".join(problems)
