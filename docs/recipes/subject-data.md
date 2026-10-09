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
