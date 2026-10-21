# Exportação e exclusão de titular (LGPD)

A LGPD (art. 18) dá ao titular o direito de pedir **cópia** de tudo que você
guarda sobre ele e a **eliminação** desses dados. Os dois pedidos têm a mesma
pergunta por baixo: *quais linhas e quais arquivos são dessa pessoa?*

Responder isso na mão — uma lista de tabelas num service, um laço de
`remove_object` num script — funciona no dia em que é escrito. No mês
seguinte alguém cria a tabela `favorites` com `user_id`, esquece a lista, e a
exportação passa a omitir dado e a exclusão passa a falhar (ou a deixar linha
para trás). Ninguém percebe até o pedido real.

O módulo `tempest_fastapi_sdk.privacy` tira a lista da sua mão:

- **`SubjectGraph`** lê a `MetaData` do SQLAlchemy e deriva o conjunto de
  tabelas que um `DELETE` da linha raiz do titular alcança por `ON DELETE
  CASCADE`. Esse conjunto é o que ele exporta, e qualquer chave estrangeira que
  aponte para ele **sem** cascata vira uma violação que um teste pega.
- **`SubjectObjectStorage`** guarda todo arquivo do titular sob um prefixo
  próprio no MinIO/S3 e apaga o prefixo inteiro em lote.

## Instalação

`SubjectGraph` só precisa do SQLAlchemy, que já vem no pacote base.
`SubjectObjectStorage` usa o `AsyncMinIOClient`, que precisa do extra
`[minio]`:

```bash
uv add "tempest-fastapi-sdk[minio]"
```

Importar `tempest_fastapi_sdk.privacy` não exige o extra; só construir o
cliente exige.

## O banco desenhado para apagar

A exclusão mais barata é a que o banco faz sozinho: toda tabela com dado do
titular tem uma chave estrangeira com `ondelete="CASCADE"` em direção à linha
raiz (direta ou por outra tabela), então um único `DELETE` na raiz apaga o
resto. É esse desenho que o `SubjectGraph` lê de volta.

```python
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class UserModel(BaseModel):
    """Titular: a linha raiz de tudo que é dele."""

    email: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(120))


class AddressModel(BaseModel):
    """Endereço do titular."""

    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    street: Mapped[str] = mapped_column(String(120))


class OrderModel(BaseModel):
    """Pedido do titular."""

    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class OrderItemModel(BaseModel):
    """Item de pedido: é do titular por meio do pedido."""

    order_id: Mapped[UUID] = mapped_column(ForeignKey("order.id", ondelete="CASCADE"))
    sku: Mapped[str] = mapped_column(String(20))


class InvoiceModel(BaseModel):
    """Nota fiscal: sobrevive à exclusão, sem o vínculo com o titular."""

    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    number: Mapped[str] = mapped_column(String(20))
```

`OrderItemModel` não aponta para `user`: ele é do titular **por meio** do
pedido. O grafo segue a cascata em qualquer profundidade.

`InvoiceModel` é o caso que a lei pede ao contrário: a nota fiscal precisa
ficar guardada mesmo depois da exclusão. `SET NULL` mantém a linha e corta o
vínculo.

## O guard: quebra o CI, não o pedido

Crie o grafo apontando a raiz e rode o guard pronto num teste:

```python
from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.privacy import SubjectGraph
from tempest_fastapi_sdk.testing import assert_subject_graph_valid


def test_subject_graph_supports_erasure() -> None:
    """Toda FK para dado do titular cascata, ou está justificada."""
    assert_subject_graph_valid(SubjectGraph(BaseModel.metadata, root="user"))
```

Com os models acima, ele falha — e diz onde:

```text
AssertionError: subject graph rooted at 'user' has 1 erasure violation(s):
  - invoice.user_id -> user: ON DELETE SET NULL (expected CASCADE, or SET NULL listed in retained)
```

O `SET NULL` da nota fiscal é deliberado, então ele entra em `retained`, com
o motivo ao lado — quem ler o código daqui a um ano sabe por que essa linha
sobrevive:

```python
from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.privacy import SubjectGraph
from tempest_fastapi_sdk.testing import assert_subject_graph_valid

graph = SubjectGraph(
    BaseModel.metadata,
    root="user",
    retained={"invoice.user_id": "guarda fiscal de 5 anos"},
)


def test_subject_graph_supports_erasure() -> None:
    """Toda FK para dado do titular cascata, ou está justificada."""
    assert_subject_graph_valid(graph)
```

O que vira violação (`graph.violations()` devolve uma linha por problema,
ordenada):

| Situação | Por que quebra a exclusão |
| --- | --- |
| FK para tabela do titular sem `ondelete` (`NO ACTION`) ou com `RESTRICT` | o `DELETE` da raiz falha |
| FK com `SET NULL` fora de `retained` | a linha sobrevive sem ninguém ter decidido isso |
| entrada de `retained` cuja FK não é `SET NULL` | a justificativa não descreve o schema |
| entrada de `retained` em coluna `NOT NULL` | o `SET NULL` falha na hora do `DELETE` |
| entrada de `retained` que não casa com FK nenhuma | allowlist velha, de tabela que já saiu |

!!! tip "Tabela nova entra sozinha"
    Uma tabela criada depois com FK em cascata para qualquer tabela do
    titular entra em `graph.tables()` — e, portanto, na exportação — sem
    nenhuma configuração. A que for criada **sem** cascata quebra o guard no
    PR que a criou.

## Exportar e excluir

O exemplo inteiro, rodando contra SQLite em memória:

```python
import asyncio
import json
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ForeignKey, Numeric, String, delete
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel, enable_sqlite_foreign_keys
from tempest_fastapi_sdk.privacy import SubjectGraph


class UserModel(BaseModel):
    """Titular: a linha raiz de tudo que é dele."""

    email: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(120))


class AddressModel(BaseModel):
    """Endereço do titular."""

    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    street: Mapped[str] = mapped_column(String(120))


class OrderModel(BaseModel):
    """Pedido do titular."""

    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class OrderItemModel(BaseModel):
    """Item de pedido: é do titular por meio do pedido."""

    order_id: Mapped[UUID] = mapped_column(ForeignKey("order.id", ondelete="CASCADE"))
    sku: Mapped[str] = mapped_column(String(20))


class InvoiceModel(BaseModel):
    """Nota fiscal: sobrevive à exclusão, sem o vínculo com o titular."""

    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    number: Mapped[str] = mapped_column(String(20))


graph = SubjectGraph(
    BaseModel.metadata,
    root="user",
    retained={"invoice.user_id": "guarda fiscal de 5 anos"},
)


async def main() -> None:
    """Cria o banco, exporta um titular, exclui e confere a exclusão."""
    print(graph.tables())

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = UserModel(email="ana@example.com", password_hash="$2b$12$...")
        session.add(user)
        await session.flush()
        order = OrderModel(user_id=user.id, total=Decimal("19.90"))
        session.add_all([order, AddressModel(user_id=user.id, street="Rua A, 10")])
        await session.flush()
        session.add_all(
            [
                OrderItemModel(order_id=order.id, sku="CAFE-250"),
                InvoiceModel(user_id=user.id, number="NF-0001"),
            ]
        )
        await session.commit()

        payload = await graph.export(session, user.id)
        print(json.dumps(payload["user"], indent=2))
        print({table: len(rows) for table, rows in payload.items()})

        await session.execute(delete(UserModel).where(UserModel.id == user.id))
        await session.commit()
        print(await graph.count(session, user.id))

    await engine.dispose()


asyncio.run(main())
```

Saída (os UUIDs e horários mudam a cada execução):

```text
['user', 'address', 'order', 'order_item']
[
  {
    "email": "ana@example.com",
    "id": "c5bc0b35-4c90-4930-bb36-6b9ee166e33f",
    "is_active": true,
    "created_at": "2026-10-09T16:37:43.562484+00:00",
    "updated_at": "2026-10-09T16:37:43.562487+00:00"
  }
]
{'user': 1, 'address': 1, 'order': 1, 'order_item': 1}
{'user': 0, 'address': 0, 'order': 0, 'order_item': 0}
```

Pedaço por pedaço:

- **`graph.tables()`** — a raiz primeiro, depois cada tabela na ordem em que a
  cascata a alcança. `invoice` fica de fora: o `SET NULL` não apaga a linha.
