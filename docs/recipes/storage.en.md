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
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from tempest_fastapi_sdk import AsyncMinIOClient

from src.core.settings import settings


# settings.minio_kwargs() maps MINIO_* -> endpoint/access_key/secret_key/
# default_bucket/secure/region, so you don't repeat each field.
storage = AsyncMinIOClient(**settings.minio_kwargs())


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
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

When the download has to go through the backend — an authorised file, or a
bucket the browser cannot reach — `download_response` does stat + stream in
one call, chunk by chunk, without loading the object into memory.
[`DownloadUtils(minio)`](downloads.md) wraps the same call.

Pass the `request`. It is what lets a `<video>` seek, a download resume and
an image be revalidated instead of downloaded again:

```python
from fastapi import APIRouter, Request
from starlette.responses import Response

from src.api.app import storage

router = APIRouter()


@router.get("/files/{key}")
async def download_file(key: str, request: Request) -> Response:
    """Stream the object from the default bucket, with Range and revalidation."""
    return await storage.download_response(
        key,
        request=request,
        as_attachment=False,
        cache_control="private, max-age=3600",
    )
```

Every response carries `Accept-Ranges: bytes`, the object's `ETag` (quoted)
and `Last-Modified`. With the `request` in hand, the route answers like this:

| The client sends | Response |
| --- | --- |
| nothing special | `200`, whole object |
| `Range: bytes=1000-1999`, `bytes=1000-` or `bytes=-300` | `206` with `Content-Range`, reading **only** that slice from the bucket |
| a `Range` starting at or past the end of the object | `416` with `Content-Range: bytes */<size>` |
| `Range: bytes=0-1,5-6` (several ranges) | `200`, whole object |
| `If-None-Match` with the current `ETag` | `304`, no body, object not read |
| `If-Modified-Since` not older than the object (no `If-None-Match`) | `304` |
| `Range` + an `If-Range` that no longer matches the object | `200`, whole object |

Each row of the table is a test against a real MinIO in a container
(`tests/storage/test_download_live.py`, `make test-docker`). The `206` passes
`offset`/`length` to `minio-py`'s `get_object`, which sends the `Range` to the
bucket — the test spies on that call and checks only the slice was asked for.

!!! info "Why several ranges become `200`"
    RFC 9110 allows ignoring `Range` and answering with the whole object. The
    alternative, a `multipart/byteranges` body, is not what `<video>`,
    `<audio>` or download managers ask for — they send a single range.

!!! tip "`If-Range` protects the resume"
    A resuming download manager sends `If-Range` with the `ETag` it had. If
    the file changed in between, the SDK ignores `Range` and sends the whole
    new file, instead of stitching bytes of two versions together.

The browser side needs nothing but the tag:

```html
<video src="/files/lesson-01.mp4" controls preload="metadata"></video>
```

Measured in Chromium (Playwright) with a 60 s, 30 033 093-byte MP4 served by
the route above: the player opened with `Range: bytes=0-` (`206`) and, when
seeking to 50 s, asked for `bytes=24969216-30033092`, answered with `206`; the
video settled at `currentTime == 50`, with no media error.

!!! warning "Without `request`, it is the old behaviour"
    `download_response(key)` without `request` reads no request header and
    always answers `200` with the whole object — only the
    `Accept-Ranges`/`ETag`/`Last-Modified` headers are new. In that mode a
    `<video>` cannot seek.

Only need the bytes, not a response? `stream_object(key, offset=, length=)`
returns the iterator over that slice.

### Delivery through nginx — `X-Accel-Redirect`

With `download_response`, every byte goes through the Python process. It
works, but for video and high traffic you want a different split: **the
backend only authorises, and nginx delivers**.

The route checks the permission and answers an **empty** response with one
header:

```text
X-Accel-Redirect: /_bucket/media/lesson.mp4?X-Amz-Algorithm=...&X-Amz-Signature=...
```

nginx intercepts that header, follows it into an internal `location`, fetches
the file from the bucket over the private network and streams it to the
client. The backend carries no bytes, the bucket stays private and the client
only ever sees the backend's domain.

#### When to pick each mode

