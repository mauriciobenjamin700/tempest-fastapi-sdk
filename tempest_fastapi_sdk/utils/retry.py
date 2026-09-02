"""Retry policy and the decorator that applies it to a coroutine.

:class:`RetryPolicy` is the backoff curve — how many tries, how long to
wait between them. It lives here rather than beside the HTTP client
because the curve is not an HTTP idea: a RabbitMQ connect on boot, a
push dispatch and a third-party call all want the same bounded
exponential backoff, and only the HTTP client cares about
``retry_statuses``.

:func:`async_retry` is what applies that curve to an ``async def``.
Before it, a policy could describe the waiting and nothing in the SDK
could do the waiting — so every service wrote the loop again, and each
one made its own call on the two decisions that are easy to get wrong:
which exceptions count as transient (retrying a ``TypeError`` turns a
bug into three bugs and a delay) and what surfaces when the budget runs
out (swallowing the last exception hides a permanent failure behind a
generic one).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import wraps
from typing import ParamSpec, Protocol, TypeVar, runtime_checkable

P = ParamSpec("P")
T = TypeVar("T")

_LOGGER: logging.Logger = logging.getLogger(__name__)


@runtime_checkable
class RetryLogger(Protocol):
    """The two calls :func:`async_retry` makes on whatever it logs to.

    ``async_retry`` writes exactly one WARNING per retry and one ERROR
    when the budget runs out. Typing the parameter as
    ``logging.Logger`` named the *implementation* the SDK happens to
    use rather than that requirement — and excluded
    :class:`~tempest_fastapi_sdk.LogUtils`, which is the logger this
    same SDK hands the service. A module that opens with
    ``logger: LogUtils = LogUtils(__name__)`` could not pass that
    ``logger`` to a decorator shipped alongside it: the call worked at
    runtime, because the shapes match, and failed under mypy with
    ``Argument "logger" to "async_retry" has incompatible type
    "LogUtils"``. The workaround, ``logger=logger.logger``, reaches
    past the facade to the object it wraps.

    Both parameters are positional-only. A protocol member written
    ``def warning(self, msg: str)`` also demands the *name* ``msg``
    from the implementer, and the two loggers disagree —
    ``logging.Logger`` calls it ``msg``, ``LogUtils`` calls it
    ``message``. mypy accepts either spelling; basedpyright rejects the
    mismatch outright, which is the checker the consumer runs.

    ``runtime_checkable`` makes ``isinstance`` work, but it only checks
    that the two members *exist* — the signatures are the type
    checker's job, and the recipe carries a typed example so both
    checkers see it.
    """

    def warning(self, msg: str, /, *args: object) -> None:
        """Emit a WARNING record.

        Args:
            msg (str): The ``%``-style template.
            *args (object): Interpolation arguments, kept lazy.
        """
        ...

    def error(self, msg: str, /, *args: object) -> None:
        """Emit an ERROR record.

        Args:
            msg (str): The ``%``-style template.
            *args (object): Interpolation arguments, kept lazy.
        """
        ...


@dataclass(slots=True)
class RetryPolicy:
    """Bounded exponential backoff for retried operations.

    The first retry sleeps for ``backoff_initial_seconds``; each
    subsequent retry doubles the wait, capped at
    ``backoff_max_seconds``. Total retries are bounded by
    ``max_attempts`` (the first try counts).

    Attributes:
        max_attempts (int): Total tries including the first.
            ``1`` disables retries.
        backoff_initial_seconds (float): Sleep before the second
            attempt.
        backoff_max_seconds (float): Hard cap per sleep.
        retry_statuses (frozenset[int]): HTTP status codes worth
            retrying. Defaults to common 5xx; ``429`` is included
            because it usually means "back off and try again". Read by
            :class:`~tempest_fastapi_sdk.HTTPClient` only —
            :func:`async_retry` branches on exceptions instead.
    """

    max_attempts: int = 3
    backoff_initial_seconds: float = 0.5
    backoff_max_seconds: float = 8.0
    retry_statuses: frozenset[int] = field(
        default_factory=lambda: frozenset({429, 500, 502, 503, 504}),
    )

    def sleep_for(self, attempt: int) -> float:
        """Compute the sleep between attempt ``n`` and attempt ``n+1``.

        Args:
            attempt (int): Zero-based retry attempt number.

        Returns:
            float: Seconds to wait before the next attempt.
        """
        wait: float = self.backoff_initial_seconds * (2 ** max(0, attempt - 1))
        return min(wait, self.backoff_max_seconds)


def async_retry(
    policy: RetryPolicy | None = None,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    *,
    logger: RetryLogger | None = None,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Retry an async callable with the bounded backoff of ``policy``.

    Example:

        >>> from tempest_fastapi_sdk import RetryPolicy, async_retry
        >>>
        >>> @async_retry(RetryPolicy(max_attempts=5), (ConnectionError,))
        ... async def connect(url: str) -> None:
        ...     ...

    When every attempt fails, the **last** exception propagates rather
    than a wrapper: a caller that could react to a permanent
    ``ConnectionError`` should still see a ``ConnectionError``.

    ``exceptions`` defaults to ``Exception``, which deliberately
    excludes :class:`asyncio.CancelledError` — it derives from
    ``BaseException``, and retrying a cancelled task fights the
    cancellation instead of honoring it. Narrow the tuple to the
    transient failures you actually mean; a ``TypeError`` retried three
    times is the same bug, later.

    Args:
        policy (RetryPolicy | None): Attempt budget and backoff curve.
            ``None`` (default) builds a fresh :class:`RetryPolicy` —
            a shared module-level instance would be mutable state every
            decorated function points at.
        exceptions (tuple[type[BaseException], ...]): Exception types
            that count as transient. Anything outside the tuple
            propagates immediately, without consuming an attempt.
        logger (RetryLogger | None): Where the retry and give-up
            records go. Anything with ``warning`` and ``error`` fits —
            a ``logging.Logger``, or the
            :class:`~tempest_fastapi_sdk.LogUtils` this SDK hands the
            service. ``None`` uses this module's logger.

    Returns:
        Callable: A decorator wrapping the target coroutine function.

    Raises:
        ValueError: When ``policy.max_attempts`` is below ``1``, which
            would run the callable zero times and return ``None`` from
            something annotated to return ``T``.
    """
    resolved: RetryPolicy = policy if policy is not None else RetryPolicy()
    log: RetryLogger = logger if logger is not None else _LOGGER

    if resolved.max_attempts < 1:
        raise ValueError("policy.max_attempts must be >= 1")

    def decorator(fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        """Wrap ``fn`` in the retry loop.

        Args:
            fn (Callable[P, Awaitable[T]]): The coroutine function.

        Returns:
            Callable[P, Awaitable[T]]: The wrapped function.
        """

        @wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            """Call ``fn``, retrying transient failures.

            Args:
                *args (P.args): Forwarded positionally.
                **kwargs (P.kwargs): Forwarded by keyword.

            Returns:
                T: Whatever ``fn`` returned on the first success.

            Raises:
                BaseException: The last failure, when the budget runs
                    out, or the first one outside ``exceptions``.
            """
            for attempt in range(1, resolved.max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except exceptions as error:
                    if attempt == resolved.max_attempts:
                        log.error(
                            "async_retry: %s gave up after %d attempts: %s",
                            fn.__qualname__,
                            resolved.max_attempts,
                            error,
                        )
                        raise
                    delay: float = resolved.sleep_for(attempt)
                    log.warning(
                        "async_retry: %s failed on attempt %d/%d (%s);"
                        " retrying in %.2fs",
                        fn.__qualname__,
                        attempt,
                        resolved.max_attempts,
                        error,
                        delay,
                    )
                    await asyncio.sleep(delay)
            raise AssertionError("unreachable: the loop returns or raises")

        return wrapper

    return decorator


__all__: list[str] = [
    "RetryLogger",
    "RetryPolicy",
    "async_retry",
]