- **`await graph.export(session, user_id)`** — um `dict` com **toda** tabela do
  grafo, cada uma com a lista das linhas do titular (lista vazia quando não há
  nenhuma). Os valores já saem prontos para `json.dumps`: data e hora em ISO
  8601, `UUID` e `Decimal` como texto, `bytes` em base64, enum pelo valor.
- **`password_hash` não aparece.** Coluna cujo nome contém `hash`, `secret` ou
  `password` fica fora da exportação (`DEFAULT_SECRET_MARKERS`). Para marcar
  uma coluna cujo nome não diz nada, use
  `mapped_column(..., info={"secret": True})` ou
  `SubjectGraph(..., secret_columns={"user": ["cpf_token"]})`.
- **O `DELETE` é seu.** Excluir é apagar a linha raiz; quem apaga o resto é o
  banco, pelas cascatas que o guard garantiu.
- **`await graph.count(session, user_id)`** — quantas linhas do titular
  sobram por tabela. Depois da exclusão, tudo `0`: é a prova que você guarda
  no registro do atendimento.

!!! warning "SQLite só cascata com `PRAGMA foreign_keys=ON`"
    Sem `enable_sqlite_foreign_keys(engine)`, o SQLite ignora o
    `ondelete="CASCADE"`: o `DELETE` da raiz passa e as linhas filhas
    ficam. O `count` depois da exclusão é o que denuncia isso. Os engines
    do `AsyncDatabaseManager` e do `tempest_fastapi_sdk.testing` já ligam o
    pragma.

### Coluna binária fica fora

Por padrão, coluna `LargeBinary` (e subclasses) não entra na exportação: o
`payload` de uma tabela de job, um blob de modelo — nada disso é dado que o
titular lê, e em base64 vira só volume no arquivo. `exclude_binary=False`
volta a exportá-las, em base64.

### Dado do titular que não chega por chave estrangeira

O convite enviado **ao** titular guarda o e-mail dele em `invites.to_email`,
mas a chave do convite aponta para quem mandou. O `DELETE` da raiz não
alcança essa linha, e a exportação não a traz. `value_matches` declara a
coluna que identifica o titular por valor, e `export`, `count`, `condition` e
`erase` recebem o valor em `values=`:

```python
import asyncio

from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from tempest_fastapi_sdk.privacy import SubjectGraph
from tempest_fastapi_sdk.testing import create_test_engine

metadata = MetaData()
users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("email", String(120)),
)
invites = Table(
    "invites",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("sender_id", ForeignKey("users.id", ondelete="CASCADE")),
    Column("to_email", String(120)),
)

graph = SubjectGraph(
    metadata, root="users", value_matches={"invites.to_email": "email"}
)


async def main() -> None:
    """Bia invites Ana; Ana's export and erasure reach that invite."""
    engine = create_test_engine()
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        await conn.execute(
            insert(users),
            [{"id": 1, "email": "ana@x.io"}, {"id": 2, "email": "bia@x.io"}],
        )
        await conn.execute(
            insert(invites), [{"id": 1, "sender_id": 2, "to_email": "ana@x.io"}]
        )
    ana = {"email": "ana@x.io"}
    async with async_sessionmaker(engine)() as session:
        print(graph.violations())
        print((await graph.export(session, 1, values=ana))["invites"])
        print(await graph.count(session, 1))
        print(await graph.erase(session, 1, values=ana))
        await session.commit()
    await engine.dispose()


asyncio.run(main())
```

```text
[]
[{'id': 1, 'sender_id': 2, 'to_email': 'ana@x.io'}]
{'users': 1, 'invites': 0}
{'users': 0, 'invites': 0}
```

A terceira linha é o motivo de passar `values` também ao `count`: sem ele, a
prova diz `0` convites com o convite ainda no banco. `erase` apaga as linhas
casadas por valor, depois a raiz (o banco cascateia o resto), e devolve o
`count` — sem `commit`, para você reverter se a prova não vier limpa. Entrada
de `value_matches` fora do fecho ou com coluna inexistente aparece em
`violations()`.

## Os arquivos do titular

Linha apagada com o arquivo ainda no bucket não é exclusão. A forma barata de
achar "todos os arquivos da Ana" é a **chave** dizer isso:
`SubjectObjectStorage` grava tudo em `<prefix>/<subject_id>/<nome>`.

