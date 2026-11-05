"""``tempest bundle-route`` — one route's code, as markdown for an AI chat.

Asking an assistant about a bug in one endpoint means pasting the router,
the controller, the service, the repository, the model and the schemas
it touches, and nothing else. Done by hand, the paste either misses the
file where the bug lives or drowns it in the rest of the project.

The route is resolved on the live application, exactly as
``tempest routes`` lists it, so the starting points are the endpoint
function and every callable of its ``Depends`` tree (the ones inherited
from ``include_router`` included). From there the project's own code is
followed **statically**: each module is parsed with :mod:`ast`, nothing
beyond the application itself is imported or executed.

The walk is per symbol, not per module. Following every import of every
reached file would drag the whole project in through the re-exporting
``__init__.py`` files the scaffold mandates (``from src.services import
UserService`` names a package that re-exports every service), so a
reached name is traced through those re-exports to the module that
defines it, and only the names that definition references are followed
next. A file is bundled whole once any definition in it is reached; a
package ``__init__`` crossed only as a re-export is not.

Only ``.py`` files under the project root, inside the project's own
packages (``src``/``app`` or the endpoint's top-level package), are
read — never the standard library, ``site-packages``, a virtualenv
inside the project, the SDK, or a non-Python file such as ``.env``.
"""

from __future__ import annotations

import ast
import difflib
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from tempest_fastapi_sdk.cli.project import (
    CODE_ROOTS,
    ensure_project_on_path,
    load_project_app,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

LAYER_ORDER: tuple[tuple[str, str], ...] = (
    ("routers", "router"),
    ("dependencies", "dependencies"),
    ("controllers", "controllers"),
    ("services", "services"),
    ("repositories", "repositories"),
    ("models", "models"),
    ("schemas", "schemas"),
    ("core", "core"),
)
"""Module-name segment that places a file in a layer, in bundle order."""

OTHER_LAYER: str = "other"
"""Layer of a file whose module name carries none of :data:`LAYER_ORDER`."""

EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {".venv", "venv", "site-packages", "dist-packages", "node_modules", ".git"}
)
"""Directory names whose files are never project code, even under the root."""

_WEBSOCKET: str = "WEBSOCKET"
"""Pseudo-method of a route that declares no HTTP methods."""


@dataclass(frozen=True)
class RouteTarget:
    """The route being bundled.

    Attributes:
        method (str): HTTP method, or ``WEBSOCKET``.
        path (str): The effective path, prefixes applied.
        endpoint (Any): The endpoint callable.
        dependencies (tuple[Any, ...]): Every callable of the route's
            ``Depends`` tree, depth first, without repetition.
    """

    method: str
    path: str
    endpoint: Any
    dependencies: tuple[Any, ...]


@dataclass
class _ParsedModule:
    """One project module, parsed once.

    Attributes:
        name (str): Dotted module name.
        path (Path): Source file.
        is_package (bool): Whether the file is a package ``__init__.py``.
        definitions (dict[str, list[ast.stmt]]): Top-level statements by
            the name they bind (functions, classes, assignments).
        imports (dict[str, tuple[str, str | None]]): Local name to
            ``(module, attribute)``; ``attribute`` is ``None`` when the
            name binds the module itself.
    """

    name: str
    path: Path
    is_package: bool
    definitions: dict[str, list[ast.stmt]] = field(default_factory=dict)
    imports: dict[str, tuple[str, str | None]] = field(default_factory=dict)


@dataclass(frozen=True)
class BundledFile:
    """A file that goes into the bundle.

    Attributes:
        module (str): Dotted module name.
        relative_path (str): POSIX path relative to the project root.
        layer (str): Layer label the file is ordered by.
        source (str): The file's full text.
    """

    module: str
    relative_path: str
    layer: str
    source: str

    @property
    def line_count(self) -> int:
        """Number of lines in the file.

        Returns:
            int: Line count of :attr:`source`.
        """
        return len(self.source.splitlines())


