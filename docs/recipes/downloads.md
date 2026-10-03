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
from fastapi import APIRouter, Request, Response

from tempest_fastapi_sdk import AsyncMinIOClient, DownloadUtils

from src.core.settings import settings

router = APIRouter()


minio = AsyncMinIOClient(**settings.minio_kwargs())
downloads = DownloadUtils(minio)


@router.get("/files/{name}")
async def download(name: str, request: Request) -> Response:
    """Baixa o objeto do bucket via streaming, atrás do auth da app."""
    return await downloads.download(name, subdir="invoices", request=request)
```

Parâmetros do `download`: `subdir=` (pasta local / prefixo da key),
`filename=` (nome mostrado ao cliente), `media_type=` (senão vem do
content-type do objeto / extensão), `as_attachment=False` (servir
**inline** — ex.: abrir um PDF no navegador), `request=` (no modo MinIO,
responde `Range` com `206` e `If-None-Match`/`If-Modified-Since` com `304`
— detalhe na [receita de storage](storage.md#streaming-de-download)),
`cache_control=` (valor do `Cache-Control`), `headers=`.

!!! warning "Sem `request=`, o MinIO responde sempre `200` inteiro"
    O modo local não precisa dele: o `FileResponse` do Starlette lê o `Range`
    direto do request. No modo MinIO, sem `request=` o `<video>` não avança e
    o download interrompido recomeça do zero.

!!! tip "Proxy (app) vs presigned (direto)"
    `download()` faz o **proxy** pela app — ideal quando o download precisa
    passar pelo auth ou o MinIO não é público. Quando o cliente pode falar
    direto com o MinIO, prefira `presigned_get_url` (veja
    [Storage](storage.md)) e devolva um redirect — offload total do tráfego.

## Servir um arquivo do disco (controle fino)

No modo local, `file_response` dá controle direto e devolve um `FileResponse`
transmitido em chunks pelo Starlette (suporta range requests):

```python
from fastapi import APIRouter
from starlette.responses import FileResponse

from tempest_fastapi_sdk import DownloadUtils

router = APIRouter(prefix="/invoices", tags=["invoices"])
downloads = DownloadUtils("./uploads")


@router.get("/{name}")
async def show_invoice(name: str) -> FileResponse:
    """Abre ./uploads/invoices/<name> inline no navegador."""
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

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from tempest_fastapi_sdk import DownloadUtils

downloads = DownloadUtils("./uploads/invoices")

router = APIRouter()


@router.get("/report.csv")
async def report() -> StreamingResponse:
    """Gera um CSV sob demanda e baixa como report.csv."""
    async def rows() -> AsyncIterator[bytes]:
        yield b"id,name\n"
        for i in range(1000):
            yield f"{i},item-{i}\n".encode()

    return downloads.stream(rows(), filename="report.csv", media_type="text/csv")
```

## `Content-Type` sem depender da imagem

Quando você não passa `media_type=`, o `DownloadUtils` adivinha pelo nome do
arquivo. O `mimetypes` do Python sozinho não serve para isso: a tabela
embutida dele não tem `.xlsx`, `.docx`, `.pptx`, `.odt`, `.ods` nem `.ogg`
(medido no Python 3.11, 3.12 e 3.13), e ele só os conhece quando o host tem
`/etc/mime.types`. Na sua máquina tem; na `python:3.13-slim` — a base do
Dockerfile que o `tempest new` gera — não tem, e o mesmo `.xlsx` saía como
`application/octet-stream` só em produção.

Por isso o palpite passa por `guess_media_type`, que consulta uma tabela do
SDK antes do `mimetypes`. Medido dentro da `python:3.13-slim`:

| Arquivo | `mimetypes.guess_type` | `guess_media_type` |
| --- | --- | --- |
| `a.xlsx` | `None` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| `a.docx` | `None` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| `a.ods` | `None` | `application/vnd.oasis.opendocument.spreadsheet` |
| `a.pdf` | `application/pdf` | `application/pdf` |

Use a mesma função (ou as constantes) quando montar a resposta à mão:

```python
from tempest_fastapi_sdk import XLSX_MEDIA_TYPE, guess_media_type

media_type: str | None = guess_media_type("exports/Orçamento.XLSX")
print(media_type == XLSX_MEDIA_TYPE)
# -> True, no host e no container
```

`XLSX_MEDIA_TYPE`, `DOCX_MEDIA_TYPE` e `PPTX_MEDIA_TYPE` ficam em
`tempest_fastapi_sdk.utils` (e no topo do pacote); `XLSX_MEDIA_TYPE` também
em `tempest_fastapi_sdk.spreadsheet`. Extensão que nem a tabela nem o
`mimetypes` conhecem continua `None`, e o download cai em
`application/octet-stream`.

## Header `Content-Disposition`

Para montar o header manualmente (fora do `DownloadUtils`), use
`build_content_disposition` — ela escapa o nome do arquivo corretamente
(RFC 5987, com fallback ASCII):

```python
from tempest_fastapi_sdk import build_content_disposition

header: str = build_content_disposition("relatorio 2026.pdf", as_attachment=True)
# -> attachment; filename="relatorio 2026.pdf"; filename*=UTF-8''relatorio%202026.pdf
```

!!! warning "O nome é tratado como não confiável"
    No uso normal ele é `UploadFile.filename`, ou seja, escolhido pelo cliente.
    Além de reduzir a basename (nenhum path passa), a função remove **todo**
    caractere de controle: um nome com `\r\n` produzia um header com quebra de
    linha real, que um servidor ASGI sem validação de header value (uvicorn no
    `httptools`) escreve no socket como está — deixando quem enviou o arquivo
    acrescentar headers próprios na sua resposta.

    ```python
    build_content_disposition("rel\r\nX-Injected: 1.pdf")
    # -> attachment; filename="relX-Injected: 1.pdf"; filename*=...
    #    uma linha só, sempre
    ```

## Recap

- `DownloadUtils(pasta)` ou `DownloadUtils(minio_client)` — backend no construtor.
- `await downloads.download(key, ...)` — unificado: `FileResponse` (local) ou streaming (MinIO).
- `stream(content, filename=...)` para bytes/geradores produzidos na hora (qualquer modo).
- `file_response(...)` é local-only (controle fino); MinIO usa `download()`.
- `as_attachment=False` serve inline; `as_attachment=True` (default) força download.
- Local: path traversal vira `NotFoundException` — seguro por construção.
- Sem `media_type=`, o tipo sai de `guess_media_type`: `.xlsx`/`.docx`/`.pptx` acertam também numa imagem slim, sem `/etc/mime.types`.
