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
from tempest_fastapi_sdk.api.middlewares.quota import (
    MemoryQuotaStore as MemoryQuotaStore,
)
from tempest_fastapi_sdk.api.middlewares.quota import (
    PlanRateLimitPolicy as PlanRateLimitPolicy,
)
from tempest_fastapi_sdk.api.middlewares.quota import (
    QuotaResult as QuotaResult,
)
from tempest_fastapi_sdk.api.middlewares.quota import (
    QuotaStore as QuotaStore,
)
from tempest_fastapi_sdk.api.middlewares.quota import (
    RateLimitPolicy as RateLimitPolicy,
)
from tempest_fastapi_sdk.api.middlewares.quota import (
    RateLimitRule as RateLimitRule,
)
from tempest_fastapi_sdk.api.middlewares.quota import (
    RedisQuotaStore as RedisQuotaStore,
)
from tempest_fastapi_sdk.api.middlewares.quota import (
    StaticRateLimitPolicy as StaticRateLimitPolicy,
)
from tempest_fastapi_sdk.api.middlewares.quota import (
    key_by_plan_principal as key_by_plan_principal,
)
from tempest_fastapi_sdk.api.middlewares.quota import (
    plan_by_header as plan_by_header,
)
from tempest_fastapi_sdk.api.middlewares.quota import (
    plan_by_jwt_claim as plan_by_jwt_claim,
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
from tempest_fastapi_sdk.api.middlewares.response_cache import (
    MemoryResponseCacheStore,
    RedisResponseCacheStore,
    ResponseCacheMiddleware,
    ResponseCacheStore,
)

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
    "MemoryQuotaStore",
    "MemoryRateLimitStore",
    "MemoryResponseCacheStore",
    "PlanRateLimitPolicy",
    "QuotaResult",
    "QuotaStore",
    "RateLimitMiddleware",
    "RateLimitPolicy",
    "RateLimitResult",
    "RateLimitRule",
    "RateLimitStore",
    "RedisIdempotencyStore",
    "RedisQuotaStore",
    "RedisRateLimitStore",
    "RedisResponseCacheStore",
    "RequestIDMiddleware",
    "ResponseCacheMiddleware",
    "ResponseCacheStore",
    "StaticRateLimitPolicy",
    "apply_cors",
    "generate_csrf_token",
    "key_by_header",
    "key_by_ip",
    "key_by_jwt_claim",
    "key_by_jwt_subject",
    "key_by_plan_principal",
    "make_csrf_token_dependency",
    "plan_by_header",
    "plan_by_jwt_claim",
]
