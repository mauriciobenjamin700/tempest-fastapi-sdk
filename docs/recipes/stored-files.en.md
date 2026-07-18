# File on the service — `StoredFileServiceMixin`

An entity almost always carries **one** storage key: the user's avatar, an
event banner, a product cover, an attachment. And every service that owns one
rewrites the same dance by hand:

1. resolve the entity,
2. upload the new file and delete the old one,
3. write the new key onto the model,
4. `commit`,
5. hand out a temporary download URL.

`StoredFileServiceMixin` does this flow **once**, parameterized by the
**field name** — so a single service can manage several file fields without
duplication. It builds on top of [`UploadUtils`](uploads.md) (upload +
validation) and [`AsyncMinIOClient`](storage.md) (presigned URL); it needs the
`[upload]` and `[minio]` extras.

!!! info "Scope: the common case"
    Covers **one key per field → presigned URL**. Thumbnails, variants
    (S/M/L), public/CDN buckets and galleries (one-to-many) are out of scope —
    compose `UploadUtils` directly for those.

## Mixing it into your service

The mixin builds nothing: it reads two collaborators off `self` —
`upload_utils` and `storage`. The service stays in charge of configuration
(size, allowed types, bucket):

```python
from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    BaseService,
    StoredFileServiceMixin,
    UploadUtils,
)

from src.db.models import UserModel
from src.db.repositories import UserRepository
from src.schemas import UserResponseSchema


class UserService(
    BaseService[UserRepository, UserResponseSchema],
    StoredFileServiceMixin[UserModel],
):
    def __init__(
        self,
        repository: UserRepository,
        storage: AsyncMinIOClient,
        upload_utils: UploadUtils,
    ) -> None:
        super().__init__(repository)
        self.storage = storage
        self.upload_utils = upload_utils
```

Base order matters: `BaseService` brings the `repository`; the mixin only
adds the file methods on top.

### How the inheritance works

The service inherits from **two** generic bases at once — this is composition
by multiple inheritance, and each piece has a role:

```python
class UserService(
    BaseService[UserRepository, UserResponseSchema],  # (1) state + CRUD
    StoredFileServiceMixin[UserModel],                # (2) file methods
):
    ...
```

1. **`BaseService[Repo, Response]`** comes **first** in the MRO (method
   resolution order). It defines `__init__(repository)` and holds
   `self.repository` — which is why your `super().__init__(repository)` lands on
   it. Its two generic parameters pin the **repository type** and the
   **response schema**.
2. **`StoredFileServiceMixin[Model]`** comes **after**. It has **no `__init__`
   and no state of its own** — it only stacks `set_file` / `file_url` /
   `file_urls` / `clear_file` on top. Its single generic (`Model`) keeps the
   return type of `set_file`/`clear_file` precise (`UserModel`, not a loose
   `Any`).

!!! info "Why the mixin builds nothing"
    A mixin that created `storage`/`upload_utils` would steal configuration
    (bucket, max size, allowed types) from the service. Instead it **reads the
    collaborators off `self`** via *structural typing* (the `SupportsUpload`
    and `SupportsPresign` Protocols): any object with the right methods works.
    Practical upshot: **importing the mixin does not pull the** `[upload]` /
    `[minio]` **extras** — they only kick in once you actually instantiate an
    `UploadUtils` / `AsyncMinIOClient`.

!!! note "`repository: Any` on the mixin — and mypy"
    The mixin declares `repository: Any` as an annotation only. Without it, mypy
    would flag a **conflicting `repository` field** across the two bases
    (`BaseService` types it as `RepositoryT`). With `Any` on the mixin, the
    concrete base wins and the public methods stay precise via `Model` — no
    `# type: ignore` in your service.

## Swap the file — `set_file`

