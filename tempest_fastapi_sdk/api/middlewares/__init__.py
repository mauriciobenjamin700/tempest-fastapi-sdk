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
from tempest_fastapi_sdk.api.middlewares.rate_limit import (
    MemoryRateLimitStore,
    RateLimitMiddleware,
    RateLimitResult,
    RateLimitStore,
    RedisRateLimitStore,
    key_by_header,
    key_by_ip,
    key_by_jwt_claim,
    key_by_jwt_subject,
)
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
    "MemoryRateLimitStore",
    "RateLimitMiddleware",
    "RateLimitResult",
    "RateLimitStore",
    "RedisIdempotencyStore",
    "RedisRateLimitStore",
    "RequestIDMiddleware",
    "apply_cors",
    "generate_csrf_token",
    "key_by_header",
    "key_by_ip",
    "key_by_jwt_claim",
    "key_by_jwt_subject",
    "make_csrf_token_dependency",
]
