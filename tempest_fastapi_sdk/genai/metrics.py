"""Prometheus metrics for genai inference.

`GenAIMetrics` bundles the counters and histogram every inference service ends
up reimplementing — request count, latency, and tokens in/out — labelled by
model and operation. It reuses ``prometheus-client`` (the ``[prometheus]``
extra) and accepts an explicit registry so it composes with the SDK's existing
``PrometheusMiddleware`` / ``/metrics`` endpoint. It is **opt-in**: nothing is
recorded unless you construct one and either call :meth:`GenAIMetrics.track`
around a call or pass ``metrics=`` to a generator.
"""

from __future__ import annotations

import time
from types import TracebackType
from typing import Any


def _require_prometheus() -> Any:
    """Import ``prometheus_client`` or raise a helpful error.

    Returns:
        Any: The imported ``prometheus_client`` module.

    Raises:
        ImportError: When the ``[prometheus]`` extra is not installed.
    """
    try:
        import prometheus_client
    except ImportError as exc:
        raise ImportError(
            "GenAI metrics require the optional [prometheus] extra. "
            "Install with: pip install tempest-fastapi-sdk[prometheus]",
        ) from exc
    return prometheus_client


class GenAIMetrics:
    """Prometheus counters + histogram for genai inference.

    Example:

        >>> metrics = GenAIMetrics()
        >>> async with metrics.track("Qwen/Qwen2.5-7B", "generate") as span:
        ...     span.tokens_out = 128
        ...     ...  # run the model

    Attributes:
        namespace (str): Metric name prefix.
    """

    def __init__(self, *, namespace: str = "genai", registry: Any = None) -> None:
        """Build the metric objects.

        Args:
            namespace (str): Prefix for every metric name.
            registry (Any): A ``prometheus_client.CollectorRegistry`` to
                register on; ``None`` uses the client's default registry.
        """
        prometheus = _require_prometheus()
        self.namespace = namespace
        kwargs: dict[str, Any] = {} if registry is None else {"registry": registry}
        self._requests = prometheus.Counter(
            f"{namespace}_requests_total",
            "Total genai inference requests.",
            ["model", "op"],
            **kwargs,
        )
        self._latency = prometheus.Histogram(
            f"{namespace}_request_seconds",
            "genai inference request latency in seconds.",
            ["model", "op"],
            **kwargs,
        )
        self._tokens_in = prometheus.Counter(
            f"{namespace}_tokens_in_total",
            "Total input tokens sent to genai models.",
            ["model"],
            **kwargs,
        )
        self._tokens_out = prometheus.Counter(
            f"{namespace}_tokens_out_total",
            "Total output tokens produced by genai models.",
            ["model"],
            **kwargs,
        )

    def record(
        self,
        model: str,
        op: str,
        duration_seconds: float,
        *,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
    ) -> None:
        """Record one completed inference.

        Args:
            model (str): The model id label.
            op (str): The operation label (``"generate"`` / ``"chat"`` / …).
            duration_seconds (float): Wall-clock duration.
            tokens_in (int | None): Input tokens, when known.
            tokens_out (int | None): Output tokens, when known.
        """
        self._requests.labels(model=model, op=op).inc()
        self._latency.labels(model=model, op=op).observe(duration_seconds)
        self.record_tokens(model, tokens_in=tokens_in, tokens_out=tokens_out)

    def record_tokens(
        self,
        model: str,
        *,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
    ) -> None:
        """Add to the input/output token counters (no-op for ``None``).

        Args:
            model (str): The model id label.
            tokens_in (int | None): Input tokens to add.
            tokens_out (int | None): Output tokens to add.
        """
        if tokens_in:
            self._tokens_in.labels(model=model).inc(tokens_in)
        if tokens_out:
            self._tokens_out.labels(model=model).inc(tokens_out)

    def track(self, model: str, op: str = "generate") -> _Span:
        """Return an async context manager that times and records a call.

        Set ``span.tokens_in`` / ``span.tokens_out`` inside the block to record
        token counts alongside the latency.

        Args:
            model (str): The model id label.
            op (str): The operation label.

        Returns:
            _Span: The timing span (async context manager).
        """
        return _Span(self, model, op)


class _Span:
    """Async context manager timing one inference and recording it on exit.

    Attributes:
        tokens_in (int | None): Set inside the block to record input tokens.
        tokens_out (int | None): Set inside the block to record output tokens.
    """

    def __init__(self, metrics: GenAIMetrics, model: str, op: str) -> None:
        """Initialize the span.

        Args:
            metrics (GenAIMetrics): The owning metrics bundle.
            model (str): The model id label.
            op (str): The operation label.
        """
        self._metrics = metrics
        self._model = model
        self._op = op
        self._start: float = 0.0
        self.tokens_in: int | None = None
        self.tokens_out: int | None = None

    async def __aenter__(self) -> _Span:
        """Start the timer."""
        self._start = time.monotonic()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Record the elapsed time and any token counts set on the span."""
        self._metrics.record(
            self._model,
            self._op,
            time.monotonic() - self._start,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
        )


__all__: list[str] = [
    "GenAIMetrics",
]
