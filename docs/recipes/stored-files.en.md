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

- Mix `StoredFileServiceMixin[Model]` into the service and expose
  `upload_utils` + `storage`.
- `set_file(ref, file, *, field, subdir=...)` → upload, swap old, persist.
  Detach-safe.
- `file_url(key, *, expires=...)` → presigned URL or `None`.
- `clear_file(ref, *, field)` → delete + null (no-op when already empty).
- Common case (one key + presigned). For resize/variants/gallery, use
  [`UploadUtils`](uploads.md) directly.
