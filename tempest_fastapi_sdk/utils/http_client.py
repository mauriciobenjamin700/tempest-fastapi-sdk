"""Typed async HTTP client built on ``httpx``.

Wraps :class:`httpx.AsyncClient` with the bits every Tempest
service ends up reimplementing: retry with exponential backoff,
``X-Request-ID`` propagation, default timeouts, basic
circuit-breaker. Requires the ``[http]`` extra.

Design notes:

* The client owns its own ``httpx.AsyncClient`` — reuse the same
  instance across requests so the connection pool stays warm.
* Retries cover **network errors** (``ConnectError``,
  ``ReadTimeout``) and **5xx** responses. **4xx is never retried**
  — by definition the client is at fault.
* ``X-Request-ID`` is read from the current
  :func:`tempest_fastapi_sdk.get_request_id` contextvar so the
  outbound call propagates the inbound correlation id.
* The circuit-breaker is **per-host** and trips after
  ``failure_threshold`` consecutive 5xx/network failures; the next
  request returns immediately with :class:`CircuitOpenError` until
  ``recovery_seconds`` elapses (half-open). One successful request
  closes the circuit.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

try:
    import httpx as _httpx_mod
except ImportError:  # pragma: no cover - guarded by [http] extra
    _httpx_mod = None  # type: ignore[assignment]

from tempest_fastapi_sdk.core.context import get_request_id

REQUEST_ID_HEADER: str = "X-Request-ID"
"""Outbound header carrying the inbound correlation id."""


class CircuitOpenError(Exception):
    """Raised when the circuit-breaker rejects a call.

    Carries the host that tripped the breaker so callers can
    branch on it (e.g. fall back to a cache or a queue).
    """

    def __init__(self, host: str) -> None:
        """Initialize.

        Args:
            host (str): The host whose breaker is open.
        """
        super().__init__(f"circuit open for host {host!r}")
        self.host: str = host


@dataclass(slots=True)
class _BreakerState:
    """Mutable per-host circuit state. Internal.

    Attributes:
        consecutive_failures (int): Counter reset on every success.
        opened_at (float): ``time.monotonic()`` when the circuit
            tripped. ``0.0`` means closed.
    """

    consecutive_failures: int = 0
    opened_at: float = 0.0


@dataclass(slots=True)
class RetryPolicy:
    """Bounded exponential backoff for retried requests.

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
            because it usually means "back off and try again".
    """

    max_attempts: int = 3
    backoff_initial_seconds: float = 0.5
    backoff_max_seconds: float = 8.0
    retry_statuses: frozenset[int] = field(
        default_factory=lambda: frozenset({429, 500, 502, 503, 504}),
    )

    def sleep_for(self, attempt: int) -> float:
        """Compute the sleep between attempt ``n`` and attempt ``n+1``."""
        wait: float = self.backoff_initial_seconds * (2 ** max(0, attempt - 1))
        return min(wait, self.backoff_max_seconds)


class HTTPClient:
    """Async HTTP client with retries, circuit-breaker and request-id propagation.

    Example:

        >>> client = HTTPClient(base_url="https://api.example.com")
        >>> async with client:
        ...     response = await client.get("/users/me")
        ...     payload: dict[str, Any] = response.json()

    The client is **safe to share** across requests on the same
    event loop — internally each call uses the shared
    :class:`httpx.AsyncClient` connection pool.
    """

    def __init__(
        self,
        *,
        base_url: str = "",
        timeout: float = 10.0,
        retry_policy: RetryPolicy | None = None,
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
        default_headers: Mapping[str, str] | None = None,
        verify_tls: bool = True,
        propagate_request_id: bool = True,
    ) -> None:
        """Initialize.

        Args:
            base_url (str): Prepended to relative paths. Use empty
                string to require absolute URLs at the call site.
            timeout (float): Per-request timeout in seconds.
                Overridable per call.
            retry_policy (RetryPolicy | None): Retry configuration.
                ``None`` uses the defaults (3 attempts, ~0.5/1/2s
                backoff).
            failure_threshold (int): Consecutive 5xx/network errors
                that trip the circuit per host. ``0`` disables the
                breaker.
            recovery_seconds (float): Seconds the breaker stays
                open before allowing one half-open probe.
            default_headers (Mapping[str, str] | None): Headers
                attached to every request (e.g. ``Authorization``).
            verify_tls (bool): Whether to verify TLS certificates.
                Default ``True`` — flip only for internal mTLS or
                dev with self-signed certs.
            propagate_request_id (bool): When ``True`` (default),
                attach ``X-Request-ID`` from the current
                contextvar to outbound requests.

        Raises:
            ImportError: When the ``[http]`` extra is missing.
        """
        if _httpx_mod is None:
            raise ImportError(
                "HTTPClient requires the [http] extra. "
                "Install with `pip install tempest-fastapi-sdk[http]`."
            )
        self.base_url: str = base_url
        self.timeout: float = timeout
        self.retry_policy: RetryPolicy = retry_policy or RetryPolicy()
        self.failure_threshold: int = failure_threshold
        self.recovery_seconds: float = recovery_seconds
        self.propagate_request_id: bool = propagate_request_id
        self._client: httpx.AsyncClient = _httpx_mod.AsyncClient(
            base_url=base_url,
            timeout=_httpx_mod.Timeout(timeout, connect=5.0),
            headers=dict(default_headers or {}),
            verify=verify_tls,
        )
        self._breakers: dict[str, _BreakerState] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def __aenter__(self) -> HTTPClient:
        """Async context manager — returns ``self``."""
        return self

    async def __aexit__(self, *_: object) -> None:
        """Close the underlying ``httpx.AsyncClient`` on exit."""
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying connection pool. Safe to call twice."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Circuit-breaker helpers
    # ------------------------------------------------------------------

    def _host_of(self, url: str) -> str:
        """Extract the netloc used as the circuit-breaker key."""
        assert _httpx_mod is not None, "guarded by __init__"
        parsed = _httpx_mod.URL(url if "://" in url else self.base_url + url)
        return parsed.host or "_unknown_"

    async def _breaker_check(self, host: str) -> None:
        """Open/half-open gate consulted before each attempt."""
        if self.failure_threshold <= 0:
            return
        async with self._lock:
            state = self._breakers.setdefault(host, _BreakerState())
            if state.opened_at == 0.0:
                return
            elapsed = time.monotonic() - state.opened_at
            if elapsed < self.recovery_seconds:
                raise CircuitOpenError(host)
            # half-open: allow one probe; reset on success/failure later.
            state.opened_at = 0.0

    async def _breaker_record(self, host: str, *, failed: bool) -> None:
        """Update the breaker state after an attempt finishes."""
        if self.failure_threshold <= 0:
            return
        async with self._lock:
            state = self._breakers.setdefault(host, _BreakerState())
            if failed:
                state.consecutive_failures += 1
                if state.consecutive_failures >= self.failure_threshold:
                    state.opened_at = time.monotonic()
            else:
                state.consecutive_failures = 0
                state.opened_at = 0.0

    # ------------------------------------------------------------------
    # Request loop
    # ------------------------------------------------------------------

    def _build_headers(
        self,
        explicit: Mapping[str, str] | None,
    ) -> dict[str, str]:
        """Merge contextvar / default / explicit headers."""
        headers: dict[str, str] = dict(explicit or {})
        if self.propagate_request_id and REQUEST_ID_HEADER not in headers:
            request_id = get_request_id()
            if request_id is not None:
                headers[REQUEST_ID_HEADER] = request_id
        return headers

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Perform an HTTP request with retries and circuit-breaker.

        Args:
            method (str): HTTP verb (``"GET"``, ``"POST"``, ...).
            url (str): Absolute URL or path relative to ``base_url``.
            params (Mapping[str, Any] | None): Query-string params.
            json (Any): JSON-serializable body. Mutually exclusive
                with ``data``.
            data (Any): Form body. Mutually exclusive with ``json``.
            headers (Mapping[str, str] | None): Per-request headers
                merged on top of ``default_headers`` + propagated
                ``X-Request-ID``.
            timeout (float | None): Override for this call.

        Returns:
            httpx.Response: The successful response. Caller checks
            ``response.status_code`` for 4xx outcomes (those are
            **not** retried).

        Raises:
            CircuitOpenError: When the per-host breaker is open.
            httpx.HTTPError: When all attempts failed (last
                exception is re-raised).
        """
        assert _httpx_mod is not None, "guarded by __init__"
        host = self._host_of(url)
        await self._breaker_check(host)

        merged_headers = self._build_headers(headers)
        last_exc: BaseException | None = None
        last_response: httpx.Response | None = None
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    data=data,
                    headers=merged_headers,
                    timeout=timeout if timeout is not None else self.timeout,
                )
            except (_httpx_mod.ConnectError, _httpx_mod.ReadTimeout) as exc:
                last_exc = exc
                if attempt == self.retry_policy.max_attempts:
                    await self._breaker_record(host, failed=True)
                    raise
                await asyncio.sleep(self.retry_policy.sleep_for(attempt))
                continue

            if response.status_code in self.retry_policy.retry_statuses:
                last_response = response
                if attempt == self.retry_policy.max_attempts:
                    await self._breaker_record(host, failed=True)
                    return response
                await asyncio.sleep(self.retry_policy.sleep_for(attempt))
                continue

            await self._breaker_record(host, failed=False)
            return response

        # Should be unreachable — the loop either returns or raises.
        if last_response is not None:
            return last_response
        assert last_exc is not None
        raise last_exc  # pragma: no cover

    # ------------------------------------------------------------------
    # Verb-level conveniences
    # ------------------------------------------------------------------

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """Shortcut for ``request("GET", url, ...)``."""
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        """Shortcut for ``request("POST", url, ...)``."""
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> httpx.Response:
        """Shortcut for ``request("PUT", url, ...)``."""
        return await self.request("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        """Shortcut for ``request("PATCH", url, ...)``."""
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        """Shortcut for ``request("DELETE", url, ...)``."""
        return await self.request("DELETE", url, **kwargs)


__all__: list[str] = [
    "REQUEST_ID_HEADER",
    "CircuitOpenError",
    "HTTPClient",
    "RetryPolicy",
]
