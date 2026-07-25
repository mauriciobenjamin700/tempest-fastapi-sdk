"""Write the missing error declarations back into the routes that need them.

:mod:`tempest_fastapi_sdk.cli.openapi_errors` finds routes whose reachable
exceptions are not declared. This module closes the loop: it injects
``responses=error_responses(...)`` into the route decorator, appends to a
declaration that already exists, and adds whatever imports the new names
need.

Three properties make an automatic source rewrite defensible here:

* **Edits are anchored on AST positions, never on a regex.** Every insertion
  point is the closing parenthesis of a call node, so nothing depends on how
  the decorator happens to be formatted, and comments and layout elsewhere in
  the file are untouched.
* **It only ever adds.** Names already declared stay, in their original
  order; the analyzer's ``unreachable`` findings are deliberately *not*
  acted on. Reachability is resolved by call name and cannot see a dynamic
  raise, so removing a declaration on its word could delete a correct one.
* **A dirty working tree is refused.** With a clean tree, ``git diff`` is the
  review and ``git checkout`` is the undo — which is the real safety net for
  a tool that edits code the user wrote.
"""

from __future__ import annotations

import ast
import difflib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from tempest_fastapi_sdk.cli.openapi_errors import RouteFinding

DECLARATION_HELPER: str = "error_responses"
"""The helper injected when a route declares nothing yet.

Chosen over ``@raises`` because it works with a plain ``fastapi.APIRouter``.
``@raises`` is only read by ``TempestAPIRouter``, so injecting it into a
project using a plain router would produce a decorator that silently does
nothing — the worst possible outcome for a tool meant to close a
documentation gap. An existing ``@raises`` **is** extended, since its
presence proves the project already opted into that style.
"""

HELPER_IMPORT: str = "from tempest_fastapi_sdk import error_responses"
"""Import added when a file gains its first ``error_responses`` call."""


class DirtyWorkingTreeError(RuntimeError):
    """Raised when ``--fix`` is asked to edit a repository with local changes."""


@dataclass(slots=True)
class FilePlan:
    """The edits queued for one source file.

    Attributes:
        path (Path): The file to rewrite.
        insertions (list[tuple[int, str]]): ``(byte offset, text)`` pairs,
            applied from the end backwards so earlier offsets stay valid.
        imports (list[str]): Import statements the file is missing.
        routes (list[str]): Human-readable ``METHOD path`` labels, for the
            summary.
        unresolved (list[str]): Exception names that could not be imported,
            so the caller can report them instead of writing a ``NameError``.
    """

    path: Path
    insertions: list[tuple[int, str]] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


def ensure_clean_worktree(root: Path) -> None:
    """Refuse to continue when the repository has uncommitted changes.

    Args:
        root (Path): Directory inside the repository to check.

    Raises:
        DirtyWorkingTreeError: When ``git status --porcelain`` reports
            anything. A clean tree is what makes ``git diff`` a usable
            review of the rewrite and ``git checkout`` a usable undo.
    """
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return
    if completed.stdout.strip():
        raise DirtyWorkingTreeError(
            "the working tree has uncommitted changes. Commit or stash them "
            "first — with a clean tree, `git diff` reviews what this wrote "
            "and `git checkout` undoes it."
        )


def _offset_of(source: str, line: int, col: int) -> int:
    """Convert a 1-indexed ``(line, col)`` AST position to a string offset.

    Args:
        source (str): The file contents.
        line (int): 1-indexed line number.
        col (int): 0-indexed **UTF-8 byte** column, as ``ast`` reports it.

    Returns:
        int: The offset into ``source``.
    """
    start = 0
    for _ in range(line - 1):
        start = source.index("\n", start) + 1
    # ast columns are byte offsets; decode the prefix to land on a character
    # boundary when the line holds non-ASCII text.
    line_end = source.find("\n", start)
    raw = source[start : line_end if line_end != -1 else len(source)]
    return start + len(raw.encode("utf-8")[:col].decode("utf-8", errors="ignore"))


def _module_path(file: Path, root: Path) -> str | None:
    """Return the dotted import path for ``file`` under ``root``.

    Args:
        file (Path): The defining module.
        root (Path): The project root the import is written relative to.

    Returns:
        str | None: A dotted path such as ``"src.core.exceptions"``, or
        ``None`` when the file lies outside ``root`` and no import can be
        derived.
    """
    try:
        relative = file.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts else None


