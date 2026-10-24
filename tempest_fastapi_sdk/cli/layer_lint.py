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
* ``LAY001`` (warning) — a module of a layer directory
  (:data:`LAYER_DIRS`: ``controllers/``, ``services/``,
  ``repositories/``, ``models/``) that defines a second class, of any
  name, or a module-level function. ``services/`` is left to ``SVC002``,
  which already reports the same thing there as an error.
* ``LAY002`` (warning) — a module directly under a layer directory
  instead of under a context subpackage: ``services/order.py`` belongs in
  ``services/<context>/order.py``. Any context name is accepted;
  ``__init__.py`` is exempt.
* ``REPO002`` (warning) — a module in ``repositories/`` imports a
  ``*Repository`` name from another repository module (an absolute import
  whose path has a ``repositories`` segment, or a relative import). A
  repository that needs another table's rows joins in its own query; the
  orchestration between two repositories belongs to a service. A name the
  module only uses as a base class (a project base repository) is left
  alone.
* ``DI001`` (warning) — the ``__init__`` of a class in ``services/`` or
  ``controllers/`` takes a parameter annotated with ``AsyncSession``. The
  session reaches the repository; the service receives the repository.
* ``DI002`` (warning) — the ``__init__`` of a class in ``services/``
  takes a ``*Service`` parameter, or one in ``controllers/`` takes a
  ``*Controller``. Combining two services is a controller's job; a real
  case (two services sharing one transaction) is a declared exception.
* ``DI003`` (warning) — a function or method named ``from_session``
  anywhere in the source root. A class that assembles itself hides its
  graph; assembly lives in the request providers
  (``api/dependencies/``) and, outside a request, in one composition
  root of ``build_*`` functions.
* ``STR001`` (warning) — a function defined inside another function, in
  the source root and in ``tests/``. A callback that needs state becomes
  a callable object (a class with ``__call__``); one that needs an
  argument bound goes through :func:`functools.partial`.
* ``STR002`` (warning) — a module-level function whose name starts with a
  single ``_``, in the source root and in ``tests/``. A helper with no
  home: a method of the class that uses it, a public function in
  ``utils/``, or, in the suite, a fixture.

* ``IMP001`` (warning) — an ``__init__.py`` that imports a module of its
  own package with an absolute import (``from src.services.order import
  OrderService`` inside ``src/services/__init__.py``). The package names
  its neighbours relatively: ``from .order import OrderService``.
* ``IMP002`` (warning) — outside an ``__init__.py``, an absolute import
  of a name from a submodule when an ancestor package already re-exports
  it (``from src.services.order.service import OrderService`` when
  ``src.services`` binds ``OrderService``). Re-exports are resolved
  through ``from .x import *`` too. The suggested package is the top-most
  ancestor that binds the name and does not contain the importing module,
  so a module never imports its own package's ``__init__``; an import
  that would close a cycle anyway is a declared exception.

* ``RTR003`` (warning) — a module in ``routers/`` that builds more than
  one ``APIRouter``; one finding per router after the first. One router
  per file keeps a domain's routes where its name says.
* ``RTR004`` (warning) — a ``prefix`` of ``/api`` (or under it) passed to
  ``APIRouter(...)`` or ``include_router(...)`` anywhere but inside a
  ``build_api_router`` function (``routers/api.py``). The ``/api`` prefix
  is set once; a domain router declares only its own prefix.

``tests/`` is the project's suite: ``<target>/tests``, or the one beside
a source-root target (``tempest lint-layers src`` reads ``./tests``).

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

Every rule has a severity (:data:`RULES`). An ``error`` fails the step; a
``warning`` is printed with ``(warning)`` after its code and does not,
unless the run is strict (``tempest lint-layers --strict`` or
``layers_strict = true`` under ``[tool.tempest]``). A rule enters as a
warning in one release and becomes an error in the next, so a project can
migrate one code at a time with ``tempest lint-layers --rule <CODE>``.

An exception a project keeps on purpose is declared in one place, by
:data:`EXCEPTIONS_PATH` (``tests/architecture_exceptions.py``): a module
level dict named :data:`EXCEPTIONS_NAME` whose keys are
``"<CODE> <path>"`` or ``"<CODE> <path>::<subject>"`` and whose values are
the reason, reviewed in the diff like code. The file is read with
:mod:`ast` as well. ``EXC001`` reports an entry the checker cannot use
(malformed key, unknown code, empty reason) and is always an error;
``EXC002`` warns about an entry that matches no finding, so an exception
does not outlive the code it excused.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TextIO

from tempest_fastapi_sdk.cli.config import load_tempest_table

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

LAYER_DIRS: tuple[str, ...] = ("controllers", "services", "repositories", "models")
"""Layer directories whose modules hold one class under a context package.

Read by ``LAY001`` and ``LAY002``.
"""

CONTROLLERS_DIR: str = "controllers"
"""Directory name whose classes ``DI001`` and ``DI002`` read with services."""

SESSION_TYPE: str = "AsyncSession"
"""Annotation name ``DI001`` reports in a service or controller ``__init__``."""

SELF_ASSEMBLY_METHOD: str = "from_session"
"""Function name ``DI003`` reports."""

ROUTER_CLASS: str = "APIRouter"
"""Callee name ``RTR003`` counts and ``RTR004`` reads the prefix of."""

API_PREFIX: str = "/api"
"""The prefix only :data:`API_ROUTER_BUILDER` may set (``RTR004``)."""

