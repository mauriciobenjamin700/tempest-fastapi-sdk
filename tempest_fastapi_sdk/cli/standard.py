"""``tempest generate --standard`` — bring an existing service to the standard.

``tempest new`` writes the service standard (``docs/service-standard.md``)
into a new project. A project scaffolded before it has the gaps the
standard closes: a flat ``core/exceptions.py``, no ``core/constants/``,
``core/enums/`` and ``core/types/`` packages, and none of the guards under
``tests/unit/``. This module fills them in an existing project:

* **Additive pieces** — the packages and guard tests that collide with
  nothing are written from the same templates ``tempest new`` uses. A file
  that already exists is skipped unless ``force`` is set, and a package
  whose flat module already exists (``core/constants.py`` next to a
  would-be ``core/constants/``) is skipped with the reason, since the new
  package would shadow the module in silence.
* **Exception migration** — a flat ``core/exceptions.py`` becomes the
  ``core/exceptions/`` package, one module per HTTP status. The status of
  each class is read by importing the module, not guessed from its bases,
  so a deep hierarchy and a class that overrides ``status_code`` land in
  the module of the status the client actually receives. The package
  ``__init__`` re-exports every public class, so
  ``from src.core.exceptions import X`` keeps working, and the flat module
  is deleted.
* **Frozen contract** — ``tests/unit/core/test_exceptions_contract.py`` is
  written with the status, ``code``, message and ``field`` each exported
  class carries today, read the same way.

The migration refuses, before writing anything, when the flat module holds
anything besides imports, classes and ``__all__`` (a module-level helper or
constant has no status to be filed under), when moving the classes would
create an import cycle between status modules, or when the module cannot
be imported.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from tempest_fastapi_sdk.cli.new import _render, _templates_root
from tempest_fastapi_sdk.cli.src_layers import detect_source_root

STATUS_MODULES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "unprocessable",
    429: "too_many_requests",
    500: "internal",
    502: "bad_gateway",
    503: "service_unavailable",
}
"""Module that holds the exceptions of each HTTP status.

The first five are the modules ``tempest new`` writes. A status outside
this table goes to ``status_<code>.py``; a class that is not an
``AppException`` (no status at all) goes to ``base.py``.
"""

UNCLASSIFIED_MODULE: str = "base"
"""Module for the classes of the flat file that carry no HTTP status."""

ADDITIVE_TEMPLATES: tuple[str, ...] = (
    "src/core/constants/__init__.py",
    "src/core/constants/messages/__init__.py",
    "src/core/constants/messages/errors.py",
    "src/core/constants/messages/successes.py",
    "src/core/enums/__init__.py",
    "src/core/types/__init__.py",
    "tests/unit/__init__.py",
    "tests/unit/architecture/__init__.py",
    "tests/unit/architecture/test_layers.py",
    "tests/unit/architecture/test_openapi_errors.py",
    "tests/unit/architecture/test_schemas.py",
    "tests/unit/core/__init__.py",
)
"""Templates written when missing, relative to the templates root without
the ``.tmpl`` suffix. ``src`` is the scaffold's root and is renamed to the
project's (``app``) on write."""

EXCEPTION_TEMPLATES: tuple[str, ...] = (
    "src/core/exceptions/__init__.py",
    "src/core/exceptions/bad_request.py",
    "src/core/exceptions/conflict.py",
    "src/core/exceptions/forbidden.py",
    "src/core/exceptions/not_found.py",
    "src/core/exceptions/unauthorized.py",
)
"""The exception package of ``tempest new``, written when the project has
no exceptions at all."""

CONTRACT_TEST: str = "tests/unit/core/test_exceptions_contract.py"
"""The guard that freezes the contract of every exported exception."""

_INTROSPECT_SCRIPT: str = """\
import importlib
import inspect
import json
import sys

sys.path.insert(0, sys.argv[1])
from tempest_fastapi_sdk import AppException

module = importlib.import_module(sys.argv[2])
exported = set(getattr(module, "__all__", ()))
found = {}
for name, value in vars(module).items():
    if not inspect.isclass(value) or not issubclass(value, AppException):
        continue
    if value.__module__ != module.__name__ and not value.__module__.startswith(
        module.__name__ + "."
    ):
        continue
    found[name] = {
        "status": value.status_code,
        "code": value.code,
        "message": value.message,
        "field": value.field,
        "has_init": "__init__" in vars(value),
        "exported": name in exported,
    }
print(json.dumps(found))
"""
"""Runs in a child interpreter: lists the ``AppException`` classes a module
defines with the contract each one carries on the class."""


