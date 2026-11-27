# 1. O modelo base

Use para declarar tabelas com `BaseModel`: nomes de tabela centralizados, convenção de nomes de constraints e os helpers que vêm de graça.

Parte da receita [Banco de dados](../database.md).

Todo modelo da sua service herda de `BaseModel`. Você ganha quatro
colunas sem escrever nenhuma:

```python
# src/db/models/user.py
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class UserModel(BaseModel):
    """Users table."""

    name: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str] = mapped_column()
```

Isso já cria a tabela `user` com **sete** colunas: as três suas
(`name`, `email`, `password_hash`) mais as quatro herdadas:

| Coluna | Tipo | Padrão | Papel |
| --- | --- | --- | --- |
| `id` | `UUID` (v4) | `uuid4()` | Chave primária, portável entre Postgres/SQLite/MySQL/MSSQL. |
| `is_active` | `bool` | `True` | Flag de soft-delete rápido. |
| `created_at` | `datetime` (tz-aware) | `utcnow()` no flush | Carimbo de criação. |
| `updated_at` | `datetime` (tz-aware) | `utcnow()` no `onupdate` | Carimbo da última escrita. |

!!! info "Por que o nome da tabela é `user` e não `UserModel`?"
    `BaseModel` deriva `__tablename__` da classe automaticamente: tira o
    sufixo `Model` e converte para `snake_case`. `UserModel` → `user`,
    `OrderItemModel` → `order_item`. Você sempre pode fixar
    `__tablename__ = "users"` explicitamente — a declaração explícita
    vence o automático.

!!! tip "Fixar o nome não é só gosto"
    `USER` é palavra reservada no SQL padrão. O SQLAlchemy sempre escreve
    o identificador entre aspas, então a aplicação funciona — mas um
    `SELECT * FROM user` digitado à mão no `psql` devolve o **usuário do
    banco**, não a sua tabela, e sem erro nenhum. O plural (`users`)
    evita a pegadinha e é a convenção que o próprio SDK assume nas
    tabelas de token (`user_tokens`, `user_refresh_tokens`).

## Centralizando os nomes das tabelas

Um nome de tabela quase nunca aparece só uma vez. Ele está no
`__tablename__` **e** volta como string em toda `ForeignKey` que aponta
para ela:

```python hl_lines="9 12"
# src/db/models/user_token.py
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class UserTokenModel(BaseModel):
    __tablename__ = "user_tokens"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
    )
```

Renomear `users` passa a depender de você lembrar de cada lugar em que a
string aparece. E esquecer uma FK não estoura na hora: o SQLAlchemy só
resolve o alvo quando monta os mapeamentos, então o erro chega na
**subida da aplicação** — ou, pior, numa migração que aponta para uma
tabela que não existe mais.

A saída é um módulo que só guarda nomes:

```python
# src/db/configs/names.py
"""Nomes das tabelas do projeto. Fonte única de verdade."""

USER_TABLE_NAME = "users"
USER_TOKEN_TABLE_NAME = "user_tokens"
USER_REFRESH_TOKEN_TABLE_NAME = "user_refresh_tokens"
ORDER_TABLE_NAME = "orders"
ORDER_ITEM_TABLE_NAME = "order_items"
```

O sufixo `_TABLE_NAME` deixa a constante autoexplicativa no ponto de
uso, onde ela aparece longe deste arquivo: `ForeignKey(f"{USER_TABLE_NAME}.id")`
diz sozinho o que é aquela string. O prefixo acompanha o modelo, no
singular (`UserTokenModel` → `USER_TOKEN_TABLE_NAME`), mesmo quando o
valor é plural.

Cada modelo passa a importar dali, dos dois lados da relação:

```python hl_lines="6 10"
# src/db/models/user.py
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel

from src.db.configs.names import USER_TABLE_NAME


class UserModel(BaseModel):
    __tablename__ = USER_TABLE_NAME

    email: Mapped[str] = mapped_column(unique=True)
```

```python hl_lines="9 13 16"
# src/db/models/user_token.py
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel

from src.db.configs.names import USER_TABLE_NAME, USER_TOKEN_TABLE_NAME


class UserTokenModel(BaseModel):
    __tablename__ = USER_TOKEN_TABLE_NAME

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{USER_TABLE_NAME}.id", ondelete="CASCADE"),
        index=True,
    )
```

O ganho aparece na hora de mexer. Renomear uma tabela vira **uma linha**
em `names.py`. O "find usages" do editor encontra todo mundo que depende
dela, porque virou símbolo em vez de string solta. E nenhuma
`ForeignKey` consegue apontar para uma tabela que não existe mais sem que
o import quebre antes.

