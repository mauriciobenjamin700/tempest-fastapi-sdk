"""Prometheus ``/metrics`` endpoint built on ``prometheus-client``.

Exposes the metrics in the standard Prometheus exposition format so
any Prometheus scraper / Grafana Agent / VictoriaMetrics agent can
poll the service directly. Built-in collectors track per-request
counts, latency histogram, and in-flight gauge; callers can also
register their own counters/histograms against the shared registry.

The endpoint **MUST** stay behind a private network or an
``X-Token`` dependency in production — metric labels often leak
internal path structure.

Requires the ``[prometheus]`` extra.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, Depends, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

if TYPE_CHECKING:
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover - guarded by [prometheus] extra
    _PROMETHEUS_AVAILABLE = False


# Default histogram buckets in seconds — covers fast-IO services up
# to ~10s p99 without absurd cardinality.
DEFAULT_LATENCY_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)


def _require_prometheus() -> None:
    """Raise a clear error when the optional dependency is missing."""
    if not _PROMETHEUS_AVAILABLE:
        raise ImportError(
            "Prometheus metrics require the [prometheus] extra. "
            "Install with `pip install tempest-fastapi-sdk[prometheus]`."
        )


def make_prometheus_registry() -> CollectorRegistry:
    """Build a fresh :class:`CollectorRegistry`.

    Use this once at app boot, share the registry across the
    middleware and any custom metric you register, then pass it to
    :func:`make_prometheus_router`.

    Returns:
        CollectorRegistry: An empty registry, decoupled from the
        ``prometheus_client`` default singleton so tests don't bleed
        metrics between runs.

    Raises:
        ImportError: When the ``[prometheus]`` extra is missing.
    """
    _require_prometheus()
    return CollectorRegistry()


class PrometheusMiddleware(BaseHTTPMiddleware):
    """ASGI middleware tracking HTTP requests on three core metrics.

    Registered series:

    - ``http_requests_total{method, path, status}`` (Counter) —
      every request counts here once the response status is known.
    - ``http_request_duration_seconds{method, path}`` (Histogram) —
      end-to-end latency in seconds.
    - ``http_requests_in_progress{method}`` (Gauge) — live inflight
      count, decremented in a ``finally`` so dropped connections
      never leave stale gauges.

    The ``path`` label uses the **route template** (e.g.
    ``/orders/{order_id}``) when the request hit a FastAPI route,
    not the raw URL — that keeps the cardinality bounded.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        registry: CollectorRegistry,
        latency_buckets: tuple[float, ...] = DEFAULT_LATENCY_BUCKETS,
    ) -> None:
        """Initialize the middleware.

        Args:
            app (ASGIApp): The wrapped ASGI app.
            registry (CollectorRegistry): Shared registry. Reuse the
                same instance for ``make_prometheus_router`` so the
                ``/metrics`` endpoint scrapes these series.
            latency_buckets (tuple[float, ...]): Histogram bucket
                upper bounds in seconds.

        Raises:
            ImportError: When the ``[prometheus]`` extra is missing.
        """
        _require_prometheus()
        super().__init__(app)
        self.requests_total: Counter = Counter(
            "http_requests_total",
            "HTTP requests by method, route template, and response status.",
            labelnames=("method", "path", "status"),
            registry=registry,
        )
        self.request_duration: Histogram = Histogram(
            "http_request_duration_seconds",
            "HTTP request latency by method and route template (seconds).",
            labelnames=("method", "path"),
            buckets=latency_buckets,
            registry=registry,
        )
        self.in_progress: Gauge = Gauge(
            "http_requests_in_progress",
            "HTTP requests currently being handled.",
            labelnames=("method",),
            registry=registry,
        )

    @staticmethod
    def _route_template(request: Request) -> str:
        """Pick the route template the request matched, falling back to the path."""
        route = request.scope.get("route")
        if route is not None and hasattr(route, "path"):
            return str(route.path)
        return request.url.path

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Wrap the request with counters, gauge, and latency timer."""
        method = request.method
        self.in_progress.labels(method=method).inc()
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            elapsed = time.perf_counter() - start
            path = self._route_template(request)
            self.requests_total.labels(
                method=method,
                path=path,
                status=str(status_code),
            ).inc()
            self.request_duration.labels(method=method, path=path).observe(elapsed)
            self.in_progress.labels(method=method).dec()


def make_prometheus_router(
    *,
    registry: CollectorRegistry,
    path: str = "/metrics",
    dependencies: list[Callable[..., Any]] | None = None,
) -> APIRouter:
    """Build the ``GET /metrics`` router scraping ``registry``.

    Args:
        registry (CollectorRegistry): The same registry passed to
            :class:`PrometheusMiddleware` and any custom metric.
        path (str): Endpoint path. Defaults to ``/metrics``.
        dependencies (list | None): FastAPI dependencies to attach
            — typically ``[Depends(require_x_token)]`` so the
            endpoint isn't world-readable.

    Returns:
        APIRouter: Mount with ``app.include_router(router)``.

    Raises:
        ImportError: When the ``[prometheus]`` extra is missing.
    """
    _require_prometheus()
    router = APIRouter()

    @router.get(
        path,
        dependencies=[Depends(d) for d in (dependencies or [])],
        include_in_schema=False,
    )
    async def metrics() -> Response:
        """Render the registry in Prometheus exposition format."""
        return Response(
            content=generate_latest(registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    return router


class BusinessMetrics:
    """A typed factory for application metrics on the shared registry.

    Wraps ``prometheus_client``'s ``Counter`` / ``Gauge`` / ``Histogram``
    so a service declares its own business metrics — orders placed,
    queue depth, job duration — without repeating the ``registry=`` wiring
    or reaching for the global default registry. Metrics land on the same
    ``/metrics`` endpoint as the built-in request collectors.

    Creation is de-duplicated by name, so calling a factory twice with
    the same name (module re-import, tests, hot reload) returns the same
    metric instead of raising ``Duplicated timeseries``.

    No magic: the returned objects are the real ``prometheus_client``
    metrics — ``.inc()`` / ``.set()`` / ``.observe()`` / ``.labels(...)``
    behave exactly as upstream documents.

    Example:
        ```python
        registry = make_prometheus_registry()
        metrics = BusinessMetrics(registry, namespace="shop")
        orders = metrics.counter("orders_total", "Orders placed", labelnames=["status"])
        orders.labels(status="paid").inc()
        ```

    Attributes:
        namespace (str): Optional prefix applied to every metric name
            (``"shop"`` → ``shop_orders_total``).
    """

    def __init__(
        self,
        registry: CollectorRegistry,
        *,
        namespace: str = "",
    ) -> None:
        """Bind the factory to a registry.

        Args:
            registry (CollectorRegistry): The shared registry (from
                :func:`make_prometheus_registry`) so the metrics show up
                on the same ``/metrics`` endpoint.
            namespace (str): Optional name prefix for every metric.

        Raises:
            ImportError: When the ``[prometheus]`` extra is missing.
        """
        _require_prometheus()
        self._registry: CollectorRegistry = registry
        self.namespace: str = namespace
        self._metrics: dict[str, Any] = {}

    def _full_name(self, name: str) -> str:
        """Return ``name`` prefixed with the namespace, if any.

        Args:
            name (str): The bare metric name.

        Returns:
            str: ``"{namespace}_{name}"`` or ``name`` when no namespace.
        """
        return f"{self.namespace}_{name}" if self.namespace else name

    def _get_or_create(
        self,
        factory: Any,
        name: str,
        documentation: str,
        labelnames: Sequence[str],
        **kwargs: Any,
    ) -> Any:
        """Create a metric (or return the cached one with the same name).

        Args:
            factory (Any): The ``prometheus_client`` metric class.
            name (str): The bare metric name.
            documentation (str): The HELP text.
            labelnames (Sequence[str]): Label names for the metric.
            **kwargs (Any): Extra metric kwargs (e.g. ``buckets``).

        Returns:
            Any: The registered metric instance.
        """
        full = self._full_name(name)
        cached = self._metrics.get(full)
        if cached is not None:
            return cached
        metric = factory(
            full,
            documentation,
            labelnames=list(labelnames),
            registry=self._registry,
            **kwargs,
        )
        self._metrics[full] = metric
        return metric

    def counter(
        self,
        name: str,
        documentation: str,
        *,
        labelnames: Sequence[str] = (),
    ) -> Counter:
        """Register (or fetch) a monotonically increasing counter.

        Args:
            name (str): The metric name.
            documentation (str): The HELP text.
            labelnames (Sequence[str]): Optional label names.

        Returns:
            Counter: The ``prometheus_client`` counter.
        """
        return cast(
            "Counter", self._get_or_create(Counter, name, documentation, labelnames)
        )

    def gauge(
        self,
        name: str,
        documentation: str,
        *,
        labelnames: Sequence[str] = (),
    ) -> Gauge:
        """Register (or fetch) a gauge (up/down value).

        Args:
            name (str): The metric name.
            documentation (str): The HELP text.
            labelnames (Sequence[str]): Optional label names.

        Returns:
            Gauge: The ``prometheus_client`` gauge.
        """
        return cast(
            "Gauge", self._get_or_create(Gauge, name, documentation, labelnames)
        )

    def histogram(
        self,
        name: str,
        documentation: str,
        *,
        labelnames: Sequence[str] = (),
        buckets: Sequence[float] | None = None,
    ) -> Histogram:
        """Register (or fetch) a histogram (value distribution).

        Args:
            name (str): The metric name.
            documentation (str): The HELP text.
            labelnames (Sequence[str]): Optional label names.
            buckets (Sequence[float] | None): Bucket upper bounds;
                defaults to ``prometheus_client``'s default buckets.

        Returns:
            Histogram: The ``prometheus_client`` histogram.
        """
        extra: dict[str, Any] = {}
        if buckets is not None:
            extra["buckets"] = tuple(buckets)
        return cast(
            "Histogram",
            self._get_or_create(Histogram, name, documentation, labelnames, **extra),
        )


__all__: list[str] = [
    "DEFAULT_LATENCY_BUCKETS",
    "BusinessMetrics",
    "PrometheusMiddleware",
    "make_prometheus_registry",
    "make_prometheus_router",
]
