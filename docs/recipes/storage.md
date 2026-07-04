# Object storage — MinIO / S3

`AsyncMinIOClient` é uma fachada async sobre o pacote oficial `minio`. Cobre o que serviço FastAPI típico precisa: bucket (ensure/exists/list/remove), object I/O (put/get/stream/stat/list/remove/copy) e presigned URLs (GET/PUT). Operações avançadas (versioning, lifecycle XML, SSE-KMS, multipart fine-tuning) ficam acessíveis via atributo `.client`.

!!! tip "Por que esse wrapper existe"
    `minio-py` é **síncrono**. Chamar `client.put_object(...)` direto dentro de uma rota FastAPI bloqueia o event loop durante o upload inteiro. O wrapper envolve cada chamada em `asyncio.to_thread`, então o loop continua respondendo enquanto a operação roda no executor.

## Instalação

```bash
pip install "tempest-fastapi-sdk[minio]"
# ou:
uv add "tempest-fastapi-sdk[minio]"
```

O pacote `minio` é lazy-loaded — só carrega quando `AsyncMinIOClient` é instanciado. Projetos sem storage não precisam do extra.

## Configuração via settings mixin

```python
from tempest_fastapi_sdk import (
    BaseAppSettings,
    MinIOSettings,
    ServerSettings,
)


class Settings(
    ServerSettings,
    MinIOSettings,
    BaseAppSettings,
):
    """Service settings — herda MinIO defaults."""
```

`.env`:

```bash
MINIO_ENDPOINT=minio.internal:9000
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...
MINIO_SECURE=true
MINIO_REGION=us-east-1
MINIO_DEFAULT_BUCKET=uploads
```

## Wiring no `create_app()`

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from tempest_fastapi_sdk import AsyncMinIOClient

from src.core.settings import settings


