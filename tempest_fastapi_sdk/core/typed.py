"""Runtime type-enforcement decorators.

Type hints are checked by static analysers (mypy / pyright) at
development time, but they are *erased* at runtime -- nothing stops a
caller from passing a ``str`` where an ``int`` is annotated once the
code ships. These decorators close that gap by validating argument and
return values against the annotations on every call.

They are built on Pydantic v2's :func:`pydantic.validate_call` (already
a hard dependency of the SDK), so there is no extra package to install
and the validation rules match the ones FastAPI uses for request
bodies.

Three tools, two concerns:

* :func:`strict_types` / :func:`typed` enforce that runtime *values*
  match the annotations (the part hints cannot guarantee). ``strict``
  rejects mismatches outright; ``typed`` coerces when Pydantic safely
  can (``"1"`` -> ``1``).
* :func:`require_annotations` enforces that a function *is* fully
  annotated, raising at decoration time (import time) rather than
  waiting for a linter run.

These add per-call overhead, so reach for them at trust boundaries
(queue messages, third-party API payloads, CLI input, dynamically
constructed data) rather than on hot internal paths -- inside a FastAPI
service the request body is already validated by its Pydantic schema at
the router.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import TypeVar

from pydantic import ConfigDict
from pydantic import validate_call as _validate_call

F = TypeVar("F", bound=Callable[..., object])


def strict_types(func: F) -> F:
    """Validate argument and return values with **no type coercion**.

    Wraps ``func`` so that, on every call, each argument and the return
    value are validated against the function's annotations in Pydantic's
    *strict* mode: a value must already be of the annotated type. A
    ``str`` passed where ``int`` is annotated raises -- it is **not**
    coerced to ``1``. Use this when you want the strongest runtime
    guarantee that values match their declared types.

    Args:
        func (F): The function or method to wrap. Every parameter and
            the return must be annotated (unannotated parameters are
            treated as ``Any`` by Pydantic and therefore not enforced).

    Returns:
        F: The wrapped callable with the same signature. Raises
        :class:`pydantic.ValidationError` when a value does not match
        its annotation.

    Example:
        >>> @strict_types
        ... def add(a: int, b: int) -> int:
        ...     return a + b
        >>> add(1, 2)
        3
        >>> add("1", 2)  # doctest: +SKIP
        Traceback (most recent call last):
        pydantic_core._pydantic_core.ValidationError: ...
    """
    wrapped = _validate_call(
        config=ConfigDict(strict=True),
        validate_return=True,
    )(func)
    return wrapped


def typed(func: F) -> F:
    """Validate argument and return values, **coercing** when safe.

    Like :func:`strict_types`, but in Pydantic's default (lax) mode:
    values that are not of the annotated type are coerced when Pydantic
    can do so unambiguously (``"1"`` -> ``1``, ``"true"`` -> ``True``).
    A value that cannot be coerced still raises. Use this when you want
    runtime safety but also want the convenience of automatic coercion
    of stringly-typed input.

    Args:
        func (F): The function or method to wrap. Every parameter and
            the return should be annotated; unannotated parameters are
            treated as ``Any`` and not enforced.

    Returns:
        F: The wrapped callable with the same signature. Raises
        :class:`pydantic.ValidationError` when a value cannot be
        validated or coerced to its annotation.

    Example:
        >>> @typed
        ... def add(a: int, b: int) -> int:
        ...     return a + b
        >>> add("1", 2)  # coerced
        3
    """
    wrapped = _validate_call(validate_return=True)(func)
    return wrapped


def require_annotations(func: F) -> F:
    """Require every parameter and the return to be annotated.

    Unlike :func:`strict_types` / :func:`typed`, this does **not**
    validate runtime values -- it enforces that the function *carries*
    annotations at all, failing fast at decoration time (i.e. when the
    module is imported) instead of relying on a separate linter pass.
    ``self`` and ``cls`` are exempt, as are ``*args`` / ``**kwargs``.

    Args:
        func (F): The function or method to inspect.

    Returns:
        F: The same function, unchanged, when fully annotated.

    Raises:
        TypeError: If any parameter (other than ``self`` / ``cls`` /
            ``*args`` / ``**kwargs``) or the return value lacks an
            annotation. The message lists every offending name.

    Example:
        >>> @require_annotations
        ... def ok(a: int) -> int:
        ...     return a
        >>> @require_annotations  # doctest: +SKIP
        ... def bad(a) -> int:
        ...     return a
        Traceback (most recent call last):
        TypeError: bad: missing type annotation for parameter 'a'
    """
    signature = inspect.signature(func)
    missing: list[str] = []
    skip_kinds = {
        inspect.Parameter.VAR_POSITIONAL,
        inspect.Parameter.VAR_KEYWORD,
    }
    for name, param in signature.parameters.items():
        if name in {"self", "cls"} or param.kind in skip_kinds:
            continue
        if param.annotation is inspect.Parameter.empty:
            missing.append(f"missing type annotation for parameter {name!r}")
    if signature.return_annotation is inspect.Signature.empty:
        missing.append("missing return type annotation")
    if missing:
        raise TypeError(f"{func.__qualname__}: " + "; ".join(missing))
    return func


__all__: list[str] = [
    "require_annotations",
    "strict_types",
    "typed",
]
