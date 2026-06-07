# Uploads — local disk + S3 / MinIO

`UploadUtils` picks the backend **once at construction**: pass a **folder**
to store on local disk, or an **`AsyncMinIOClient`** to store in an S3/MinIO
bucket. The rest of the upload code is identical either way. Requires the
`[upload]` extra (and `[minio]` when using MinIO).

!!! warning "Change in v0.41.0 (breaking)"
    The backend now comes from the constructor — the old per-call
    `save(file, storage=...)` was **removed**. `save()` returns the storage
    **key** (relative), and `delete()` is now **async**. See the migration
    at the end.

!!! tip "Validation stays in `UploadUtils`"
    Size, extension, MIME, magic bytes, and `content_validator` are checked
    in `UploadUtils` before any byte reaches the backend — the storage only
    ever receives validated data.

## Local disk

```python
from fastapi import APIRouter, UploadFile

from tempest_fastapi_sdk import UploadUtils

router = APIRouter()
uploads = UploadUtils("var/uploads", max_size_bytes=10 * 1024 * 1024)


@router.post("/files")
async def upload(file: UploadFile) -> dict[str, str]:
    """Validate and write to disk; return the key (relative to base dir)."""
    key = await uploads.save(file)
    return {"key": str(key)}
```

## MinIO / S3

Pass the `AsyncMinIOClient` directly — nothing else changes:

```python
from tempest_fastapi_sdk import AsyncMinIOClient, UploadUtils

from src.core.settings import settings

minio = AsyncMinIOClient(**settings.minio_kwargs())
uploads = UploadUtils(minio, max_size_bytes=10 * 1024 * 1024)

# identical to the local case:
key = await uploads.save(file, filename="logo.png")   # writes to the bucket
```

!!! tip "Centralize in `resources.py`"
    Build `uploads` (and `minio`) once in
    [`src/api/dependencies/resources.py`](../architecture.md) and inject via
    `Depends(get_uploads)`, instead of instantiating per request.

## Switching via settings

Choose the constructor argument from a flag on your `Settings` — no manual
pluggable backend needed:

```python
# src/api/dependencies/resources.py
from tempest_fastapi_sdk import AsyncMinIOClient, UploadUtils

from src.core.settings import settings

if settings.UPLOAD_BACKEND == "minio":
    uploads = UploadUtils(AsyncMinIOClient(**settings.minio_kwargs()))
else:
    uploads = UploadUtils(settings.UPLOAD_DIR)
```

(`UPLOAD_BACKEND` is a field on your `Settings`; the SDK only loads
`UPLOAD_DIR` / `UPLOAD_MAX_SIZE_BYTES` / `UPLOAD_ALLOWED_EXTENSIONS` /
`UPLOAD_ALLOWED_MIMETYPES` via `UploadSettings`.)

## Common operations

```python
key = await uploads.save(file, filename="logo.png")  # -> Path("logo.png")
removed = await uploads.delete(key)                  # async; True/False
```

To **download** what was uploaded (local or MinIO), use
[`DownloadUtils`](downloads.md) — it takes the same backend in its
constructor.

## When to use a direct presigned PUT

For files > 50 MB, skip the in-memory buffer — have the client `PUT`
straight to MinIO via a presigned URL. See
[Storage MinIO/S3](storage.md#presigned-url-direct-browser-upload).

## Migrating from < v0.41.0

- `UploadUtils("./dir")` is unchanged (local disk).
- `UploadUtils("./tmp")` + `save(file, storage=MinIOUploadStorage(client))`
  → becomes `UploadUtils(client)` + `save(file)`.
- `save()` now returns the **key** (relative), not an absolute path — store
  the key and use `DownloadUtils.download(key)` to serve it.
- `utils.delete(path)` (sync) → `await utils.delete(key)` (async).
