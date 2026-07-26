"""Static drift check between raised and documented ``AppException``s.

:func:`tempest_fastapi_sdk.error_responses` and
:func:`tempest_fastapi_sdk.raises` are explicit by design — the list of
exceptions a route can produce is versioned, type-checked and free of
import-time magic. The cost of explicitness is that a list can go stale:
a service starts raising a new exception three layers down and nobody
updates the route decorator.

This module finds that drift **without running the application**. It
parses the project's source with :mod:`ast`, walks
``router -> controller -> service -> repository`` by call name, collects
the exceptions each function raises (from ``raise`` statements *and* from
the Google-style ``Raises:`` docstring sections the project convention
already requires), and compares the union against what each route
declared. It reports both directions:

* **undocumented** — reachable in the flow, absent from the route. The
  hole a frontend developer falls into.
* **unreachable** — declared on the route, never found in the flow. An
  inflated list that documents errors that cannot happen.

Deliberately static and deliberately outside the runtime. Call-graph
analysis is too fragile to drive a response schema in production, but it
is perfectly acceptable as a CI step that exits non-zero.

Its two known imprecisions, both chosen to over-approximate rather than
miss a hole:

* **Calls resolve by name, not by type.** Two methods named
  ``get_by_id`` on different classes are treated as one node, so their
  exceptions merge. This inflates the reachable set (a possible false
  *unreachable* clear) instead of hiding a real hole.
* **Dynamic raises are invisible.** ``raise EXCEPTION_MAP[key]`` cannot
  be resolved. Documenting the exception in the ``Raises:`` section
  covers it, which the project convention already requires.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

HTTP_METHODS: frozenset[str] = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
)
"""Decorator attribute names that mark a function as a route handler."""

SDK_EXCEPTIONS: frozenset[str] = frozenset(
    {
        "AppException",
        "ConflictException",
        "ExpiredTokenException",
        "FileTooLargeException",
        "ForbiddenException",
        "InvalidFileTypeException",
        "InvalidTokenException",
        "NotFoundException",
        "OAuthError",
        "TooManyRequestsException",
        "UnauthorizedException",
        "ValidationException",
    }
)
"""SDK exception classes a project subclasses or raises directly."""

_EXCEPTION_MODULE_RE = re.compile(r"(^|\.)(exceptions?|errors?)$")
_RAISES_SECTION_RE = re.compile(r"^\s*Raises:\s*$")
_RAISES_ENTRY_RE = re.compile(r"^\s*([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*)\s*:")
_SECTION_RE = re.compile(
    r"^\s*(Args|Arguments|Returns|Yields|Raises|Attributes|Note|Notes|Example|"
    r"Examples|Warns|Warning|Warnings|See Also|References|Todo)\s*:\s*$"
)


@dataclass(slots=True)
class FunctionInfo:
    """Everything the analyzer needs to know about one function.

    Attributes:
        name (str): The function or method name (unqualified — the
            analyzer resolves calls by name).
        file (Path): Source file the function was found in.
        lineno (int): 1-indexed line of the ``def``.
        raised (set[str]): Exception names raised directly in the body.
        documented (set[str]): Exception names listed in the docstring's
            ``Raises:`` section.
        calls (set[str]): Names this function calls, used as call-graph
            edges. Holds the bare name for ``f()`` and the attribute for
            ``self.svc.f()``.
        route (RouteInfo | None): Set when the function is a route
            handler.
    """

    name: str
    file: Path
    lineno: int
    raised: set[str] = field(default_factory=set)
    documented: set[str] = field(default_factory=set)
    calls: set[str] = field(default_factory=set)
    route: RouteInfo | None = None


@dataclass(slots=True)
class RouteInfo:
    """The route decoration found on a handler.

    Attributes:
        method (str): Upper-cased HTTP method (``"POST"``).
        path (str): The path literal from the decorator, or ``"?"`` when
            it was not a plain string.
        declared (set[str]): Exception names the route documents, from
            ``responses=error_responses(...)`` and/or ``@raises(...)``.
        has_declaration (bool): Whether any declaration was found at all.
            Distinguishes "declared nothing" from "declared an empty
            list".
        decorator_end (tuple[int, int] | None): ``(line, col)`` of the
            closing parenthesis of the route decorator call, where a new
            ``responses=`` argument is inserted.
        error_responses_end (tuple[int, int] | None): Same, for an
            existing ``error_responses(...)`` call, where missing
            exceptions are appended.
        raises_end (tuple[int, int] | None): Same, for an existing
            ``@raises(...)`` decorator.
        declares_empty_call (bool): Whether the existing declaration call
            has no positional arguments yet. Descriptive only — the writer
            derives the separator it needs from the source itself
            (``openapi_fix._separator_before``), since this flag cannot see
            the trailing comma a multi-line call carries.
    """

    method: str
    path: str
    declared: set[str] = field(default_factory=set)
    has_declaration: bool = False
    decorator_end: tuple[int, int] | None = None
    error_responses_end: tuple[int, int] | None = None
    raises_end: tuple[int, int] | None = None
    declares_empty_call: bool = False


@dataclass(slots=True)
class RouteFinding:
    """Drift found on one route.

    Attributes:
        function (FunctionInfo): The handler.
        route (RouteInfo): Its route decoration.
        undocumented (list[str]): Reachable but not declared.
        unreachable (list[str]): Declared but not reachable.
    """

    function: FunctionInfo
    route: RouteInfo
    undocumented: list[str]
    unreachable: list[str]

    @property
    def location(self) -> str:
        """Return a ``path:line`` reference to the handler."""
        return f"{self.function.file}:{self.function.lineno}"


def _docstring_raises(node: ast.AST) -> set[str]:
    """Extract the exception names from a Google-style ``Raises:`` section.

    Args:
        node (ast.AST): A module, class or function node.

    Returns:
        set[str]: The names found, with any dotted prefix stripped
        (``exceptions.UserNotFound`` yields ``UserNotFound``). Empty when
        the node has no docstring or no ``Raises:`` section.
    """
    if not isinstance(
        node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
    ):
        return set()
    doc = ast.get_docstring(node, clean=False)
    if not doc:
        return set()
    names: set[str] = set()
    inside = False
    for line in doc.splitlines():
        if _RAISES_SECTION_RE.match(line):
            inside = True
            continue
        if not inside:
            continue
        if not line.strip():
            continue
        if _SECTION_RE.match(line):
            break
        match = _RAISES_ENTRY_RE.match(line)
        if match is None:
            continue
        for raw in match.group(1).split(","):
            names.add(raw.strip().rsplit(".", 1)[-1])
    return names


def _raised_name(node: ast.Raise) -> str | None:
    """Return the exception class name a ``raise`` statement produces.

    Args:
        node (ast.Raise): The statement to inspect.

    Returns:
        str | None: The class name for ``raise X``, ``raise X(...)`` and
        ``raise pkg.X(...)``. ``None`` for a bare ``raise`` (re-raise) or
        an expression too dynamic to resolve statically.
    """
    exc = node.exc
    if exc is None:
        return None
    if isinstance(exc, ast.Call):
        exc = exc.func
    if isinstance(exc, ast.Name):
        return exc.id
    if isinstance(exc, ast.Attribute):
        return exc.attr
    return None


def _called_name(node: ast.Call) -> str | None:
    """Return the callee name of a call expression.

    Args:
        node (ast.Call): The call to inspect.

    Returns:
        str | None: ``f`` for ``f()``, and the trailing attribute for
        ``self.service.f()``. ``None`` when the callee is an expression
        (a subscript, a lambda, …).
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _exception_args(node: ast.Call) -> set[str]:
    """Return the class names passed positionally to a call.

    Args:
        node (ast.Call): An ``error_responses(...)`` or ``raises(...)``
            call.

    Returns:
        set[str]: The positional argument names. Starred arguments
        (``error_responses(*ALL)``) cannot be resolved statically and are
        skipped — such a route reports no declaration rather than a wrong
        one.
    """
    names: set[str] = set()
    for arg in node.args:
        if isinstance(arg, ast.Name):
            names.add(arg.id)
        elif isinstance(arg, ast.Attribute):
            names.add(arg.attr)
    return names


