# File store unificado — `FileStoreUtils`

Guardar um arquivo, servir de volta e assinar uma URL temporária normalmente
exige orquestrar **três** peças na mão: `UploadUtils` (validar + gravar),
`DownloadUtils` (servir bytes pela API) e o `AsyncMinIOClient` (URLs
presigned). `FileStoreUtils` embrulha as três atrás de **um objeto**, com
**uma configuração**, apontando pro **mesmo backend de storage**.

!!! tip "Quando usar"
    Use `FileStoreUtils` quando o serviço **grava e serve** os mesmos
    arquivos. Se você só precisa de uma metade (só upload, ou só download),
    `UploadUtils` / `DownloadUtils` avulsos continuam disponíveis — o
    `FileStoreUtils` só junta as duas com o backend compartilhado.

Requer o extra `[upload]` para disco local e `[minio]` para MinIO/S3.

## O básico — disco local

O backend é escolhido **uma vez no construtor**: passe uma pasta para gravar
em disco local.

```python
from fastapi import APIRouter, UploadFile
from starlette.responses import Response

from tempest_fastapi_sdk import FileStoreUtils

router = APIRouter()
store = FileStoreUtils("var/uploads", max_size_bytes=10 * 1024 * 1024)


@router.post("/files")
async def upload(file: UploadFile) -> dict[str, str]:
    """Valida e grava; devolve a key (relativa ao base dir)."""
    key = await store.save(file)
    return {"key": str(key)}


@router.get("/files/{key}")
async def download(key: str) -> Response:
    """Serve o arquivo de volta pela própria API."""
    return await store.download(key)
```

O mesmo objeto `store` grava (`save`), serve (`download`), apaga (`delete`),
checa existência (`exists`) e troca (`replace`) — sem instanciar mais nada.

## MinIO / S3 — troca só o `source`

Passe um `AsyncMinIOClient` no lugar da pasta. Nada mais no seu código muda:

```python
from tempest_fastapi_sdk import AsyncMinIOClient, FileStoreUtils

minio = AsyncMinIOClient(
    endpoint="minio:9000",
    access_key="...",
    secret_key="...",
    default_bucket="avatars",
)
store = FileStoreUtils(minio, max_size_bytes=5 * 1024 * 1024)

key = await store.save(file, subdir="users/42")   # grava no bucket
url = await store.presigned_get_url(str(key))       # URL assinada de leitura
```

!!! info "Bucket"
    Para mirar um bucket específico, configure o `default_bucket` do
    `AsyncMinIOClient` — as duas metades (upload e download) leem dele.

## URLs presigned

No backend MinIO, `FileStoreUtils` expõe os dois atalhos de presign — deixe o
cliente baixar/subir direto do MinIO, sem passar os bytes pela sua API:

```python
from datetime import timedelta

get_url = await store.presigned_get_url(key, expires=timedelta(hours=1))
put_url = await store.presigned_put_url(key, expires=timedelta(minutes=15))
```

!!! note "Local devolve `None`"
    Em disco local não existe URL pública, então `presigned_get_url` e
    `presigned_put_url` retornam `None` — o mesmo call site funciona pros dois
    backends, sem `if backend == ...`.

!!! tip "Muitas chaves de uma vez *(v0.133.0+)*"
    `presigned_get_url` assina **uma** chave. Para uma página inteira (uma chave
    por linha), use o cliente MinIO por baixo — `store.client.presigned_get_urls([...])`
    (ver [Operações em lote](storage.md#operacoes-em-lote-presign-upload-download-v01330))
    — que dispara o fan-out concorrente e devolve um `dict` chave→URL. Num
    **serviço**, o atalho é o [`file_urls`](stored-files.md#uma-pagina-inteira-file_urls-v01330)
    do `StoredFileServiceMixin`.

## Validação

A validação (tamanho, extensão, MIME, magic bytes, `content_validator`) é a do
`UploadUtils` — configurada no construtor e aplicada **antes** de qualquer byte
ir pro backend:

```python
store = FileStoreUtils(
    "var/uploads",
    max_size_bytes=5 * 1024 * 1024,
    allowed_extensions={"png", "jpg", "jpeg"},
    allowed_mimetypes={"image/png", "image/jpeg"},
    verify_magic_bytes=True,
)
```

## Trocar um arquivo (avatar / anexo)

`replace` grava o novo **primeiro** (um erro de validação deixa o antigo
intacto), depois apaga o antigo — pelo mesmo backend:

```python
new_key = await store.replace(user.avatar_key, file, filename=f"{user.id}.png")
user.avatar_key = str(new_key)
```

## Escape hatches

As peças internas continuam acessíveis quando você precisa do controle fino:

```python
store.uploader     # UploadUtils     — save/replace/delete/validate
store.downloader   # DownloadUtils   — download/file_response/stream/resolve
store.backend      # UploadStorage   — write_stream/delete/exists/presigned_url
store.client       # AsyncMinIOClient | None  — None quando disco local
```

## Recap

- `FileStoreUtils(source, ...)` — **um** objeto para gravar, servir e assinar.
- `source` é pasta (disco local) **ou** `AsyncMinIOClient` (MinIO/S3); o resto
  do código não muda entre os dois.
- Um único backend `UploadStorage` é construído e **compartilhado** com a
  metade de upload; no MinIO, o **mesmo** client vai pra metade de download
  (pool de conexão compartilhado).
- Presign existe só no MinIO — em disco local devolve `None`, mantendo o call
  site uniforme.
- Precisa de só uma metade? `UploadUtils` / `DownloadUtils` seguem avulsos.
