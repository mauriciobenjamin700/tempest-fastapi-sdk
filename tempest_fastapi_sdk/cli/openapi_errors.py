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

Its known imprecisions, chosen to over-approximate rather than miss a
hole:

* **A call whose receiver cannot be typed resolves by name.** With an
  annotation — ``self.svc: UserService``, or a handler's ``controller:
  UserController`` — the call resolves inside that class's hierarchy.
  Without one, two methods named ``get_by_id`` on different classes are
  treated as one node and their exceptions merge, inflating the reachable
  set (a possible false *unreachable* clear) instead of hiding a hole.
* **A method inherited from outside the scanned tree is not followed.**
  ``super().delete(id)`` on a class whose base is the SDK's resolves to
  nothing: the implementation is not in the scanned paths, so it cannot
  raise a project exception. Falling back to bare-name resolution here is
  what once made every DELETE route claim the one unrelated ``delete``
  in the tree.
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
        name (str): The function or method name, unqualified. Paired with
            ``owner`` it identifies the method for typed resolution.
        file (Path): Source file the function was found in.
        lineno (int): 1-indexed line of the ``def``.
        raised (set[str]): Exception names raised directly in the body.
        documented (set[str]): Exception names listed in the docstring's
            ``Raises:`` section.
        calls (set[str]): Names called **without a receiver** — ``f()`` — plus
            the guards a ``@requires`` decorator names. Resolved only against
            module-level functions, since a bare call can never reach an
            instance method.
        attr_calls (set[str]): Attribute names called on a receiver whose class
            could **not** be typed — ``obj.f()`` where ``obj`` is unannotated.
            Resolved against every function with that name, methods included,
            because the real target is genuinely unknown.
        typed_calls (set[tuple[str, str]]): ``(class, method)`` pairs for
            calls whose receiver resolved to a class — ``self.svc.f()``
            with ``svc`` annotated, an annotated parameter, ``self.f()``
            or ``super().f()``. Resolved inside that class's hierarchy
            only, never by bare name.
        owner (str | None): Class the function is defined in, when it is
            a method. Needed to resolve ``self`` and ``super()``.
        route (RouteInfo | None): Set when the function is a route
            handler.
    """

    name: str
    file: Path
    lineno: int
    raised: set[str] = field(default_factory=set)
    documented: set[str] = field(default_factory=set)
    calls: set[str] = field(default_factory=set)
    attr_calls: set[str] = field(default_factory=set)
    typed_calls: set[tuple[str, str]] = field(default_factory=set)
    owner: str | None = None
    route: RouteInfo | None = None


@dataclass(slots=True)
class ClassInfo:
    """The class-shape facts needed to resolve a typed call.

    Attributes:
        name (str): The class name.
        bases (list[str]): Base class names, in declaration order.
            ``BaseController[Svc, Resp]`` contributes ``BaseController``.
        attr_types (dict[str, str]): Attribute name to annotated class
            name, collected from the class body and from ``__init__``
            (both ``self.x: T = ...`` and ``self.x = x`` where the
            parameter ``x`` is annotated ``T``).
        configured_exceptions (dict[str, set[str]]): Exception classes
            handed to the base constructor, keyed by the kwarg that
            received them — ``super().__init__(...,
            not_found_exception=CoinPackNotFoundException)`` yields
            ``{"not_found_exception": {"CoinPackNotFoundException"}}``.
            These are raised by inherited SDK methods, whose bodies are
            not in the scanned tree.
    """

    name: str
    bases: list[str] = field(default_factory=list)
    attr_types: dict[str, str] = field(default_factory=dict)
    configured_exceptions: dict[str, set[str]] = field(default_factory=dict)


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


def _annotation_name(node: ast.expr | None) -> str | None:
    """Return the class name an annotation refers to.

    Args:
        node (ast.expr | None): The annotation expression.

    Returns:
        str | None: ``T`` for ``T``, ``pkg.T`` and ``T[X]`` (a generic's
        own name is what carries the methods). ``None`` for anything
        else — a union, a string forward reference, a literal.
    """
    if isinstance(node, ast.Subscript):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


CONFIGURED_RAISERS: dict[str, frozenset[str]] = {
    "not_found_exception": frozenset(
        {"get", "get_by_id", "resolve", "delete", "soft_delete", "restore"}
    ),
    "create_conflict_exception": frozenset({"add", "save_with_outbox", "add_audited"}),
    "update_conflict_exception": frozenset(
        {"update", "update_audited", "soft_delete", "restore"}
    ),
    "bulk_create_conflict_exception": frozenset(
        {"add_all", "bulk_create_values", "bulk_upsert"}
    ),
    "bulk_update_conflict_exception": frozenset({"update_many", "bulk_update"}),
}
"""Which inherited method raises the class handed to each constructor kwarg.

