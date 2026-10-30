# Storage on local disk — `LocalObjectStorageClient`

Your service keeps photos, exports and attachments, and until now that meant
an S3 server (MinIO, AWS, R2) running beside the API. For a service that runs
on one machine, that server is one more piece to start, upgrade and watch.

`LocalObjectStorageClient` keeps the objects **in a directory of the server
itself** and answers the same calls as
[`AsyncMinIOClient`](storage.md) — `put_object`, `stat_object`,
`stream_object`, `list_objects`, `remove_object`, `remove_objects`,
`presigned_get_url`, `presigned_put_url`. The rest of the service does not
change.

What changes is the link. A disk has no endpoint the browser can reach, so
the "presigned" link points at **your API**, signed with
[`sign_path`](../../../reference/#tempest_fastapi_sdk.utils.signed_url.sign_path),
and `make_local_storage_router` serves those routes.

No extra needed: only the standard library and the FastAPI the SDK already
brings.

## The first object

```python
import asyncio
from datetime import timedelta

from tempest_fastapi_sdk import LocalObjectStorageClient


async def main() -> None:
    """Write, describe, read and sign an object on disk."""
    storage = LocalObjectStorageClient(
        "var/storage",
        public_url="http://127.0.0.1:8000",
        signing_secret="replace-with-a-long-secret",
    )
    etag = await storage.put_object(
        "reports/2026.txt",
        b"conteudo",
        content_type="text/plain",
    )
    stat = await storage.stat_object("reports/2026.txt")
    stream = await storage.stream_object("reports/2026.txt")
    body = b"".join([chunk async for chunk in stream])
    link = await storage.presigned_get_url(
        "reports/2026.txt", expires=timedelta(minutes=10)
    )
    print(etag, stat.size, stat.content_type, body)
    print(link)


asyncio.run(main())
```

Running it prints something like:

```text
b59853db2f3ef8f156a72e38c30ba7d2 8 text/plain b'conteudo'
http://127.0.0.1:8000/storage/uploads/reports/2026.txt?expires=1792869637&signature=tvsYmCeLaH3y...
```

Piece by piece:

* `"var/storage"` is the root. The client creates `objects/`, `meta/` and
  `tmp/` inside it.
* The default bucket is `"uploads"` (`default_bucket=`). Each bucket is a
  subfolder of `objects/`.
* `put_object` returns the ETag, the MD5 of the bytes, as S3 answers for a
  single-part upload.
* `stat_object` reads size and date from the file, and content type and
  metadata from the metadata file stored beside it.
* The link lasts 10 minutes and only opens that object's route.

!!! info "What it looks like on disk"
    ```text
    var/storage/
      objects/uploads/reports/2026.txt          the bytes
      meta/uploads/reports/2026.txt.meta.json   content type, metadata, ETag
      tmp/                                      writes in flight
    ```
    Every write goes to `tmp/` first and is moved into place with
    `os.replace`. A reader sees the old object or the new one, never half of
    one.

## Serving the links from the API

The link points at `<public_url><route_prefix>/<bucket>/<key>`. Mount the
router **at the app root**, with no extra `prefix`: the signature covers the
path the app sees, and the router already carries the client's
`route_prefix`.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    LocalObjectStorageClient,
    make_local_storage_router,
    register_exception_handlers,
)

storage = LocalObjectStorageClient(
    "var/storage",
    public_url="http://127.0.0.1:8000",
    signing_secret="replace-with-a-long-secret",
    route_prefix="/api/storage",
    max_upload_bytes=5 * 1024 * 1024,
)

app = FastAPI()
register_exception_handlers(app)
app.include_router(make_local_storage_router(storage))


@app.get("/api/photos/{name}")
async def photo_link(name: str) -> dict[str, str]:
    """Return a 15-minute link to the photo (authorize first)."""
    return {"url": await storage.presigned_get_url(f"photos/{name}")}


@app.post("/api/photos/{name}/upload-url")
async def upload_link(name: str) -> dict[str, str]:
    """Return a direct upload link for the photo."""
    return {"url": await storage.presigned_put_url(f"photos/{name}")}
