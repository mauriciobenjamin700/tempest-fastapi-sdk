"""Lifecycle signals fired by :class:`BaseRepository` write methods.

The repository emits four signals around the unit-of-work write path
so a service can react to persistence without threading a callback
through every call site (audit hooks, cache busting, outbox enqueue,
search-index sync, domain events):

* ``PRE_SAVE`` — before the ``INSERT`` / ``UPDATE`` commits. Raising
  here vetoes the write (the repository rolls back and re-raises).
* ``POST_SAVE`` — after the row is committed and refreshed.
* ``PRE_DELETE`` — before a single-row delete commits; the handler
  receives the row as it was just before deletion.
* ``POST_DELETE`` — after the delete commits.

Handlers are registered per model class in a process-global registry
and fire for **every** repository operating on that model (or a
subclass). Registration on a base model applies to all its subclasses,
resolved through the instance's MRO.

Handlers may be sync or ``async`` — a coroutine result is awaited. A
handler receives the ORM instance as its only positional argument.

!!! warning "Signals cover the unit-of-work path only"
    ``add`` / ``add_all`` / ``update`` / ``update_many`` /
    ``soft_delete`` / ``restore`` / ``delete`` fire signals.
    The set-based bulk methods (``bulk_update``, ``bulk_create_values``,
    ``bulk_upsert``, ``delete_many``, ``delete_batch``) issue a single
    SQL statement and **bypass** the ORM and therefore signals — by
    design, since they never materialize the affected rows.
"""

from __future__ import annotations

import inspect as _inspect
from collections.abc import Awaitable, Callable
from typing import Any

from tempest_fastapi_sdk.core.enums import BaseStrEnum

#: A signal handler: called with the ORM instance, optionally ``async``.
SignalHandler = Callable[[Any], Awaitable[None] | None]


class RepositorySignal(BaseStrEnum):
    """The lifecycle moments a :class:`BaseRepository` emits."""

    PRE_SAVE = "pre_save"
    POST_SAVE = "post_save"
    PRE_DELETE = "pre_delete"
    POST_DELETE = "post_delete"


#: Registry keyed by ``(model class, signal)`` → ordered handler list.
_HANDLERS: dict[tuple[type[Any], RepositorySignal], list[SignalHandler]] = {}


def connect(
    model: type[Any],
    signal: RepositorySignal,
    handler: SignalHandler,
) -> None:
    """Register ``handler`` to fire on ``signal`` for ``model``.

    Registering the same handler twice for the same
    ``(model, signal)`` is a no-op, so idempotent module-import wiring
    is safe.

    Args:
        model (type[Any]): The SQLAlchemy model class to watch. A base
            class also matches its subclasses.
        signal (RepositorySignal): The lifecycle moment to hook.
        handler (SignalHandler): The sync or async callable invoked
            with the ORM instance.
    """
    handlers = _HANDLERS.setdefault((model, signal), [])
    if handler not in handlers:
        handlers.append(handler)


def disconnect(
    model: type[Any],
    signal: RepositorySignal,
    handler: SignalHandler,
) -> None:
    """Unregister ``handler`` from ``signal`` for ``model``.

    Silently ignores a handler that was never connected.

    Args:
        model (type[Any]): The model class the handler was registered
            against.
        signal (RepositorySignal): The lifecycle moment.
        handler (SignalHandler): The callable to remove.
    """
    handlers = _HANDLERS.get((model, signal))
    if handlers and handler in handlers:
        handlers.remove(handler)


def on_signal(
    model: type[Any],
    signal: RepositorySignal,
) -> Callable[[SignalHandler], SignalHandler]:
    """Decorator form of :func:`connect`.

    Args:
        model (type[Any]): The model class to watch.
        signal (RepositorySignal): The lifecycle moment to hook.

    Returns:
        Callable[[SignalHandler], SignalHandler]: A decorator that
        registers the wrapped function and returns it unchanged.
    """

    def decorator(handler: SignalHandler) -> SignalHandler:
        connect(model, signal, handler)
        return handler

    return decorator


def handlers_for(
    model_class: type[Any],
    signal: RepositorySignal,
) -> list[SignalHandler]:
    """Return the handlers for ``model_class`` and its ancestors.

    Walks the MRO so a handler registered on a base model also fires
    for a subclass. Order is base-to-subclass, preserving registration
    order within each class and de-duplicating shared handlers.

    Args:
        model_class (type[Any]): The concrete class of the instance.
        signal (RepositorySignal): The lifecycle moment.

    Returns:
        list[SignalHandler]: The applicable handlers, possibly empty.
    """
    collected: list[SignalHandler] = []
    for klass in reversed(model_class.__mro__):
        for handler in _HANDLERS.get((klass, signal), []):
            if handler not in collected:
                collected.append(handler)
    return collected


def has_handlers(model_class: type[Any], signal: RepositorySignal) -> bool:
    """Return whether any handler is registered for ``model_class``.

    Lets the repository skip loading a row for delete signals when no
    delete handler is registered — zero overhead on the common path.

    Args:
        model_class (type[Any]): The concrete class of the instance.
        signal (RepositorySignal): The lifecycle moment.

    Returns:
        bool: ``True`` when at least one handler applies.
    """
    return bool(handlers_for(model_class, signal))


async def emit(
    model_class: type[Any],
    signal: RepositorySignal,
    instance: Any,
) -> None:
    """Invoke every handler registered for ``signal`` on ``model_class``.

    Sync handlers run inline; a coroutine return value is awaited.
    Handlers run in registration order; an exception propagates to the
    caller (so a ``PRE_SAVE`` handler can veto a write).

    Args:
        model_class (type[Any]): The concrete class of ``instance``.
        signal (RepositorySignal): The lifecycle moment.
        instance (Any): The ORM row passed to each handler.
    """
    for handler in handlers_for(model_class, signal):
        result = handler(instance)
        if _inspect.isawaitable(result):
            await result


def clear_signals() -> None:
    """Remove every registered handler.

    Intended for test isolation — call it in a fixture teardown so
    signals registered by one test never leak into another.
    """
    _HANDLERS.clear()


__all__: list[str] = [
    "RepositorySignal",
    "SignalHandler",
    "clear_signals",
    "connect",
    "disconnect",
    "emit",
    "handlers_for",
    "has_handlers",
    "on_signal",
]
