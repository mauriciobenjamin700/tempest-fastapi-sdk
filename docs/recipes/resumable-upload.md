---
description: "Use para aceitar upload que retoma de onde parou (protocolo tus) com `make_tus_router`; contém `on_complete`, dono, `Idempotency-Key`, `S3TusStore`, expiração, limites e CORS."
---

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

Quando o último byte chega, o router chama `on_complete(upload, store)` **uma vez por
upload, dentro da requisição do último `PATCH`**, sob o lock do upload, antes do `204`.
É ali que o serviço registra a foto ou move o arquivo:

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
- `await store.delete(upload.id)` dentro do callback é permitido: o router não recria o
  registro, e o `HEAD` seguinte responde `404`. O exemplo acima roda em
  `tests/storage/tus/test_hardening.py`, lido direto desta página.
- Um `PATCH` num upload já completo — inclusive o vazio, com `Upload-Offset` igual ao
  tamanho — responde `409 TUS_UPLOAD_COMPLETED` sem gravar nada e sem chamar o callback
  de novo. O `createResumableUpload` trata todo `409` relendo o offset com `HEAD` antes
  da tentativa seguinte (`RESYNCABLE_STATUSES` em `resumable-upload.ts`, lido no
  código), e com o offset igual ao tamanho o envio termina.

!!! warning "Callback que levanta"
    O `PATCH` responde `500`, e o upload fica completo com `notified = False`. O cliente
    não repete: o `HEAD` seguinte já mostra todos os bytes, e para ele o envio terminou.
    Ache esses uploads com
    `[u for u in await store.list_uploads() if u.completed and not u.notified]` e
    reprocesse — coberto em `test_failing_on_complete_leaves_the_upload_complete_and_unnotified`.

## Dono do upload

O `str()` do que `current_user_id` resolve vira o dono, e a dependência precisa devolver o
**id** do usuário como `str`, `int` ou `UUID`: qualquer outro tipo (o model do usuário, o
dict de claims) levanta `TypeError` — o `str()` de um objeto pode mudar entre requisições
ou coincidir entre usuários. Só o mesmo valor sonda, continua
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
restart.

Sem `current_user_id` (modo anônimo) a chave é **ignorada**: sem dono, todos os
anônimos dividiriam um espaço de chaves, e quem repetisse a chave de outro receberia a URL
dele. O cliente gera a chave com `crypto.randomUUID()` e, onde o navegador não oferece
essa função (fora de contexto seguro, isto é, sem HTTPS), cai num UUID montado com
`Math.random()`, que não é aleatoriedade criptográfica (`src/http/idempotency.ts` do
`tempest-react-sdk`). No modo anônimo, uma criação repetida depois de um `201` perdido
deixa um upload órfão, que o `purge_expired()` apaga. Não precisa do `IdempotencyMiddleware` para esta rota: ele guarda a resposta pela
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

O registro completo é salvo **antes** de os chunks serem apagados. Se a montagem falhar no
meio, o `PATCH` responde `500`, o registro continua no offset anterior e a repetição do
último chunk monta de novo; se só a remoção dos chunks falhar, o upload já está completo e
os chunks que sobraram saem no `store.delete(id)` — os dois casos com falha injetada em
`test_hardening.py`.

`S3TusStore(min_chunk_size=...)` (padrão 64 KiB) recusa com
`400 TUS_CHUNK_TOO_SMALL` um chunk menor que isso, exceto o que fecha o upload: cada chunk
é um objeto, e sem piso um cliente mandando 1 byte por `PATCH` criaria um objeto por byte.
Com 64 KiB, um upload de 1 GiB tem no máximo 16 384 chunks; o cliente manda 5 MiB por
padrão e 256 KiB na receita de campo.