```

The two routes the router mounts:

| Route | What it does |
| --- | --- |
| `GET /api/storage/{bucket}/{key}` | checks the download signature and serves the file with its stored content type, with `Range` (`206`), `If-None-Match` (`304`) and `416` |
| `PUT /api/storage/{bucket}/{key}` | checks the upload signature and stores the raw body with the request's `Content-Type`; answers `200` with the `ETag` |

The app's client uploads like this, with no token: the link is the
credential.

```bash
curl -X PUT -H "Content-Type: image/jpeg" --data-binary @photo.jpg "<upload url>"
```

!!! tip "Download and upload do not mix"
    `GET` and `PUT` links are signed for different purposes. A download link
    used in a `PUT` answers `403 SIGNED_URL_INVALID` and stores nothing, and
    the other way round too.

The router's error responses:

| Situation | Response |
| --- | --- |
| no signature, tampered signature, or another purpose | `403 SIGNED_URL_INVALID` |
| expired link | `403 SIGNED_URL_EXPIRED` |
| object that does not exist (download) | `404 NOT_FOUND` |
| body over `max_upload_bytes` | `413 FILE_TOO_LARGE`, leaving no file |

The cap holds even without `Content-Length`: the body is counted as it
arrives and cut when it passes the limit.

!!! warning "The payload is not signed"
    As with S3, the upload link only fixes **where** the file goes, not what
    it is. Check size and type with `stat_object` (and the first bytes with
    `stream_object`) before trusting the object.

## Capped upload and download with a signed type

The two S3 features the plain `PUT` and `GET` lack exist here under the same
call, so the service switches backend without touching the app's client.

`presigned_post_policy` returns a `PresignedPost` (URL + form fields). The
`POST /api/storage/{bucket}/{key}` route reads the `multipart/form-data` and
applies the signed limits before storing:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    LocalObjectStorageClient,
    make_local_storage_router,
    register_exception_handlers,
)

storage = LocalObjectStorageClient(
    "var/storage",
    public_url="http://127.0.0.1:8000",
    signing_secret="replace-with-a-long-secret",
    route_prefix="/api/storage",
    max_upload_bytes=5 * 1024 * 1024,
)

app = FastAPI()
register_exception_handlers(app)
app.include_router(make_local_storage_router(storage))


@app.post("/api/photos/{name}/form")
async def upload_form(name: str) -> dict[str, object]:
    """Form that accepts only JPEG up to 2 MiB."""
    post = await storage.presigned_post_policy(
        f"photos/{name}",
        max_bytes=2 * 1024 * 1024,
        content_type="image/jpeg",
    )
    return {"url": post.url, "fields": post.fields}


@app.get("/api/photos/{name}/inline")
async def inline_link(name: str) -> dict[str, str]:
    """Link that opens the photo inline, as JPEG."""
    url = await storage.presigned_get_url(
        f"photos/{name}",
        media_type="image/jpeg",
        as_attachment=False,
    )
    return {"url": url}
```

| `POST` case | Answer |
| --- | --- |
| file within `[min_bytes, max_bytes]` | `204`, with the `ETag` |
| over `max_bytes` | `413 FILE_TOO_LARGE`, nothing stored |
| under `min_bytes` | `400 FILE_TOO_SMALL`, nothing stored |
| `Content-Type` or `key` field other than the signed one | `403 SIGNED_URL_INVALID` |
| `max_bytes` (or another limit) edited in the URL | `403 SIGNED_URL_INVALID` |

The limits and the `GET` overrides travel in the query, and the SDK binds
them to the signature (`bound_purpose`): changing one verifies as another
purpose and fails. A link signed with none of them still works as before.

!!! note "Needs the `[upload]` extra"
    Reading the form takes `python-multipart`. The check runs in
    `presigned_post_policy`, so a missing extra breaks where the link is
    issued, not on the user's upload.

!!! info "`max_bytes` up to `max_upload_bytes`"
    The form's cap cannot exceed the store's: asking for more raises
    `ValueError` when signing.

## Picking the backend from settings

`StorageSettings` picks between S3 and disk with `STORAGE_BACKEND`, and
`build_storage_client()` builds the right client:

```python
from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    BaseAppSettings,
    LocalObjectStorageClient,
    StorageSettings,
)


class Settings(StorageSettings, BaseAppSettings):
    """The service's settings."""


settings = Settings()
client: AsyncMinIOClient | LocalObjectStorageClient = settings.build_storage_client()
```

```env
STORAGE_BACKEND=local
STORAGE_LOCAL_DIR=/var/lib/app/storage
STORAGE_LOCAL_PUBLIC_URL=https://api.example.com
STORAGE_LOCAL_ROUTE_PREFIX=/api/storage
STORAGE_LOCAL_SIGNING_SECRET=a-long-random-secret
STORAGE_LOCAL_MAX_UPLOAD_BYTES=5242880
STORAGE_DEFAULT_BUCKET=media
```

