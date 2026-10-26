# Storage em disco local — `LocalObjectStorageClient`

Seu serviço guarda fotos, exportações e anexos, e até agora isso pedia um
servidor S3 (MinIO, AWS, R2) de pé ao lado da API. Para um serviço que roda
numa máquina só, esse servidor é uma peça a mais para subir, atualizar e
vigiar.

`LocalObjectStorageClient` guarda os objetos **num diretório do próprio
servidor** e responde às mesmas chamadas do
[`AsyncMinIOClient`](storage.md) — `put_object`, `stat_object`,
`stream_object`, `list_objects`, `remove_object`, `remove_objects`,
`presigned_get_url`, `presigned_put_url`. O resto do serviço não muda.

O que muda é o link. Um disco não tem endpoint que o navegador alcance, então
o link "presigned" aponta para a **sua API**, assinado com
[`sign_path`](../reference.md#tempest_fastapi_sdk.utils.signed_url.sign_path), e o
`make_local_storage_router` serve essas rotas.

Não precisa de extra: só biblioteca padrão e o FastAPI que o SDK já traz.

## O primeiro objeto

```python
import asyncio
from datetime import timedelta

from tempest_fastapi_sdk import LocalObjectStorageClient


async def main() -> None:
    """Grava, descreve, lê e assina um objeto no disco."""
    storage = LocalObjectStorageClient(
        "var/storage",
        public_url="http://127.0.0.1:8000",
        signing_secret="troque-por-um-segredo-longo",
    )
    etag = await storage.put_object(
        "relatorios/2026.txt",
        b"conteudo",
        content_type="text/plain",
    )
    stat = await storage.stat_object("relatorios/2026.txt")
    stream = await storage.stream_object("relatorios/2026.txt")
    body = b"".join([chunk async for chunk in stream])
    link = await storage.presigned_get_url(
        "relatorios/2026.txt", expires=timedelta(minutes=10)
    )
    print(etag, stat.size, stat.content_type, body)
    print(link)


asyncio.run(main())
```

Rodando, você vê algo assim:

```text
b59853db2f3ef8f156a72e38c30ba7d2 8 text/plain b'conteudo'
http://127.0.0.1:8000/storage/uploads/relatorios/2026.txt?expires=1792869637&signature=tvsYmCeLaH3y...
```

Pedaço por pedaço:

* `"var/storage"` é a raiz. O cliente cria `objects/`, `meta/` e `tmp/` dentro
  dela.
* O bucket padrão é `"uploads"` (`default_bucket=`). Cada bucket vira uma
  subpasta de `objects/`.
* `put_object` devolve o ETag, o MD5 dos bytes, como o S3 responde num upload
  de uma parte só.
* `stat_object` lê o tamanho e a data do arquivo, e o content type e a
  metadata do arquivo de metadado guardado ao lado.
* O link vale 10 minutos e só abre a rota daquele objeto.

!!! info "Como fica no disco"
    ```text
    var/storage/
      objects/uploads/relatorios/2026.txt          os bytes
      meta/uploads/relatorios/2026.txt.meta.json   content type, metadata, ETag
      tmp/                                         escritas em andamento
    ```
    Toda escrita vai primeiro para `tmp/` e entra no lugar com `os.replace`.
    Quem lê vê o objeto antigo ou o novo, nunca metade de um.

## Servindo os links pela API

O link aponta para `<public_url><route_prefix>/<bucket>/<key>`. Monte o router
**na raiz do app**, sem `prefix` extra: a assinatura cobre o caminho que o app
enxerga, e o router já carrega o `route_prefix` do cliente.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    LocalObjectStorageClient,
    make_local_storage_router,
    register_exception_handlers,
)

storage = LocalObjectStorageClient(
    "var/storage",
    public_url="http://127.0.0.1:8000",
    signing_secret="troque-por-um-segredo-longo",
    route_prefix="/api/storage",
    max_upload_bytes=5 * 1024 * 1024,
)

app = FastAPI()
register_exception_handlers(app)
app.include_router(make_local_storage_router(storage))


@app.get("/api/photos/{name}")
async def photo_link(name: str) -> dict[str, str]:
    """Devolve um link de 15 minutos para a foto (autorize antes)."""
    return {"url": await storage.presigned_get_url(f"photos/{name}")}


@app.post("/api/photos/{name}/upload-url")
async def upload_link(name: str) -> dict[str, str]:
    """Devolve um link de upload direto para a foto."""
    return {"url": await storage.presigned_put_url(f"photos/{name}")}
