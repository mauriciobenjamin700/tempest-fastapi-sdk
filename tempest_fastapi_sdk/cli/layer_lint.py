"""Static rules for the ``services`` / ``repositories`` / ``routers`` layers.

The ``CLAUDE.md`` that ``tempest new`` writes lists how a service and a
repository are shaped: every service subclasses :class:`BaseService`, a
service module holds one public class and no module-level helper, a
service does not re-declare the ``__init__`` the base already has, and a
repository does not wrap a :class:`BaseRepository` method under another
name. Prose is not a gate — a service built without any of that passed
``tempest check`` clean (#520) — so this module is the gate.

It parses the project with :mod:`ast` and never imports it: the rules
only need the shape of the code, and a project whose imports are broken
should still get the report. Each finding prints as one line,
``path:line: CODE message``, where the message says what to change.

The rules:

* ``SVC001`` — a public class in ``services/`` does not inherit
  ``BaseService``. A base counts when it is ``BaseService`` itself, a
  ``*Service`` class imported from ``tempest_fastapi_sdk`` (the SDK's own
  services, such as ``UserAuthService``), or another class in the
  project's ``services/`` that passes this rule. A service with no
  repository at all — a static catalogue read from a file — opts out with
  the :data:`NO_BASE_SERVICE_MARKER` comment on its ``class`` line.
* ``SVC002`` — a module in ``services/`` defines more than one public
  class, or any module-level function. One finding per extra class and
  per function.
* ``SVC003`` — a service ``__init__`` whose body only calls
  ``super().__init__`` with its own parameters and/or stores
  ``self.repository = <parameter>``.
* ``REPO001`` — a repository method whose body only returns a call to a
  :class:`BaseRepository` method on ``self``. A method that overrides a
  base name (``delete`` forwarding to ``soft_delete``) is a policy, not a
  wrapper, and is left alone.
* ``ENUM001`` — a public class anywhere in the source root that inherits
  ``BaseStrEnum`` (directly, or through a project class that does) and
  whose name does not end in ``Enum`` (#518). Renaming is free on the
  database side: ``enum_type_name`` drops one trailing ``Enum``, so
  ``OrderStatus`` and ``OrderStatusEnum`` both map to
  ``order_status_enum``.
* ``RTR001`` — a module in ``routers/`` calls a ``*Controller`` or
  ``*Service`` class (a name that starts upper case and ends in one of
  those suffixes), anywhere in the module: in a route body, in a helper
  provider, at module level. The controller comes from a provider in
  ``api/dependencies/controllers.py`` and reaches the route through
  ``Depends``; a call that genuinely has to stay opts out with the
  :data:`ROUTER_BUILDS_MARKER` comment on its line.
* ``RTR002`` — a route function in ``routers/`` (decorated with
  ``@<router>.get`` / ``post`` / ``put`` / ``patch`` / ``delete`` /
  ``head`` / ``options`` / ``trace`` / ``api_route``) whose return
  annotation or ``response_model=`` names an ORM model, at any depth of a
  subscript or union (``list[ProductModel]``, ``ProductModel | None``).
  A name is an ORM model when it is imported from a module with a
  ``models`` segment in its dotted path (``src.db.models``,
  ``..models.product``), or when it is a class defined in a ``models/``
  directory of the project and not imported from a ``schemas`` module.
  There is no opt-out: the fix — return the response schema the
  repository maps to — has one shape.

What the router rules cannot see, because they read syntax and never
import the project: a route with no return annotation and no
``response_model``; a model kept outside any ``models/`` directory and
imported from a module without that segment; the SDK's own abstract ORM
bases (``BaseUserModel``) used directly; a controller built through an
alias (``factory = ProductController; factory()``) or a class whose name
does not end in ``Controller`` / ``Service``. Where a permission guard
sits (``@requires`` on the route or on the controller method) is not a
rule: the ``authz`` recipe applies ``@requires`` to the route handler as
its first form, and ``requires`` documents route handlers, controller
and service methods alike, so flagging the route would contradict the
SDK's own documentation.

The opt-out is a comment, not a class attribute, because it is an
instruction to this checker and nothing at runtime reads it; the same
reason ``# noqa`` and ``# type: ignore`` are comments.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

NO_BASE_SERVICE_MARKER: str = "# tempest: no-base-service"
"""Comment on a ``class`` line that exempts a service from ``SVC001``.

