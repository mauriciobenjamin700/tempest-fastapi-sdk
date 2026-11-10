"""Where FastAPI dependencies live, read statically for ``openapi-errors``.

A route's errors do not all come from its body. FastAPI also runs, before
the handler:

* every ``Depends(...)`` / ``Security(...)`` in the handler's parameters —
  as a default (``x: T = Depends(f)``), inside ``Annotated[T, Depends(f)]``,
  or through a module-level alias (``XDep = Annotated[T, Depends(f)]``);
* the ``dependencies=[...]`` of the route decorator;
* the ``dependencies=[...]`` of the ``APIRouter(...)`` the route hangs on;
* the ``dependencies=[...]`` of every ``include_router(...)`` that mounts
  that router, transitively.

``openapi-errors`` used to walk only the body, so a ``require_api_key``
on the router, or a provider in ``api/dependencies/`` answering ``404``
when a capability is off, was invisible: the report said every route
matched its flow with the ``401`` and the ``404`` undeclared (#718).

This module only **finds** those places, with :mod:`ast` and without
importing anything; :mod:`tempest_fastapi_sdk.cli.openapi_errors` turns
each dependency into a call-graph edge and walks it like any other call.
The same router sites carry ``responses=``, which counts as a declaration
for every route under that router.

What it cannot see, by construction:

* a router built by a function call and mounted inline
  (``app.include_router(build_api_router(), dependencies=[...])``): the
  child is an expression, not a name, so it is not followed;
* a dependency that is an *instance* bound at module level to a class the
  analyzer cannot type (``require_x = RequireX()``), except the SDK
  factories listed in :data:`SDK_DEPENDENCY_FACTORIES`.

Documenting the exception in the dependency's ``Raises:`` section, or
declaring it on the route, covers both.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

DEPENDENCY_MARKERS: frozenset[str] = frozenset({"Depends", "Security"})
"""Callee names that wrap a FastAPI dependency."""

ROUTER_CLASS_NAME: str = "APIRouter"
"""The class whose construction declares router-level dependencies."""

INCLUDE_ROUTER: str = "include_router"
"""The method that mounts a router under another, with its own dependencies."""

ANNOTATED_NAME: str = "Annotated"
"""``typing.Annotated``, whose first argument is the type and the rest metadata."""

SDK_DEPENDENCY_FACTORIES: dict[str, frozenset[str]] = {
    "make_token_dependency": frozenset({"UnauthorizedException"}),
    "make_bearer_token_dependency": frozenset({"UnauthorizedException"}),
    "make_jwt_user_dependency": frozenset({"UnauthorizedException"}),
    "make_mfa_required_dependency": frozenset(
        {"UnauthorizedException", "MFARequiredException"}
    ),
    "make_role_dependency": frozenset({"UnauthorizedException", "ForbiddenException"}),
}
"""Exceptions the dependency returned by each SDK factory raises.