!!! tip "Por que `db/configs/` e não `core/constants.py`?"
    Nome de tabela é detalhe do banco, e quem consome é `db/models/`.
    Deixar em `db/` mantém a dependência dentro da própria camada, e o
    módulo fica sem import nenhum do projeto — ele é só string. É isso
    que garante que ele nunca participe de um ciclo: `models` importa
    `configs`, e `configs` não importa ninguém.

!!! check "Vale também para as tabelas do SDK"
    Os modelos abstratos que o SDK entrega (`BaseUserModel`,
    `BaseUserTokenModel`, `BaseUserRefreshTokenModel`,
    `BaseWebPushSubscriptionModel`, `BaseOutboxModel`) deixam de propósito
    o `__tablename__` e a FK para o projeto concreto declarar — justamente
    para que os dois saiam do seu `names.py`, sob a sua convenção.

## `__tablename__` explícito com o Pyright

`BaseModel` declara `__tablename__` como um `@declared_attr.directive`,
que é o mecanismo do SQLAlchemy 2.0 para derivar o nome a partir da
classe. O mypy entende que uma subclasse sobrescreva isso com uma string
e **não reclama** — é o checker que `tempest type` roda, então o gate
padrão passa limpo.

O Pyright é mais estrito: lê o atributo herdado como variável mutável de
tipo invariante e acusa a atribuição.

!!! warning "`reportIncompatibleVariableOverride` no Pyright/Pylance"
    ```text
    Type "Literal['users']" is not assignable to declared type
    "_declared_directive[str]" (reportAssignmentType)
    ```

    Não é defeito no seu modelo: em tempo de execução a atribuição
    funciona, e é a forma usada em toda esta documentação. É o Pyright
    sendo mais rígido que o mypy sobre override de descritor.

Se o seu editor roda Pyright e você quer o arquivo limpo, declare o nome
pelo mesmo mecanismo da classe base:

```python hl_lines="7 8"
from sqlalchemy.orm import declared_attr
from tempest_fastapi_sdk import BaseModel

from src.db.configs.names import USER_TABLE_NAME


class UserModel(BaseModel):
    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805
        """Fixa o nome da tabela."""
        return USER_TABLE_NAME
```

Mais verboso, e equivalente em runtime. Escolha pelo checker do projeto:
com mypy (ou sem checagem estática no editor), prefira
`__tablename__ = USER_TABLE_NAME`, que é mais direto de ler.

## Convenção de nomes de constraints

`BaseModel.metadata` já vem configurado com `NAMING_CONVENTION`. Isso faz
toda PK/FK/índice/unique/check receber um nome **determinístico** —
`ix_user_email`, `uq_user_email`, `fk_order_user_id_user` — igual em toda
máquina e todo engine.

!!! check "O ganho real está nas migrações"
    Sem nomes determinísticos, o `alembic revision --autogenerate`
    inventa identificadores aleatórios e cada desenvolvedor gera um diff
    diferente para o mesmo schema. Com a convenção, o autogenerate só
    emite **diffs de schema reais** — sem churn de nomes.

## Unique composta — o nome leva todas as colunas

