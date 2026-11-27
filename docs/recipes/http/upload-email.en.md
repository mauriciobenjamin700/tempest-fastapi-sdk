# File upload and transactional email

Use for an avatar endpoint with validation and cleanup and a password-reset flow with `EmailUtils`.

Part of the [HTTP layer](../http.md) recipe.

## File uploads


Avatar endpoint with validation + cleanup. Requires the `[upload]` extra.

```python
# src/core/storage.py
from tempest_fastapi_sdk import UploadUtils

from src.core.settings import settings


avatar_storage = UploadUtils(
    f"{settings.UPLOAD_DIR}/avatars",
    max_size_bytes=5 * 1024 * 1024,            # 5 MiB
    allowed_extensions={"png", "jpg", "jpeg", "webp"},
    allowed_mimetypes={"image/png", "image/jpeg", "image/webp"},
    verify_magic_bytes=True,                   # sniff bytes, reject polyglots
)
```

`verify_magic_bytes=True` reads the first bytes of each upload and confirms the file *really is* one of the allowed types — an HTML+JS payload sent as `image/png` is rejected even though its extension and `Content-Type` header look valid. Only enable it when every accepted format is one `sniff_mime` recognizes (JPEG, PNG, GIF, BMP, WebP, PDF); otherwise a legitimate but unsniffable upload would be refused. For finer control, pass a `content_validator` predicate to `save()` (`save(file, content_validator=lambda b: sniff_mime(b) in {"image/png"})`), and pass `filename="..."` for a deterministic, addressable name (e.g. `f"{user_id}.jpg"`) instead of the default UUID.

```python
# src/api/routers/users.py (extension)

from uuid import UUID

from fastapi import APIRouter, Depends, UploadFile

from tempest_fastapi_sdk import ForbiddenException

from src.api.dependencies import get_user_controller
from src.api.dependencies.auth import get_current_user
from src.controllers.user import UserController
from src.core.storage import avatar_storage
from src.db.models import UserModel
from src.schemas import UserResponseSchema

router = APIRouter()


@router.post("/{user_id}/avatar", response_model=UserResponseSchema)
async def upload_avatar(
    user_id: UUID,
    file: UploadFile,
    current: UserModel = Depends(get_current_user),
    controller: UserController = Depends(get_user_controller),
) -> UserResponseSchema:
    if current.id != user_id:
        raise ForbiddenException(message="Só pode editar o próprio avatar")
    path = await avatar_storage.save(file, subdir=str(user_id))
    return await controller.set_avatar(user_id, str(path))
```

Add `set_avatar` to both the service and the controller (the controller stays a thin pass-through unless orchestration is needed — e.g. firing an "avatar updated" event):

```python
# src/services/user.py

from uuid import UUID

from tempest_fastapi_sdk import UploadUtils

from src.schemas import UserResponseSchema

avatar_storage = UploadUtils(source="./uploads/avatars")


class UserService:
    async def set_avatar(self, user_id: UUID, path: str) -> UserResponseSchema:
        user = await self.repo.get_by_id(user_id)
        # Delete previous file when replacing.
        if user.avatar_path and user.avatar_path != path:
            await avatar_storage.delete(user.avatar_path)
        user.avatar_path = path
        user = await self.repo.update(user)
        return self.repo.map_to_response(user)


# src/controllers/user.py
class UserController:
    async def set_avatar(self, user_id: UUID, path: str) -> UserResponseSchema:
        return await self.service.set_avatar(user_id, path)
```

`UploadUtils.save()` raises `FileTooLargeException` (413) or `InvalidFileTypeException` (415) on rejection — the SDK's exception handler already returns the right status code with a `code` field on the response.

#### Serving the file back

Local-disk uploads are best served by an upstream (nginx / Caddy) so FastAPI doesn't stream bytes. For dev:

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.core.settings import settings

app = FastAPI()


app.mount(
    "/static/uploads",
    StaticFiles(directory=settings.UPLOAD_DIR),
    name="uploads",
)
```

Construct the public URL in the response schema:

```python
from pydantic import EmailStr, field_validator

from tempest_fastapi_sdk import BaseResponseSchema


class UserResponseSchema(BaseResponseSchema):
    name: str
    email: EmailStr
    avatar_url: str | None = None

    @field_validator("avatar_url", mode="before")
    @classmethod
    def _absolute_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # avatar_path stored as relative path → public URL
        return f"/static/uploads/{value}"
```

#### Serving private files through the API (`DownloadUtils`)

When a file must stay **behind auth** — invoices, contracts, medical scans — a public `/static` URL leaks it to anyone who learns the path. `DownloadUtils` streams the bytes through the endpoint itself, so the same `Depends(get_current_user)` / permission checks that guard every other route guard the download too. No public link is ever exposed. It needs **no extra** (uses Starlette's `FileResponse` / `StreamingResponse`, which ship with FastAPI).

```python
# src/core/storage.py
from tempest_fastapi_sdk import DownloadUtils

from src.core.settings import settings


invoice_files = DownloadUtils(f"{settings.UPLOAD_DIR}/invoices")
```

```python
# src/api/routers/invoices.py