class StandardRefusedError(Exception):
    """Raised when the exception migration cannot run safely.

    Nothing has been written when it is raised; the message names the
    file, the line and what to change by hand.
    """


@dataclass(frozen=True)
class ExceptionContract:
    """The contract an exception class carries, read without raising it.

    Attributes:
        status (int): The HTTP status.
        code (str): The stable error code.
        message (str): The message the client reads.
        field (str | None): The input the error points at.
        has_init (bool): Whether the class defines its own ``__init__``.
        exported (bool): Whether the module lists the class in ``__all__``.
    """

    status: int
    code: str
    message: str
    field: str | None
    has_init: bool
    exported: bool


@dataclass
class StandardReport:
    """What ``generate --standard`` did to the project.

    Attributes:
        written (list[Path]): Files written this run.
        skipped (list[tuple[Path, str]]): Files left alone, with the reason.
        moved (dict[str, str]): Exception class name → status module it
            was moved to.
        removed (list[Path]): Files deleted (the migrated flat module).
        warnings (list[str]): Things the project still has to fix by hand.
    """

    written: list[Path] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    moved: dict[str, str] = field(default_factory=dict)
    removed: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def introspect_exceptions(target: Path, module: str) -> dict[str, ExceptionContract]:
    """Import ``module`` in a child interpreter and read its exceptions.

    A child process keeps the project's modules out of the CLI's own
    ``sys.modules``, so a second read after the migration sees the new
    package rather than the cached flat module.

    Args:
        target (Path): The project root, put first on ``sys.path``.
        module (str): Dotted module to import (``src.core.exceptions``).

    Returns:
        dict[str, ExceptionContract]: Class name → contract, for every
        ``AppException`` subclass the module (or a submodule of the
        package) defines.

    Raises:
        StandardRefusedError: When the module cannot be imported.
    """
    completed = subprocess.run(
        [sys.executable, "-c", _INTROSPECT_SCRIPT, str(target), module],
        cwd=target,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()
        last = detail[-1] if detail else f"exit {completed.returncode}"
        raise StandardRefusedError(f"could not import {module}: {last}")
    raw: dict[str, dict[str, object]] = json.loads(completed.stdout)
    return {
        name: ExceptionContract(
            status=int(str(entry["status"])),
            code=str(entry["code"]),
            message=str(entry["message"]),
            field=None if entry["field"] is None else str(entry["field"]),
            has_init=bool(entry["has_init"]),
            exported=bool(entry["exported"]),
        )
        for name, entry in raw.items()
    }


def status_module(status: int | None) -> str:
    """Return the module name that holds the exceptions of ``status``.

    Args:
        status (int | None): The HTTP status, or ``None`` for a class that
            is not an ``AppException``.

    Returns:
        str: The module name, without ``.py``.
    """
    if status is None:
        return UNCLASSIFIED_MODULE
    return STATUS_MODULES.get(status, f"status_{status}")


def _is_all_target(node: ast.stmt) -> bool:
    """Return whether ``node`` assigns ``__all__``.

    Args:
        node (ast.stmt): A top-level statement.

    Returns:
        bool: True for ``__all__ = ...`` and ``__all__: list[str] = ...``.
    """
    if isinstance(node, ast.Assign):
        return any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        )
    if isinstance(node, ast.AnnAssign):
        return isinstance(node.target, ast.Name) and node.target.id == "__all__"
    return False


def _is_string_expression(node: ast.stmt) -> bool:
    """Return whether ``node`` is a bare string (a docstring).

    Args:
        node (ast.stmt): A top-level statement.

    Returns:
        bool: True for a statement that is only a string literal.
    """
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _check_movable(tree: ast.Module, source: Path) -> None:
    """Refuse a flat module that holds more than imports and classes.

    Args:
        tree (ast.Module): The parsed flat module.
        source (Path): Its path, for the message.

    Raises:
        StandardRefusedError: On the first statement that has no status
            module to go to.
    """
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef)):
            continue
        if _is_all_target(node) or _is_string_expression(node):
            continue
        raise StandardRefusedError(
            f"{source}:{node.lineno}: only imports, classes and __all__ can be "
            "moved into status modules; move this statement out of the "
            "exceptions module (a message into core/constants/messages, a "
            "helper into utils) and run again"
        )


def _bound_name(alias: ast.alias, is_from: bool) -> str:
    """Return the name an import alias binds in the module namespace.

    Args:
        alias (ast.alias): The alias.
        is_from (bool): Whether it comes from ``from x import ...``.

    Returns:
        str: The bound name.
    """
    if alias.asname:
        return alias.asname
    return alias.name if is_from else alias.name.split(".")[0]


