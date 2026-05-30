"""FastAPI integration primitives exposed at module level."""

from tempest_fastapi_sdk.api.cookies import (
    SameSite,
    clear_cookie,
    set_cookie,
)
from tempest_fastapi_sdk.api.dependencies import (
    make_bearer_token_dependency,
    make_jwt_user_dependency,
    make_permission_dependency,
    make_role_dependency,
    make_token_dependency,
    require_x_token,
)
from tempest_fastapi_sdk.api.handlers import (
    app_exception_handler,
    register_exception_handlers,
)
from tempest_fastapi_sdk.api.middlewares import (
    RateLimitMiddleware,
    RequestIDMiddleware,
    apply_cors,
)
from tempest_fastapi_sdk.api.routers import (
    HealthCheck,
    make_health_router,
    make_tool_spec_router,
)
from tempest_fastapi_sdk.api.server import run_server
from tempest_fastapi_sdk.api.static import (
    DEFAULT_STATIC_SECURITY_HEADERS,
    HardenedStaticFiles,
)
from tempest_fastapi_sdk.api.webhooks import (
    RSAWebhookSignatureVerifier,
    WebhookSignatureVerifier,
)

__all__: list[str] = [
    "DEFAULT_STATIC_SECURITY_HEADERS",
    "HardenedStaticFiles",
    "HealthCheck",
    "RSAWebhookSignatureVerifier",
    "RateLimitMiddleware",
    "RequestIDMiddleware",
    "SameSite",
    "WebhookSignatureVerifier",
    "app_exception_handler",
    "apply_cors",
    "clear_cookie",
    "make_bearer_token_dependency",
    "make_health_router",
    "make_jwt_user_dependency",
    "make_permission_dependency",
    "make_role_dependency",
    "make_token_dependency",
    "make_tool_spec_router",
    "register_exception_handlers",
    "require_x_token",
    "run_server",
    "set_cookie",
]