API_ROUTER_BUILDER: str = "build_api_router"
"""The function that mounts every domain router under :data:`API_PREFIX`."""

TESTS_DIR: str = "tests"
"""The suite directory, beside the source root, that ``STR*`` also reads."""

REPOSITORY_SUFFIX: str = "Repository"
"""Name suffix ``REPO002`` treats as a repository class."""

STR_ENUM_BASE: str = "BaseStrEnum"
"""The SDK base class whose subclasses ``ENUM001`` reads."""

SKIPPED_DIRS: frozenset[str] = frozenset(
    {"tests", "node_modules", "__pycache__", "site-packages"}
)
"""Directory names never descended into, besides any starting with ``.``."""

Severity = Literal["error", "warning"]
"""How a finding counts: an ``error`` fails the run, a ``warning`` does not."""


@dataclass(frozen=True)
class LayerRule:
    """One rule the checker knows, with the severity it reports at.

    Attributes:
        code (str): The rule code (``SVC001``, ``EXC002``, ...).
        severity (Severity): ``error`` or ``warning``.
        summary (str): One line on what the rule reports.
    """

    code: str
    severity: Severity
    summary: str


RULES: dict[str, LayerRule] = {
    rule.code: rule
    for rule in (
        LayerRule("SVC001", "error", "public class in services/ without BaseService"),
        LayerRule("SVC002", "error", "second public class or a function in services/"),
        LayerRule("SVC003", "error", "service __init__ only storing the repository"),
        LayerRule("REPO001", "error", "repository method that only wraps the base"),
        LayerRule("ENUM001", "error", "BaseStrEnum subclass not named *Enum"),
        LayerRule("RTR001", "error", "controller or service built inside routers/"),
        LayerRule("RTR002", "error", "route that returns an ORM model"),
        LayerRule("LAY001", "warning", "second class or a function in a layer module"),
        LayerRule("LAY002", "warning", "layer module outside a context package"),
        LayerRule("REPO002", "warning", "repository importing another repository"),
        LayerRule("DI001", "warning", "AsyncSession in a service or controller"),
        LayerRule("DI002", "warning", "collaborator of the same layer injected"),
        LayerRule("DI003", "warning", "from_session self-assembly"),
        LayerRule("STR001", "warning", "function defined inside a function"),
        LayerRule("STR002", "warning", "private module-level function"),
        LayerRule("IMP001", "warning", "absolute import of a neighbour in __init__"),
        LayerRule("IMP002", "warning", "submodule import of a re-exported name"),
        LayerRule("RTR003", "warning", "more than one APIRouter in a module"),
        LayerRule("RTR004", "warning", "/api prefix outside build_api_router"),
        LayerRule("EXC001", "error", "declared exception the checker cannot use"),
        LayerRule("EXC002", "warning", "declared exception that matches no finding"),
    )
}
"""Every rule code, with its severity. ``--rule`` only accepts these."""

EXCEPTIONS_PATH: str = "tests/architecture_exceptions.py"
"""Where a project declares its exceptions, relative to the project root.

Looked up under the check target and, when the target is the source root
(``tempest lint-layers src``), under its parent.
"""

EXCEPTIONS_NAME: str = "ARCHITECTURE_EXCEPTIONS"
"""Name of the module-level dict in :data:`EXCEPTIONS_PATH`."""

SUBJECT_SEPARATOR: str = "::"
"""Separates the path from the subject in an exception key."""

STRICT_CONFIG_KEY: str = "layers_strict"
"""``[tool.tempest]`` key that makes warnings fail ``tempest check``."""


@dataclass(frozen=True)
class LayerFinding:
    """One rule violation at one place in the source.

    Attributes:
        path (Path): The file the violation is in.
        line (int): The 1-based line of the offending definition.
        code (str): The rule code (``SVC001``, ``REPO001``, ...).
        message (str): What is wrong and what to change.
        subject (str | None): The name the finding is about (a class,
            ``Class.method``, a function), which an exception key can
            narrow to after ``::``. ``None`` when there is none.
    """

    path: Path
    line: int
    code: str
    message: str
    subject: str | None = None

    @property
    def severity(self) -> Severity:
        """Return the severity :data:`RULES` gives this finding's code.

        Returns:
            Severity: ``error`` for a code :data:`RULES` does not list.
        """
        rule = RULES.get(self.code)
        return rule.severity if rule is not None else "error"

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
        tag = " (warning)" if self.severity == "warning" else ""
        return f"{shown}:{self.line}: {self.code}{tag} {self.message}"


@dataclass(frozen=True)
class DeclaredException:
    """One entry of the project's :data:`EXCEPTIONS_NAME` dict.

    Attributes:
        key (str): The key as written.
        code (str): The rule code it excuses.
        path (str): The file, POSIX, relative to the project root.
        subject (str | None): The finding subject it narrows to, or
            ``None`` for every finding of ``code`` in ``path``.
        reason (str): Why the exception stands.
        line (int): The key's line in the exceptions file.
    """

    key: str
    code: str
    path: str
    subject: str | None
    reason: str
    line: int

    def matches(self, finding: LayerFinding, relative: str) -> bool:
        """Tell whether this entry excuses a finding.

        Args:
            finding (LayerFinding): The finding.
            relative (str): The finding's path relative to the project
                root, POSIX.

        Returns:
            bool: True when code and path match and the subject, if the
            entry names one, is the finding's.
        """
        return (
            finding.code == self.code
            and relative == self.path
            and (self.subject is None or self.subject == finding.subject)
        )


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
                        subject=node.name,
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
                        subject=f"{node.name}.__init__",
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
                    subject=extra.name,
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
                    subject=statement.name,
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
                        subject=f"{node.name}.{statement.name}",
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
                subject=node.name,
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
                    subject=name,
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
                            subject=function.name,
                            message=(
                                f"route `{function.name}` returns the ORM model "
                                f"`{name}` ({where}); return the response schema "
                                f"the repository maps it to (`map_to_response`) "
                                f"and annotate the route with that schema."
                            ),
                        )
                    )
    return findings


