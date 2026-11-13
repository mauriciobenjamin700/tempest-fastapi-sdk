"""Object-storage primitives — MinIO / S3-compatible, or the local disk.

The MinIO client wraps the official ``minio`` package via
``asyncio.to_thread`` so it slots into the SDK's async-first
conventions without forking the wire protocol. Install with the
``[minio]`` extra.

:class:`LocalObjectStorageClient` answers the same calls from a directory
of the server and signs links to the app's own routes, which
:func:`make_local_storage_router` serves. It needs no extra, except
``[storage-encryption]`` to encrypt the files at rest (``encryption_keys``).

:class:`BucketRoutedMinIOClient` is the S3 client for a store that binds each
access key to one bucket: every call is signed with the credential of the
bucket it names.
"""

from tempest_fastapi_sdk.settings.storage_credential import (
    StorageBucketCredential as StorageBucketCredential,
)
from tempest_fastapi_sdk.storage._download import (
    DownloadableStore as DownloadableStore,
)
from tempest_fastapi_sdk.storage._download import (
    object_download_response as object_download_response,
)
from tempest_fastapi_sdk.storage.bucket_router import (
    BucketRoutedMinIOClient as BucketRoutedMinIOClient,
)
from tempest_fastapi_sdk.storage.local_client import (
    DEFAULT_LOCAL_MAX_UPLOAD_BYTES as DEFAULT_LOCAL_MAX_UPLOAD_BYTES,
)
from tempest_fastapi_sdk.storage.local_client import (
    DEFAULT_LOCAL_STORAGE_ROUTE_PREFIX as DEFAULT_LOCAL_STORAGE_ROUTE_PREFIX,
)
from tempest_fastapi_sdk.storage.local_client import (
    LOCAL_META_SUFFIX as LOCAL_META_SUFFIX,
)
from tempest_fastapi_sdk.storage.local_client import (
    LOCAL_PRESIGN_MAX_EXPIRES as LOCAL_PRESIGN_MAX_EXPIRES,
)
from tempest_fastapi_sdk.storage.local_client import (
    LOCAL_STORAGE_CHUNK_SIZE as LOCAL_STORAGE_CHUNK_SIZE,
)
from tempest_fastapi_sdk.storage.local_client import (
    LOCAL_STORAGE_GET_PURPOSE as LOCAL_STORAGE_GET_PURPOSE,
)
from tempest_fastapi_sdk.storage.local_client import (
    LOCAL_STORAGE_POST_PURPOSE as LOCAL_STORAGE_POST_PURPOSE,
)
from tempest_fastapi_sdk.storage.local_client import (
    LOCAL_STORAGE_PUT_PURPOSE as LOCAL_STORAGE_PUT_PURPOSE,
)
from tempest_fastapi_sdk.storage.local_client import (
    LocalObjectNotFoundError as LocalObjectNotFoundError,
)
from tempest_fastapi_sdk.storage.local_client import (
    LocalObjectStorageClient as LocalObjectStorageClient,
)
from tempest_fastapi_sdk.storage.local_crypto import (
    LOCAL_ENCRYPTION_BLOCK_SIZE as LOCAL_ENCRYPTION_BLOCK_SIZE,
)
from tempest_fastapi_sdk.storage.local_crypto import (
    LOCAL_ENCRYPTION_FORMAT_VERSION as LOCAL_ENCRYPTION_FORMAT_VERSION,
)
from tempest_fastapi_sdk.storage.local_crypto import (
    LOCAL_ENCRYPTION_KEY_BYTES as LOCAL_ENCRYPTION_KEY_BYTES,
)
from tempest_fastapi_sdk.storage.local_crypto import (
    LOCAL_ENCRYPTION_MAGIC as LOCAL_ENCRYPTION_MAGIC,
)
from tempest_fastapi_sdk.storage.local_crypto import (
    LocalObjectCipher as LocalObjectCipher,
)
from tempest_fastapi_sdk.storage.local_router import (
    make_local_storage_router as make_local_storage_router,
)
from tempest_fastapi_sdk.storage.minio_client import (
    AsyncMinIOClient as AsyncMinIOClient,
)
from tempest_fastapi_sdk.storage.minio_client import (
    ObjectDeleteError as ObjectDeleteError,
)
from tempest_fastapi_sdk.storage.minio_client import (
    ObjectStat as ObjectStat,
)
from tempest_fastapi_sdk.storage.minio_client import (
    PutObjectItem as PutObjectItem,
)
from tempest_fastapi_sdk.storage.presign import (
    POST_FILE_FIELD as POST_FILE_FIELD,
)
from tempest_fastapi_sdk.storage.presign import (
    PresignedPost as PresignedPost,
)

__all__: list[str] = [
    "DEFAULT_LOCAL_MAX_UPLOAD_BYTES",
    "DEFAULT_LOCAL_STORAGE_ROUTE_PREFIX",
    "LOCAL_ENCRYPTION_BLOCK_SIZE",
    "LOCAL_ENCRYPTION_FORMAT_VERSION",
    "LOCAL_ENCRYPTION_KEY_BYTES",
    "LOCAL_ENCRYPTION_MAGIC",
    "LOCAL_META_SUFFIX",
    "LOCAL_PRESIGN_MAX_EXPIRES",
    "LOCAL_STORAGE_CHUNK_SIZE",
    "LOCAL_STORAGE_GET_PURPOSE",
    "LOCAL_STORAGE_POST_PURPOSE",
    "LOCAL_STORAGE_PUT_PURPOSE",
    "POST_FILE_FIELD",
    "AsyncMinIOClient",
    "BucketRoutedMinIOClient",
    "DownloadableStore",
    "LocalObjectCipher",
    "LocalObjectNotFoundError",
    "LocalObjectStorageClient",
    "ObjectDeleteError",
    "ObjectStat",
    "PresignedPost",
    "PutObjectItem",
    "StorageBucketCredential",
    "make_local_storage_router",
    "object_download_response",
]
