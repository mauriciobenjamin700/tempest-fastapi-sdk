"""Declarative permission decorator over plain guard functions.

:func:`make_permission_checker` gates a *route* through FastAPI's
dependency system. The registry in
:mod:`tempest_fastapi_sdk.authz.permissions` answers "may this user touch
this row?" through registered predicates. Neither covers the smallest,
most common shape: a function that already receives the domain user and
wants to assert an invariant on it before running — at any layer, not
just the router.

That is what :func:`requires` does. It takes one or more **guards** — a
plain callable receiving the user and returning the user (or ``None``) —
and runs them before the decorated function's body:

```python
from tempest_fastapi_sdk import ForbiddenException, requires
from tempest_fastapi_sdk.auth import require_admin


def order_owner(user: UserModel) -> UserModel:
    \"\"\"Assert the user owns the order under edit.

    Args:
        user (UserModel): The authenticated user.

    Returns:
        UserModel: The same user.

    Raises:
        ForbiddenException: When the user does not own the order.
    \"\"\"
    if not user.owns_current_order:
        raise ForbiddenException(message="Not the order owner")
    return user


@router.delete("/orders/{order_id}")
@requires(require_admin, order_owner)
async def delete_order(
    order_id: UUID,
    user: UserModel = Depends(get_current_user),
) -> None: ...
```

The guard contract:

* **one parameter** — the user, typically ``UserT | None``;
* **denies by raising** an :class:`AppException` subclass, so
  ``register_exception_handlers`` maps it to the right status and the
  ``{detail, code, details}`` envelope;
* **returns the user, or ``None``** — a non-``None`` return replaces the
  user seen by the next guard *and* by the decorated function, which is
  how the SDK guards narrow ``UserT | None`` to ``UserT``; ``None`` keeps
  the current user unchanged;
* **may declare a second parameter** ``meta: dict[str, Any]`` to receive
  metadata, which is what makes one generic guard specific per call site:

  ```python
  def has_role(user: UserModel, meta: dict[str, Any]) -> UserModel:
      if meta["role"] not in user.roles:
          raise ForbiddenException(message="Role required")
      return user


  @requires(has_role, meta={"role": "manager"})
  async def close_month(user: UserModel = Depends(get_current_user)) -> None: ...
  ```

  ``meta=`` carries literals fixed at decoration; ``include_args=True``
  merges the arguments of the call (path params, body, other dependencies)
  into the same mapping, so an ownership guard reads
  ``meta["order_id"]`` without the route having to hand it over. One-parameter
  guards are unaffected — the second argument is passed only to guards that
  declare it.

The decorated function keeps its signature, so FastAPI's dependency
injection, ``mypy`` and the OpenAPI schema are unaffected, and the
decorator works the same on a controller or service method (sync or
async) with no framework involved.

Misuse is caught in two places, because each catches what the other
cannot:

* **At decoration time** (import time) — a guard that is not callable,
  takes the wrong number of parameters, is ``async`` under a sync
  function, or a decorated function with no resolvable user parameter
  raises :class:`TempestPermissionError`. The application refuses to
  start rather than skipping a check at runtime.
* **At call time** — a guard that raises something outside the
  :class:`AppException` hierarchy (so the HTTP layer would answer 500),
  or returns a non-user value such as ``False`` (a permission check
  written as a predicate, whose denial would be silently ignored), warns
  with :class:`GuardContractWarning`. The original exception still
  propagates; the warning names the guard.

"Non-user value" is decided by ``isinstance`` against the declarative
base, so a guard that receives a **Pydantic schema** rather than an ORM
row — common when a service layer traffics DTOs — must ``return None``
rather than returning the subject it validated. Returning the schema
looks like the narrowing contract the ORM guards use, but the decorator
cannot recognize it, so it warns and keeps the original user. ``None``
is the documented way to say "I only assert; I do not narrow", and it is
never a denial: guards deny by raising.

Whole classes of mistake are invisible to both — a guard defined but
never wired, a guard whose ``raise`` is dynamic — so the static checker
``tempest permissions`` walks the same contract over the project source.
See :mod:`tempest_fastapi_sdk.cli.permissions`.
"""

from __future__ import annotations

import functools
import inspect
import types
import typing
import warnings
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

