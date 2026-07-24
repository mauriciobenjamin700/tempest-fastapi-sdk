"""OpenTelemetry spans for genai inference calls.

Ambient tracing that reuses the global ``TracerProvider`` configured by
:func:`tempest_fastapi_sdk.api.tracing.setup_tracing`. Wrap a genai call in
:class:`genai_span` and, when an OTel provider is set, it emits a span
following the OpenTelemetry **GenAI semantic conventions**
(``gen_ai.system`` / ``gen_ai.operation.name`` / ``gen_ai.request.model`` /
``gen_ai.usage.input_tokens`` / ``gen_ai.usage.output_tokens``); the span name
is ``"{operation} {model}"`` per the same convention.

Zero-config and zero-cost by default. If the ``[otel]`` extra is not installed
the context manager is a no-op; if it is installed but no provider was
configured, the global no-op tracer produces non-recording spans. Nothing has
to be injected into the generators — call ``setup_tracing`` once at app startup
and genai spans start flowing next to the FastAPI / SQLAlchemy / httpx spans.

The generators already wrap ``generate`` / ``chat`` / ``embed`` and the RAG
``Retriever`` in :class:`genai_span`, so instrumentation is automatic; set
:attr:`genai_span.tokens_in` / :attr:`genai_span.tokens_out` inside the block
to attach token usage (the Ollama path does this from its response counters).
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

_TRACER_NAME: str = "tempest.genai"
_SYSTEM: str = "tempest"


def _otel_trace() -> Any | None:
    """Return the ``opentelemetry.trace`` module, or ``None`` when absent.

    Returns:
        Any | None: The imported module when the ``[otel]`` extra is installed,
        otherwise ``None`` (so callers degrade to a no-op).
    """
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    return trace


class genai_span:  # noqa: N801
    """Async context manager emitting one OTel span for a genai call.

    Example:

        >>> async with genai_span("chat", "Qwen/Qwen2.5-7B") as span:
        ...     span.tokens_out = 128
        ...     ...  # run the model

    Attributes:
        tokens_in (int | None): Set inside the block to record input tokens
            (``gen_ai.usage.input_tokens``).
        tokens_out (int | None): Set inside the block to record output tokens
            (``gen_ai.usage.output_tokens``).
    """

    def __init__(self, operation: str, model: str, **attributes: Any) -> None:
        """Configure the span (no span is started until entry).

        Args:
            operation (str): The genai operation name
                (``"generate"`` / ``"chat"`` / ``"embed"`` / ``"retrieve"``).
            model (str): The model id (``gen_ai.request.model``).
            **attributes (Any): Extra span attributes; ``None`` values are
                dropped. Keys should already be namespaced (e.g.
                ``"gen_ai.request.top_k"``).
        """
        self._operation = operation
        self._model = model
        self._attributes = attributes
        self._span: Any = None
        self._cm: Any = None
        self.tokens_in: int | None = None
        self.tokens_out: int | None = None

    async def __aenter__(self) -> genai_span:
        """Start the span when an OTel provider is available.

        Returns:
            genai_span: ``self`` (a no-op carrier when OTel is absent).
        """
        trace = _otel_trace()
        if trace is None:
            return self
        tracer = trace.get_tracer(_TRACER_NAME)
        self._cm = tracer.start_as_current_span(f"{self._operation} {self._model}")
        self._span = self._cm.__enter__()
        self._span.set_attribute("gen_ai.system", _SYSTEM)
        self._span.set_attribute("gen_ai.operation.name", self._operation)
        self._span.set_attribute("gen_ai.request.model", self._model)
        for key, value in self._attributes.items():
            if value is not None:
                self._span.set_attribute(key, value)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Attach token usage, mark errors, and close the span."""
        if self._span is None:
            return
        if self.tokens_in is not None:
            self._span.set_attribute("gen_ai.usage.input_tokens", self.tokens_in)
        if self.tokens_out is not None:
            self._span.set_attribute("gen_ai.usage.output_tokens", self.tokens_out)
        if exc is not None:
            from opentelemetry.trace import Status, StatusCode

            self._span.record_exception(exc)
            self._span.set_status(Status(StatusCode.ERROR, str(exc)))
        self._cm.__exit__(exc_type, exc, tb)


__all__: list[str] = [
    "genai_span",
]
