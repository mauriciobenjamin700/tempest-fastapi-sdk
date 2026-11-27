---
description: "Use to accept uploads that resume where they stopped (tus protocol) with `make_tus_router`; covers `on_complete`, ownership, `Idempotency-Key`, `S3TusStore`, expiry, limits and CORS."
---

# Resumable uploads (tus)

A field technician takes ten photos with no signal; when the network comes back they go
up — and if the connection drops halfway through the third one, the upload continues
from the byte where it stopped, not from zero. In the browser that is
`tempest-react-sdk`'s `createResumableUpload`, which speaks the
[tus 1.0.0](https://tus.io/protocols/resumable-upload) protocol. This recipe builds the
server side with `make_tus_router`.

!!! info "No new extra"
    The router and `LocalTusStore` use only what the base install already ships
    (FastAPI and the stdlib). `S3TusStore` over `AsyncMinIOClient` needs the `[minio]`
    extra.

## The minimal example

```python
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI

from tempest_fastapi_sdk import (
    LocalTusStore,
    TusStore,
    TusUpload,
    make_tus_router,
    register_exception_handlers,
)


async def current_user_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000001")


async def photo_received(upload: TusUpload, store: TusStore, /) -> None:
    print("upload complete:", upload.id, upload.filename, upload.length)


store = LocalTusStore(Path("var/tus"))
app = FastAPI()
register_exception_handlers(app)
app.include_router(
    make_tus_router(
        store,
        current_user_id=current_user_id,
        on_complete=photo_received,
    ),
)
```

Replace `current_user_id` above with the authentication dependency your service already
uses (the one that returns the signed-in user's id). On the frontend:

```ts
const done = await createResumableUpload({
  endpoint: "/api/uploads",
  file,
  metadata: { filename: file.name },
  getToken: auth.getToken,
}).start();
```

The default `prefix` is `/api/uploads`, the same `endpoint` as the `tempest-react-sdk`
examples.

## What the client sends — and what the router answers

The implemented subset is exactly what `createResumableUpload` sends. Measured two ways:
reading `src/http/resumable-upload.ts` and its tests, and running the real client
(bundled from source with `esbuild`, on Node 24, over the `fetch` transport) against this
router: a 1 MiB file in 256 KiB chunks, paused halfway, resumed by a second handle — as
after a reload — with the `sha256` of the file on disk equal to the client's.

| Step | Client request | Router response |
| --- | --- | --- |
| Create | `POST /api/uploads` + `Tus-Resumable`, `Upload-Length`, `Idempotency-Key`, `Upload-Metadata` (when there is metadata) | `201` + relative `Location` (`/api/uploads/<id>`) + `Upload-Expires` |
| Probe | `HEAD /api/uploads/<id>` + `Tus-Resumable` | `200` + `Upload-Offset`, `Upload-Length`, `Cache-Control: no-store` |
| Write | `PATCH /api/uploads/<id>` + `Tus-Resumable`, `Upload-Offset`, `Content-Type: application/offset+octet-stream`, body = chunk | `204` + the new `Upload-Offset` |
| Discard | `DELETE /api/uploads/<id>` + `Tus-Resumable` (on `abort({ discard: true })`) | `204` |

Plus what the client adds depending on its options: `Authorization: Bearer` (with
`getToken`), `X-CSRF-Token` on `POST`, `PATCH` and `DELETE` — never on `HEAD` — (with
`csrf`), and the static `headers`. The router reads none of them: authentication is your
`current_user_id` dependency, and CSRF is the app's `CSRFMiddleware`.

Left out because the client never sends them: `OPTIONS` discovery,
`Upload-Defer-Length`, a body on the `POST` (*creation-with-upload*), *checksum*,
*concatenation* and `X-HTTP-Method-Override`.

??? note "Why `Location` is relative"
    The client resolves `Location` against the URL the `POST` went to (RFC 9110). A
    relative path needs no knowledge of the host and scheme the proxy exposes; behind a
    `root_path` (`/svc`), `Location` comes out as `/svc/api/uploads/<id>` — covered in
    `tests/storage/tus/test_router.py`.

## The finished file: `on_complete`

When the last byte arrives, the router calls `on_complete(upload, store)` **once per
upload, inside the request of the last `PATCH`**, under the upload's lock, before the
`204`. That is where the service registers the photo or moves the file:

```python
import shutil
from pathlib import Path

from tempest_fastapi_sdk import LocalTusStore, TusStore, TusUpload

ARCHIVE = Path("var/photos")


async def archive_photo(upload: TusUpload, store: TusStore, /) -> None:
    assert isinstance(store, LocalTusStore)
    name = upload.filename or "photo.bin"
    shutil.move(store.data_path(upload.id), ARCHIVE / f"{upload.id}-{name}")
    await store.delete(upload.id)
```

- `upload.metadata` holds the decoded `Upload-Metadata` (base64 → UTF-8), as the client
  sent it — `nota-ação.webm` arrives with its accent.
- `upload.filename` is the **sanitized** name: only the last path component, letters,
  digits and `-_. ()`. `../../etc/passwd` becomes `passwd`. Use it, never
  `metadata["filename"]`, to build a path.
- `store.stream(upload.id)` reads the bytes from any store; `LocalTusStore.data_path` and
  `S3TusStore.object_key` give the path and the key, to move without copying through the
  process.
- `await store.delete(upload.id)` inside the callback is allowed: the router does not
  recreate the record, and the next `HEAD` answers `404`. The example above runs in
  `tests/storage/tus/test_hardening.py`, read straight from this page.
- A `PATCH` to an upload that is already complete — the empty one too, with
  `Upload-Offset` equal to the length — answers `409 TUS_UPLOAD_COMPLETED` without
  writing anything or calling the callback again. `createResumableUpload` handles every
  `409` by re-reading the offset with `HEAD` before the next attempt
  (`RESYNCABLE_STATUSES` in `resumable-upload.ts`, read in the code), and with the offset
  equal to the length the upload finishes.

!!! warning "A callback that raises"
    The `PATCH` answers `500`, and the upload stays complete with `notified = False`. The
    client does not retry: the next `HEAD` already shows every byte, so for it the upload
    is done. Find those uploads with
    `[u for u in await store.list_uploads() if u.completed and not u.notified]` and
    reprocess them — covered in `test_failing_on_complete_leaves_the_upload_complete_and_unnotified`.

## Upload owner

The `str()` of what `current_user_id` resolves becomes the owner, and the dependency must
return the user's **id** as `str`, `int` or `UUID`: any other type (the user model, a
claims dict) raises `TypeError` — the `str()` of an object can change between requests or
collide between users. Only the same value can
probe, continue or delete that upload; anyone else gets `404 TUS_UPLOAD_NOT_FOUND` — the
same `404` as an id that does not exist, so it never confirms the id is real.

Without `current_user_id`, the upload is anonymous and the URL is the only credential.
The id carries 192 random bits (`secrets.token_urlsafe(24)`, 32 characters), and the store
treats any id outside `[A-Za-z0-9_-]{16,128}` as absent: there is no `/`, `.` or `\`, so a
`LocalTusStore` path never leaves its directory.

## `Idempotency-Key` on creation

The client generates one key per upload and repeats it when the `201` is lost. The router
keeps `sha256(owner + key) → id` in the store itself:

- same key, same owner, same `Upload-Length` → the **same** `Location`;
- same key from another owner → another upload (the key never hands out someone else's URL);
- same key, same owner, another `Upload-Length` → `422 TUS_IDEMPOTENCY_KEY_REUSED`.

The mapping lives in the store (a file on disk, an object in the bucket), so it holds
after a restart.

Without `current_user_id` (anonymous mode) the key is **ignored**: with no owner, every
anonymous caller would share one key space, and whoever repeated someone else's key would
get their URL. The client generates the key with `crypto.randomUUID()` and, where the
browser does not offer it (outside a secure context, that is, without HTTPS), falls back
to a UUID built with `Math.random()`, which is not cryptographic randomness
(`src/http/idempotency.ts` in `tempest-react-sdk`). In anonymous mode, a creation retried
after a lost `201` leaves an orphan upload, which `purge_expired()` deletes. This route does not need `IdempotencyMiddleware`: that one keys the
cached response on a digest of the credentials, and a token refreshed between the attempt
and the retry no longer matches.

## MinIO / S3: `S3TusStore`

```python
from tempest_fastapi_sdk import AsyncMinIOClient, S3TusStore, make_tus_router

client = AsyncMinIOClient(
    endpoint="localhost:9000",
    access_key="minioadmin",
    secret_key="minioadmin",
    default_bucket="uploads",
)
router = make_tus_router(S3TusStore(client, prefix="tus/"))
```

S3 has no *append*, and a multipart part must be at least 5 MiB (except the last) — but
the client picks the chunk size, and `tempest-react-sdk`'s "Offline field app" recipe
sends 256 KiB. So each accepted `PATCH` becomes one small object, keyed by the offset it
starts at, and the file is assembled once, when the last byte arrives:

| Key | Content |
| --- | --- |
| `tus/<id>/info.json` | the record (offset, length, owner, expiry) |
| `tus/<id>/chunks/<20-digit offset>` | one object per `PATCH` |
| `tus/<id>/data` | the finished file, written at the end; the chunks are deleted |
| `tus/_idempotency/<digest>` | the id an `Idempotency-Key` created |

Everything lives in the bucket: another process, or another replica, sees the same
uploads.

The complete record is saved **before** the chunks are deleted. If the assembly fails
half-way, the `PATCH` answers `500`, the record stays at the previous offset and the
retried last chunk assembles again; if only deleting the chunks fails, the upload is
already complete and the leftover chunks go with `store.delete(id)` — both cases with an
injected failure in `test_hardening.py`.

`S3TusStore(min_chunk_size=...)` (64 KiB by default) refuses a chunk below it with
`400 TUS_CHUNK_TOO_SMALL`, except the one that ends the upload: every chunk is an object,
and without a floor a client sending 1 byte per `PATCH` would create one object per byte.
At 64 KiB a 1 GiB upload has at most 16 384 chunks; the client sends 5 MiB by default and
256 KiB in the field recipe.

!!! warning "The cost"
    Every byte is written to the bucket **twice** (chunk, then file) and read once. And
    the request of the last `PATCH` waits for the assembly, which spools the file through
    a temporary file on the server's disk (`SpooledTemporaryFile`, 8 MiB in memory before
    it goes to disk). For photos and recordings of a few MiB that is invisible; for
    multi-gigabyte files the last `PATCH` takes the time to read and rewrite the whole
    file — raise the proxy timeout, or prefer `LocalTusStore` on a shared disk. The
    temporary disk adds up per concurrent assembly: ten 1 GiB uploads finishing at once
    take up to 10 GiB in the server's temporary directory.

Measured against a real S3 server (SeaweedFS's S3 gateway in a container — the
`minio/minio` image answered *pull access denied* on this machine on 2026-11-22): 6 MiB
in 256 KiB chunks, the server killed with `SIGKILL` halfway, a new process resumes from
the offset stored in the bucket, and the final `data` object is byte-for-byte the upload,
with only `data` and `info.json` left (`tests/storage/tus/test_s3_live.py`,
`make test-docker`).

## Expiry and cleanup

An incomplete upload expires `expiration` after creation or after its last `PATCH` that
stored bytes (24 h by default) — an empty `PATCH` does not renew it; the deadline goes out as `Upload-Expires`. Past it, `HEAD` and
`PATCH` answer `410 TUS_UPLOAD_EXPIRED`, and the client starts over. A **complete**
upload never expires: its bytes already belong to the service.

Deleting what expired is up to you, on a schedule:

```python
from tempest_fastapi_sdk import LocalTusStore

store = LocalTusStore("var/tus")


async def sweep_uploads() -> list[str]:
    return await store.purge_expired()
```

`purge_expired()` returns the deleted ids (`[]` when nothing expired). Each upload is
re-read under the same per-upload lock the router uses, and one with a request in
progress is skipped. Hook it to a `TaskQueue` periodic task or a cron.

## Per-owner limits

Without a limit, a signed-in user creates uploads until the disk or the bucket is full.
Two router parameters close that, counting the owner's **unfinished, unexpired** uploads:

```python
from tempest_fastapi_sdk import LocalTusStore, make_tus_router

router = make_tus_router(
    LocalTusStore("var/tus"),
    max_uploads_per_owner=20,
    max_bytes_per_owner=2 * 1024 * 1024 * 1024,
)
```

Past either, the creation answers `403 TUS_QUOTA_EXCEEDED` with `details.limit`
(`"uploads"` or `"bytes"`) and `details.max`. Both default to `None`. They apply only with
`current_user_id`: in anonymous mode every caller would share one quota and one of them
would exhaust it for everyone, so none is enforced. An empty upload, which completes on
creation, never counts.

!!! warning "Cost of the check"
    Every creation with a limit reads every record in the store (`list_uploads()`). Keep
    the store swept with `purge_expired()`; with tens of thousands of live uploads this
    weighs on every `POST`.

## Concurrency and replicas

A `PATCH` takes the upload's lock **before** reading its body. A second `PATCH` (or
`DELETE`) to the same upload while the first is in progress answers
`409 TUS_UPLOAD_LOCKED` at once, without reading the body — through the same `409`
handling, the client re-reads with `HEAD` and retries after its backoff — so an upload never has two bodies spooled.
Measured with the interleaving forced (the first request held inside the write): 1 write,
the second at `409` with its body never read, the file holding only the first's bytes.
Without the lock, the same test shows both answering `204` for the same range
(`tests/storage/tus/test_concurrency.py`). The body goes to a `SpooledTemporaryFile` with
1 MiB in memory; the rest, up to `max_chunk_size`, goes to the temporary disk.

### Mobile networks: a body that stops half-way

A phone that loses signal mid-chunk leaves a half-open connection, with no FIN: the server
waits for the rest of the body until TCP gives up, minutes later — and since the lock is
taken before the body is read, every retry would answer `409 TUS_UPLOAD_LOCKED` until
then. So each piece of the body has a deadline: `body_read_timeout` (30 s by default).
When nothing arrives within it, the `PATCH` answers `408 TUS_BODY_READ_TIMEOUT`, **the
whole chunk is discarded** (the stored offset does not move; what arrived before the
deadline is not kept) and the lock is released. `408` is in the list of statuses
`createResumableUpload` retries (`RETRIABLE_STATUSES` in `errors.ts`), re-reading the
offset with `HEAD` first. An explicit disconnect mid-body stores nothing either: it becomes
an `INFO` log, not a `500`. Both cases run in `tests/storage/tus/test_resilience.py`, with
the app called as raw ASGI.

!!! warning "The lock is per process"
    Two replicas behind a load balancer can still race on one upload. With more than one
    replica, route an upload's requests to the same replica (sticky sessions on the
    `/api/uploads/<id>` path).

A restart, on the other hand, loses nothing: the record is written only after the bytes,
and it is the authority on the offset. Measured in `tests/storage/tus/test_restart.py`:
uvicorn in a subprocess, killed with `SIGKILL` after 192 KiB of 300 KiB; the new process,
built from nothing but the same directory, answers `HEAD` with `Upload-Offset: 196608`
and accepts the rest, and the final file is identical to the upload.

## CORS

A frontend on another origin can only read `Location` and `Upload-Offset` if the backend
exposes them — otherwise `getResponseHeader("Location")` returns `null` and the client
cannot find the upload it created:

```python
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from tempest_fastapi_sdk import TUS_CORS_ALLOW_HEADERS, TUS_CORS_EXPOSE_HEADERS

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.example.com"],
    allow_methods=["POST", "HEAD", "PATCH", "DELETE"],
    allow_headers=list(TUS_CORS_ALLOW_HEADERS),
    expose_headers=list(TUS_CORS_EXPOSE_HEADERS),
)
```

## Errors

Errors use the SDK envelope (`detail`/`code`/`details`). Every response of the tus routes
carries `Tus-Resumable: 1.0.0`, refusals included — the router's and a `401`/`403` raised
by your `current_user_id` dependency. The exception is an unhandled `500`: the app's
handler answers it, outside the route, without the header.

| Status | `code` | When |
| --- | --- | --- |
| `400` | `TUS_HEADER_INVALID` | `Upload-Length` or `Upload-Offset` missing, not a number or longer than 20 digits; `Upload-Metadata` not base64/UTF-8. `details.header` names the header |
| `400` | `TUS_CHUNK_TOO_SMALL` | a chunk that does not end the upload is below the store's `min_chunk_size`; `details.min_bytes` |
| `403` | `TUS_QUOTA_EXCEEDED` | the creation would pass `max_uploads_per_owner` or `max_bytes_per_owner` |
| `404` | `TUS_UPLOAD_NOT_FOUND` | unknown, malformed or someone else's id |
| `408` | `TUS_BODY_READ_TIMEOUT` | the `PATCH` body went longer than `body_read_timeout` without arriving; nothing is stored |
| `409` | `TUS_OFFSET_MISMATCH` | `Upload-Offset` differs from the stored one; `details.offset` is the server's |
| `409` | `TUS_UPLOAD_COMPLETED` | `PATCH` to an upload that already has every byte; nothing is stored |
| `409` | `TUS_UPLOAD_LOCKED` | another `PATCH`/`DELETE` of the same upload is in progress |
| `410` | `TUS_UPLOAD_EXPIRED` | incomplete upload past `Upload-Expires` |
| `412` | `TUS_VERSION_UNSUPPORTED` | `Tus-Resumable` missing or not `1.0.0`; the response carries `Tus-Version` |
| `413` | `TUS_UPLOAD_TOO_LARGE` | `Upload-Length` above `max_size` (with `Tus-Max-Size`), or a `PATCH` body past `Upload-Length` or `max_chunk_size`; nothing is stored |
| `415` | `TUS_CONTENT_TYPE_INVALID` | `PATCH` without `Content-Type: application/offset+octet-stream` |
| `422` | `TUS_IDEMPOTENCY_KEY_REUSED` | the key already created an upload of another size |

## Recap

- `make_tus_router(store, current_user_id=..., on_complete=...)` serves
  `createResumableUpload`: `POST`, `HEAD`, `PATCH`, `DELETE`.
- `LocalTusStore` keeps uploads on disk; `S3TusStore` in the bucket, one object per chunk
  and the assembly at the end (twice the writes, in exchange for accepting any chunk size).
- State survives a restart; the concurrency lock is per process.
- `on_complete` runs once per upload; a `PATCH` to a complete upload is `409`.
- `Idempotency-Key` per owner (ignored without one), per-owner limits, expiry with
  `purge_expired()`, a sanitized file name in `upload.filename`.