```python
import asyncio
from datetime import timedelta

from tempest_fastapi_sdk import AsyncMinIOClient
from tempest_fastapi_sdk.privacy import SubjectObjectStorage

client = AsyncMinIOClient(
    endpoint="localhost:9000",
    access_key="minioadmin",
    secret_key="minioadmin",
    default_bucket="uploads",
)
storage = SubjectObjectStorage(client, prefix="users")


async def main() -> None:
    """Grava, lista, assina e apaga os arquivos de um titular."""
    await client.ensure_bucket()
    key = await storage.put(42, "docs/rg.pdf", b"%PDF-1.7", content_type="application/pdf")
    print(key)
    print(await storage.names(42))
    print(await storage.presign(42, "docs/rg.pdf", expires=timedelta(minutes=10)))
    print(await storage.delete_all(42))


asyncio.run(main())
```

Saída contra um MinIO local (a assinatura da URL muda a cada execução):

```text
users/42/docs/rg.pdf
['docs/rg.pdf']
http://localhost:9000/uploads/users/42/docs/rg.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256&...&X-Amz-Expires=600&...
1
```

- **`put` devolve a chave inteira** (`users/42/docs/rg.pdf`). O nome pode ter
  subpasta; não pode começar com `/` nem ter segmento vazio, `.` ou `..` —
  nada que saia do prefixo do titular. O id do titular não pode ter `/`.
- **O prefixo termina em `/`.** O titular `7` nunca casa com os arquivos do
  `77`.
- **`delete_all` lista o prefixo e apaga em lote**, com o
  `AsyncMinIOClient.remove_objects` (novo nesta versão), que manda um
  `DeleteObjects` por 1000 chaves em vez de um `DELETE` por arquivo. Devolve
  quantos objetos apagou; rodar de novo devolve `0`.
- Se o storage recusar alguma chave, `delete_all` levanta
  `SubjectErasureError` com a lista (`errors`: chave, código e mensagem do
  S3). As outras chaves já foram apagadas; rodar de novo tenta só o que
  sobrou.

!!! info "Medido contra MinIO real"
    `tests/privacy/test_storage_live.py` (`make test-docker`) grava 1203
    objetos do titular `7`, um do `77` e um do `8`, chama `delete_all(7)` e
    confere: 1203 apagados em **duas** requisições `DeleteObjects` (1000 +
    203), zero sobrando no prefixo do `7`, os do `77` e do `8` intactos.

### Testar sem MinIO

`FakeObjectStorageClient` (em `tempest_fastapi_sdk.testing.fakes`) entra no
lugar do `AsyncMinIOClient` — o `SubjectObjectStorage` aceita qualquer
`SubjectStorageClient`, o protocolo que os dois cumprem — e guarda tudo num
dict. `fail_delete(chave)` faz o storage recusar a exclusão, que é o ramo do
`SubjectErasureError` que um MinIO de verdade não entrega quando você quer:

```python
import asyncio

from tempest_fastapi_sdk.privacy import SubjectErasureError, SubjectObjectStorage
from tempest_fastapi_sdk.testing.fakes import FakeObjectStorageClient


async def main() -> None:
    """Export, erase and a refused key, with no MinIO."""
    client = FakeObjectStorageClient()
    storage = SubjectObjectStorage(client, prefix="users")

    await storage.put(42, "avatar.png", b"png")
    await storage.put(7, "doc.pdf", b"pdf")
    print(await storage.names(42))
    print(await storage.delete_all(42), sorted(client.objects))

    await storage.put(42, "rg.pdf", b"rg")
    client.fail_delete("users/42/rg.pdf")
    try:
        await storage.delete_all(42)
    except SubjectErasureError as error:
        print([e.key for e in error.errors])


asyncio.run(main())
```

```text
['avatar.png']
1 ['users/7/doc.pdf']
['users/42/rg.pdf']
```

`client.objects` mostra o que sobrou no bucket padrão e `client.deleted` o que
saiu, em ordem.

### Upload direto sob o prefixo do titular

Foto de evidência, documento, avatar: o app sobe o arquivo **direto para o
S3**, sem os bytes passarem pela API. O servidor assina a URL, o app faz o
`PUT`, e depois o servidor confere o que chegou antes de aceitar. As três
peças moram no `SubjectObjectStorage`, sob o mesmo prefixo do titular — então
o arquivo enviado também sai no `names` e no `delete_all`:

- **`presign_put(subject_id, name, expires=...)`** devolve a URL de `PUT` de
  `key(subject_id, name)`. Default de 15 minutos, o mesmo do
  `AsyncMinIOClient.presigned_put_url`.
- **`stat(subject_id, name)`** devolve o `ObjectStat` (tamanho, content type,
  ETag, metadata) sem baixar o objeto.
- **`read(subject_id, name, max_bytes=...)`** lê o objeto. Com `max_bytes`,
  pede ao storage só esse tanto (leitura com `Range`) e para de ler ali
  mesmo se o storage mandar mais.

```python
import asyncio
from uuid import uuid4

from tempest_fastapi_sdk.privacy import SubjectObjectStorage
from tempest_fastapi_sdk.testing.fakes import FakeObjectStorageClient

MAX_PHOTO_BYTES: int = 5 * 1024 * 1024
JPEG_MAGIC: bytes = b"\xff\xd8\xff"

client = FakeObjectStorageClient()
storage = SubjectObjectStorage(client, prefix="users")


async def start_upload(user_id: int) -> tuple[str, str]:
    """Assina o PUT de um nome que o servidor escolheu."""
    name = f"uploads/{uuid4().hex}.jpg"
    return name, await storage.presign_put(user_id, name)


async def accept_upload(user_id: int, name: str) -> str | None:
    """Confere o que o app subiu e guarda sob um nome definitivo."""
    stat = await storage.stat(user_id, name)
    if stat.size > MAX_PHOTO_BYTES:
        return None
    data = await storage.read(user_id, name, max_bytes=MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES or not data.startswith(JPEG_MAGIC):
        return None
    key = await storage.put(
        user_id, "photos/latest.jpg", data, content_type="image/jpeg"
    )
    await storage.delete(user_id, name)
    return key


async def main() -> None:
    """Upload direto, conferência e aceite, sem MinIO."""
    name, url = await start_upload(42)
    print(storage.key(42, name) in url, "method=PUT" in url)
    await client.put_object(
        storage.key(42, name), JPEG_MAGIC + b"photo", content_type="image/jpeg"
    )
    print(await accept_upload(42, name))
    print(await storage.names(42))

    name, _ = await start_upload(42)
    await client.put_object(storage.key(42, name), b"x" * (MAX_PHOTO_BYTES + 1))
    print(await accept_upload(42, name))


asyncio.run(main())
```

```text
True True
users/42/photos/latest.jpg
['photos/latest.jpg']
None
```

- **O servidor escolhe o nome** (`uploads/<uuid>.jpg`). O app nunca manda
  chave nem prefixo: a URL só alcança o prefixo do titular que o servidor
  assinou.
- **Aqui o `client.put_object` faz o papel do app.** Em produção é um `PUT`
  HTTP na URL, com o corpo do arquivo.
- **`stat` primeiro, `read` limitado depois.** O `stat` recusa o grande sem
  baixar nada. Mas entre o `stat` e o `read` o objeto pode ser trocado por
  outro (a URL ainda vale), então o `read` pede `MAX_PHOTO_BYTES + 1`:
  `len(data) > MAX_PHOTO_BYTES` quer dizer "grande demais", sem nunca trazer
  o resto para a memória.
- **A URL não assina tamanho nem content type.** O app manda o que quiser;
  quem decide se aceita é a conferência (aqui, o tamanho e o começo de um
  JPEG). Por isso os bytes conferidos vão para um nome definitivo com `put`,
  e o upload provisório sai com `delete`.
- **Objeto que não existe** (o app pediu a URL e não subiu) faz o `stat` e o
  `read` levantarem o erro do cliente, sem tradução: `S3Error` (código
  `NoSuchKey`) no `AsyncMinIOClient`, `FileNotFoundError` no
  `FakeObjectStorageClient`.
- **Storage desligado** (`STORAGE_ENABLED=false`): as três respondem
  `IntegrationDisabledException` (`503`), como `put` e `delete_all`.