```

As duas rotas que o router monta:

| Rota | O que faz |
| --- | --- |
| `GET /api/storage/{bucket}/{key}` | confere a assinatura de download e entrega o arquivo com o content type gravado, com `Range` (`206`), `If-None-Match` (`304`) e `416` |
| `PUT /api/storage/{bucket}/{key}` | confere a assinatura de upload e grava o corpo cru com o `Content-Type` da requisição; responde `200` com o `ETag` |

O cliente do app faz o upload assim, sem token: o link é a credencial.

```bash
curl -X PUT -H "Content-Type: image/jpeg" --data-binary @foto.jpg "<url do upload>"
```

!!! tip "Download e upload não se misturam"
    Os links de `GET` e de `PUT` são assinados com propósitos diferentes. Um
    link de download usado num `PUT` responde `403 SIGNED_URL_INVALID` e não
    grava nada, e o contrário também vale.

As respostas de erro do router:

| Situação | Resposta |
| --- | --- |
| sem assinatura, assinatura adulterada ou de outro propósito | `403 SIGNED_URL_INVALID` |
| link expirado | `403 SIGNED_URL_EXPIRED` |
| objeto que não existe (download) | `404 NOT_FOUND` |
| corpo acima de `max_upload_bytes` | `413 FILE_TOO_LARGE`, sem deixar arquivo |

O teto vale mesmo sem `Content-Length`: o corpo é contado enquanto chega e
cortado ao passar do limite.

!!! warning "O payload não é assinado"
    Como no S3, o link de upload só fixa **onde** o arquivo vai, não o que ele
    é. Confira tamanho e tipo com `stat_object` (e os primeiros bytes com
    `stream_object`) antes de confiar no objeto.

## Upload com teto e download com tipo assinado

Os dois recursos do S3 que o `PUT` e o `GET` simples não têm existem aqui
com a mesma chamada, então o serviço troca de backend sem mexer no cliente
do app.

`presigned_post_policy` devolve um `PresignedPost` (URL + campos do
formulário). A rota `POST /api/storage/{bucket}/{key}` lê o
`multipart/form-data` e aplica os limites assinados antes de gravar:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    LocalObjectStorageClient,
    make_local_storage_router,
    register_exception_handlers,
)

storage = LocalObjectStorageClient(
    "var/storage",
    public_url="http://127.0.0.1:8000",
    signing_secret="troque-por-um-segredo-longo",
    route_prefix="/api/storage",
    max_upload_bytes=5 * 1024 * 1024,
)

app = FastAPI()
register_exception_handlers(app)
app.include_router(make_local_storage_router(storage))


@app.post("/api/photos/{name}/form")
async def upload_form(name: str) -> dict[str, object]:
    """Formulário que aceita só JPEG de até 2 MiB."""
    post = await storage.presigned_post_policy(
        f"photos/{name}",
        max_bytes=2 * 1024 * 1024,
        content_type="image/jpeg",
    )
    return {"url": post.url, "fields": post.fields}


@app.get("/api/photos/{name}/inline")
async def inline_link(name: str) -> dict[str, str]:
    """Link que abre a foto inline, como JPEG."""
    url = await storage.presigned_get_url(
        f"photos/{name}",
        media_type="image/jpeg",
        as_attachment=False,
    )
    return {"url": url}
```

| Situação no `POST` | Resposta |
| --- | --- |
| arquivo dentro de `[min_bytes, max_bytes]` | `204`, com o `ETag` |
| acima de `max_bytes` | `413 FILE_TOO_LARGE`, nada gravado |
| abaixo de `min_bytes` | `400 FILE_TOO_SMALL`, nada gravado |
| campo `Content-Type` ou `key` diferente do assinado | `403 SIGNED_URL_INVALID` |
| `max_bytes` (ou outro limite) editado na URL | `403 SIGNED_URL_INVALID` |

Os limites e os overrides do `GET` viajam na query, e o SDK os amarra à
assinatura (`bound_purpose`): mudar um deles verifica como outro propósito e
falha. Um link assinado sem nenhum deles continua valendo como antes.

!!! note "Precisa do extra `[upload]`"
    Ler o formulário exige `python-multipart`. A checagem acontece no
    `presigned_post_policy`, então o extra faltando quebra onde o link é
    emitido, não no upload do usuário.

!!! info "`max_bytes` até `max_upload_bytes`"
    O teto do formulário não passa do teto do store: pedir mais levanta
    `ValueError` ao assinar.

## Escolhendo o backend pelas settings

`StorageSettings` escolhe entre S3 e disco com `STORAGE_BACKEND`, e
`build_storage_client()` monta o cliente certo:

```python
from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    BaseAppSettings,
    LocalObjectStorageClient,
    StorageSettings,
)


class Settings(StorageSettings, BaseAppSettings):
    """Settings do serviço."""


settings = Settings()
client: AsyncMinIOClient | LocalObjectStorageClient = settings.build_storage_client()
```

```env
STORAGE_BACKEND=local
STORAGE_LOCAL_DIR=/var/lib/app/storage
STORAGE_LOCAL_PUBLIC_URL=https://api.example.com
STORAGE_LOCAL_ROUTE_PREFIX=/api/storage
STORAGE_LOCAL_SIGNING_SECRET=um-segredo-longo-e-aleatorio
STORAGE_LOCAL_MAX_UPLOAD_BYTES=5242880
STORAGE_DEFAULT_BUCKET=media
```

