# Downloads

`DownloadUtils` serves files for download/inline — from **local disk** or
straight from a **MinIO/S3 bucket**. Pick the backend **once at
construction** (just like [Uploads](uploads.md)): pass a folder, or an
`AsyncMinIOClient`. Then call `download(key)` — it works the same for both.
Ships in the base SDK (no extra; MinIO needs `[minio]`).

In local mode there's **path-traversal protection**: any path escaping
`base_dir` (`../`, absolute, symlink) raises `NotFoundException` — the same
404 as a missing file, so the client can't tell "doesn't exist" from
"forbidden".

## Local disk

```python
# src/api/routers/files.py
from fastapi import APIRouter
from starlette.responses import Response

from tempest_fastapi_sdk import DownloadUtils

router = APIRouter(prefix="/files", tags=["files"])
downloads = DownloadUtils("var/uploads")


@router.get("/{name}")
async def download(name: str) -> Response:
    """Download a file from var/uploads (forcing a download)."""
    return await downloads.download(name)
```

## MinIO / S3

Same code, only the constructor changes — `download(key)` proxies the object
from the bucket (never lands on disk, never loads fully into memory):

```python
from fastapi import APIRouter, Request, Response

from tempest_fastapi_sdk import AsyncMinIOClient, DownloadUtils

from src.core.settings import settings

router = APIRouter()


minio = AsyncMinIOClient(**settings.storage_kwargs())
downloads = DownloadUtils(minio)


@router.get("/files/{name}")
async def download(name: str, request: Request) -> Response:
    """Stream the object from the bucket, behind the app's auth."""
    return await downloads.download(name, subdir="invoices", request=request)
```

