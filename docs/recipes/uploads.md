# Uploads — disco local + S3 / MinIO

`UploadUtils` escolhe o backend **uma vez no construtor**: passe uma **pasta**
para gravar em disco local, ou um **`AsyncMinIOClient`** para gravar num
bucket S3/MinIO. O resto do código de upload é idêntico nos dois casos.
Requer o extra `[upload]` (e `[minio]` quando usar MinIO).

!!! warning "Mudança em v0.41.0 (breaking)"
    O backend agora vem no construtor — o antigo `save(file, storage=...)`
    por chamada **foi removido**. `save()` devolve a **key** de storage
    (relativa), e `delete()` virou **async**. Veja a migração no fim.

!!! tip "Validação fica no `UploadUtils`"
    Tamanho, extensão, MIME, magic bytes e `content_validator` são validados
    no `UploadUtils` antes de qualquer byte ir pro backend — o storage só
    recebe dados já validados.

## Disco local

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

## MinIO / S3

Passe o `AsyncMinIOClient` direto — nada mais muda:

```python
import asyncio

from fastapi import UploadFile

from tempest_fastapi_sdk import AsyncMinIOClient, UploadUtils

from src.core.settings import settings

file: UploadFile = ...  # comes from the endpoint signature


minio = AsyncMinIOClient(**settings.minio_kwargs())
uploads = UploadUtils(minio, max_size_bytes=10 * 1024 * 1024)


async def main() -> None:
    """Run this example."""
    # idêntico ao caso local:
    key = await uploads.save(file, filename="logo.png")   # grava no bucket


asyncio.run(main())
```

!!! tip "Centralize em `resources.py`"
    Construa o `uploads` (e o `minio`) uma vez em
    [`src/api/dependencies/resources.py`](../architecture.md) e injete via
    `Depends(get_uploads)`, em vez de instanciar por request. O `get_uploads`
    é glue do seu projeto (não vem do SDK) — um provider que devolve a
    instância única:

    ```python
    # src/api/dependencies/resources.py
    from tempest_fastapi_sdk import UploadUtils

    uploads = UploadUtils("var/uploads", max_size_bytes=10 * 1024 * 1024)


    def get_uploads() -> UploadUtils:
        """Return the shared UploadUtils instance."""
        return uploads
    ```

## Restringir extensões (allowlist)

Passe `allowed_extensions` no construtor com o conjunto de extensões que
você aceita. Tudo fora da lista é rejeitado com **HTTP 415**
(`InvalidFileTypeException`) **antes de qualquer byte ser lido** — então um
`.zip` malicioso nunca chega ao backend nem ocupa memória:

```python
from tempest_fastapi_sdk import UploadUtils

# Só modelos ONNX — qualquer outra extensão é bloqueada.
uploads = UploadUtils(
    "var/models",
    allowed_extensions={".onnx", ".ort"},
    max_size_bytes=200 * 1024 * 1024,
)
```

```python
from fastapi import APIRouter, UploadFile

from tempest_fastapi_sdk import UploadUtils

uploads = UploadUtils(source="./uploads")
router = APIRouter()


@router.post("/models")
async def upload_model(file: UploadFile) -> dict[str, str]:
    """Aceita só .onnx / .ort; um .zip levanta 415 aqui dentro do save()."""
    key = await uploads.save(file)   # file.zip -> InvalidFileTypeException (415)
    return {"key": str(key)}
```

!!! info "Ponto e case são normalizados"
    `{".onnx", ".ort"}`, `{"onnx", "ort"}` e `{".ONNX"}` são equivalentes — o
    `UploadUtils` tira o ponto inicial e baixa pra minúsculo. A extensão vem
    de `Path(file.filename).suffix`, então `modelo.ONNX` passa e `pacote.zip`
    não.

