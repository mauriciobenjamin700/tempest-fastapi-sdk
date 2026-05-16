"""FastAPI integration primitives exposed at module level."""

from tempest_fastapi_sdk.api.dependencies import (
    make_token_dependency,
    require_x_token,
)
from tempest_fastapi_sdk.api.handlers import (
    app_exception_handler,
    register_exception_handlers,
)
from tempest_fastapi_sdk.api.middlewares import (
    RequestIDMiddleware,
    apply_cors,
)
from tempest_fastapi_sdk.api.routers import HealthCheck, make_health_router

__all__: list[str] = [
    "HealthCheck",
    "RequestIDMiddleware",
    "app_exception_handler",
    "apply_cors",
    "make_health_router",
    "make_token_dependency",
    "register_exception_handlers",
    "require_x_token",
]