For a service that has no repository to hand to ``BaseService`` (a static
catalogue read from a file, a facade over an external API). It also
skips ``SVC003`` for that class, since its ``__init__`` is its own.
"""

ROUTER_BUILDS_MARKER: str = "# tempest: router-builds"
"""Comment on a call line that exempts that call from ``RTR001``.

For a construction that has to happen in the router module — an SDK
router factory that takes an instance at import time, say. The marker
names the exception; review reads why.
"""

ROUTE_DECORATORS: frozenset[str] = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "trace", "api_route"}
)
"""Attribute names that make ``@<router>.<name>(...)`` a route (``RTR002``)."""

CONSTRUCTED_SUFFIXES: tuple[str, ...] = ("Controller", "Service")
"""Class-name suffixes ``RTR001`` reports when called in ``routers/``."""

BASE_REPOSITORY_METHODS: frozenset[str] = frozenset(
    {
        "add",
        "add_all",
        "add_audited",
        "bulk_create_values",
        "bulk_update",
        "bulk_upsert",
        "changes_since",
        "commit",
        "count",
        "cursor_paginate",
        "delete",
        "delete_audited",
        "delete_batch",
        "delete_many",
        "exists",
        "exists_excluding",
        "explain",
        "first",
        "flush",
        "full_text_condition",
        "full_text_search",
        "get",
        "get_by_id",
        "get_or_none",
        "list",
        "map_to_model",
        "map_to_response",
        "map_to_schema",
        "paginate",
        "record_event",
        "resolve",
        "restore",
        "rollback",
        "save_with_outbox",
        "savepoint",
        "search",
        "search_condition",
        "snapshot",
        "soft_delete",
        "transaction",
        "update",
        "update_audited",
        "update_many",
        "update_returning",
    }
)
"""Public methods of :class:`tempest_fastapi_sdk.BaseRepository`.

Kept as a literal so the checker never imports SQLAlchemy;
``tests/cli/test_layer_lint.py`` compares it with the class, so a method
added to the base shows up as a failing test instead of a wrapper this
rule stops seeing.
"""

SOURCE_ROOTS: tuple[str, ...] = ("src", "app")
"""Directory names a project keeps its code under."""

SERVICES_DIR: str = "services"
"""Directory name whose modules the ``SVC*`` rules read."""

REPOSITORIES_DIR: str = "repositories"
"""Directory name whose modules the ``REPO*`` rules read."""

ENUM_NAME_SUFFIX: str = "Enum"
"""Suffix every ``BaseStrEnum`` subclass name ends in (``ENUM001``).

The same string :data:`tempest_fastapi_sdk.db.enums.ENUM_CLASS_SUFFIX`
strips before naming the database type; kept as a literal so the checker
never imports SQLAlchemy, and pinned to that constant by
``tests/cli/test_layer_lint.py``.
"""

ROUTERS_DIR: str = "routers"
"""Directory name whose modules the ``RTR*`` rules read."""

MODELS_DIR: str = "models"
"""Directory name whose classes ``RTR002`` treats as ORM models.

