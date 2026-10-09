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
    ServerSettings,
    StorageSettings,
)


class Settings(
    ServerSettings,
    StorageSettings,
    BaseAppSettings,
):
    """Service settings — inherits the storage defaults (local MinIO)."""
```

`.env`:

```bash
STORAGE_ENDPOINT=minio.internal:9000
STORAGE_ACCESS_KEY=...
STORAGE_SECRET_KEY=...
STORAGE_SECURE=true
STORAGE_REGION=us-east-1
STORAGE_DEFAULT_BUCKET=uploads
```

The mixin talks to **any** S3-compatible storage (AWS S3, MinIO, R2, B2,
Wasabi, Spaces) — the `MinIO` in `AsyncMinIOClient` is the library the client
uses, not a requirement on the server. That is why the variables are named
`STORAGE_*`.

### Coming from `MinIOSettings`

Until now the mixin was called `MinIOSettings` and the variables were
`MINIO_*`. Nothing breaks on the switch:

- **The old variables keep working.** Each field reads `STORAGE_X` first and
  falls back to `MINIO_X` when the new one is not set — in the environment and
  in the `.env`.
- **`MinIOSettings` stays importable**, as a deprecated subclass of
  `StorageSettings`: composing or instantiating it emits `DeprecationWarning`,
  and the `settings.MINIO_*` attributes keep answering.
- **`minio_kwargs()` still exists** and returns the same dict as
  `storage_kwargs()`.

| Old variable | New variable |
| --- | --- |
| `MINIO_ENDPOINT` | `STORAGE_ENDPOINT` |
| `MINIO_ACCESS_KEY` | `STORAGE_ACCESS_KEY` |
| `MINIO_SECRET_KEY` | `STORAGE_SECRET_KEY` |
| `MINIO_SECURE` | `STORAGE_SECURE` |
| `MINIO_REGION` | `STORAGE_REGION` |
| `MINIO_DEFAULT_BUCKET` | `STORAGE_DEFAULT_BUCKET` |
| `MINIO_PUBLIC_ENDPOINT` | `STORAGE_PUBLIC_ENDPOINT` |
| `MINIO_PUBLIC_SECURE` | `STORAGE_PUBLIC_SECURE` |

!!! warning "Both names with different values stop the boot"
    With `STORAGE_ENDPOINT=minio:9000` in the environment and a forgotten
    `MINIO_ENDPOINT=localhost:9000` in the `.env`, building the settings raises
    `SettingsError` naming the pair (`STORAGE_ENDPOINT and MINIO_ENDPOINT`),
    without printing any value. Without this check `AliasChoices` would keep
    the new name silently and the old one would look configured. Both names
    with the **same** value are accepted, for the migration window. Remove the
    old name to fix it.

    This also applies if you regenerate only the `docker-compose.yaml` with
    `tempest`: the generated block now writes `STORAGE_ENDPOINT`, and a
    different `MINIO_ENDPOINT` in the `.env` the container reads is refused.

!!! tip "In production"
    With [`EnvironmentSettings`](system-checks.md#production-guard-environmentsettings)
    and `ENV=production`, the boot refuses the default `minioadmin` keys,
    `STORAGE_SECURE=false` and an explicit `STORAGE_PUBLIC_SECURE=false`.

## Wiring into `create_app()`

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from tempest_fastapi_sdk import AsyncMinIOClient

from src.core.settings import settings


# settings.storage_kwargs() maps the STORAGE_* fields -> endpoint,
# access_key, secret_key, default_bucket, secure, region, public_endpoint,
# public_secure, accel_redirect and accel_prefix: nothing to repeat by hand.
storage = AsyncMinIOClient(**settings.storage_kwargs())


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
and `Last-Modified`, plus the security headers of every download (`nosniff`,
the `sandbox` CSP and `same-site` CORP). `as_attachment=False` only becomes
`inline` when the type stored with the object is in `INLINE_SAFE_MEDIA_TYPES`
— a `text/html` goes out as `attachment`. Details in
[Downloads](downloads.md#security-headers-and-what-goes-inline). With the `request` in hand, the route answers like this:

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
what the client got in its constructor, and `StorageSettings` maps two new
variables:

```bash
# .env
STORAGE_ENDPOINT=bucket:9000          # INTERNAL endpoint: the SDK signs against it
STORAGE_ACCEL_REDIRECT=true         # false (default) = proxy through the app
STORAGE_ACCEL_PREFIX=/_bucket/      # nginx's internal location
```

```python
from fastapi import APIRouter, Request
from starlette.responses import Response
from tempest_fastapi_sdk import AsyncMinIOClient, guess_media_type

