"""Reusable Starlette middlewares for FastAPI services."""

from tempest_fastapi_sdk.api.middlewares.body_size import BodySizeLimitMiddleware
from tempest_fastapi_sdk.api.middlewares.cors import apply_cors
from tempest_fastapi_sdk.api.middlewares.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    CSRFMiddleware,
    generate_csrf_token,
    make_csrf_token_dependency,
)
from tempest_fastapi_sdk.api.middlewares.graceful import GracefulShutdownMiddleware
from tempest_fastapi_sdk.api.middlewares.idempotency import (
    IDEMPOTENCY_HEADER,
    CachedResponse,
    IdempotencyMiddleware,
    IdempotencyStore,
    MemoryIdempotencyStore,
    RedisIdempotencyStore,
)
from tempest_fastapi_sdk.api.middlewares.rate_limit import RateLimitMiddleware
from tempest_fastapi_sdk.api.middlewares.request_id import RequestIDMiddleware

__all__: list[str] = [
    "CSRF_COOKIE_NAME",
    "CSRF_HEADER_NAME",
    "IDEMPOTENCY_HEADER",
    "BodySizeLimitMiddleware",
    "CSRFMiddleware",
    "CachedResponse",
    "GracefulShutdownMiddleware",
    "IdempotencyMiddleware",
    "IdempotencyStore",
    "MemoryIdempotencyStore",
    "RateLimitMiddleware",
    "RedisIdempotencyStore",
    "RequestIDMiddleware",
    "apply_cors",
    "generate_csrf_token",
    "make_csrf_token_dependency",
]
