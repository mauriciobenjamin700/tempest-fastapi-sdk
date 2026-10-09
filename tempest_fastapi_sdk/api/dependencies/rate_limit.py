"""Per-route rate limit as a FastAPI dependency.

:class:`~tempest_fastapi_sdk.RateLimitMiddleware` limits the whole
application and keys on what a middleware can see: the request line and
its headers. Two needs fall outside it:

* **One route, not the app.** ``POST /api/invites`` deserves a ceiling
  that ``GET /api/invites`` does not, and the middleware only narrows by
  subtraction (``exempt_paths``).
* **A key taken from the body.** Limiting invitations per invited e-mail
  means reading the payload, which the middleware's synchronous
  ``key_func`` never receives.

:func:`make_rate_limit_dependency` covers both. It counts in the same
:class:`~tempest_fastapi_sdk.RateLimitStore` the middleware uses (memory
or Redis) and answers the same ``429`` envelope, built by the same
helpers, so a client cannot tell which of the two refused it.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from typing import Any

from fastapi import Request, Response

from tempest_fastapi_sdk.api.middlewares.rate_limit import (
    RateLimitStore,
    _rate_limit_details,
    _rate_limit_headers,
)
from tempest_fastapi_sdk.exceptions.too_many_requests import (
    TooManyRequestsException,
)
from tempest_fastapi_sdk.utils.client_ip import get_client_ip

RateLimitKeyResult = str | Sequence[str]
"""What a route rate-limit key function returns: one key, or several."""

RateLimitKeyFunc = Callable[
    [Request],
    RateLimitKeyResult | Awaitable[RateLimitKeyResult],
]
"""A key function: sync or async, receiving the request.

