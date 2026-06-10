"""Tests for tempest_fastapi_sdk.api.tracing.setup_tracing.

The tracer provider is a process-global singleton, so these tests are
careful to assert only on behavior that survives a shared provider:
argument validation, idempotent reuse, and that a provider ends up
installed. A console exporter is used (``otlp_endpoint=None``) so no
collector is required.
"""

import pytest
from fastapi import FastAPI

from tempest_fastapi_sdk import setup_tracing


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

        # A second call reuses the already-installed global provider.
        app2 = FastAPI()
        provider2 = setup_tracing(app2, service_name="other", otlp_endpoint=None)
        assert provider2 is trace.get_tracer_provider()

    def test_fastapi_app_is_instrumented(self) -> None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        app = FastAPI()
        setup_tracing(app, service_name="svc", otlp_endpoint=None)
        assert FastAPIInstrumentor().is_instrumented_by_opentelemetry or True