!!! warning "O custo"
    Cada byte é escrito no bucket **duas vezes** (chunk, depois arquivo) e lido uma. E a
    requisição do último `PATCH` espera a montagem, que passa o arquivo por um
    temporário no disco do servidor (`SpooledTemporaryFile`, 8 MiB em memória antes de ir
    para o disco). Para fotos e áudios de alguns MiB isso é invisível; para arquivos de
    gigabytes, o último `PATCH` demora o tempo de reler e reescrever o arquivo inteiro —
    ajuste o timeout do proxy, ou prefira o `LocalTusStore` com o disco compartilhado.
    O disco temporário conta por montagem simultânea: dez uploads de 1 GiB terminando ao
    mesmo tempo ocupam até 10 GiB no diretório temporário do servidor.

Medido contra um servidor S3 real (o gateway S3 do SeaweedFS em container — a imagem
`minio/minio` respondeu *pull access denied* nesta máquina em 2026-11-22): 6 MiB em chunks
de 256 KiB, servidor morto com `SIGKILL` na metade, um processo novo retoma do offset
gravado no bucket, e o objeto `data` final é idêntico byte a byte ao enviado, com só
`data` e `info.json` restando (`tests/storage/tus/test_s3_live.py`, `make test-docker`).

## Expiração e limpeza

Upload incompleto expira `expiration` depois da criação ou do último `PATCH` que gravou
bytes (padrão 24 h) — um `PATCH` vazio não renova o prazo; o prazo sai no `Upload-Expires`. Passado o prazo, `HEAD` e `PATCH` respondem
`410 TUS_UPLOAD_EXPIRED`, e o cliente recomeça do zero. Upload **completo** nunca expira:
os bytes já são do serviço.

Quem apaga o que expirou é você, num agendamento:

```python
from tempest_fastapi_sdk import LocalTusStore

store = LocalTusStore("var/tus")


async def sweep_uploads() -> list[str]:
    return await store.purge_expired()
```

`purge_expired()` devolve os ids apagados (`[]` quando nada expirou). Cada upload é relido
sob o mesmo lock por upload que o router usa, e um upload com requisição em andamento é
pulado. Ligue a uma task periódica do `TaskQueue` ou a um cron.

## Limites por dono

Sem limite, um usuário autenticado cria uploads até encher o disco ou o bucket. Dois
parâmetros do router fecham isso, contando os uploads **inacabados e não expirados** do
mesmo dono:

```python
from tempest_fastapi_sdk import LocalTusStore, make_tus_router

router = make_tus_router(
    LocalTusStore("var/tus"),
    max_uploads_per_owner=20,
    max_bytes_per_owner=2 * 1024 * 1024 * 1024,
)
```

Passado um deles, a criação responde `403 TUS_QUOTA_EXCEEDED` com
`details.limit` (`"uploads"` ou `"bytes"`) e `details.max`. Os dois são `None` por padrão.
Só valem com `current_user_id`: no modo anônimo todos os chamadores dividiriam uma cota, e
um só esgotaria para todos, então nenhuma é aplicada. Upload de tamanho 0, que completa na
criação, nunca conta.

!!! warning "Custo da checagem"
    Cada criação com limite lê todos os registros do store (`list_uploads()`). Mantenha o
    store varrido com `purge_expired()`; com dezenas de milhares de uploads vivos, isso
    pesa em cada `POST`.

## Concorrência e réplicas

O `PATCH` pega o lock do upload **antes** de ler o corpo. Um segundo `PATCH` (ou
`DELETE`) no mesmo upload enquanto o primeiro está em andamento responde
`409 TUS_UPLOAD_LOCKED` na hora, sem ler o corpo — pelo mesmo tratamento de `409`, o cliente
relê com `HEAD` e tenta de novo depois do backoff —, então um upload nunca tem dois corpos no spool. Medido com a
intercalação forçada (a primeira requisição presa dentro da gravação): 1 gravação, a
segunda em `409` sem ter o corpo lido, o arquivo só com os bytes da primeira. Sem o lock,
o mesmo teste mostra as duas respondendo `204` para o mesmo intervalo
(`tests/storage/tus/test_concurrency.py`). O corpo vai para um `SpooledTemporaryFile`
com 1 MiB em memória; o resto, até `max_chunk_size`, vai para o disco temporário.

### Rede móvel: corpo que para no meio