def _check_layer_shape(
    layers: dict[str, list[Path]],
) -> list[LayerFinding]:
    """Apply ``LAY001`` and ``LAY002`` to the layer directories.

    Args:
        layers (dict[str, list[Path]]): Each :data:`LAYER_DIRS` name to
            the directories of that name found under the scan roots.

    Returns:
        list[LayerFinding]: The violations, in file and line order.
    """
    findings: list[LayerFinding] = []
    for name, directories in layers.items():
        for directory in directories:
            for path in _python_files([directory]):
                if path.name == "__init__.py":
                    continue
                if path.parent == directory:
                    findings.append(
                        LayerFinding(
                            path=path,
                            line=1,
                            code="LAY002",
                            message=(
                                f"{name}/{path.name} sits at the root of the "
                                f"layer; move it to {name}/<context>/{path.name} "
                                f"(a subpackage per domain context, re-exported "
                                f"by {name}/__init__.py)."
                            ),
                        )
                    )
                if name == SERVICES_DIR:
                    continue
                parsed = _parse(path)
                if parsed is not None:
                    findings.extend(_extra_members(parsed, name))
    return findings


def _extra_members(parsed: _ParsedModule, layer: str) -> Iterator[LayerFinding]:
    """Yield ``LAY001`` for each class after the first and each function.

    Args:
        parsed (_ParsedModule): A module of a layer directory.
        layer (str): The layer directory name, for the message.

    Yields:
        LayerFinding: One finding per extra member.
    """
    classes = [node for node in parsed.tree.body if isinstance(node, ast.ClassDef)]
    for extra in classes[1:]:
        yield LayerFinding(
            path=parsed.path,
            line=extra.lineno,
            code="LAY001",
            subject=extra.name,
            message=(
                f"`{extra.name}` shares this module with `{classes[0].name}`; "
                f"one class per module in {layer}/ — move it to its own file "
                f"in the same context package."
            ),
        )
    for statement in parsed.tree.body:
        if not isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if statement.name.startswith("__") and statement.name.endswith("__"):
            continue
        yield LayerFinding(
            path=parsed.path,
            line=statement.lineno,
            code="LAY001",
            subject=statement.name,
            message=(
                f"module-level function `{statement.name}` in {layer}/; make it "
                f"a method of the class that uses it, or move it to utils/."
            ),
        )


def _base_names(module: ast.Module) -> set[str]:
    """Collect every name used as a class base anywhere in a module.

    Args:
        module (ast.Module): The parsed module.

    Returns:
        set[str]: The bare base names.
    """
    return {
        name
        for node in ast.walk(module)
        if isinstance(node, ast.ClassDef)
        for expression in node.bases
        if (name := _base_name(expression)) is not None
    }


