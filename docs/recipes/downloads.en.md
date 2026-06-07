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
from tempest_fastapi_sdk import AsyncMinIOClient, DownloadUtils

from src.core.settings import settings

minio = AsyncMinIOClient(**settings.minio_kwargs())
downloads = DownloadUtils(minio)


@router.get("/files/{name}")
async def download(name: str) -> Response:
    """Stream the object from the bucket, behind the app's auth."""
    return await downloads.download(name, subdir="invoices")
```

`download` parameters: `subdir=` (local folder / key prefix), `filename=`
(name shown to the client), `media_type=` (otherwise from the object's
content-type / extension), `as_attachment=False` (serve **inline** — e.g.
view a PDF in the browser), `headers=`.

!!! tip "Proxy (app) vs presigned (direct)"
    `download()` **proxies** through the app — ideal when the download must
    pass through auth or MinIO isn't public. When the client can talk to
    MinIO directly, prefer `presigned_get_url` (see [Storage](storage.md))
    and return a redirect — fully offloading the transfer.

## Serve a file from disk (fine control)

In local mode, `file_response` gives direct control and returns a
`FileResponse` streamed in chunks by Starlette (supports range requests):

```python
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

from fastapi.responses import StreamingResponse


@router.get("/report.csv")
async def report() -> StreamingResponse:
    """Generate a CSV on demand and download it as report.csv."""
    async def rows() -> AsyncIterator[bytes]:
        yield b"id,name\n"
        for i in range(1000):
            yield f"{i},item-{i}\n".encode()

    return downloads.stream(rows(), filename="report.csv", media_type="text/csv")
```

## `Content-Disposition` header

To build the header manually (outside `DownloadUtils`), use
`build_content_disposition` — it escapes the filename correctly (RFC 5987,
with an ASCII fallback):

```python
from tempest_fastapi_sdk import build_content_disposition

header: str = build_content_disposition("report 2026.pdf", as_attachment=True)
# -> attachment; filename="report 2026.pdf"; filename*=UTF-8''report%202026.pdf
```

## Recap

- `DownloadUtils(folder)` or `DownloadUtils(minio_client)` — backend at construction.
- `await downloads.download(key, ...)` — unified: `FileResponse` (local) or streaming (MinIO).
- `stream(content, filename=...)` for bytes/generators produced on the fly (either mode).
- `file_response(...)` is local-only (fine control); MinIO uses `download()`.
- `as_attachment=False` serves inline; `as_attachment=True` (default) forces a download.
- Local: path traversal becomes `NotFoundException` — safe by construction.