```python
async def update_profile_picture(
    self, user: UUID | UserModel, image: UploadFile
) -> UserResponseSchema:
    """Upload the new picture, delete the old one, return the profile + URL."""
    updated = await self.set_file(
        user, image, field="profile_picture", subdir="profiles"
    )
    response = await self._map_to_response(updated)
    response.profile_picture_url = await self.file_url(updated.profile_picture)
    return response
```

That's it. Against the ~13 hand-written lines, `set_file` resolves the
entity, calls `replace` (writes the new file **before** deleting the old),
stores the key and commits — in one step.

!!! tip "Safe with the authenticated user"
    `set_file` re-resolves the entity on the request session via
    `repository.resolve()`. If you pass the `UserModel` from
    `get_current_user` (which on mis-wired apps used to be *detached*),
    `resolve` re-attaches it before the write — no
    `InvalidRequestError: Instance is not persistent within this Session`.

## Serve the URL — `file_url`

```python
url = await self.file_url(user.profile_picture)            # 1h lifetime
url = await self.file_url(user.profile_picture, expires=timedelta(minutes=5))
```

Returns `None` when the key is empty, so you can feed the result straight into
a response-schema field without an `if`:

```python
response.profile_picture_url = await self.file_url(updated.profile_picture)
```

## A whole page — `file_urls` *(v0.133.0+)*

A **list** endpoint has to resolve **one key per row** — a page of candidates,
each with its picture. Doing that in a `for` loop with
`await self.file_url(...)` **serializes** the thread hops (each `minio` presign
runs in `asyncio.to_thread`). `file_urls` is the batch counterpart of
`file_url`: it fans the work out at once and returns a `dict` keyed by object
key.

```python
async def _load_profile_picture_from_users(
    self, candidates: list[CandidateResponseSchema]
) -> None:
    """Fill each candidate's ``profile_picture_url`` in one shot."""
    users = [c.user for c in candidates if c.user is not None]
    urls = await self.file_urls([user.profile_picture for user in users])
    for user in users:
        user.profile_picture_url = urls.get(user.profile_picture)
```

`None`/empty keys are **dropped** and duplicates are **collapsed**, so the
`dict` holds one entry per distinct non-empty key. Look each row up with
`urls.get(row.key)` — a row whose key was empty yields `None`, no `if` needed.

!!! info "Concurrency ceiling (`max_concurrency`, default 16)"
    Each presign is dispatched to a default-executor thread. `file_urls` bounds
    how many run at once with an `asyncio.Semaphore`, preserving order — a large
    page cannot saturate the pool. Tune it via
    `file_urls(keys, max_concurrency=32)`.

!!! note "Fail-fast"
    If a presign fails, the whole batch aborts and propagates (default
    `asyncio.gather`) — the same behavior as signing them one by one.

## Remove the file — `clear_file`

```python
updated = await self.clear_file(user, field="profile_picture")
```

Deletes the storage object and nulls the field. When the field is already
empty it is a **no-op**: the entity is returned without a `commit` and without
touching storage.

## Several fields? Same mixin

`field=` is just an argument — one service handles as many fields as you like:

```python
await self.set_file(event, cover, field="cover_image", subdir="events/covers")
await self.set_file(event, banner, field="banner_image", subdir="events/banners")
```

## Recap

- Mix `StoredFileServiceMixin[Model]` into the service (**after**
  `BaseService[Repo, Response]` in the MRO) and expose `upload_utils` +
  `storage`. The mixin holds no state of its own: it reads the collaborators off
  `self` via Protocol, so importing it does not pull the `[upload]`/`[minio]`
  extras.
- `set_file(ref, file, *, field, subdir=...)` → upload, swap old, persist.
  Detach-safe.
- `file_url(key, *, expires=...)` → presigned URL or `None`.
- `file_urls(keys, *, expires=..., max_concurrency=16)` → `dict` key→URL for a
  whole page; drops empty keys, dedups, fail-fast.
- `clear_file(ref, *, field)` → delete + null (no-op when already empty).
- Common case (one key + presigned). For resize/variants/gallery, use
  [`UploadUtils`](uploads.md) directly.
