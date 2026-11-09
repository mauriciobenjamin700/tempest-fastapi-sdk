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

Não precisa de extra: só biblioteca padrão e o FastAPI que o SDK já traz. A
cifra em repouso, opcional, pede o `[storage-encryption]`.

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

## Cifra em repouso

Até aqui os arquivos ficam **em claro** no disco. Se o seu serviço guarda foto
de documento, de caderno ou qualquer coisa pessoal, quem tiver acesso ao
disco, a um snapshot do volume ou a um backup dele lê tudo — mesmo que o
banco cifre as colunas de conteúdo.

Os bytes entram e saem por caminhos que são do SDK (o `PUT`/`POST` assinado
que o app chama direto, o `GET` assinado, o `download_response`), então a
cifra mora no cliente: passe `encryption_keys` e todo caminho de escrita
cifra, todo caminho de leitura decifra. O resto do serviço não muda.

### Gerando a chave

Cada chave tem 32 bytes (AES-256). Gere uma em base64:

```bash
python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
```

```text
2yk6fNE3HpXOhHypqJSA3ncp6UWjTI/4jORNYXz2RDo=
```

E instale o extra que traz a cifra (`cryptography`):

```bash
uv add "tempest-fastapi-sdk[storage-encryption]"
```

### O primeiro objeto cifrado

```python
import asyncio
import os

from tempest_fastapi_sdk import LocalObjectStorageClient


async def main() -> None:
    """Grava um objeto cifrado, olha o disco e lê de volta."""
    storage = LocalObjectStorageClient(
        "var/storage",
        public_url="http://127.0.0.1:8000",
        signing_secret="troque-por-um-segredo-longo",
        encryption_keys={"2026-11": os.urandom(32)},
    )
    await storage.put_object(
        "cadernos/foto.jpg",
        b"conteudo do caderno",
        content_type="image/jpeg",
    )
    on_disk = storage.object_path("cadernos/foto.jpg").read_bytes()
    stat = await storage.stat_object("cadernos/foto.jpg")
    stream = await storage.stream_object("cadernos/foto.jpg")
    body = b"".join([chunk async for chunk in stream])
    print(on_disk[:8], b"caderno" in on_disk, len(on_disk))
    print(stat.size, body)


asyncio.run(main())
```

```text
b'TFSDKENC' False 59
19 b'conteudo do caderno'
```

Pedaço por pedaço:

* `encryption_keys` é um dicionário de **id → chave**. O id vai gravado no
  cabeçalho de cada arquivo; a chave, nunca.
* O arquivo no disco começa com o marcador `TFSDKENC` e não contém o texto:
  são 59 bytes para 19 de conteúdo (cabeçalho + 16 bytes de tag por bloco).
* `stat_object` responde o tamanho **em claro** (19), que é o que você compara
  com o teto de upload.
* `stream_object` devolve os bytes originais.

!!! warning "A chave do exemplo some com o processo"
    `os.urandom(32)` no código serve para ver a cifra funcionando. Num
    serviço, a chave vem do ambiente (veja as settings abaixo): sem ela, nada
    do que foi gravado volta a abrir.

### Como funciona

O arquivo é cortado em blocos de 64 KiB (`LOCAL_ENCRYPTION_BLOCK_SIZE`) e cada
bloco é cifrado com AES-256-GCM, do `cryptography`. É a construção STREAM:

* o nonce de cada bloco é um prefixo aleatório do arquivo + o número do
  bloco + uma marca de "último bloco";
* o cabeçalho (versão do formato, id da chave, prefixo) e o
  `<bucket>/<key>` do objeto entram como dado autenticado.

Por isso a escrita e a leitura continuam em stream, sem carregar o arquivo
inteiro, e cada adulteração vira erro:

| O que aconteceu com o arquivo | Resultado |
| --- | --- |
| byte trocado no conteúdo, na tag ou no prefixo do nonce | `StorageIntegrityException`, `reason="authentication"` |
| versão desconhecida ou cabeçalho cortado | `reason="format"` |
| blocos fora de ordem | `reason="authentication"` |
| último bloco removido | `reason="authentication"` |
| cortado no meio de uma tag | `reason="format"` |
| copiado ou renomeado para outra chave ou bucket | `reason="authentication"` |
| cifrado com uma chave que o cliente não tem | `reason="unknown_key"` |
| arquivo em claro, sem `allow_plaintext_read` | `reason="plaintext"` |

`StorageIntegrityException` é um `AppException` com status `500` e
`code="STORAGE_INTEGRITY_ERROR"`. O `details` leva só o `reason` — nunca o
nome do objeto nem um byte dele.

!!! check "Nenhum byte sem autenticação"
    Cada bloco só é entregue depois que a tag dele confere. O primeiro bloco
    é conferido **antes** de `stream_object` devolver o stream, então pelo
    router um arquivo adulterado responde `500 STORAGE_INTEGRITY_ERROR`
    limpo. Se um bloco do meio falhar, a resposta já começou: a conexão é
    cortada depois dos blocos que conferiram (medido com uvicorn e httpx:
    `200`, 2 blocos de 64 KiB recebidos, depois `RemoteProtocolError` por
    faltar o resto do `Content-Length`).

### Pela API, nada muda

O `make_local_storage_router` não muda de forma:

* o `PUT` e o `POST` assinados gravam cifrado; o teto (`max_upload_bytes`,
  `max_bytes`) conta o conteúdo, não o cabeçalho nem as tags;
* o `GET` assinado entrega em claro, com `Content-Length` do conteúdo;
* `Range` funciona: o cliente decifra só os blocos que o intervalo cobre.
  `SubjectObjectStorage.read(..., max_bytes=...)` também para no limite e
  não decifra além dele.