from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from tempest_fastapi_sdk import ForbiddenException

from src.api.dependencies import get_invoice_controller
from src.api.dependencies.auth import get_current_user
from src.controllers.invoice import InvoiceController
from src.core.storage import invoice_files
from src.db.models import UserModel

router = APIRouter()


@router.get("/{invoice_id}/file")
async def download_invoice(
    invoice_id: UUID,
    current: UserModel = Depends(get_current_user),
    controller: InvoiceController = Depends(get_invoice_controller),
) -> FileResponse:
    invoice = await controller.get_by_id(invoice_id)
    if invoice.owner_id != current.id:
        raise ForbiddenException(message="Fatura de outro usuário")
    # base_dir confines the read — a stored "../../etc/passwd" path 404s.
    return invoice_files.file_response(
        invoice.file_path,                 # relative to base_dir
        filename=f"fatura-{invoice.number}.pdf",
        as_attachment=True,                # force a download dialog
    )
```

Any relative path that escapes `base_dir` (`../` traversal, absolute paths, symlink escapes) raises `NotFoundException` (404) instead of leaking the file — the same 404 you get for a genuinely missing file, so callers never distinguish "forbidden" from "absent". `file_response` guesses the MIME type from the filename (override with `media_type=`), and `as_attachment=False` asks for **inline** (e.g. preview a PDF in-browser) — granted only to a type in `INLINE_SAFE_MEDIA_TYPES`, so HTML and SVG still download. Every download response carries `X-Content-Type-Options: nosniff`, a `sandbox` CSP and `Cross-Origin-Resource-Policy: same-site`.

For payloads built on the fly — a generated report, an in-memory zip, decrypted bytes — use `stream()` instead of touching disk:

```python
import io
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.controllers import get_invoice_controller
from src.controllers import InvoiceController
from src.core.storage import invoice_files
from src.db.models import UserModel

router = APIRouter()


@router.get("/{invoice_id}/receipt.csv")
async def download_receipt(
    invoice_id: UUID,
    current: UserModel = Depends(get_current_user),
    controller: InvoiceController = Depends(get_invoice_controller),
) -> StreamingResponse:
    csv_bytes: bytes = await controller.render_receipt_csv(invoice_id, current.id)
    return invoice_files.stream(
        csv_bytes,                         # bytes, or a (sync/async) byte generator
        filename="recibo.csv",
    )
```

`stream()` accepts raw `bytes`, a sync `Iterable[bytes]`, or an `AsyncIterable[bytes]`, so a large export can be yielded chunk-by-chunk without buffering it all in memory. Both methods set a UTF-8-safe `Content-Disposition` (non-ASCII filenames survive via the RFC 5987 `filename*` parameter); `build_content_disposition()` is exported if you need to set that header on a hand-rolled response.

---


## Transactional email


Password reset flow using `EmailUtils` + a short-lived JWT. Requires the `[email]` extra.

```python
# src/core/mailer.py
from tempest_fastapi_sdk import EmailUtils

from src.core.settings import settings


mailer = EmailUtils(
    host=settings.SMTP_HOST,
    port=settings.SMTP_PORT,
    from_addr=settings.SMTP_FROM_ADDR,
    username=settings.SMTP_USERNAME,
    password=settings.SMTP_PASSWORD,
    use_starttls=True,
)
```

```python
# src/services/password_reset.py
from datetime import timedelta
from uuid import UUID

from tempest_fastapi_sdk import (
    EmailUtils,
    InvalidTokenException,
    JWTUtils,
    PasswordUtils,
)

from src.db.repositories import UserRepository


class PasswordResetService:
    def __init__(
        self,
        repo: UserRepository,
        tokens: JWTUtils,
        mailer: EmailUtils,
    ) -> None:
        self.repo = repo
        self.tokens = tokens
        self.mailer = mailer

    async def request_reset(self, email: str) -> None:
        """Send a password-reset link to `email`.

        Always returns silently — don't reveal whether the email
        is registered or not (avoids account enumeration).
        """
        user = await self.repo.get_or_none({"email": email})
        if user is None:
            return
        token = self.tokens.encode(
            {"sub": str(user.id), "typ": "password_reset"},
            ttl=timedelta(minutes=15),
        )
        reset_url = f"https://my-app.com/reset-password?token={token}"
        await self.mailer.send(
            to=user.email,
            subject="Reset your password",
            body=f"Click here to reset your password: {reset_url}",
            html=f'<p>Click <a href="{reset_url}">here</a> to reset.</p>',
        )

    async def consume_reset(
        self,
        token: str,
        new_password: str,
        passwords: PasswordUtils,
    ) -> None:
        # `decode` raises InvalidTokenException / ExpiredTokenException
        # (both 401). Caught by the SDK handler.
        payload = self.tokens.decode(token)
        if payload.get("typ") != "password_reset":
            raise InvalidTokenException()
        user = await self.repo.get_by_id(UUID(payload["sub"]))
        user.password_hash = passwords.hash(new_password)
        await self.repo.update(user)
```
