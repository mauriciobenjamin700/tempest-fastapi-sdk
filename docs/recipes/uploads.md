# Uploads — disco local + S3 / MinIO

`UploadUtils` valida e grava o `UploadFile` do FastAPI. O backend é escolhido
**uma vez, no construtor**: uma **pasta** grava em disco local, um
**`AsyncMinIOClient`** grava num bucket S3/MinIO, e o resto do código não muda.
Requer o extra `[upload]` (e `[minio]` para MinIO).

!!! tip "Validação fica no `UploadUtils`"
    Extensão, MIME, tamanho, magic bytes e `content_validator` rodam no
    `UploadUtils` antes de qualquer byte chegar ao backend — o storage só
    recebe dados já validados.

!!! info "Google Drive"
    Para gravar numa pasta (ou Shared Drive) do Google Drive, passe um
    `GoogleDriveUploadStorage` em `backend=` — veja a receita
    [Google Drive (uploads)](google-drive.md).

Quer ver o upload dentro de um serviço inteiro (settings, migrations, auth,
teste)? Veja [Serviço completo](full-service.md).

## Referência rápida

Use quando precisa da assinatura exata, do retorno ou do status HTTP de cada
recusa.

### Construtor `UploadUtils(...)`

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

| Parâmetro | O que faz |
| --- | --- |
| `source` | Pasta (disco local, criada se faltar) ou `AsyncMinIOClient` (bucket). `None` só com `backend=` |
| `backend` | Backend pronto (`GoogleDriveUploadStorage`, um `UploadStorage` seu); ignora a seleção por `source` |
| `max_size_bytes` | Teto do arquivo, conferido enquanto o stream é lido. `None` ou `0` desliga |
| `allowed_extensions` | Allowlist de extensão. Ponto e caixa normalizados (`{"PNG", ".jpg"}` funciona). `None` ou `set()` desliga |
| `allowed_mimetypes` | Allowlist do `Content-Type` declarado (sem diferenciar caixa). `None` ou `set()` desliga |
| `verify_magic_bytes` | Confere os primeiros bytes (`sniff_mime`) contra o tipo declarado / a allowlist |
| `require_known_signature` | Com `verify_magic_bytes`, recusa assinatura desconhecida (`True`) ou só a contradição (`False`) |
| `chunk_size` | Tamanho de cada leitura do stream, em bytes (default 1 MiB) |

Sem o extra `[upload]`, `UploadUtils("pasta")` levanta `ImportError`; sem
`source` nem `backend`, `ValueError`.

### `save(...)` e os outros métodos

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

| Parâmetro | O que faz |
| --- | --- |
| `subdir` | Subpasta local ou prefixo de key no MinIO (`"avatars"`, `str(user.id)`) |
| `filename` | Nome final fixo (`f"{user_id}.jpg"`); reduzido ao basename, protegido contra path traversal |
| `keep_original_name` | Mantém o nome enviado; sem ele (e sem `filename`) o nome é um UUID com a extensão original |
| `content_validator` | Predicado sobre o **primeiro chunk**; `False` recusa e remove o parcial |
| `hasher` | Objeto `hashlib` alimentado com **todos** os chunks — leia `hasher.hexdigest()` depois do `save` |

**Retorno:** `Path` com a **key relativa** ao backend
(`Path("<subdir>/<uuid>.png")`), não um caminho absoluto. Guarde `str(key)` no
banco e leia de volta com o [`DownloadUtils`](downloads.md).

| Método | Retorno | Uso |
| --- | --- | --- |
| `await save(file, ...)` | `Path` (key) | valida e grava |
| `await replace(old_key, file, ...)` | `Path` (key nova) | grava a nova e só então apaga a antiga |
| `await delete(key)` | `bool` | `True` se apagou, `False` se a key não existia |
| `validate(file)` | `None` | só extensão e MIME, sem ler o stream |

### Exceções e status HTTP

Cada recusa é uma `AppException`; com `register_exception_handlers(app)` ela
vira o status e o envelope `{"detail", "code", "details"}` abaixo.

