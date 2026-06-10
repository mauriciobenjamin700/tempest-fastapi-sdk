"""Graceful-shutdown helper: drain in-flight requests before exit.

When an orchestrator (Kubernetes, a process manager) wants a replica
gone, it sends ``SIGTERM`` and, after a grace period, ``SIGKILL``. If a
request is still mid-flight when the worker dies, that request is
severed — a 502 the client sees as a flaky API.

``GracefulShutdownMiddleware`` smooths the window:

1. Once **draining**, new requests get ``503 Service Unavailable`` with a
   ``Retry-After`` header, so a load balancer in front quickly stops
   routing here and the client retries elsewhere.
2. In-flight requests are **counted**; :meth:`wait_drained` blocks until
   they finish (or a timeout), so shutdown can wait them out instead of
   severing them.

You hold the instance and wire its :meth:`dispatch` into the app, then
drive it from the lifespan shutdown (which uvicorn runs on ``SIGTERM``,
and uvicorn owns the signal handling):

    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from starlette.middleware.base import BaseHTTPMiddleware

    from tempest_fastapi_sdk.api.middlewares.graceful import (
        GracefulShutdownMiddleware,
    )

    shutdown = GracefulShutdownMiddleware(drain_timeout=25.0)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        shutdown.begin_drain()
        await shutdown.wait_drained()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(BaseHTTPMiddleware, dispatch=shutdown.dispatch)

Set the orchestrator's grace period a little **above** ``drain_timeout``
and uvicorn's ``--timeout-graceful-shutdown`` to match.

!!! warning "Signal handling belongs to your server"
    uvicorn already installs ``SIGTERM`` handlers and triggers the
    lifespan shutdown — drive draining from there. The opt-in
    :meth:`install_signal_handlers` is only for servers that do **not**
    manage signals themselves; it chains the previous handler via
    :func:`signal.signal` and is a no-op off the main thread.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import TYPE_CHECKING, Any

from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from starlette.requests import Request
    from starlette.responses import Response

logger = logging.getLogger("tempest_fastapi_sdk.api.graceful")


class GracefulShutdownMiddleware:
    """Track in-flight requests and reject new ones with 503 while draining.

    Wire :meth:`dispatch` via
    ``app.add_middleware(BaseHTTPMiddleware, dispatch=shutdown.dispatch)``
    and call :meth:`begin_drain` / :meth:`wait_drained` from the
    lifespan shutdown.

    Attributes:
        drain_timeout (float): Seconds :meth:`wait_drained` waits for
            in-flight requests before giving up.
        retry_after (int): ``Retry-After`` header value on the 503s
            served while draining.
    """

    def __init__(
        self,
        *,
        drain_timeout: float = 30.0,
        retry_after: int = 5,
        exempt_paths: Sequence[str] = (),
    ) -> None:
        """Initialize the helper.

        Args:
            drain_timeout (float): Max seconds to wait for in-flight
                requests in :meth:`wait_drained`. Defaults to ``30.0``.
            retry_after (int): ``Retry-After`` seconds advertised on the
                503 served while draining. Defaults to ``5``.
            exempt_paths (Sequence[str]): Paths that keep being served
                during drain. Usually unnecessary — letting health
                endpoints return 503 is what makes a load balancer
                deregister the instance. Defaults to none.
        """
        self.drain_timeout: float = drain_timeout
        self.retry_after: int = retry_after
        self._exempt: frozenset[str] = frozenset(exempt_paths)
        self._in_flight: int = 0
        self._draining: bool = False
        self._idle: asyncio.Event = asyncio.Event()
        self._idle.set()

    @property
    def in_flight(self) -> int:
        """Return the number of requests currently being served.

        Returns:
            int: The in-flight request count.
        """
        return self._in_flight

    @property
    def is_draining(self) -> bool:
        """Return whether draining has begun.

        Returns:
            bool: ``True`` once :meth:`begin_drain` (or a signal) fired.
        """
        return self._draining

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Starlette ``BaseHTTPMiddleware`` dispatch hook.

        Serves ``503`` for new non-exempt requests once draining;
        otherwise counts the request as in-flight while it runs.

        Args:
            request (Request): The incoming request.
            call_next (Callable[[Request], Awaitable[Response]]): The
                downstream handler.

        Returns:
            Response: The downstream response, or a ``503`` while
            draining.
        """
        if self._draining and request.url.path not in self._exempt:
            return JSONResponse(
                {"detail": "Server is shutting down."},
                status_code=503,
                headers={
                    "Retry-After": str(self.retry_after),
                    "Connection": "close",
                },
            )
        self._in_flight += 1
        self._idle.clear()
        try:
            return await call_next(request)
        finally:
            self._in_flight -= 1
            if self._in_flight == 0:
                self._idle.set()

    def begin_drain(self) -> None:
        """Flip into draining mode (idempotent).

        New non-exempt requests get ``503`` from now on. Call from the
        lifespan shutdown hook.
        """
        if not self._draining:
            self._draining = True
            logger.info("graceful shutdown: draining (in-flight=%d)", self._in_flight)

    async def wait_drained(self) -> bool:
        """Wait until in-flight requests finish or the timeout elapses.

        Returns:
            bool: ``True`` if everything drained in time, ``False`` if
            :attr:`drain_timeout` elapsed with requests still running.
        """
        if self._in_flight == 0:
            return True
        try:
            await asyncio.wait_for(self._idle.wait(), timeout=self.drain_timeout)
            return True
        except TimeoutError:
            logger.warning(
                "graceful shutdown: drain timed out with %d request(s) in flight",
                self._in_flight,
            )
            return False

    def install_signal_handlers(
        self, signals: Sequence[int] = (signal.SIGTERM, signal.SIGINT)
    ) -> None:
        """Chain a drain trigger onto the given signals via ``signal.signal``.

        Only for servers that do **not** manage signals themselves —
        uvicorn does, so prefer the lifespan hook. Best-effort: a no-op
        when called off the main thread.

        Args:
            signals (Sequence[int]): Signals to hook. Defaults to
                ``SIGTERM`` and ``SIGINT``.
        """
        for sig in signals:
            previous = signal.getsignal(sig)

            def _handler(signum: int, frame: Any, _previous: Any = previous) -> None:
                self.begin_drain()
                if callable(_previous):
                    _previous(signum, frame)

            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):  # pragma: no cover - non-main thread
                logger.debug("could not install handler for signal %s", sig)


__all__: list[str] = [
    "GracefulShutdownMiddleware",
]
