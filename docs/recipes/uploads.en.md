# Uploads — local disk + S3 / MinIO

`UploadUtils` validates and stores FastAPI's `UploadFile`. The backend is
chosen **once, in the constructor**: a **directory** stores on local disk, an
**`AsyncMinIOClient`** stores in an S3/MinIO bucket, and the rest of the code
does not change. Requires the `[upload]` extra (and `[minio]` for MinIO).

!!! tip "Validation lives in `UploadUtils`"
    Extension, MIME, size, magic bytes and `content_validator` run in
    `UploadUtils` before any byte reaches the backend — the storage only
    receives data that already passed.

!!! info "Google Drive"
    To store into a Google Drive folder (or Shared Drive), pass a
    `GoogleDriveUploadStorage` as `backend=` — see the
    [Google Drive (uploads)](google-drive.md) recipe.

Want to see uploads inside a whole service (settings, migrations, auth,
tests)? See [Complete service](full-service.md).

## Quick reference

Use this when you need the exact signature, the return value or the HTTP
status of each refusal.

### Constructor `UploadUtils(...)`

```text
UploadUtils(
    source: str | Path | AsyncMinIOClient | None = None,
    *,
    backend: UploadStorage | None = None,
    max_size_bytes: int | None = None,
    allowed_extensions: set[str] | None = None,
    allowed_mimetypes: set[str] | None = None,
    verify_magic_bytes: bool = False,
    require_known_signature: bool = True,
    chunk_size: int = 1024 * 1024,
)
```

| Parameter | What it does |
| --- | --- |
| `source` | Directory (local disk, created if missing) or `AsyncMinIOClient` (bucket). `None` only with `backend=` |
| `backend` | A ready backend (`GoogleDriveUploadStorage`, your own `UploadStorage`); bypasses the `source`-based selection |
| `max_size_bytes` | File ceiling, checked while the stream is read. `None` or `0` disables it |
| `allowed_extensions` | Extension allowlist. Dot and case are normalized (`{"PNG", ".jpg"}` works). `None` or `set()` disables it |
| `allowed_mimetypes` | Allowlist for the declared `Content-Type` (case-insensitive). `None` or `set()` disables it |
| `verify_magic_bytes` | Checks the first bytes (`sniff_mime`) against the declared type / the allowlist |
| `require_known_signature` | With `verify_magic_bytes`, refuses an unknown signature (`True`) or only a contradiction (`False`) |
| `chunk_size` | Size of each stream read, in bytes (default 1 MiB) |

Without the `[upload]` extra, `UploadUtils("dir")` raises `ImportError`; with
neither `source` nor `backend`, `ValueError`.

### `save(...)` and the other methods

```text
await uploads.save(
    file: UploadFile,
    *,
    subdir: str = "",
    filename: str | None = None,
    keep_original_name: bool = False,
    content_validator: Callable[[bytes], bool] | None = None,
    hasher: Any | None = None,
) -> Path
```

| Parameter | What it does |
| --- | --- |
| `subdir` | Local subdirectory or MinIO key prefix (`"avatars"`, `str(user.id)`) |
| `filename` | Fixed final name (`f"{user_id}.jpg"`); reduced to its basename, guarded against path traversal |
| `keep_original_name` | Keeps the uploaded name; without it (and without `filename`) the name is a UUID with the original extension |
| `content_validator` | Predicate over the **first chunk**; `False` refuses and removes the partial object |
| `hasher` | A `hashlib` object fed **every** chunk — read `hasher.hexdigest()` after `save` |

**Returns:** a `Path` holding the **key relative** to the backend
(`Path("<subdir>/<uuid>.png")`), not an absolute path. Store `str(key)` in the
database and read it back with [`DownloadUtils`](downloads.md).

| Method | Returns | Use |
| --- | --- | --- |
| `await save(file, ...)` | `Path` (key) | validate and store |
| `await replace(old_key, file, ...)` | `Path` (new key) | store the new one, only then delete the old one |
| `await delete(key)` | `bool` | `True` if deleted, `False` if the key did not exist |
| `validate(file)` | `None` | extension and MIME only, without reading the stream |