| Recusa | Exceção | Status | `code` | `details` |
| --- | --- | --- | --- | --- |
| Extensão fora de `allowed_extensions` | `InvalidFileTypeException` | `415` | `INVALID_FILE_TYPE` | `{"extension": "zip", "allowed": [...]}` |
| MIME fora de `allowed_mimetypes` | `InvalidFileTypeException` | `415` | `INVALID_FILE_TYPE` | `{"mimetype": "...", "allowed": [...]}` |
| Bytes não batem com o tipo (`verify_magic_bytes`) | `InvalidFileTypeException` | `415` | `INVALID_FILE_TYPE` | `{"reason": "...", "declared": "...", ...}` |
| `content_validator` devolveu `False` | `InvalidFileTypeException` | `415` | `INVALID_FILE_TYPE` | `{"reason": "content_validator rejected the upload bytes"}` |
| Passou de `max_size_bytes` | `FileTooLargeException` | `413` | `FILE_TOO_LARGE` | `{"max_size_bytes": 1024}` |

??? example "Os status acima saíram deste programa, que dispara cada recusa"

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
        """Recusa texto que começa com `nope`; o resto passa pelas allowlists.

        Args:
            file (UploadFile): O arquivo do formulário multipart.

        Returns:
            dict[str, str]: A key gravada.
        """
        key = await uploads.save(
            file,
            content_validator=lambda head: not head.startswith(b"nope"),
        )
        return {"key": str(key)}


    client: TestClient = TestClient(app)
    png: bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    cases: list[tuple[str, str, bytes, str]] = [
        ("aceito", "foto.png", png, "image/png"),
        ("extensao", "pacote.zip", b"PK", "application/zip"),
        ("mime", "foto.png", png, "application/zip"),
        ("tamanho", "notas.txt", b"x" * 2048, "text/plain"),
        ("content_validator", "notas.txt", b"nope", "text/plain"),
        ("magic bytes", "falso.png", b"<html></html>", "image/png"),
    ]
    for label, name, data, mime in cases:
        response = client.post("/files", files={"file": (name, data, mime)})
        body: dict[str, object] = response.json()
        print(f"{label}: {response.status_code} {body.get('code', '')}")
    print("arquivos gravados:", len(list(upload_dir.iterdir())))
    ```

Saída:

```text
aceito: 200
extensao: 415 INVALID_FILE_TYPE
mime: 415 INVALID_FILE_TYPE
tamanho: 413 FILE_TOO_LARGE
content_validator: 415 INVALID_FILE_TYPE
magic bytes: 415 INVALID_FILE_TYPE
arquivos gravados: 1
```

Só o arquivo aceito fica na pasta: o recusado por tamanho ou conteúdo é
removido antes da exceção. Extensão e MIME são recusados **antes de ler**
qualquer byte.

## Fazer upload para disco local

Passe uma pasta no construtor.

```python
from fastapi import APIRouter, UploadFile

from tempest_fastapi_sdk import UploadUtils

router = APIRouter()
uploads = UploadUtils("var/uploads", max_size_bytes=10 * 1024 * 1024)


@router.post("/files")
async def upload(file: UploadFile) -> dict[str, str]:
    """Valida e grava em disco; devolve a key (relativa ao base dir)."""
    key = await uploads.save(file)
    return {"key": str(key)}
```

!!! tip "Centralize em `resources.py`"
    Construa o `uploads` uma vez em
    [`src/api/dependencies/resources.py`](../architecture.md) e injete via
    `Depends(get_uploads)`. O `get_uploads` é glue do seu projeto (não vem do
    SDK):

    ```python
    # src/api/dependencies/resources.py
    from tempest_fastapi_sdk import UploadUtils

    uploads = UploadUtils("var/uploads", max_size_bytes=10 * 1024 * 1024)


    def get_uploads() -> UploadUtils:
        """Return the shared UploadUtils instance."""
        return uploads
    ```

## Fazer upload para MinIO / S3

Passe o `AsyncMinIOClient` no lugar da pasta; a rota é a mesma.

```python
from fastapi import APIRouter, UploadFile

from tempest_fastapi_sdk import AsyncMinIOClient, UploadUtils

