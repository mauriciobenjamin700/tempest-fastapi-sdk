"""Reusable FastAPI routers shipped with the SDK."""

from tempest_fastapi_sdk.api.routers.health import (
    HealthCheck,
    make_health_router,
)
from tempest_fastapi_sdk.api.routers.logs import (
    LogSource,
    make_logs_router,
)
from tempest_fastapi_sdk.api.routers.metrics import (
    DEFAULT_LATENCY_BUCKETS,
    BusinessMetrics,
    PrometheusMiddleware,
    make_prometheus_registry,
    make_prometheus_router,
)
from tempest_fastapi_sdk.api.routers.tool_spec import make_tool_spec_router

__all__: list[str] = [
    "DEFAULT_LATENCY_BUCKETS",
    "BusinessMetrics",
    "HealthCheck",
    "LogSource",
    "PrometheusMiddleware",
    "make_health_router",
    "make_logs_router",
    "make_prometheus_registry",
    "make_prometheus_router",
    "make_tool_spec_router",
]