### Exceptions and HTTP status

Every refusal is an `AppException`; with `register_exception_handlers(app)` it
becomes the status and the `{"detail", "code", "details"}` envelope below.

| Refusal | Exception | Status | `code` | `details` |
| --- | --- | --- | --- | --- |
| Extension outside `allowed_extensions` | `InvalidFileTypeException` | `415` | `INVALID_FILE_TYPE` | `{"extension": "zip", "allowed": [...]}` |
| MIME outside `allowed_mimetypes` | `InvalidFileTypeException` | `415` | `INVALID_FILE_TYPE` | `{"mimetype": "...", "allowed": [...]}` |
| Bytes do not match the type (`verify_magic_bytes`) | `InvalidFileTypeException` | `415` | `INVALID_FILE_TYPE` | `{"reason": "...", "declared": "...", ...}` |
| `content_validator` returned `False` | `InvalidFileTypeException` | `415` | `INVALID_FILE_TYPE` | `{"reason": "content_validator rejected the upload bytes"}` |
| Over `max_size_bytes` | `FileTooLargeException` | `413` | `FILE_TOO_LARGE` | `{"max_size_bytes": 1024}` |

??? example "The statuses above came from this program, which triggers each refusal"

    ```python
    import tempfile
    from pathlib import Path

    from fastapi import FastAPI, UploadFile
    from fastapi.testclient import TestClient

    from tempest_fastapi_sdk import UploadUtils, register_exception_handlers

    upload_dir: Path = Path(tempfile.mkdtemp())
    uploads: UploadUtils = UploadUtils(
        upload_dir,
        max_size_bytes=1024,
        allowed_extensions={".png", ".txt"},
        allowed_mimetypes={"image/png", "text/plain"},
        verify_magic_bytes=True,
        require_known_signature=False,
    )
    app: FastAPI = FastAPI()
    register_exception_handlers(app)


    @app.post("/files")
    async def upload_file(file: UploadFile) -> dict[str, str]:
        """Refuse text starting with `nope`; everything else goes through the allowlists.

        Args:
            file (UploadFile): The multipart form file.

        Returns:
            dict[str, str]: The stored key.
        """
        key = await uploads.save(
            file,
            content_validator=lambda head: not head.startswith(b"nope"),
        )
        return {"key": str(key)}


    client: TestClient = TestClient(app)
    png: bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    cases: list[tuple[str, str, bytes, str]] = [
        ("accepted", "photo.png", png, "image/png"),
        ("extension", "bundle.zip", b"PK", "application/zip"),
        ("mime", "photo.png", png, "application/zip"),
        ("size", "notes.txt", b"x" * 2048, "text/plain"),
        ("content_validator", "notes.txt", b"nope", "text/plain"),
        ("magic bytes", "fake.png", b"<html></html>", "image/png"),
    ]
    for label, name, data, mime in cases:
        response = client.post("/files", files={"file": (name, data, mime)})
        body: dict[str, object] = response.json()
        print(f"{label}: {response.status_code} {body.get('code', '')}")
    print("files stored:", len(list(upload_dir.iterdir())))
    ```

Output:

```text
accepted: 200
extension: 415 INVALID_FILE_TYPE
mime: 415 INVALID_FILE_TYPE
size: 413 FILE_TOO_LARGE
content_validator: 415 INVALID_FILE_TYPE
magic bytes: 415 INVALID_FILE_TYPE
files stored: 1
```

Only the accepted file stays in the directory: one refused for size or content
is removed before the exception. Extension and MIME are refused **before
reading** any byte.

## Upload to local disk

Pass a directory to the constructor.