| | Proxy (`download_response`) | Redirect (`accel_redirect_response`) |
| --- | --- | --- |
| Who moves the bytes | the Python process | nginx |
| Needs nginx in front | no | yes, with the internal `location` below |
| `206` / `304` | answered by the SDK | answered by the bucket, behind nginx |
| Good for | small files, local dev, deploys without nginx | video, large files, high traffic |

#### Switch it by configuration

Both modes come out of **the same route**. `serve_object` picks according to
what the client got in its constructor, and `MinIOSettings` maps two new
variables:

```bash
# .env
MINIO_ENDPOINT=bucket:9000          # INTERNAL endpoint: the SDK signs against it
STORAGE_ACCEL_REDIRECT=true         # false (default) = proxy through the app
STORAGE_ACCEL_PREFIX=/_bucket/      # nginx's internal location
```

```python
from fastapi import APIRouter, Request
from starlette.responses import Response
from tempest_fastapi_sdk import AsyncMinIOClient

from src.core.settings import settings

router = APIRouter()
storage = AsyncMinIOClient(**settings.minio_kwargs())


@router.get("/files/{key:path}")
async def download_file(key: str, request: Request) -> Response:
    """Authorise and deliver — through the app or through nginx, per the .env."""
    return await storage.serve_object(key, request=request, as_attachment=False)
```

With `STORAGE_ACCEL_REDIRECT=false`, it is the previous section's
`download_response`. With `true`, it is `accel_redirect_response`, and the
`request` is not used: nginx forwards the client's `Range` and
`If-None-Match` to the bucket by itself.

!!! info "`accel_redirect_response` directly"
    Want the redirect on a single route, regardless of the flag? Call
    `storage.accel_redirect_response(key, internal_prefix="/_bucket/",
    expires=timedelta(minutes=5), filename=..., media_type=...,
    as_attachment=False, cache_control=...)`. It **never** uses
    `MINIO_PUBLIC_ENDPOINT`: the URL is signed against `MINIO_ENDPOINT`, the
    host nginx is going to call.

#### The nginx block

```nginx
upstream app {
    server app:8000;
}

server {
    listen 80;
    server_name example.com;

    location / {
        proxy_pass http://app;
        proxy_set_header Host $host;
    }

    location /_bucket/ {
        internal;
        proxy_pass http://bucket:9000/;
        proxy_set_header Host bucket:9000;
    }
}
```

Three lines do the work of `location /_bucket/`, and each has a reason:

- **`internal;`** — only nginx gets in here, following an
  `X-Accel-Redirect`. A client asking for `/_bucket/...` directly gets `404`.
- **`proxy_pass http://bucket:9000/;` with the trailing slash** — with the
  slash, nginx replaces the `/_bucket/` prefix with `/`, and the bucket
  receives `/media/lesson.mp4?X-Amz-...`, exactly the path that was signed.
  Without it, the prefix goes along, MinIO reads `_bucket` as the bucket name
  and answers `400 InvalidBucketName`.
- **`proxy_set_header Host bucket:9000;`** — the SigV4 signature covers
  `Host`. It has to be the `MINIO_ENDPOINT` that signed the URL. The danger is
  inheritance: a `proxy_set_header` defined at `server` level (the
  `Host $host` nearly every config has for the app) is inherited by any
  `location` that defines none. The bucket then receives
  `Host: example.com` and answers `403 SignatureDoesNotMatch` — the symptom is
  "the file does not load".

!!! warning "`Content-Type` and `Content-Disposition` travel inside the URL"
    The SDK does not put those headers on the empty response: it signs them
    into the URL as the S3 `response-content-type`,
    `response-content-disposition` and `response-cache-control` overrides, and
    the bucket returns them along with the bytes. Measured on nginx 1.27.5:
    the bucket's `Content-Type` wins over the one the app sets, and a
    `Content-Disposition` set on both reaches the client twice.

!!! danger "Without nginx in front, the flag leaks the signed URL"
    With `STORAGE_ACCEL_REDIRECT=true` and no nginx intercepting, the client
    gets an empty `200` carrying the `X-Accel-Redirect` — path and signature
    included. The URL points at the internal host and is valid for `expires`
    (5 minutes by default). Only turn the flag on where the block above is
    deployed.

