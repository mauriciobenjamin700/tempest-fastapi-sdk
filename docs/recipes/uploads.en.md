# Uploads — local disk + S3 / MinIO

Since v0.24.0 `UploadUtils` accepts a **pluggable storage backend**: keep the same upload code and switch between local disk and MinIO/S3 via a settings flag.

!!! tip "Validation stays in `UploadUtils`"
    Size, extension, MIME, magic bytes and `content_validator` are still validated by `UploadUtils` before any byte hits the backend — backends only see validated data.

## Available backends

| Backend | When to use | Required extra |
|---------|-------------|----------------|
| `LocalUploadStorage` | Dev / single-replica / local FS | `[upload]` |
| `MinIOUploadStorage` | Multi-replica, S3/MinIO/R2/B2/Spaces | `[upload]` + `[minio]` |

Both implement the `UploadStorage` protocol (`write_stream`, `delete`, `exists`, `presigned_url`).

## Default: local disk (backwards-compat)

No change required — `UploadUtils(upload_dir)` keeps writing to disk:

```python
from pathlib import Path

from fastapi import APIRouter, UploadFile
from tempest_fastapi_sdk import UploadUtils

router = APIRouter()
utils = UploadUtils(Path("./uploads"), max_size_bytes=10 * 1024 * 1024)


@router.post("/files")
async def upload(file: UploadFile) -> dict[str, str]:
    """Persist the file to disk and return the absolute path."""
    path = await utils.save(file)
    return {"path": str(path)}
```

## Switching to MinIO/S3

Reuse the same `AsyncMinIOClient` the app already owns:

```python
from fastapi import APIRouter, UploadFile
from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    MinIOUploadStorage,
    UploadUtils,
)

from src.core.settings import settings


client = AsyncMinIOClient(
    endpoint=settings.MINIO_ENDPOINT,
    access_key=settings.MINIO_ACCESS_KEY,
    secret_key=settings.MINIO_SECRET_KEY,
    default_bucket=settings.MINIO_DEFAULT_BUCKET,
)
remote = MinIOUploadStorage(client)
utils = UploadUtils(
    "./tmp",  # still required but unused when storage= is passed
    max_size_bytes=10 * 1024 * 1024,
)

router = APIRouter()


@router.post("/files")
async def upload(file: UploadFile) -> dict[str, str]:
    """Validate then push straight to MinIO."""
    key = await utils.save(file, storage=remote, filename=file.filename)
    return {"key": str(key)}
```

## Flag-driven backend selection

```python
from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    LocalUploadStorage,
    MinIOUploadStorage,
    UploadStorage,
    UploadUtils,
)

from src.core.settings import settings


def make_storage() -> UploadStorage:
    """Pick the backend based on environment configuration."""
    if settings.UPLOAD_BACKEND == "minio":
        client = AsyncMinIOClient(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            default_bucket=settings.MINIO_DEFAULT_BUCKET,
        )
        return MinIOUploadStorage(client)
    return LocalUploadStorage(settings.UPLOAD_DIR)


storage = make_storage()
utils = UploadUtils(settings.UPLOAD_DIR)
```

## Common operations

```python
# Save
await utils.save(file, storage=storage, filename="logo.png")

# Delete
await storage.delete("logo.png")

# Probe
exists = await storage.exists("logo.png")

# Temporary URL (S3 returns a URL; local returns None)
from datetime import timedelta
url = await storage.presigned_url("logo.png", expires=timedelta(hours=1))
```

## When to use presigned PUT directly

For files > 50 MB, skip the in-memory buffer — have the client `PUT` straight to MinIO via a presigned URL. See [Storage MinIO/S3](storage.md#presigned-url--direct-browser-upload).
