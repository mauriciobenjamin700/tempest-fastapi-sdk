"""Reusable Starlette middlewares for FastAPI services."""

from tempest_fastapi_sdk.api.middlewares.body_size import (
    BodySizeLimitMiddleware as BodySizeLimitMiddleware,
)
from tempest_fastapi_sdk.api.middlewares.cors import apply_cors as apply_cors
from tempest_fastapi_sdk.api.middlewares.csrf import (
    CSRF_COOKIE_NAME as CSRF_COOKIE_NAME,
)
from tempest_fastapi_sdk.api.middlewares.csrf import (
    CSRF_HEADER_NAME as CSRF_HEADER_NAME,
)
from tempest_fastapi_sdk.api.middlewares.csrf import (
    CSRFMiddleware as CSRFMiddleware,
)
from tempest_fastapi_sdk.api.middlewares.csrf import (
    generate_csrf_token as generate_csrf_token,
)
from tempest_fastapi_sdk.api.middlewares.csrf import (
    make_csrf_token_dependency as make_csrf_token_dependency,
)
from tempest_fastapi_sdk.api.middlewares.graceful import (
    GracefulShutdownMiddleware as GracefulShutdownMiddleware,
)
from tempest_fastapi_sdk.api.middlewares.idempotency import (
    IDEMPOTENCY_HEADER as IDEMPOTENCY_HEADER,
)
from tempest_fastapi_sdk.api.middlewares.idempotency import (
    CachedResponse as CachedResponse,
)
from tempest_fastapi_sdk.api.middlewares.idempotency import (
    IdempotencyMiddleware as IdempotencyMiddleware,
)
from tempest_fastapi_sdk.api.middlewares.idempotency import (
    IdempotencyStore as IdempotencyStore,
)
from tempest_fastapi_sdk.api.middlewares.idempotency import (
    MemoryIdempotencyStore as MemoryIdempotencyStore,
)
from tempest_fastapi_sdk.api.middlewares.idempotency import (
    RedisIdempotencyStore as RedisIdempotencyStore,
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
    MemoryRateLimitStore as MemoryRateLimitStore,
)
from tempest_fastapi_sdk.api.middlewares.rate_limit import (
    RateLimitMiddleware as RateLimitMiddleware,
)
from tempest_fastapi_sdk.api.middlewares.rate_limit import (
    RateLimitResult as RateLimitResult,
)
from tempest_fastapi_sdk.api.middlewares.rate_limit import (
    RateLimitStore as RateLimitStore,
)
from tempest_fastapi_sdk.api.middlewares.rate_limit import (
    RedisRateLimitStore as RedisRateLimitStore,
)
from tempest_fastapi_sdk.api.middlewares.rate_limit import (
    key_by_header as key_by_header,
)
from tempest_fastapi_sdk.api.middlewares.rate_limit import (
    key_by_ip as key_by_ip,
)
from tempest_fastapi_sdk.api.middlewares.rate_limit import (
    key_by_jwt_claim as key_by_jwt_claim,
)
from tempest_fastapi_sdk.api.middlewares.rate_limit import (
    key_by_jwt_subject as key_by_jwt_subject,
)
from tempest_fastapi_sdk.api.middlewares.request_id import (
    RequestIDMiddleware as RequestIDMiddleware,
)
from tempest_fastapi_sdk.api.middlewares.response_cache import (
    MemoryResponseCacheStore as MemoryResponseCacheStore,
)
from tempest_fastapi_sdk.api.middlewares.response_cache import (
    RedisResponseCacheStore as RedisResponseCacheStore,
)
from tempest_fastapi_sdk.api.middlewares.response_cache import (
    ResponseCacheMiddleware as ResponseCacheMiddleware,
)
from tempest_fastapi_sdk.api.middlewares.response_cache import (
    ResponseCacheStore as ResponseCacheStore,
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
