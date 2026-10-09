# Colunas de enum (seguras nos dois bancos)

O SQLAlchemy já mapeia `Mapped[MeuEnum]` para uma coluna. Os defaults
dele, porém, custam segurança de três formas — e o SDK troca as três.

## O que muda

```python
from sqlalchemy.orm import Mapped

from tempest_fastapi_sdk import BaseModel, BaseStrEnum


class OrderStatus(BaseStrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class OrderModel(BaseModel):
    status: Mapped[OrderStatus]
```

Sem nenhuma configuração, essa anotação produz:

```sql
-- PostgreSQL
CREATE TYPE order_status_enum AS ENUM ('open', 'in_progress', 'done');
status order_status_enum NOT NULL

-- SQLite
status VARCHAR(11) NOT NULL
CONSTRAINT ck_order_order_status_enum
    CHECK (status IN ('open', 'in_progress', 'done'))
```

Os três defaults trocados:

1. **Guarda o `value`, não o `name`.** O default do SQLAlchemy gravaria
   `IN_PROGRESS`. Todo consumidor que não é este processo Python — um
   relatório, um dashboard, um serviço vizinho — leria uma string que o
   domínio nunca definiu.
2. **`CHECK` no SQLite.** O default emite um `VARCHAR` **cru, sem
   constraint**: a coluna de produção rejeita valor inválido, a de teste
   aceita em silêncio. Um bug que o banco pegaria em produção passaria na
   suíte.
3. **Nome de tipo sem colisão.** O default nomearia o tipo do PostgreSQL
   como `orderstatus`; o SDK usa `order_status_enum`, porque tipo e
   tabela dividem o mesmo namespace.

!!! info "A ordem de declaração vira a ordem do tipo"
    O PostgreSQL ordena uma coluna `ENUM` pela ordem dos labels, não pelo
    alfabeto. Declarar `OPEN, IN_PROGRESS, DONE` faz `ORDER BY status`
    seguir o fluxo de trabalho.

## Quando a anotação não basta

`enum_column()` é a mesma coisa escrita por extenso, para quando a coluna
precisa de argumentos:

```python
from sqlalchemy.orm import Mapped

from tempest_fastapi_sdk import BaseModel, BaseStrEnum, enum_column


class OrderStatus(BaseStrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class OrderModel(BaseModel):
    status: Mapped[OrderStatus] = enum_column(
        OrderStatus, default=OrderStatus.OPEN, index=True
    )
```

Um tipo explícito sempre vence o mapa de anotações, então
`mapped_column(sqlalchemy.Enum(...))` continua disponível para uma coluna
que precise do comportamento original.

## Mudou o enum? Isso é mudança de schema

E o `alembic revision --autogenerate` **não detecta sozinho**, em nenhum
dos dois bancos:

- no PostgreSQL os labels moram no `pg_enum`, que o autogenerate não
  compara;
- no SQLite moram dentro do `CHECK`, que ele também não compara — e o
  `VARCHAR(n)` só muda de tamanho quando o valor *mais longo* muda, então
  nem o `compare_type` percebe.

O SDK fecha isso com o hook `sync_enum_types`, já ligado no `env.py`
gerado pelo `tempest db init`. Acrescente um membro ao enum, rode o
autogenerate e a migration sai preenchida:

```python
from alembic import op

from tempest_fastapi_sdk import EnumColumnRef


def upgrade() -> None:
    """Acrescenta ``archived`` ao enum de status do pedido."""
    op.replace_enum(
        "order_status_enum",
        new_values=["open", "in_progress", "done", "archived"],
        old_values=["open", "in_progress", "done"],
        columns=[EnumColumnRef(table="order", column="status")],
    )
```

### Por que não `ALTER TYPE ... ADD VALUE`

É o comando que todo mundo tenta primeiro, e ele:

- não roda dentro de um bloco de transação em servidores mais antigos —
  o erro clássico de migration de enum;
- não remove valor nenhum;
- não reordena.

`replace_enum` renomeia o tipo antigo, cria o novo com o nome real,
converte cada coluna dependente e derruba o antigo. Tudo isso é DDL
comum, então roda dentro da transação do Alembic:

```sql
ALTER TYPE order_status_enum RENAME TO order_status_enum__old;
CREATE TYPE order_status_enum AS ENUM ('open', 'in_progress', 'done', 'archived');
ALTER TABLE "order" ALTER COLUMN status
    TYPE order_status_enum USING (status::text)::order_status_enum;
DROP TYPE order_status_enum__old;
```

No SQLite a mesma operação reconstrói a tabela para que o `CHECK`
acompanhe.

!!! tip "`DEFAULT` da coluna é preservado"
    Um `DEFAULT 'open'::order_status_enum` ainda aponta para o tipo que
    está saindo, e o PostgreSQL recusa a conversão enquanto isso for
    verdade. A operação lê o default atual do `information_schema`, remove
    antes da conversão e restaura depois — em vez de assumir que não há
    default.

### Renomear um membro

Sem ajuda, remover `wip` para introduzir `in_progress` falha na conversão
das linhas que ainda têm `wip`. Diga o mapeamento:

```python
from alembic import op

from tempest_fastapi_sdk import EnumColumnRef


def upgrade() -> None:
    """Renomeia ``wip`` para ``in_progress``, levando as linhas junto."""
    op.replace_enum(
        "task_status_enum",
        new_values=["open", "in_progress"],
        old_values=["open", "wip"],
        columns=[EnumColumnRef(table="task", column="status")],
        value_map={"wip": "in_progress"},
    )
```

