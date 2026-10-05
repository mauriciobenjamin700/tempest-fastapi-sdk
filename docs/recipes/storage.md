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
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from tempest_fastapi_sdk import AsyncMinIOClient

from src.core.settings import settings


# settings.minio_kwargs() mapeia MINIO_* e STORAGE_ACCEL_* -> endpoint,
# access_key, secret_key, default_bucket, secure, region, public_endpoint,
# public_secure, accel_redirect e accel_prefix: nada a repetir campo a campo.
storage = AsyncMinIOClient(**settings.minio_kwargs())


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
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

Quando o download precisa passar pelo backend — arquivo com autorização, ou
bucket que o navegador não alcança —, `download_response` faz stat + stream
numa chamada só, chunk a chunk, sem carregar o objeto na memória. O
[`DownloadUtils(minio)`](downloads.md) embrulha a mesma chamada.

Passe o `request`. É ele que deixa o `<video>` avançar, o download retomar e
a imagem ser revalidada em vez de baixada de novo:

```python
from fastapi import APIRouter, Request
from starlette.responses import Response

from src.api.app import storage

router = APIRouter()


@router.get("/files/{key}")
async def download_file(key: str, request: Request) -> Response:
    """Stream do objeto no bucket padrão, com Range e revalidação."""
    return await storage.download_response(
        key,
        request=request,
        as_attachment=False,
        cache_control="private, max-age=3600",
    )
```