def _declared_in(node: ast.AST) -> tuple[set[str], bool]:
    """Collect declared exceptions from any nested declaration call.

    Walks ``node`` looking for ``error_responses(...)`` / ``raises(...)``
    calls, so ``responses={**error_responses(A, B), 418: {...}}`` is
    handled as naturally as the plain form.

    Args:
        node (ast.AST): A decorator expression.

    Returns:
        tuple[set[str], bool]: The declared names, and whether a
        declaration call was present at all.
    """
    names: set[str] = set()
    found = False
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        callee = _called_name(child)
        if callee in {"error_responses", "raises"}:
            found = True
            names |= _exception_args(child)
    return names, found


def _declaration_anchors(
    node: ast.AST,
) -> tuple[tuple[int, int] | None, tuple[int, int] | None, bool]:
    """Locate the existing declaration calls inside a decorator.

    Args:
        node (ast.AST): A decorator expression.

    Returns:
        tuple[tuple[int, int] | None, tuple[int, int] | None, bool]: The
        end position of an ``error_responses(...)`` call, the end position
        of a ``raises(...)`` call, and whether the one that was found has
        no positional arguments yet. Positions are ``(line, col)`` of the
        closing parenthesis, which is where a writer appends.
    """
    er: tuple[int, int] | None = None
    ra: tuple[int, int] | None = None
    empty = False
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        callee = _called_name(child)
        if callee not in {"error_responses", "raises"}:
            continue
        end = (child.end_lineno or 0, child.end_col_offset or 0)
        if callee == "error_responses":
            er = end
        else:
            ra = end
        empty = not child.args
    return er, ra, empty