from src.core.settings import settings

router = APIRouter()
minio = AsyncMinIOClient(**settings.storage_kwargs())
uploads = UploadUtils(minio, max_size_bytes=10 * 1024 * 1024)


@router.post("/logos")
async def upload_logo(file: UploadFile) -> dict[str, str]:
    """Grava no bucket com nome fixo; devolve a key S3."""
    key = await uploads.save(file, filename="logo.png")
    return {"key": str(key)}
```

## Upload só para usuário logado

Peça o usuário com a dependency de `auth_service.current_user_dependency()`:
sem token válido a rota responde `401` antes de ler o arquivo.

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
    """Grava o arquivo na pasta do usuário dono do token.

    Args:
        file (UploadFile): O arquivo do formulário multipart.
        current (UserModel): O usuário resolvido a partir do bearer token.
        uploads (UploadUtils): O utilitário de upload do processo.

    Returns:
        dict[str, str]: A key relativa ao diretório de upload.
    """
    key = await uploads.save(file, subdir=str(current.id))
    return {"key": str(key)}
```

O `get_current_user` é construído uma vez, ao lado do `UserAuthService`:
`get_current_user = auth_service.current_user_dependency()`. O
[Serviço completo](full-service.md) traz o projeto inteiro em volta desta rota
(settings, `resources.py`, migrations) e o teste que mediu as respostas:

| Envio | Status | `code` |
| --- | --- | --- |
| Com bearer token, `.png` | `201` | — (`{"key": "<user_id>/<uuid>.png"}`) |
| Sem token | `401` | `UNAUTHORIZED` |
| Com token, `.zip` fora da allowlist | `415` | `INVALID_FILE_TYPE` |
| Com token, acima de `max_size_bytes` | `413` | `FILE_TOO_LARGE` |