Celular que perde sinal no meio de um chunk deixa a conexão meio-aberta, sem FIN: o
servidor fica esperando o resto do corpo até o TCP desistir, minutos depois — e, como o
lock é pego antes de ler o corpo, toda nova tentativa responderia `409 TUS_UPLOAD_LOCKED`
até lá. Por isso cada pedaço do corpo tem prazo: `body_read_timeout` (padrão 30 s). Passou
o prazo sem chegar nada, o `PATCH` responde `408 TUS_BODY_READ_TIMEOUT`, **o chunk inteiro
é descartado** (o offset gravado não anda; o que chegou antes do prazo não é guardado) e o
lock é liberado. `408` está na lista de status que o `createResumableUpload` repete
(`RETRIABLE_STATUSES` em `errors.ts`), relendo o offset com `HEAD` antes. Uma desconexão
explícita no meio do corpo também não grava nada: vira um log `INFO`, não um `500`. Os dois
casos rodam em `tests/storage/tus/test_resilience.py`, com o app chamado como ASGI cru.

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

Os erros saem no envelope do SDK (`detail`/`code`/`details`). Toda resposta das rotas tus
leva `Tus-Resumable: 1.0.0`, inclusive as recusas — as do router e um `401`/`403` levantado
pela sua dependência `current_user_id`. A exceção é um `500` não tratado: quem responde é o
handler do app, fora da rota, sem o header.

| Status | `code` | Quando |
| --- | --- | --- |
| `400` | `TUS_HEADER_INVALID` | `Upload-Length` ou `Upload-Offset` ausente, não numérico ou com mais de 20 dígitos; `Upload-Metadata` fora de base64/UTF-8. `details.header` nomeia o header |
| `400` | `TUS_CHUNK_TOO_SMALL` | chunk que não fecha o upload abaixo do `min_chunk_size` do store; `details.min_bytes` |
| `403` | `TUS_QUOTA_EXCEEDED` | criação passaria de `max_uploads_per_owner` ou `max_bytes_per_owner` |
| `404` | `TUS_UPLOAD_NOT_FOUND` | id inexistente, malformado ou de outro dono |
| `408` | `TUS_BODY_READ_TIMEOUT` | o corpo do `PATCH` ficou mais de `body_read_timeout` sem chegar; nada é gravado |
| `409` | `TUS_OFFSET_MISMATCH` | `Upload-Offset` diferente do gravado; `details.offset` é o do servidor |
| `409` | `TUS_UPLOAD_COMPLETED` | `PATCH` num upload que já tem todos os bytes; nada é gravado |
| `409` | `TUS_UPLOAD_LOCKED` | outro `PATCH`/`DELETE` do mesmo upload em andamento |
| `410` | `TUS_UPLOAD_EXPIRED` | upload incompleto depois do `Upload-Expires` |
| `412` | `TUS_VERSION_UNSUPPORTED` | `Tus-Resumable` ausente ou diferente de `1.0.0`; a resposta traz `Tus-Version` |
| `413` | `TUS_UPLOAD_TOO_LARGE` | `Upload-Length` acima de `max_size` (com `Tus-Max-Size`), ou corpo do `PATCH` além do `Upload-Length` ou de `max_chunk_size`; nada é gravado |
| `415` | `TUS_CONTENT_TYPE_INVALID` | `PATCH` sem `Content-Type: application/offset+octet-stream` |
| `422` | `TUS_IDEMPOTENCY_KEY_REUSED` | a chave já criou um upload de outro tamanho |


## Recap

- `make_tus_router(store, current_user_id=..., on_complete=...)` atende o
  `createResumableUpload`: `POST`, `HEAD`, `PATCH`, `DELETE`.
- `LocalTusStore` guarda no disco; `S3TusStore` no bucket, com um objeto por chunk e a
  montagem no fim (o dobro de escrita, em troca de aceitar chunk de qualquer tamanho).
- O estado sobrevive a restart; o lock de concorrência é por processo.
- `on_complete` roda uma vez por upload; `PATCH` em upload completo é `409`.
- `Idempotency-Key` por dono (ignorada sem dono), limites por dono, expiração com
  `purge_expired()`, nome de arquivo saneado em `upload.filename`.
