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

## Restrict extensions (allowlist)

Pass `allowed_extensions` to the constructor with the set of extensions you
accept. Anything outside the list is rejected with **HTTP 415**
(`InvalidFileTypeException`) **before a single byte is read** — so a
malicious `.zip` never reaches the backend nor uses memory:

```python
from tempest_fastapi_sdk import UploadUtils

# ONNX models only — any other extension is blocked.
uploads = UploadUtils(
    "var/models",
    allowed_extensions={".onnx", ".ort"},
    max_size_bytes=200 * 1024 * 1024,
)
```

```python
@router.post("/models")
async def upload_model(file: UploadFile) -> dict[str, str]:
    """Accepts only .onnx / .ort; a .zip raises 415 here inside save()."""
    key = await uploads.save(file)   # file.zip -> InvalidFileTypeException (415)
    return {"key": str(key)}
```

!!! info "Dot and case are normalized"
    `{".onnx", ".ort"}`, `{"onnx", "ort"}` and `{".ONNX"}` are equivalent —
    `UploadUtils` strips the leading dot and lowercases. The extension comes
    from `Path(file.filename).suffix`, so `model.ONNX` passes and
    `package.zip` does not.

!!! warning "Extension is not the content"
    Checking the extension stops the honest mistake and the obvious `.zip`,
    but the filename is client-controlled. For formats with a known signature
    (images, PDF) turn on `verify_magic_bytes=True` + `allowed_mimetypes={...}`
    to match the **real bytes** against the allowlist. Binary formats with no
    signature in `sniff_mime` (like `.onnx` / `.ort`) **must** keep
    `verify_magic_bytes=False` (the default) — otherwise the sniff fails to
    recognize the signature and rejects everything. For those, validate the
    content with a `content_validator=...` on `save()`.

### Via settings (`.env`)

To configure per environment, `UploadSettings` already exposes
`UPLOAD_ALLOWED_EXTENSIONS` (and `UPLOAD_ALLOWED_MIMETYPES`):

```bash
# .env
UPLOAD_ALLOWED_EXTENSIONS=[".onnx", ".ort"]
UPLOAD_MAX_SIZE_BYTES=209715200
```

```python
uploads = UploadUtils(
    settings.UPLOAD_DIR,
    allowed_extensions=settings.UPLOAD_ALLOWED_EXTENSIONS,
    max_size_bytes=settings.UPLOAD_MAX_SIZE_BYTES,
)
```

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

### Swap a file (avatar, attachment) — `replace`

The classic case: the user uploads a new profile picture and you want to
**save the new one and delete the old one**. Instead of doing `save` +
`delete` by hand (and risking deleting through the wrong backend), use
`replace`:

```python
# old_key is whatever is stored on the model today (may be None on 1st upload)
new_key = await uploads.replace(
    user.profile_picture, file, filename=f"{user.id}.jpg"
)
user.profile_picture = str(new_key)
```

!!! tip "Order matters — and `replace` gets it right for you"
    `replace` **saves the new file first**, then deletes the old one. If
    validation rejects the new file (extension/MIME/size), the old one is
    left **intact** — you never end up with no image at all. Pass
    `old_key=None` on the first upload (nothing to delete) and it just
    saves. Everything goes through the **same** configured backend (local
    or MinIO), avoiding the save-here-delete-there mistake.

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