from src.core.settings import settings

router = APIRouter()
storage = AsyncMinIOClient(**settings.storage_kwargs())


@router.get("/files/{key:path}")
async def download_file(key: str, request: Request) -> Response:
    """Authorise and deliver — through the app or through nginx, per the .env."""
    return await storage.serve_object(
        key,
        request=request,
        media_type=guess_media_type(key),
        as_attachment=False,
    )
```

!!! warning "In redirect mode, `inline` needs `media_type=`"
    `accel_redirect_response` makes no `stat`, so it does not know which type
    the bucket will answer with. Without `media_type=`, `as_attachment=False`
    comes out `attachment`. With it, the type is signed into the URL (the
    bucket answers with that one, not the stored one) and only becomes
    `inline` if it is in `INLINE_SAFE_MEDIA_TYPES`. `guess_media_type(key)`
    above resolves it from the key's extension: `lesson.mp4` goes inline,
    `page.html` becomes a download. `accel_redirect_response` defaults to
    `as_attachment=True`.

With `STORAGE_ACCEL_REDIRECT=false`, it is the previous section's
`download_response`. With `true`, it is `accel_redirect_response`, and the
`request` is not used: nginx forwards the client's `Range` and
`If-None-Match` to the bucket by itself.

!!! info "`accel_redirect_response` directly"
    Want the redirect on a single route, regardless of the flag? Call
    `storage.accel_redirect_response(key, internal_prefix="/_bucket/",
    expires=timedelta(minutes=5), filename=..., media_type=...,
    as_attachment=False, cache_control=...)`. It **never** uses
    `STORAGE_PUBLIC_ENDPOINT`: the URL is signed against `STORAGE_ENDPOINT`, the
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
        proxy_hide_header X-Content-Type-Options;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Content-Security-Policy "default-src 'none'; sandbox" always;
        add_header Cross-Origin-Resource-Policy "same-site" always;
    }
}
```

Each line of `location /_bucket/` has a reason:

- **`internal;`** — only nginx gets in here, following an
  `X-Accel-Redirect`. A client asking for `/_bucket/...` directly gets `404`.
- **`proxy_pass http://bucket:9000/;` with the trailing slash** — with the
  slash, nginx replaces the `/_bucket/` prefix with `/`, and the bucket
  receives `/media/lesson.mp4?X-Amz-...`, exactly the path that was signed.
  Without it, the prefix goes along, MinIO reads `_bucket` as the bucket name
  and answers `400 InvalidBucketName`.
- **`proxy_set_header Host bucket:9000;`** — the SigV4 signature covers
  `Host`. It has to be the `STORAGE_ENDPOINT` that signed the URL. The danger is
  inheritance: a `proxy_set_header` defined at `server` level (the
  `Host $host` nearly every config has for the app) is inherited by any
  `location` that defines none. The bucket then receives
  `Host: example.com` and answers `403 SignatureDoesNotMatch` — the symptom is
  "the file does not load".
- **The `add_header ... always` lines** — the security headers of every
  download, and this is the only place they reach the client from. nginx
  answers with the bucket's response, not the app's empty one: a header the
  app put there would not get through (measured — which is why the SDK does
  not set them). Without `always`, nginx adds the header to only part of
  the statuses — that is how the `add_header` documentation defines it.