```python
from fastapi import APIRouter, UploadFile

from tempest_fastapi_sdk import UploadUtils

router = APIRouter()
uploads = UploadUtils("var/uploads", max_size_bytes=10 * 1024 * 1024)


@router.post("/files")
async def upload(file: UploadFile) -> dict[str, str]:
    """Validate and store on disk; return the key (relative to the base dir)."""
    key = await uploads.save(file)
    return {"key": str(key)}
```

!!! tip "Centralize it in `resources.py`"
    Build `uploads` once in
    [`src/api/dependencies/resources.py`](../architecture.md) and inject it via
    `Depends(get_uploads)`. `get_uploads` is your project's glue (it does not
    come from the SDK):

    ```python
    # src/api/dependencies/resources.py
    from tempest_fastapi_sdk import UploadUtils

    uploads = UploadUtils("var/uploads", max_size_bytes=10 * 1024 * 1024)


    def get_uploads() -> UploadUtils:
        """Return the shared UploadUtils instance."""
        return uploads
    ```

## Upload to MinIO / S3

Pass the `AsyncMinIOClient` instead of the directory; the route is the same.

```python
from fastapi import APIRouter, UploadFile

from tempest_fastapi_sdk import AsyncMinIOClient, UploadUtils

from src.core.settings import settings

router = APIRouter()
minio = AsyncMinIOClient(**settings.storage_kwargs())
uploads = UploadUtils(minio, max_size_bytes=10 * 1024 * 1024)


@router.post("/logos")
async def upload_logo(file: UploadFile) -> dict[str, str]:
    """Store in the bucket under a fixed name; return the S3 key."""
    key = await uploads.save(file, filename="logo.png")
    return {"key": str(key)}
```

## Upload for logged-in users only

Ask for the user through the dependency from
`auth_service.current_user_dependency()`: without a valid token the route
answers `401` before reading the file.

```python
# src/api/routers/files.py
from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile

from tempest_fastapi_sdk import UploadUtils

from src.api.dependencies import get_current_user, get_uploads
from src.db import UserModel

router: APIRouter = APIRouter(prefix="/api/files", tags=["files"])


@router.post("", status_code=201)
async def upload_file(
    file: UploadFile,
    current: Annotated[UserModel, Depends(get_current_user)],
    uploads: Annotated[UploadUtils, Depends(get_uploads)],
) -> dict[str, str]:
    """Store the file in the folder of the token's owner.

    Args:
        file (UploadFile): The multipart form file.
        current (UserModel): The user resolved from the bearer token.
        uploads (UploadUtils): The process-wide upload utility.

    Returns:
        dict[str, str]: The key relative to the upload directory.
    """
    key = await uploads.save(file, subdir=str(current.id))
    return {"key": str(key)}
```

`get_current_user` is built once, next to the `UserAuthService`:
`get_current_user = auth_service.current_user_dependency()`. The
[Complete service](full-service.md) page has the whole project around this
route (settings, `resources.py`, migrations) and the test that measured the
responses:

| Request | Status | `code` |
| --- | --- | --- |
| With bearer token, `.png` | `201` | — (`{"key": "<user_id>/<uuid>.png"}`) |
| No token | `401` | `UNAUTHORIZED` |
| With token, `.zip` outside the allowlist | `415` | `INVALID_FILE_TYPE` |
| With token, over `max_size_bytes` | `413` | `FILE_TOO_LARGE` |

- `subdir=str(current.id)` keeps each owner's files apart.
- `soft` mode, cookie and the shared session of `current_user_dependency()`:
  [Auth flow](auth-flow/current-user.md).

## Restrict extensions (allowlist)

Pass `allowed_extensions` with what you accept: anything else is `415`
**before any byte is read**, so a `.zip` never reaches the backend.

```python
from fastapi import APIRouter, UploadFile

from tempest_fastapi_sdk import UploadUtils

router = APIRouter()
uploads = UploadUtils(
    "var/models",
    allowed_extensions={".onnx", ".ort"},
    max_size_bytes=200 * 1024 * 1024,
)


@router.post("/models")
async def upload_model(file: UploadFile) -> dict[str, str]:
    """Accept only .onnx / .ort; a .zip raises 415 inside save()."""
    key = await uploads.save(file)
    return {"key": str(key)}
```