Also the module path segment that marks an import as a model
(``from src.db.models import ProductModel``).
"""

SCHEMAS_SEGMENT: str = "schemas"
"""Module path segment that marks an import as a schema, never a model."""

STR_ENUM_BASE: str = "BaseStrEnum"
"""The SDK base class whose subclasses ``ENUM001`` reads."""

SKIPPED_DIRS: frozenset[str] = frozenset(
    {"tests", "node_modules", "__pycache__", "site-packages"}
)
"""Directory names never descended into, besides any starting with ``.``."""


@dataclass(frozen=True)
class LayerFinding:
    """One rule violation at one place in the source.

    Attributes:
        path (Path): The file the violation is in.
        line (int): The 1-based line of the offending definition.
        code (str): The rule code (``SVC001``, ``REPO001``, ...).
        message (str): What is wrong and what to change.
    """

    path: Path
    line: int
    code: str
    message: str

    def render(self, relative_to: Path | None = None) -> str:
        """Format the finding as ``path:line: CODE message``.

        Args:
            relative_to (Path | None): Directory the path is shown
                relative to, when it lives under it. ``None`` keeps the
                path as stored.

        Returns:
            str: The one-line report.
        """
        shown = self.path
        if relative_to is not None:
            try:
                shown = self.path.resolve().relative_to(relative_to.resolve())
            except ValueError:
                shown = self.path
        return f"{shown}:{self.line}: {self.code} {self.message}"


@dataclass(frozen=True)
class _ParsedModule:
    """A parsed source file and the lines its markers are read from.

    Attributes:
        path (Path): The file.
        tree (ast.Module): Its syntax tree.
        lines (list[str]): Its source, one entry per line.
    """

    path: Path
    tree: ast.Module
    lines: list[str]


def scan_roots(target: Path) -> list[Path]:
    """Return the directories to scan for a check target.

    Args:
        target (Path): The path handed to ``tempest check`` /
            ``tempest lint-layers`` — usually the project root.

    Returns:
        list[Path]: ``target/src`` and/or ``target/app`` when present,
        otherwise ``target`` itself (so ``tempest lint-layers src``
        works too). A file target yields an empty list.
    """
    if not target.is_dir():
        return []
    roots = [target / name for name in SOURCE_ROOTS if (target / name).is_dir()]
    return roots or [target]


def _layer_dirs(root: Path, name: str) -> Iterator[Path]:
    """Yield every directory called ``name`` under ``root``.

    Hidden directories and the ones in :data:`SKIPPED_DIRS` are not
    descended into, so a virtualenv inside the target is never read.

    Args:
        root (Path): Directory to walk.
        name (str): Directory name to look for.

    Yields:
        Path: Each matching directory, in sorted order.
    """
    if root.name == name:
        yield root
        return
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name in SKIPPED_DIRS:
            continue
        yield from _layer_dirs(child, name)


def _source_files(root: Path) -> Iterator[Path]:
    """Yield every ``.py`` file under ``root``, skipping what is never source.

    Hidden directories and the ones in :data:`SKIPPED_DIRS` are not
    descended into, the same way :func:`_layer_dirs` walks.

    Args:
        root (Path): Directory to walk.

    Yields:
        Path: Each Python file, in sorted order.
    """
    for child in sorted(root.iterdir()):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            if child.name not in SKIPPED_DIRS:
                yield from _source_files(child)
        elif child.suffix == ".py":
            yield child


def _python_files(directories: Iterable[Path]) -> list[Path]:
    """List the ``.py`` files under the given directories.

    Args:
        directories (Iterable[Path]): Layer directories.

    Returns:
        list[Path]: Sorted, de-duplicated file paths.
    """
    files: set[Path] = set()
    for directory in directories:
        files.update(
            path
            for path in directory.rglob("*.py")
            if not any(part.startswith(".") for part in path.parts)
        )
    return sorted(files)


def _parse(path: Path) -> _ParsedModule | None:
    """Parse one file, skipping it when it is not valid Python.

    A file that does not parse is ``ruff``'s finding, not this checker's,
    and ``tempest check`` runs ``ruff`` first.

    Args:
        path (Path): The file.

    Returns:
        _ParsedModule | None: The parsed module, or ``None`` on a syntax
        or decoding error.
    """
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError, ValueError):
        return None
    return _ParsedModule(path=path, tree=tree, lines=source.splitlines())


def _public_classes(module: ast.Module) -> list[ast.ClassDef]:
    """Return the module's top-level classes whose name is public.

    Args:
        module (ast.Module): The parsed module.

    Returns:
        list[ast.ClassDef]: Classes whose name does not start with ``_``.
    """
    return [
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    ]


def _base_name(expression: ast.expr) -> str | None:
    """Return the class name a base expression refers to.

    Args:
        expression (ast.expr): One entry of ``ClassDef.bases`` —
            ``Name``, ``module.Name`` or either subscripted.

    Returns:
        str | None: The bare class name, or ``None`` for anything else.
    """
    if isinstance(expression, ast.Subscript):
        expression = expression.value
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        return expression.attr
    return None


def _sdk_imports(module: ast.Module) -> dict[str, str]:
    """Map local names imported from ``tempest_fastapi_sdk`` to their origin.

    Args:
        module (ast.Module): The parsed module.

    Returns:
        dict[str, str]: Local name (after ``as``) to the imported name.
    """
    names: dict[str, str] = {}
    for node in ast.walk(module):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if node.module.split(".")[0] != "tempest_fastapi_sdk":
            continue
        for alias in node.names:
            names[alias.asname or alias.name] = alias.name
    return names


def _has_marker(parsed: _ParsedModule, node: ast.ClassDef) -> bool:
    """Tell whether a class header carries :data:`NO_BASE_SERVICE_MARKER`.

    The header runs from the ``class`` keyword to the line before the
    first body statement, so a base list split across lines works.

    Args:
        parsed (_ParsedModule): The module the class is in.
        node (ast.ClassDef): The class.

    Returns:
        bool: True when any header line holds the marker.
    """
    last = max(node.lineno, node.body[0].lineno - 1)
    header = parsed.lines[node.lineno - 1 : last]
    return any(NO_BASE_SERVICE_MARKER in line for line in header)


def _accepted_services(modules: list[_ParsedModule]) -> set[str]:
    """Resolve which service class names inherit ``BaseService``.

    Iterates to a fixed point so a service extending another project
    service is accepted once its parent is, whatever the file order.

    Args:
        modules (list[_ParsedModule]): Every module in ``services/``.

    Returns:
        set[str]: Names of the project classes that pass ``SVC001``.
    """
    candidates: list[tuple[str, list[str]]] = []
    accepted: set[str] = set()
    for parsed in modules:
        sdk_names = _sdk_imports(parsed.tree)
        for node in _public_classes(parsed.tree):
            bases: list[str] = []
            for expression in node.bases:
                name = _base_name(expression)
                if name is None:
                    continue
                original = sdk_names.get(name, name)
                if original == "BaseService" or (
                    name in sdk_names and original.endswith("Service")
                ):
                    accepted.add(node.name)
                bases.append(name)
            candidates.append((node.name, bases))
    changed = True
    while changed:
        changed = False
        for name, bases in candidates:
            if name not in accepted and any(base in accepted for base in bases):
                accepted.add(name)
                changed = True
    return accepted


def _parameter_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """List a function's parameter names, ``self`` excluded.

    Args:
        function (ast.FunctionDef | ast.AsyncFunctionDef): The function.

    Returns:
        list[str]: Every named parameter after the first positional one,
        plus ``*args`` / ``**kwargs`` names.
    """
    arguments = function.args
    positional = [*arguments.posonlyargs, *arguments.args][1:]
    names = [argument.arg for argument in [*positional, *arguments.kwonlyargs]]
    for variadic in (arguments.vararg, arguments.kwarg):
        if variadic is not None:
            names.append(variadic.arg)
    return names


def _is_docstring(statement: ast.stmt) -> bool:
    """Tell whether a statement is a bare string literal.

    Args:
        statement (ast.stmt): The statement.

    Returns:
        bool: True for a docstring-shaped expression statement.
    """
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _forwarded(call: ast.Call, parameters: set[str]) -> bool:
    """Tell whether every argument of a call is a parameter, unchanged.

    Args:
        call (ast.Call): The call.
        parameters (set[str]): The enclosing function's parameter names.

    Returns:
        bool: True when each positional, ``*`` / ``**`` and keyword value
        is a bare reference to one of ``parameters``.
    """
    values: list[ast.expr] = [
        argument.value if isinstance(argument, ast.Starred) else argument
        for argument in call.args
    ]
    values.extend(keyword.value for keyword in call.keywords)
    return all(
        isinstance(value, ast.Name) and value.id in parameters for value in values
    )


def _is_super_init(statement: ast.stmt, parameters: set[str]) -> bool:
    """Tell whether a statement is ``super().__init__(<parameters>)``.

    Also matches the explicit ``BaseService.__init__(self, ...)`` form.

    Args:
        statement (ast.stmt): The statement.
        parameters (set[str]): The ``__init__`` parameter names.

    Returns:
        bool: True for a base-initialiser call that forwards unchanged.
    """
    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
        return False
    call = statement.value
    function = call.func
    if not isinstance(function, ast.Attribute) or function.attr != "__init__":
        return False
    receiver = function.value
    if (
        isinstance(receiver, ast.Call)
        and isinstance(receiver.func, ast.Name)
        and receiver.func.id == "super"
    ):
        return _forwarded(call, parameters)
    if _base_name(receiver) == "BaseService" and call.args:
        first = call.args[0]
        if isinstance(first, ast.Name) and first.id == "self":
            trimmed = ast.Call(
                func=function, args=call.args[1:], keywords=call.keywords
            )
            return _forwarded(trimmed, parameters)
    return False


def _is_repository_store(statement: ast.stmt, parameters: set[str]) -> bool:
    """Tell whether a statement is ``self.repository = <parameter>``.

    Args:
        statement (ast.stmt): The statement (annotated or not).
        parameters (set[str]): The ``__init__`` parameter names.

    Returns:
        bool: True for a plain store of a parameter into
        ``self.repository``.
    """
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        target: ast.expr = statement.targets[0]
        value: ast.expr | None = statement.value
    elif isinstance(statement, ast.AnnAssign):
        target, value = statement.target, statement.value
    else:
        return False
    return (
        isinstance(target, ast.Attribute)
        and target.attr == "repository"
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
        and isinstance(value, ast.Name)
        and value.id in parameters
    )


def _redundant_init(node: ast.ClassDef) -> ast.FunctionDef | None:
    """Return the class ``__init__`` when it repeats what ``BaseService`` does.

    Args:
        node (ast.ClassDef): The service class.

    Returns:
        ast.FunctionDef | None: The ``__init__`` when every statement
        besides the docstring forwards to the base initialiser or stores
        a parameter as ``self.repository``; ``None`` otherwise.
    """
    for statement in node.body:
        if not isinstance(statement, ast.FunctionDef) or statement.name != "__init__":
            continue
        parameters = set(_parameter_names(statement))
        body = [line for line in statement.body if not _is_docstring(line)]
        if body and all(
            _is_super_init(line, parameters) or _is_repository_store(line, parameters)
            for line in body
        ):
            return statement
    return None


def _check_services(modules: list[_ParsedModule]) -> list[LayerFinding]:
    """Apply ``SVC001`` to ``SVC003`` to the ``services/`` modules.

    Args:
        modules (list[_ParsedModule]): Every module in ``services/``.

    Returns:
        list[LayerFinding]: The violations, in file and line order.
    """
    accepted = _accepted_services(modules)
    findings: list[LayerFinding] = []
    for parsed in modules:
        classes = _public_classes(parsed.tree)
        for node in classes:
            opted_out = _has_marker(parsed, node)
            if node.name not in accepted and not opted_out:
                findings.append(
                    LayerFinding(
                        path=parsed.path,
                        line=node.lineno,
                        code="SVC001",
                        message=(
                            f"`{node.name}` does not inherit BaseService; subclass "
                            f"`BaseService[Repository, ResponseSchema]` and drop "
                            f"what it reimplements. A service with no repository "
                            f"opts out with `{NO_BASE_SERVICE_MARKER}` on the "
                            f"class line; a type that is not a service belongs "
                            f"in schemas/ or core/."
                        ),
                    )
                )
            init = None if opted_out else _redundant_init(node)
            if init is not None:
                findings.append(
                    LayerFinding(
                        path=parsed.path,
                        line=init.lineno,
                        code="SVC003",
                        message=(
                            f"`{node.name}.__init__` only stores the repository, "
                            f"which BaseService already does; delete it."
                        ),
                    )
                )
        for extra in classes[1:]:
            findings.append(
                LayerFinding(
                    path=parsed.path,
                    line=extra.lineno,
                    code="SVC002",
                    message=(
                        f"`{extra.name}` shares this module with "
                        f"`{classes[0].name}`; one public class per module in "
                        f"services/ — move it to its own file."
                    ),
                )
            )
        for statement in parsed.tree.body:
            if not isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if statement.name.startswith("__") and statement.name.endswith("__"):
                continue
            findings.append(
                LayerFinding(
                    path=parsed.path,
                    line=statement.lineno,
                    code="SVC002",
                    message=(
                        f"module-level function `{statement.name}` in services/; "
                        f"move it to utils/, or make it a method of the service "
                        f"that uses it."
                    ),
                )
            )
    return findings


def _forwarded_base_method(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str | None:
    """Return the ``BaseRepository`` method a repository method only wraps.

    Args:
        function (ast.FunctionDef | ast.AsyncFunctionDef): The method.

    Returns:
        str | None: The base method name when the body, docstring aside,
        is a single ``return [await] self.<base method>(...)``; ``None``
        otherwise.
    """
    body = [line for line in function.body if not _is_docstring(line)]
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return None
    value = body[0].value
    if isinstance(value, ast.Await):
        value = value.value
    if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Attribute):
        return None
    receiver = value.func.value
    if not isinstance(receiver, ast.Name) or receiver.id != "self":
        return None
    if value.func.attr not in BASE_REPOSITORY_METHODS:
        return None
    return value.func.attr


def _check_repositories(modules: list[_ParsedModule]) -> list[LayerFinding]:
    """Apply ``REPO001`` to the ``repositories/`` modules.

    Args:
        modules (list[_ParsedModule]): Every module in ``repositories/``.

    Returns:
        list[LayerFinding]: The violations, in file and line order.
    """
    findings: list[LayerFinding] = []
    for parsed in modules:
        for node in parsed.tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            own = {
                statement.name
                for statement in node.body
                if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef)
            }
            for statement in node.body:
                if not isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if statement.name in BASE_REPOSITORY_METHODS:
                    continue
                target = _forwarded_base_method(statement)
                if target is None or target in own:
                    continue
                findings.append(
                    LayerFinding(
                        path=parsed.path,
                        line=statement.lineno,
                        code="REPO001",
                        message=(
                            f"`{node.name}.{statement.name}` only forwards to "
                            f"BaseRepository.{target}; delete it and call "
                            f"`repository.{target}(...)` with those arguments at "
                            f"the call site."
                        ),
                    )
                )
    return findings


def _check_enums(modules: list[_ParsedModule]) -> list[LayerFinding]:
    """Apply ``ENUM001`` to every module of the source root.

    A class is a string enum when a base resolves to ``BaseStrEnum`` —
    under any alias it was imported with — or to a project class that
    already is one; the set is grown to a fixed point so file order does
    not matter. Only public classes are reported: a private intermediate
    base names no database type.

    Args:
        modules (list[_ParsedModule]): Every module under the scan roots.

    Returns:
        list[LayerFinding]: The violations, in file and line order.
    """
    classes: list[tuple[_ParsedModule, ast.ClassDef, list[str]]] = []
    str_enums: set[str] = set()
    for parsed in modules:
        sdk_names = _sdk_imports(parsed.tree)
        for node in parsed.tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [
                name
                for expression in node.bases
                if (name := _base_name(expression)) is not None
            ]
            if any(sdk_names.get(base, base) == STR_ENUM_BASE for base in bases):
                str_enums.add(node.name)
            classes.append((parsed, node, bases))
    changed = True
    while changed:
        changed = False
        for _parsed, node, bases in classes:
            if node.name not in str_enums and any(base in str_enums for base in bases):
                str_enums.add(node.name)
                changed = True
    findings: list[LayerFinding] = []
    for parsed, node, _bases in classes:
        if node.name.startswith("_") or node.name not in str_enums:
            continue
        if node.name.endswith(ENUM_NAME_SUFFIX):
            continue
        expected = f"{node.name}{ENUM_NAME_SUFFIX}"
        findings.append(
            LayerFinding(
                path=parsed.path,
                line=node.lineno,
                code="ENUM001",
                message=(
                    f"`{node.name}` inherits BaseStrEnum; rename it to "
                    f"`{expected}`. The database type name does not change "
                    f"(enum_type_name drops one trailing `Enum`), so no "
                    f"migration is needed."
                ),
            )
        )
    return findings


def _call_name(call: ast.Call) -> str | None:
    """Return the bare name a call's callee refers to.

    Args:
        call (ast.Call): The call.

    Returns:
        str | None: ``Name`` for ``Name(...)`` and ``attr`` for
        ``module.attr(...)``; ``None`` for anything else.
    """
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def _is_constructed_class(name: str) -> bool:
    """Tell whether a callee name is a controller or service class.

    Args:
        name (str): The bare callee name.

    Returns:
        bool: True for a name that starts upper case and ends in a suffix
        of :data:`CONSTRUCTED_SUFFIXES`. ``get_product_service`` is a
        provider, not a class, and is left alone.
    """
    return name[:1].isupper() and name.endswith(CONSTRUCTED_SUFFIXES)


def _spans_marker(parsed: _ParsedModule, node: ast.expr, marker: str) -> bool:
    """Tell whether any source line an expression spans holds ``marker``.

    Args:
        parsed (_ParsedModule): The module the expression is in.
        node (ast.expr): The expression.
        marker (str): The opt-out comment.

    Returns:
        bool: True when a line from the expression's first line to its
        last holds the marker.
    """
    last = node.end_lineno or node.lineno
    return any(marker in line for line in parsed.lines[node.lineno - 1 : last])


def _check_router_constructions(modules: list[_ParsedModule]) -> list[LayerFinding]:
    """Apply ``RTR001`` to the ``routers/`` modules.

    Args:
        modules (list[_ParsedModule]): Every module in ``routers/``.

    Returns:
        list[LayerFinding]: The violations, in file and line order.
    """
    findings: list[LayerFinding] = []
    for parsed in modules:
        for node in ast.walk(parsed.tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name is None or not _is_constructed_class(name):
                continue
            if _spans_marker(parsed, node, ROUTER_BUILDS_MARKER):
                continue
            provider = "controllers" if name.endswith("Controller") else "services"
            findings.append(
                LayerFinding(
                    path=parsed.path,
                    line=node.lineno,
                    code="RTR001",
                    message=(
                        f"`{name}(...)` is built inside a router; move the "
                        f"construction to a provider in "
                        f"api/dependencies/{provider}.py and receive it in the "
                        f"route as `Depends(get_...)`. A call that has to stay "
                        f"opts out with `{ROUTER_BUILDS_MARKER}` on its line."
                    ),
                )
            )
    return findings


def _module_has_segment(module: str | None, segment: str) -> bool:
    """Tell whether a dotted module path has ``segment`` as one of its parts.

    Args:
        module (str | None): ``ImportFrom.module`` (``None`` for
            ``from . import x``).
        segment (str): The part to look for.

    Returns:
        bool: True when a dot-separated part equals ``segment``.
    """
    return module is not None and segment in module.split(".")


def _model_names(parsed: _ParsedModule, project_models: set[str]) -> set[str]:
    """Resolve which names of a router module refer to ORM models.

    Args:
        parsed (_ParsedModule): The router module.
        project_models (set[str]): Public classes defined in the
            project's ``models/`` directories.

    Returns:
        set[str]: Local names imported from a module with a ``models``
        segment, plus every project model name this module does not
        import from a ``schemas`` module.
    """
    from_models: set[str] = set()
    from_schemas: set[str] = set()
    for node in ast.walk(parsed.tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            local = alias.asname or alias.name
            if _module_has_segment(node.module, MODELS_DIR):
                from_models.add(local)
            elif _module_has_segment(node.module, SCHEMAS_SEGMENT):
                from_schemas.add(local)
    return from_models | (project_models - from_schemas)


def _annotation_names(expression: ast.expr | None) -> Iterator[str]:
    """Yield every class name an annotation mentions, at any depth.

    Descends into subscripts (``list[X]``, ``Annotated[X, ...]``), unions
    written with ``|``, the tuple of a multi-argument subscript and
    string annotations, which are parsed.

    Args:
        expression (ast.expr | None): The annotation.

    Yields:
        str: The bare name of each ``Name`` and ``Attribute`` node.
    """
    if expression is None:
        return
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        try:
            parsed = ast.parse(expression.value, mode="eval")
        except SyntaxError:
            return
        yield from _annotation_names(parsed.body)
    elif isinstance(expression, ast.Name):
        yield expression.id
    elif isinstance(expression, ast.Attribute):
        yield expression.attr
    elif isinstance(expression, ast.Subscript):
        yield from _annotation_names(expression.value)
        yield from _annotation_names(expression.slice)
    elif isinstance(expression, ast.BinOp):
        yield from _annotation_names(expression.left)
        yield from _annotation_names(expression.right)
    elif isinstance(expression, ast.Tuple | ast.List):
        for element in expression.elts:
            yield from _annotation_names(element)


def _route_decorator(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.Call | None:
    """Return the ``@<router>.<method>(...)`` decorator of a route function.

    Args:
        function (ast.FunctionDef | ast.AsyncFunctionDef): The function.

    Returns:
        ast.Call | None: The first decorator call whose attribute is in
        :data:`ROUTE_DECORATORS`; ``None`` when the function is no route.
    """
    for decorator in function.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr in ROUTE_DECORATORS
        ):
            return decorator
    return None


def _check_router_returns(
    modules: list[_ParsedModule], project_models: set[str]
) -> list[LayerFinding]:
    """Apply ``RTR002`` to the ``routers/`` modules.

    Args:
        modules (list[_ParsedModule]): Every module in ``routers/``.
        project_models (set[str]): Public classes defined in the
            project's ``models/`` directories.

    Returns:
        list[LayerFinding]: The violations, in file and line order.
    """
    findings: list[LayerFinding] = []
    for parsed in modules:
        models = _model_names(parsed, project_models)
        if not models:
            continue
        for function in ast.walk(parsed.tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            decorator = _route_decorator(function)
            if decorator is None:
                continue
            sources: list[tuple[str, ast.expr | None]] = [
                ("return annotation", function.returns),
                *(
                    ("response_model=", keyword.value)
                    for keyword in decorator.keywords
                    if keyword.arg == "response_model"
                ),
            ]
            reported: set[str] = set()
            for where, expression in sources:
                for name in _annotation_names(expression):
                    if name not in models or name in reported:
                        continue
                    reported.add(name)
                    findings.append(
                        LayerFinding(
                            path=parsed.path,
                            line=function.lineno,
                            code="RTR002",
                            message=(
                                f"route `{function.name}` returns the ORM model "
                                f"`{name}` ({where}); return the response schema "
                                f"the repository maps it to (`map_to_response`) "
                                f"and annotate the route with that schema."
                            ),
                        )
                    )
    return findings


def lint_layers(target: Path) -> list[LayerFinding]:
    """Run every layer rule over a project.

    Args:
        target (Path): The project root (or its ``src`` / ``app``
            directory). Layer directories are found under
            :func:`scan_roots`.

    Returns:
        list[LayerFinding]: Every violation, sorted by file and line.
        Empty for a clean project and for one with no ``services/``,
        ``repositories/`` or ``routers/`` directory and no
        ``BaseStrEnum`` subclass.
    """
    roots = scan_roots(target)
    services: list[Path] = []
    repositories: list[Path] = []
    routers: list[Path] = []
    models: list[Path] = []
    for root in roots:
        services.extend(_layer_dirs(root, SERVICES_DIR))
        repositories.extend(_layer_dirs(root, REPOSITORIES_DIR))
        routers.extend(_layer_dirs(root, ROUTERS_DIR))
        models.extend(_layer_dirs(root, MODELS_DIR))
    service_modules = [
        parsed for path in _python_files(services) if (parsed := _parse(path))
    ]
    repository_modules = [
        parsed for path in _python_files(repositories) if (parsed := _parse(path))
    ]
    router_modules = [
        parsed for path in _python_files(routers) if (parsed := _parse(path))
    ]
    project_models = {
        node.name
        for path in _python_files(models)
        if (parsed := _parse(path))
        for node in _public_classes(parsed.tree)
    }
    source_modules = [
        parsed
        for root in roots
        for path in _source_files(root)
        if (parsed := _parse(path))
    ]
    findings = [
        *_check_services(service_modules),
        *_check_repositories(repository_modules),
        *_check_enums(source_modules),
        *_check_router_constructions(router_modules),
        *_check_router_returns(router_modules, project_models),
    ]
    return sorted(findings, key=lambda item: (str(item.path), item.line, item.code))


def run_lint_layers(target: str, *, stream: TextIO | None = None) -> int:
    """Print the layer findings for ``target`` and return an exit code.

    This is the callable ``tempest check`` runs as its ``lint-layers``
    step and ``tempest lint-layers`` runs on its own.

    Args:
        target (str): The path to check, ``.`` for the current project.
        stream (TextIO | None): Where the report goes. ``None`` means
            standard output.

    Returns:
        int: ``1`` when any rule is violated, ``0`` otherwise.
    """
    output = stream or sys.stdout
    findings = lint_layers(Path(target))
    for finding in findings:
        output.write(finding.render(relative_to=Path.cwd()) + "\n")
    if findings:
        output.write(f"{len(findings)} layer finding(s).\n")
        return 1
    output.write("Layer rules: no findings.\n")
    return 0


__all__: list[str] = [
    "BASE_REPOSITORY_METHODS",
    "CONSTRUCTED_SUFFIXES",
    "ENUM_NAME_SUFFIX",
    "MODELS_DIR",
    "NO_BASE_SERVICE_MARKER",
    "REPOSITORIES_DIR",
    "ROUTERS_DIR",
    "ROUTER_BUILDS_MARKER",
    "ROUTE_DECORATORS",
    "SCHEMAS_SEGMENT",
    "SERVICES_DIR",
    "SKIPPED_DIRS",
    "SOURCE_ROOTS",
    "STR_ENUM_BASE",
    "LayerFinding",
    "lint_layers",
    "run_lint_layers",
    "scan_roots",
]
