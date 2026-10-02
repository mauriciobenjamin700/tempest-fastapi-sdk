"""Tests for tempest_fastapi_sdk.api.tracing.

The tracer provider is a process-global singleton, so these tests are
careful to assert only on behavior that survives a shared provider:
argument validation, idempotent reuse, and that a provider ends up
installed. A console exporter is used (``otlp_endpoint=None``) so no
collector is required; the span-tree tests add an in-memory exporter to
the shared provider and read only the spans their own request produced.
"""

import warnings
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from sqlalchemy import text

from tempest_fastapi_sdk import AsyncDatabaseManager, setup_tracing
from tempest_fastapi_sdk.api import instrument_sqlalchemy_engine


class TestArgumentValidation:
    def test_sample_ratio_out_of_range_rejected(self) -> None:
        app = FastAPI()
        with pytest.raises(ValueError):
            setup_tracing(app, service_name="svc", sample_ratio=1.5)
        with pytest.raises(ValueError):
            setup_tracing(app, service_name="svc", sample_ratio=-0.1)


class TestInstallation:
    def test_installs_a_provider_and_is_idempotent(self) -> None:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider

        app = FastAPI()
        provider = setup_tracing(app, service_name="test-service", otlp_endpoint=None)
        assert isinstance(provider, TracerProvider)
        assert isinstance(trace.get_tracer_provider(), TracerProvider)

        app2 = FastAPI()
        provider2 = setup_tracing(app2, service_name="other", otlp_endpoint=None)
        assert provider2 is trace.get_tracer_provider()

    def test_fastapi_app_is_instrumented(self) -> None:
        app = FastAPI()
        setup_tracing(app, service_name="svc", otlp_endpoint=None)
        assert getattr(app, "_is_instrumented_by_opentelemetry", False) is True


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    """Capture finished spans and leave SQLAlchemy uninstrumented.

    The tracer provider and the SQLAlchemy instrumentor are both
    process-wide singletons, so another test may have instrumented
    SQLAlchemy already; this fixture starts and ends with it
    uninstrumented so each test measures its own call. The provider is
    flushed on teardown so the console exporter writes while pytest's
    captured stdout is still open, not at interpreter exit.

    Yields:
        InMemorySpanExporter: The exporter receiving every finished span.
    """
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    instrumentor = SQLAlchemyInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.uninstrument()
    provider = setup_tracing(FastAPI(), service_name="svc", otlp_endpoint=None)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    try:
        yield exporter
    finally:
        provider.force_flush()
        exporter.shutdown()
        if instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.uninstrument()


def _build_app(
    db: AsyncDatabaseManager,
    *,
    in_lifespan: Callable[[FastAPI], object],
) -> FastAPI:
    """Build an app that connects ``db`` in the lifespan and queries it.

    Args:
        db (AsyncDatabaseManager): The manager connected by the lifespan.
        in_lifespan (Callable[[FastAPI], object]): What the lifespan runs
            right after ``connect()``; its return value is discarded.

    Returns:
        FastAPI: The app, with ``GET /orders`` running ``SELECT 1``.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Connect, run the hook under test, disconnect on shutdown.

        Args:
            app (FastAPI): The application.

        Yields:
            None: While the app serves.
        """
        await db.connect()
        in_lifespan(app)
        yield
        await db.disconnect()

    app = FastAPI(lifespan=lifespan)

    @app.get("/orders")
    async def orders() -> dict[str, int]:
        """Run one query.

        Returns:
            dict[str, int]: The query result.
        """
        async with db.get_session_context() as session:
            result = await session.execute(text("SELECT 1"))
            return {"n": result.scalar_one()}

    return app


def _parents(exporter: InMemorySpanExporter) -> dict[str, str | None]:
    """Map each finished span name to its parent span name.

    Args:
        exporter (InMemorySpanExporter): The span sink.

    Returns:
        dict[str, str | None]: Span name to parent name (``None`` for a
        root span).
    """
    finished = exporter.get_finished_spans()
    by_id: dict[int, str] = {
        s.context.span_id: s.name for s in finished if s.context is not None
    }
    return {
        s.name: by_id.get(s.parent.span_id) if s.parent is not None else None
        for s in finished
    }


class TestLateCall:
    """``setup_tracing`` from the lifespan cannot trace requests."""

    def test_call_from_lifespan_warns_and_traces_no_request(
        self, spans: InMemorySpanExporter
    ) -> None:
        db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        app = _build_app(
            db,
            in_lifespan=lambda a: setup_tracing(
                a, service_name="svc", otlp_endpoint=None
            ),
        )
        with (
            pytest.warns(RuntimeWarning, match="module level"),
            TestClient(app) as client,
        ):
            assert client.get("/orders").status_code == 200
        assert "GET /orders" not in _parents(spans)

    def test_module_level_call_does_not_warn(self) -> None:
        app = FastAPI()
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            setup_tracing(app, service_name="svc", otlp_endpoint=None)


class TestInstrumentSqlalchemyEngine:
    """The engine connected in the lifespan is traced under the request."""

    def test_query_span_is_child_of_request_span(
        self, spans: InMemorySpanExporter
    ) -> None:
        db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        app = _build_app(
            db, in_lifespan=lambda _: instrument_sqlalchemy_engine(db.engine)
        )
        setup_tracing(app, service_name="svc", otlp_endpoint=None)
        with TestClient(app) as client:
            assert client.get("/orders").status_code == 200
        parents = _parents(spans)
        assert parents["GET /orders"] is None
        selects = [name for name in parents if name.startswith("SELECT")]
        assert selects
        assert all(parents[name] == "GET /orders" for name in selects)

    def test_engine_passed_on_a_reused_provider_is_instrumented(
        self, spans: InMemorySpanExporter
    ) -> None:
        """A second ``setup_tracing`` used to drop ``sqlalchemy_engine``."""
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from sqlalchemy import create_engine

        engine = create_engine("sqlite://")
        setup_tracing(
            FastAPI(),
            service_name="svc",
            otlp_endpoint=None,
            sqlalchemy_engine=engine,
        )
        assert SQLAlchemyInstrumentor().is_instrumented_by_opentelemetry
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        assert any(name.startswith("SELECT") for name in _parents(spans))