A repository configures ``not_found_exception=CoinPackNotFoundException`` and
then never mentions it again: the ``raise`` lives in
:class:`~tempest_fastapi_sdk.BaseRepository`, outside the scanned tree, so the
analyzer used to report the 404 of every such route as undeclared. This table is
how a configured class is attributed to the methods that actually raise it —
``add`` cannot produce a 404 and ``get_by_id`` cannot produce a create conflict,
so a blanket "any configured exception" would just trade a false negative for a
false positive.

Entries are **transitive**: ``soft_delete`` and ``restore`` never mention a
conflict class, but both call ``self.update(...)``, so both can surface the
update conflict as well as the 404. That pair was missing from the first version
of this table and the test below is what caught it.

``conflict_exception`` is deliberately absent: it is the blanket fallback for
the four conflict kwargs, so it is expanded to their union at lookup time.
``tests/cli/test_openapi_errors_configured.py`` asserts this table against
``BaseRepository`` itself — following ``self.*`` calls the same way — so it
cannot drift as that API changes.
"""

CONFLICT_FALLBACK_KWARG: str = "conflict_exception"
"""Kwarg whose class covers every conflict path not overridden individually."""

DELEGATION_ATTRS: frozenset[str] = frozenset({"service", "repository"})
"""Attributes the SDK's own pass-throughs delegate to.