def _imports_for(tree: ast.Module, used: set[str]) -> list[str]:
    """Render the imports of the flat module that ``used`` needs.

    A relative import gains one level, since the new module lives one
    package deeper than the flat file did.

    Args:
        tree (ast.Module): The parsed flat module.
        used (set[str]): Names the moved classes reference.

    Returns:
        list[str]: One rendered import statement per kept import.
    """
    lines: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                lines.append(ast.unparse(node))
                continue
            names = [alias for alias in node.names if _bound_name(alias, True) in used]
            if names:
                level = node.level + 1 if node.level else 0
                kept = ast.ImportFrom(module=node.module, names=names, level=level)
                lines.append(ast.unparse(kept))
        elif isinstance(node, ast.Import):
            names = [alias for alias in node.names if _bound_name(alias, False) in used]
            if names:
                lines.append(ast.unparse(ast.Import(names=names)))
    return lines


def _class_source(node: ast.ClassDef, lines: list[str]) -> str:
    """Return the source of a class, decorators included.

    Args:
        node (ast.ClassDef): The class.
        lines (list[str]): The flat module's lines.

    Returns:
        str: The class source, verbatim.
    """
    start = min([node.lineno, *(item.lineno for item in node.decorator_list)])
    end = node.end_lineno if node.end_lineno is not None else node.lineno
    return "\n".join(lines[start - 1 : end])


def _order_modules(dependencies: dict[str, set[str]], source: Path) -> None:
    """Refuse a migration whose status modules would import each other.

    Args:
        dependencies (dict[str, set[str]]): Module → sibling modules it
            imports a base class from.
        source (Path): The flat module, for the message.

    Raises:
        StandardRefusedError: On an import cycle.
    """
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(module: str, path: list[str]) -> None:
        """Depth-first walk that raises on a back edge.

        Args:
            module (str): The module being visited.
            path (list[str]): The modules on the current walk.

        Raises:
            StandardRefusedError: When ``module`` is already on the walk.
        """
        if module in done:
            return
        if module in visiting:
            cycle = " -> ".join([*path, module])
            raise StandardRefusedError(
                f"{source}: moving the classes by status would make the "
                f"status modules import each other ({cycle}); give the shared "
                "base class its own status or move it by hand"
            )
        visiting.add(module)
        for dependency in sorted(dependencies.get(module, set())):
            visit(dependency, [*path, module])
        visiting.discard(module)
        done.add(module)

    for module in sorted(dependencies):
        visit(module, [])


def plan_exception_migration(
    source: Path,
    contracts: dict[str, ExceptionContract],
) -> tuple[dict[str, str], dict[str, str]]:
    """Split a flat exceptions module into one module per HTTP status.

    Args:
        source (Path): The flat ``core/exceptions.py``.
        contracts (dict[str, ExceptionContract]): What
            :func:`introspect_exceptions` read from it.

    Returns:
        tuple[dict[str, str], dict[str, str]]: ``(files, moved)`` — the
        package's file name → content (``__init__.py`` included), and
        class name → module it was moved to.

    Raises:
        StandardRefusedError: When the module holds statements that are
            not imports, classes or ``__all__``, or the split would create
            an import cycle.
    """
    text = source.read_text(encoding="utf-8")
    tree = ast.parse(text)
    _check_movable(tree, source)
    lines = text.splitlines()
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    moved: dict[str, str] = {}
    for node in classes:
        contract = contracts.get(node.name)
        moved[node.name] = status_module(contract.status if contract else None)

    grouped: dict[str, list[ast.ClassDef]] = {}
    for node in classes:
        grouped.setdefault(moved[node.name], []).append(node)

    dependencies: dict[str, set[str]] = {}
    sibling_imports: dict[str, dict[str, set[str]]] = {}
    for module, nodes in grouped.items():
        for node in nodes:
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id in moved:
                    other = moved[base.id]
                    if other != module:
                        dependencies.setdefault(module, set()).add(other)
                        sibling_imports.setdefault(module, {}).setdefault(
                            other, set()
                        ).add(base.id)
    _order_modules(dependencies, source)

    files: dict[str, str] = {}
    for module, nodes in sorted(grouped.items()):
        used = {
            name.id
            for node in nodes
            for name in ast.walk(node)
            if isinstance(name, ast.Name)
        }
        imports = _imports_for(tree, used)
        siblings = [
            f"from .{other} import {', '.join(sorted(names))}"
            for other, names in sorted(sibling_imports.get(module, {}).items())
        ]
        public = sorted(node.name for node in nodes if not node.name.startswith("_"))
        exported = ", ".join(json.dumps(name) for name in public)
        parts = [
            f'"""Exceptions of the ``{module}`` group, moved from '
            '``core/exceptions.py``\nby ``tempest generate --standard``.\n"""',
            "\n".join(imports),
            "\n".join(siblings),
            "\n\n\n".join(_class_source(node, lines) for node in nodes),
            f"__all__: list[str] = [{exported}]",
        ]
        files[f"{module}.py"] = "\n\n\n".join(part for part in parts if part) + "\n"

    docstring = ast.get_docstring(tree, clean=False)
    header = (
        f'"""{docstring}"""'
        if docstring is not None
        else '"""The service\'s exception family, one module per HTTP status."""'
    )
    reexports = [
        f"from .{moved[name]} import {name} as {name}"
        for name in sorted(moved, key=lambda item: (moved[item], item))
        if not name.startswith("_")
    ]
    names = sorted(name for name in moved if not name.startswith("_"))
    all_entries = "".join(f"    {json.dumps(name)},\n" for name in names)
    files["__init__.py"] = (
        f"{header}\n\n"
        + "\n".join(reexports)
        + f"\n\n__all__: list[str] = [\n{all_entries}]\n"
    )
    return files, moved