Ported from the ``raise`` statements inside the nested dependency of each
factory in :mod:`tempest_fastapi_sdk.api.dependencies.auth`. The project
writes ``require_token = make_token_dependency(...)`` once and the
``raise`` lives in the SDK, outside the scanned tree, so without this table
the most common router-level dependency would add nothing.
``tests/cli/test_openapi_errors_dependencies.py`` recomputes each entry from
that module's source, so the table fails a test instead of drifting.
"""

RouterKey = tuple[Path, str]
"""A router variable: the file it is bound in and its name there."""


@dataclass(slots=True)
class RouterSite:
    """One place that attaches dependencies or responses to a router.

    Attributes:
        dependencies (ast.expr | None): The ``dependencies=`` value.
        responses (ast.expr | None): The ``responses=`` value.
    """

    dependencies: ast.expr | None = None
    responses: ast.expr | None = None


@dataclass(slots=True)
class _Binding:
    """What a name imported into a module points at.

    Attributes:
        file (Path): The module file the name comes from.
        name (str | None): The name inside that module, or ``None`` when
            the binding is the module itself (``from pkg import mod``).
    """

    file: Path
    name: str | None


@dataclass(slots=True)
class RouterIndex:
    """Router sites and the ``include_router`` edges between routers.

    Attributes:
        own (dict[RouterKey, list[RouterSite]]): ``APIRouter(...)``
            constructions, by the variable they are bound to.
        includes (list[tuple[RouterKey, RouterKey, RouterSite]]):
            ``(parent, child, site)`` for every ``parent.include_router(child,
            ...)`` whose child resolved to a router variable.
        bindings (dict[Path, dict[str, _Binding]]): Imported names per file.
        modules (set[Path]): Every scanned file, for import resolution.
    """

    own: dict[RouterKey, list[RouterSite]] = field(default_factory=dict)
    includes: list[tuple[RouterKey, RouterKey, RouterSite]] = field(
        default_factory=list
    )
    bindings: dict[Path, dict[str, _Binding]] = field(default_factory=dict)
    modules: set[Path] = field(default_factory=set)

    def resolve(self, file: Path, node: ast.expr, depth: int = 0) -> RouterKey | None:
        """Resolve a router expression in ``file`` to the variable it names.

        Follows ``from x import router`` and re-exports through package
        ``__init__`` modules, and ``module.router`` attribute access.

        Args:
            file (Path): The file the expression is written in.
            node (ast.expr): A name (``items_router``) or a module attribute
                (``items.router``).
            depth (int): Re-export hops already taken; stops at ten.

        Returns:
            RouterKey | None: The defining file and variable name, or
            ``None`` when the expression is anything else.
        """
        if depth > 10:
            return None
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            binding = self.bindings.get(file, {}).get(node.value.id)
            if binding is None or binding.name is not None:
                return None
            return self.resolve(binding.file, ast.Name(id=node.attr), depth + 1)
        if not isinstance(node, ast.Name):
            return None
        binding = self.bindings.get(file, {}).get(node.id)
        if binding is None or binding.name is None:
            return (file, node.id)
        return self.resolve(binding.file, ast.Name(id=binding.name), depth + 1)

    def sites_for(self, key: RouterKey) -> list[RouterSite]:
        """Return every site that applies to routes of ``key``.

        Args:
            key (RouterKey): The router the route hangs on.

        Returns:
            list[RouterSite]: The router's own construction, every
            ``include_router`` that mounts it, and — transitively — the
            sites of the routers that include it.
        """
        found: list[RouterSite] = []
        seen: set[RouterKey] = set()
        queue: list[RouterKey] = [key]
        while queue:
            current = queue.pop()
            if current in seen:
                continue
            seen.add(current)
            found.extend(self.own.get(current, ()))
            for parent, child, site in self.includes:
                if child == current:
                    found.append(site)
                    queue.append(parent)
        return found


def called_name(node: ast.Call) -> str | None:
    """Return the trailing name of a call's callee.

    Args:
        node (ast.Call): The call.

    Returns:
        str | None: ``f`` for ``f()`` and ``pkg.f()``; ``None`` otherwise.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def unwrap_annotated(node: ast.expr | None) -> tuple[ast.expr | None, list[ast.expr]]:
    """Split ``Annotated[T, *metadata]`` into ``T`` and its metadata.

    Args:
        node (ast.expr | None): An annotation.

    Returns:
        tuple[ast.expr | None, list[ast.expr]]: The inner type and the
        metadata for an ``Annotated`` subscript; the annotation itself and
        an empty list otherwise.
    """
    if not isinstance(node, ast.Subscript):
        return node, []
    base = node.value
    name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
    if name != ANNOTATED_NAME or not isinstance(node.slice, ast.Tuple):
        return node, []
    elements = node.slice.elts
    if not elements:
        return node, []
    return elements[0], list(elements[1:])


def dependency_targets(node: ast.AST | None) -> Iterator[ast.expr | None]:
    """Yield the callables wrapped by ``Depends`` / ``Security`` in ``node``.

    Args:
        node (ast.AST | None): Any expression — a default value, the
            metadata of an ``Annotated``, a ``dependencies=[...]`` list.

    Yields:
        ast.expr | None: The first positional argument (or the
        ``dependency=`` keyword) of each marker call; ``None`` for a bare
        ``Depends()``, whose callable is the annotated type.
    """
    if node is None:
        return
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if called_name(child) not in DEPENDENCY_MARKERS:
            continue
        if child.args:
            yield child.args[0]
            continue
        keyword = next((k for k in child.keywords if k.arg == "dependency"), None)
        yield None if keyword is None else keyword.value


def _keyword(node: ast.Call, name: str) -> ast.expr | None:
    """Return the value of keyword ``name`` in a call.

    Args:
        node (ast.Call): The call.
        name (str): The keyword.

    Returns:
        ast.expr | None: Its value, or ``None`` when absent.
    """
    return next((k.value for k in node.keywords if k.arg == name), None)


def dependency_aliases(trees: Iterable[ast.Module]) -> dict[str, ast.expr]:
    """Collect module-level ``Annotated`` aliases that carry a dependency.

    Reads ``XDep = Annotated[T, Depends(f)]``, its annotated form
    (``XDep: TypeAlias = ...``) and ``type XDep = Annotated[...]``.

    Args:
        trees (Iterable[ast.Module]): The parsed project modules.

    Returns:
        dict[str, ast.expr]: Alias name to its ``Annotated`` expression. A
        name defined in two modules keeps the last one read.
    """
    aliases: dict[str, ast.expr] = {}
    for tree in trees:
        for statement in tree.body:
            target: ast.expr | None = None
            value: ast.expr | None = None
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target, value = statement.targets[0], statement.value
            elif isinstance(statement, ast.AnnAssign):
                target, value = statement.target, statement.value
            elif isinstance(statement, ast.TypeAlias):
                target, value = statement.name, statement.value
            if not isinstance(target, ast.Name) or value is None:
                continue
            _, metadata = unwrap_annotated(value)
            if any(True for item in metadata for _ in dependency_targets(item)):
                aliases[target.id] = value
    return aliases


