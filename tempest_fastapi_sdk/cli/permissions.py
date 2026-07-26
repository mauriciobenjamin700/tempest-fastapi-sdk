"""Static contract check for the ``@requires`` permission decorator.

:func:`tempest_fastapi_sdk.requires` validates what it can see at import
time — the guards it was handed and the signature it was applied to. Two
whole classes of mistake are outside that reach:

* a guard whose ``raise`` sits behind a condition no test exercises, so
  the ``AppException`` contract is never checked at runtime;
* a guard written as a predicate (``-> bool``, ``return False``), whose
  denial the decorator can only warn about *after* the request was already
  allowed.

This module reads the same contract off the project source with
:mod:`ast`, without importing the application, so a CI step fails the
build instead of a warning scrolling past in production logs. It reuses
the analyzer primitives from
:mod:`tempest_fastapi_sdk.cli.openapi_errors` — the exception-class index,
the call-graph walk and the Google-style ``Raises:`` reader — so both
commands agree on what counts as an exception class.

What it reports, per ``@requires``-decorated function:

* ``no-guards`` (error) — ``@requires()`` with no guard: every request
  passes.
* ``user-param-missing`` (error) — no parameter annotated with a user
  model, and no ``user_param=`` to point at one.
* ``user-param-ambiguous`` (error) — several user-model parameters and no
  ``user_param=`` choosing between them.
* ``guard-arity`` (error) — the guard does not take one required parameter
  (the user) or two (the user plus the injected ``dict[str, Any]`` of
  metadata).
* ``meta-unused`` (error) — the decoration passes ``meta=`` /
  ``include_args=True`` but no guard declares a second parameter to receive
  it, so the configuration does nothing.
* ``guard-async-in-sync`` (error) — an ``async`` guard on a synchronous
  function; the coroutine would never be awaited.
* ``guard-returns-bool`` (error) — a predicate-style guard: its ``False``
  is silently ignored, so the check does not deny.
* ``guard-foreign-exception`` (error) — the guard raises something outside
  the :class:`~tempest_fastapi_sdk.exceptions.base.AppException`
  hierarchy, which the API layer answers as HTTP 500 with no error code.
* ``guard-never-denies`` (warning) — nothing in the guard's call graph
  raises and its docstring documents no ``Raises:``; it cannot deny.
* ``guard-missing-annotation`` (warning) — the user parameter or the
  return type is unannotated, against the project convention.
* ``guard-return-type`` (warning) — the return annotation is neither the
  user, ``None``, nor their union.
* ``guard-meta-missing`` (warning) — the guard declares a metadata
  parameter but the decoration supplies none, so it reads an empty dict.
* ``guard-meta-annotation`` (warning) — the metadata parameter is annotated
  as something that cannot hold a ``dict[str, Any]``.
* ``meta-key-collision`` (warning) — a ``meta=`` literal key shadows a
  parameter name under ``include_args=True``; the literal wins and the
  argument never reaches the guard.
* ``guard-unresolved`` (warning) — the guard argument is not a plain name
  (a lambda, a call), or its definition is outside the scanned paths, or
  its name maps to several definitions. Reported rather than guessed: a
  wrong definition would produce a confident, wrong finding.

Its imprecisions mirror the sibling command's, and for the same reason —
over-report rather than miss:

* **Guards resolve by name.** Two functions named ``order_owner`` in
  different modules make the guard ambiguous, which is reported as
  ``guard-unresolved`` instead of checked against the wrong one.
* **``guard-foreign-exception`` reads direct ``raise`` statements only.**
  An exception raised by a helper the guard calls is attributed to the
  helper, where the fix belongs.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tempest_fastapi_sdk.cli.openapi_errors import (
    CallGraph,
    _docstring_raises,
    _exception_class_names,
    _python_files,
    _raised_name,
    _reachable_exceptions,
    build_call_graph,
)

Severity = Literal["error", "warning"]
"""Whether a finding fails ``--check`` on its own."""

SDK_GUARDS: frozenset[str] = frozenset(
    {"require_authenticated", "require_active", "require_admin"}
)
"""Guards shipped by the SDK, exempt from the source-level checks.