#### What was measured

`tests/storage/test_accel_redirect_live.py` (`make test-docker`) starts
MinIO and nginx in containers and the app under uvicorn, and downloads
through the route. The same file passed in full on nginx 1.22.1, 1.27.5 and
1.29.8:

| Request | Result |
| --- | --- |
| `GET /files/clip.mp4` | `200`, the object's bytes, the `Content-Type` stored in the bucket |
| `GET /files/clip.mp4` with `Range: bytes=100-199` | `206`, `Content-Range: bytes 100-199/1048576` |
| `GET /files/clip.mp4` with `If-None-Match: <etag>` | `304` |
| key with a space, an accent and `+` | `200` |
| `GET /_bucket/media/clip.mp4` straight from the client | `404` |
| a `location` inheriting `Host $host` | `403 SignatureDoesNotMatch` |
| `proxy_pass` without the trailing slash | `400 InvalidBucketName` |
| the same route with `STORAGE_ACCEL_REDIRECT=false` and `Range: bytes=-10` | `206` from the app |

In the test, bucket, app and nginx run on the host network, so the internal
endpoint is `127.0.0.1:<port>` instead of `bucket:9000`. What matters is the
same: the `Host` nginx sends is the host that signed the URL.

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
import asyncio

from tempest_fastapi_sdk import AsyncMinIOClient, PutObjectItem

from src.core.settings import settings

storage = AsyncMinIOClient(**settings.minio_kwargs())

# In your code these come off disk (`Path(...).read_bytes()`) or from the upload.
thumb_a = b"\xff\xd8\xff\xdb bytes of the first JPEG"
thumb_b = b"\xff\xd8\xff\xdb bytes of the second JPEG"


async def main() -> None:
    """Run this example."""
    etags = await storage.put_objects(
        [
            PutObjectItem(key="thumbs/a.jpg", data=thumb_a, content_type="image/jpeg"),
            PutObjectItem(key="thumbs/b.jpg", data=thumb_b, content_type="image/jpeg"),
        ]
    )


asyncio.run(main())
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
import asyncio

from tempest_fastapi_sdk import AsyncMinIOClient

from src.core.settings import settings

storage = AsyncMinIOClient(**settings.minio_kwargs())


async def main() -> None:
    """Run this example."""
    await storage.copy_object("uploads/draft-1", "uploads/final-1")
    await storage.remove_object("uploads/draft-1")


asyncio.run(main())
```

## When NOT to use `AsyncMinIOClient`

- When you need operations **outside** the listed surface (SSE-KMS, S3 v2 ACLs, bucket replication). Use `storage.client.<method>` directly — `minio-py` stays accessible.
- For huge resumable uploads (> 5 GiB) — `minio-py` does multipart automatically but doesn't support `tus` or resume. Consider `tus.io` separately.

## Recap

- `AsyncMinIOClient` is an async facade over the official `minio` package,
  behind the `[minio]` extra: buckets, objects and presigned URLs — what a
  FastAPI service usually needs.
- Configuration comes from the settings mixin, and the client is built in
  `create_app()`'s `lifespan` — one instance per process, not one per request.
- A presigned URL is how you hand over a private file without streaming the
  bytes through your own process.
- When the bytes must go through the backend, `download_response(key,
  request=request)` answers `206`, `304` and `416` — without the `request` it
  is always a full `200`.
- To have nginx deliver instead of the app, `serve_object` with
  `STORAGE_ACCEL_REDIRECT=true` answers `X-Accel-Redirect`; the internal
  `location` needs `proxy_pass` with a trailing slash and `Host` equal to
  `MINIO_ENDPOINT`.
- Anything outside the facade is not blocked: call `storage.client.<method>` and
  use `minio-py` directly, instead of waiting for the facade to grow.
- To switch between local disk and MinIO by configuration, the pluggable upload
  backend is the road — this facade is for a service that already chose MinIO.

## What's next

- The pluggable upload backend `MinIOUploadStorage` shipped in v0.24.0 — for the upload pipeline that switches between local disk and MinIO/S3 via a settings flag, see the [uploads recipe](uploads.en.md).
