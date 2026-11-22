# Upload resumível (tus)

O técnico de campo tira dez fotos sem sinal; quando a rede volta, elas sobem — e se a
conexão cair no meio da terceira, o envio continua do byte onde parou, não do zero.
No navegador quem faz isso é o `createResumableUpload` do `tempest-react-sdk`, que fala
o protocolo [tus 1.0.0](https://tus.io/protocols/resumable-upload). Esta receita monta o
lado servidor com `make_tus_router`.

!!! info "Sem extra novo"
    O router e o `LocalTusStore` usam só o que a instalação base já traz (FastAPI e a
    stdlib). O `S3TusStore` sobre o `AsyncMinIOClient` pede o extra `[minio]`.

## O exemplo mínimo

```python
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI

from tempest_fastapi_sdk import (
    LocalTusStore,
    TusStore,
    TusUpload,
    make_tus_router,
    register_exception_handlers,
)


async def current_user_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000001")


async def photo_received(upload: TusUpload, store: TusStore, /) -> None:
    print("upload completo:", upload.id, upload.filename, upload.length)


store = LocalTusStore(Path("var/tus"))
app = FastAPI()
register_exception_handlers(app)
app.include_router(
    make_tus_router(
        store,
        current_user_id=current_user_id,
        on_complete=photo_received,
    ),
)
```

Troque o `current_user_id` acima pela dependência de autenticação que o seu serviço já
usa (a que devolve o id do usuário logado). No frontend:

```ts
const done = await createResumableUpload({
  endpoint: "/api/uploads",
  file,
  metadata: { filename: file.name },
  getToken: auth.getToken,
}).start();
```

O `prefix` padrão é `/api/uploads`, o mesmo `endpoint` dos exemplos do
`tempest-react-sdk`.

## O que o cliente manda — e o que o router responde

O subconjunto implementado é exatamente o que o `createResumableUpload` envia. Medido de
duas formas: lendo `src/http/resumable-upload.ts` e os testes dele, e rodando o cliente
real (empacotado do código-fonte com `esbuild`, no Node 24, com o transporte `fetch`)
contra este router: um arquivo de 1 MiB em chunks de 256 KiB, pausado na metade,
retomado por um segundo handle — como depois de um reload —, com o `sha256` do arquivo no
disco igual ao do cliente.

| Passo | Requisição do cliente | Resposta do router |
| --- | --- | --- |
| Criar | `POST /api/uploads` + `Tus-Resumable`, `Upload-Length`, `Idempotency-Key`, `Upload-Metadata` (quando há metadata) | `201` + `Location` relativo (`/api/uploads/<id>`) + `Upload-Expires` |
| Sondar | `HEAD /api/uploads/<id>` + `Tus-Resumable` | `200` + `Upload-Offset`, `Upload-Length`, `Cache-Control: no-store` |
| Escrever | `PATCH /api/uploads/<id>` + `Tus-Resumable`, `Upload-Offset`, `Content-Type: application/offset+octet-stream`, corpo = chunk | `204` + o novo `Upload-Offset` |
| Descartar | `DELETE /api/uploads/<id>` + `Tus-Resumable` (no `abort({ discard: true })`) | `204` |

Mais o que o cliente acrescenta conforme a configuração: `Authorization: Bearer` (com
`getToken`), `X-CSRF-Token` no `POST`, `PATCH` e `DELETE` — nunca no `HEAD` — (com
`csrf`), e os `headers` fixos. O router não lê nenhum deles: autenticação é a sua
dependência `current_user_id`, e CSRF é o `CSRFMiddleware` do app.

Ficaram de fora porque o cliente nunca manda: `OPTIONS` de descoberta,
`Upload-Defer-Length`, corpo no `POST` (*creation-with-upload*), *checksum*,
*concatenation* e `X-HTTP-Method-Override`.

??? note "Por que o `Location` é relativo"
    O cliente resolve o `Location` contra a URL para onde o `POST` foi (RFC 9110). Um
    caminho relativo dispensa saber o host e o esquema que o proxy expõe; atrás de um
    `root_path` (`/svc`), o `Location` sai `/svc/api/uploads/<id>` — coberto em
    `tests/storage/tus/test_router.py`.

## O arquivo pronto: `on_complete`

Quando o último byte chega, o router chama `on_complete(upload, store)` **dentro da
requisição do último `PATCH`**, antes do `204`. É ali que o serviço registra a foto ou
move o arquivo:

```python
import shutil
from pathlib import Path

from tempest_fastapi_sdk import LocalTusStore, TusStore, TusUpload

ARCHIVE = Path("var/photos")


async def archive_photo(upload: TusUpload, store: TusStore, /) -> None:
    assert isinstance(store, LocalTusStore)
    name = upload.filename or "foto.bin"
    shutil.move(store.data_path(upload.id), ARCHIVE / f"{upload.id}-{name}")
    await store.delete(upload.id)
```

- `upload.metadata` traz o `Upload-Metadata` decodificado (base64 → UTF-8), como o
  cliente mandou — `nota-ação.webm` chega com o acento.
- `upload.filename` é o nome **saneado**: só o último componente do caminho, letras,
  dígitos e `-_. ()`. `../../etc/passwd` vira `passwd`. Use este, nunca
  `metadata["filename"]`, para montar caminho.
- `store.stream(upload.id)` lê os bytes em qualquer store; `LocalTusStore.data_path` e
  `S3TusStore.object_key` dão o caminho e a chave, para mover sem copiar pelo processo.

!!! warning "Callback que levanta"
    O `PATCH` responde `500`, e o upload fica completo com `notified = False`. O cliente
    não repete: o `HEAD` seguinte já mostra todos os bytes, e para ele o envio terminou.
    Ache esses uploads com
    `[u for u in await store.list_uploads() if u.completed and not u.notified]` e
    reprocesse — coberto em `test_failing_on_complete_leaves_the_upload_complete_and_unnotified`.

## Dono do upload

O `str()` do que `current_user_id` resolve vira o dono. Só o mesmo valor sonda, continua
ou apaga aquele upload; qualquer outro recebe `404 TUS_UPLOAD_NOT_FOUND` — o mesmo `404`
de um id inexistente, para não confirmar que o id existe.

Sem `current_user_id`, o upload é anônimo e a URL é a única credencial. O id tem 192 bits
aleatórios (`secrets.token_urlsafe(24)`, 32 caracteres), e o store recusa como ausente
qualquer id fora de `[A-Za-z0-9_-]{16,128}`: não há `/`, `.` nem `\`, então o caminho do
`LocalTusStore` nunca sai do diretório.

## `Idempotency-Key` na criação

O cliente gera uma chave por upload e a repete se o `201` se perder. O router guarda
`sha256(dono + chave) → id` no próprio store:

- mesma chave, mesmo dono, mesmo `Upload-Length` → o **mesmo** `Location`;
- mesma chave de outro dono → outro upload (a chave nunca entrega a URL de outra pessoa);
- mesma chave, mesmo dono, outro `Upload-Length` → `422 TUS_IDEMPOTENCY_KEY_REUSED`.

O mapeamento mora no store (arquivo no disco, objeto no bucket), então vale depois de um
restart. Não precisa do `IdempotencyMiddleware` para esta rota: ele guarda a resposta pela
digest das credenciais, e um token renovado entre a tentativa e a repetição já não casa.

## MinIO / S3: `S3TusStore`

```python
from tempest_fastapi_sdk import AsyncMinIOClient, S3TusStore, make_tus_router

client = AsyncMinIOClient(
    endpoint="localhost:9000",
    access_key="minioadmin",
    secret_key="minioadmin",
    default_bucket="uploads",
)
router = make_tus_router(S3TusStore(client, prefix="tus/"))
```

S3 não tem *append*, e uma parte de multipart precisa de pelo menos 5 MiB (menos a
última) — mas quem escolhe o tamanho do chunk é o cliente, e a receita "App de campo
offline" do `tempest-react-sdk` manda 256 KiB. Por isso cada `PATCH` aceito vira um
objeto pequeno, chaveado pelo offset em que começa, e o arquivo é montado uma vez, quando
o último byte chega:

| Chave | Conteúdo |
| --- | --- |
| `tus/<id>/info.json` | o registro (offset, tamanho, dono, expiração) |
| `tus/<id>/chunks/<offset com 20 dígitos>` | um objeto por `PATCH` |
| `tus/<id>/data` | o arquivo pronto, escrito no fim; os chunks são apagados |
| `tus/_idempotency/<digest>` | o id que uma `Idempotency-Key` criou |

Tudo mora no bucket: outro processo, ou outra réplica, vê os mesmos uploads.

!!! warning "O custo"
    Cada byte é escrito no bucket **duas vezes** (chunk, depois arquivo) e lido uma. E a
    requisição do último `PATCH` espera a montagem, que passa o arquivo por um
    temporário no disco do servidor (`SpooledTemporaryFile`, 8 MiB em memória antes de ir
    para o disco). Para fotos e áudios de alguns MiB isso é invisível; para arquivos de
    gigabytes, o último `PATCH` demora o tempo de reler e reescrever o arquivo inteiro —
    ajuste o timeout do proxy, ou prefira o `LocalTusStore` com o disco compartilhado.

Medido contra um servidor S3 real (o gateway S3 do SeaweedFS em container — a imagem
`minio/minio` respondeu *pull access denied* nesta máquina em 2026-11-22): 6 MiB em chunks
de 256 KiB, servidor morto com `SIGKILL` na metade, um processo novo retoma do offset
gravado no bucket, e o objeto `data` final é idêntico byte a byte ao enviado, com só
`data` e `info.json` restando (`tests/storage/tus/test_s3_live.py`, `make test-docker`).

## Expiração e limpeza

Upload incompleto expira `expiration` depois da criação ou do último `PATCH` aceito (padrão
24 h); o prazo sai no `Upload-Expires`. Passado o prazo, `HEAD` e `PATCH` respondem
`410 TUS_UPLOAD_EXPIRED`, e o cliente recomeça do zero. Upload **completo** nunca expira:
os bytes já são do serviço.

Quem apaga o que expirou é você, num agendamento:

```python
from tempest_fastapi_sdk import LocalTusStore

store = LocalTusStore("var/tus")


async def sweep_uploads() -> list[str]:
    return await store.purge_expired()
```

`purge_expired()` devolve os ids apagados (`[]` quando nada expirou). Ligue a uma task
periódica do `TaskQueue` ou a um cron.

## Concorrência e réplicas

Dois `PATCH` no mesmo offset do mesmo upload: um grava, o outro responde
`409 TUS_OFFSET_MISMATCH` com `details.offset` — o cliente relê com `HEAD` e segue. O
offset é conferido de novo depois de ler o corpo, sob um lock por upload. Medido com a
intercalação forçada (a primeira requisição presa dentro da gravação até a segunda ler o
corpo inteiro): 1 gravação, a segunda em `409`, o arquivo só com os bytes da vencedora.
Sem o lock, o mesmo teste mostra as duas respondendo `204` para o mesmo intervalo
(`tests/storage/tus/test_concurrency.py`).

!!! warning "O lock é do processo"
    Duas réplicas atrás de um balanceador ainda podem disputar o mesmo upload. Com mais de
    uma réplica, roteie as requisições de um upload para a mesma réplica (sessão fixa no
    caminho `/api/uploads/<id>`).

Um restart, por outro lado, não perde nada: o registro é gravado só depois dos bytes, e é
a autoridade do offset. Medido em `tests/storage/tus/test_restart.py`: uvicorn em
subprocesso, morto com `SIGKILL` depois de 192 KiB de 300 KiB; o processo novo, montado
só com o mesmo diretório, responde o `HEAD` com `Upload-Offset: 196608` e aceita o resto,
e o arquivo final é idêntico ao enviado.

## CORS

Frontend em outra origem só lê `Location` e `Upload-Offset` se o backend os expuser — sem
isso, `getResponseHeader("Location")` devolve `null` e o cliente não acha o upload que
criou:

```python
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from tempest_fastapi_sdk import TUS_CORS_ALLOW_HEADERS, TUS_CORS_EXPOSE_HEADERS

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.exemplo.com"],
    allow_methods=["POST", "HEAD", "PATCH", "DELETE"],
    allow_headers=list(TUS_CORS_ALLOW_HEADERS),
    expose_headers=list(TUS_CORS_EXPOSE_HEADERS),
)
```

## Erros

Todo erro sai no envelope do SDK (`detail`/`code`/`details`) e leva `Tus-Resumable: 1.0.0`.

| Status | `code` | Quando |
| --- | --- | --- |
| `400` | `TUS_HEADER_INVALID` | `Upload-Length` ou `Upload-Offset` ausente ou não numérico; `Upload-Metadata` fora de base64/UTF-8. `details.header` nomeia o header |
| `404` | `TUS_UPLOAD_NOT_FOUND` | id inexistente, malformado ou de outro dono |
| `409` | `TUS_OFFSET_MISMATCH` | `Upload-Offset` diferente do gravado; `details.offset` é o do servidor |
| `410` | `TUS_UPLOAD_EXPIRED` | upload incompleto depois do `Upload-Expires` |
| `412` | `TUS_VERSION_UNSUPPORTED` | `Tus-Resumable` ausente ou diferente de `1.0.0`; a resposta traz `Tus-Version` |
| `413` | `TUS_UPLOAD_TOO_LARGE` | `Upload-Length` acima de `max_size` (com `Tus-Max-Size`), ou corpo do `PATCH` além do `Upload-Length` ou de `max_chunk_size`; nada é gravado |
| `415` | `TUS_CONTENT_TYPE_INVALID` | `PATCH` sem `Content-Type: application/offset+octet-stream` |
| `422` | `TUS_IDEMPOTENCY_KEY_REUSED` | a chave já criou um upload de outro tamanho |

Um `401` da sua dependência de autenticação sai como ela o levanta, sem `Tus-Resumable` —
o cliente lê o status, não o header.

## Recap

- `make_tus_router(store, current_user_id=..., on_complete=...)` atende o
  `createResumableUpload`: `POST`, `HEAD`, `PATCH`, `DELETE`.
- `LocalTusStore` guarda no disco; `S3TusStore` no bucket, com um objeto por chunk e a
  montagem no fim (o dobro de escrita, em troca de aceitar chunk de qualquer tamanho).
- O estado sobrevive a restart; o lock de concorrência é por processo.
- `Idempotency-Key` por dono, expiração com `purge_expired()`, nome de arquivo saneado em
  `upload.filename`.