They are defined in ``tempest_fastapi_sdk.auth.guards``, outside the
scanned project paths, and are known to honor the contract. Skipping them
keeps the common call site quiet instead of emitting
``guard-unresolved`` for every route.
"""

USER_MODEL_BASES: frozenset[str] = frozenset({"BaseModel", "BaseUserModel"})
"""Base classes whose subclasses count as the user model."""

_NONE_ANNOTATIONS: frozenset[str] = frozenset({"None", "NoneType"})
_BOOL_ANNOTATIONS: frozenset[str] = frozenset({"bool", "builtins.bool"})


@dataclass(slots=True)
class GuardFinding:
    """One contract violation found on a ``@requires`` usage.

    Attributes:
        file (Path): Source file the finding is anchored to.
        lineno (int): 1-indexed line of the ``def`` it is anchored to.
        function (str): Name of the decorated function the usage belongs
            to.
        guard (str | None): Name of the offending guard, or ``None`` when
            the finding is about the decorated function itself.
        code (str): Machine-readable finding code (``guard-arity``, …).
        message (str): One-line description, including the fix.
        severity (Severity): ``"error"`` fails ``--check``; ``"warning"``
            only fails it under ``--strict``.
    """

    file: Path
    lineno: int
    function: str
    guard: str | None
    code: str
    message: str
    severity: Severity

    @property
    def location(self) -> str:
        """Return a ``path:line`` reference to the finding.

        Returns:
            str: The clickable location.
        """
        return f"{self.file}:{self.lineno}"


@dataclass(slots=True)
class _FunctionNode:
    """A parsed function definition plus where it came from.

    Attributes:
        node (ast.FunctionDef | ast.AsyncFunctionDef): The definition.
        file (Path): The file it was parsed from.
    """

    node: ast.FunctionDef | ast.AsyncFunctionDef
    file: Path


def _is_requires_decorator(decorator: ast.expr) -> ast.Call | None:
    """Return the ``requires(...)`` call of a decorator, if it is one.

    Matches both ``@requires(...)`` and a qualified
    ``@authz.requires(...)`` / ``@tempest_fastapi_sdk.requires(...)``.

    Args:
        decorator (ast.expr): The decorator expression.

    Returns:
        ast.Call | None: The call node, or ``None``.
    """
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if isinstance(func, ast.Name) and func.id == "requires":
        return decorator
    if isinstance(func, ast.Attribute) and func.attr == "requires":
        return decorator
    return None


def guard_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Return the guard names a function's ``@requires`` declares.

    Exposed because the error-documentation analyzer needs the same list:
    a guard raises on denial, so its exceptions are reachable from the
    route and belong in ``error_responses(...)``.

    Args:
        node (ast.FunctionDef | ast.AsyncFunctionDef): The function.

    Returns:
        list[str]: The resolvable guard names, in declaration order.
        Arguments that are not plain names (a lambda, a call, a starred
        iterable) are omitted — :func:`analyze_permissions` reports those
        as ``guard-unresolved``.
    """
    names: list[str] = []
    for decorator in node.decorator_list:
        call = _is_requires_decorator(decorator)
        if call is None:
            continue
        for arg in call.args:
            if isinstance(arg, ast.Name):
                names.append(arg.id)
            elif isinstance(arg, ast.Attribute):
                names.append(arg.attr)
    return names


def _unresolvable_guard_args(call: ast.Call) -> list[str]:
    """Return a label for each guard argument that is not a plain name.

    Args:
        call (ast.Call): The ``requires(...)`` call.

    Returns:
        list[str]: The unparsed source of every argument the analyzer
        cannot follow.
    """
    return [
        ast.unparse(arg)
        for arg in call.args
        if not isinstance(arg, ast.Name | ast.Attribute)
    ]


def _call_guard_names(call: ast.Call) -> list[str]:
    """Return the resolvable guard names of one ``requires(...)`` call.

    Args:
        call (ast.Call): The decorator call.

    Returns:
        list[str]: The plain names passed positionally, in order.
    """
    return [
        arg.id if isinstance(arg, ast.Name) else arg.attr
        for arg in call.args
        if isinstance(arg, ast.Name | ast.Attribute)
    ]


def _unresolved_guard_names(
    call: ast.Call,
    nodes: dict[str, list[_FunctionNode]],
) -> list[str]:
    """Return the guard references whose arity could not be determined.

    Used to hold back the ``meta-unused`` verdict: a guard the checker
    cannot resolve may well be the one that declares the metadata
    parameter, and reporting "no guard receives it" would then be wrong.

    Args:
        call (ast.Call): The decorator call.
        nodes (dict[str, list[_FunctionNode]]): Definitions per name.

    Returns:
        list[str]: Labels of the unresolvable references.
    """
    unresolved = _unresolvable_guard_args(call)
    for name in _call_guard_names(call):
        if name in SDK_GUARDS:
            continue
        if len(nodes.get(name, [])) != 1:
            unresolved.append(name)
    return unresolved