def render_contract_test(contracts: dict[str, ExceptionContract]) -> str:
    """Render the contract guard with the given contracts frozen in it.

    Args:
        contracts (dict[str, ExceptionContract]): Exported class name →
            the contract to freeze.

    Returns:
        str: The test module.
    """
    template = (_templates_root() / f"{CONTRACT_TEST}.tmpl").read_text(encoding="utf-8")
    entries = "".join(
        f"    {json.dumps(name)}: (\n"
        f"        {contract.status},\n"
        f"        {json.dumps(contract.code, ensure_ascii=False)},\n"
        f"        {json.dumps(contract.message, ensure_ascii=False)},\n"
        f"        {'None' if contract.field is None else json.dumps(contract.field)},\n"
        "    ),\n"
        for name, contract in sorted(contracts.items())
    )
    start = template.index("FROZEN_CONTRACT: dict")
    end = template.index("}\n", start) + 2
    block = (
        "FROZEN_CONTRACT: dict[str, tuple[int, str, str, str | None]] = {\n"
        f"{entries}}}\n"
    )
    return _render(template[:start] + block + template[end:], {})


def _rename_root(content: str, root: str) -> str:
    """Point a template written for ``src`` at the project's root package.

    Args:
        content (str): Rendered template.
        root (str): ``"src"`` or ``"app"``.

    Returns:
        str: The content with ``src`` imports and paths renamed.
    """
    if root == "src":
        return content
    return (
        content.replace("src.", f"{root}.")
        .replace("import src\n", f"import {root}\n")
        .replace('/ "src"', f'/ "{root}"')
        .replace("``src``", f"``{root}``")
    )


def _destination(relative: str, target: Path, root: str) -> Path:
    """Return where a template lands in the project.

    Args:
        relative (str): Template path relative to the templates root,
            without ``.tmpl``.
        target (Path): The project root.
        root (str): The project's source root package.

    Returns:
        Path: The absolute destination.
    """
    parts = relative.split("/")
    if parts[0] == "src":
        parts[0] = root
    return target.joinpath(*parts)


def _shadowed_module(destination: Path, target: Path) -> Path | None:
    """Return the flat module a new package directory would shadow.

    Args:
        destination (Path): Where a template would be written.
        target (Path): The project root.

    Returns:
        Path | None: ``core/constants.py`` for a destination under
        ``core/constants/``, when that file exists; otherwise ``None``.
    """
    for parent in destination.relative_to(target).parents:
        if parent == Path("."):
            continue
        flat = (target / parent).with_suffix(".py")
        if flat.is_file():
            return flat
    return None


def _write_template(
    relative: str,
    target: Path,
    root: str,
    *,
    force: bool,
    report: StandardReport,
) -> bool:
    """Write one template unless it exists or would shadow a module.

    Args:
        relative (str): Template path without ``.tmpl``.
        target (Path): The project root.
        root (str): The project's source root package.
        force (bool): Overwrite an existing file.
        report (StandardReport): Collects what happened.

    Returns:
        bool: True when the file was written.
    """
    destination = _destination(relative, target, root)
    shadowed = _shadowed_module(destination, target)
    if shadowed is not None:
        report.skipped.append(
            (
                destination,
                f"{shadowed.relative_to(target)} exists and would be shadowed",
            )
        )
        return False
    if destination.exists() and not force:
        report.skipped.append((destination, "exists — pass --force to overwrite"))
        return False
    template = _templates_root() / f"{relative}.tmpl"
    content = _render(template.read_text(encoding="utf-8"), {})
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(_rename_root(content, root), encoding="utf-8")
    report.written.append(destination)
    return True