def _resolve_module(
    importer: Path, module: str | None, level: int, modules: set[Path]
) -> Path | None:
    """Find the scanned file an import statement names.

    Args:
        importer (Path): The file holding the import.
        module (str | None): The dotted module (``None`` for ``from . import x``).
        level (int): Leading dots of a relative import.
        modules (set[Path]): Every scanned file.

    Returns:
        Path | None: ``<module>.py`` or ``<module>/__init__.py`` among the
        scanned files. A relative import resolves from the importer's
        directory; an absolute one matches the file whose path ends with
        the module's parts. ``None`` when nothing matches.
    """
    parts = [part for part in (module or "").split(".") if part]
    if level:
        base = importer.parent
        for _ in range(level - 1):
            base = base.parent
        candidates = [base.joinpath(*parts, "__init__.py")]
        if parts:
            candidates.insert(0, base.joinpath(*parts[:-1], f"{parts[-1]}.py"))
        return next((c for c in candidates if c in modules), None)
    if not parts:
        return None
    suffixes = [
        (*parts[:-1], f"{parts[-1]}.py"),
        (*parts, "__init__.py"),
    ]
    for suffix in suffixes:
        for candidate in sorted(modules):
            if candidate.parts[-len(suffix) :] == suffix:
                return candidate
    return None


def _bindings(file: Path, tree: ast.Module, modules: set[Path]) -> dict[str, _Binding]:
    """Map every name a module imports to where it comes from.

    Args:
        file (Path): The module file.
        tree (ast.Module): Its parsed source.
        modules (set[Path]): Every scanned file.

    Returns:
        dict[str, _Binding]: Local name to the binding, for imports that
        resolve to a scanned file. A submodule imported by name
        (``from src.api import routers``) binds the module itself.
    """
    bindings: dict[str, _Binding] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            source = _resolve_module(file, node.module, node.level, modules)
            for alias in node.names:
                local = alias.asname or alias.name
                dotted = ".".join(filter(None, [node.module, alias.name]))
                submodule = _resolve_module(file, dotted, node.level, modules)
                if submodule is not None and submodule != source:
                    bindings[local] = _Binding(file=submodule, name=None)
                elif source is not None:
                    bindings[local] = _Binding(file=source, name=alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname is None:
                    continue
                target = _resolve_module(file, alias.name, 0, modules)
                if target is not None:
                    bindings[alias.asname] = _Binding(file=target, name=None)
    return bindings


def build_router_index(parsed: Iterable[tuple[Path, ast.Module]]) -> RouterIndex:
    """Index the router constructions and ``include_router`` calls.

    Args:
        parsed (Iterable[tuple[Path, ast.Module]]): The parsed modules,
            paired with their files.

    Returns:
        RouterIndex: The sites, the include edges and the import bindings
        needed to resolve a route's router across modules.
    """
    modules = list(parsed)
    index = RouterIndex(modules={file for file, _ in modules})
    for file, tree in modules:
        index.bindings[file] = _bindings(file, tree, index.modules)
    for file, tree in modules:
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign | ast.AnnAssign):
                value = node.value
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if not isinstance(value, ast.Call):
                    continue
                if called_name(value) != ROUTER_CLASS_NAME:
                    continue
                site = RouterSite(
                    dependencies=_keyword(value, "dependencies"),
                    responses=_keyword(value, "responses"),
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        index.own.setdefault((file, target.id), []).append(site)
            elif isinstance(node, ast.Call) and called_name(node) == INCLUDE_ROUTER:
                receiver = node.func
                if not isinstance(receiver, ast.Attribute) or not node.args:
                    continue
                parent = index.resolve(file, receiver.value)
                child = index.resolve(file, node.args[0])
                if parent is None or child is None:
                    continue
                site = RouterSite(
                    dependencies=_keyword(node, "dependencies"),
                    responses=_keyword(node, "responses"),
                )
                index.includes.append((parent, child, site))
    return index


def sdk_dependency_instances(tree: ast.Module) -> Iterator[tuple[str, int, str]]:
    """Yield module-level names bound to an SDK dependency factory's result.

    Args:
        tree (ast.Module): A parsed module.

    Yields:
        tuple[str, int, str]: The bound name, its line and the factory
        (a key of :data:`SDK_DEPENDENCY_FACTORIES`), for
        ``require_token = make_token_dependency(...)``.
    """
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets, value = [statement.target], statement.value
        else:
            continue
        if not isinstance(value, ast.Call):
            continue
        factory = called_name(value)
        if factory not in SDK_DEPENDENCY_FACTORIES:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                yield target.id, statement.lineno, factory


__all__: list[str] = [
    "ANNOTATED_NAME",
    "DEPENDENCY_MARKERS",
    "INCLUDE_ROUTER",
    "ROUTER_CLASS_NAME",
    "SDK_DEPENDENCY_FACTORIES",
    "RouterIndex",
    "RouterKey",
    "RouterSite",
    "build_router_index",
    "called_name",
    "dependency_aliases",
    "dependency_targets",
    "sdk_dependency_instances",
    "unwrap_annotated",
]