def _explicit_user_param(call: ast.Call) -> str | None:
    """Return the ``user_param=`` literal of a ``requires(...)`` call.

    Args:
        call (ast.Call): The decorator call.

    Returns:
        str | None: The parameter name, or ``None`` when the keyword is
        absent or not a string literal.
    """
    for keyword in call.keywords:
        if keyword.arg != "user_param":
            continue
        if isinstance(keyword.value, ast.Constant) and isinstance(
            keyword.value.value, str
        ):
            return keyword.value.value
    return None


def _user_model_names(trees: Iterable[ast.Module]) -> set[str]:
    """Collect the class names that are user models.

    Starts from :data:`USER_MODEL_BASES` and closes transitively over the
    project's ``ClassDef`` bases, so ``UserModel(BaseUserModel)`` and a
    further ``AdminUser(UserModel)`` are both recognized regardless of
    file order.

    Args:
        trees (Iterable[ast.Module]): The parsed project modules.

    Returns:
        set[str]: Every known user-model class name.
    """
    bases: dict[str, set[str]] = {}
    for tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases.setdefault(node.name, set()).update(
                base.id if isinstance(base, ast.Name) else base.attr
                for base in node.bases
                if isinstance(base, ast.Name | ast.Attribute)
            )
    known = set(USER_MODEL_BASES)
    changed = True
    while changed:
        changed = False
        for name, parents in bases.items():
            if name not in known and parents & known:
                known.add(name)
                changed = True
    return known


def _annotation_mentions_user(annotation: ast.expr | None, users: set[str]) -> bool:
    """Return whether an annotation refers to a user model.

    Accepts the exact class name, any name mentioned inside a union or
    ``Annotated[...]``, and — mirroring the decorator's runtime fallback —
    any name containing ``User``, which keeps the check working when the
    model is imported under ``TYPE_CHECKING`` and never parsed here.

    Args:
        annotation (ast.expr | None): The annotation node.
        users (set[str]): Known user-model class names.

    Returns:
        bool: ``True`` when the annotation denotes a user model.
    """
    if annotation is None:
        return False
    for node in ast.walk(annotation):
        name: str | None = None
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            name = node.value
        if name is None:
            continue
        if name in users or "User" in name:
            return True
    return False


_MAPPING_ANNOTATION_NAMES: frozenset[str] = frozenset(
    {"dict", "Dict", "Mapping", "MutableMapping", "Any", "object"}
)
"""Annotation heads accepted for a guard's metadata parameter."""


def _is_mapping_annotation(annotation: ast.expr) -> bool:
    """Return whether an annotation reads as the injected metadata mapping.

    Args:
        annotation (ast.expr): The annotation node.

    Returns:
        bool: ``True`` for ``dict[...]`` / ``Mapping[...]`` / bare ``dict``
        / ``Any`` — the shapes that can receive a ``dict[str, Any]``.
    """
    node: ast.expr = annotation
    if isinstance(node, ast.Subscript):
        node = node.value
    if isinstance(node, ast.Attribute):
        return node.attr in _MAPPING_ANNOTATION_NAMES
    if isinstance(node, ast.Name):
        return node.id in _MAPPING_ANNOTATION_NAMES
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.split("[", 1)[0].rsplit(".", 1)[-1] in (
            _MAPPING_ANNOTATION_NAMES
        )
    return False


def _supplies_meta(call: ast.Call) -> tuple[bool, bool, list[str]]:
    """Read the metadata configuration off a ``requires(...)`` call.

    Args:
        call (ast.Call): The decorator call.

    Returns:
        tuple[bool, bool, list[str]]: Whether metadata is supplied at all,
        whether ``include_args`` is truthy, and the literal keys of a
        ``meta={...}`` dict (empty when it is not a dict literal — a
        variable is supplied but its keys cannot be read statically).
    """
    supplies = False
    include_args = False
    keys: list[str] = []
    for keyword in call.keywords:
        if keyword.arg == "meta":
            supplies = True
            if isinstance(keyword.value, ast.Dict):
                keys = [
                    key.value
                    for key in keyword.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                ]
        elif keyword.arg == "include_args":
            value = keyword.value
            if (
                isinstance(value, ast.Constant) and value.value is True
            ) or not isinstance(value, ast.Constant):
                supplies = True
                include_args = True
    return supplies, include_args, keys


