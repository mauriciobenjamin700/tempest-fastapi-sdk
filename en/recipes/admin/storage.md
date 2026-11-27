# Storage browser — view, preview and edit files

Use to open the bucket on an admin screen: list, preview, upload, edit text, rename and delete, with `StorageBrowserService`.

Part of the [Admin site](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/admin.md) recipe.

The admin already writes to object storage (`upload_fields=`), but an
operator had no screen to see what a bucket holds, open a file, or remove a
stray upload. `StorageBrowserService` is that screen: it lists folders,
previews a file, and uploads, replaces, edits text in place, renames and
deletes.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import StorageBrowserService
from tempest_fastapi_sdk.storage import LocalObjectStorageClient

from src.core.settings import settings
from src.db.models import UserModel

app: FastAPI = FastAPI()
db: AsyncDatabaseManager = AsyncDatabaseManager(settings.DATABASE_URL)
store: LocalObjectStorageClient = LocalObjectStorageClient(
    "/var/lib/app/storage",
    public_url="https://api.example.com",
    signing_secret=settings.JWT_SECRET,
)

app.include_router(
    make_admin_router(
        AdminSite(title="Ops"),
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.ADMIN_SECRET_KEY,
        storage=StorageBrowserService(store, label="uploads"),
    )
)
```

MinIO/S3 is the same line: `StorageBrowserService(AsyncMinIOClient(...))`.
The service uses only five calls — `list_objects`, `stat_object`,
`stream_object`, `put_object` and `remove_object` (the `AdminStorageClient`
protocol) — which both SDK clients implement.

That mounts the routes below and a "Storage" entry in the sidebar:

| Route | What it does |
| --- | --- |
| `GET /admin/storage?prefix=` | one folder: sub-folders first, then files with size, type and date; paged |
| `GET /admin/storage/object?key=` | one file: metadata, preview, text editor and the write forms |
| `GET /admin/storage/raw?key=` | the bytes, for the preview (`&download=true` forces a download) |
| `POST /admin/storage/upload` | uploads a file into the current folder |
| `POST /admin/storage/object/replace` | swaps the content, keeping the key and metadata |
| `POST /admin/storage/object/edit` | saves the text editor |
| `POST /admin/storage/object/rename` | moves to another key, folder included |
| `POST /admin/storage/object/delete` | deletes |

Let's go piece by piece.

**Folders do not exist.** In object storage a folder is only the key prefix up
to a `/`. So a folder appears as soon as one file is written under it and
disappears when the last one leaves. On upload, the "Save as" field accepts
`a/b.txt` and creates the folders on the way.

**The preview depends on the type.** Images, video, audio and PDF are
rendered by the browser from the `raw` route. Text (`text/*`, JSON, YAML, XML,
CSV) is shown in the page itself, escaped, in a `<textarea>` editable up to
`max_text_bytes` (256 KiB by default). Anything else offers the download only.

!!! danger "SVG and HTML are never rendered"
    The `raw` route answers on the admin's origin. HTML rendered there runs
    script with the signed-in operator's session, and SVG carries `<script>`
    just the same. So the route grants `inline` only to the types in
    `INLINE_SAFE_MEDIA_TYPES` — SVG and HTML always arrive as `attachment` —
    and every response carries `X-Content-Type-Options: nosniff` and a CSP
    with `sandbox`. In the page, SVG and HTML are shown as escaped source.

**Text editing refuses a concurrent write.** The editor sends back the ETag
it opened at; if the file changed in between, the save is refused with
"changed since you opened it" instead of wiping the other write. And the
`\r\n` the browser sends becomes `\n` when the original held no `\r\n`, so
saving a Unix file unchanged does not rewrite every line.

**Renaming costs one download and one upload.** Object storage has no rename:
the service copies the bytes, type and metadata to the new key and deletes the
old one. A new key that is already taken is refused.

!!! tip "Read-only"
    `StorageBrowserService(store, read_only=True)` keeps listing, preview and
    download, hides the forms and answers `403` to every write. Who may open
    the screen is the `surface_policy`'s call, with `AdminSurfaceEnum.STORAGE` —
    see [SQL console, tasks and logs](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/admin/permissions.md#sql-console-tasks-and-logs-surface_policy).

!!! info "Limits"
    `max_upload_bytes` (25 MiB) applies to upload and replace,
    `max_text_bytes` (256 KiB) to text preview and editing, and `page_size`
    (100) to the listing. A key with an empty, `.` or `..` segment, a
    backslash, a control character or over 1024 bytes is refused before it
    reaches the store. Refusals come back as a red message on the same
    screen.
