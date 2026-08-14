"""Redis-backed cache primitives.

Imports the optional ``redis`` package lazily so the rest of the SDK
remains importable when the extra is not installed.
"""

from tempest_fastapi_sdk.cache.decorator import cached as cached
from tempest_fastapi_sdk.cache.invalidation import (
    CacheInvalidator as CacheInvalidator,
)
from tempest_fastapi_sdk.cache.invalidation import (
    namespace_registry_key as namespace_registry_key,
)
from tempest_fastapi_sdk.cache.invalidation import (
    tag_registry_key as tag_registry_key,
)
from tempest_fastapi_sdk.cache.redis_manager import (
    AsyncRedisManager as AsyncRedisManager,
)

__all__: list[str] = [
    "AsyncRedisManager",
    "CacheInvalidator",
    "cached",
    "namespace_registry_key",
    "tag_registry_key",
]