def _existing_names(tree: ast.Module) -> set[str]:
    """Return every name already imported or defined at module level.

    Args:
        tree (ast.Module): The parsed module.

    Returns:
        set[str]: Names the module can already reference without a new
        import.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(
                (alias.asname or alias.name).split(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
    return names


def _import_anchor(source: str, tree: ast.Module) -> int:
    """Return the offset where a new import block should be inserted.

    Args:
        source (str): The file contents.
        tree (ast.Module): The parsed module.

    Returns:
        int: Offset just past the last top-level import, or past the module
        docstring when the file imports nothing.
    """
    last = None
    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            last = node
        elif last is not None:
            break
    if last is not None:
        end = last.end_lineno or last.lineno
        return _offset_of(source, end, 0) + len(
            source.splitlines(keepends=True)[end - 1]
        )
    if tree.body and isinstance(tree.body[0], ast.Expr):
        end = tree.body[0].end_lineno or 1
        return _offset_of(source, end, 0) + len(
            source.splitlines(keepends=True)[end - 1]
        )
    return 0


def plan_file(
    path: Path,
    findings: list[RouteFinding],
    locations: dict[str, Path],
    root: Path,
) -> FilePlan:
    """Build the edit plan for one file.

    Args:
        path (Path): The file to rewrite.
        findings (list[RouteFinding]): Its routes with undocumented
            exceptions.
        locations (dict[str, Path]): Exception class name to defining file.
        root (Path): Project root, used to derive import paths.

    Returns:
        FilePlan: The queued insertions, imports and labels. A route whose
        decorator position is unknown is skipped rather than guessed at.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    plan = FilePlan(path=path)
    known = _existing_names(tree)
    wanted: dict[str, str] = {}

    for finding in findings:
        route = finding.route
        missing = list(finding.undocumented)
        if not missing:
            continue
        joined = ", ".join(missing)
        if route.error_responses_end is not None:
            anchor = route.error_responses_end
            text = joined if route.declares_empty_call else f", {joined}"
        elif route.raises_end is not None:
            anchor = route.raises_end
            text = joined if route.declares_empty_call else f", {joined}"
        elif route.decorator_end is not None:
            anchor = route.decorator_end
            text = f", responses={DECLARATION_HELPER}({joined})"
        else:
            continue
        # The recorded position is the closing parenthesis; insert before it.
        offset = _offset_of(source, anchor[0], anchor[1]) - 1
        plan.insertions.append((offset, text))
        plan.routes.append(f"{route.method} {route.path}")

        needs_helper = route.error_responses_end is None and route.raises_end is None
        if needs_helper and DECLARATION_HELPER not in known:
            wanted[DECLARATION_HELPER] = HELPER_IMPORT
        for name in missing:
            if name in known:
                continue
            defining = locations.get(name)
            module = _module_path(defining, root) if defining else None
            if module is None:
                plan.unresolved.append(name)
                continue
            wanted[name] = f"from {module} import {name}"

    by_module: dict[str, list[str]] = {}
    for name, statement in wanted.items():
        if statement == HELPER_IMPORT:
            by_module.setdefault(HELPER_IMPORT, [])
            continue
        module = statement.split(" import ")[0]
        by_module.setdefault(module, []).append(name)
    for module, names in sorted(by_module.items()):
        if module == HELPER_IMPORT:
            plan.imports.append(HELPER_IMPORT)
        else:
            plan.imports.append(f"{module} import {', '.join(sorted(names))}")
    return plan


def render_file(plan: FilePlan) -> str:
    """Apply a plan to its file's text and return the result.

    Args:
        plan (FilePlan): The queued edits.

    Returns:
        str: The rewritten source. Insertions are applied from the highest
        offset down, so earlier offsets stay valid as the string grows.
    """
    source = plan.path.read_text(encoding="utf-8")
    for offset, text in sorted(plan.insertions, key=lambda item: -item[0]):
        source = source[:offset] + text + source[offset:]
    if plan.imports:
        tree = ast.parse(source)
        anchor = _import_anchor(source, tree)
        block = "".join(f"{line}\n" for line in plan.imports)
        source = source[:anchor] + block + source[anchor:]
    return source


def normalize(source: str, suffix: str = ".py") -> str:
    """Run ``ruff format`` and the import sorter over rewritten source.

    The insertion is a single-line splice, so a decorator that was already
    wrapped comes out over-long, and a new import lands wherever the anchor
    was rather than in sorted position. Both are fixed here rather than by
    the caller, so the ``--dry-run`` diff shows exactly what a real write
    would produce — a preview that differs from the write is worse than no
    preview.

    Args:
        source (str): The rewritten file contents.
        suffix (str): Extension for the temporary file ruff is pointed at.

    Returns:
        str: The formatted source, or ``source`` unchanged when ruff is not
        available. Degrading here is safe: the splice is already valid
        Python, just not pretty.
    """
    import tempfile

    from tempest_fastapi_sdk.cli.lint import _resolve

    runner = _resolve("ruff")
    if runner is None:
        return source
    with tempfile.TemporaryDirectory() as directory:
        scratch = Path(directory) / f"scratch{suffix}"
        scratch.write_text(source, encoding="utf-8")
        subprocess.run(
            [*runner, "check", "--fix", "--quiet", "--select", "I", str(scratch)],
            check=False,
            capture_output=True,
        )
        subprocess.run(
            [*runner, "format", "--quiet", str(scratch)],
            check=False,
            capture_output=True,
        )
        return scratch.read_text(encoding="utf-8")


def unified_diff(path: Path, before: str, after: str, root: Path) -> str:
    """Render a unified diff between two versions of a file.

    Args:
        path (Path): The file, for the diff header.
        before (str): Original contents.
        after (str): Rewritten contents.
        root (Path): Project root, so the header shows a relative path.

    Returns:
        str: The diff, empty when the two are identical.
    """
    try:
        label = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        label = path.as_posix()
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{label}",
            tofile=f"b/{label}",
        )
    )


__all__: list[str] = [
    "DECLARATION_HELPER",
    "HELPER_IMPORT",
    "DirtyWorkingTreeError",
    "FilePlan",
    "ensure_clean_worktree",
    "normalize",
    "plan_file",
    "render_file",
    "unified_diff",
]