def _required_positional(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
    """Return the parameters a caller must fill positionally.

    ``self`` / ``cls`` are excluded, so a guard written as a method is
    measured by its real arity.

    Args:
        node (ast.FunctionDef | ast.AsyncFunctionDef): The function.

    Returns:
        list[ast.arg]: The required positional parameters.
    """
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    if positional and positional[0].arg in {"self", "cls"}:
        positional = positional[1:]
    if args.defaults:
        positional = positional[: len(positional) - len(args.defaults)] or []
    return positional


def _user_parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    users: set[str],
) -> list[str]:
    """Return the parameters of a function that carry a user model.

    Args:
        node (ast.FunctionDef | ast.AsyncFunctionDef): The function.
        users (set[str]): Known user-model class names.

    Returns:
        list[str]: The matching parameter names.
    """
    args = node.args
    candidates = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    return [
        arg.arg
        for arg in candidates
        if _annotation_mentions_user(arg.annotation, users)
    ]


def _index_nodes(
    parsed: Iterable[tuple[Path, ast.Module]],
) -> dict[str, list[_FunctionNode]]:
    """Index every function definition by its unqualified name.

    Args:
        parsed (Iterable[tuple[Path, ast.Module]]): The parsed modules.

    Returns:
        dict[str, list[_FunctionNode]]: Definitions per name; a name with
        more than one entry is ambiguous and reported rather than checked.
    """
    index: dict[str, list[_FunctionNode]] = {}
    for file, tree in parsed:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                index.setdefault(node.name, []).append(
                    _FunctionNode(node=node, file=file)
                )
    return index


def _check_guard_definition(
    guard: _FunctionNode,
    *,
    guard_name: str,
    owner: str,
    owner_is_async: bool,
    takes_meta: bool,
    users: set[str],
    known_exceptions: set[str],
    reachable_raises: set[str],
) -> list[GuardFinding]:
    """Check one guard definition against the ``@requires`` contract.

    Args:
        guard (_FunctionNode): The guard's definition and file.
        guard_name (str): The name it was referenced by.
        owner (str): The decorated function's name, for the message.
        owner_is_async (bool): Whether the decorated function is ``async``.
        takes_meta (bool): Whether the ``@requires`` decoration supplies
            metadata (``meta=`` or ``include_args=True``), which is what a
            two-parameter guard needs to be useful.
        users (set[str]): Known user-model class names.
        known_exceptions (set[str]): Names that count as exception classes.
        reachable_raises (set[str]): Exceptions reachable from the guard's
            call graph, used only to decide whether it can deny at all.

    Returns:
        list[GuardFinding]: The findings, possibly empty.
    """
    node = guard.node
    findings: list[GuardFinding] = []

    def add(code: str, message: str, severity: Severity) -> None:
        """Append a finding anchored at the guard definition.

        Args:
            code (str): The finding code.
            message (str): The description.
            severity (Severity): The severity.
        """
        findings.append(
            GuardFinding(
                file=guard.file,
                lineno=node.lineno,
                function=owner,
                guard=guard_name,
                code=code,
                message=message,
                severity=severity,
            )
        )

    positional = _required_positional(node)
    takes_varargs = node.args.vararg is not None
    if len(positional) not in (1, 2) and not (takes_varargs and len(positional) <= 1):
        add(
            "guard-arity",
            f"guard {guard_name!r} takes {len(positional)} required params, "
            f"expected 1 (user) or 2 (user, meta)",
            "error",
        )
    else:
        if positional and positional[0].annotation is None:
            add(
                "guard-missing-annotation",
                f"guard {guard_name!r} parameter {positional[0].arg!r} has no "
                f"type annotation",
                "warning",
            )
        if len(positional) == 2:
            meta_arg = positional[1]
            if meta_arg.annotation is None:
                add(
                    "guard-missing-annotation",
                    f"guard {guard_name!r} metadata parameter "
                    f"{meta_arg.arg!r} has no type annotation; expected "
                    f"dict[str, Any]",
                    "warning",
                )
            elif not _is_mapping_annotation(meta_arg.annotation):
                add(
                    "guard-meta-annotation",
                    f"guard {guard_name!r} declares {meta_arg.arg!r} as "
                    f"{ast.unparse(meta_arg.annotation)}; @requires injects a "
                    f"dict[str, Any] as the second argument",
                    "warning",
                )
            if not takes_meta:
                add(
                    "guard-meta-missing",
                    f"guard {guard_name!r} declares a metadata parameter but "
                    f"the @requires on {owner} passes neither meta= nor "
                    f"include_args=True; it would read an empty dict",
                    "warning",
                )

    for index, arg in enumerate(node.args.kwonlyargs):
        if node.args.kw_defaults[index] is None:
            add(
                "guard-arity",
                f"guard {guard_name!r} has a required keyword-only param "
                f"{arg.arg!r}; a guard receives only the user",
                "error",
            )

    if isinstance(node, ast.AsyncFunctionDef) and not owner_is_async:
        add(
            "guard-async-in-sync",
            f"guard {guard_name!r} is async but {owner} is not; make the "
            f"decorated function async",
            "error",
        )

    returns = node.returns
    if returns is None:
        add(
            "guard-missing-annotation",
            f"guard {guard_name!r} has no return annotation; expected the user "
            f"model, None, or their union",
            "warning",
        )
    else:
        rendered = ast.unparse(returns)
        if rendered in _BOOL_ANNOTATIONS:
            add(
                "guard-returns-bool",
                f"guard {guard_name!r} returns bool; a guard denies by raising "
                f"an AppException, so a False return is ignored and the check "
                f"never denies",
                "error",
            )
        elif (
            not _annotation_mentions_user(returns, users)
            and rendered not in _NONE_ANNOTATIONS
        ):
            add(
                "guard-return-type",
                f"guard {guard_name!r} returns {rendered}; expected the user "
                f"model, None, or their union",
                "warning",
            )

    direct_raises: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Raise):
            name = _raised_name(child)
            if name is not None:
                direct_raises.add(name)
    foreign = sorted(direct_raises - known_exceptions)
    for name in foreign:
        add(
            "guard-foreign-exception",
            f"guard {guard_name!r} raises {name}, which is not an AppException "
            f"subclass; the API layer answers it as HTTP 500 without an error "
            f"code",
            "error",
        )

    documented = _docstring_raises(node)
    if not direct_raises and not documented and not reachable_raises:
        add(
            "guard-never-denies",
            f"guard {guard_name!r} never raises and documents no Raises:; it "
            f"cannot deny access",
            "warning",
        )

    return findings