!!! warning "Extensão não é o conteúdo"
    Conferir extensão impede o engano honesto e o `.zip` óbvio, mas o nome do
    arquivo é controlado pelo cliente. Pra formatos com assinatura conhecida
    (imagens, PDF) ligue `verify_magic_bytes=True` + `allowed_mimetypes={...}`
    pra casar os **bytes reais** contra a allowlist. Formatos binários sem
    assinatura no `sniff_mime` (como `.onnx` / `.ort`) não passam com
    `verify_magic_bytes=True` sozinho: o default `require_known_signature=True`
    recusa toda assinatura desconhecida. Duas saídas: manter
    `verify_magic_bytes=False` (o default), ou ligar o sniff com
    `require_known_signature=False`, que só recusa a **contradição** — um
    `.onnx` enviado como `application/octet-stream` é gravado, o mesmo arquivo
    declarado como `image/png` é recusado (415), porque `image/png` é um tipo
    que o `sniff_mime` conhece e os bytes não carregam. Pra validar o
    conteúdo de verdade, use um `content_validator=...` no `save()`.

### Arquivo que vai ser servido de volta

Se o arquivo enviado volta a ser servido pela sua API (avatar, anexo,
comprovante), feche o upload nas duas pontas: `allowed_extensions` recusa o
nome errado, e `verify_magic_bytes=True` + `allowed_mimetypes` recusam o
conteúdo que não bate com o tipo — um HTML renomeado para `.png` é `415`:

```python
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import UploadUtils, register_exception_handlers

uploads = UploadUtils(
    "uploads",
    allowed_extensions={".png", ".jpg", ".jpeg", ".pdf"},
    allowed_mimetypes={"image/png", "image/jpeg", "application/pdf"},
    verify_magic_bytes=True,
)
app = FastAPI()
register_exception_handlers(app)


@app.post("/files")
async def upload_file(file: UploadFile) -> dict[str, str]:
    """Grava só PNG, JPEG ou PDF cujos bytes batem com o tipo."""
    key = await uploads.save(file)
    return {"key": str(key)}


client = TestClient(app)
png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
html = b"<script>window.ran = true</script>"
for name, data, mime in [
    ("photo.png", png, "image/png"),
    ("evil.html", html, "text/html"),
    ("evil.png", html, "image/png"),
]:
    response = client.post("/files", files={"file": (name, data, mime)})
    print(name, response.status_code)
```

```text
photo.png 200
evil.html 415
evil.png 415
```