A operação é reversível: o `downgrade` troca as listas e inverte o
`value_map` sozinho.

!!! warning "Modo offline (`--sql`) não é suportado no PostgreSQL"
    Preservar o `DEFAULT` exige lê-lo do banco, e um script offline não
    tem conexão. Em vez de gerar em silêncio um script que derruba o
    default, a operação levanta `NotImplementedError` explicando isso.
    Rode o upgrade online, ou escreva a sequência `ALTER TYPE` à mão para
    o script offline.

### Detecção é deliberadamente conservadora

Um enum que o backend não consegue reportar é **ignorado**, não comparado
com um palpite — emitir um `replace_enum` errado derrubaria valores de
linhas vivas. No SQLite isso significa que só um `CHECK` no formato que o
SDK gera é lido de volta; uma constraint escrita à mão não é interpretada.

## Migrations não importam o SDK

O Alembic renderizaria `TempestEnum` como um caminho pontilhado para
dentro deste pacote, num arquivo cujos únicos imports são `alembic.op` e
`sqlalchemy as sa` — a migration quebraria no import. O hook
`render_enum_types` renderiza um `sa.Enum` com os valores por extenso, o
que também transforma a migration num retrato de verdade, independente do
que o enum em Python virar depois.

### Um `CHECK` só por coluna

Sob SQLAlchemy 2.1, o Alembic deixa de reconhecer o `CHECK` que o tipo enum
anexa à coluna (ele procura `constraint._create_rule.target`, que o 2.1 não
define mais). Medido com SQLAlchemy 2.1.4 e Alembic 1.20.0, o `create_table`
gerado saía assim (as colunas do `BaseModel` resumidas a `id`):

```python
import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    """O que o autogenerate escrevia sob SQLAlchemy 2.1, sem o SDK."""
    op.create_table(
        "things",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "GUARDIAN",
                "TEEN",
                name="user_role_enum",
                native_enum=True,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('GUARDIAN', 'TEEN')", name="user_role_enum"),
        sa.CheckConstraint(
            "role IN ('GUARDIAN', 'TEEN')", name=op.f("ck_things_user_role_enum")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_things")),
    )
```

A naming convention transforma os dois nomes em `ck_things_user_role_enum`, e
o PostgreSQL 16 recusa o segundo:
`check constraint "ck_things_user_role_enum" already exists`.

O `render_enum_types` omite esse `CHECK` ligado ao tipo. O
`sa.Enum(..., create_constraint=True)` da coluna já o recria onde ele faz
sentido: no SQLite a tabela fica com exatamente um `CHECK`, chamado
`ck_things_user_role_enum` (medido sob SQLAlchemy 2.0.52 e 2.1.4), e no
PostgreSQL não há `CHECK` nenhum, só o tipo nativo. Um `CheckConstraint` que
você declara em `__table_args__` não é ligado a tipo e continua sendo
renderizado.

## Downgrade derruba o tipo

O downgrade que o Alembic escreve para uma tabela nova é só `op.drop_table`,
e no PostgreSQL isso deixa o `ENUM` para trás. Medido no PostgreSQL 16: depois
de `upgrade` → `downgrade`, o `user_role_enum` continuava em `pg_type`, e o
`upgrade` seguinte falhava com `type "user_role_enum" already exists`.

O hook `drop_enum_types_on_downgrade`, ligado no `env.py` gerado, acrescenta o
drop do tipo no fim do downgrade:

```python
from alembic import op


def downgrade() -> None:
    """Downgrade gerado para duas tabelas que dividem um enum."""
    op.drop_table("things")
    op.drop_table("others")
    op.drop_enum_type("user_role_enum")
```

- `op.drop_enum_type` emite `DROP TYPE IF EXISTS` no PostgreSQL e não faz
  nada nos outros bancos — no SQLite o enum mora no `CHECK`, que sai junto
  com a tabela.
- Um tipo usado por duas tabelas só é derrubado pela revisão que remove a
  última: se outra tabela do model ainda usa o tipo, o hook não acrescenta o
  drop.
- A decisão sai só da metadata, sem conexão, então a mesma migration roda nos
  dois bancos. Com o hook, `upgrade` → `downgrade` → `upgrade` passa no
  PostgreSQL 16.

!!! tip "Migration escrita antes desta versão"
    O hook só age em revisões novas. Numa migration já gerada, acrescente
    `op.drop_enum_type("<nome>_enum")` no fim do `downgrade()`, depois dos
    `op.drop_table` que usavam o tipo.

## Recapitulando

- `Mapped[MeuEnum]` já sai seguro: `value` no banco, `ENUM` nativo no
  PostgreSQL, `CHECK` no SQLite, nome de tipo sem colisão.
- `enum_column()` para quando a coluna precisa de `default`, `index`, etc.
- Mudança de membro é mudança de schema, e o `sync_enum_types` a detecta
  onde o autogenerate é cego.
- `op.replace_enum(...)` adiciona, remove e reordena numa operação só,
  dentro da transação, com `value_map=` para renomes e `downgrade`
  automático.
- O `CHECK` ligado ao tipo não sai duplicado na migration (o
  `render_enum_types` o omite), e o downgrade de tabela nova derruba o
  `ENUM` do PostgreSQL com `op.drop_enum_type`.