def _route_of(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> RouteInfo | None:
    """Build the :class:`RouteInfo` for a handler, if it is one.

    A function counts as a route handler when one of its decorators is a
    call on an attribute named after an HTTP method
    (``@router.post(...)``, ``@app.get(...)``). That covers every FastAPI
    routing style without importing the module.

    Args:
        node (ast.FunctionDef | ast.AsyncFunctionDef): The function.

    Returns:
        RouteInfo | None: The route decoration, or ``None`` when the
        function is not a handler.
    """
    route: RouteInfo | None = None
    declared: set[str] = set()
    has_declaration = False
    error_responses_end: tuple[int, int] | None = None
    raises_end: tuple[int, int] | None = None
    declares_empty = False
    for decorator in node.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr in HTTP_METHODS
        ):
            path = "?"
            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                value = decorator.args[0].value
                if isinstance(value, str):
                    path = value
            route = RouteInfo(
                method=decorator.func.attr.upper(),
                path=path,
                decorator_end=(
                    decorator.end_lineno or 0,
                    decorator.end_col_offset or 0,
                ),
            )
        names, found = _declared_in(decorator)
        declared |= names
        has_declaration = has_declaration or found
        er, ra, empty = _declaration_anchors(decorator)
        if er is not None:
            error_responses_end = er
            declares_empty = empty
        if ra is not None:
            raises_end = ra
            declares_empty = empty
    if route is None:
        return None
    route.declared = declared
    route.has_declaration = has_declaration
    route.error_responses_end = error_responses_end
    route.raises_end = raises_end
    route.declares_empty_call = declares_empty
    return route