Toda resposta leva `Accept-Ranges: bytes`, o `ETag` do objeto (entre aspas)
e o `Last-Modified`, mais os headers de segurança de todo download
(`nosniff`, a CSP `sandbox` e o CORP `same-site`). O `as_attachment=False`
só vira `inline` quando o tipo guardado no objeto está em
`INLINE_SAFE_MEDIA_TYPES` — um `text/html` sai como `attachment`. Detalhe em
[Downloads](downloads.md#headers-de-seguranca-e-o-que-vai-inline). Com o `request` em mãos, a rota responde assim:

| O cliente manda | Resposta |
| --- | --- |
| nada de especial | `200`, objeto inteiro |
| `Range: bytes=1000-1999`, `bytes=1000-` ou `bytes=-300` | `206` com `Content-Range`, lendo **só** o trecho no bucket |
| `Range` que começa no fim do objeto ou depois | `416` com `Content-Range: bytes */<tamanho>` |
| `Range: bytes=0-1,5-6` (vários trechos) | `200`, objeto inteiro |
| `If-None-Match` com o `ETag` atual | `304`, sem corpo e sem ler o objeto |
| `If-Modified-Since` não mais antigo que o objeto (sem `If-None-Match`) | `304` |
| `Range` + `If-Range` que não bate mais com o objeto | `200`, objeto inteiro |

Cada linha da tabela é um teste contra um MinIO de verdade em container
(`tests/storage/test_download_live.py`, `make test-docker`). O `206` passa
`offset`/`length` ao `get_object` do `minio-py`, que manda o `Range` ao
bucket — o teste espiona essa chamada e confere que foi só o trecho.

!!! info "Por que vários trechos viram `200`"
    O RFC 9110 permite ignorar o `Range` e responder o objeto inteiro. A
    alternativa, um corpo `multipart/byteranges`, não é o que `<video>`,
    `<audio>` ou gerenciador de download pedem — eles mandam um trecho só.

!!! tip "`If-Range` protege a retomada"
    O gerenciador de download que retoma manda `If-Range` com o `ETag` que
    tinha. Se o arquivo mudou no meio, o SDK ignora o `Range` e manda o
    arquivo novo inteiro, em vez de emendar bytes de duas versões.

O lado do navegador não precisa de nada além da tag:

```html
<video src="/files/aula-01.mp4" controls preload="metadata"></video>
```

Medido num Chromium (Playwright) com um MP4 de 60 s e 30 033 093 bytes servido
pela rota acima: o player abriu com `Range: bytes=0-` (`206`) e, ao pular para
os 50 s, pediu `bytes=24969216-30033092`, respondido com `206`; o vídeo parou
em `currentTime == 50`, sem erro de mídia.

!!! warning "Sem `request`, é o comportamento antigo"
    `download_response(key)` sem `request` não lê header nenhum da requisição
    e responde sempre `200` com o objeto inteiro — só os headers
    `Accept-Ranges`/`ETag`/`Last-Modified` são novos. Nesse modo o `<video>`
    não avança.

Quer só os bytes, sem montar resposta? `stream_object(key, offset=, length=)`
devolve o iterador do trecho.

### Entrega pelo nginx — `X-Accel-Redirect`

No `download_response`, todo byte passa pelo processo Python. Funciona, mas
para vídeo e tráfego alto você quer outra divisão de trabalho: **o backend só
autoriza, e o nginx entrega**.

A rota confere a permissão e responde uma resposta **vazia** com um header:

```text
X-Accel-Redirect: /_bucket/media/aula.mp4?X-Amz-Algorithm=...&X-Amz-Signature=...
```

O nginx intercepta esse header, segue para uma `location` interna, busca o
arquivo no bucket pela rede privada e faz o streaming para o cliente. O
backend não carrega bytes, o bucket continua privado e o cliente só vê o
domínio do backend.

#### Quando escolher cada modo

| | Proxy (`download_response`) | Redirect (`accel_redirect_response`) |
| --- | --- | --- |
| Quem move os bytes | o processo Python | o nginx |
| Precisa de nginx na frente | não | sim, com a `location` interna abaixo |
| `206` / `304` | respondidos pelo SDK | respondidos pelo bucket, atrás do nginx |
| Bom para | arquivo pequeno, dev local, deploy sem nginx | vídeo, arquivo grande, tráfego alto |

#### Liga por configuração

Os dois modos saem da **mesma rota**. `serve_object` escolhe pelo que o
cliente recebeu no construtor, e o `MinIOSettings` mapeia duas variáveis
novas:

```bash
# .env
MINIO_ENDPOINT=bucket:9000          # endpoint INTERNO: é contra ele que o SDK assina
STORAGE_ACCEL_REDIRECT=true         # false (default) = proxy pelo app
STORAGE_ACCEL_PREFIX=/_bucket/      # a location interna do nginx
```

```python
from fastapi import APIRouter, Request
from starlette.responses import Response
from tempest_fastapi_sdk import AsyncMinIOClient, guess_media_type

from src.core.settings import settings

router = APIRouter()
storage = AsyncMinIOClient(**settings.minio_kwargs())


@router.get("/files/{key:path}")
async def download_file(key: str, request: Request) -> Response:
    """Autoriza e entrega — pelo app ou pelo nginx, conforme o .env."""
    return await storage.serve_object(
        key,
        request=request,
        media_type=guess_media_type(key),
        as_attachment=False,
    )
```

!!! warning "No modo redirect, `inline` exige `media_type=`"
    O `accel_redirect_response` não faz `stat`, então não sabe com que tipo o
    bucket vai responder. Sem `media_type=`, o `as_attachment=False` sai
    `attachment`. Com ele, o tipo é assinado na URL (o bucket responde com
    esse, não com o guardado) e só vira `inline` se estiver em
    `INLINE_SAFE_MEDIA_TYPES`. O `guess_media_type(key)` acima resolve pela
    extensão da chave: `aula.mp4` vai inline, `pagina.html` vira download.
    O default do `accel_redirect_response` é `as_attachment=True`.

Com `STORAGE_ACCEL_REDIRECT=false`, é o `download_response` da seção
anterior. Com `true`, é o `accel_redirect_response`, e o `request` não é
usado: o nginx repassa sozinho o `Range` e o `If-None-Match` do cliente ao
bucket.

!!! info "`accel_redirect_response` direto"
    Quer o redirect numa rota só, sem depender da flag? Chame
    `storage.accel_redirect_response(key, internal_prefix="/_bucket/",
    expires=timedelta(minutes=5), filename=..., media_type=...,
    as_attachment=False, cache_control=...)`. Ele **nunca** usa o
    `MINIO_PUBLIC_ENDPOINT`: a URL é assinada contra o `MINIO_ENDPOINT`, que é
    o host que o nginx vai chamar.

#### O bloco do nginx

```nginx
upstream app {
    server app:8000;
}

server {
    listen 80;
    server_name exemplo.com;

    location / {
        proxy_pass http://app;
        proxy_set_header Host $host;
    }

    location /_bucket/ {
        internal;
        proxy_pass http://bucket:9000/;
        proxy_set_header Host bucket:9000;
        proxy_hide_header X-Content-Type-Options;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Content-Security-Policy "default-src 'none'; sandbox" always;
        add_header Cross-Origin-Resource-Policy "same-site" always;
    }
}
```

As linhas da `location /_bucket/` têm, cada uma, um porquê:

- **`internal;`** — só o nginx entra aqui, seguindo um `X-Accel-Redirect`. O
  cliente que pede `/_bucket/...` direto recebe `404`.
- **`proxy_pass http://bucket:9000/;` com a barra final** — com a barra, o
  nginx troca o prefixo `/_bucket/` por `/`, e o bucket recebe
  `/media/aula.mp4?X-Amz-...`, exatamente o path que foi assinado. Sem a
  barra, o prefixo vai junto, o MinIO lê `_bucket` como nome do bucket e
  responde `400 InvalidBucketName`.
- **`proxy_set_header Host bucket:9000;`** — a assinatura SigV4 cobre o
  `Host`. Ele precisa ser o `MINIO_ENDPOINT` que assinou a URL. O perigo é a
  herança: `proxy_set_header` definido no `server` (o `Host $host` que quase
  todo config tem para o app) é herdado pela `location` que não define
  nenhum. Aí o bucket recebe `Host: exemplo.com` e responde
  `403 SignatureDoesNotMatch` — o sintoma é "o arquivo não carrega".
- **Os `add_header ... always`** — são os headers de segurança de todo
  download, e aqui é o único lugar onde eles chegam ao cliente. O nginx
  responde com a resposta do bucket, não com a resposta vazia do app: um
  header que o app pusesse nela não passaria (medido — por isso o SDK nem
  põe). Sem o `always`, o nginx só acrescenta o header a uma parte dos
  status — é como a documentação do `add_header` o define.
- **`proxy_hide_header X-Content-Type-Options;`** — o MinIO já manda
  `nosniff`; sem esconder o dele, o cliente recebe `nosniff, nosniff`
  (medido). Esconder e reemitir deixa um valor só, qualquer que seja o
  storage atrás.

!!! warning "O `Content-Type` e o `Content-Disposition` vão dentro da URL"
    O SDK não põe esses headers na resposta vazia: ele os assina na URL como
    os overrides `response-content-type`, `response-content-disposition` e
    `response-cache-control` do S3, e o bucket os devolve junto com os bytes.
    Medido no nginx 1.27.5: o `Content-Type` do bucket vence o que o app
    define, e um `Content-Disposition` definido nos dois chega duplicado no
    cliente.

!!! danger "Sem nginx na frente, a flag vaza a URL assinada"
    Com `STORAGE_ACCEL_REDIRECT=true` e nenhum nginx interceptando, o cliente
    recebe um `200` vazio com o `X-Accel-Redirect` — path e assinatura
    incluídos. A URL aponta para o host interno e vale por `expires` (5
    minutos por padrão). Ligue a flag só no ambiente que tem o bloco acima.

#### O que foi medido

`tests/storage/test_accel_redirect_live.py` (`make test-docker`) sobe um
MinIO e um nginx em container e o app com uvicorn, e baixa pela rota. O
mesmo arquivo passou inteiro no nginx 1.22.1, 1.27.5 e 1.29.8:

| Requisição | Resultado |
| --- | --- |
| `GET /files/clip.mp4` | `200`, bytes do objeto, `Content-Type` guardado no bucket |
| `GET /files/clip.mp4` com `Range: bytes=100-199` | `206`, `Content-Range: bytes 100-199/1048576` |
| `GET /files/clip.mp4` com `If-None-Match: <etag>` | `304` |
| chave com espaço, acento e `+` | `200` |
| `GET /_bucket/media/clip.mp4` direto do cliente | `404` |
| `location` que herda `Host $host` | `403 SignatureDoesNotMatch` |
| `proxy_pass` sem a barra final | `400 InvalidBucketName` |
| `GET /files/clip.mp4`, `location` com os `add_header` | `X-Content-Type-Options`, CSP e CORP, um valor cada |
| `location` sem os `add_header` | `200` sem CSP nem CORP, mesmo com o app pondo os dois na resposta vazia |
| `as_attachment=False` sem `media_type=` | `Content-Disposition: attachment` |
| mesma rota com `STORAGE_ACCEL_REDIRECT=false` e `Range: bytes=-10` | `206` pelo app |

No teste, bucket, app e nginx rodam na rede do host, então o endpoint
interno é `127.0.0.1:<porta>` em vez de `bucket:9000`. O que importa é o
mesmo: o `Host` que o nginx manda é o host que assinou a URL.

### Arquivo privado com URL assinada da própria rota

Quando o frontend só fala com o backend, um arquivo privado precisa de uma URL
**do backend** que o navegador abra sozinho. `<img src>`, `<video src>` e um
link de download não mandam `Authorization: Bearer`, e cookie de sessão entre
o domínio do app e o da API esbarra em `SameSite`. A URL presignada do bucket
não serve: ela leva o navegador ao bucket, não à sua rota.

A saída tem o mesmo formato da URL presignada, só que aponta para a rota do
app. O mapper que já autorizou o caller gera
`/api/files/<key>?expires=<ts>&signature=<mac>` com `sign_path`, e a rota
confere a assinatura com `make_signed_path_dependency` antes de fazer o
streaming:

```python
from datetime import timedelta

from fastapi import Depends, FastAPI, Request
from starlette.responses import Response
from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    BaseSchema,
    make_signed_path_dependency,
    register_exception_handlers,
    sign_path,
)

FILES_SECRET: str = "troque-pelo-settings.JWT_SECRET"
FILES_PURPOSE: str = "files"

storage = AsyncMinIOClient(
    endpoint="minio.internal:9000",
    access_key="minioadmin",
    secret_key="minioadmin",
    default_bucket="uploads",
)
signed_file = make_signed_path_dependency(secret=FILES_SECRET, purpose=FILES_PURPOSE)


class AttachmentResponseSchema(BaseSchema):
    """Anexo como o frontend recebe: a chave e a URL que o navegador abre."""

    key: str
    url: str


def to_attachment_response(key: str) -> AttachmentResponseSchema:
    """Mapeia a chave guardada para o schema, com a URL já assinada."""
    return AttachmentResponseSchema(
        key=key,
        url=sign_path(
            f"/api/files/{key}",
            secret=FILES_SECRET,
            expires_in=timedelta(minutes=15),
            purpose=FILES_PURPOSE,
        ),
    )


app = FastAPI()
register_exception_handlers(app)


@app.get("/api/attachments/{key:path}")
async def get_attachment(key: str) -> AttachmentResponseSchema:
    """A sua autorização entra aqui: JWT, dono do recurso, papel."""
    return to_attachment_response(key)


@app.get("/api/files/{key:path}", dependencies=[Depends(signed_file)])
async def download_file(key: str, request: Request) -> Response:
    """Só chega aqui com URL assinada e dentro da validade."""
    return await storage.download_response(
        key, request=request, as_attachment=False
    )
```

O `url` que o frontend recebe para a chave `aulas/aula 01.mp4`, assinado às
12:00 UTC de 1º de outubro de 2026, é:

```text
/api/files/aulas/aula%2001.mp4?expires=1790856900&signature=e9vPzlJkaaLXY6fJ0dSsV6RS6D3pOElruFdeskdikck
```

E vai direto na tag, sem header nenhum:

```html
<video src="/api/files/aulas/aula%2001.mp4?expires=1790856900&signature=e9vPzlJkaaLXY6fJ0dSsV6RS6D3pOElruFdeskdikck" controls></video>
```

Pedaço por pedaço:

- **`sign_path` no mapper.** Ele roda depois da autorização, então só quem
  pôde ver o anexo recebe a URL. Quem tem a URL abre o arquivo até o
  `expires` — é uma capacidade, como a URL presignada, e por isso a validade
  é curta.
- **`make_signed_path_dependency` na rota.** A dependency lê `expires` e
  `signature` da query e confere contra o path decodificado da requisição. O
  handler só roda com assinatura válida e dentro da validade: o bucket nem é
  consultado numa URL recusada.
- **`download_response(..., request=)` continua igual.** O `Range` passa pela
  assinatura, então o `<video>` avança e o download retoma do mesmo jeito.
  Trocando por `serve_object`, a mesma dependency guarda o modo
  `X-Accel-Redirect` da seção anterior.

`tests/storage/test_signed_url_live.py` (`make test-docker`) monta essa rota
contra um MinIO em container e mede: `200` com o objeto inteiro pela URL
assinada, `206` com `Range: bytes=1000-1999` e só o trecho, e `403` numa URL
adulterada ou sem assinatura, sem nenhum `stat_object` no bucket. Com
`serve_object` e `accel_redirect=True`, a URL assinada responde o
`X-Accel-Redirect` e a adulterada responde `403` sem ele.

#### O que a assinatura cobre

- **O path e o `expires`.** A assinatura é
  `HMAC-SHA256(chave, "<expires>\n<path>")`. Mudar a chave do objeto ou
  esticar o `expires` quebra o MAC. Outros parâmetros de query **não** entram.
- **O `purpose`.** A chave do MAC é derivada do segredo com o `purpose`
  (`HMAC-SHA256(secret, "tempest-fastapi-sdk.signed-url.v1\0" + purpose)`), e
  nunca é o segredo cru. Por isso reusar o `JWT_SECRET` é seguro, e uma URL
  assinada para `"files"` não vale numa rota protegida com `"email-link"`.
- **Comparação em tempo constante**, com `hmac.compare_digest`.

| A requisição | Resposta |
| --- | --- |
| URL assinada, dentro da validade | o handler roda |
| `expires` alterado | `403`, `code: "SIGNED_URL_INVALID"` |
| path alterado | `403`, `code: "SIGNED_URL_INVALID"` |
| assinada com outro `purpose` ou outro segredo | `403`, `code: "SIGNED_URL_INVALID"` |
| sem `expires`/`signature`, ou `expires` que não é número | `403`, `code: "SIGNED_URL_INVALID"` (nunca `422`) |
| autêntica, mas no segundo do `expires` ou depois | `403`, `code: "SIGNED_URL_EXPIRED"` |

Cada linha é um teste de `tests/api/test_signed_path_dependency.py`. O corpo
segue o envelope do SDK:

```json
{"detail": "Signed URL has expired", "code": "SIGNED_URL_EXPIRED", "details": {}}
```

!!! info "Por que `403`, e não `401`"
    Uma URL assinada é uma capacidade, não um login: nenhuma credencial que o
    cliente mande conserta a URL. O S3 responde `403` para presignada vencida
    ou adulterada pelo mesmo motivo. E `401` dispararia o interceptor de
    "sessão expirada, volte ao login" que quase todo frontend tem, com a
    sessão do usuário intacta. Os dois `code` existem para o frontend separar
    "peça uma URL nova ao backend" (`SIGNED_URL_EXPIRED`) de "este link foi
    adulterado" (`SIGNED_URL_INVALID`).

#### A regra de encoding do path

`sign_path` recebe o path **decodificado**, do jeito que a rota vai vê-lo em
`request.scope["path"]` (é o que o `{key:path}` recebe), e devolve a URL já
com percent-encoding. O Starlette decodifica o path antes de rotear, então os
dois lados assinam a mesma string:

- `aula 01.mp4` viaja como `aula%2001.mp4`, `100%.pdf` como `100%25.pdf`, e
  `á.txt` como `%C3%A1.txt`; a rota recebe a chave original;
- `/api/files/a%2Fb.pdf` e `/api/files/a/b.pdf` chegam à rota como o mesmo
  path, com a mesma chave `a/b.pdf`, e valem com a mesma assinatura.

Medido no Starlette 0.46.0 (o piso que `fastapi>=0.141.1` aceita) e no 1.6.0.

!!! warning "Passe o path decodificado, nunca um já codificado"
    `sign_path("/api/files/aula%2001.mp4", ...)` assina a chave literal
    `aula%2001.mp4`, que vira `aula%252001.mp4` na URL. Monte o path com a
    chave como ela está guardada.

!!! tip "App montado e `root_path`"
    Sob `app.mount("/v1", sub_app)`, a rota vê `/v1/api/files/...`, então
    assine com o prefixo do `mount`. Já o `root_path` de proxy (o prefixo que
    o proxy reverso tira antes de repassar) **não** aparece em
    `request.scope["path"]`: assine sem ele e acrescente o prefixo na URL que
    o navegador recebe.

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

### Operações em lote — presign / upload / download *(v0.133.0+)*

Endpoints de **listagem** normalmente precisam resolver **uma chave por linha** — uma página de perfis, cada um com sua foto. Fazer isso num `for` com `await presigned_get_url(...)` **serializa** os N hops de thread (cada chamada do `minio` roda em `asyncio.to_thread`). Os três métodos batch disparam o fan-out de uma vez, com um teto de concorrência:

- `presigned_get_urls(keys)` → `dict[str, str]` (chave → URL)
- `put_objects(items)` → `dict[str, str]` (chave → ETag)
- `get_objects_bytes(keys)` → `dict[str, bytes]` (chave → payload)

```python
from fastapi import APIRouter

from src.api.app import storage

router = APIRouter()


@router.post("/files/urls")
async def sign_many(keys: list[str]) -> dict[str, str]:
    """Assina uma página de chaves de uma vez, em vez de uma por request."""
    return await storage.presigned_get_urls(keys)
```

Chaves **duplicadas são deduplicadas** (cada objeto é assinado/baixado uma vez), e o retorno é um `dict` — faça o lookup por chave com `result.get(row.key)`.

!!! tip "No serviço: `file_urls` para páginas"
    Se o serviço usa `StoredFileServiceMixin`, prefira `file_urls([...])` — o par em lote de `file_url`. Ele **descarta chaves `None`/vazias** e devolve `dict`, ideal pra montar uma página de respostas:

    ```python
    users = [...]  # linhas da página
    urls = await user_service.file_urls([u.profile_picture for u in users])
    for user in users:
        user.profile_picture_url = urls.get(user.profile_picture)  # None se vazia
    ```

!!! note "Semântica fail-fast"
    Os três métodos são **fail-fast**: a primeira falha aborta o lote e propaga (`asyncio.gather` padrão) — mesmo comportamento de rodar as operações uma a uma. Precisa tolerar falha parcial? Rode os itens individualmente e trate cada exceção.

!!! info "Teto de concorrência (`max_concurrency`, padrão 16)"
    Cada operação vai pra um thread do executor default. Agendar milhares de uma vez satura o pool e estoura memória. Um `asyncio.Semaphore` limita quantas rodam ao mesmo tempo, preservando a ordem. Ajuste via `max_concurrency=` (mínimo 1; `0` ou negativo levanta `ValueError`).

Upload em lote usa `PutObjectItem`, que espelha os argumentos por-objeto de `put_object` (content-type, metadata, length para streams):

```python
import asyncio

from tempest_fastapi_sdk import AsyncMinIOClient, PutObjectItem

from src.core.settings import settings

storage = AsyncMinIOClient(**settings.minio_kwargs())

# No seu código estes vêm do disco (`Path(...).read_bytes()`) ou do upload.
thumb_a = b"\xff\xd8\xff\xdb bytes do primeiro JPEG"
thumb_b = b"\xff\xd8\xff\xdb bytes do segundo JPEG"


async def main() -> None:
    """Run this example."""
    etags = await storage.put_objects(
        [
            PutObjectItem(key="thumbs/a.jpg", data=thumb_a, content_type="image/jpeg"),
            PutObjectItem(key="thumbs/b.jpg", data=thumb_b, content_type="image/jpeg"),
        ]
    )


asyncio.run(main())
```

!!! warning "`get_objects_bytes` carrega tudo em memória"
    Como `get_object_bytes`, o lote é para objetos **pequenos** — cada payload vira `bytes` na RAM. Para arquivos grandes, faça streaming individual com `stream_object`.

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
import asyncio

from tempest_fastapi_sdk import AsyncMinIOClient

from src.core.settings import settings

storage = AsyncMinIOClient(**settings.minio_kwargs())


async def main() -> None:
    """Run this example."""
    await storage.copy_object("uploads/draft-1", "uploads/final-1")
    await storage.remove_object("uploads/draft-1")


asyncio.run(main())
```

## Quando NÃO usar `AsyncMinIOClient`

- Quando você precisa de operações **fora** das listadas (SSE-KMS, ACLs S3 v2, bucket replication). Use `storage.client.<método>` direto — `minio-py` continua acessível.
- Para uploads gigantes (> 5 GiB) com retomada — `minio-py` faz multipart automático mas não suporta `tus` ou resume. Considere `tus.io` separadamente.

## Recap

- `AsyncMinIOClient` é fachada async sobre o pacote oficial `minio`, no extra
  `[minio]`: bucket, objeto e URL pré-assinada, que é o que um serviço FastAPI
  costuma precisar.
- A configuração vem do settings mixin, e o cliente é montado no `lifespan` do
  `create_app()` — uma instância por processo, não uma por request.
- URL pré-assinada é a forma de entregar arquivo privado sem passar os bytes
  pelo seu processo.
- Quando os bytes precisam passar pelo backend, `download_response(key,
  request=request)` responde `206`, `304` e `416` — sem o `request`, é sempre
  `200` inteiro.
- Para o nginx entregar no lugar do app, `serve_object` com
  `STORAGE_ACCEL_REDIRECT=true` responde `X-Accel-Redirect`; a `location`
  interna precisa de `proxy_pass` com barra final, `Host` igual ao
  `MINIO_ENDPOINT` e os `add_header` de segurança — o nginx não repassa os
  do app.
- Para o navegador abrir arquivo privado pela **rota do app**, assine o
  path no mapper com `sign_path` e proteja a rota com
  `make_signed_path_dependency`: a assinatura cobre path, `expires` e
  `purpose`, e a recusa é `403` (`SIGNED_URL_INVALID` ou `SIGNED_URL_EXPIRED`).
- Operação fora da fachada não é bloqueada: chame `storage.client.<método>` e
  use o `minio-py` direto, em vez de esperar que a fachada cresça.
- Para alternar disco local e MinIO por configuração, o backend pluggável de
  upload é o caminho — a fachada é para quem já decidiu usar MinIO.

## Próximos passos

- O backend pluggable de upload `MinIOUploadStorage` está disponível desde a v0.24.0 — para o pipeline que alterna disco local ↔ MinIO/S3 via flag de settings, veja a [receita de uploads](uploads.md).