`download` parameters: `subdir=` (local folder / key prefix), `filename=`
(name shown to the client), `media_type=` (otherwise from the object's
content-type / extension), `as_attachment=False` (asks for **inline** — e.g.
view a PDF in the browser; granted only to a safe type, see
[below](#security-headers-and-what-goes-inline)), `request=` (in MinIO mode, answers `Range` with
`206` and `If-None-Match`/`If-Modified-Since` with `304` — details in the
[storage recipe](storage.md#streaming-download)), `cache_control=` (the
`Cache-Control` value), `headers=`.

!!! warning "Without `request=`, MinIO always answers a full `200`"
    Local mode does not need it: Starlette's `FileResponse` reads `Range`
    straight from the request. In MinIO mode, without `request=` a `<video>`
    cannot seek and an interrupted download restarts from zero.

!!! tip "Proxy (app) vs presigned (direct)"
    `download()` **proxies** through the app — ideal when the download must
    pass through auth or MinIO isn't public. When the client can talk to
    MinIO directly, prefer `presigned_get_url` (see [Storage](storage.md))
    and return a redirect — fully offloading the transfer.

## Serve a file from disk (fine control)

In local mode, `file_response` gives direct control and returns a
`FileResponse` streamed in chunks by Starlette (supports range requests):

```python
from fastapi import APIRouter
from starlette.responses import FileResponse

from tempest_fastapi_sdk import DownloadUtils

router = APIRouter(prefix="/invoices", tags=["invoices"])
downloads = DownloadUtils("./uploads")


@router.get("/{name}")
async def show_invoice(name: str) -> FileResponse:
    """Open ./uploads/invoices/<name> inline in the browser."""
    return downloads.file_response(name, subdir="invoices", as_attachment=False)
```

Parameters: `subdir=`, `filename=`, `media_type=`, `as_attachment=`,
`headers=`. (Local mode only; on a MinIO `DownloadUtils` it raises
`RuntimeError` — use `download()`.)

!!! danger "Path traversal is blocked by construction"
    `downloads.file_response("../../etc/passwd")` raises
    `NotFoundException` (404), it does not leak the file. Always construct
    `DownloadUtils` with a `base_dir` dedicated to servable content.

## Stream bytes produced on the fly

When the payload is produced at runtime (a report, an in-memory zip,
decrypted bytes) and does **not** come from disk, use `stream` — it accepts
`bytes`, a sync iterable, or an async-iterable:

```python
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from tempest_fastapi_sdk import DownloadUtils

downloads = DownloadUtils("./uploads/invoices")

router = APIRouter()


@router.get("/report.csv")
async def report() -> StreamingResponse:
    """Generate a CSV on demand and download it as report.csv."""
    async def rows() -> AsyncIterator[bytes]:
        yield b"id,name\n"
        for i in range(1000):
            yield f"{i},item-{i}\n".encode()

    return downloads.stream(rows(), filename="report.csv", media_type="text/csv")
```

## `Content-Type` that does not depend on the image

When you pass no `media_type=`, `DownloadUtils` guesses from the file name.
Python's `mimetypes` alone does not cut it: its built-in table has no
`.xlsx`, `.docx`, `.pptx`, `.odt`, `.ods` or `.ogg` (measured on Python
3.11, 3.12 and 3.13), and it only knows them when the host has
`/etc/mime.types`. Your machine does; `python:3.13-slim` — the base of the
Dockerfile `tempest new` generates — does not, and the same `.xlsx` went out
as `application/octet-stream` in production only.

So the guess goes through `guess_media_type`, which checks an SDK table
before `mimetypes`. Measured inside `python:3.13-slim`:

| File | `mimetypes.guess_type` | `guess_media_type` |
| --- | --- | --- |
| `a.xlsx` | `None` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| `a.docx` | `None` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| `a.ods` | `None` | `application/vnd.oasis.opendocument.spreadsheet` |
| `a.pdf` | `application/pdf` | `application/pdf` |

Use the same function (or the constants) when you build the response by
hand:

```python
from tempest_fastapi_sdk import XLSX_MEDIA_TYPE, guess_media_type

media_type: str | None = guess_media_type("exports/Budget.XLSX")
print(media_type == XLSX_MEDIA_TYPE)
# -> True, on the host and in the container
```

`XLSX_MEDIA_TYPE`, `DOCX_MEDIA_TYPE` and `PPTX_MEDIA_TYPE` live in
`tempest_fastapi_sdk.utils` (and at the package top level); `XLSX_MEDIA_TYPE`
also in `tempest_fastapi_sdk.spreadsheet`. An extension neither the table
nor `mimetypes` knows stays `None`, and the download falls back to
`application/octet-stream`.

## Security headers and what goes inline

The file you serve reaches the browser **on your API's origin**, with the
`Content-Type` that, in the usual flow, the uploader declared. An `.html`
uploaded by a user and served `inline` would be a page of your API, running
script with the session of whoever opened the link.

So every download response — `download`, `file_response`, `stream` and the
MinIO `download_response` — carries the same headers as
`HardenedStaticFiles` (`DEFAULT_STATIC_SECURITY_HEADERS`):

- `X-Content-Type-Options: nosniff`
- `Content-Security-Policy: default-src 'none'; sandbox`
- `Cross-Origin-Resource-Policy: same-site`

And `as_attachment=False` is now a **request**: it only becomes `inline` when
the response type is in `INLINE_SAFE_MEDIA_TYPES` — raster images (PNG, JPEG,
GIF, WebP, AVIF), `application/pdf`, `text/plain` and common audio/video.
Any other type goes out as `attachment`. `text/html` and `image/svg+xml` are
left out on purpose: both carry script.

```python
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import FileResponse

from tempest_fastapi_sdk import DownloadUtils

Path("uploads").mkdir(exist_ok=True)
Path("uploads/evil.html").write_text("<script>window.ran = true</script>")
Path("uploads/photo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

downloads = DownloadUtils("uploads")
app = FastAPI()


@app.get("/files/{name}")
async def show_file(name: str) -> FileResponse:
    """Ask for inline; the SDK decides whether the type may."""
    return downloads.file_response(name, as_attachment=False)


client = TestClient(app)
for name in ("photo.png", "evil.html"):
    headers = client.get(f"/files/{name}").headers
    print(name, "->", headers["content-disposition"].split(";")[0])
    print("  x-content-type-options:", headers["x-content-type-options"])
    print("  content-security-policy:", headers["content-security-policy"])
    print("  cross-origin-resource-policy:", headers["cross-origin-resource-policy"])
```

Running it prints:

```text
photo.png -> inline
  x-content-type-options: nosniff
  content-security-policy: default-src 'none'; sandbox
  cross-origin-resource-policy: same-site
evil.html -> attachment
  x-content-type-options: nosniff
  content-security-policy: default-src 'none'; sandbox
  cross-origin-resource-policy: same-site
```

!!! tip "Your header wins"
    Pass `headers={"Content-Security-Policy": "..."}` and your value replaces
    the default (names compare case-insensitively, so it is not sent twice).
    The other two stay.

!!! info "In MinIO mode, the type checked is the object's"
    `download` / `download_response` read the type stored in the bucket (the
    `stat`) when you pass no `media_type=`. When you pass one, yours counts.
    In `X-Accel-Redirect` mode the object is not looked up — see
    [Storage](storage.md#the-nginx-block).

## `Content-Disposition` header

To build the header manually (outside `DownloadUtils`), use
`build_content_disposition` — it escapes the filename correctly (RFC 5987,
with an ASCII fallback):

```python
from tempest_fastapi_sdk import build_content_disposition

header: str = build_content_disposition("report 2026.pdf", as_attachment=True)
# -> attachment; filename="report 2026.pdf"; filename*=UTF-8''report%202026.pdf
```

For `inline`, also pass the type the response will carry:
`build_content_disposition("photo.png", as_attachment=False, media_type="image/png")`.
With no `media_type=`, or a type outside `INLINE_SAFE_MEDIA_TYPES`, the value
comes out `attachment` — the same rule as the download helpers.

!!! warning "The name is treated as untrusted"
    In normal use it is `UploadFile.filename` — chosen by the client. On top of
    reducing it to a basename (no path gets through), the function strips
    **every** control character: a name containing `\r\n` produced a header
    with a real line break, which an ASGI server that does not validate header
    values (uvicorn on `httptools`) writes to the socket as-is, letting whoever
    uploaded the file append headers of their own to your response.

    ```python
    build_content_disposition("rep\r\nX-Injected: 1.pdf")
    # -> attachment; filename="repX-Injected: 1.pdf"; filename*=...
    #    one line, always
    ```

## Recap

- `DownloadUtils(folder)` or `DownloadUtils(minio_client)` — backend at construction.
- `await downloads.download(key, ...)` — unified: `FileResponse` (local) or streaming (MinIO).
- `stream(content, filename=...)` for bytes/generators produced on the fly (either mode).
- `file_response(...)` is local-only (fine control); MinIO uses `download()`.
- `as_attachment=True` (default) forces a download; `as_attachment=False` only becomes `inline` for a type in `INLINE_SAFE_MEDIA_TYPES` — HTML and SVG go out as `attachment`.
- Every download response carries `nosniff`, the `sandbox` CSP and `same-site` CORP; a header of the same name passed in `headers=` wins.
- Local: path traversal becomes `NotFoundException` — safe by construction.
- With no `media_type=`, the type comes from `guess_media_type`: `.xlsx`/`.docx`/`.pptx` come out right in a slim image too, with no `/etc/mime.types`.