- `subdir=str(current.id)` separa os arquivos por dono.
- Modo `soft`, cookie e sessão compartilhada do `current_user_dependency()`:
  [Auth flow](auth-flow.md#pegando-o-current_user-da-requisicao).

## Restringir extensões (allowlist)

Passe `allowed_extensions` com o que você aceita: o resto é `415` **antes de
qualquer byte ser lido**, então um `.zip` nunca chega ao backend.

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
    """Aceita só .onnx / .ort; um .zip levanta 415 dentro do save()."""
    key = await uploads.save(file)
    return {"key": str(key)}
```

- `{".onnx", ".ort"}`, `{"onnx", "ort"}` e `{".ONNX"}` são equivalentes. A
  extensão vem de `Path(file.filename).suffix`, então `modelo.ONNX` passa e
  `pacote.zip` não.
- O nome do arquivo é do cliente: extensão não é o conteúdo. Para formatos com
  assinatura conhecida (imagens, PDF), ligue `verify_magic_bytes=True` +
  `allowed_mimetypes={...}` para casar os **bytes reais**.

!!! warning "Formato binário sem assinatura conhecida (`.onnx`, `.ort`)"
    Com `verify_magic_bytes=True`, o default `require_known_signature=True`
    recusa toda assinatura que o `sniff_mime` não reconhece. Duas saídas:
    manter `verify_magic_bytes=False` (o default), ou usar
    `require_known_signature=False`, que só recusa a **contradição** — um
    `.onnx` enviado como `application/octet-stream` é gravado, o mesmo arquivo
    declarado como `image/png` é recusado (`415`). Para validar o conteúdo de
    verdade, use `content_validator=...` no `save()`.

Os tipos que o `sniff_mime` reconhece pelos bytes estão em
`SNIFFABLE_MIMETYPES`; declarar um deles é o que torna a contradição
detectável.

```python
from tempest_fastapi_sdk.utils import SNIFFABLE_MIMETYPES

print(sorted(SNIFFABLE_MIMETYPES))
```

```text
['application/pdf', 'image/bmp', 'image/gif', 'image/heic', 'image/jpeg', 'image/png', 'image/webp']
```

### Arquivo que vai ser servido de volta

Use quando o arquivo volta a ser servido pela API (avatar, anexo,
comprovante): feche nome **e** conteúdo, e um HTML renomeado para `.png` é
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

Medido com esta configuração: `photo.png` com bytes de PNG → `200`;
`evil.html` (`text/html`) → `415`; o mesmo HTML como `evil.png` / `image/png`
→ `415`. O programa da [referência rápida](#excecoes-e-status-http) dispara
as mesmas recusas.

O download já se defende sozinho — toda resposta sai com `nosniff` e a CSP
`sandbox`, e HTML/SVG nunca vão `inline` (veja
[Downloads](downloads.md#headers-de-seguranca-e-o-que-vai-inline)). Validar na
entrada é a outra metade: o arquivo hostil nem chega ao storage.

### Configurar pelo `.env`

Use o mixin `UploadSettings` quando a allowlist e o teto vêm do ambiente
(como compor a classe: [Settings](settings.md)).

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

`UploadSettings` traz `UPLOAD_DIR`, `UPLOAD_MAX_SIZE_BYTES`,
`UPLOAD_ALLOWED_EXTENSIONS` e `UPLOAD_ALLOWED_MIMETYPES`, e o
`upload_kwargs()` os entrega ao construtor.

!!! tip "Vazio e `0` significam sem restrição"
    Sem `UPLOAD_ALLOWED_EXTENSIONS` no ambiente o campo vale `set()`, e
    `UPLOAD_MAX_SIZE_BYTES=0` desliga o limite. O `UploadUtils` trata `set()` e
    `0` exatamente como `None` — sem checagem —, em vez de recusar todo upload
    com 415.

## Foto com tipo declarado — `ImageMediaTypeEnum`

Use quando o arquivo **não** passa pelo `UploadUtils` (a rota lê os bytes e
grava em outro lugar) e o app declara o tipo num campo: o enum normaliza o
tipo, recusa o desconhecido e confere os bytes.

Os membros são os tipos de imagem que o `sniff_mime` reconhece: `JPEG`, `PNG`,
`GIF`, `BMP`, `WEBP` e `HEIC`.

```python
from pydantic import BaseModel, ValidationError

from tempest_fastapi_sdk import ImageMediaTypeEnum


class EvidencePhotoSchema(BaseModel):
    """Foto que o cliente vai enviar."""

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

- A entrada é comparada **sem diferenciar maiúsculas** e sem o espaço em volta;
  os aliases que o `UploadUtils` aceita (`image/jpg`, `image/pjpeg` → JPEG,
  `image/x-png` → PNG) viram o membro canônico.
- Valor desconhecido é o erro `enum` do Pydantic — numa rota, `422` —, e o JSON
  Schema (portanto o `openapi.json`) lista os seis valores canônicos.
- `.extension` dá a extensão do arquivo (`"jpg"`, `"png"`, `"gif"`, `"bmp"`,
  `"webp"`, `"heic"`).

### Aceitar só alguns tipos — `image_media_type_field`

Use quando nem todos os seis tipos servem: o `image_media_type_field` restringe
o campo **e** o JSON Schema juntos, sem `field_validator` à mão.

```python
from typing import Annotated

from pydantic import BaseModel, ValidationError

from tempest_fastapi_sdk import ImageMediaTypeEnum, image_media_type_field


class EvidencePhotoSchema(BaseModel):
    """Foto de câmera: GIF e BMP ficam de fora."""

    content_type: Annotated[
        ImageMediaTypeEnum,
        image_media_type_field(
            ImageMediaTypeEnum.JPEG,
            ImageMediaTypeEnum.PNG,
            ImageMediaTypeEnum.WEBP,
            ImageMediaTypeEnum.HEIC,
            title="Tipo da foto",
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

- A normalização do enum roda **antes** da restrição: `IMAGE/PNG` e
  `image/x-png` continuam virando `ImageMediaTypeEnum.PNG`.
- Tipo conhecido mas fora da lista é `value_error` (numa rota, `422`) com os
  permitidos na mensagem; tipo desconhecido continua sendo o erro `enum`.
- O JSON Schema lista **só** os permitidos. `description` e `examples` saem da
  lista quando você não passa os seus (o exemplo é o primeiro permitido).
- Sem argumentos, `image_media_type_field()` aceita os seis e só preenche
  título, descrição e exemplo.

!!! warning "O OpenAPI ganha um `ImageMediaTypeEnum` órfão — `prune_orphan_enum_schemas`"
    O FastAPI publica um componente para **todo** campo anotado com um `Enum`,
    mesmo quando o schema do campo traz o enum restrito inline. O
    `components.schemas.ImageMediaTypeEnum` sai com os seis membros e nenhum
    `$ref` aponta para ele, e o gerador de cliente cria um enum morto. Uma
    linha no `create_app` tira os enums que ninguém referencia (o `tempest
    new` já gera com ela):

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

    Só sai componente de enum sem nenhum `$ref` no documento; um enum usado
    por outro campo fica, e schema de objeto nunca é tocado.

### Conferir os bytes contra o tipo declarado

Use `.matches(data)` para perguntar ao `sniff_mime` o que os bytes **são** e
comparar com o tipo que o cliente declarou.

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
    """Onde a foto foi gravada, e como o quê."""

    key: str
    content_type: ImageMediaTypeEnum


app = FastAPI()
register_exception_handlers(app)


@app.post("/photos")
async def upload_photo(
    content_type: Annotated[ImageMediaTypeEnum, Form()],
    file: UploadFile,
) -> PhotoResponseSchema:
    """Aceita a foto só quando os bytes são do tipo declarado."""
    data: bytes = await read_upload_capped(file, max_bytes=MAX_PHOTO_BYTES)
    if not content_type.matches(data):
        raise InvalidFileTypeException(field="content_type")
    key: str = f"photos/{uuid4()}.{content_type.extension}"
    return PhotoResponseSchema(key=key, content_type=content_type)
```

| Envio | Resposta |
| --- | --- |
| HEIC declarado como `" Image/HEIC "` | `200`, key `photos/<uuid>.heic` |
| JPEG declarado como `image/jpg` | `200` |
| JPEG declarado como `image/png` | `415 INVALID_FILE_TYPE` |
| `image/avif` | `422` da validação, antes do corpo da função |
| Foto acima de `MAX_PHOTO_BYTES` | `413 UPLOAD_TOO_LARGE`, teto em `details.max_bytes` (até a 0.348.0 era `422 VALIDATION_ERROR`) |

O `read_upload_capped` para de ler no bloco que cruza o teto.

!!! info "HEIC pelos bytes"
    Um HEIC é uma caixa `ftyp` do ISO-BMFF: `ftyp` nos bytes 4 a 8 e a marca
    principal nos bytes 8 a 12. O `sniff_mime` reconhece as marcas HEVC de
    `HEIC_BRANDS` (`heic`, `heix`, `heim`, `heis`, `hevc`, `hevx`, `hevm`,
    `hevs`, portadas do `heif_get_file_mime_type` do libheif). As marcas
    neutras `mif1`/`msf1` (`HEIF_STRUCTURAL_BRANDS`) só contam como HEIC
    quando uma marca compatível da caixa é HEVC — é assim que um AVIF, que
    usa o mesmo contêiner e muitas vezes declara `mif1`, **não** passa por
    HEIC. Passe pelo menos os primeiros 32 bytes para as marcas compatíveis
    caberem.

!!! tip "Foto de usuário: limpe os metadados antes de gravar"
    `content_type.matches(data)` confere o tipo, mas a foto continua com o
    EXIF dentro (GPS, aparelho). Para gravar ou repassar sem isso, veja
    [Metadados de imagem](image-metadata.md).

## Alternar o backend por settings

Escolha o argumento do construtor conforme uma flag do seu `Settings` — não
precisa de backend pluggável manual.

```python
# src/api/dependencies/resources.py
from tempest_fastapi_sdk import AsyncMinIOClient, UploadUtils

from src.core.settings import settings

if settings.UPLOAD_BACKEND == "minio":
    uploads = UploadUtils(AsyncMinIOClient(**settings.storage_kwargs()))
else:
    uploads = UploadUtils(settings.UPLOAD_DIR)
```

`UPLOAD_BACKEND` é um campo do **seu** `Settings`; o SDK só traz os
`UPLOAD_*` do `UploadSettings`.

## Trocar um arquivo (avatar, anexo) — `replace`

Use quando o usuário manda um arquivo novo e o antigo tem de sumir, sem risco de
ficar sem nenhum.

```python
from fastapi import UploadFile

from tempest_fastapi_sdk import UploadUtils

uploads = UploadUtils("./uploads")


async def replace_avatar(user_id: str, old_key: str | None, file: UploadFile) -> str:
    """Grava o avatar novo e apaga o anterior.

    Args:
        user_id (str): Dono do avatar; vira o nome do arquivo.
        old_key (str | None): Key salva hoje no banco (`None` no primeiro upload).
        file (UploadFile): O arquivo novo.

    Returns:
        str: A key nova, para guardar no lugar da antiga.
    """
    new_key = await uploads.replace(old_key, file, filename=f"{user_id}.jpg")
    return str(new_key)
```

O `replace` **grava a nova primeiro** e só então apaga a antiga. Se a validação
reprovar o arquivo novo, a antiga fica **intacta**; com `old_key=None` ele só
salva. Tudo passa pelo **mesmo backend**, então não há como salvar num e apagar
no outro. Para apagar sem substituir, `await uploads.delete(key)` devolve
`True`/`False`.

Para **baixar** o que foi enviado (local ou MinIO), use o
[`DownloadUtils`](downloads.md) — ele aceita o mesmo backend no construtor.

## Gravar um stream direto no backend — `write_stream` e `UploadResult`

Use quando os bytes não vêm de um formulário (proxy de outro serviço, resultado
de um job, arquivo gerado na hora): `write_stream` consome um
`AsyncIterator[bytes]` sem carregar o arquivo inteiro na memória.

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

| Campo | Tipo | Conteúdo |
| --- | --- | --- |
| `key` | `str` | Identificador canônico — caminho relativo no local, key S3 no MinIO |
| `size` | `int` | Bytes gravados |
| `path` | `Path` ou `None` | Caminho em disco, só quando o backend escreve em filesystem |
| `url` | `str` ou `None` | URL de download (presigned ou estática), quando o backend sabe gerar |

`max_size_bytes=` corta o stream ao cruzar o teto (`FileTooLargeException`), e
`validator=` inspeciona os primeiros bytes — as duas checagens rodam
**enquanto** grava.

## Arquivo grande: presigned PUT direto no bucket

Acima de ~50 MB, evite que o arquivo passe pelo seu processo: o cliente faz
`PUT` direto no MinIO por uma URL presigned. Veja
[Storage MinIO/S3](storage.md#presigned-url-upload-direto-do-browser).

## Migrar de < v0.41.0

Na v0.41.0 o backend passou para o construtor e o `save(file, storage=...)` por
chamada **foi removido**.

- `UploadUtils("./dir")` continua igual (disco local).
- `UploadUtils("./tmp")` + `save(file, storage=MinIOUploadStorage(client))`
  → vira `UploadUtils(client)` + `save(file)`.
- `save()` agora devolve a **key** (relativa), não um caminho absoluto —
  guarde a key e use `DownloadUtils.download(key)` pra servir.
- `utils.delete(path)` (sync) → `await utils.delete(key)` (async).

## Recap

- `UploadUtils` escolhe o backend **uma vez, no construtor**: pasta grava em
  disco, `AsyncMinIOClient` grava no bucket.
- `save()` devolve a **key** relativa (`Path`); é ela que vai para o banco.
- Recusas: tipo, MIME, magic bytes e `content_validator` → `415
  INVALID_FILE_TYPE`; tamanho → `413 FILE_TOO_LARGE`; sem token numa rota com
  `current_user_dependency()` → `401`.
- Arquivo que volta a ser servido: `allowed_extensions` +
  `verify_magic_bytes=True` + `allowed_mimetypes`.
- Bytes fora do `UploadUtils`: `ImageMediaTypeEnum` no campo e `.matches(data)`.
- `replace` troca sem deixar órfão; `write_stream` grava sem carregar na
  memória; acima de ~50 MB, presigned PUT.