- `{".onnx", ".ort"}`, `{"onnx", "ort"}` and `{".ONNX"}` are equivalent. The
  extension comes from `Path(file.filename).suffix`, so `model.ONNX` passes and
  `bundle.zip` does not.
- The filename belongs to the client: the extension is not the content. For
  formats with a known signature (images, PDF), turn on
  `verify_magic_bytes=True` + `allowed_mimetypes={...}` to match the **real
  bytes**.

!!! warning "Binary format without a known signature (`.onnx`, `.ort`)"
    With `verify_magic_bytes=True`, the default `require_known_signature=True`
    refuses every signature `sniff_mime` does not recognize. Two ways out:
    keep `verify_magic_bytes=False` (the default), or use
    `require_known_signature=False`, which refuses only the **contradiction** —
    an `.onnx` sent as `application/octet-stream` is stored, the same file
    declared as `image/png` is refused (`415`). To truly validate the content,
    use `content_validator=...` on `save()`.

The types `sniff_mime` recognizes from the bytes are in
`SNIFFABLE_MIMETYPES`; declaring one of them is what makes the contradiction
detectable.

```python
from tempest_fastapi_sdk.utils import SNIFFABLE_MIMETYPES

print(sorted(SNIFFABLE_MIMETYPES))
```

```text
['application/pdf', 'image/bmp', 'image/gif', 'image/heic', 'image/jpeg', 'image/png', 'image/webp']
```

### A file that will be served back

Use this when the file is served back by the API (avatar, attachment,
receipt): lock both name **and** content, and an HTML file renamed to `.png` is
`415`.

```python
from tempest_fastapi_sdk import UploadUtils

uploads = UploadUtils(
    "uploads",
    allowed_extensions={".png", ".jpg", ".jpeg", ".pdf"},
    allowed_mimetypes={"image/png", "image/jpeg", "application/pdf"},
    verify_magic_bytes=True,
)
```