def _exception_class_names(trees: Iterable[ast.Module]) -> set[str]:
    """Collect the names of every exception class the project defines.

    Three sources, unioned:

    1. :data:`SDK_EXCEPTIONS` — the SDK classes a project subclasses or
       raises directly.
    2. Every ``ClassDef`` reachable transitively from a known exception,
       so a deep hierarchy (``AppException`` -> ``DomainException`` ->
       ``UserNotFoundException``) is captured regardless of file order.
    3. Names imported from a module whose dotted path ends in
       ``exception(s)`` / ``error(s)`` (:data:`_EXCEPTION_MODULE_RE`),
       matching the ``core/exceptions.py`` convention. This keeps the
       analyzer useful when ``--path`` points at a single file whose
       exception classes are defined elsewhere.

    Knowing this set is what lets the analyzer ignore ``raise
    ValueError`` and ``raise StopIteration`` without maintaining a
    denylist of builtins.

    Args:
        trees (Iterable[ast.Module]): The parsed project modules.

    Returns:
        set[str]: Every known exception class name.
    """
    modules = list(trees)
    bases: dict[str, set[str]] = {}
    imported: set[str] = set()
    for tree in modules:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and _EXCEPTION_MODULE_RE.search(node.module):
                    imported.update(alias.asname or alias.name for alias in node.names)
                continue
            if not isinstance(node, ast.ClassDef):
                continue
            names = {
                base.id if isinstance(base, ast.Name) else base.attr
                for base in node.bases
                if isinstance(base, ast.Name | ast.Attribute)
            }
            bases.setdefault(node.name, set()).update(names)
    known = set(SDK_EXCEPTIONS) | imported
    changed = True
    while changed:
        changed = False
        for name, parents in bases.items():
            if name in known:
                continue
            if parents & known or any(p.endswith("Exception") for p in parents):
                known.add(name)
                changed = True
    return known


def _iter_functions(
    tree: ast.Module,
    path: Path,
) -> Iterator[FunctionInfo]:
    """Yield a :class:`FunctionInfo` for every function in a module.

    Args:
        tree (ast.Module): The parsed module.
        path (Path): The file the module came from, for reporting.

    Yields:
        FunctionInfo: One entry per (possibly nested) function
        definition, with its raises / docstring / call data filled in.

    Notes:
        The guards named by a ``@requires(...)`` decorator become
        call-graph edges too. A guard denies by raising an
        ``AppException``, so its exceptions are as reachable from the
        route as those of any function the body calls — and the ``ast``
        walk would otherwise see only the ``requires`` call itself, not
        the names passed to it. Imported inside the loop because
        :mod:`tempest_fastapi_sdk.cli.permissions` imports this module.
    """
    from tempest_fastapi_sdk.cli.permissions import guard_names

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        info = FunctionInfo(
            name=node.name,
            file=path,
            lineno=node.lineno,
            documented=_docstring_raises(node),
            route=_route_of(node),
            calls=set(guard_names(node)),
        )
        for child in ast.walk(node):
            if isinstance(child, ast.Raise):
                name = _raised_name(child)
                if name is not None:
                    info.raised.add(name)
            elif isinstance(child, ast.Call):
                callee = _called_name(child)
                if callee is not None:
                    info.calls.add(callee)
        yield info


def _reachable_exceptions(
    start: FunctionInfo,
    by_name: dict[str, list[FunctionInfo]],
    known: set[str],
) -> set[str]:
    """Union the exceptions reachable from a handler's call graph.

    Args:
        start (FunctionInfo): The route handler to walk from.
        by_name (dict[str, list[FunctionInfo]]): Every analyzed function,
            grouped by unqualified name. A name may map to several
            functions; all of them are followed, which over-approximates
            rather than misses.
        known (set[str]): Names that count as exception classes.

    Returns:
        set[str]: Exception names raised or documented anywhere in the
        transitive call graph, including the handler itself.
    """
    found: set[str] = set()
    seen: set[int] = {id(start)}
    queue: list[FunctionInfo] = [start]
    while queue:
        current = queue.pop()
        found |= (current.raised | current.documented) & known
        for callee in current.calls:
            for target in by_name.get(callee, ()):
                if id(target) in seen:
                    continue
                seen.add(id(target))
                queue.append(target)
    return found