class _ReferenceCollector(ast.NodeVisitor):
    """Collect the dotted names a statement reads.

    ``a.b.c`` is recorded as ``["a", "b", "c"]`` rather than as three
    separate reads, so a module alias can be walked down to the
    submodule or symbol it reaches.
    """

    def __init__(self) -> None:
        """Start with no references."""
        self.chains: list[list[str]] = []

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Record an attribute chain rooted at a plain name.

        Args:
            node (ast.Attribute): The outermost attribute of the chain.
        """
        parts: list[str] = []
        current: ast.expr = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            self.chains.append(list(reversed(parts)))
            return
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """Record a bare name.

        Args:
            node (ast.Name): The name node.
        """
        self.chains.append([node.id])


class _ProjectIndex:
    """Resolve and parse the project's modules from the filesystem.

    Args:
        root (Path): The resolved project root.
        packages (frozenset[str]): Top-level package names that count as
            project code.
    """

    def __init__(self, root: Path, packages: frozenset[str]) -> None:
        """Bind the index to a project.

        Args:
            root (Path): The resolved project root.
            packages (frozenset[str]): Top-level project packages.
        """
        self.root: Path = root
        self.packages: frozenset[str] = packages
        self._cache: dict[str, _ParsedModule | None] = {}

    def module_path(self, name: str) -> tuple[Path, bool] | None:
        """Locate a project module's source file without importing it.

        Args:
            name (str): Dotted module name.

        Returns:
            tuple[Path, bool] | None: The file and whether it is a
                package ``__init__.py``; ``None`` for anything outside the
                project packages, outside the root, or under an excluded
                directory.
        """
        parts = name.split(".")
        if not name or parts[0] not in self.packages:
            return None
        base = self.root.joinpath(*parts)
        for candidate, is_package in (
            (base.with_suffix(".py"), False),
            (base / "__init__.py", True),
        ):
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
            if not resolved.is_relative_to(self.root):
                return None
            relative_parts = resolved.relative_to(self.root).parts
            if EXCLUDED_DIR_NAMES.intersection(relative_parts):
                return None
            return resolved, is_package
        return None

    def load(self, name: str) -> _ParsedModule | None:
        """Parse a project module, once.

        Args:
            name (str): Dotted module name.

        Returns:
            _ParsedModule | None: The parsed module, or ``None`` when it
                is not project code or does not parse.
        """
        if name in self._cache:
            return self._cache[name]
        located = self.module_path(name)
        parsed: _ParsedModule | None = None
        if located is not None:
            path, is_package = located
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            except (SyntaxError, UnicodeDecodeError):
                tree = None
            if tree is not None:
                parsed = _ParsedModule(name, path, is_package)
                self._index(parsed, tree)
        self._cache[name] = parsed
        return parsed

    def _index(self, module: _ParsedModule, tree: ast.Module) -> None:
        """Fill a module's definitions and imports from its top level.

        Statements under ``if`` / ``try`` (``TYPE_CHECKING`` blocks,
        optional imports) are indexed too, since they bind module names.

        Args:
            module (_ParsedModule): The module being indexed.
            tree (ast.Module): Its parsed source.
        """
        pending: deque[ast.stmt] = deque(tree.body)
        while pending:
            statement = pending.popleft()
            if isinstance(statement, (ast.If, ast.Try)):
                pending.extend(statement.body)
                pending.extend(statement.orelse)
                if isinstance(statement, ast.Try):
                    pending.extend(statement.finalbody)
                    for handler in statement.handlers:
                        pending.extend(handler.body)
                continue
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    if alias.asname:
                        module.imports[alias.asname] = (alias.name, None)
                    else:
                        head = alias.name.split(".", 1)[0]
                        module.imports[head] = (head, None)
                continue
            if isinstance(statement, ast.ImportFrom):
                source = self._absolute(module, statement)
                for alias in statement.names:
                    if alias.name == "*":
                        continue
                    local = alias.asname or alias.name
                    module.imports[local] = (source, alias.name)
                continue
            for name in _bound_names(statement):
                module.definitions.setdefault(name, []).append(statement)

    @staticmethod
    def _absolute(module: _ParsedModule, statement: ast.ImportFrom) -> str:
        """Turn a possibly relative ``from`` import into a dotted name.

        Args:
            module (_ParsedModule): The importing module.
            statement (ast.ImportFrom): The import statement.

        Returns:
            str: The absolute module name.
        """
        if not statement.level:
            return statement.module or ""
        package = module.name if module.is_package else module.name.rpartition(".")[0]
        parts = package.split(".") if package else []
        if statement.level > 1:
            parts = parts[: len(parts) - (statement.level - 1)]
        if statement.module:
            parts.append(statement.module)
        return ".".join(parts)


def _bound_names(statement: ast.stmt) -> list[str]:
    """Names a top-level statement binds.

    Args:
        statement (ast.stmt): A module-level statement.

    Returns:
        list[str]: The bound names; empty for statements that bind none.
    """
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [statement.name]
    targets: list[ast.expr] = []
    if isinstance(statement, ast.Assign):
        targets = list(statement.targets)
    elif isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
        targets = [statement.target]
    names: list[str] = []
    for target in targets:
        names.extend(node.id for node in ast.walk(target) if isinstance(node, ast.Name))
    return names


def _flatten_dependencies(dependant: Any) -> tuple[Any, ...]:
    """Collect every callable of a ``Depends`` tree, depth first.

    Args:
        dependant (Any): A FastAPI ``Dependant`` (or ``None``).

    Returns:
        tuple[Any, ...]: The callables, each once, in visit order.
    """
    seen: list[Any] = []
    stack: list[Any] = list(reversed(getattr(dependant, "dependencies", ()) or ()))
    while stack:
        current = stack.pop()
        call = getattr(current, "call", None)
        if call is not None and not any(call is known for known in seen):
            seen.append(call)
        stack.extend(reversed(getattr(current, "dependencies", ()) or ()))
    return tuple(seen)


def _route_targets(app: FastAPI) -> list[RouteTarget]:
    """List every method/path pair of the application.

    Args:
        app (FastAPI): The application.

    Returns:
        list[RouteTarget]: One target per method and path, ``HEAD``
            omitted (FastAPI derives it from ``GET``).
    """
    from fastapi.routing import iter_route_contexts

    targets: list[RouteTarget] = []
    for context in iter_route_contexts(app.routes):
        path = getattr(context, "path", None)
        endpoint = getattr(context, "endpoint", None)
        if not isinstance(path, str) or endpoint is None:
            continue
        dependencies = _flatten_dependencies(getattr(context, "dependant", None))
        methods = getattr(context, "methods", None) or {_WEBSOCKET}
        targets.extend(
            RouteTarget(method, path, endpoint, dependencies)
            for method in sorted(methods)
            if method != "HEAD"
        )
    return targets


def resolve_route(app: FastAPI, route: str) -> RouteTarget:
    """Find the route the operator named.

    Args:
        app (FastAPI): The application.
        route (str): ``"METHOD /path"`` or ``"/path"``. Without a
            method, the path must be served by exactly one.

    Returns:
        RouteTarget: The matched route.

    Raises:
        typer.Exit: Exit code 1 when nothing matches (close matches are
            suggested on stderr); exit code 2 when the path alone is
            served by several methods (they are listed).
    """
    tokens = route.split()
    method: str | None = None
    path = route.strip()
    if len(tokens) == 2 and not tokens[0].startswith("/"):
        method, path = tokens[0].upper(), tokens[1]
    targets = _route_targets(app)
    on_path = [target for target in targets if target.path == path]
    matches = [t for t in on_path if method is None or t.method == method]
    if not matches:
        known = [f"{target.method} {target.path}" for target in targets]
        typer.echo(f"error: no route matches {route!r}.", err=True)
        if on_path:
            served = ", ".join(sorted({target.method for target in on_path}))
            typer.echo(f"  {path} is served by: {served}", err=True)
        else:
            for suggestion in difflib.get_close_matches(
                f"{method or 'GET'} {path}", known, n=5, cutoff=0.5
            ):
                typer.echo(f"  did you mean: {suggestion}", err=True)
        typer.echo("  run `tempest routes` to list every route.", err=True)
        raise typer.Exit(1)
    methods = sorted({target.method for target in matches})
    if len(methods) > 1:
        typer.echo(
            f"error: {path} is served by several methods: {', '.join(methods)}. "
            f"Name one, e.g. '{methods[0]} {path}'.",
            err=True,
        )
        raise typer.Exit(2)
    return matches[0]


def _symbol_of(callable_: Any) -> tuple[str, str] | None:
    """Module and top-level name a callable is defined under.

    A closure (``make_x()`` returning an inner function) is traced to the
    top-level function that builds it; a callable instance to its class.

    Args:
        callable_ (Any): A function, class or callable instance.

    Returns:
        tuple[str, str] | None: ``(module, name)``, or ``None`` when the
            callable carries neither.
    """
    target = callable_
    if not hasattr(target, "__qualname__") or not hasattr(target, "__module__"):
        target = type(callable_)
    module = getattr(target, "__module__", None)
    qualname = getattr(target, "__qualname__", None)
    if not isinstance(module, str) or not isinstance(qualname, str):
        return None
    top = qualname.split(".<locals>", 1)[0].split(".", 1)[0]
    return module, top


def _layer_of(module: str) -> tuple[int, str]:
    """Rank a module by the layer its name places it in.

    Args:
        module (str): Dotted module name.

    Returns:
        tuple[int, str]: Sort rank and layer label.
    """
    parts = module.split(".")
    for rank, (segment, label) in enumerate(LAYER_ORDER):
        if segment in parts:
            return rank, label
    return len(LAYER_ORDER), OTHER_LAYER


def collect_route_files(
    target: RouteTarget,
    root: Path,
    *,
    max_depth: int | None = None,
) -> list[BundledFile]:
    """Gather the project files the route's code reaches.

    Args:
        target (RouteTarget): The resolved route.
        root (Path): The project root.
        max_depth (int | None): How many reference hops to follow from
            the endpoint and its dependencies; ``0`` keeps only the files
            that define them. ``None`` follows everything reachable.

    Returns:
        list[BundledFile]: Files ordered by layer, then path; the
            endpoint's module always first among the routers.
    """
    root = root.resolve()
    starts = [
        symbol
        for symbol in map(_symbol_of, (target.endpoint, *target.dependencies))
        if symbol is not None
    ]
    packages = {name for name in CODE_ROOTS if (root / name).is_dir()}
    endpoint_symbol = _symbol_of(target.endpoint)
    if endpoint_symbol is not None:
        packages.add(endpoint_symbol[0].split(".", 1)[0])
    packages.discard("tempest_fastapi_sdk")
    index = _ProjectIndex(root, frozenset(packages))

    included: dict[str, _ParsedModule] = {}
    visited: set[tuple[str, str | None]] = set()
    queue: deque[tuple[str, str | None, int]] = deque(
        (module, name, 0) for module, name in starts
    )

    def follow(module: _ParsedModule, chain: list[str], depth: int) -> None:
        """Queue whatever a dotted reference read in ``module`` reaches.

        Args:
            module (_ParsedModule): The module the reference is read in.
            chain (list[str]): The dotted reference.
            depth (int): Depth of the referencing definition.
        """
        head, rest = chain[0], chain[1:]
        if head in module.definitions:
            queue.append((module.name, head, depth))
            return
        if head not in module.imports:
            return
        source, attribute = module.imports[head]
        if attribute is not None:
            nested = f"{source}.{attribute}" if source else attribute
            if index.module_path(nested) is None:
                queue.append((source, attribute, depth))
                return
            source = nested
        current = source
        for part in rest:
            nested = f"{current}.{part}"
            if index.module_path(nested) is None:
                queue.append((current, part, depth))
                return
            current = nested
        queue.append((current, None, depth))

    while queue:
        module_name, name, depth = queue.popleft()
        if (module_name, name) in visited:
            continue
        visited.add((module_name, name))
        module = index.load(module_name)
        if module is None:
            continue
        if name is None:
            included.setdefault(module.name, module)
            continue
        if name in module.definitions:
            included.setdefault(module.name, module)
            if max_depth is not None and depth >= max_depth:
                continue
            collector = _ReferenceCollector()
            for statement in module.definitions[name]:
                collector.visit(statement)
            for chain in collector.chains:
                follow(module, chain, depth + 1)
            continue
        if name in module.imports:
            follow(module, [name], depth)

    endpoint_module = endpoint_symbol[0] if endpoint_symbol else None
    files = [
        BundledFile(
            module=module.name,
            relative_path=module.path.relative_to(root).as_posix(),
            layer=_layer_of(module.name)[1],
            source=module.path.read_text(encoding="utf-8"),
        )
        for module in included.values()
    ]
    return sorted(
        files,
        key=lambda item: (
            0 if item.module == endpoint_module else 1,
            _layer_of(item.module)[0],
            item.relative_path,
        ),
    )


def _fence(source: str) -> str:
    """Pick a code fence longer than any backtick run in ``source``.

    Args:
        source (str): The text to fence.

    Returns:
        str: A run of backticks, at least three long.
    """
    longest = max((len(run) for run in re.findall(r"`+", source)), default=0)
    return "`" * max(3, longest + 1)


def render_bundle(target: RouteTarget, files: list[BundledFile]) -> str:
    """Render the bundle as markdown.

    Args:
        target (RouteTarget): The resolved route.
        files (list[BundledFile]): The files, already ordered.

    Returns:
        str: The markdown document, ending in a newline.
    """
    endpoint = getattr(target.endpoint, "__name__", type(target.endpoint).__name__)
    dependency_names = [
        getattr(call, "__name__", type(call).__name__) for call in target.dependencies
    ]
    total = sum(item.line_count for item in files)
    lines = [
        f"# Route bundle: `{target.method} {target.path}`",
        "",
        f"- **Method:** `{target.method}`",
        f"- **Path:** `{target.path}`",
        f"- **Endpoint:** `{endpoint}`",
        "- **Dependencies:** "
        + (", ".join(f"`{name}`" for name in dependency_names) or "none"),
        "",
        f"## Files ({len(files)} files, {total} lines)",
        "",
        "| Layer | File | Lines |",
        "| --- | --- | --- |",
    ]
    lines.extend(
        f"| {item.layer} | `{item.relative_path}` | {item.line_count} |"
        for item in files
    )
    for item in files:
        fence = _fence(item.source)
        lines.extend(
            [
                "",
                f"## `{item.relative_path}`",
                "",
                f"{fence}python",
                item.source.rstrip("\n"),
                fence,
            ]
        )
    return "\n".join(lines) + "\n"


def bundle_route_command(
    route: str = typer.Argument(
        ...,
        help=(
            "The route, as 'METHOD /path' (e.g. 'GET /api/users/{user_id}') "
            "or '/path' when a single method serves it. The path is the one "
            "`tempest routes` prints."
        ),
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the markdown to this file instead of stdout.",
    ),
    max_depth: int | None = typer.Option(
        None,
        "--max-depth",
        min=0,
        help=(
            "Reference hops to follow from the endpoint and its dependencies "
            "(0 = only the files defining them). Unlimited when omitted."
        ),
    ),
    app_spec: str = typer.Option(
        "",
        "--app",
        "-a",
        help=(
            "Import spec of the FastAPI app ('module:attr'). Omitted, the "
            "scaffolded locations are probed."
        ),
    ),
) -> None:
    """Bundle one route's code into markdown to paste into an AI chat.

    The router, its dependencies, controllers, services, repositories,
    models, schemas and core modules the route reaches are written whole,
    ordered by layer. Only the project's own ``.py`` files are read; the
    bundle still carries your source code, so review it before pasting it
    into a third-party service.

    Raises:
        typer.Exit: Exit code 1 when no route matches or the route
            reaches no project file; exit code 2 when the path is served
            by several methods and none was named, or the app cannot be
            imported.
    """
    root = ensure_project_on_path(None)
    app = load_project_app(app_spec or None)
    target = resolve_route(app, route)
    files = collect_route_files(target, root, max_depth=max_depth)
    if not files:
        typer.echo(
            f"error: {target.method} {target.path} reaches no file of this "
            "project (its endpoint lives in an installed package).",
            err=True,
        )
        raise typer.Exit(1)
    markdown = render_bundle(target, files)
    total = sum(item.line_count for item in files)
    if output is None:
        typer.echo(markdown, nl=False)
    else:
        output.write_text(markdown, encoding="utf-8")
        typer.echo(f"wrote {output}", err=True)
    typer.echo(
        f"{target.method} {target.path}: {len(files)} file(s), {total} line(s). "
        "The bundle is your source code: review it before pasting it anywhere.",
        err=True,
    )


__all__: list[str] = [
    "BundledFile",
    "RouteTarget",
    "bundle_route_command",
    "collect_route_files",
    "render_bundle",
    "resolve_route",
]