Uma regra de unicidade que envolve mais de uma coluna ("o mesmo título não
pode repetir no mesmo ano") é uma `UniqueConstraint` com várias colunas em
`__table_args__`. Você não precisa dar nome a ela — a convenção dá:

```python
# src/db/models/book.py
from sqlalchemy import Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class BookModel(BaseModel):
    """Livros do catálogo."""

    __tablename__ = "books"
    __table_args__ = (
        UniqueConstraint("isbn"),
        UniqueConstraint("title", "release_year"),
        UniqueConstraint("title", "author"),
        Index(None, "author", "title"),
    )

    isbn: Mapped[str] = mapped_column()
    title: Mapped[str] = mapped_column()
    release_year: Mapped[int] = mapped_column()
    author: Mapped[str] = mapped_column()


names: list[str] = sorted(
    str(item.name)
    for item in [*BookModel.__table__.constraints, *BookModel.__table__.indexes]
)
print(names)
```

Saída:

```text
['ix_books_author_books_title', 'pk_books', 'uq_books_isbn', 'uq_books_title_author', 'uq_books_title_release_year']
```

Cada constraint composta recebe **todas** as colunas no nome, unidas por `_`:
`uq_books_title_release_year` e `uq_books_title_author` são duas regras
diferentes e têm dois nomes diferentes. Os templates usam os tokens
`column_0_N_*` do SQLAlchemy, que para uma coluna só renderizam exatamente o
que os `column_0_*` renderizavam — então `uq_books_isbn`, `ix_user_email` e
`fk_order_user_id_user` continuam iguais.

Nome distinto é o que torna útil o `constraint` que o Postgres devolve em
[`parse_integrity_error`](repository.md#qual-constraint-recusou-parse_integrity_error): um
dicionário `nome → código de erro` passa a apontar para uma regra só.

```python
from typing import Any

from sqlalchemy.exc import IntegrityError

from tempest_fastapi_sdk import parse_integrity_error

CONFLICT_CODES: dict[str, str] = {
    "uq_books_isbn": "ISBN_TAKEN",
    "uq_books_title_release_year": "TITLE_TAKEN_THIS_YEAR",
    "uq_books_title_author": "AUTHOR_ALREADY_HAS_TITLE",
}


def to_conflict(error: IntegrityError) -> dict[str, Any]:
    """Traduz a violação no código que o cliente ramifica."""
    failure = parse_integrity_error(error)
    return {
        "code": CONFLICT_CODES.get(failure.constraint or "", "CONFLICT"),
        "fields": list(failure.columns),
    }
```

!!! warning "Antes desta correção, as duas uniques tinham o mesmo nome"
    Até a versão anterior, os templates de `uq`, `ix` e `fk` liam **só a
    primeira coluna**. `UniqueConstraint("title")` e
    `UniqueConstraint("title", "release_year")` viravam ambas
    `uq_books_title`. O PostgreSQL recusa o `CREATE TABLE` com
    `relation "uq_books_title" already exists`; o SQLite aceita as duas, e é
    por isso que o defeito passava no banco de teste. Quando o banco foi
    criado com a convenção antiga, as constraints compostas dele ainda têm o
    nome antigo — o passo a passo para renomear está em
    [Migrar constraints compostas da convenção antiga](migrations.md#migrar-constraints-compostas-da-convencao-antiga).

!!! note "Nome acima de 63 caracteres"
    O PostgreSQL aceita identificador de até 63 caracteres. Quando o nome da
    convenção passa disso, o SQLAlchemy encurta na hora de emitir o DDL: os
    55 primeiros caracteres, `_` e os 4 últimos dígitos hexadecimais do MD5
    do nome completo — `uq_subscription_billing_events_customer_identifier_bill_f06e`
    para uma unique de 90 caracteres. É o mesmo em toda execução, e o
    autogenerate não acusa diferença depois (medido contra PostgreSQL 16). O
    SQLite não tem limite e guarda o nome inteiro.

??? info "A única ambiguidade que sobra"
    Juntar com `_` deixa um caso que a convenção não separa: colunas
    `("a_b", "c")` e `("a", "b_c")` na mesma tabela geram o mesmo nome. Se o
    seu schema tem esse par, dê `name=` explícito a uma delas — nome
    explícito vence a convenção.

## Helpers que vêm de graça

Toda instância de `BaseModel` ganha:

```python
# Serializar para dict (útil em logs/testes)

from typing import Any

from src.db.models import UserModel
from src.schemas import UserUpdateSchema

payload = UserUpdateSchema(name="Ana Paula")
user = UserModel(name="Ana", email="ana@example.com")


data: dict[str, Any] = user.to_dict(exclude=["password_hash"])

# Atribuir vários campos de uma vez, com whitelist contra mass-assignment
user.update_from_dict(
    payload.model_dump(exclude_unset=True),
    allowed_fields={"name", "email"},   # id/role nunca são escritos
)
```

`__eq__` e `__hash__` comparam por `(tipo, id)`, então a mesma linha
carregada em sessões diferentes é igual — prático em testes e `set`s.
Linhas ainda não persistidas (`id is None`) caem para identidade Python.

!!! warning "Use sempre `allowed_fields` em payloads externos"
    `update_from_dict` sem `allowed_fields` aceita qualquer coluna
    mapeada. Para corpos de PATCH vindos do cliente, passe a whitelist —
    é a defesa contra mass-assignment em colunas sensíveis (`id`, `role`,
    `is_active`).

## O nome da tabela — `get_table_name()`

Chave de dict, lookup no painel, assert de teste: quando você precisa do
nome da tabela, peça ao model, não ao dunder:

```python
from tempest_fastapi_sdk import AdminModel, AdminSite, BaseModel


class OrderItemModel(BaseModel):
    """Item de um pedido."""


print(OrderItemModel.get_table_name())  # order_item

site = AdminSite(title="Painel")
site.register(AdminModel(model=OrderItemModel))
admin = site.get(OrderItemModel)
```

`get_table_name()` lê a tabela que o mapper construiu, não o
`__tablename__`: responde o nome derivado da classe, o `__tablename__`
explícito e também o model declarado com `__table__ = Table(...)`, que
não tem `__tablename__` nenhum. Numa herança de tabela única, a subclasse
responde a tabela do pai, onde as linhas dela moram. O retorno é `str` para
o type-checker, e `AdminSite.get` / `require` / `unregister` aceitam a
própria classe no lugar da string.

**Recap:** herde `BaseModel`, declare só as colunas do seu domínio, e o
SDK entrega id/timestamps/soft-delete, nomes de constraint determinísticos
(unique composta com todas as colunas no nome) e helpers de serialização.