- **`proxy_hide_header X-Content-Type-Options;`** — MinIO already sends
  `nosniff`; without hiding its own, the client gets `nosniff, nosniff`
  (measured). Hiding and re-emitting leaves a single value, whatever storage
  sits behind.

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
| `GET /files/clip.mp4`, `location` with the `add_header` lines | `X-Content-Type-Options`, CSP and CORP, one value each |
| `location` without the `add_header` lines | `200` with no CSP nor CORP, even with the app setting both on the empty response |
| `as_attachment=False` with no `media_type=` | `Content-Disposition: attachment` |
| the same route with `STORAGE_ACCEL_REDIRECT=false` and `Range: bytes=-10` | `206` from the app |

In the test, bucket, app and nginx run on the host network, so the internal
endpoint is `127.0.0.1:<port>` instead of `bucket:9000`. What matters is the
same: the `Host` nginx sends is the host that signed the URL.

### Private file behind a signed URL of your own route

When the frontend talks only to the backend, a private file needs a URL **of
the backend** that the browser can open on its own. `<img src>`,
`<video src>` and a download link send no `Authorization: Bearer`, and a
session cookie across the app's domain and the API's runs into `SameSite`.
The bucket's presigned URL does not help: it sends the browser to the bucket,
not to your route.

The way out has the same shape as a presigned URL, aimed at the app's route.
The mapper that already authorized the caller builds
`/api/files/<key>?expires=<ts>&signature=<mac>` with `sign_path`, and the
route checks the signature with `make_signed_path_dependency` before
streaming:

```python
from datetime import timedelta

from fastapi import Depends, FastAPI, Request
from starlette.responses import Response
from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    BaseSchema,
    make_signed_path_dependency,
    register_exception_handlers,
    sign_path,
)

FILES_SECRET: str = "replace-with-settings.JWT_SECRET"
FILES_PURPOSE: str = "files"

storage = AsyncMinIOClient(
    endpoint="minio.internal:9000",
    access_key="minioadmin",
    secret_key="minioadmin",
    default_bucket="uploads",
)
signed_file = make_signed_path_dependency(secret=FILES_SECRET, purpose=FILES_PURPOSE)


class AttachmentResponseSchema(BaseSchema):
    """An attachment as the frontend gets it: the key and a URL to open."""

    key: str
    url: str


def to_attachment_response(key: str) -> AttachmentResponseSchema:
    """Map the stored key to the schema, with the URL already signed."""
    return AttachmentResponseSchema(
        key=key,
        url=sign_path(
            f"/api/files/{key}",
            secret=FILES_SECRET,
            expires_in=timedelta(minutes=15),
            purpose=FILES_PURPOSE,
        ),
    )


app = FastAPI()
register_exception_handlers(app)


@app.get("/api/attachments/{key:path}")
async def get_attachment(key: str) -> AttachmentResponseSchema:
    """Your authorization goes here: JWT, resource owner, role."""
    return to_attachment_response(key)


@app.get("/api/files/{key:path}", dependencies=[Depends(signed_file)])
async def download_file(key: str, request: Request) -> Response:
    """Only reached with a signed URL that has not expired."""
    return await storage.download_response(
        key, request=request, as_attachment=False
    )
```

Signed at 12:00 UTC on October 1st 2026 with the secret above, the `url` the
frontend receives for the key `aulas/aula 01.mp4` is:

```text
/api/files/aulas/aula%2001.mp4?expires=1790856900&signature=e9vPzlJkaaLXY6fJ0dSsV6RS6D3pOElruFdeskdikck
```

It goes straight into the tag, with no header at all:

```html
<video src="/api/files/aulas/aula%2001.mp4?expires=1790856900&signature=e9vPzlJkaaLXY6fJ0dSsV6RS6D3pOElruFdeskdikck" controls></video>
```