from tempest_fastapi_sdk.exceptions.base import AppException

Guard = Callable[..., Any | Awaitable[Any] | None]
"""A permission guard, sync or ``async``.

Two accepted shapes: ``(user) -> user | None`` and
``(user, meta: dict[str, Any]) -> user | None``. The second one receives
the metadata :func:`requires` was configured with (``meta=`` literals plus,
under ``include_args=True``, the arguments of the call), which is how one
generic guard becomes a specific check per call site.

Denial is expressed by raising an :class:`AppException` subclass, never
by returning a falsy value.
"""

F = TypeVar("F", bound=Callable[..., Any])

USER_PARAM_NAMES: frozenset[str] = frozenset(
    {"user", "current_user", "actor", "requester", "principal"}
)
"""Parameter names accepted as the user when the annotation is unusable.

The primary resolution is by type — the parameter whose annotation is a
:class:`~tempest_fastapi_sdk.db.model.BaseModel` subclass (which
:class:`~tempest_fastapi_sdk.db.user_model.BaseUserModel` is). Under
``from __future__ import annotations`` with a ``TYPE_CHECKING``-only
import, the annotation cannot be evaluated at decoration time; these
names are the documented fallback so the decorator still resolves instead
of failing on a correct call site.
"""


class TempestPermissionError(TypeError):
    """Raised when :func:`requires` is used incorrectly.

    A programming error, not an application error: it fires while the
    module is being imported (or on a call that cannot supply the user),
    so it must not be caught and converted into an HTTP response. It is a
    :class:`TypeError` because every case it reports is a signature or
    contract mismatch.
    """


class GuardContractWarning(UserWarning):
    """Warns that a guard broke its contract at runtime.

    Two cases, both of which would otherwise be silent:

    * the guard raised an exception outside the :class:`AppException`
      hierarchy, which the API layer answers as HTTP 500 with no error
      ``code``;
    * the guard returned a value that is not a user model and not
      ``None`` — almost always a predicate-style ``return False``, whose
      denial :func:`requires` cannot honor, since guards deny by raising.

    A warning rather than an exception: the decorator must not change the
    outcome of a call it is only observing, and a warning is visible in
    tests (``-W error``) and in logs without breaking production traffic.
    """


def _guard_name(guard: Guard) -> str:
    """Return a readable name for a guard, for error messages.

    Args:
        guard (Guard): The guard callable.

    Returns:
        str: Its ``__qualname__`` when it has one, else ``repr``.
    """
    return getattr(guard, "__qualname__", None) or repr(guard)


def _safe_hints(fn: Callable[..., Any]) -> dict[str, Any]:
    """Resolve a function's type hints, tolerating unresolvable ones.

    ``get_type_hints`` raises when an annotation references a name that
    only exists under ``TYPE_CHECKING``, which is a legitimate and common
    pattern. The decorator degrades to name-based resolution instead of
    rejecting such a call site.

    Args:
        fn (Callable[..., Any]): The function to inspect.

    Returns:
        dict[str, Any]: The resolved hints, or an empty mapping when
        resolution failed.
    """
    try:
        return typing.get_type_hints(fn, include_extras=True)
    except Exception:
        return {}


def _strip_annotated(annotation: Any) -> Any:
    """Return the underlying type of an ``Annotated[...]`` annotation.

    Args:
        annotation (Any): The annotation to unwrap.

    Returns:
        Any: ``X`` for ``Annotated[X, ...]``, the annotation itself
        otherwise.
    """
    if typing.get_origin(annotation) is typing.Annotated:
        return typing.get_args(annotation)[0]
    return annotation


def _strip_optional(annotation: Any) -> Any:
    """Return the single non-``None`` member of an optional annotation.

    Args:
        annotation (Any): The annotation to unwrap.

    Returns:
        Any: ``X`` for ``X | None`` / ``Optional[X]``, the annotation
        itself for anything else (including a wider union, which is not a
        user annotation the decorator can act on).
    """
    origin = typing.get_origin(annotation)
    if origin is not typing.Union and origin is not types.UnionType:
        return annotation
    members = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
    if len(members) == 1:
        return members[0]
    return annotation