| Variable | Default | What for |
| --- | --- | --- |
| `STORAGE_BACKEND` | `s3` | `local` turns the disk on |
| `STORAGE_LOCAL_DIR` | `var/storage` | the store's root |
| `STORAGE_LOCAL_PUBLIC_URL` | `http://127.0.0.1:8000` | start of the links |
| `STORAGE_LOCAL_ROUTE_PREFIX` | `/storage` | path of the routes, as the app sees it |
| `STORAGE_LOCAL_SIGNING_SECRET` | dev placeholder | secret of the links |
| `STORAGE_LOCAL_MAX_UPLOAD_BYTES` | 25 MiB | `PUT` cap |

With `ENV=production` (`EnvironmentSettings`) and `STORAGE_BACKEND=local`, the
boot refuses the development secret, a `STORAGE_LOCAL_PUBLIC_URL` without
`https` and a relative `STORAGE_LOCAL_DIR`. The S3 credentials are no longer
checked, because nothing reads them.

!!! tip "Reusing `JWT_SECRET`"
    The signature derives its key from the secret **and** the purpose, so
    putting the `JWT_SECRET` value in `STORAGE_LOCAL_SIGNING_SECRET` is safe:
    a link never works as a token, and the other way round.

## With `SubjectObjectStorage`

The local client satisfies `SubjectStorageClient`, so per-subject export and
erasure (LGPD) work the same:

```python
import asyncio

from tempest_fastapi_sdk import LocalObjectStorageClient
from tempest_fastapi_sdk.privacy import SubjectObjectStorage


async def main() -> None:
    """Store two files of a subject and erase them all at once."""
    client = LocalObjectStorageClient(
        "var/storage",
        public_url="http://127.0.0.1:8000",
        signing_secret="replace-with-a-long-secret",
    )
    storage = SubjectObjectStorage(client, prefix="users")
    await storage.put(42, "avatar.png", b"png", content_type="image/png")
    await storage.put(42, "export.json", b"{}", content_type="application/json")
    print(await storage.names(42))
    print(await storage.delete_all(42))


asyncio.run(main())
```

```text
['avatar.png', 'export.json']
2
```

A missing object raises `LocalObjectNotFoundError`, a `FileNotFoundError`
with `code = "NoSuchKey"` — the code S3 answers. Code that branches on the S3
error's `code` keeps working.

## Before going to production

!!! warning "One machine only"
    The disk belongs to **one** server. Two API instances on different
    machines do not see each other's files. Once you scale past one machine,
    go back to S3 (`STORAGE_BACKEND=s3`).

!!! warning "API and worker on the same volume"
    Processes on the same machine share the store by mounting **the same
    directory**. In `docker compose`, that is a named volume mounted at the
    same path in the API and in the worker:

    ```yaml
    services:
      api:
        volumes:
          - storage:/var/lib/app/storage
      worker:
        volumes:
          - storage:/var/lib/app/storage

    volumes:
      storage:
    ```

!!! warning "Backup is yours"
    Managed S3 replicates the data; a directory does not. Add
    `STORAGE_LOCAL_DIR` (with `objects/` and `meta/`) to the server's backup
    routine.

??? info "Technical details"
    * Every blocking call runs in `asyncio.to_thread`.
    * An empty key, one starting with `/`, with a `.` or `..` segment, with
      `\` or NUL, or with a segment ending in `.meta.json` is refused with
      `ValueError`. The final path is resolved (following symlinks) and must
      stay under the root.
    * On a disk, keys `a` and `a/b` cannot coexist (`a` cannot be a file and a
      folder at once); writing the second raises `OSError`
      (`FileExistsError` or `IsADirectoryError`, depending on the order). S3
      accepts both.
    * `list_objects(recursive=False)` returns "folders" ending in `/`, as S3
      does. A prefix whose directory part has an empty, `.` or `..` segment,
      or that resolves outside the bucket (a symlink included), returns `[]`
      — on S3 `../` is just characters no key starts with.
    * `remove_objects` returns `AccessDenied` for a permission error and
      `InternalError` for anything else, one `ObjectDeleteError` per key.
    * `X-Accel-Redirect` is not supported by the local client: `GET` always
      goes through the API.

## Recap

* `LocalObjectStorageClient` keeps objects in a directory and speaks the same
  language as `AsyncMinIOClient`.
* The "presigned" links point at the API; `make_local_storage_router` serves
  them, at the app root.
* `STORAGE_BACKEND=local` + `build_storage_client()` swap the backend without
  touching the rest of the service.
* One machine only, a volume shared by the API and the worker, a backup of
  the directory.