| Variável | Padrão | Para quê |
| --- | --- | --- |
| `STORAGE_BACKEND` | `s3` | `local` liga o disco |
| `STORAGE_LOCAL_DIR` | `var/storage` | raiz do store |
| `STORAGE_LOCAL_PUBLIC_URL` | `http://127.0.0.1:8000` | começo dos links |
| `STORAGE_LOCAL_ROUTE_PREFIX` | `/storage` | caminho das rotas, como o app o vê |
| `STORAGE_LOCAL_SIGNING_SECRET` | placeholder de dev | segredo dos links |
| `STORAGE_LOCAL_MAX_UPLOAD_BYTES` | 25 MiB | teto do `PUT` |

Com `ENV=production` (`EnvironmentSettings`) e `STORAGE_BACKEND=local`, o boot
recusa o segredo de desenvolvimento, `STORAGE_LOCAL_PUBLIC_URL` sem `https` e
`STORAGE_LOCAL_DIR` relativo. As credenciais de S3 deixam de ser cobradas,
porque nada as lê.

!!! tip "Reusar o `JWT_SECRET`"
    A assinatura deriva a chave do segredo **e** do propósito, então usar o
    mesmo valor do `JWT_SECRET` em `STORAGE_LOCAL_SIGNING_SECRET` é seguro:
    um link nunca vale como token, e o contrário também.

## Com o `SubjectObjectStorage`

O cliente local satisfaz o `SubjectStorageClient`, então a exportação e a
exclusão por titular (LGPD) funcionam igual:

```python
import asyncio

from tempest_fastapi_sdk import LocalObjectStorageClient
from tempest_fastapi_sdk.privacy import SubjectObjectStorage


async def main() -> None:
    """Grava dois arquivos de um titular e apaga todos de uma vez."""
    client = LocalObjectStorageClient(
        "var/storage",
        public_url="http://127.0.0.1:8000",
        signing_secret="troque-por-um-segredo-longo",
    )
    storage = SubjectObjectStorage(client, prefix="users")
    await storage.put(42, "avatar.png", b"png", content_type="image/png")
    await storage.put(42, "export.json", b"{}", content_type="application/json")
    print(await storage.names(42))
    print(await storage.delete_all(42))


asyncio.run(main())
```

```text
['avatar.png', 'export.json']
2
```

Objeto ausente levanta `LocalObjectNotFoundError`, um `FileNotFoundError` com
`code = "NoSuchKey"` — o código que o S3 responde. Código que decide pelo
`code` do erro do S3 continua funcionando.

## Antes de ir para produção

!!! warning "Uma máquina só"
    O disco é de **um** servidor. Duas instâncias da API em máquinas diferentes
    não enxergam os arquivos uma da outra. Escalou para mais de uma máquina,
    volte para S3 (`STORAGE_BACKEND=s3`).

!!! warning "API e worker no mesmo volume"
    Processos na mesma máquina compartilham o store montando **o mesmo
    diretório**. Num `docker compose`, isso é um volume nomeado montado no
    mesmo caminho na API e no worker:

    ```yaml
    services:
      api:
        volumes:
          - storage:/var/lib/app/storage
      worker:
        volumes:
          - storage:/var/lib/app/storage

    volumes:
      storage:
    ```

!!! warning "Backup é seu"
    S3 gerenciado replica os dados; um diretório, não. Inclua
    `STORAGE_LOCAL_DIR` (com `objects/` e `meta/`) na rotina de backup do
    servidor.

??? info "Detalhes técnicos"
    * Toda chamada bloqueante roda em `asyncio.to_thread`.
    * Chave vazia, começando com `/`, com segmento `.` ou `..`, com `\` ou NUL,
      ou com segmento terminado em `.meta.json` é recusada com `ValueError`. O
      caminho final é resolvido (seguindo symlink) e precisa ficar dentro da
      raiz.
    * Num disco, as chaves `a` e `a/b` não convivem (`a` não pode ser arquivo
      e pasta ao mesmo tempo); gravar a segunda levanta `OSError` (`FileExistsError` ou `IsADirectoryError`, conforme a ordem). O S3
      aceita as duas.
    * `list_objects(recursive=False)` devolve as "pastas" terminadas em `/`,
      como o S3.
    * `remove_objects` devolve `AccessDenied` para erro de permissão e
      `InternalError` para o resto, num `ObjectDeleteError` por chave.
    * `X-Accel-Redirect` não é suportado pelo cliente local: o `GET` sempre
      passa pela API.

## Recap

* `LocalObjectStorageClient` guarda objetos num diretório e fala a mesma
  língua do `AsyncMinIOClient`.
* Os links "presigned" apontam para a API; `make_local_storage_router` os
  serve, na raiz do app.
* `STORAGE_BACKEND=local` + `build_storage_client()` trocam o backend sem
  mexer no resto do serviço.
* Uma máquina só, volume compartilhado entre API e worker, backup do
  diretório.
