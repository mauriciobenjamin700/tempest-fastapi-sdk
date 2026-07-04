# Unified file store — `FileStoreUtils`

Storing a file, serving it back and signing a temporary URL usually means
wiring **three** pieces by hand: `UploadUtils` (validate + persist),
`DownloadUtils` (serve bytes through the API) and `AsyncMinIOClient`
(presigned URLs). `FileStoreUtils` wraps all three behind **one object**, with
**one configuration**, targeting the **same storage backend**.

!!! tip "When to use it"
    Reach for `FileStoreUtils` when a service both **stores and serves** the
    same files. If you only need one half (upload only, or download only), the
    standalone `UploadUtils` / `DownloadUtils` are still there — the facade
    just joins them over a shared backend.

Requires the `[upload]` extra for local disk and `[minio]` for MinIO/S3.

## The basics — local disk

The backend is picked **once at construction**: pass a directory to store files
on local disk.

```python
from fastapi import APIRouter, UploadFile
from starlette.responses import Response

from tempest_fastapi_sdk import FileStoreUtils

router = APIRouter()
store = FileStoreUtils("var/uploads", max_size_bytes=10 * 1024 * 1024)


@router.post("/files")
async def upload(file: UploadFile) -> dict[str, str]:
    """Validate and persist; returns the key (relative to the base dir)."""
    key = await store.save(file)
    return {"key": str(key)}


@router.get("/files/{key}")
async def download(key: str) -> Response:
    """Serve the file back through the API itself."""
    return await store.download(key)
```

The same `store` object persists (`save`), serves (`download`), removes
(`delete`), checks existence (`exists`) and swaps (`replace`) — no extra
instances.

## MinIO / S3 — just change `source`

Pass an `AsyncMinIOClient` instead of the directory. Nothing else in your code
changes:

```python
from tempest_fastapi_sdk import AsyncMinIOClient, FileStoreUtils

minio = AsyncMinIOClient(
    endpoint="minio:9000",
    access_key="...",
    secret_key="...",
    default_bucket="avatars",
)
store = FileStoreUtils(minio, max_size_bytes=5 * 1024 * 1024)

key = await store.save(file, subdir="users/42")   # writes to the bucket
url = await store.presigned_get_url(str(key))       # signed read URL
```

!!! info "Bucket"
    To target a specific bucket, set the `AsyncMinIOClient`'s
    `default_bucket` — both halves (upload and download) read it.

## Presigned URLs

On the MinIO backend, `FileStoreUtils` exposes both presign shortcuts — let the
client download/upload straight from MinIO without streaming the bytes through
your API:

```python
from datetime import timedelta

get_url = await store.presigned_get_url(key, expires=timedelta(hours=1))
put_url = await store.presigned_put_url(key, expires=timedelta(minutes=15))
```

!!! note "Local returns `None`"
    Local disk has no public URL, so `presigned_get_url` and
    `presigned_put_url` return `None` — the same call site works for both
    backends, no `if backend == ...`.

## Validation

Validation (size, extension, MIME, magic bytes, `content_validator`) is
`UploadUtils`' own — configured in the constructor and applied **before** any
byte reaches the backend:

```python
store = FileStoreUtils(
    "var/uploads",
    max_size_bytes=5 * 1024 * 1024,
    allowed_extensions={"png", "jpg", "jpeg"},
    allowed_mimetypes={"image/png", "image/jpeg"},
    verify_magic_bytes=True,
)
```

## Replace a file (avatar / attachment)

`replace` writes the new object **first** (a validation error leaves the old
one intact), then deletes the old one — through the same backend:

```python
new_key = await store.replace(user.avatar_key, file, filename=f"{user.id}.png")
user.avatar_key = str(new_key)
```

## Escape hatches

The internal pieces stay reachable when you need finer control:

```python
store.uploader     # UploadUtils     — save/replace/delete/validate
store.downloader   # DownloadUtils   — download/file_response/stream/resolve
store.backend      # UploadStorage   — write_stream/delete/exists/presigned_url
store.client       # AsyncMinIOClient | None  — None for local disk
```

## Recap

- `FileStoreUtils(source, ...)` — **one** object to store, serve and sign.
- `source` is a directory (local disk) **or** an `AsyncMinIOClient` (MinIO/S3);
  the rest of your code is identical between the two.
- A single `UploadStorage` backend is built and **shared** with the upload
  half; on MinIO the **same** client goes to the download half (shared
  connection pool).
- Presign exists only on MinIO — local disk returns `None`, keeping the call
  site uniform.
- Only need one half? `UploadUtils` / `DownloadUtils` are still standalone.