def _check_usage(
    owner: _FunctionNode,
    call: ast.Call,
    *,
    users: set[str],
    known_exceptions: set[str],
    nodes: dict[str, list[_FunctionNode]],
    graph: CallGraph,
) -> list[GuardFinding]:
    """Check one ``@requires`` decoration and every guard it names.

    Args:
        owner (_FunctionNode): The decorated function.
        call (ast.Call): Its ``requires(...)`` decorator call.
        users (set[str]): Known user-model class names.
        known_exceptions (set[str]): Names that count as exception classes.
        nodes (dict[str, list[_FunctionNode]]): Definitions per name.
        graph (CallGraph): Shared call-graph indexes, so a guard's
            reachable exceptions resolve with the same typed edges the
            route analyzer uses.

    Returns:
        list[GuardFinding]: The findings for this decoration.
    """
    node = owner.node
    owner_name = node.name
    findings: list[GuardFinding] = []

    def add(
        code: str, message: str, severity: Severity, guard: str | None = None
    ) -> None:
        """Append a finding anchored at the decorated function.

        Args:
            code (str): The finding code.
            message (str): The description.
            severity (Severity): The severity.
            guard (str | None): The guard involved, when any.
        """
        findings.append(
            GuardFinding(
                file=owner.file,
                lineno=node.lineno,
                function=owner_name,
                guard=guard,
                code=code,
                message=message,
                severity=severity,
            )
        )

    if not call.args and not call.keywords:
        add(
            "no-guards",
            "@requires() declares no guard; every request would pass",
            "error",
        )
    elif not call.args:
        add(
            "no-guards",
            "@requires(...) declares no guard, only keywords; every request would pass",
            "error",
        )

    supplies_meta, include_args, meta_keys = _supplies_meta(call)
    explicit = _explicit_user_param(call)
    parameters = {
        arg.arg
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
    }
    if explicit is not None:
        if explicit not in parameters:
            add(
                "user-param-missing",
                f"@requires(user_param={explicit!r}) but {owner_name} has no "
                f"such parameter",
                "error",
            )
    else:
        candidates = _user_parameters(node, users)
        if not candidates:
            add(
                "user-param-missing",
                f"{owner_name} has no parameter annotated with a user model; "
                f"annotate it or pass user_param=",
                "error",
            )
        elif len(candidates) > 1:
            add(
                "user-param-ambiguous",
                f"{owner_name} has several user-model parameters "
                f"({', '.join(candidates)}); pass user_param= to choose one",
                "error",
            )

    for rendered in _unresolvable_guard_args(call):
        add(
            "guard-unresolved",
            f"guard argument {rendered} is not a plain name; the checker "
            f"cannot verify it — extract it into a named function",
            "warning",
        )

    if supplies_meta and include_args:
        collisions = sorted(set(meta_keys) & parameters)
        for name in collisions:
            add(
                "meta-key-collision",
                f"@requires(meta={{...}}, include_args=True) declares key "
                f"{name!r}, which is also a parameter of {owner_name}; the "
                f"literal wins and the argument never reaches the guard",
                "warning",
            )

    owner_is_async = isinstance(node, ast.AsyncFunctionDef)
    meta_consumers = 0
    for guard_name in guard_names(node):
        if guard_name in SDK_GUARDS:
            continue
        definitions = nodes.get(guard_name, [])
        if not definitions:
            add(
                "guard-unresolved",
                f"guard {guard_name!r} is not defined in the scanned paths; "
                f"its contract was not checked",
                "warning",
                guard=guard_name,
            )
            continue
        if len(definitions) > 1:
            files = ", ".join(sorted(str(d.file) for d in definitions))
            add(
                "guard-unresolved",
                f"guard {guard_name!r} maps to {len(definitions)} definitions "
                f"({files}); rename one so the checker can resolve it",
                "warning",
                guard=guard_name,
            )
            continue
        definition = definitions[0]
        if len(_required_positional(definition.node)) == 2:
            meta_consumers += 1
        reachable: set[str] = set()
        for info in graph.by_name.get(guard_name, []):
            reachable |= _reachable_exceptions(info, graph, known_exceptions)
        findings.extend(
            _check_guard_definition(
                definition,
                guard_name=guard_name,
                owner=owner_name,
                owner_is_async=owner_is_async,
                takes_meta=supplies_meta,
                users=users,
                known_exceptions=known_exceptions,
                reachable_raises=reachable,
            )
        )

    if (
        supplies_meta
        and meta_consumers == 0
        and not _unresolved_guard_names(call, nodes)
    ):
        add(
            "meta-unused",
            f"@requires on {owner_name} passes meta= / include_args= but no "
            f"guard declares a second parameter to receive it",
            "error",
        )
    return findings


