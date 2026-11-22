"""Server side of tus 1.0.0 resumable uploads.

:func:`make_tus_router` mounts the routes ``tempest-react-sdk``'s
``createResumableUpload`` talks to; :class:`LocalTusStore` keeps uploads on
the server's disk and :class:`S3TusStore` in an S3-compatible bucket. Both
persist every upload's state, so an interrupted upload resumes across a
restart of the process.

Needs no extra beyond the base install; :class:`S3TusStore` over
:class:`~tempest_fastapi_sdk.storage.AsyncMinIOClient` needs ``[minio]``.
"""

from tempest_fastapi_sdk.storage.tus.models import (
    TUS_UPLOAD_ID_PATTERN as TUS_UPLOAD_ID_PATTERN,
)
from tempest_fastapi_sdk.storage.tus.models import TUS_VERSION as TUS_VERSION
from tempest_fastapi_sdk.storage.tus.models import TusUpload as TusUpload
from tempest_fastapi_sdk.storage.tus.models import (
    encode_upload_metadata as encode_upload_metadata,
)
from tempest_fastapi_sdk.storage.tus.models import http_date as http_date
from tempest_fastapi_sdk.storage.tus.models import (
    parse_upload_metadata as parse_upload_metadata,
)
from tempest_fastapi_sdk.storage.tus.models import (
    sanitize_upload_filename as sanitize_upload_filename,
)
from tempest_fastapi_sdk.storage.tus.router import (
    DEFAULT_TUS_BODY_READ_TIMEOUT as DEFAULT_TUS_BODY_READ_TIMEOUT,
)
from tempest_fastapi_sdk.storage.tus.router import (
    DEFAULT_TUS_EXPIRATION as DEFAULT_TUS_EXPIRATION,
)
from tempest_fastapi_sdk.storage.tus.router import (
    DEFAULT_TUS_MAX_CHUNK_SIZE as DEFAULT_TUS_MAX_CHUNK_SIZE,
)
from tempest_fastapi_sdk.storage.tus.router import (
    DEFAULT_TUS_MAX_SIZE as DEFAULT_TUS_MAX_SIZE,
)
from tempest_fastapi_sdk.storage.tus.router import (
    TUS_CHUNK_CONTENT_TYPE as TUS_CHUNK_CONTENT_TYPE,
)
from tempest_fastapi_sdk.storage.tus.router import (
    TUS_CHUNK_SPOOL_BYTES as TUS_CHUNK_SPOOL_BYTES,
)
from tempest_fastapi_sdk.storage.tus.router import (
    TUS_CORS_ALLOW_HEADERS as TUS_CORS_ALLOW_HEADERS,
)
from tempest_fastapi_sdk.storage.tus.router import (
    TUS_CORS_EXPOSE_HEADERS as TUS_CORS_EXPOSE_HEADERS,
)
from tempest_fastapi_sdk.storage.tus.router import (
    TUS_MAX_INTEGER_HEADER_DIGITS as TUS_MAX_INTEGER_HEADER_DIGITS,
)
from tempest_fastapi_sdk.storage.tus.router import (
    TusCompleteCallback as TusCompleteCallback,
)
from tempest_fastapi_sdk.storage.tus.router import make_tus_router as make_tus_router
from tempest_fastapi_sdk.storage.tus.s3_store import (
    DEFAULT_S3_TUS_MIN_CHUNK_SIZE as DEFAULT_S3_TUS_MIN_CHUNK_SIZE,
)
from tempest_fastapi_sdk.storage.tus.s3_store import (
    TUS_ASSEMBLY_SPOOL_BYTES as TUS_ASSEMBLY_SPOOL_BYTES,
)
from tempest_fastapi_sdk.storage.tus.s3_store import S3TusStore as S3TusStore
from tempest_fastapi_sdk.storage.tus.s3_store import TusObjectClient as TusObjectClient
from tempest_fastapi_sdk.storage.tus.store import (
    TUS_STREAM_CHUNK_SIZE as TUS_STREAM_CHUNK_SIZE,
)
from tempest_fastapi_sdk.storage.tus.store import LocalTusStore as LocalTusStore
from tempest_fastapi_sdk.storage.tus.store import TusStore as TusStore
from tempest_fastapi_sdk.storage.tus.store import valid_upload_id as valid_upload_id

__all__: list[str] = [
    "DEFAULT_S3_TUS_MIN_CHUNK_SIZE",
    "DEFAULT_TUS_BODY_READ_TIMEOUT",
    "DEFAULT_TUS_EXPIRATION",
    "DEFAULT_TUS_MAX_CHUNK_SIZE",
    "DEFAULT_TUS_MAX_SIZE",
    "TUS_ASSEMBLY_SPOOL_BYTES",
    "TUS_CHUNK_CONTENT_TYPE",
    "TUS_CHUNK_SPOOL_BYTES",
    "TUS_CORS_ALLOW_HEADERS",
    "TUS_CORS_EXPOSE_HEADERS",
    "TUS_MAX_INTEGER_HEADER_DIGITS",
    "TUS_STREAM_CHUNK_SIZE",
    "TUS_UPLOAD_ID_PATTERN",
    "TUS_VERSION",
    "LocalTusStore",
    "S3TusStore",
    "TusCompleteCallback",
    "TusObjectClient",
    "TusStore",
    "TusUpload",
    "encode_upload_metadata",
    "http_date",
    "make_tus_router",
    "parse_upload_metadata",
    "sanitize_upload_filename",
    "valid_upload_id",
]