The middleware's ``key_by_*`` factories (sync, one key) fit, and so does
:func:`key_by_body_field` (async, zero or one key).
"""


def key_by_body_field(
    field: str,
    *,
    scope: str | None = None,
    normalize: bool = True,
    hash_value: bool = True,
) -> Callable[[Request], Coroutine[Any, Any, list[str]]]:
    """Build an async key function that buckets by a field of the JSON body.

    The body is read with ``await request.body()``. FastAPI reads the
    body before resolving dependencies and Starlette caches it on the
    request, so the endpoint still receives its validated payload — the
    dependency does not consume the stream.

    A body that is not JSON, is not an object, or lacks the field (or
    carries a non-string, non-number value) yields **no key**, not an
    error: the endpoint's own validation answers that request with
    ``422``, and the other keys of the dependency still count it.

    Args:
        field (str): Top-level JSON field whose value is the key (e.g.
            ``"email"``).
        scope (str | None): Prefix of the key. Defaults to ``field``.
        normalize (bool): Strip surrounding whitespace and lowercase the
            value, so ``" Ana@X.com"`` and ``"ana@x.com"`` share a bucket.
        hash_value (bool): Store the SHA-256 hex of the value instead of
            the value itself, so the counter backend never holds the
            e-mail address (or whatever personal data the field carries)
            in clear text.

    Returns:
        Callable[[Request], Coroutine[Any, Any, list[str]]]: The key
        function, yielding ``["<scope>:<value>"]`` or ``[]``.
    """
    label = scope or field

    async def _key(request: Request) -> list[str]:
        """Read ``field`` off the JSON body.

        Args:
            request (Request): The inbound request.

        Returns:
            list[str]: One key, or none when the field is unusable.
        """
        try:
            payload: Any = json.loads(await request.body())
        except ValueError:
            return []
        if not isinstance(payload, dict):
            return []
        raw = payload.get(field)
        if isinstance(raw, bool) or not isinstance(raw, str | int | float):
            return []
        value = str(raw)
        if normalize:
            value = value.strip().lower()
        if not value:
            return []
        if hash_value:
            value = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return [f"{label}:{value}"]

    return _key


async def _resolve_keys(
    funcs: Sequence[RateLimitKeyFunc],
    request: Request,
) -> list[str]:
    """Run every key function and flatten the keys, in order.

    Args:
        funcs (Sequence[RateLimitKeyFunc]): The key functions.
        request (Request): The inbound request.

    Returns:
        list[str]: Every key produced, in declaration order.
    """
    keys: list[str] = []
    for func in funcs:
        produced = func(request)
        if inspect.isawaitable(produced):
            produced = await produced
        if isinstance(produced, str):
            keys.append(produced)
        else:
            keys.extend(produced)
    return keys


def _route_scope(request: Request) -> str:
    """Name the route serving ``request``, for the default bucket prefix.

    Uses the route's path template (``/users/{user_id}``), so every
    resource behind one route shares a budget. Falls back to the literal
    path when no route is resolved yet.

    Args:
        request (Request): The inbound request.

    Returns:
        str: ``"<METHOD> <path template>"``.
    """
    route = request.scope.get("route")
    path = getattr(route, "path_format", None) or request.url.path
    return f"{request.method} {path}"


def make_rate_limit_dependency(
    store: RateLimitStore,
    *,
    max_requests: int,
    window_seconds: float,
    key: RateLimitKeyFunc | Sequence[RateLimitKeyFunc] | None = None,
    trusted_ip_header: str | None = None,
    scope: str | None = None,
    limit_headers: bool = True,
    retry_after_header: bool = True,
    error_message: str = "Too many requests",
    error_code: str = TooManyRequestsException.code,
) -> Callable[[Request, Response], Coroutine[Any, Any, None]]:
    """Build a dependency that rate-limits the route it is attached to.

    Attach with ``dependencies=[Depends(limit)]`` on one route (or a
    router). Each request is counted against every key the ``key``
    functions produce; the first key over ``max_requests`` inside
    ``window_seconds`` refuses the request with ``429``. Keys are checked
    in order and the check stops at the first refusal, so a refused
    request does not spend the budget of the keys after it.

    The refusal raises :class:`~tempest_fastapi_sdk.TooManyRequestsException`
    carrying the same ``details`` (``retry_after_seconds``, ``limit``) and
    headers (``Retry-After``, ``RateLimit-*``) that
    :class:`~tempest_fastapi_sdk.RateLimitMiddleware` writes, built by the
    same helpers; the app's
    :func:`~tempest_fastapi_sdk.register_exception_handlers` turns it into
    the envelope. With a ``MessageCatalog`` registered there, ``detail`` is
    localized by ``code`` — the middleware's is not, since it answers
    outside the exception handlers.

    An accepted request gets ``RateLimit-Limit`` / ``RateLimit-Remaining``
    (the lowest across the keys) on its response, through the
    ``Response`` FastAPI injects into dependencies. A handler that returns
    its own ``Response`` object discards those, as FastAPI does for every
    header set that way.

    Buckets are prefixed with ``scope`` — by default the method and path
    template of the route (``"POST /api/invites"``), so the same
    dependency attached to two routes gives each its own budget, and a
    route-level bucket never collides with the middleware's bucket for
    the same IP in a shared store. Pass one ``scope`` string to several
    routes to make them share a budget.

    Args:
        store (RateLimitStore): Counter backend —
            :class:`~tempest_fastapi_sdk.MemoryRateLimitStore` for one
            process, :class:`~tempest_fastapi_sdk.RedisRateLimitStore` to
            share counters across replicas.
        max_requests (int): Requests allowed per key inside the window.
        window_seconds (float): Sliding-window length in seconds.
        key (RateLimitKeyFunc | Sequence[RateLimitKeyFunc] | None): Key
            function, or several, each returning one key or a sequence of
            keys (sync or async). ``[key_by_ip(trusted_header="x-real-ip"),
            key_by_body_field("email")]`` limits per IP **and** per e-mail.
            ``None`` keys on the client IP resolved with
            ``trusted_ip_header``. A request for which no function produces
            a key is not limited.
        trusted_ip_header (str | None): Single edge-set header holding the
            real client IP (e.g. ``"x-real-ip"``) for the default key.
            ``None`` uses the transport peer, which is the proxy once one
            fronts the app — every client then shares one bucket. Only
            valid without ``key``; pass it to
            :func:`~tempest_fastapi_sdk.key_by_ip` inside ``key`` instead.
        scope (str | None): Prefix of every bucket. ``None`` uses the
            route's ``"<METHOD> <path template>"``.
        limit_headers (bool): Whether to emit ``RateLimit-*``.
        retry_after_header (bool): Whether to emit ``Retry-After`` on 429.
        error_message (str): ``detail`` of the 429 envelope.
        error_code (str): ``code`` of the 429 envelope. Defaults to
            :class:`~tempest_fastapi_sdk.TooManyRequestsException`'s
            ``TOO_MANY_REQUESTS``, matching the middleware.

    Returns:
        Callable[[Request, Response], Coroutine[Any, Any, None]]: The
        async dependency.

    Raises:
        ValueError: If ``max_requests`` < 1, ``window_seconds`` <= 0, or
            both ``key`` and ``trusted_ip_header`` are passed (the header
            would be silently ignored).
    """
    if max_requests < 1:
        raise ValueError("max_requests must be >= 1")
    if window_seconds <= 0:
        raise ValueError("window_seconds must be > 0")
    if key is not None and trusted_ip_header is not None:
        raise ValueError(
            "trusted_ip_header only shapes the default key; with key= pass "
            "it to key_by_ip(trusted_header=...) inside key instead",
        )

    funcs: list[RateLimitKeyFunc]
    if key is None:

        def _ip_key(request: Request) -> str:
            """Key on the resolved client IP.

            Args:
                request (Request): The inbound request.

            Returns:
                str: ``"ip:<addr>"``.
            """
            return f"ip:{get_client_ip(request, trusted_header=trusted_ip_header)}"

        funcs = [_ip_key]
    elif callable(key):
        funcs = [key]
    else:
        funcs = list(key)

    async def _rate_limit(request: Request, response: Response) -> None:
        """Count the request and refuse it once a key is over budget.

        Args:
            request (Request): The inbound request.
            response (Response): FastAPI's header carrier for the
                eventual response.

        Raises:
            TooManyRequestsException: When a key is over budget.
        """
        keys = await _resolve_keys(funcs, request)
        if not keys:
            return
        prefix = scope if scope is not None else _route_scope(request)
        remaining = max_requests
        for bucket in keys:
            result = await store.hit(
                f"{prefix}:{bucket}",
                max_requests,
                window_seconds,
            )
            if not result.allowed:
                refusal = TooManyRequestsException(
                    message=error_message,
                    details=_rate_limit_details(
                        retry_after=result.retry_after,
                        limit=max_requests,
                    ),
                    headers=_rate_limit_headers(
                        limit=max_requests,
                        remaining=0,
                        reset=result.retry_after,
                        retry_after=result.retry_after,
                        limit_headers=limit_headers,
                        retry_after_header=retry_after_header,
                    ),
                )
                refusal.code = error_code
                raise refusal
            remaining = min(remaining, result.remaining)
        response.headers.update(
            _rate_limit_headers(
                limit=max_requests,
                remaining=remaining,
                reset=None,
                retry_after=None,
                limit_headers=limit_headers,
                retry_after_header=retry_after_header,
            ),
        )

    return _rate_limit


__all__: list[str] = [
    "key_by_body_field",
    "make_rate_limit_dependency",
]
