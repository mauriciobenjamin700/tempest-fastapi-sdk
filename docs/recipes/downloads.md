# Downloads

`DownloadUtils` serve arquivos para download/inline — de **disco local** ou
direto de um **bucket MinIO/S3**. Escolha o backend **uma vez no
construtor** (igual ao [Uploads](uploads.md)): passe uma pasta, ou um
`AsyncMinIOClient`. Depois chame `download(key)` — funciona igual nos dois.
Faz parte do SDK base (sem extra; MinIO precisa do `[minio]`).

No modo local há **proteção contra path traversal**: qualquer caminho que
escape do `base_dir` (`../`, absoluto, symlink) levanta `NotFoundException`
— o mesmo 404 de arquivo inexistente, então o cliente nunca distingue "não
existe" de "proibido".

## Disco local

```python
# src/api/routers/files.py
from fastapi import APIRouter
from starlette.responses import Response

from tempest_fastapi_sdk import DownloadUtils

router = APIRouter(prefix="/files", tags=["files"])
downloads = DownloadUtils("var/uploads")


@router.get("/{name}")
async def download(name: str) -> Response:
    """Baixa um arquivo de var/uploads (forçando download)."""
    return await downloads.download(name)
```

## MinIO / S3

Mesmo código, só muda o construtor — `download(key)` faz proxy do objeto do
bucket (sem cair em disco, sem carregar inteiro na memória):

```python
from tempest_fastapi_sdk import AsyncMinIOClient, DownloadUtils

from src.core.settings import settings

minio = AsyncMinIOClient(**settings.minio_kwargs())
downloads = DownloadUtils(minio)


@router.get("/files/{name}")
async def download(name: str) -> Response:
    """Baixa o objeto do bucket via streaming, atrás do auth da app."""
    return await downloads.download(name, subdir="invoices")
```

Parâmetros do `download`: `subdir=` (pasta local / prefixo da key),
`filename=` (nome mostrado ao cliente), `media_type=` (senão vem do
content-type do objeto / extensão), `as_attachment=False` (servir
**inline** — ex.: abrir um PDF no navegador), `headers=`.

!!! tip "Proxy (app) vs presigned (direto)"
    `download()` faz o **proxy** pela app — ideal quando o download precisa
    passar pelo auth ou o MinIO não é público. Quando o cliente pode falar
    direto com o MinIO, prefira `presigned_get_url` (veja
    [Storage](storage.md)) e devolva um redirect — offload total do tráfego.

## Servir um arquivo do disco (controle fino)

No modo local, `file_response` dá controle direto e devolve um `FileResponse`
transmitido em chunks pelo Starlette (suporta range requests):

```python
return downloads.file_response(name, subdir="invoices", as_attachment=False)
```

Parâmetros: `subdir=`, `filename=`, `media_type=`, `as_attachment=`,
`headers=`. (Só no modo local; num `DownloadUtils` MinIO levanta
`RuntimeError` — use `download()`.)

!!! danger "Path traversal é bloqueado por construção"
    `downloads.file_response("../../etc/passwd")` levanta
    `NotFoundException` (404), não vaza o arquivo. Sempre construa o
    `DownloadUtils` com um `base_dir` dedicado a conteúdo servível.

## Transmitir bytes gerados na hora

Quando o payload é produzido em runtime (relatório, zip em memória, bytes
descriptografados) e **não** vem do disco, use `stream` — aceita `bytes`,
um iterável sync ou um async-iterable:

```python
from collections.abc import AsyncIterator

from fastapi.responses import StreamingResponse


@router.get("/report.csv")
async def report() -> StreamingResponse:
    """Gera um CSV sob demanda e baixa como report.csv."""
    async def rows() -> AsyncIterator[bytes]:
        yield b"id,name\n"
        for i in range(1000):
            yield f"{i},item-{i}\n".encode()

    return downloads.stream(rows(), filename="report.csv", media_type="text/csv")
```

## Header `Content-Disposition`

Para montar o header manualmente (fora do `DownloadUtils`), use
`build_content_disposition` — ela escapa o nome do arquivo corretamente
(RFC 5987, com fallback ASCII):

```python
from tempest_fastapi_sdk import build_content_disposition

header: str = build_content_disposition("relatório 2026.pdf", as_attachment=True)
# -> attachment; filename="relatorio 2026.pdf"; filename*=UTF-8''relat%C3%B3rio%202026.pdf
```

## Recap

- `DownloadUtils(pasta)` ou `DownloadUtils(minio_client)` — backend no construtor.
- `await downloads.download(key, ...)` — unificado: `FileResponse` (local) ou streaming (MinIO).
- `stream(content, filename=...)` para bytes/geradores produzidos na hora (qualquer modo).
- `file_response(...)` é local-only (controle fino); MinIO usa `download()`.
- `as_attachment=False` serve inline; `as_attachment=True` (default) força download.
- Local: path traversal vira `NotFoundException` — seguro por construção.