O download já se defende sozinho — toda resposta sai com `nosniff` e a CSP
`sandbox`, e HTML/SVG nunca vão `inline` (veja
[Downloads](downloads.md#headers-de-seguranca-e-o-que-vai-inline)). Validar
na entrada é a outra metade: o arquivo hostil nem chega ao storage.

### Via settings (`.env`)

Quando preferir configurar por ambiente, o `UploadSettings` já expõe
`UPLOAD_ALLOWED_EXTENSIONS` (e `UPLOAD_ALLOWED_MIMETYPES`):

```bash
# .env
UPLOAD_ALLOWED_EXTENSIONS=[".onnx", ".ort"]
UPLOAD_MAX_SIZE_BYTES=209715200
```

```python
from tempest_fastapi_sdk import UploadUtils

from src.core.settings import settings


uploads = UploadUtils(
    settings.UPLOAD_DIR,
    allowed_extensions=settings.UPLOAD_ALLOWED_EXTENSIONS,
    max_size_bytes=settings.UPLOAD_MAX_SIZE_BYTES,
)
```

!!! tip "Vazio e `0` significam sem restrição"
    Sem `UPLOAD_ALLOWED_EXTENSIONS` no ambiente o campo vale `set()`, e
    `UPLOAD_MAX_SIZE_BYTES=0` desliga o limite. O `UploadUtils` trata
    `set()` e `0` exatamente como `None` — sem checagem —, então tanto o
    código acima quanto `UploadUtils(**settings.upload_kwargs())` aceitam
    qualquer extensão quando a allowlist não foi configurada, em vez de
    recusar todo upload com 415.

## Alternar por settings

Escolha o argumento do construtor conforme uma flag do seu `Settings` — não
precisa de backend pluggável manual:

```python
# src/api/dependencies/resources.py
from tempest_fastapi_sdk import AsyncMinIOClient, UploadUtils

from src.core.settings import settings

if settings.UPLOAD_BACKEND == "minio":
    uploads = UploadUtils(AsyncMinIOClient(**settings.minio_kwargs()))
else:
    uploads = UploadUtils(settings.UPLOAD_DIR)
```

(`UPLOAD_BACKEND` é um campo do seu `Settings`; o SDK só carrega
`UPLOAD_DIR` / `UPLOAD_MAX_SIZE_BYTES` / `UPLOAD_ALLOWED_EXTENSIONS` /
`UPLOAD_ALLOWED_MIMETYPES` via `UploadSettings`.)

## Operações comuns

```python
import asyncio

from fastapi import UploadFile

from tempest_fastapi_sdk import UploadUtils

file: UploadFile = ...  # comes from the endpoint signature
uploads = UploadUtils(source="./uploads")


async def main() -> None:
    """Run this example."""
    key = await uploads.save(file, filename="logo.png")  # -> Path("logo.png")
    removed = await uploads.delete(key)                  # async; True/False


asyncio.run(main())
```

### Trocar um arquivo (avatar, anexo) — `replace`

O caso clássico: o usuário manda uma foto de perfil nova e você quer
**gravar a nova e apagar a antiga**. Em vez de fazer `save` + `delete` na
mão (e arriscar apagar pelo backend errado), use `replace`:

```python
import asyncio

from fastapi import UploadFile

from tempest_fastapi_sdk import UploadUtils

from src.db.models import UserModel

file: UploadFile = ...  # comes from the endpoint signature
uploads = UploadUtils(source="./uploads")
user = UserModel(name="Ana", email="ana@example.com")


async def main() -> None:
    """Run this example."""
    # old_key é o que está salvo hoje no model (pode ser None no 1º upload)
    new_key = await uploads.replace(
        user.profile_picture, file, filename=f"{user.id}.jpg"
    )
    user.profile_picture = str(new_key)


asyncio.run(main())
```

!!! tip "A ordem importa — e o `replace` acerta pra você"
    O `replace` **grava a nova primeiro** e só então apaga a antiga. Se a
    validação reprovar o arquivo novo (extensão/MIME/tamanho), a antiga
    fica **intacta** — você nunca fica sem imagem nenhuma. Passe
    `old_key=None` no primeiro upload (não há nada pra apagar) e o método
    só salva. Tudo passa pelo **mesmo backend** configurado (local ou
    MinIO), evitando o erro de salvar num e apagar no outro.

Para **baixar** o que foi enviado (local ou MinIO), use o
[`DownloadUtils`](downloads.md) — ele aceita o mesmo backend no construtor.

### Streaming direto pro backend — `write_stream` e `UploadResult`

`save()` é a porta de entrada pra `UploadFile` do FastAPI e devolve a **key**.
Quando os bytes não vêm de um formulário — proxy de outro serviço, resultado de
um job, arquivo gerado na hora — fale com o backend direto: `write_stream`
consome um `AsyncIterator[bytes]` sem buffer do arquivo inteiro na memória, e
devolve um `UploadResult`:

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
**enquanto** grava, sem esperar o arquivo terminar.

## Quando usar presigned PUT direto

Pra arquivos > 50 MB, evite buffer em memória — mande o cliente fazer `PUT`
direto no MinIO via URL presigned. Veja
[Storage MinIO/S3](storage.md#presigned-url-upload-direto-do-browser).

## Migração de < v0.41.0

- `UploadUtils("./dir")` continua igual (disco local).
- `UploadUtils("./tmp")` + `save(file, storage=MinIOUploadStorage(client))`
  → vira `UploadUtils(client)` + `save(file)`.
- `save()` agora devolve a **key** (relativa), não um caminho absoluto —
  guarde a key e use `DownloadUtils.download(key)` pra servir.
- `utils.delete(path)` (sync) → `await utils.delete(key)` (async).

## Recap

- `UploadUtils` escolhe o backend **uma vez, no construtor**: uma pasta grava
  em disco, um `AsyncMinIOClient` grava no bucket. O resto do código de upload
  não muda.
- `allowed_extensions` é allowlist, não denylist — e o `UploadSettings` deixa
  configurar por ambiente.
- Arquivo que volta a ser servido: `allowed_extensions` + `verify_magic_bytes=True`
  + `allowed_mimetypes`, para o conteúdo bater com o tipo.
- `save()` recebe o `UploadFile` do FastAPI e devolve a **key**; é a key que
  você guarda no banco, não o caminho.
- `replace` cobre o caso do avatar trocado sem deixar órfão, e `write_stream`
  evita carregar arquivo grande na memória.
- Acima de ~50 MB, o caminho é presigned PUT: o cliente manda direto para o
  bucket, e o seu processo não vira gargalo.
