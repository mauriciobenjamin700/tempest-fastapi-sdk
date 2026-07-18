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

from src.api.app import storage

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

### Batch operations — presign / upload / download *(v0.133.0+)*

**List** endpoints usually resolve **one key per row** — a page of profiles, each with its picture. Doing that in a `for` loop with `await presigned_get_url(...)` **serializes** the N thread hops (every `minio` call runs in `asyncio.to_thread`). The three batch methods fan the work out at once, under a concurrency ceiling:

- `presigned_get_urls(keys)` → `dict[str, str]` (key → URL)
- `put_objects(items)` → `dict[str, str]` (key → ETag)
- `get_objects_bytes(keys)` → `dict[str, bytes]` (key → payload)

```python
from fastapi import APIRouter

from src.api.app import storage

router = APIRouter()


@router.post("/files/urls")
async def sign_many(keys: list[str]) -> dict[str, str]:
    """Sign a page of keys in one call instead of one per request."""
    return await storage.presigned_get_urls(keys)
```

Duplicate keys are **collapsed** (each object is signed/fetched once), and the result is a `dict` — look each row up with `result.get(row.key)`.

!!! tip "In the service: `file_urls` for pages"
    If your service uses `StoredFileServiceMixin`, prefer `file_urls([...])` — the batch counterpart of `file_url`. It **drops `None`/empty keys** and returns a `dict`, ideal for building a page of responses:

    ```python
    users = [...]  # rows on the page
    urls = await user_service.file_urls([u.profile_picture for u in users])
    for user in users:
        user.profile_picture_url = urls.get(user.profile_picture)  # None if empty
    ```

!!! note "Fail-fast semantics"
    All three methods are **fail-fast**: the first failure aborts the batch and propagates (default `asyncio.gather`) — the same behavior as running the operations one by one. Need to tolerate partial failure? Run the items individually and handle each exception.

!!! info "Concurrency ceiling (`max_concurrency`, default 16)"
    Each operation is dispatched to a default-executor thread. Scheduling thousands at once saturates the pool and spikes memory. An `asyncio.Semaphore` bounds how many run at the same time while preserving order. Tune it via `max_concurrency=` (minimum 1; `0` or negative raises `ValueError`).

Batch upload uses `PutObjectItem`, which mirrors the per-object arguments of `put_object` (content type, metadata, length for streams):

```python
from tempest_fastapi_sdk import PutObjectItem

etags = await storage.put_objects(
    [
        PutObjectItem(key="thumbs/a.jpg", data=thumb_a, content_type="image/jpeg"),
        PutObjectItem(key="thumbs/b.jpg", data=thumb_b, content_type="image/jpeg"),
    ]
)
```

!!! warning "`get_objects_bytes` loads everything in memory"
    Like `get_object_bytes`, the batch is for **small** objects — each payload becomes `bytes` in RAM. For large files, stream them individually with `stream_object`.

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
