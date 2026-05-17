"""Redis-backed cache primitives.

Imports the optional ``redis`` package lazily so the rest of the SDK
remains importable when the extra is not installed.
"""

from tempest_fastapi_sdk.cache.decorator import cached
from tempest_fastapi_sdk.cache.redis_manager import AsyncRedisManager

__all__: list[str] = [
    "AsyncRedisManager",
    "cached",
]