def _user_base() -> type[Any] | None:
    """Return the SQLAlchemy declarative base user models inherit from.

    Imported lazily: :mod:`tempest_fastapi_sdk.authz` must stay importable
    from the package root without pulling the ORM layer in first.

    Returns:
        type[Any] | None: ``BaseModel``, or ``None`` when the DB layer is
        unavailable.
    """
    try:
        from tempest_fastapi_sdk.db.model import BaseModel

        return BaseModel
    except Exception:
        return None


def _is_user_type(annotation: Any) -> bool:
    """Return whether an annotation denotes a user model class.

    Args:
        annotation (Any): A resolved annotation.

    Returns:
        bool: ``True`` when it is a class inheriting the SDK declarative
        base — which every ``BaseUserModel`` subclass does.
    """
    base = _user_base()
    if base is None:
        return False
    target = _strip_optional(_strip_annotated(annotation))
    return isinstance(target, type) and issubclass(target, base)


def _is_user_instance(value: Any) -> bool:
    """Return whether a value is a user model instance.

    Args:
        value (Any): The value a guard returned.

    Returns:
        bool: ``True`` when it is an instance of the SDK declarative
        base. Falls back to ``True`` when the DB layer is unavailable, so
        a project without the ORM never sees a spurious warning.
    """
    base = _user_base()
    if base is None:
        return True
    return isinstance(value, base)


def _looks_like_user_annotation(raw: Any) -> bool:
    """Return whether an unresolved annotation reads as a user model.

    Args:
        raw (Any): The raw (string) annotation from the signature.

    Returns:
        bool: ``True`` when the text mentions ``User``.
    """
    return isinstance(raw, str) and "User" in raw


