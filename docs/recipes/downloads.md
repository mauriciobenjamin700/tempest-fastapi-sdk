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


minio = AsyncMinIOClient(**settings.storage_kwargs())
downloads = DownloadUtils(minio)


@router.get("/files/{name}")
async def download(name: str, request: Request) -> Response:
    """Baixa o objeto do bucket via streaming, atrás do auth da app."""
    return await downloads.download(name, subdir="invoices", request=request)
```

Parâmetros do `download`: `subdir=` (pasta local / prefixo da key),
`filename=` (nome mostrado ao cliente), `media_type=` (senão vem do
content-type do objeto / extensão), `as_attachment=False` (pede
**inline** — ex.: abrir um PDF no navegador; só vale para tipo seguro, veja
[abaixo](#headers-de-seguranca-e-o-que-vai-inline)), `request=` (no modo MinIO,
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

## Headers de segurança e o que vai inline

O arquivo que você serve chega ao navegador **pela origem da sua API**, e com
o `Content-Type` que, no fluxo comum, quem fez o upload declarou. Um `.html`
enviado por um usuário e servido `inline` seria uma página da sua API,
rodando script com a sessão de quem abriu o link.

Por isso toda resposta de download — `download`, `file_response`, `stream` e
o `download_response` do MinIO — sai com os mesmos headers do
`HardenedStaticFiles` (`DEFAULT_STATIC_SECURITY_HEADERS`):

- `X-Content-Type-Options: nosniff`
- `Content-Security-Policy: default-src 'none'; sandbox`
- `Cross-Origin-Resource-Policy: same-site`

E `as_attachment=False` virou um **pedido**: só vira `inline` quando o tipo
da resposta está em `INLINE_SAFE_MEDIA_TYPES` — imagens raster (PNG, JPEG,
GIF, WebP, AVIF), `application/pdf`, `text/plain` e áudio/vídeo comuns.
Qualquer outro tipo sai como `attachment`. `text/html` e `image/svg+xml`
ficam fora de propósito: os dois carregam script.

```python
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import FileResponse

from tempest_fastapi_sdk import DownloadUtils

Path("uploads").mkdir(exist_ok=True)
Path("uploads/evil.html").write_text("<script>window.ran = true</script>")
Path("uploads/photo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

downloads = DownloadUtils("uploads")
app = FastAPI()


@app.get("/files/{name}")
async def show_file(name: str) -> FileResponse:
    """Pede inline; o SDK decide se o tipo pode."""
    return downloads.file_response(name, as_attachment=False)


client = TestClient(app)
for name in ("photo.png", "evil.html"):
    headers = client.get(f"/files/{name}").headers
    print(name, "->", headers["content-disposition"].split(";")[0])
    print("  x-content-type-options:", headers["x-content-type-options"])
    print("  content-security-policy:", headers["content-security-policy"])
    print("  cross-origin-resource-policy:", headers["cross-origin-resource-policy"])
```

Rodando, a saída é:

```text
photo.png -> inline
  x-content-type-options: nosniff
  content-security-policy: default-src 'none'; sandbox
  cross-origin-resource-policy: same-site
evil.html -> attachment
  x-content-type-options: nosniff
  content-security-policy: default-src 'none'; sandbox
  cross-origin-resource-policy: same-site
```

!!! tip "Header seu vence"
    Passe `headers={"Content-Security-Policy": "..."}` e o seu valor substitui
    o default (a comparação ignora maiúscula/minúscula, então não sai
    duplicado). Os outros dois continuam.

!!! info "No modo MinIO, o tipo conferido é o do objeto"
    `download` / `download_response` leem o tipo guardado no bucket (o
    `stat`) quando você não passa `media_type=`. Passando, vale o seu. No
    modo `X-Accel-Redirect` o objeto não é consultado — veja
    [Storage](storage.md#o-bloco-do-nginx).

## Header `Content-Disposition`

Para montar o header manualmente (fora do `DownloadUtils`), use
`build_content_disposition` — ela escapa o nome do arquivo corretamente
(RFC 5987, com fallback ASCII):

```python
from tempest_fastapi_sdk import build_content_disposition

header: str = build_content_disposition("relatorio 2026.pdf", as_attachment=True)
# -> attachment; filename="relatorio 2026.pdf"; filename*=UTF-8''relatorio%202026.pdf
```

Para `inline`, passe também o tipo que a resposta vai carregar:
`build_content_disposition("foto.png", as_attachment=False, media_type="image/png")`.
Sem `media_type=`, ou com um tipo fora de `INLINE_SAFE_MEDIA_TYPES`, o valor
sai `attachment` — a mesma regra dos helpers de download.

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
- `as_attachment=True` (default) força download; `as_attachment=False` só vira `inline` para tipo em `INLINE_SAFE_MEDIA_TYPES` — HTML e SVG saem como `attachment`.
- Toda resposta de download sai com `nosniff`, a CSP `sandbox` e o CORP `same-site`; header com o mesmo nome passado em `headers=` vence.
- Local: path traversal vira `NotFoundException` — seguro por construção.
- Sem `media_type=`, o tipo sai de `guess_media_type`: `.xlsx`/`.docx`/`.pptx` acertam também numa imagem slim, sem `/etc/mime.types`.
