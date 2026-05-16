"""Reusable FastAPI routers shipped with the SDK."""

from tempest_fastapi_sdk.api.routers.health import (
    HealthCheck,
    make_health_router,
)

__all__: list[str] = [
    "HealthCheck",
    "make_health_router",
]