``BaseController`` forwards to ``self.service`` and ``BaseService`` forwards to
``self.repository`` — those two names, and no others. Following *every*
annotated attribute instead looked reasonable and was badly wrong in practice: a
service holding ``self.user_repository`` and ``self.category_repository``
alongside its own donated their configured 404s to every route that reached it,
turning one missing exception into four wrong ones.
"""

_CONFIGURED_KWARGS: frozenset[str] = frozenset(
    {*CONFIGURED_RAISERS, CONFLICT_FALLBACK_KWARG}
)
"""Constructor kwargs whose value is an exception class worth tracking."""


def _configured_in_init(node: ast.ClassDef) -> dict[str, set[str]]:
    """Collect the exception classes a class hands to its base constructor.

    Args:
        node (ast.ClassDef): The class to inspect.

    Returns:
        dict[str, set[str]]: Kwarg name to the exception class names passed
        to it, over every call inside ``__init__``. Reads
        ``super().__init__(...)`` and an explicit
        ``BaseRepository.__init__(self, ...)`` alike, since only the
        keyword matters.
    """
    configured: dict[str, set[str]] = {}
    for stmt in node.body:
        if not isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if stmt.name != "__init__":
            continue
        for child in ast.walk(stmt):
            if not isinstance(child, ast.Call):
                continue
            for keyword in child.keywords:
                if keyword.arg is None or keyword.arg not in _CONFIGURED_KWARGS:
                    continue
                name = _annotation_name(keyword.value)
                if name is not None:
                    configured.setdefault(keyword.arg, set()).add(name)
    return configured


def _class_attr_types(node: ast.ClassDef) -> dict[str, str]:
    """Collect the annotated attribute types of a class.

    Four shapes are read, because the layered services in scope use all
    four: a class-body annotation (``service: UserService``), an
    annotated assignment in ``__init__`` (``self.service: UserService =
    service``), a plain assignment from an annotated parameter
    (``self.service = service`` with ``service: UserService``), and — for
    the names in :data:`DELEGATION_ATTRS` only — an ``__init__``
    parameter that is never assigned at all, because the SDK base stores
    it under the same name::

        class CoinPackController(BaseController[...]):
            def __init__(self, service: CoinPackService) -> None:
                super().__init__(service)

    Without that last shape the controller looks attribute-less, and the
    chain from a route down to the repository's configured exceptions
    breaks at its first link.

    Args:
        node (ast.ClassDef): The class to inspect.

    Returns:
        dict[str, str]: Attribute name to annotated class name.
    """
    types: dict[str, str] = {}
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            annotated = _annotation_name(stmt.annotation)
            if annotated is not None:
                types[stmt.target.id] = annotated
        if not isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if stmt.name != "__init__":
            continue
        params: dict[str, str] = {}
        args = stmt.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            annotated = _annotation_name(arg.annotation)
            if annotated is not None:
                params[arg.arg] = annotated
        for child in ast.walk(stmt):
            if isinstance(child, ast.AnnAssign):
                target = child.target
                if isinstance(target, ast.Attribute) and _is_self(target.value):
                    annotated = _annotation_name(child.annotation)
                    if annotated is not None:
                        types[target.attr] = annotated
            elif isinstance(child, ast.Assign) and isinstance(child.value, ast.Name):
                source = params.get(child.value.id)
                if source is None:
                    continue
                for assigned in child.targets:
                    if isinstance(assigned, ast.Attribute) and _is_self(assigned.value):
                        types[assigned.attr] = source
        for attr in DELEGATION_ATTRS:
            annotated = params.get(attr)
            if annotated is not None:
                types.setdefault(attr, annotated)
    return types


def _is_self(node: ast.expr) -> bool:
    """Return whether an expression is the bare name ``self``.

    Args:
        node (ast.expr): The expression to test.

    Returns:
        bool: True for ``self``, False otherwise.
    """
    return isinstance(node, ast.Name) and node.id == "self"


def _base_names(node: ast.ClassDef) -> list[str]:
    """Return the resolvable base class names of a class.

    Args:
        node (ast.ClassDef): The class to inspect.

    Returns:
        list[str]: Base names in declaration order, skipping bases too
        dynamic to resolve. A subscripted generic contributes the
        generic's own name.
    """
    names: list[str] = []
    for base in node.bases:
        name = _annotation_name(base)
        if name is not None:
            names.append(name)
    return names


GENERIC_DELEGATES: dict[str, dict[int, str]] = {
    "BaseService": {0: "repository"},
    "BaseController": {0: "service"},
}
"""Where an SDK base's generic parameters name the object it delegates to.

``BaseService[RepositoryT, ResponseT]`` stores its first parameter as
``self.repository``; ``BaseController[ServiceT, ResponseT]`` stores its first as
``self.service``. A concrete class that overrides nothing has **no** ``__init__``
to read::

    class CategoryService(BaseService[CategoryRepository, CategoryResponseSchema]):
        \"\"\"Business logic for categories.\"\"\"

Its subscript is then the only statement of what it delegates to, and without
reading it the chain from a route to the repository breaks at exactly the classes
that are pure pass-throughs — the common case the layering encourages.
"""


def _generic_delegates(node: ast.ClassDef) -> dict[str, str]:
    """Map delegation attributes declared through a base's generic parameters.

    Args:
        node (ast.ClassDef): The class to inspect.

    Returns:
        dict[str, str]: Attribute name to class name, for every base listed in
        :data:`GENERIC_DELEGATES`. Empty when the class subscripts no known
        base.
    """
    delegates: dict[str, str] = {}
    for base in node.bases:
        if not isinstance(base, ast.Subscript):
            continue
        base_name = _annotation_name(base.value)
        positions = GENERIC_DELEGATES.get(base_name or "")
        if positions is None:
            continue
        arguments = (
            base.slice.elts if isinstance(base.slice, ast.Tuple) else [base.slice]
        )
        for index, attr in positions.items():
            if index >= len(arguments):
                continue
            argument = _annotation_name(arguments[index])
            if argument is not None:
                delegates[attr] = argument
    return delegates


def _iter_classes(tree: ast.Module) -> Iterator[ClassInfo]:
    """Yield a :class:`ClassInfo` for every class in a module.

    Args:
        tree (ast.Module): The parsed module.

    Yields:
        ClassInfo: One entry per class definition.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            attr_types = _generic_delegates(node)
            attr_types.update(_class_attr_types(node))
            yield ClassInfo(
                name=node.name,
                bases=_base_names(node),
                attr_types=attr_types,
                configured_exceptions=_configured_in_init(node),
            )