### Pelas settings

```env
STORAGE_BACKEND=local
STORAGE_LOCAL_ENCRYPTION_KEYS='{"2026-11":"2yk6fNE3HpXOhHypqJSA3ncp6UWjTI/4jORNYXz2RDo="}'
```

| Variável | Padrão | Para quê |
| --- | --- | --- |
| `STORAGE_LOCAL_ENCRYPTION_KEYS` | `{}` | JSON de id → chave em base64; a **primeira** grava, todas leem |
| `STORAGE_LOCAL_ALLOW_PLAINTEXT_READ` | `false` | lê arquivo antigo, sem cabeçalho, em claro (só durante a migração) |

`local_storage_kwargs()` decodifica as chaves e repassa `encryption_keys` e
`allow_plaintext_read` ao cliente, e `build_storage_client()` já sai cifrando.
Sem chave, o dicionário é o de antes. Chave que não é base64 de 32 bytes
derruba o boot com o id dela na mensagem, nunca o valor.

!!! info "Produção sem chave: aviso, não recusa"
    Com `ENV=production`, `STORAGE_BACKEND=local` e nenhuma chave, o boot
    **segue** e loga um `WARNING` no logger `tempest_fastapi_sdk.settings`.
    Recusar quebraria, sem aviso, todo serviço que já roda o backend local em
    produção no dia do upgrade. Ligue a cifra e o aviso some.

### Migrando um volume que já tem arquivos

O arquivo gravado antes da chave não tem cabeçalho. Com a chave ligada ele é
recusado (`reason="plaintext"`), a não ser que você permita a leitura em claro
durante a migração:

1. Gere a chave e suba com as duas variáveis:

    ```env
    STORAGE_LOCAL_ENCRYPTION_KEYS='{"2026-11":"<base64>"}'
    STORAGE_LOCAL_ALLOW_PLAINTEXT_READ=true
    ```

    A partir daqui, todo arquivo novo já é gravado cifrado, e o antigo
    continua abrindo.

2. Cifre o que existe, de preferência numa janela sem escrita:

    ```bash
    tempest storage encrypt-local
    ```

    ```text
    docs: 1 object(s) encrypted
    uploads: 1 object(s) encrypted
    ```

    O comando passa por todo bucket de `STORAGE_LOCAL_DIR` (`--bucket` limita
    a um) e é **idempotente**: arquivo já cifrado é pulado, então rodar de
    novo depois de uma interrupção não regrava nada. Content type e metadata
    ficam; o ETag passa a ser o MD5 do arquivo cifrado.

3. Tire `STORAGE_LOCAL_ALLOW_PLAINTEXT_READ` e reinicie.

Em código, o mesmo é `await storage.encrypt_existing()` (por bucket, com
`await storage.list_buckets()`).

!!! warning "Escrita durante a migração"
    Cada arquivo é regravado em `tmp/` e só entra no lugar se não mudou
    enquanto era lido (mesmo inode, tamanho e data). A conferência e a troca
    são dois passos, então uma escrita que caia exatamente entre os dois se
    perde. Rode com o serviço parado ou sem upload em andamento.

### Rodando a chave

A primeira chave do JSON grava; qualquer uma lê, pelo id do cabeçalho:

1. Ponha a chave nova **na frente** e mantenha a antiga:

    ```env
    STORAGE_LOCAL_ENCRYPTION_KEYS='{"2027-05":"<nova>","2026-11":"<antiga>"}'
    ```

    Gravação nova já sai na `2027-05`; o que está na `2026-11` continua
    abrindo.

2. Regrave o que está na chave antiga:

    ```bash
    tempest storage reencrypt-local
    ```

    Idempotente também: o que já está na chave da frente é pulado. Arquivo
    que não decifra (chave fora do anel, arquivo danificado) para o comando
    com o erro e código de saída `1`, sem regravar nada dele. Em código:
    `await storage.reencrypt()`.

3. Tire a chave antiga do JSON.

### O que a cifra protege, e o que não

Protege os arquivos **parados**: o disco, um snapshot do volume, um backup
copiado para outro lugar, um volume montado por engano em outra máquina.

Não protege:

* **o processo comprometido.** A API decifra para servir; quem roda código
  nela lê o que ela lê.
* **a chave no mesmo lugar dos dados.** Chave no `.env` que vai junto do
  volume no mesmo backup é cadeado com a chave pendurada. Guarde-a num
  gerenciador de segredos ou ao menos fora do volume e do backup dele.
* **o que já vazou antes da migração.** Backups e snapshots antigos
  continuam em claro, e o disco pode guardar os blocos liberados do arquivo
  antigo. Rode os backups antigos para fora do ciclo de retenção.
* **o metadado.** Nome da chave (`<bucket>/<key>`), content type, metadata do
  usuário e o tamanho aproximado continuam visíveis no disco.

!!! tip "Uma chave por ambiente"
    Desenvolvimento, homologação e produção com chaves diferentes: um backup
    de produção restaurado por engano em outro ambiente não abre.

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
      como o S3. Prefixo cuja parte de diretório tem segmento vazio, `.` ou
      `..`, ou que resolve para fora do bucket (symlink incluído), devolve
      `[]` — no S3 `../` é só texto que nenhuma chave tem.
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
* `encryption_keys` (`STORAGE_LOCAL_ENCRYPTION_KEYS`) cifra cada arquivo em
  blocos AES-256-GCM: a primeira chave grava, todas leem, e adulteração vira
  `StorageIntegrityException`.
* `tempest storage encrypt-local` migra o volume antigo e
  `tempest storage reencrypt-local` fecha a rotação; os dois são idempotentes.
* A cifra protege disco, snapshot e backup; não protege o processo nem a chave
  guardada junto dos dados.
