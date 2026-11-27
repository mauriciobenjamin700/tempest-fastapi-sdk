---
description: "Use para um endpoint de avatar com validação e limpeza e um fluxo de reset de senha com `EmailUtils`."
---

# Upload de arquivos e e-mail transacional

Use para um endpoint de avatar com validação e limpeza e um fluxo de reset de senha com `EmailUtils`.

Parte da receita [Camada HTTP](../http.md).

## Upload de arquivos


Endpoint de avatar com validação + limpeza. Requer o extra `[upload]`.

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

`verify_magic_bytes=True` lê os primeiros bytes de cada upload e confirma que o arquivo *realmente é* um dos tipos permitidos — um payload HTML+JS enviado como `image/png` é rejeitado mesmo que sua extensão e header `Content-Type` pareçam válidos. Só ative quando todo formato aceito é um que o `sniff_mime` reconhece (JPEG, PNG, GIF, BMP, WebP, PDF); caso contrário, um upload legítimo mas não-snifável seria recusado. Para controle mais fino, passe um predicado `content_validator` para `save()` (`save(file, content_validator=lambda b: sniff_mime(b) in {"image/png"})`), e passe `filename="..."` para um nome determinístico e endereçável (ex.: `f"{user_id}.jpg"`) em vez do UUID padrão.

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

Adicione `set_avatar` tanto ao service quanto ao controller (o controller fica como um pass-through fino a menos que orquestração seja necessária — ex.: disparar um evento de "avatar atualizado"):

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

`UploadUtils.save()` levanta `FileTooLargeException` (413) ou `InvalidFileTypeException` (415) na rejeição — o exception handler do SDK já retorna o status code certo com um campo `code` na resposta.

#### Servindo o arquivo de volta

Uploads em disco local são melhor servidos por um upstream (nginx / Caddy) para que o FastAPI não fique transmitindo bytes. Para dev:

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

Construa a URL pública no schema de resposta:

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

#### Servindo arquivos privados pela API (`DownloadUtils`)

Quando um arquivo deve ficar **atrás de auth** — faturas, contratos, exames médicos — uma URL pública `/static` o vaza para qualquer um que descubra o caminho. `DownloadUtils` transmite os bytes pelo próprio endpoint, para que os mesmos `Depends(get_current_user)` / checks de permissão que guardam toda outra rota guardem o download também. Nenhum link público é exposto. Não precisa de **nenhum extra** (usa `FileResponse` / `StreamingResponse` do Starlette, que vêm com o FastAPI).

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

Qualquer caminho relativo que escape de `base_dir` (traversal `../`, caminhos absolutos, escapes via symlink) levanta `NotFoundException` (404) em vez de vazar o arquivo — o mesmo 404 que você ganha para um arquivo genuinamente ausente, então callers nunca distinguem "proibido" de "ausente". `file_response` adivinha o tipo MIME pelo nome do arquivo (sobrescreva com `media_type=`), e `as_attachment=False` pede **inline** (ex.: pré-visualizar um PDF no navegador) — atendido só para tipo em `INLINE_SAFE_MEDIA_TYPES`, então HTML e SVG continuam baixando. Toda resposta de download sai com `X-Content-Type-Options: nosniff`, CSP `sandbox` e `Cross-Origin-Resource-Policy: same-site`.

Para payloads construídos na hora — um relatório gerado, um zip em memória, bytes descriptografados — use `stream()` em vez de tocar o disco:

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

`stream()` aceita `bytes` cru, um `Iterable[bytes]` sync ou um `AsyncIterable[bytes]`, então um export grande pode ser entregue pedaço a pedaço sem bufferizar tudo em memória. Ambos os métodos definem um `Content-Disposition` seguro em UTF-8 (nomes de arquivo não-ASCII sobrevivem via o parâmetro `filename*` da RFC 5987); `build_content_disposition()` é exportado se você precisar definir esse header em uma resposta feita à mão.

---


## E-mail transacional


Fluxo de reset de senha usando `EmailUtils` + um JWT de vida curta. Requer o extra `[email]`.

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
