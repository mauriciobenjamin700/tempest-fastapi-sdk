"""Reusable FastAPI routers shipped with the SDK."""

from tempest_fastapi_sdk.api.routers.health import (
    HealthCheck as HealthCheck,
)
from tempest_fastapi_sdk.api.routers.health import (
    make_health_router as make_health_router,
)
from tempest_fastapi_sdk.api.routers.logs import (
    DEFAULT_MAX_RECORDS_PER_FILE as DEFAULT_MAX_RECORDS_PER_FILE,
)
from tempest_fastapi_sdk.api.routers.logs import (
    LogReadResult as LogReadResult,
)
from tempest_fastapi_sdk.api.routers.logs import (
    LogSource as LogSource,
)
from tempest_fastapi_sdk.api.routers.logs import (
    clear_log_files as clear_log_files,
)
from tempest_fastapi_sdk.api.routers.logs import (
    make_logs_router as make_logs_router,
)
from tempest_fastapi_sdk.api.routers.logs import (
    read_log_entries as read_log_entries,
)
from tempest_fastapi_sdk.api.routers.logs import (
    render_entries_json as render_entries_json,
)
from tempest_fastapi_sdk.api.routers.logs import (
    render_entries_markdown as render_entries_markdown,
)
from tempest_fastapi_sdk.api.routers.metrics import (
    DEFAULT_LATENCY_BUCKETS as DEFAULT_LATENCY_BUCKETS,
)
from tempest_fastapi_sdk.api.routers.metrics import (
    BusinessMetrics as BusinessMetrics,
)
from tempest_fastapi_sdk.api.routers.metrics import (
    PrometheusMiddleware as PrometheusMiddleware,
)
from tempest_fastapi_sdk.api.routers.metrics import (
    make_prometheus_registry as make_prometheus_registry,
)
from tempest_fastapi_sdk.api.routers.metrics import (
    make_prometheus_router as make_prometheus_router,
)
from tempest_fastapi_sdk.api.routers.tool_spec import (
    make_tool_spec_router as make_tool_spec_router,
)

__all__: list[str] = [
    "DEFAULT_LATENCY_BUCKETS",
    "DEFAULT_MAX_RECORDS_PER_FILE",
    "BusinessMetrics",
    "HealthCheck",
    "LogReadResult",
    "LogSource",
    "PrometheusMiddleware",
    "clear_log_files",
    "make_health_router",
    "make_logs_router",
    "make_prometheus_registry",
    "make_prometheus_router",
    "make_tool_spec_router",
    "read_log_entries",
    "render_entries_json",
    "render_entries_markdown",
]