def _resolve_user_param(fn: Callable[..., Any], explicit: str | None) -> str:
    """Determine which parameter of ``fn`` carries the user.

    Resolution order: an explicit ``user_param``; then the single
    parameter whose resolved annotation is a user model; then, only when
    no annotation resolved to one, the single parameter whose name is in
    :data:`USER_PARAM_NAMES` or whose unresolved annotation mentions
    ``User``.

    Args:
        fn (Callable[..., Any]): The function being decorated.
        explicit (str | None): The ``user_param`` argument, if given.

    Returns:
        str: The parameter name.

    Raises:
        TempestPermissionError: When ``explicit`` names a parameter the
            function does not have, when no parameter can be identified as
            the user, or when several can and none was chosen.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError) as exc:
        raise TempestPermissionError(
            f"@requires cannot inspect the signature of {_guard_name(fn)}"
        ) from exc

    if explicit is not None:
        if explicit not in sig.parameters:
            raise TempestPermissionError(
                f"@requires(user_param={explicit!r}) on {_guard_name(fn)}: "
                f"no such parameter (has: {', '.join(sig.parameters) or 'none'})"
            )
        return explicit

    hints = _safe_hints(fn)
    typed = [name for name in sig.parameters if _is_user_type(hints.get(name))]
    if len(typed) == 1:
        return typed[0]
    if len(typed) > 1:
        raise TempestPermissionError(
            f"@requires on {_guard_name(fn)}: several parameters are user "
            f"models ({', '.join(typed)}); pass user_param= to choose one"
        )

    fallback = [
        name
        for name, param in sig.parameters.items()
        if name in USER_PARAM_NAMES or _looks_like_user_annotation(param.annotation)
    ]
    if len(fallback) == 1:
        return fallback[0]
    if len(fallback) > 1:
        raise TempestPermissionError(
            f"@requires on {_guard_name(fn)}: several parameters could be the "
            f"user ({', '.join(fallback)}); pass user_param= to choose one"
        )
    raise TempestPermissionError(
        f"@requires on {_guard_name(fn)}: no parameter annotated with a user "
        f"model (a BaseModel / BaseUserModel subclass) was found; annotate the "
        f"user parameter or pass user_param="
    )


def _validate_guard(guard: Guard, *, owner: str, allow_async: bool) -> bool:
    """Assert a guard matches the contract, and say whether it wants meta.

    Args:
        guard (Guard): The guard to validate.
        owner (str): Name of the decorated function, for the message.
        allow_async (bool): Whether the decorated function can await —
            ``True`` only when it is itself ``async``.

    Returns:
        bool: ``True`` when the guard declares the optional second
        parameter and must therefore be called as ``guard(user, meta)``.
        A guard whose signature cannot be introspected (a C callable) is
        reported as ``False`` and called with the user alone.

    Raises:
        TempestPermissionError: When the guard is not callable, does not
            take one or two fillable parameters, or is a coroutine
            function under a synchronous decorated function.
    """
    if not callable(guard):
        raise TempestPermissionError(
            f"@requires on {owner}: {guard!r} is not callable; pass a guard "
            f"function taking the user"
        )
    if not allow_async and inspect.iscoroutinefunction(guard):
        raise TempestPermissionError(
            f"@requires on {owner}: guard {_guard_name(guard)!r} is async but "
            f"{owner} is not; make the decorated function async"
        )
    try:
        sig = inspect.signature(guard)
    except (TypeError, ValueError):
        return False

    positional = 0
    accepts_varargs = False
    for param in sig.parameters.values():
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            accepts_varargs = True
        elif param.kind is inspect.Parameter.VAR_KEYWORD:
            continue
        elif param.kind is inspect.Parameter.KEYWORD_ONLY:
            if param.default is inspect.Parameter.empty:
                raise TempestPermissionError(
                    f"@requires on {owner}: guard {_guard_name(guard)!r} has a "
                    f"required keyword-only parameter {param.name!r}; a guard "
                    f"receives the user and the metadata positionally and "
                    f"nothing else"
                )
        elif param.default is inspect.Parameter.empty:
            positional += 1

    if accepts_varargs and positional <= 1:
        return False
    if positional not in (1, 2):
        raise TempestPermissionError(
            f"@requires on {owner}: guard {_guard_name(guard)!r} takes "
            f"{positional} required params, expected 1 (user) or 2 "
            f"(user, meta)"
        )
    return positional == 2


def _is_dependency_marker(value: Any) -> bool:
    """Return whether a default value is a framework injection marker.

    ``user: UserModel = Depends(get_current_user)`` has a default, but it
    is a marker FastAPI replaces — never a usable user. Reading it as one
    would hand the guards a ``Depends`` object.

    Args:
        value (Any): The parameter's default.

    Returns:
        bool: ``True`` for ``fastapi.params`` / ``pydantic.fields``
        markers (``Depends``, ``Security``, ``Query``, ``FieldInfo``, …).
    """
    module = type(value).__module__ or ""
    return module.startswith("fastapi.params") or module.startswith("pydantic.fields")


def _accept_result(result: Any, user: Any, guard: Guard, owner: str) -> Any:
    """Fold a guard's return value into the current user.

    Args:
        result (Any): What the guard returned.
        user (Any): The user passed to the guard.
        guard (Guard): The guard, for the warning message.
        owner (str): The decorated function's name, for the message.

    Returns:
        Any: ``result`` when it is a user model instance (the guard
        narrowed or swapped the user), otherwise the unchanged ``user``.

    Warns:
        GuardContractWarning: When the guard returned something that is
            neither a user model nor ``None`` — typically ``False`` from a
            predicate-style check, whose denial cannot be honored because
            guards deny by raising.
    """
    if result is None:
        return user
    if _is_user_instance(result):
        return result
    warnings.warn(
        f"guard {_guard_name(guard)!r} on {owner} returned "
        f"{type(result).__name__} instead of the user or None; guards deny by "
        f"raising an AppException, so this value is ignored",
        GuardContractWarning,
        stacklevel=4,
    )
    return user


def _call_guard(
    guard: Guard,
    user: Any,
    owner: str,
    meta: dict[str, Any] | None = None,
) -> Any:
    """Invoke a guard, flagging exceptions outside the SDK hierarchy.

    Args:
        guard (Guard): The guard to call.
        user (Any): The current user.
        owner (str): The decorated function's name, for the message.
        meta (dict[str, Any] | None): The metadata mapping to pass as the
            guard's second argument. ``None`` calls the guard with the
            user alone, which is the contract for a one-parameter guard.

    Returns:
        Any: The guard's return value, awaitable for an async guard.

    Raises:
        Exception: Whatever the guard raised, unchanged — a denial must
            reach the exception handlers exactly as written.

    Warns:
        GuardContractWarning: When the raised exception is not an
            :class:`AppException`, so the API layer would answer HTTP 500
            with no error ``code``.
    """
    try:
        return guard(user) if meta is None else guard(user, meta)
    except Exception as exc:
        if not isinstance(exc, AppException):
            warnings.warn(
                f"guard {_guard_name(guard)!r} on {owner} raised "
                f"{type(exc).__name__}, which is not an AppException subclass; "
                f"the API layer answers it as HTTP 500 without an error code",
                GuardContractWarning,
                stacklevel=4,
            )
        raise


def _resolved_signature(fn: Callable[..., Any]) -> inspect.Signature | None:
    """Rebuild a signature with its annotations already evaluated.

    FastAPI evaluates string annotations against the callable's
    ``__globals__``. A wrapper's globals are this module's, so a handler
    written under ``from __future__ import annotations`` would fail to
    resolve its own types. Publishing an already-evaluated
    ``__signature__`` on the wrapper removes the problem: nothing is left
    to evaluate.

    Args:
        fn (Callable[..., Any]): The decorated function.

    Returns:
        inspect.Signature | None: The signature with resolved
        annotations, or ``None`` when they could not be resolved (leaving
        the wrapper's signature to the default lookup through
        ``__wrapped__``).
    """
    hints = _safe_hints(fn)
    if not hints:
        return None
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    parameters = [
        param.replace(annotation=hints[name])
        if name in hints
        else param.replace(annotation=param.annotation)
        for name, param in sig.parameters.items()
    ]
    return sig.replace(
        parameters=parameters,
        return_annotation=hints.get("return", sig.return_annotation),
    )


def _bind_user(
    sig: inspect.Signature,
    param: str,
    owner: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[inspect.BoundArguments, Any]:
    """Bind a call and extract the user argument from it.

    Args:
        sig (inspect.Signature): The decorated function's signature.
        param (str): The user parameter's name.
        owner (str): The decorated function's name, for the message.
        args (tuple[Any, ...]): Positional call arguments.
        kwargs (dict[str, Any]): Keyword call arguments.

    Returns:
        tuple[inspect.BoundArguments, Any]: The bound arguments (to be
        re-emitted after the guards run) and the current user.

    Raises:
        TempestPermissionError: When the call supplied no user and the
            parameter's default cannot stand in for one — a guard must
            never be skipped because its input was missing.
    """
    bound = sig.bind(*args, **kwargs)
    if param in bound.arguments:
        return bound, bound.arguments[param]
    default = sig.parameters[param].default
    if default is inspect.Parameter.empty or _is_dependency_marker(default):
        raise TempestPermissionError(
            f"{owner} was called without its {param!r} argument; @requires "
            f"cannot check a user it never receives"
        )
    return bound, default


def _build_meta(
    bound: inspect.BoundArguments,
    user_param: str,
    static_meta: dict[str, Any],
    include_args: bool,
) -> dict[str, Any]:
    """Build the metadata mapping handed to the guards of one call.

    A fresh dict per call, shared by every guard in that call — so a guard
    may add a key the next guard reads, while nothing leaks into the next
    request.

    Args:
        bound (inspect.BoundArguments): The decorated function's bound
            arguments.
        user_param (str): Name of the user parameter, excluded from the
            merged arguments because the guard already receives the user.
        static_meta (dict[str, Any]): Literals declared at decoration.
        include_args (bool): Whether to merge the call's arguments.

    Returns:
        dict[str, Any]: The metadata mapping. Parameters the caller left
        out contribute their default, so the guard sees the values the body
        will actually run with; a default that is a framework injection
        marker (``Depends(...)``) is skipped rather than handed over as a
        value. Keys from ``static_meta`` win over argument names: the
        decoration is the explicit declaration, the arguments are ambient
        data.
    """
    if not include_args:
        return dict(static_meta)
    payload = {
        name: value for name, value in bound.arguments.items() if name != user_param
    }
    for name, param in bound.signature.parameters.items():
        if name in payload or name == user_param:
            continue
        if param.default is inspect.Parameter.empty:
            continue
        if _is_dependency_marker(param.default):
            continue
        payload[name] = param.default
    payload.update(static_meta)
    return payload


def requires(
    *guards: Guard,
    user_param: str | None = None,
    meta: Mapping[str, Any] | None = None,
    include_args: bool = False,
) -> Callable[[F], F]:
    """Run permission guards on the user before the function body.

    Each guard receives the current user and either returns it (or
    ``None`` to leave it unchanged) or raises an
    :class:`~tempest_fastapi_sdk.exceptions.base.AppException` subclass to
    deny. Guards run left to right, and a guard's non-``None`` return
    becomes the user the next guard — and the decorated function — sees,
    which is how the SDK's ``require_*`` guards narrow ``UserT | None`` to
    ``UserT``.

    The user parameter is found by annotation: the single parameter typed
    as a ``BaseModel`` / ``BaseUserModel`` subclass. Pass ``user_param``
    to name it explicitly, which is also the way to disambiguate a
    function taking two user models.

    Works on route handlers, controller and service methods, sync or
    ``async``. The wrapper keeps the decorated signature, so FastAPI's
    dependency injection and the OpenAPI schema are untouched — place it
    **below** the route decorator so the router registers the guarded
    function.

    A guard may declare an optional **second parameter** to receive a
    ``dict[str, Any]`` of metadata, which is what turns one generic guard
    into a specific check per call site: ``meta`` carries the literals
    declared at decoration (``meta={"role": "manager"}``), and
    ``include_args=True`` merges the arguments of the call itself (path
    params, body, injected dependencies other than the user) into the same
    mapping, so an ownership guard can read ``meta["order_id"]``. Guards
    that declare one parameter are called exactly as before.

    Example:
        ```python
        @router.delete(
            "/orders/{order_id}",
            responses=error_responses(ForbiddenException),
        )
        @requires(require_active, order_owner, include_args=True)
        async def delete_order(
            order_id: UUID,
            user: UserModel = Depends(get_current_user),
        ) -> None: ...
        ```

        ```python
        def has_role(user: UserModel, meta: dict[str, Any]) -> UserModel:
            # Raises ForbiddenException when the declared role is missing.
            if meta["role"] not in user.roles:
                raise ForbiddenException(message="Role required")
            return user


        @requires(has_role, meta={"role": "manager"})
        async def close_month(
            user: UserModel = Depends(get_current_user),
        ) -> None: ...
        ```

    Args:
        *guards (Guard): One or more guards, applied in order.
        user_param (str | None): Name of the parameter carrying the user.
            ``None`` resolves it from the annotations.
        meta (Mapping[str, Any] | None): Literal metadata handed to every
            guard that declares a second parameter. Copied at decoration
            time, so a later mutation of the passed mapping cannot change
            what the route enforces.
        include_args (bool): When ``True``, the arguments the decorated
            function was called with (minus the user) are merged into the
            metadata mapping. Keys from ``meta`` win over argument names,
            since the decoration is the explicit declaration.

    Returns:
        Callable[[F], F]: The decorator, returning a wrapper with the same
        signature as the decorated function.

    Raises:
        TempestPermissionError: At decoration time when no guard was
            given, a guard is not callable or has the wrong signature, an
            ``async`` guard is used on a synchronous function, the user
            parameter cannot be resolved, ``meta`` is not a mapping, or
            ``meta`` / ``include_args`` was given while no guard declares a
            second parameter to receive it.

    Warns:
        GuardContractWarning: At call time when a guard raises a
            non-:class:`AppException` error or returns a non-user value.

    Notes:
        Runtime validation cannot see a guard that is never wired or a
        dynamic ``raise``. Run ``tempest permissions --check`` in CI for
        the static half of the same contract.
    """
    if not guards:
        raise TempestPermissionError(
            "@requires() needs at least one guard; an empty decorator would "
            "silently allow every request"
        )
    if meta is not None and not isinstance(meta, Mapping):
        raise TempestPermissionError(
            f"@requires(meta=...) must be a mapping of str to Any, got "
            f"{type(meta).__name__}"
        )
    static_meta: dict[str, Any] = dict(meta or {})
    declares_meta = meta is not None or include_args

    def decorator(fn: F) -> F:
        """Wrap ``fn`` so the guards run before its body.

        Args:
            fn (F): The function to guard.

        Returns:
            F: The guarded wrapper.

        Raises:
            TempestPermissionError: On any contract violation described by
                :func:`requires`.
        """
        owner = _guard_name(fn)
        is_async = inspect.iscoroutinefunction(fn)
        wants_meta = tuple(
            _validate_guard(guard, owner=owner, allow_async=is_async)
            for guard in guards
        )
        if declares_meta and not any(wants_meta):
            raise TempestPermissionError(
                f"@requires on {owner}: meta= / include_args= was given but no "
                f"guard declares a second parameter to receive it; add "
                f"(user, meta) to a guard or drop the argument"
            )
        param = _resolve_user_param(fn, user_param)
        sig = inspect.signature(fn)

        wrapper: Any
        if is_async:

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                """Run the guards, then await the decorated coroutine."""
                bound, user = _bind_user(sig, param, owner, args, kwargs)
                payload = _build_meta(bound, param, static_meta, include_args)
                for guard, takes_meta in zip(guards, wants_meta, strict=True):
                    result = _call_guard(
                        guard, user, owner, payload if takes_meta else None
                    )
                    if inspect.isawaitable(result):
                        result = await result
                    user = _accept_result(result, user, guard, owner)
                bound.arguments[param] = user
                return await fn(*bound.args, **bound.kwargs)

            wrapper = async_wrapper
        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                """Run the guards, then call the decorated function.

                Raises:
                    TempestPermissionError: When a guard returned an
                        awaitable, which a synchronous call site cannot
                        resolve.
                """
                bound, user = _bind_user(sig, param, owner, args, kwargs)
                payload = _build_meta(bound, param, static_meta, include_args)
                for guard, takes_meta in zip(guards, wants_meta, strict=True):
                    result = _call_guard(
                        guard, user, owner, payload if takes_meta else None
                    )
                    if inspect.isawaitable(result):
                        raise TempestPermissionError(
                            f"@requires on {owner}: guard "
                            f"{_guard_name(guard)!r} returned an awaitable but "
                            f"{owner} is not async"
                        )
                    user = _accept_result(result, user, guard, owner)
                bound.arguments[param] = user
                return fn(*bound.args, **bound.kwargs)

            wrapper = sync_wrapper

        resolved = _resolved_signature(fn)
        if resolved is not None:
            wrapper.__signature__ = resolved
        wrapper.__tempest_guards__ = tuple(guards)
        wrapper.__tempest_user_param__ = param
        wrapper.__tempest_guard_meta__ = dict(static_meta)
        return typing.cast("F", wrapper)

    return decorator


def declared_guards(fn: Callable[..., Any]) -> tuple[Guard, ...]:
    """Return the guards :func:`requires` attached to a function.

    The counterpart of
    :func:`tempest_fastapi_sdk.api.error_docs.declared_raises`: it lets a
    test, a router audit or an admin page read a route's authorization
    without calling it.

    Args:
        fn (Callable[..., Any]): The (possibly decorated) function.

    Returns:
        tuple[Guard, ...]: The guards in application order, empty when the
        function is not decorated with :func:`requires`.
    """
    return tuple(getattr(fn, "__tempest_guards__", ()))


def guard_metadata(fn: Callable[..., Any]) -> dict[str, Any]:
    """Return the literal metadata :func:`requires` attached to a function.

    Only the ``meta=`` literals, which are fixed at decoration time — the
    arguments merged by ``include_args=True`` exist per call and cannot be
    read from the function object.

    Args:
        fn (Callable[..., Any]): The (possibly decorated) function.

    Returns:
        dict[str, Any]: A copy of the declared metadata, empty when the
        function carries none.
    """
    value = getattr(fn, "__tempest_guard_meta__", None)
    return dict(value) if isinstance(value, dict) else {}


def guarded_user_param(fn: Callable[..., Any]) -> str | None:
    """Return the parameter name :func:`requires` reads the user from.

    Args:
        fn (Callable[..., Any]): The (possibly decorated) function.

    Returns:
        str | None: The parameter name, or ``None`` when the function is
        not decorated with :func:`requires`.
    """
    value = getattr(fn, "__tempest_user_param__", None)
    return value if isinstance(value, str) else None


__all__: list[str] = [
    "USER_PARAM_NAMES",
    "Guard",
    "GuardContractWarning",
    "TempestPermissionError",
    "declared_guards",
    "guard_metadata",
    "guarded_user_param",
    "requires",
]