def _method_owners(tree: ast.Module) -> dict[int, str]:
    """Map each method's ``def`` node to the class that defines it.

    Args:
        tree (ast.Module): The parsed module.

    Returns:
        dict[int, str]: ``id(node)`` of every function defined directly in
        a class body, to that class's name. Nested functions inside a
        method are deliberately absent — they have no ``self``.
    """
    owners: dict[int, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                owners[id(stmt)] = node.name
    return owners


def _receiver_type(
    node: ast.Call,
    owner: str | None,
    attr_types: dict[str, str],
    local_types: dict[str, str],
) -> str | None:
    """Return the class a call's receiver resolves to.

    Args:
        node (ast.Call): The call whose receiver is inspected.
        owner (str | None): Class the calling function belongs to.
        attr_types (dict[str, str]): ``owner``'s annotated attributes.
        local_types (dict[str, str]): Annotated parameters of the calling
            function — how a route handler's ``controller:
            UserController`` becomes resolvable.

    Returns:
        str | None: The receiver's class name, or ``None`` when the call
        has no receiver (``f()``) or the receiver cannot be typed.
    """
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    receiver = func.value
    if _is_self(receiver) or _is_super_call(receiver):
        return owner
    if isinstance(receiver, ast.Name):
        return local_types.get(receiver.id)
    if isinstance(receiver, ast.Attribute) and _is_self(receiver.value):
        return attr_types.get(receiver.attr)
    return None


def _is_super_call(node: ast.expr) -> bool:
    """Return whether an expression is a ``super()`` call.

    ``super().delete(id)`` is the shape that made every delete route
    inherit another domain's exceptions: the receiver is not ``self``, so
    the call fell through to bare-name resolution.

    Args:
        node (ast.expr): The expression to test.

    Returns:
        bool: True for ``super()`` and ``super(C, self)``.
    """
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "super"
    )


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

        Only the function **body** is walked for calls. Walking the whole
        node swept in the route decorator, so ``@router.delete(...)``
        registered a call to ``delete`` — which then matched every
        unrelated ``delete`` in the project by name. That is why a
        coin-pack route was reported as raising
        ``CategoryInUseException``: the only ``delete`` in the tree was
        the category repository's. ``get`` and ``post`` collide the same
        way wherever a project happens to define methods with those
        names.
    """
    from tempest_fastapi_sdk.cli.permissions import guard_names

    owners = _method_owners(tree)
    attrs_by_class = {info.name: info.attr_types for info in _iter_classes(tree)}

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        owner = owners.get(id(node))
        attr_types = attrs_by_class.get(owner or "", {})
        local_types: dict[str, str] = {}
        args = node.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            annotated = _annotation_name(arg.annotation)
            if annotated is not None:
                local_types[arg.arg] = annotated
        info = FunctionInfo(
            name=node.name,
            file=path,
            lineno=node.lineno,
            documented=_docstring_raises(node),
            route=_route_of(node),
            calls=set(guard_names(node)),
            owner=owner,
        )
        for statement in node.body:
            for child in ast.walk(statement):
                if isinstance(child, ast.Raise):
                    name = _raised_name(child)
                    if name is not None:
                        info.raised.add(name)
                elif isinstance(child, ast.Call):
                    callee = _called_name(child)
                    if callee is None:
                        continue
                    receiver = _receiver_type(child, owner, attr_types, local_types)
                    if receiver is not None:
                        info.typed_calls.add((receiver, callee))
                    elif isinstance(child.func, ast.Attribute):
                        info.attr_calls.add(callee)
                    else:
                        info.calls.add(callee)
        yield info


@dataclass(slots=True)
class CallGraph:
    """The indexes needed to walk calls, built once per analysis run.

    Attributes:
        functions (list[FunctionInfo]): Every analyzed function.
        by_name (dict[str, list[FunctionInfo]]): Functions grouped by
            unqualified name, for edges whose receiver could not be typed.
        by_qualname (dict[tuple[str, str], list[FunctionInfo]]): Methods
            indexed by ``(class, method)``, for typed edges.
        bases (dict[str, list[str]]): Class name to base class names, used
            to walk a hierarchy when the receiver's own class does not
            define the method.
        classes (dict[str, ClassInfo]): Every analyzed class by name, for
            the annotated attributes and configured exceptions needed to
            attribute a raise that happens inside an inherited method.
    """

    functions: list[FunctionInfo] = field(default_factory=list)
    by_name: dict[str, list[FunctionInfo]] = field(default_factory=dict)
    by_qualname: dict[tuple[str, str], list[FunctionInfo]] = field(default_factory=dict)
    bases: dict[str, list[str]] = field(default_factory=dict)
    classes: dict[str, ClassInfo] = field(default_factory=dict)


def build_call_graph(parsed: Iterable[tuple[Path, ast.Module]]) -> CallGraph:
    """Index every function and class in the parsed modules.

    Shared by the route analyzer and the permission checker so both
    resolve a call the same way — a guard's reachable exceptions are
    computed with the same edges as a route's.

    Args:
        parsed (Iterable[tuple[Path, ast.Module]]): The parsed modules,
            paired with the file each came from.

    Returns:
        CallGraph: The populated indexes.
    """
    graph = CallGraph()
    for file, tree in parsed:
        graph.functions.extend(_iter_functions(tree, file))
        for class_info in _iter_classes(tree):
            graph.bases.setdefault(class_info.name, class_info.bases)
            graph.classes.setdefault(class_info.name, class_info)
    for info in graph.functions:
        graph.by_name.setdefault(info.name, []).append(info)
        if info.owner is not None:
            graph.by_qualname.setdefault((info.owner, info.name), []).append(info)
    return graph


def _typed_targets(
    receiver: str,
    method: str,
    by_qualname: dict[tuple[str, str], list[FunctionInfo]],
    bases: dict[str, list[str]],
) -> list[FunctionInfo]:
    """Resolve ``receiver.method`` inside ``receiver``'s own hierarchy.

    Args:
        receiver (str): The receiver's class name.
        method (str): The called method name.
        by_qualname (dict[tuple[str, str], list[FunctionInfo]]): Methods
            indexed by ``(class, method)``.
        bases (dict[str, list[str]]): Class name to base class names.

    Returns:
        list[FunctionInfo]: The methods found, walking bases breadth-first
        and stopping at the first class that defines the name — the
        textual stand-in for an MRO. Empty when no analyzed class in the
        hierarchy defines it, which is the normal answer for a method
        inherited from the SDK: the implementation lives outside the
        scanned tree and cannot raise a project exception.
    """
    queue: list[str] = [receiver]
    seen: set[str] = {receiver}
    while queue:
        current = queue.pop(0)
        found = by_qualname.get((current, method))
        if found:
            return found
        for base in bases.get(current, ()):
            if base not in seen:
                seen.add(base)
                queue.append(base)
    return []


def _configured_for(class_info: ClassInfo, method: str) -> set[str]:
    """Return the configured exceptions ``method`` raises on one class.

    Args:
        class_info (ClassInfo): The class whose constructor configuration is
            read.
        method (str): The inherited method being called.

    Returns:
        set[str]: Exception names, empty when the method raises none of the
        configured kinds — ``add`` cannot produce a 404, ``get_by_id``
        cannot produce a create conflict.
    """
    found: set[str] = set()
    for kwarg, methods in CONFIGURED_RAISERS.items():
        if method not in methods:
            continue
        found |= class_info.configured_exceptions.get(kwarg, set())
        if kwarg != "not_found_exception":
            found |= class_info.configured_exceptions.get(
                CONFLICT_FALLBACK_KWARG, set()
            )
    return found


def _inherited_exceptions(
    receiver: str,
    method: str,
    graph: CallGraph,
) -> tuple[list[FunctionInfo], set[str]]:
    """Attribute a raise that happens inside an inherited method.

    A repository writes ``not_found_exception=CoinPackNotFoundException``
    once, in its constructor, and never names the class again — the
    ``raise`` lives in ``BaseRepository``, outside the scanned tree. The
    route that deletes a coin pack therefore produces a 404 that no
    ``raise`` statement in the project can show.

    Resolution walks the delegation chain the SDK's own pass-throughs
    create: the receiver, its bases, and the classes of the attributes in
    :data:`DELEGATION_ATTRS` — a controller's ``service``, a service's
    ``repository``. Those layers forward a call by the same method name,
    so ``CoinPackController.delete`` reaching ``CoinPackRepository``'s
    configured 404 is the edge the runtime actually takes.

    Args:
        receiver (str): The receiver's class name.
        method (str): The called method name.
        graph (CallGraph): The analyzed classes.

    Returns:
        tuple[list[FunctionInfo], set[str]]: The methods found further down
        the chain, and the exception names configured for this method
        anywhere in it. A layer that overrides the method — a repository
        whose own ``delete`` translates an ``IntegrityError`` — is returned
        as a target so its ``raise`` statements are walked like any other;
        collecting only configured classes would miss it.
    """
    targets: list[FunctionInfo] = []
    found: set[str] = set()
    queue: list[str] = [receiver]
    seen: set[str] = {receiver}
    while queue:
        name = queue.pop(0)
        current = graph.classes.get(name)
        if current is None:
            continue
        found |= _configured_for(current, method)
        if name != receiver:
            targets.extend(graph.by_qualname.get((name, method), ()))
        delegates = [
            class_name
            for attr, class_name in current.attr_types.items()
            if attr in DELEGATION_ATTRS
        ]
        for candidate in (*current.bases, *delegates):
            if candidate not in seen:
                seen.add(candidate)
                queue.append(candidate)
    return targets, found


def _reachable_exceptions(
    start: FunctionInfo,
    graph: CallGraph,
    known: set[str],
) -> set[str]:
    """Union the exceptions reachable from a handler's call graph.

    Three kinds of edge are followed, and the difference is what keeps the
    report honest:

    * A **typed** edge (``self.svc.f()``, ``super().f()``, an annotated
      parameter) resolves inside the receiver's class hierarchy only.
      Finding nothing there means the method is inherited from outside the
      scanned tree, so no edge is followed at all.
    * A **bare** edge — ``f()``, with no receiver — resolves only against
      module-level functions. A bare call cannot reach an instance method, and
      treating it as if it could is how ``update(UserModel)`` (SQLAlchemy's
      ``update``) reached an unrelated ``CoinPackService.update`` and put a coin
      pack's 404 on a category route. Imported helpers still resolve, since they
      are module-level too.
    * An **attribute** edge — ``obj.f()`` where ``obj``'s class is unknown —
      still follows every function with that name, methods included,
      over-approximating rather than missing.

    Resolving everything by bare name — as this did before — made every
    ``super().delete(id)`` reach every other ``delete`` in the project,
    so a coin-pack route was reported as raising ``CategoryInUseException``.
    Delete routes suffered most because ``delete`` is the most-colliding
    name in a layered service.

    Args:
        start (FunctionInfo): The route handler to walk from.
        graph (CallGraph): The indexes to resolve edges against.
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
        targets: list[FunctionInfo] = []
        for callee in current.calls:
            targets.extend(
                target
                for target in graph.by_name.get(callee, ())
                if target.owner is None
            )
        for callee in current.attr_calls:
            targets.extend(graph.by_name.get(callee, ()))
        for receiver, method in current.typed_calls:
            resolved = _typed_targets(receiver, method, graph.by_qualname, graph.bases)
            targets.extend(resolved)
            if not resolved:
                delegated, configured = _inherited_exceptions(receiver, method, graph)
                targets.extend(delegated)
                found |= configured & known
        for target in targets:
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
    graph = build_call_graph(parsed)

    findings: list[RouteFinding] = []
    for info in graph.functions:
        if info.route is None:
            continue
        reachable = _reachable_exceptions(info, graph, known)
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