Piece by piece:

- **`sign_path` in the mapper.** It runs after authorization, so only a caller
  allowed to see the attachment ever gets the URL. Whoever holds the URL opens
  the file until `expires` — it is a capability, like a presigned URL, which
  is why the window is short.
- **`make_signed_path_dependency` on the route.** The dependency reads
  `expires` and `signature` from the query and checks them against the
  decoded request path. The handler only runs for a valid signature that has
  not expired: the bucket is not even consulted for a rejected URL.
- **`download_response(..., request=)` is unchanged.** `Range` goes through
  the signature, so the `<video>` still seeks and a download still resumes.
  Swap in `serve_object` and the same dependency guards the
  `X-Accel-Redirect` mode from the previous section.

`tests/storage/test_signed_url_live.py` (`make test-docker`) builds this route
against MinIO in a container and measures: `200` with the whole object through
the signed URL, `206` with `Range: bytes=1000-1999` and only that slice, and
`403` for a tampered or unsigned URL, with no `stat_object` reaching the
bucket. With `serve_object` and `accel_redirect=True`, the signed URL answers
the `X-Accel-Redirect` and the tampered one answers `403` without it.

#### What the signature covers

- **The path and `expires`.** The signature is
  `HMAC-SHA256(key, "<expires>\n<path>")`. Changing the object key or
  stretching `expires` breaks the MAC. Other query parameters are **not**
  covered.
- **The `purpose`.** The MAC key is derived from the secret with the
  `purpose` (`HMAC-SHA256(secret, "tempest-fastapi-sdk.signed-url.v1\0" +
  purpose)`) and is never the raw secret. That is why reusing `JWT_SECRET` is
  safe, and why a URL signed for `"files"` does not open a route guarded with
  `"email-link"`.
- **Constant-time comparison**, with `hmac.compare_digest`.

| The request | Response |
| --- | --- |
| signed URL, not expired | the handler runs |
| `expires` changed | `403`, `code: "SIGNED_URL_INVALID"` |
| path changed | `403`, `code: "SIGNED_URL_INVALID"` |
| signed with another `purpose` or another secret | `403`, `code: "SIGNED_URL_INVALID"` |
| no `expires`/`signature`, or a non-numeric `expires` | `403`, `code: "SIGNED_URL_INVALID"` (never `422`) |
| authentic, but at the `expires` second or later | `403`, `code: "SIGNED_URL_EXPIRED"` |

Each row is a test in `tests/api/test_signed_path_dependency.py`. The body
follows the SDK envelope:

```json
{"detail": "Signed URL has expired", "code": "SIGNED_URL_EXPIRED", "details": {}}
```

!!! info "Why `403` and not `401`"
    A signed URL is a capability, not a login: no credential the client could
    send fixes the URL. S3 answers an expired or tampered presigned URL with
    `403` for the same reason. A `401` would also trip the "session expired,
    back to login" interceptor nearly every frontend installs, while the
    user's session is fine. The two `code`s exist so the frontend can tell
    "ask the backend for a fresh URL" (`SIGNED_URL_EXPIRED`) from "this link
    was tampered with" (`SIGNED_URL_INVALID`).

#### The path encoding rule

`sign_path` takes the **decoded** path, the way the route will see it in
`request.scope["path"]` (it is what `{key:path}` receives), and returns the
URL already percent-encoded. Starlette decodes the path before routing, so
both sides sign the same string:

- `aula 01.mp4` travels as `aula%2001.mp4`, `100%.pdf` as `100%25.pdf`, and
  `á.txt` as `%C3%A1.txt`; the route receives the original key;
- `/api/files/a%2Fb.pdf` and `/api/files/a/b.pdf` reach the route as the same
  path, with the same key `a/b.pdf`, and verify with the same signature.

Measured on Starlette 0.46.0 (the floor `fastapi>=0.141.1` accepts) and on
1.6.0.