def analyze_permissions(paths: Iterable[Path]) -> list[GuardFinding]:
    """Analyze source trees and return every ``@requires`` violation.

    Args:
        paths (Iterable[Path]): Directories (walked recursively for
            ``*.py``) or single files to scan.

    Returns:
        list[GuardFinding]: The findings, ordered by file then line, then
        by code so the report is stable across runs.

    Raises:
        FileNotFoundError: If a given path does not exist.

    Notes:
        A file that fails to parse is skipped rather than aborting the run:
        this is advisory tooling, and one generated or vendored file must
        not blind the whole report.
    """
    parsed: list[tuple[Path, ast.Module]] = []
    for file in _python_files(paths):
        try:
            parsed.append((file, ast.parse(file.read_text(encoding="utf-8"))))
        except (SyntaxError, UnicodeDecodeError):
            continue

    known_exceptions = _exception_class_names(tree for _, tree in parsed)
    users = _user_model_names(tree for _, tree in parsed)
    nodes = _index_nodes(parsed)

    graph = build_call_graph(parsed)

    findings: list[GuardFinding] = []
    for definitions in nodes.values():
        for definition in definitions:
            for decorator in definition.node.decorator_list:
                call = _is_requires_decorator(decorator)
                if call is None:
                    continue
                findings.extend(
                    _check_usage(
                        definition,
                        call,
                        users=users,
                        known_exceptions=known_exceptions,
                        nodes=nodes,
                        graph=graph,
                    )
                )

    deduped: dict[tuple[str, int, str, str | None, str], GuardFinding] = {}
    for finding in findings:
        key = (
            str(finding.file),
            finding.lineno,
            finding.code,
            finding.guard,
            finding.function,
        )
        deduped.setdefault(key, finding)
    return sorted(
        deduped.values(),
        key=lambda f: (str(f.file), f.lineno, f.code, f.guard or ""),
    )


__all__: list[str] = [
    "SDK_GUARDS",
    "USER_MODEL_BASES",
    "GuardFinding",
    "Severity",
    "analyze_permissions",
    "guard_names",
]
