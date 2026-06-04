# Object storage — MinIO / S3

`AsyncMinIOClient` is an async facade over the official `minio` package. It covers what a typical FastAPI service actually needs: buckets (ensure / exists / list / remove), object I/O (put / get / stream / stat / list / remove / copy) and presigned URLs (GET / PUT). Advanced operations (versioning, lifecycle XML, SSE-KMS, multipart fine-tuning) are reachable via the underlying `.client` attribute.

!!! tip "Why the wrapper exists"
    `minio-py` is **synchronous**. Calling `client.put_object(...)` directly inside a FastAPI route blocks the event loop for the whole upload. The wrapper hands every call to `asyncio.to_thread`, so the loop stays responsive while the operation runs in the executor.

## Installation

```bash
pip install "tempest-fastapi-sdk[minio]"
# or:
uv add "tempest-fastapi-sdk[minio]"
```

The `minio` package is lazy-loaded — it only imports when `AsyncMinIOClient` is instantiated. Projects without storage don't need the extra.

## Configuration via settings mixin

```python
from tempest_fastapi_sdk import (
    BaseAppSettings,
    MinIOSettings,
    ServerSettings,
)


class Settings(
    ServerSettings,
    MinIOSettings,
    BaseAppSettings,
):
    """Service settings — inherits MinIO defaults."""
```

`.env`:

```bash
MINIO_ENDPOINT=minio.internal:9000
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...
MINIO_SECURE=true
MINIO_REGION=us-east-1
MINIO_DEFAULT_BUCKET=uploads
```

## Wiring into `create_app()`

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from tempest_fastapi_sdk import AsyncMinIOClient

from src.core.settings import settings


storage = AsyncMinIOClient(
    endpoint=settings.MINIO_ENDPOINT,
    access_key=settings.MINIO_ACCESS_KEY,
    secret_key=settings.MINIO_SECRET_KEY,
    default_bucket=settings.MINIO_DEFAULT_BUCKET,
    secure=settings.MINIO_SECURE,
    region=settings.MINIO_REGION,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Ensure the default bucket exists before serving traffic."""
    await storage.ensure_bucket()
    yield


def create_app() -> FastAPI:
    """Build the configured FastAPI instance."""
    return FastAPI(lifespan=lifespan)
```

## Recipes

### Upload from FastAPI `UploadFile`

```python
from fastapi import APIRouter, UploadFile

from src.api.app import storage

router = APIRouter()


@router.post("/files")
async def upload_file(file: UploadFile) -> dict[str, str]:
    """Persist the received file in the default bucket."""
    body = await file.read()
    etag = await storage.put_object(
        file.filename or "unnamed",
        body,
        content_type=file.content_type or "application/octet-stream",
        metadata={"original-name": file.filename or ""},
    )
    return {"key": file.filename or "unnamed", "etag": etag}
```

### Streaming download

Use for large files — chunk-by-chunk avoids loading everything in memory:

```python
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from src.api.app import storage

router = APIRouter()


@router.get("/files/{key}")
async def download_file(key: str) -> StreamingResponse:
    """Stream the object from the default bucket."""
    stat = await storage.stat_object(key)
    stream = await storage.stream_object(key, chunk_size=64 * 1024)
    return StreamingResponse(
        stream,
        media_type=stat.content_type or "application/octet-stream",
        headers={"content-length": str(stat.size)},
    )
```

### Presigned URL — direct browser upload

Recommended pattern for large files: the client `PUT`s directly to MinIO/S3 and bytes don't pass through FastAPI.

```python
from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel

from src.api.app import storage

router = APIRouter()


class PresignedUploadResponse(BaseModel):
    key: str
    url: str


@router.post("/uploads/presign")
async def presign_upload() -> PresignedUploadResponse:
    """Return a temporary URL the client can PUT to directly."""
    key = f"uploads/{uuid4().hex}"
    url = await storage.presigned_put_url(key, expires=timedelta(minutes=15))
    return PresignedUploadResponse(key=key, url=url)
```

JS client:

```javascript
const { key, url } = await fetch("/uploads/presign", { method: "POST" }).then(r => r.json());
await fetch(url, { method: "PUT", body: file });
```

### Presigned URL — temporary download

To serve private files without routing bytes through the API:

```python
from datetime import timedelta

from fastapi import APIRouter

from src.api.app import storage

router = APIRouter()


@router.get("/files/{key}/url")
async def get_download_url(key: str) -> dict[str, str]:
    """Download URL valid for 1 hour."""
    url = await storage.presigned_get_url(key, expires=timedelta(hours=1))
    return {"url": url}
```

### List objects by prefix

```python
from fastapi import APIRouter

from src.api.app import storage

router = APIRouter()


@router.get("/files")
async def list_files(prefix: str = "") -> list[str]:
    """List keys under ``prefix`` in the default bucket."""
    return await storage.list_objects(prefix)
```

`list_objects` returns `[]` when nothing matches — aligned with the SDK convention ("no match is not an error").

### Copy / move

```python
await storage.copy_object("uploads/draft-1", "uploads/final-1")
await storage.remove_object("uploads/draft-1")
```

## When NOT to use `AsyncMinIOClient`

- When you need operations **outside** the listed surface (SSE-KMS, S3 v2 ACLs, bucket replication). Use `storage.client.<method>` directly — `minio-py` stays accessible.
- For huge resumable uploads (> 5 GiB) — `minio-py` does multipart automatically but doesn't support `tus` or resume. Consider `tus.io` separately.

## What's next

- v0.24.0 will introduce an `S3Backend` for `UploadUtils`, letting the same upload code switch between local disk and MinIO/S3 via a settings flag. See the [Roadmap](../roadmap.md).
