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


# settings.minio_kwargs() maps MINIO_* -> endpoint/access_key/secret_key/
# default_bucket/secure/region, so you don't repeat each field.
storage = AsyncMinIOClient(**settings.minio_kwargs())


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

!!! tip "Shortcut: `download_response` (or `DownloadUtils`)"
    `AsyncMinIOClient.download_response(key, ...)` already does stat + stream
    + Content-Disposition/Type/Length in one call — and
    [`DownloadUtils(minio)`](downloads.md) wraps it. The manual example below
    is just to show the moving parts.

Use for large files — chunk-by-chunk avoids loading everything in memory:

```python
from fastapi import APIRouter
from starlette.responses import Response

from src.api.dependencies.resources import storage

router = APIRouter()


@router.get("/files/{key}")
async def download_file(key: str) -> Response:
    """Stream the object from the default bucket (one call)."""
    return await storage.download_response(key)
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

### Separate public endpoint for presigned URLs *(v0.88.0+)*

Common production shape: the backend talks to MinIO over a **fast private network** (`servus-storage:9000`, no TLS), but the **browser** can't reach that host — it needs a **public HTTPS** host. If you sign the presigned URL with the internal endpoint, the link carries `servus-storage:9000` and the browser can't open it.

Fix: `MINIO_PUBLIC_ENDPOINT`. Presigned URLs (`presigned_get_url` / `presigned_put_url`) are then **signed against the public host**, while **every server→MinIO operation keeps using the internal endpoint**.

```bash
# .env
MINIO_ENDPOINT=servus-storage:9000            # internal Docker network (ops)
MINIO_SECURE=false
MINIO_PUBLIC_ENDPOINT=https://storage.example.com   # browser (presigned)
# MINIO_PUBLIC_SECURE=true                     # optional; https:// already implies it
```

!!! info "Why two clients, not a host replace"
    A presigned URL is SigV4-signed including the `Host` header. Rewriting the host **after** signing invalidates the signature. So the SDK keeps a second `minio.Minio` (same credentials) whose only job is to **sign** against the public host — the internal `AsyncMinIOClient.client` still does put/get/stat/ensure_bucket over the private network.

!!! tip "Without `MINIO_PUBLIC_ENDPOINT`"
    Unchanged behaviour: presigned URLs are signed with `MINIO_ENDPOINT` (single-endpoint mode). The split is fully opt-in.

The public host's proxy must route to the **MinIO S3 API (port 9000)** over TLS and forward the correct `Host` (the signature validates it).

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

- The pluggable upload backend `MinIOUploadStorage` shipped in v0.24.0 — for the upload pipeline that switches between local disk and MinIO/S3 via a settings flag, see the [uploads recipe](uploads.en.md).