def _check_repository_imports(modules: list[_ParsedModule]) -> list[LayerFinding]:
    """Apply ``REPO002`` to the ``repositories/`` modules.

    Args:
        modules (list[_ParsedModule]): Every module in ``repositories/``.

    Returns:
        list[LayerFinding]: The violations, in file and line order.
    """
    findings: list[LayerFinding] = []
    for parsed in modules:
        if parsed.path.name == "__init__.py":
            continue
        bases = _base_names(parsed.tree)
        for node in ast.walk(parsed.tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 0 and not _module_has_segment(
                node.module, REPOSITORIES_DIR
            ):
                continue
            for alias in node.names:
                local = alias.asname or alias.name
                if not alias.name.endswith(REPOSITORY_SUFFIX) or local in bases:
                    continue
                findings.append(
                    LayerFinding(
                        path=parsed.path,
                        line=node.lineno,
                        code="REPO002",
                        subject=alias.name,
                        message=(
                            f"imports `{alias.name}` from another repository; a "
                            f"repository reads its own tables (join in the "
                            f"query) and a service combines two repositories."
                        ),
                    )
                )
    return findings


def _init_parameters(node: ast.ClassDef) -> list[ast.arg]:
    """Return the annotated parameters of a class ``__init__``, ``self`` aside.

    Args:
        node (ast.ClassDef): The class.

    Returns:
        list[ast.arg]: Every parameter with an annotation; empty when the
        class declares no ``__init__``.
    """
    for statement in node.body:
        if isinstance(statement, ast.FunctionDef) and statement.name == "__init__":
            arguments = statement.args
            positional = [*arguments.posonlyargs, *arguments.args][1:]
            return [
                argument
                for argument in [*positional, *arguments.kwonlyargs]
                if argument.annotation is not None
            ]
    return []


def _check_injection(
    service_modules: list[_ParsedModule], controller_modules: list[_ParsedModule]
) -> list[LayerFinding]:
    """Apply ``DI001`` and ``DI002`` to services and controllers.

    Args:
        service_modules (list[_ParsedModule]): Every module in
            ``services/``.
        controller_modules (list[_ParsedModule]): Every module in
            ``controllers/``.

    Returns:
        list[LayerFinding]: The violations, in file and line order.
    """
    findings: list[LayerFinding] = []
    for modules, suffix in (
        (service_modules, "Service"),
        (controller_modules, "Controller"),
    ):
        for parsed in modules:
            for node in parsed.tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                for argument in _init_parameters(node):
                    names = set(_annotation_names(argument.annotation))
                    subject = f"{node.name}.{argument.arg}"
                    if SESSION_TYPE in names:
                        findings.append(
                            LayerFinding(
                                path=parsed.path,
                                line=argument.lineno,
                                code="DI001",
                                subject=subject,
                                message=(
                                    f"`{node.name}.__init__` takes the session "
                                    f"(`{argument.arg}`); take the repository "
                                    f"instead and let the provider in "
                                    f"api/dependencies/ build it from the session."
                                ),
                            )
                        )
                    peers = sorted(
                        name
                        for name in names
                        if name.endswith(suffix) and name[:1].isupper()
                    )
                    for peer in peers:
                        findings.append(
                            LayerFinding(
                                path=parsed.path,
                                line=argument.lineno,
                                code="DI002",
                                subject=subject,
                                message=(
                                    f"`{node.name}.__init__` takes `{peer}` "
                                    f"(`{argument.arg}`), a {suffix.lower()} of "
                                    f"the same layer; combine the two one layer "
                                    f"up, or declare the exception with its "
                                    f"reason."
                                ),
                            )
                        )
    return findings


def _check_self_assembly(modules: list[_ParsedModule]) -> list[LayerFinding]:
    """Apply ``DI003`` to every module of the source root.

    Args:
        modules (list[_ParsedModule]): Every module under the scan roots.

    Returns:
        list[LayerFinding]: The violations, in file and line order.
    """
    findings: list[LayerFinding] = []
    for parsed in modules:
        owners: dict[ast.AST, str] = {
            child: node.name
            for node in ast.walk(parsed.tree)
            if isinstance(node, ast.ClassDef)
            for child in node.body
        }
        for node in ast.walk(parsed.tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if node.name != SELF_ASSEMBLY_METHOD:
                continue
            owner = owners.get(node)
            findings.append(
                LayerFinding(
                    path=parsed.path,
                    line=node.lineno,
                    code="DI003",
                    subject=f"{owner}.{node.name}" if owner else node.name,
                    message=(
                        f"`{SELF_ASSEMBLY_METHOD}` assembles the graph inside "
                        f"the class; build it in a provider of api/dependencies/ "
                        f"for a request, or in a `build_*` function of the "
                        f"composition root outside one."
                    ),
                )
            )
    return findings


def project_root(target: Path) -> Path:
    """Return the project root a check target belongs to.

    Args:
        target (Path): The path handed to ``tempest lint-layers``.

    Returns:
        Path: The parent of a source-root target (``src`` / ``app``),
        ``target`` itself otherwise.
    """
    return target.parent if target.name in SOURCE_ROOTS else target


def find_exceptions_file(target: Path) -> Path | None:
    """Locate the project's :data:`EXCEPTIONS_PATH` for a check target.

    Args:
        target (Path): The path handed to ``tempest lint-layers``.

    Returns:
        Path | None: :data:`EXCEPTIONS_PATH` under :func:`project_root`,
        or ``None`` when it does not exist.
    """
    candidate = project_root(target) / EXCEPTIONS_PATH
    return candidate if candidate.is_file() else None


def _nested_functions(
    node: ast.AST, owner: str | None, inside_function: bool
) -> Iterator[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]]:
    """Yield each function defined inside another function, with its path.

    Args:
        node (ast.AST): The node to descend from.
        owner (str | None): The dotted name of the enclosing definitions.
        inside_function (bool): Whether ``node`` sits in a function body.

    Yields:
        tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]: The nested
        function and its dotted name (``outer.inner``).
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            name = f"{owner}.{child.name}" if owner else child.name
            is_function = not isinstance(child, ast.ClassDef)
            if not isinstance(child, ast.ClassDef) and inside_function:
                yield child, name
            yield from _nested_functions(child, name, inside_function or is_function)
        else:
            yield from _nested_functions(child, owner, inside_function)


def _calls_with_function(
    node: ast.AST, function: str | None = None
) -> Iterator[tuple[ast.Call, str | None]]:
    """Yield every call under ``node`` with the function it sits in.

    Args:
        node (ast.AST): The node to descend from.
        function (str | None): The innermost enclosing function name.

    Yields:
        tuple[ast.Call, str | None]: The call and its enclosing function
        name, ``None`` at module or class level.
    """
    for child in ast.iter_child_nodes(node):
        inner = (
            child.name
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
            else function
        )
        if isinstance(child, ast.Call):
            yield child, function
        yield from _calls_with_function(child, inner)


def _assigned_names(module: ast.Module) -> dict[ast.AST, str]:
    """Map each value assigned to a plain name to that name.

    Args:
        module (ast.Module): The parsed module.

    Returns:
        dict[ast.AST, str]: Value node to target name, for ``x = ...`` and
        ``x: T = ...``.
    """
    names: dict[ast.AST, str] = {}
    for node in ast.walk(module):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                names[node.value] = target.id
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and isinstance(node.target, ast.Name)
        ):
            names[node.value] = node.target.id
    return names


def _check_router_count(modules: list[_ParsedModule]) -> list[LayerFinding]:
    """Apply ``RTR003`` to the ``routers/`` modules.

    Args:
        modules (list[_ParsedModule]): Every module in ``routers/``.

    Returns:
        list[LayerFinding]: The violations, in file and line order.
    """
    findings: list[LayerFinding] = []
    for parsed in modules:
        names = _assigned_names(parsed.tree)
        routers = sorted(
            (
                node
                for node, _function in _calls_with_function(parsed.tree)
                if _call_name(node) == ROUTER_CLASS
            ),
            key=lambda node: (node.lineno, node.col_offset),
        )
        for extra in routers[1:]:
            name = names.get(extra)
            findings.append(
                LayerFinding(
                    path=parsed.path,
                    line=extra.lineno,
                    code="RTR003",
                    subject=name,
                    message=(
                        f"a second {ROUTER_CLASS} in this module"
                        f"{f' (`{name}`)' if name else ''}; one router per file "
                        f"in routers/ — move it to its own module."
                    ),
                )
            )
    return findings


def _api_prefix(call: ast.Call) -> str | None:
    """Return the ``prefix=`` of a call when it is :data:`API_PREFIX` or below.

    Args:
        call (ast.Call): The call.

    Returns:
        str | None: The literal prefix, or ``None`` for any other value.
    """
    for keyword in call.keywords:
        if keyword.arg != "prefix":
            continue
        value = keyword.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            return None
        prefix = value.value.rstrip("/")
        if prefix == API_PREFIX or prefix.startswith(f"{API_PREFIX}/"):
            return value.value
    return None


def _check_api_prefix(modules: list[_ParsedModule]) -> list[LayerFinding]:
    """Apply ``RTR004`` to every module of the source root.

    Args:
        modules (list[_ParsedModule]): Every module under the scan roots.

    Returns:
        list[LayerFinding]: The violations, in file and line order.
    """
    findings: list[LayerFinding] = []
    for parsed in modules:
        for call, function in _calls_with_function(parsed.tree):
            if _call_name(call) not in {ROUTER_CLASS, "include_router"}:
                continue
            if function == API_ROUTER_BUILDER:
                continue
            prefix = _api_prefix(call)
            if prefix is None:
                continue
            findings.append(
                LayerFinding(
                    path=parsed.path,
                    line=call.lineno,
                    code="RTR004",
                    subject=function,
                    message=(
                        f"prefix `{prefix}` set outside {API_ROUTER_BUILDER}(); "
                        f"mount the domain routers under `{API_PREFIX}` once, in "
                        f"routers/api.py, and give each router only its own "
                        f"prefix."
                    ),
                )
            )
    return findings


def _dotted_name(path: Path, root: Path) -> str | None:
    """Return the import path of a module relative to the project root.

    Args:
        path (Path): The module file.
        root (Path): The project root.

    Returns:
        str | None: ``src.services.order`` for
        ``src/services/order/__init__.py`` or ``src/services/order.py``;
        ``None`` outside ``root``.
    """
    relative = _relative(path, root)
    if relative is None:
        return None
    parts = list(Path(relative).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or None


def _absolute_module(dotted: str, is_package: bool, node: ast.ImportFrom) -> str | None:
    """Resolve the module an ``import from`` names, relative or not.

    Args:
        dotted (str): The importing module's import path.
        is_package (bool): Whether the importing module is an
            ``__init__.py``.
        node (ast.ImportFrom): The import.

    Returns:
        str | None: The absolute import path, or ``None`` when a relative
        import climbs above the project root.
    """
    if node.level == 0:
        return node.module
    base = dotted.split(".")
    if not is_package:
        base = base[:-1]
    climb = node.level - 1
    if climb > len(base):
        return None
    base = base[: len(base) - climb]
    return ".".join([*base, *(node.module.split(".") if node.module else [])])


@dataclass
class _ExportIndex:
    """The names each project module binds at top level, resolved lazily.

    Attributes:
        modules (dict[str, _ParsedModule]): Import path to parsed module.
        memo (dict[str, set[str]]): Names already resolved per module.
    """

    modules: dict[str, _ParsedModule]
    memo: dict[str, set[str]]

    def exports(self, dotted: str, active: frozenset[str] = frozenset()) -> set[str]:
        """Return the names a module binds, following ``import *``.

        Args:
            dotted (str): The module's import path.
            active (frozenset[str]): Modules being resolved up the stack,
                so a star-import cycle ends instead of recursing.

        Returns:
            set[str]: Every top-level name, imported or defined; empty for
            a module outside the project.
        """
        if dotted in self.memo:
            return self.memo[dotted]
        parsed = self.modules.get(dotted)
        if parsed is None or dotted in active:
            return set()
        is_package = parsed.path.name == "__init__.py"
        names: set[str] = set()
        for statement in parsed.tree.body:
            if isinstance(statement, ast.ImportFrom):
                for alias in statement.names:
                    if alias.name != "*":
                        names.add(alias.asname or alias.name)
                        continue
                    source = _absolute_module(dotted, is_package, statement)
                    if source is not None:
                        names.update(
                            name
                            for name in self.exports(source, active | {dotted})
                            if not name.startswith("_")
                        )
            elif isinstance(statement, ast.Import):
                names.update(
                    alias.asname or alias.name.split(".")[0]
                    for alias in statement.names
                )
            elif isinstance(
                statement, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
            ):
                names.add(statement.name)
            elif isinstance(statement, ast.Assign | ast.AnnAssign):
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                names.update(
                    target.id for target in targets if isinstance(target, ast.Name)
                )
        self.memo[dotted] = names
        return names


def _check_imports(modules: list[_ParsedModule], root: Path) -> list[LayerFinding]:
    """Apply ``IMP001`` and ``IMP002`` to source and test modules.

    Args:
        modules (list[_ParsedModule]): Every module under the scan roots
            and the project's ``tests/``.
        root (Path): The project root import paths are relative to.

    Returns:
        list[LayerFinding]: The violations, in file and line order.
    """
    named = {
        dotted: parsed
        for parsed in modules
        if (dotted := _dotted_name(parsed.path, root)) is not None
    }
    index = _ExportIndex(modules=named, memo={})
    findings: list[LayerFinding] = []
    for dotted, parsed in named.items():
        is_package = parsed.path.name == "__init__.py"
        for node in parsed.tree.body:
            if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
                continue
            module = node.module
            if is_package and module.startswith(f"{dotted}."):
                rest = module[len(dotted) + 1 :]
                findings.append(
                    LayerFinding(
                        path=parsed.path,
                        line=node.lineno,
                        code="IMP001",
                        subject=module,
                        message=(
                            f"`{module}` is a module of this package; import it "
                            f"relatively in __init__.py: `from .{rest} import ...`."
                        ),
                    )
                )
                continue
            if is_package or module not in named:
                continue
            parts = module.split(".")
            for alias in node.names:
                for size in range(2, len(parts)):
                    package = ".".join(parts[:size])
                    if dotted == package or dotted.startswith(f"{package}."):
                        continue
                    if alias.name not in index.exports(package):
                        continue
                    findings.append(
                        LayerFinding(
                            path=parsed.path,
                            line=node.lineno,
                            code="IMP002",
                            subject=alias.name,
                            message=(
                                f"`{alias.name}` is re-exported by `{package}`; "
                                f"import it from there: `from {package} import "
                                f"{alias.name}`."
                            ),
                        )
                    )
                    break
    return findings


def _check_structure(modules: list[_ParsedModule]) -> list[LayerFinding]:
    """Apply ``STR001`` and ``STR002`` to source and test modules.

    Args:
        modules (list[_ParsedModule]): Every module under the scan roots
            and the project's ``tests/``.

    Returns:
        list[LayerFinding]: The violations, in file and line order.
    """
    findings: list[LayerFinding] = []
    for parsed in modules:
        for function, name in _nested_functions(parsed.tree, None, False):
            findings.append(
                LayerFinding(
                    path=parsed.path,
                    line=function.lineno,
                    code="STR001",
                    subject=name,
                    message=(
                        f"`{name}` is defined inside a function; a callback "
                        f"that keeps state becomes a class with `__call__`, and "
                        f"one that binds an argument goes through "
                        f"functools.partial over a module-level function."
                    ),
                )
            )
        for statement in parsed.tree.body:
            if not isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            name = statement.name
            if not name.startswith("_") or name.startswith("__"):
                continue
            findings.append(
                LayerFinding(
                    path=parsed.path,
                    line=statement.lineno,
                    code="STR002",
                    subject=name,
                    message=(
                        f"module-level `{name}` is a helper with no home; make "
                        f"it a method of the class that uses it or a public "
                        f"function in utils/ (in tests: a fixture or a public "
                        f"helper module)."
                    ),
                )
            )
    return findings


def _exceptions_dict(module: ast.Module) -> ast.Dict | None:
    """Return the dict literal assigned to :data:`EXCEPTIONS_NAME`.

    Args:
        module (ast.Module): The parsed exceptions file.

    Returns:
        ast.Dict | None: The literal of the last module-level assignment,
        annotated or not; ``None`` when the name is never bound to one.
    """
    found: ast.Dict | None = None
    for statement in module.body:
        if isinstance(statement, ast.Assign):
            targets: list[ast.expr] = statement.targets
            value: ast.expr | None = statement.value
        elif isinstance(statement, ast.AnnAssign):
            targets, value = [statement.target], statement.value
        else:
            continue
        if any(
            isinstance(target, ast.Name) and target.id == EXCEPTIONS_NAME
            for target in targets
        ):
            found = value if isinstance(value, ast.Dict) else None
    return found


def _invalid_entry(path: Path, line: int, message: str) -> LayerFinding:
    """Build the ``EXC001`` finding for an entry the checker cannot use.

    Args:
        path (Path): The exceptions file.
        line (int): The offending line.
        message (str): What is wrong and what to change.

    Returns:
        LayerFinding: The finding.
    """
    return LayerFinding(path=path, line=line, code="EXC001", message=message)


def _parse_entry(
    path: Path, key: ast.expr | None, value: ast.expr
) -> DeclaredException | LayerFinding:
    """Turn one dict entry into a :class:`DeclaredException`.

    Args:
        path (Path): The exceptions file.
        key (ast.expr | None): The entry key (``None`` for ``**spread``).
        value (ast.expr): The entry value.

    Returns:
        DeclaredException | LayerFinding: The entry, or the ``EXC001``
        finding saying why it cannot be used.
    """
    if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
        line = value.lineno if key is None else key.lineno
        return _invalid_entry(
            path, line, f"every key of {EXCEPTIONS_NAME} is a string literal."
        )
    raw = key.value
    code, _, target = raw.partition(" ")
    target = target.strip()
    file, _, subject = target.partition(SUBJECT_SEPARATOR)
    if not file:
        return _invalid_entry(
            path,
            key.lineno,
            f"`{raw}` is not `<CODE> <path>` or `<CODE> <path>::<subject>`.",
        )
    if code not in RULES or code.startswith("EXC"):
        known = ", ".join(name for name in RULES if not name.startswith("EXC"))
        return _invalid_entry(
            path, key.lineno, f"`{raw}`: unknown rule code `{code}`; known: {known}."
        )
    if (
        not isinstance(value, ast.Constant)
        or not isinstance(value.value, str)
        or not value.value.strip()
    ):
        return _invalid_entry(
            path,
            key.lineno,
            f"`{raw}` needs a reason: a non-empty string literal saying why the "
            f"exception stands.",
        )
    return DeclaredException(
        key=raw,
        code=code,
        path=file.strip(),
        subject=subject.strip() or None,
        reason=value.value.strip(),
        line=key.lineno,
    )


def read_exceptions(
    path: Path,
) -> tuple[list[DeclaredException], list[LayerFinding]]:
    """Read the declared exceptions of a project without importing it.

    Args:
        path (Path): The exceptions file (:func:`find_exceptions_file`).

    Returns:
        tuple[list[DeclaredException], list[LayerFinding]]: The usable
        entries, and one ``EXC001`` finding per entry (or file) that is
        not usable. A key written twice keeps the first entry.
    """
    parsed = _parse(path)
    if parsed is None:
        return [], [_invalid_entry(path, 1, "the exceptions file does not parse.")]
    literal = _exceptions_dict(parsed.tree)
    if literal is None:
        return [], [
            _invalid_entry(
                path,
                1,
                f"no `{EXCEPTIONS_NAME}: dict[str, str] = {{...}}` dict literal "
                f"at module level.",
            )
        ]
    entries: list[DeclaredException] = []
    problems: list[LayerFinding] = []
    seen: set[str] = set()
    for key, value in zip(literal.keys, literal.values, strict=True):
        entry = _parse_entry(path, key, value)
        if isinstance(entry, LayerFinding):
            problems.append(entry)
        elif entry.key in seen:
            problems.append(
                _invalid_entry(path, entry.line, f"`{entry.key}` is declared twice.")
            )
        else:
            seen.add(entry.key)
            entries.append(entry)
    return entries, problems


def _relative(path: Path, root: Path) -> str | None:
    """Return a finding path relative to the project root, POSIX.

    Args:
        path (Path): The finding's path.
        root (Path): The project root.

    Returns:
        str | None: The relative path, or ``None`` outside ``root``.
    """
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _apply_exceptions(
    findings: list[LayerFinding], exceptions_file: Path, selected: Collection[str]
) -> list[LayerFinding]:
    """Drop the findings a declared exception excuses.

    Args:
        findings (list[LayerFinding]): The findings of the selected rules.
        exceptions_file (Path): The project's exceptions file.
        selected (Collection[str]): The rule codes this run reports; an
            entry for any other code is neither applied nor reported stale.

    Returns:
        list[LayerFinding]: The findings no entry excuses, plus ``EXC001``
        for each unusable entry and ``EXC002`` for each selected entry
        that excused nothing.
    """
    root = exceptions_file.parent.parent
    entries, problems = read_exceptions(exceptions_file)
    entries = [entry for entry in entries if entry.code in selected]
    used: set[str] = set()
    kept: list[LayerFinding] = []
    for finding in findings:
        relative = _relative(finding.path, root)
        match = next(
            (
                entry
                for entry in entries
                if relative is not None and entry.matches(finding, relative)
            ),
            None,
        )
        if match is None:
            kept.append(finding)
        else:
            used.add(match.key)
    stale = [
        LayerFinding(
            path=exceptions_file,
            line=entry.line,
            code="EXC002",
            subject=entry.key,
            message=(
                f"`{entry.key}` matches no finding; the code it excused is "
                f"gone or moved — delete the entry, or fix its path."
            ),
        )
        for entry in entries
        if entry.key not in used
    ]
    return [*kept, *problems, *stale]


def lint_layers(
    target: Path, *, rules: Collection[str] | None = None
) -> list[LayerFinding]:
    """Run the layer rules over a project.

    Args:
        target (Path): The project root (or its ``src`` / ``app``
            directory). Layer directories are found under
            :func:`scan_roots`.
        rules (Collection[str] | None): Rule codes to report. ``None``
            reports every rule. ``EXC001`` is reported whatever the
            selection, since an unusable exceptions file is never silent;
            ``EXC002`` is reported for the entries of the selected codes.

    Returns:
        list[LayerFinding]: Every violation not excused in
        :data:`EXCEPTIONS_PATH`, sorted by file and line. Empty for a
        clean project and for one with no ``services/``,
        ``repositories/`` or ``routers/`` directory and no
        ``BaseStrEnum`` subclass.
    """
    roots = scan_roots(target)
    services: list[Path] = []
    repositories: list[Path] = []
    routers: list[Path] = []
    models: list[Path] = []
    layers: dict[str, list[Path]] = {name: [] for name in LAYER_DIRS}
    for root in roots:
        for name in LAYER_DIRS:
            layers[name].extend(_layer_dirs(root, name))
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
    controller_modules = [
        parsed
        for path in _python_files(layers[CONTROLLERS_DIR])
        if (parsed := _parse(path))
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
    tests_root = project_root(target) / TESTS_DIR
    test_modules = (
        [parsed for path in _source_files(tests_root) if (parsed := _parse(path))]
        if tests_root.is_dir()
        else []
    )
    selected = set(RULES) if rules is None else {*rules, "EXC001", "EXC002"}
    findings = [
        finding
        for finding in (
            *_check_services(service_modules),
            *_check_repositories(repository_modules),
            *_check_enums(source_modules),
            *_check_router_constructions(router_modules),
            *_check_router_returns(router_modules, project_models),
            *_check_layer_shape(layers),
            *_check_repository_imports(repository_modules),
            *_check_injection(service_modules, controller_modules),
            *_check_self_assembly(source_modules),
            *_check_structure([*source_modules, *test_modules]),
            *_check_imports([*source_modules, *test_modules], project_root(target)),
            *_check_router_count(router_modules),
            *_check_api_prefix(source_modules),
        )
        if finding.code in selected
    ]
    exceptions_file = find_exceptions_file(target)
    if exceptions_file is not None:
        findings = [
            finding
            for finding in _apply_exceptions(findings, exceptions_file, selected)
            if finding.code in selected
        ]
    return sorted(findings, key=lambda item: (str(item.path), item.line, item.code))


def _configured_strict(target: Path) -> bool:
    """Read :data:`STRICT_CONFIG_KEY` from the target's ``[tool.tempest]``.

    Args:
        target (Path): The check target; the nearest ``pyproject.toml``
            from it (or from its directory, for a file) is read.

    Returns:
        bool: The configured value, ``False`` when absent.

    Raises:
        ValueError: When the key holds anything but a boolean.
    """
    start = target if target.is_dir() else target.parent
    pyproject, table = load_tempest_table(start)
    value = table.get(STRICT_CONFIG_KEY, False)
    if not isinstance(value, bool):
        raise ValueError(
            f"{pyproject}: [tool.tempest] {STRICT_CONFIG_KEY} must be true or "
            f"false, got {value!r}."
        )
    return value


def run_lint_layers(
    target: str,
    *,
    rules: Collection[str] | None = None,
    strict: bool | None = None,
    stream: TextIO | None = None,
) -> int:
    """Print the layer findings for ``target`` and return an exit code.

    This is the callable ``tempest check`` runs as its ``lint-layers``
    step (with the defaults) and ``tempest lint-layers`` runs on its own.

    Args:
        target (str): The path to check, ``.`` for the current project.
        rules (Collection[str] | None): Rule codes to report, any case.
            ``None`` reports every rule.
        strict (bool | None): Whether a warning fails the run. ``None``
            reads ``layers_strict`` from ``[tool.tempest]``.
        stream (TextIO | None): Where the report goes. ``None`` means
            standard output.

    Returns:
        int: ``1`` when an error (or, strict, any finding) is reported,
        ``2`` for an unknown rule code or an invalid ``layers_strict``,
        ``0`` otherwise.
    """
    output = stream or sys.stdout
    path = Path(target)
    selected = None if rules is None else {code.upper() for code in rules}
    unknown = sorted((selected or set()) - set(RULES))
    if unknown:
        output.write(
            f"unknown rule code(s): {', '.join(unknown)}; known: {', '.join(RULES)}.\n"
        )
        return 2
    if strict is None:
        try:
            strict = _configured_strict(path)
        except (ValueError, tomllib.TOMLDecodeError) as exc:
            output.write(f"{exc}\n")
            return 2
    findings = lint_layers(path, rules=selected)
    for finding in findings:
        output.write(finding.render(relative_to=Path.cwd()) + "\n")
    if not findings:
        output.write("Layer rules: no findings.\n")
        return 0
    warnings = sum(1 for finding in findings if finding.severity == "warning")
    errors = len(findings) - warnings
    if warnings:
        output.write(
            f"{len(findings)} layer finding(s): {errors} error(s), "
            f"{warnings} warning(s).\n"
        )
    else:
        output.write(f"{len(findings)} layer finding(s).\n")
    return 1 if errors or strict else 0


__all__: list[str] = [
    "API_PREFIX",
    "API_ROUTER_BUILDER",
    "BASE_REPOSITORY_METHODS",
    "CONSTRUCTED_SUFFIXES",
    "CONTROLLERS_DIR",
    "ENUM_NAME_SUFFIX",
    "EXCEPTIONS_NAME",
    "EXCEPTIONS_PATH",
    "LAYER_DIRS",
    "MODELS_DIR",
    "NO_BASE_SERVICE_MARKER",
    "REPOSITORIES_DIR",
    "REPOSITORY_SUFFIX",
    "ROUTERS_DIR",
    "ROUTER_BUILDS_MARKER",
    "ROUTER_CLASS",
    "ROUTE_DECORATORS",
    "RULES",
    "SCHEMAS_SEGMENT",
    "SELF_ASSEMBLY_METHOD",
    "SERVICES_DIR",
    "SESSION_TYPE",
    "SKIPPED_DIRS",
    "SOURCE_ROOTS",
    "STRICT_CONFIG_KEY",
    "STR_ENUM_BASE",
    "SUBJECT_SEPARATOR",
    "TESTS_DIR",
    "DeclaredException",
    "LayerFinding",
    "LayerRule",
    "Severity",
    "find_exceptions_file",
    "lint_layers",
    "project_root",
    "read_exceptions",
    "run_lint_layers",
    "scan_roots",
]
