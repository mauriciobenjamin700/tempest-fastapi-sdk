"""Object-storage primitives — MinIO / S3-compatible.

The MinIO client wraps the official ``minio`` package via
``asyncio.to_thread`` so it slots into the SDK's async-first
conventions without forking the wire protocol. Install with the
``[minio]`` extra.
"""

from tempest_fastapi_sdk.storage.minio_client import (
    AsyncMinIOClient,
    ObjectStat,
)

__all__: list[str] = [
    "AsyncMinIOClient",
    "ObjectStat",
]
