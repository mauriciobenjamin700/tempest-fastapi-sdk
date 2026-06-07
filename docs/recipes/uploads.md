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
from tempest_fastapi_sdk import AsyncMinIOClient, UploadUtils

from src.core.settings import settings

minio = AsyncMinIOClient(**settings.minio_kwargs())
uploads = UploadUtils(minio, max_size_bytes=10 * 1024 * 1024)

# idêntico ao caso local:
key = await uploads.save(file, filename="logo.png")   # grava no bucket
```

!!! tip "Centralize em `resources.py`"
    Construa o `uploads` (e o `minio`) uma vez em
    [`src/api/dependencies/resources.py`](../architecture.md) e injete via
    `Depends(get_uploads)`, em vez de instanciar por request.

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
key = await uploads.save(file, filename="logo.png")  # -> Path("logo.png")
removed = await uploads.delete(key)                  # async; True/False
```

Para **baixar** o que foi enviado (local ou MinIO), use o
[`DownloadUtils`](downloads.md) — ele aceita o mesmo backend no construtor.

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
