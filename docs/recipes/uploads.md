# Uploads — disco local + S3 / MinIO

Desde v0.24.0 o `UploadUtils` aceita **backend de storage pluggável**: mantenha o mesmo código de upload e troque entre disco local e MinIO/S3 via flag de configuração.

!!! tip "Validação fica no `UploadUtils`"
    Tamanho, extensão, MIME, magic bytes e `content_validator` continuam sendo validados no `UploadUtils` antes de qualquer byte ir pro storage — backends só recebem dados já validados.

## Backends disponíveis

| Backend | Quando usar | Extra necessário |
|---------|-------------|------------------|
| `LocalUploadStorage` | Dev / single-replica / FS local | `[upload]` |
| `MinIOUploadStorage` | Multi-réplica, S3/MinIO/R2/B2/Spaces | `[upload]` + `[minio]` |

Os dois implementam o protocolo `UploadStorage` (`write_stream`, `delete`, `exists`, `presigned_url`).

## Padrão: disco local (back-compat)

Sem mudar nada — `UploadUtils(upload_dir)` continua gravando local:

```python
from pathlib import Path

from fastapi import APIRouter, UploadFile
from tempest_fastapi_sdk import UploadUtils

router = APIRouter()
utils = UploadUtils(Path("./uploads"), max_size_bytes=10 * 1024 * 1024)


@router.post("/files")
async def upload(file: UploadFile) -> dict[str, str]:
    """Salva o arquivo em disco e devolve o caminho absoluto."""
    path = await utils.save(file)
    return {"path": str(path)}
```

## Trocando pra MinIO/S3

Reaproveite o `AsyncMinIOClient` da app:

```python
from fastapi import APIRouter, UploadFile
from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    MinIOUploadStorage,
    UploadUtils,
)

from src.core.settings import settings


client = AsyncMinIOClient(
    endpoint=settings.MINIO_ENDPOINT,
    access_key=settings.MINIO_ACCESS_KEY,
    secret_key=settings.MINIO_SECRET_KEY,
    default_bucket=settings.MINIO_DEFAULT_BUCKET,
)
remote = MinIOUploadStorage(client)
utils = UploadUtils(
    "./tmp",  # ainda obrigatório, mas não usado quando passa storage=
    max_size_bytes=10 * 1024 * 1024,
)

router = APIRouter()


@router.post("/files")
async def upload(file: UploadFile) -> dict[str, str]:
    """Valida e envia direto pro MinIO."""
    key = await utils.save(file, storage=remote, filename=file.filename)
    return {"key": str(key)}
```

## Alternando via flag de settings

```python
from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    LocalUploadStorage,
    MinIOUploadStorage,
    UploadStorage,
    UploadUtils,
)

from src.core.settings import settings


def make_storage() -> UploadStorage:
    """Pluga o backend correto conforme o ambiente."""
    if settings.UPLOAD_BACKEND == "minio":
        client = AsyncMinIOClient(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            default_bucket=settings.MINIO_DEFAULT_BUCKET,
        )
        return MinIOUploadStorage(client)
    return LocalUploadStorage(settings.UPLOAD_DIR)


storage = make_storage()
utils = UploadUtils(settings.UPLOAD_DIR)
```

## Operações comuns

```python
# Save
await utils.save(file, storage=storage, filename="logo.png")

# Delete
await storage.delete("logo.png")

# Probe
exists = await storage.exists("logo.png")

# URL temporária (S3 retorna URL; local retorna None)
from datetime import timedelta
url = await storage.presigned_url("logo.png", expires=timedelta(hours=1))
```

## Quando usar presigned PUT direto

Pra arquivos > 50 MB, evite buffer em memória — mande o cliente fazer `PUT` direto no MinIO via URL presigned. Veja [Storage MinIO/S3](storage.md#presigned-url-upload-direto-do-browser).