def _python_files(paths: Iterable[Path]) -> list[Path]:
    """Expand the given paths into a sorted list of Python files.

    Args:
        paths (Iterable[Path]): Directories (walked recursively) or files.

    Returns:
        list[Path]: The ``*.py`` files to analyze.

    Raises:
        FileNotFoundError: If a given path does not exist.
    """
    files: list[Path] = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"No such path: {path}")
        if path.is_dir():
            files.extend(sorted(path.rglob("*.py")))
        else:
            files.append(path)
    return files


def analyze_paths(paths: Iterable[Path]) -> list[RouteFinding]:
    """Analyze source trees and return the drift found on each route.

    Args:
        paths (Iterable[Path]): Directories (or single files) to scan.
            Directories are walked recursively for ``*.py``.

    Returns:
        list[RouteFinding]: One entry per route that either raises an
        undeclared exception or declares an unreachable one, ordered by
        file then line. Routes with no declaration at all are reported
        with every reachable exception listed as undocumented — that is
        the state the SDK is closing, so staying silent about it would
        defeat the purpose.

    Raises:
        FileNotFoundError: If a given path does not exist.

    Notes:
        A file that fails to parse is skipped rather than aborting the run:
        this is advisory tooling, and one generated or vendored file must
        not blind the whole report.
    """
    files = _python_files(paths)

    parsed: list[tuple[Path, ast.Module]] = []
    for file in files:
        try:
            parsed.append((file, ast.parse(file.read_text(encoding="utf-8"))))
        except (SyntaxError, UnicodeDecodeError):
            continue

    known = _exception_class_names(tree for _, tree in parsed)
    functions: list[FunctionInfo] = []
    for file, tree in parsed:
        functions.extend(_iter_functions(tree, file))

    by_name: dict[str, list[FunctionInfo]] = {}
    for info in functions:
        by_name.setdefault(info.name, []).append(info)

    findings: list[RouteFinding] = []
    for info in functions:
        if info.route is None:
            continue
        reachable = _reachable_exceptions(info, by_name, known)
        declared = info.route.declared & known
        undocumented = sorted(reachable - declared)
        unreachable = sorted(declared - reachable)
        if undocumented or unreachable:
            findings.append(
                RouteFinding(
                    function=info,
                    route=info.route,
                    undocumented=undocumented,
                    unreachable=unreachable,
                )
            )
    findings.sort(key=lambda f: (str(f.function.file), f.function.lineno))
    return findings


def exception_locations(paths: Iterable[Path]) -> dict[str, Path]:
    """Map each project exception class name to the file that defines it.

    A writer that injects ``error_responses(SomeException)`` has to import
    the class too, and the import can only be synthesized from where the
    ``class`` statement actually is. Names defined in more than one file
    are dropped rather than guessed: emitting the wrong import would
    produce code that imports a same-named class from the wrong module,
    which type-checks and fails at runtime.

    Args:
        paths (Iterable[Path]): Directories (or files) to scan.

    Returns:
        dict[str, Path]: Class name to defining file, for unambiguous
        names only.
    """
    seen: dict[str, list[Path]] = {}
    for file in _python_files(paths):
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                seen.setdefault(node.name, []).append(file)
    return {name: files[0] for name, files in seen.items() if len(files) == 1}


def default_source_paths(root: Path) -> list[Path]:
    """Return the conventional source directories under ``root``.

    Args:
        root (Path): The project root (usually the current directory).

    Returns:
        list[Path]: ``[root / "src"]`` or ``[root / "app"]`` — whichever
        exists, matching the two layouts the project convention allows.
        Empty when neither is present, so the caller can report a clear
        error instead of silently scanning nothing.
    """
    return [candidate for name in ("src", "app") if (candidate := root / name).is_dir()]


__all__: list[str] = [
    "HTTP_METHODS",
    "SDK_EXCEPTIONS",
    "FunctionInfo",
    "RouteFinding",
    "RouteInfo",
    "analyze_paths",
    "default_source_paths",
    "exception_locations",
]