# settings.minio_kwargs() mapeia MINIO_* -> endpoint/access_key/secret_key/
# default_bucket/secure/region, então não precisa repetir campo a campo.
storage = AsyncMinIOClient(**settings.minio_kwargs())


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Garante que o bucket padrão existe antes de servir tráfego."""
    await storage.ensure_bucket()
    yield


def create_app() -> FastAPI:
    """Build the configured FastAPI instance."""
    return FastAPI(lifespan=lifespan)
```

## Receitas

### Upload de `UploadFile` (FastAPI)

```python
from fastapi import APIRouter, UploadFile

from src.api.app import storage

router = APIRouter()


@router.post("/files")
async def upload_file(file: UploadFile) -> dict[str, str]:
    """Persiste o arquivo recebido no bucket padrão."""
    body = await file.read()
    etag = await storage.put_object(
        file.filename or "unnamed",
        body,
        content_type=file.content_type or "application/octet-stream",
        metadata={"original-name": file.filename or ""},
    )
    return {"key": file.filename or "unnamed", "etag": etag}
```

### Streaming de download

!!! tip "Atalho: `download_response` (ou `DownloadUtils`)"
    O `AsyncMinIOClient.download_response(key, ...)` já faz stat + stream +
    Content-Disposition/Type/Length numa chamada só — e o
    [`DownloadUtils(minio)`](downloads.md) embrulha isso. O exemplo manual
    abaixo é só pra mostrar as peças.

Use para arquivos grandes — chunk-a-chunk evita carregar tudo em memória:

```python
from fastapi import APIRouter
from starlette.responses import Response

from src.api.app import storage

router = APIRouter()


@router.get("/files/{key}")
async def download_file(key: str) -> Response:
    """Stream do objeto no bucket padrão (uma chamada)."""
    return await storage.download_response(key)
```

### Presigned URL — upload direto do browser

Padrão recomendado pra arquivos grandes: o cliente faz `PUT` direto no MinIO/S3, os bytes não passam pelo FastAPI.

```python
from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel

from src.api.app import storage

router = APIRouter()


class PresignedUploadResponse(BaseModel):
    key: str
    url: str


@router.post("/uploads/presign")
async def presign_upload() -> PresignedUploadResponse:
    """Devolve URL temporária pro cliente fazer PUT direto."""
    key = f"uploads/{uuid4().hex}"
    url = await storage.presigned_put_url(key, expires=timedelta(minutes=15))
    return PresignedUploadResponse(key=key, url=url)
```

Cliente JS:

```javascript
const { key, url } = await fetch("/uploads/presign", { method: "POST" }).then(r => r.json());
await fetch(url, { method: "PUT", body: file });
```

### Presigned URL — download temporário

Para servir arquivos privados sem rotear bytes pela API:

```python
from datetime import timedelta

from fastapi import APIRouter

from src.api.app import storage

router = APIRouter()


@router.get("/files/{key}/url")
async def get_download_url(key: str) -> dict[str, str]:
    """URL de download válida por 1 hora."""
    url = await storage.presigned_get_url(key, expires=timedelta(hours=1))
    return {"url": url}
```

### Endpoint público separado para presigned URLs *(v0.88.0+)*

Cenário comum em produção: o backend fala com o MinIO por uma **rede privada rápida** (`servus-storage:9000`, sem TLS), mas o **browser** não alcança esse host — precisa de um host **público com HTTPS**. Se você assinar a presigned URL com o endpoint interno, o link vem com `servus-storage:9000` e o navegador não abre.

Solução: `MINIO_PUBLIC_ENDPOINT`. As presigned URLs (`presigned_get_url` / `presigned_put_url`) passam a ser **assinadas contra o host público**, enquanto **todas as operações servidor→MinIO continuam no endpoint interno**.

```bash
# .env
MINIO_ENDPOINT=servus-storage:9000            # rede interna Docker (ops)
MINIO_SECURE=false
MINIO_PUBLIC_ENDPOINT=https://storage.example.com   # browser (presigned)
# MINIO_PUBLIC_SECURE=true                     # opcional; https:// já implica true
```

!!! info "Por que dois clients e não um replace de host"
    A presigned URL é assinada (SigV4) incluindo o header `Host`. Trocar o host **depois** de assinar invalida a assinatura. Por isso o SDK mantém um segundo `minio.Minio` (mesmas credenciais) só para **assinar** contra o host público — o `AsyncMinIOClient.client` interno segue fazendo put/get/stat/ensure_bucket pela rede privada.

!!! tip "Sem `MINIO_PUBLIC_ENDPOINT`"
    Comportamento inalterado: presigned URLs são assinadas com `MINIO_ENDPOINT` (modo endpoint único). O split é 100% opt-in.

O proxy do host público precisa rotear para a **API S3 do MinIO (porta 9000)** com TLS e repassar o `Host` correto (a assinatura valida o host).

### Listar objetos por prefixo

```python
from fastapi import APIRouter

from src.api.app import storage

router = APIRouter()


@router.get("/files")
async def list_files(prefix: str = "") -> list[str]:
    """Lista chaves no bucket padrão sob ``prefix``."""
    return await storage.list_objects(prefix)
```

`list_objects` devolve `[]` quando nada bate — em linha com a convenção do SDK ("nenhum match não é erro").

### Copiar / mover

```python
await storage.copy_object("uploads/draft-1", "uploads/final-1")
await storage.remove_object("uploads/draft-1")
```

## Quando NÃO usar `AsyncMinIOClient`

- Quando você precisa de operações **fora** das listadas (SSE-KMS, ACLs S3 v2, bucket replication). Use `storage.client.<método>` direto — `minio-py` continua acessível.
- Para uploads gigantes (> 5 GiB) com retomada — `minio-py` faz multipart automático mas não suporta `tus` ou resume. Considere `tus.io` separadamente.

## Próximos passos

- O backend pluggable de upload `MinIOUploadStorage` está disponível desde a v0.24.0 — para o pipeline que alterna disco local ↔ MinIO/S3 via flag de settings, veja a [receita de uploads](uploads.md).