def _format(paths: list[Path], target: Path, report: StandardReport) -> None:
    """Run ``ruff`` over the written Python files, when it is installed.

    The moved imports are rendered one statement per import, unsorted;
    ``ruff check --select I --fix`` and ``ruff format`` put them in the
    project's style. Without ``ruff`` on ``PATH`` the files stay valid
    Python and a warning says so.

    Args:
        paths (list[Path]): Files written this run.
        target (Path): The project root (``ruff`` reads its config there).
        report (StandardReport): Collects the warning.
    """
    python_files = [str(path) for path in paths if path.suffix == ".py"]
    if not python_files:
        return
    ruff = shutil.which("ruff")
    if ruff is None:
        report.warnings.append(
            "ruff is not on PATH: the written files are not sorted or formatted"
        )
        return
    for command in (
        [ruff, "check", "--select", "I", "--fix", "--quiet", *python_files],
        [ruff, "format", "--quiet", *python_files],
    ):
        subprocess.run(command, cwd=target, capture_output=True, check=False)


def apply_standard(target: Path, *, force: bool) -> StandardReport:
    """Bring an existing project to the service standard.

    Args:
        target (Path): The project root.
        force (bool): Overwrite additive files and the contract test when
            they already exist. The exception migration never overwrites:
            it runs only when ``core/exceptions/`` does not exist yet.

    Returns:
        StandardReport: What was written, skipped, moved and removed, and
        what is left to fix by hand.

    Raises:
        StandardRefusedError: When the exception migration cannot run;
            nothing has been written yet.
    """
    root = detect_source_root(target)
    report = StandardReport()
    flat = target / root / "core" / "exceptions.py"
    package = target / root / "core" / "exceptions"

    migration: tuple[dict[str, str], dict[str, str]] | None = None
    if flat.is_file() and not package.exists():
        migration = plan_exception_migration(
            flat, introspect_exceptions(target, f"{root}.core.exceptions")
        )
    elif flat.is_file():
        report.warnings.append(
            f"{flat.relative_to(target)} and {package.relative_to(target)}/ "
            "both exist: the package shadows the module; fold the module "
            "into the package by hand"
        )

    for relative in ADDITIVE_TEMPLATES:
        _write_template(relative, target, root, force=force, report=report)

    if migration is not None:
        files, moved = migration
        package.mkdir(parents=True)
        for name, content in files.items():
            destination = package / name
            destination.write_text(content, encoding="utf-8")
            report.written.append(destination)
        flat.unlink()
        report.removed.append(flat)
        report.moved = moved
    elif not package.exists() and not flat.exists():
        for relative in EXCEPTION_TEMPLATES:
            _write_template(relative, target, root, force=False, report=report)

    _format(report.written, target, report)

    layers_guard = _destination("tests/unit/architecture/test_layers.py", target, root)
    if layers_guard in report.written:
        from tempest_fastapi_sdk.cli.layer_lint import lint_layers

        findings = lint_layers(target)
        if findings:
            report.warnings.append(
                f"{len(findings)} layer finding(s): "
                f"{layers_guard.relative_to(target)} fails until they reach "
                "zero — `tempest lint-layers` lists them"
            )

    contract_path = _destination(CONTRACT_TEST, target, root)
    if package.is_dir() and (force or not contract_path.exists()):
        contracts = introspect_exceptions(target, f"{root}.core.exceptions")
        exported = {name: item for name, item in contracts.items() if item.exported}
        contract_path.parent.mkdir(parents=True, exist_ok=True)
        contract_path.write_text(
            _rename_root(render_contract_test(exported), root), encoding="utf-8"
        )
        report.written.append(contract_path)
        _format([contract_path], target, report)
        for name, item in sorted(exported.items()):
            if item.has_init:
                report.warnings.append(
                    f"{name} defines __init__: the contract test fails until "
                    "code, message and field move into the class body and the "
                    "per-raise data goes in details="
                )
    elif contract_path.exists():
        report.skipped.append((contract_path, "exists — pass --force to overwrite"))

    return report


__all__: list[str] = [
    "ADDITIVE_TEMPLATES",
    "CONTRACT_TEST",
    "EXCEPTION_TEMPLATES",
    "STATUS_MODULES",
    "UNCLASSIFIED_MODULE",
    "ExceptionContract",
    "StandardRefusedError",
    "StandardReport",
    "apply_standard",
    "introspect_exceptions",
    "plan_exception_migration",
    "render_contract_test",
    "status_module",
]