!!! warning "Pass the decoded path, never an encoded one"
    `sign_path("/api/files/aula%2001.mp4", ...)` signs the literal key
    `aula%2001.mp4`, which becomes `aula%252001.mp4` in the URL. Build the
    path from the key as it is stored.

!!! tip "Mounted apps and `root_path`"
    Under `app.mount("/v1", sub_app)` the route sees `/v1/api/files/...`, so
    sign with the mount prefix. A proxy `root_path` (the prefix the reverse
    proxy strips before forwarding) does **not** show up in
    `request.scope["path"]`: sign without it and prepend the prefix to the
    URL the browser gets.

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

Fix: `STORAGE_PUBLIC_ENDPOINT`. Presigned URLs (`presigned_get_url` / `presigned_put_url`) are then **signed against the public host**, while **every server→MinIO operation keeps using the internal endpoint**.

```bash
# .env
STORAGE_ENDPOINT=servus-storage:9000            # internal Docker network (ops)
STORAGE_SECURE=false
STORAGE_PUBLIC_ENDPOINT=https://storage.example.com   # browser (presigned)
# STORAGE_PUBLIC_SECURE=true                     # optional; https:// already implies it
```

!!! info "Why two clients, not a host replace"
    A presigned URL is SigV4-signed including the `Host` header. Rewriting the host **after** signing invalidates the signature. So the SDK keeps a second `minio.Minio` (same credentials) whose only job is to **sign** against the public host — the internal `AsyncMinIOClient.client` still does put/get/stat/ensure_bucket over the private network.

!!! tip "Without `STORAGE_PUBLIC_ENDPOINT`"
    Unchanged behaviour: presigned URLs are signed with `STORAGE_ENDPOINT` (single-endpoint mode). The split is fully opt-in.

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

storage = AsyncMinIOClient(**settings.storage_kwargs())

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

### Batch delete

`remove_objects` deletes many keys with S3's `DeleteObjects` — one request
per 1000 keys instead of one `DELETE` per object — and returns only what the
store refused:

```python
import asyncio

from tempest_fastapi_sdk import AsyncMinIOClient

storage = AsyncMinIOClient(
    endpoint="localhost:9000",
    access_key="minioadmin",
    secret_key="minioadmin",
    default_bucket="uploads",
)


async def main() -> None:
    """Delete everything under a prefix."""
    keys = await storage.list_objects("tmp/")
    errors = await storage.remove_objects(keys)
    for error in errors:
        print(error.key, error.code, error.message)


asyncio.run(main())
```

An empty list is success. To delete **a data subject's files** (LGPD), use
`SubjectObjectStorage.delete_all` — see
[Data subject export and erasure](subject-data.md).

### Copy / move

```python
import asyncio

from tempest_fastapi_sdk import AsyncMinIOClient

from src.core.settings import settings

storage = AsyncMinIOClient(**settings.storage_kwargs())


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
  `location` needs `proxy_pass` with a trailing slash, `Host` equal to
  `STORAGE_ENDPOINT` and the security `add_header` lines — nginx does not
  forward the app's.
- For the browser to open a private file through **the app's route**, sign
  the path in the mapper with `sign_path` and guard the route with
  `make_signed_path_dependency`: the signature covers path, `expires` and
  `purpose`, and a rejection is `403` (`SIGNED_URL_INVALID` or
  `SIGNED_URL_EXPIRED`).
- Anything outside the facade is not blocked: call `storage.client.<method>` and
  use `minio-py` directly, instead of waiting for the facade to grow.
- To switch between local disk and MinIO by configuration, the pluggable upload
  backend is the road — this facade is for a service that already chose MinIO.

## What's next

- The pluggable upload backend `MinIOUploadStorage` shipped in v0.24.0 — for the upload pipeline that switches between local disk and MinIO/S3 via a settings flag, see the [uploads recipe](uploads.en.md).
