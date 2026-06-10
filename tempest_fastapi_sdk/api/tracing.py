"""OpenTelemetry distributed tracing for FastAPI services.

``setup_tracing`` wires an OTLP span exporter and auto-instruments the
common layers of a Tempest service — FastAPI (incoming requests),
SQLAlchemy (queries), and httpx (outbound calls) — so a single trace
follows a request across process boundaries. It complements
:class:`~tempest_fastapi_sdk.api.middlewares.request_id.RequestIDMiddleware`:
the request id correlates *logs*, OpenTelemetry correlates *spans*.

Requires the ``[otel]`` extra::

    pip install "tempest-fastapi-sdk[otel]"

Design notes:

* **Everything is optional and lazy.** The OTel packages are imported
  inside the function, never at module import, so importing the SDK
  without the extra costs nothing and never crashes.
* **One tracer provider per process.** ``setup_tracing`` installs a
  global provider once; calling it twice is a no-op that returns the
  existing provider (OpenTelemetry itself warns and ignores a second
  ``set_tracer_provider``).
* **Instrumentors are best-effort.** SQLAlchemy / httpx
  instrumentation is skipped silently when the matching instrumentor
  package is not installed, so a service that only wants HTTP spans
  does not have to pull asyncpg-flavored extras.
* **Sampling and endpoint come from arguments**, not env vars, so the
  call site is the single source of truth. Pass ``otlp_endpoint=None``
  to install the provider with a console exporter (local debugging).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI
    from opentelemetry.sdk.trace import TracerProvider
    from sqlalchemy.engine import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine


def _require_otel() -> None:
    """Raise a clear error when the optional dependency is missing.

    Raises:
        ImportError: When the ``[otel]`` extra is not installed.
    """
    try:
        import opentelemetry.sdk.trace  # noqa: F401
    except ImportError as exc:  # pragma: no cover - guarded by [otel] extra
        raise ImportError(
            "Tracing requires the [otel] extra. "
            "Install with `pip install tempest-fastapi-sdk[otel]`."
        ) from exc


def setup_tracing(
    app: FastAPI,
    *,
    service_name: str,
    otlp_endpoint: str | None = None,
    sqlalchemy_engine: Engine | AsyncEngine | None = None,
    instrument_httpx: bool = True,
    sample_ratio: float = 1.0,
    resource_attributes: dict[str, str] | None = None,
    insecure: bool = True,
) -> TracerProvider:
    """Install OpenTelemetry tracing and auto-instrument the app.

    Call once at application startup, after the ``FastAPI`` instance
    exists and (when tracing queries) after the database engine is
    connected.

    Args:
        app (FastAPI): The application to instrument for incoming
            requests.
        service_name (str): Logical service name attached to every
            span (``service.name`` resource attribute) — this is how
            the service shows up in Jaeger / Tempo / Honeycomb.
        otlp_endpoint (str | None): OTLP/gRPC collector endpoint
            (e.g. ``"http://otel-collector:4317"``). When ``None``,
            a console span exporter is installed instead — handy for
            local debugging without a collector.
        sqlalchemy_engine (Engine | AsyncEngine | None): When given,
            instrument this engine so every query becomes a span.
            Pass ``db.engine`` from
            :class:`~tempest_fastapi_sdk.db.connection.AsyncDatabaseManager`.
            Requires ``opentelemetry-instrumentation-sqlalchemy``.
        instrument_httpx (bool): When ``True`` (default), instrument
            outbound httpx calls so they appear as child spans.
            Silently skipped if the httpx instrumentor is absent.
        sample_ratio (float): Head-based sampling ratio in ``[0, 1]``.
            ``1.0`` traces every request; ``0.1`` traces ~10%.
        resource_attributes (dict[str, str] | None): Extra resource
            attributes merged onto every span (e.g.
            ``{"deployment.environment": "prod"}``).
        insecure (bool): Whether the OTLP gRPC channel is plaintext.
            Defaults to ``True`` (typical for an in-cluster collector).
            Ignored when ``otlp_endpoint`` is ``None``.

    Returns:
        TracerProvider: The installed (or already-installed) global
        tracer provider, so callers can register custom span
        processors.

    Raises:
        ImportError: If the ``[otel]`` extra is not installed.
        ValueError: If ``sample_ratio`` is outside ``[0, 1]``.
    """
    _require_otel()

    if not 0.0 <= sample_ratio <= 1.0:
        raise ValueError("sample_ratio must be between 0.0 and 1.0")

    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SpanExporter,
    )
    from opentelemetry.sdk.trace.sampling import (
        ParentBased,
        TraceIdRatioBased,
    )

    existing = trace.get_tracer_provider()
    if isinstance(existing, TracerProvider):
        # A provider is already installed (e.g. setup_tracing ran
        # twice, or the host wired its own). Reuse it, just attach the
        # FastAPI instrumentation to this app.
        _instrument_fastapi(app, existing)
        return existing

    attributes: dict[str, Any] = {"service.name": service_name}
    if resource_attributes:
        attributes.update(resource_attributes)
    resource = Resource.create(attributes)

    sampler = ParentBased(root=TraceIdRatioBased(sample_ratio))
    provider = TracerProvider(resource=resource, sampler=sampler)

    exporter: SpanExporter
    if otlp_endpoint is None:
        exporter = ConsoleSpanExporter()
    else:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=insecure)

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    _instrument_fastapi(app, provider)
    if sqlalchemy_engine is not None:
        _instrument_sqlalchemy(sqlalchemy_engine)
    if instrument_httpx:
        _instrument_httpx()

    return provider


def _instrument_fastapi(app: FastAPI, provider: TracerProvider) -> None:
    """Attach FastAPI request instrumentation to the app.

    Args:
        app (FastAPI): The app to instrument.
        provider (TracerProvider): The tracer provider to bind spans to.
    """
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)


def _instrument_sqlalchemy(engine: Engine | AsyncEngine) -> None:
    """Instrument a SQLAlchemy engine when the instrumentor is present.

    Unwraps an ``AsyncEngine`` to its ``.sync_engine`` (the SQLAlchemy
    instrumentor binds to the sync engine). Silently no-ops when the
    instrumentor package is not installed.

    Args:
        engine (Engine | AsyncEngine): The engine to instrument.
    """
    try:
        from opentelemetry.instrumentation.sqlalchemy import (
            SQLAlchemyInstrumentor,
        )
    except ImportError:  # pragma: no cover - optional sub-extra
        return
    sync_engine = getattr(engine, "sync_engine", engine)
    SQLAlchemyInstrumentor().instrument(engine=sync_engine)


def _instrument_httpx() -> None:
    """Instrument outbound httpx calls when the instrumentor is present.

    Silently no-ops when the instrumentor package is not installed.
    """
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    except ImportError:  # pragma: no cover - optional sub-extra
        return
    HTTPXClientInstrumentor().instrument()


__all__: list[str] = [
    "setup_tracing",
]