Measured with this configuration: `photo.png` with PNG bytes → `200`;
`evil.html` (`text/html`) → `415`; the same HTML as `evil.png` / `image/png` →
`415`. The [quick reference](#exceptions-and-http-status) program triggers the
same refusals.

Downloads already defend themselves — every response carries `nosniff` and the
`sandbox` CSP, and HTML/SVG never go `inline` (see
[Downloads](downloads.md#security-headers-and-what-goes-inline)). Validating
on the way in is the other half: the hostile file never reaches storage.

### Configure from `.env`

Use the `UploadSettings` mixin when the allowlist and the ceiling come from the
environment (how to compose the class: [Settings](settings.md)).

```bash
# .env
UPLOAD_ALLOWED_EXTENSIONS=[".onnx", ".ort"]
UPLOAD_MAX_SIZE_BYTES=209715200
```

```python
from tempest_fastapi_sdk import UploadUtils

from src.core.settings import settings


uploads = UploadUtils(**settings.upload_kwargs())
```

`UploadSettings` brings `UPLOAD_DIR`, `UPLOAD_MAX_SIZE_BYTES`,
`UPLOAD_ALLOWED_EXTENSIONS` and `UPLOAD_ALLOWED_MIMETYPES`, and
`upload_kwargs()` hands them to the constructor.

!!! tip "Empty and `0` mean no restriction"
    Without `UPLOAD_ALLOWED_EXTENSIONS` in the environment the field is
    `set()`, and `UPLOAD_MAX_SIZE_BYTES=0` turns the limit off. `UploadUtils`
    treats `set()` and `0` exactly like `None` — no check —, instead of
    refusing every upload with 415.

## A photo with a declared type — `ImageMediaTypeEnum`

Use this when the file does **not** go through `UploadUtils` (the route reads
the bytes and stores them elsewhere) and the app declares the type in a field:
the enum normalizes the type, refuses the unknown one and checks the bytes.

The members are the image types `sniff_mime` recognizes: `JPEG`, `PNG`,
`GIF`, `BMP`, `WEBP` and `HEIC`.

```python
from pydantic import BaseModel, ValidationError

from tempest_fastapi_sdk import ImageMediaTypeEnum


class EvidencePhotoSchema(BaseModel):
    """The photo the client is about to send."""

    content_type: ImageMediaTypeEnum


photo: EvidencePhotoSchema = EvidencePhotoSchema.model_validate(
    {"content_type": " Image/JPG "},
)
print(photo.content_type)
print(photo.content_type.extension)

try:
    EvidencePhotoSchema.model_validate({"content_type": "image/avif"})
except ValidationError as exc:
    print(exc.errors()[0]["type"])
```

```text
image/jpeg
jpg
enum
```

- Input is matched **case-insensitively** and with surrounding whitespace
  stripped; the aliases `UploadUtils` accepts (`image/jpg`, `image/pjpeg` →
  JPEG, `image/x-png` → PNG) become the canonical member.
- An unknown value is Pydantic's `enum` error — `422` in a route —, and the
  JSON Schema (hence the `openapi.json`) lists the six canonical values.
- `.extension` gives the file extension (`"jpg"`, `"png"`, `"gif"`, `"bmp"`,
  `"webp"`, `"heic"`).

### Accepting only some types — `image_media_type_field`

Use this when not all six types fit: `image_media_type_field` restricts the
field **and** the JSON Schema together, with no hand-written
`field_validator`.

```python
from typing import Annotated

from pydantic import BaseModel, ValidationError

from tempest_fastapi_sdk import ImageMediaTypeEnum, image_media_type_field


class EvidencePhotoSchema(BaseModel):
    """Camera photo: GIF and BMP are left out."""

    content_type: Annotated[
        ImageMediaTypeEnum,
        image_media_type_field(
            ImageMediaTypeEnum.JPEG,
            ImageMediaTypeEnum.PNG,
            ImageMediaTypeEnum.WEBP,
            ImageMediaTypeEnum.HEIC,
            title="Photo type",
        ),
    ]


photo: EvidencePhotoSchema = EvidencePhotoSchema.model_validate(
    {"content_type": "IMAGE/PNG"},
)
print(photo.content_type)

try:
    EvidencePhotoSchema.model_validate({"content_type": "image/gif"})
except ValidationError as exc:
    print(exc.errors()[0]["type"], "|", exc.errors()[0]["msg"])

field: dict[str, object] = EvidencePhotoSchema.model_json_schema()["properties"][
    "content_type"
]
print(field["enum"])
print(field["examples"])
```

```text
image/png
value_error | Value error, image type must be one of: image/jpeg, image/png, image/webp, image/heic
['image/jpeg', 'image/png', 'image/webp', 'image/heic']
['image/jpeg']
```

- The enum's normalization runs **before** the restriction: `IMAGE/PNG` and
  `image/x-png` still become `ImageMediaTypeEnum.PNG`.
- A known type outside the list is a `value_error` (`422` in a route) naming
  the allowed ones; an unknown type is still the `enum` error.
- The JSON Schema lists **only** the allowed ones. `description` and
  `examples` come from the list when you pass none (the example is the first
  allowed type).
- With no arguments, `image_media_type_field()` accepts all six and only fills
  in title, description and example.

!!! warning "The OpenAPI gains an orphan `ImageMediaTypeEnum` — `prune_orphan_enum_schemas`"
    FastAPI publishes a component for **every** field annotated with an
    `Enum`, even when the field's schema carries the restricted enum inline.
    `components.schemas.ImageMediaTypeEnum` ships with all six members and no
    `$ref` pointing to it, and the client generator creates a dead enum. One
    line in `create_app` drops the enums nobody references (`tempest new`
    already generates it):

    ```python
    from fastapi import FastAPI

    from tempest_fastapi_sdk import (
        prune_orphan_enum_schemas,
        register_exception_handlers,
    )

    app = FastAPI()
    register_exception_handlers(app)
    prune_orphan_enum_schemas(app)
    ```

    Only enum components with no `$ref` in the document go; an enum used by
    another field stays, and an object schema is never touched.

### Checking the bytes against the declared type

Use `.matches(data)` to ask `sniff_mime` what the bytes **are** and compare it
with the type the client declared.

```python
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Form, UploadFile
from pydantic import BaseModel

from tempest_fastapi_sdk import (
    ImageMediaTypeEnum,
    InvalidFileTypeException,
    read_upload_capped,
    register_exception_handlers,
)

MAX_PHOTO_BYTES: int = 10 * 1024 * 1024


class PhotoResponseSchema(BaseModel):
    """Where the photo was stored, and as what."""

    key: str
    content_type: ImageMediaTypeEnum


app = FastAPI()
register_exception_handlers(app)


@app.post("/photos")
async def upload_photo(
    content_type: Annotated[ImageMediaTypeEnum, Form()],
    file: UploadFile,
) -> PhotoResponseSchema:
    """Accept a photo only when its bytes are the type it declares."""
    data: bytes = await read_upload_capped(file, max_bytes=MAX_PHOTO_BYTES)
    if not content_type.matches(data):
        raise InvalidFileTypeException(field="content_type")
    key: str = f"photos/{uuid4()}.{content_type.extension}"
    return PhotoResponseSchema(key=key, content_type=content_type)
```

| Request | Response |
| --- | --- |
| HEIC declared as `" Image/HEIC "` | `200`, key `photos/<uuid>.heic` |
| JPEG declared as `image/jpg` | `200` |
| JPEG declared as `image/png` | `415 INVALID_FILE_TYPE` |
| `image/avif` | `422` from validation, before the function body |
| Photo above `MAX_PHOTO_BYTES` | `413 UPLOAD_TOO_LARGE`, ceiling in `details.max_bytes` (up to 0.348.0 it was `422 VALIDATION_ERROR`) |

`read_upload_capped` stops reading at the chunk that crosses the ceiling.

!!! info "HEIC from the bytes"
    A HEIC file is an ISO-BMFF `ftyp` box: `ftyp` at bytes 4 to 8 and the
    major brand at bytes 8 to 12. `sniff_mime` recognizes the HEVC brands in
    `HEIC_BRANDS` (`heic`, `heix`, `heim`, `heis`, `hevc`, `hevx`, `hevm`,
    `hevs`, ported from libheif's `heif_get_file_mime_type`). The neutral
    brands `mif1`/`msf1` (`HEIF_STRUCTURAL_BRANDS`) count as HEIC only when a
    compatible brand of the box is HEVC — which is how an AVIF, which uses
    the same container and often declares `mif1`, does **not** pass as HEIC.
    Pass at least the first 32 bytes so the compatible brands fit.

!!! tip "User photos: strip the metadata before storing"
    `content_type.matches(data)` checks the type, but the photo still carries
    its EXIF (GPS, device). To store or forward it without that, see
    [Image metadata](image-metadata.md).

## Switch the backend from settings

Choose the constructor argument from a flag on your `Settings` — no manual
pluggable backend needed.

```python
# src/api/dependencies/resources.py
from tempest_fastapi_sdk import AsyncMinIOClient, UploadUtils

from src.core.settings import settings

if settings.UPLOAD_BACKEND == "minio":
    uploads = UploadUtils(AsyncMinIOClient(**settings.storage_kwargs()))
else:
    uploads = UploadUtils(settings.UPLOAD_DIR)
```

`UPLOAD_BACKEND` is a field on **your** `Settings`; the SDK only brings the
`UPLOAD_*` fields of `UploadSettings`.

## Swap a file (avatar, attachment) — `replace`

Use this when the user sends a new file and the old one has to go, without
risking being left with none.

```python
from fastapi import UploadFile

from tempest_fastapi_sdk import UploadUtils

uploads = UploadUtils("./uploads")


async def replace_avatar(user_id: str, old_key: str | None, file: UploadFile) -> str:
    """Store the new avatar and delete the previous one.

    Args:
        user_id (str): Owner of the avatar; becomes the file name.
        old_key (str | None): Key stored in the database today (`None` on the first upload).
        file (UploadFile): The new file.

    Returns:
        str: The new key, to store in place of the old one.
    """
    new_key = await uploads.replace(old_key, file, filename=f"{user_id}.jpg")
    return str(new_key)
```

`replace` **stores the new file first** and only then deletes the old one. If
validation rejects the new file, the old one stays **intact**; with
`old_key=None` it only saves. Everything goes through the **same backend**, so
there is no way to save on one and delete on the other. To delete without
replacing, `await uploads.delete(key)` returns `True`/`False`.

To **download** what was uploaded (local or MinIO), use
[`DownloadUtils`](downloads.md) — it accepts the same backend in its
constructor.

## Write a stream straight to the backend — `write_stream` and `UploadResult`

Use this when the bytes do not come from a form (a proxy of another service, a
job's output, a file generated on the fly): `write_stream` consumes an
`AsyncIterator[bytes]` without loading the whole file into memory.

```python
from collections.abc import AsyncIterator

from tempest_fastapi_sdk import LocalUploadStorage, UploadResult

storage: LocalUploadStorage = LocalUploadStorage("./var/uploads")


async def store_report(chunks: AsyncIterator[bytes]) -> UploadResult:
    """Persist a generated report without buffering it in memory."""
    return await storage.write_stream(
        "reports/2026-07.csv",
        chunks,
        content_type="text/csv",
        max_size_bytes=50 * 1024 * 1024,
    )
```

| Field | Type | Content |
| --- | --- | --- |
| `key` | `str` | Canonical identifier — relative path locally, S3 key on MinIO |
| `size` | `int` | Bytes written |
| `path` | `Path` or `None` | On-disk path, only when the backend writes to a filesystem |
| `url` | `str` or `None` | Download URL (presigned or static), when the backend can build one |

`max_size_bytes=` cuts the stream when it crosses the ceiling
(`FileTooLargeException`), and `validator=` inspects the first bytes — both
checks run **while** writing.

## Large file: presigned PUT straight to the bucket

Above ~50 MB, keep the file from passing through your process: the client does
a `PUT` straight to MinIO through a presigned URL. See
[Storage MinIO/S3](storage.md#presigned-url-direct-browser-upload).

## Migrating from < v0.41.0

In v0.41.0 the backend moved to the constructor and the per-call
`save(file, storage=...)` **was removed**.

- `UploadUtils("./dir")` stays the same (local disk).
- `UploadUtils("./tmp")` + `save(file, storage=MinIOUploadStorage(client))`
  → becomes `UploadUtils(client)` + `save(file)`.
- `save()` now returns the **key** (relative), not an absolute path — store
  the key and use `DownloadUtils.download(key)` to serve it.
- `utils.delete(path)` (sync) → `await utils.delete(key)` (async).

## Recap

- `UploadUtils` picks the backend **once, in the constructor**: a directory
  stores on disk, an `AsyncMinIOClient` stores in the bucket.
- `save()` returns the relative **key** (`Path`); that is what goes into the
  database.
- Refusals: type, MIME, magic bytes and `content_validator` → `415
  INVALID_FILE_TYPE`; size → `413 FILE_TOO_LARGE`; no token on a route with
  `current_user_dependency()` → `401`.
- A file served back: `allowed_extensions` + `verify_magic_bytes=True` +
  `allowed_mimetypes`.
- Bytes outside `UploadUtils`: `ImageMediaTypeEnum` on the field and
  `.matches(data)`.
- `replace` swaps without leaving an orphan; `write_stream` stores without
  loading into memory; above ~50 MB, presigned PUT.