!!! warning "Mudança no `SubjectStorageClient`"
    O protocolo ganhou `presigned_put_url`, `stat_object` e `stream_object`.
    `AsyncMinIOClient` e `FakeObjectStorageClient` já cumprem; um cliente seu
    que implemente o protocolo precisa ganhar os três para continuar passando
    no type-checker.


## Juntando os dois num service

Apague os arquivos **antes** da linha raiz. Se o storage falhar, a linha do
titular ainda existe e o mesmo pedido pode ser repetido; o prefixo depende só
do id, então a repetição acha exatamente o que sobrou.

```python
from typing import Any
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.privacy import SubjectGraph, SubjectObjectStorage


class SubjectDataService:
    """Atende exportação e exclusão de titular."""

    def __init__(
        self,
        session: AsyncSession,
        graph: SubjectGraph,
        storage: SubjectObjectStorage,
    ) -> None:
        """Guarda as dependências.

        Args:
            session (AsyncSession): Sessão do request.
            graph (SubjectGraph): Grafo do titular.
            storage (SubjectObjectStorage): Arquivos do titular.
        """
        self.session: AsyncSession = session
        self.graph: SubjectGraph = graph
        self.storage: SubjectObjectStorage = storage

    async def export(self, user_id: UUID) -> dict[str, Any]:
        """Monta a cópia dos dados do titular.

        Args:
            user_id (UUID): Id do titular.

        Returns:
            dict[str, Any]: Linhas por tabela e a lista de arquivos.
        """
        return {
            "tables": await self.graph.export(self.session, user_id),
            "files": await self.storage.names(user_id),
        }

    async def erase(self, user_id: UUID) -> dict[str, int]:
        """Exclui arquivos e linhas do titular.

        Args:
            user_id (UUID): Id do titular.

        Returns:
            dict[str, int]: Linhas restantes por tabela; tudo ``0``.
        """
        await self.storage.delete_all(user_id)
        root = self.graph.root
        await self.session.execute(
            delete(root).where(self.graph.condition(root, user_id))
        )
        await self.session.commit()
        return await self.graph.count(self.session, user_id)


graph = SubjectGraph(
    BaseModel.metadata,
    root="user",
    retained={"invoice.user_id": "guarda fiscal de 5 anos"},
)
```

`graph.condition(tabela, user_id)` é a cláusula `WHERE` que seleciona as
linhas do titular naquela tabela — na raiz, a chave primária; nas outras, um
`IN (SELECT ...)` aninhado pelo caminho da cascata. Serve em `select`,
`update` e `delete`.

## O que fica de fora

- **Vínculo sem chave estrangeira.** Uma coluna que guarda o id do titular
  sem FK (o `actor` e o `entity_id` texto do [audit trail](audit-trail.md),
  um id de usuário dentro de JSON) não entra no grafo, nem na exportação, nem
  na cascata. Trate essas à parte.
- **Linha alcançada só por ciclo.** Numa tabela que referencia a si mesma
  (resposta de comentário), a linha de **outro** titular que só pertence ao
  grafo por apontar para um comentário do titular não é exportada como dele
  — mas a cascata a apaga junto. Decida se isso é o que você quer.
- **Raiz com chave primária composta** é recusada com `ValueError`.
- **Busca, cache e backup.** Índice de busca, chave no Redis e backup do
  banco ficam fora do alcance da cascata.

## Recap

- `SubjectGraph(metadata, root=...)` deriva do schema o conjunto de tabelas
  que o `DELETE` da raiz alcança por cascata; tabela nova com cascata entra
  sozinha.
- `assert_subject_graph_valid(graph)` num teste derruba o CI quando uma FK
  para dado do titular não cascata; `SET NULL` deliberado entra em
  `retained` com o motivo.
- `await graph.export(session, id)` devolve as linhas do titular por tabela,
  prontas para JSON e sem colunas secretas.
- Excluir é apagar a raiz; `await graph.count(session, id)` prova que nada
  sobrou.
- `SubjectObjectStorage` guarda os arquivos sob `<prefix>/<id>/` e
  `delete_all` apaga o prefixo em lote, sem tocar outro titular.
- Upload direto: `presign_put` assina o `PUT` sob o prefixo do titular,
  `stat` e `read(max_bytes=limite + 1)` conferem o que chegou sem trazer
  arquivo grande inteiro para a memória.
