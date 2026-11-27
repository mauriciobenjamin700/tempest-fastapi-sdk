---
description: "Use para migrações com `AlembicHelper`: bootstrap, gerar e aplicar revisões no startup, travar drift de schema no CI e migrar constraints da convenção antiga."
---

# 8. Migrações Alembic

Use para fazer bootstrap do Alembic, gerar e aplicar revisões, travar drift no CI e migrar constraints da convenção antiga.

Parte da receita [Banco de dados](../database.md).

`AlembicHelper` embrulha o Alembic com uma config curada (timezone UTC,
arquivos com prefixo de data, `target_metadata` já ligado, modo batch).
Fluxo completo: bootstrap → revisão → aplicar → gate de CI.

## Bootstrap, uma vez por projeto

```python
# scripts/alembic_init.py
from tempest_fastapi_sdk import AlembicHelper

from src.core.settings import settings

helper = AlembicHelper(config_path="alembic.ini", db_url=settings.DATABASE_URL)
helper.init(
    directory="alembic",
    metadata_module="src.db.models",   # expõe BaseModel
    metadata_attr="BaseModel",
    db_url=settings.DATABASE_URL,
)
```

```bash
uv run python scripts/alembic_init.py
```

Cria:

```text
alembic.ini                 # config curada pelo SDK (UTC, prefixo de data, post-write hooks, prepend_sys_path)
alembic/
├── env.py                  # run_alembic_env(target_metadata): hooks, compare_type, batch
├── script.py.mako
└── versions/
```

## Gerar revisões

```python
# scripts/make_migration.py
import sys

from tempest_fastapi_sdk import AlembicHelper

from src.core.settings import settings

helper = AlembicHelper("alembic.ini", db_url=settings.DATABASE_URL)
helper.revision(message=sys.argv[1], autogenerate=True)
```

```bash
uv run python scripts/make_migration.py "add users table"
```

O arquivo cai em
`alembic/versions/2026_05_16_1432-ae12cd34_add_users_table.py` — o prefixo
de data ordena cronologicamente e torna conflitos de merge óbvios.

!!! check "Migrações já saem lint-clean"
    O `alembic.ini` que o `init()` escreve inclui `[post_write_hooks]` que
    roda `ruff format` e **depois** `ruff check --fix` em cada revisão.
    Sem isso, os arquivos do Alembic falham no `tempest lint` (`W291` no
    `Revises:` vazio, `E501` em `sa.Column(...)` longas) — e é por isso
    que o formatter vem primeiro: ele é quem quebra a linha longa e tira
    o espaço à direita, então rodar o linter antes reportaria erros que
    o hook seguinte ia consertar. Os hooks usam a
    config de `ruff` do **seu** projeto. Sem `ruff` (no `PATH` ou no
    ambiente do Python que roda o Alembic) a revisão sai sem formatação e
    sem erro; em todo scaffold `tempest new` ele já é dependência de dev.

## Aplicar no startup

```python
# src/api/app.py — dentro do lifespan

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tempest_fastapi_sdk import AlembicHelper

from src.api.dependencies.resources import db
from src.core.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Run pending migrations, then serve."""
    helper = AlembicHelper("alembic.ini", db_url=settings.DATABASE_URL)
    await helper.sync_schema_async()
    await db.connect()
    yield
    await db.disconnect()
```

`sync_schema_async()` é o bootstrap inteiro: banco vazio roda a árvore toda,
banco criado antes do Alembic é carimbado na revision base e depois migrado,
e banco já migrado só recebe o que falta — o retorno
(`SchemaSyncOutcomeEnum`) diz qual dos caminhos rodou. Num projeto gerado
pelo `tempest new` essa chamada já está em `run_migrations`
([Ciclo de vida no lifespan](connection.md#ciclo-de-vida-no-lifespan)).

Ele roda numa thread de trabalho: o `env.py` do SDK chama `asyncio.run`, que
não aninha dentro do loop do lifespan, então o `helper.sync_schema()` síncrono
ali levanta `RuntimeError` nomeando o `sync_schema_async`. Todo comando que
roda o `env.py` tem o mesmo par `_async` —
veja [Migrations »](../migrations.md#de-codigo-async-use-o-metodo-_async).

!!! warning "Migrações destrutivas: use `safe_upgrade`"
    `helper.pending_destructive_ops()` lista DROPs de coluna/tabela
    pendentes; `helper.safe_upgrade()` levanta `DestructiveMigrationError`
    em vez de apagar dados silenciosamente. O guia completo de deploy
    (migração + shutdown gracioso) está em
    [Deploy seguro »](../deploy-safety.md).

## Gate de CI — o schema deve casar com os modelos

```python
# scripts/check_migrations.py
import sys

from tempest_fastapi_sdk import AlembicHelper

from src.core.settings import settings

helper = AlembicHelper("alembic.ini", db_url=settings.DATABASE_URL)
if not helper.check():
    print("Schema drift detected — run make_migration.py and commit.")
    sys.exit(1)
print("Schema is in sync.")
```

```yaml
# .github/workflows/ci.yml
- name: Check migrations are in sync
  run: uv run python scripts/check_migrations.py
```

!!! check "`check()` não acusa drift falso de enum no SQLite (v0.313.0, #483)"
    No SQLite uma coluna enum é um `VARCHAR` mais um `CHECK`
    (`ck_<tabela>_<enum>`). O comparador de `CHECK` do Alembic lia a
    constraint do banco mas a descartava do lado da metadata — elas são
    `_type_bound`, ligadas ao tipo da coluna —, então `check()` propunha
    um `remove_constraint` fantasma logo depois de um `upgrade head`
    limpo e o gate de drift vivia desligado. O `run_alembic_env` passa um
    `include_object` que ignora esse `CHECK`, chaveado por `(tabela, nome)`
    para que duas tabelas com um `CHECK` homônimo não escondam o drift uma
    da outra. Drift real continua acusado — coluna nova, e também a
    mudança de membro do enum: o `sync_enum_types` anexa um
    `replace_enum`, e o `ReplaceEnumOp` ganhou `to_diff_tuple`, sem o qual
    o `check()` levantava `NotImplementedError` ao montar o diff em vez de
    reportá-lo.

!!! info "Colunas base sempre primeiro"
    O `env.py` do SDK instala o hook `reorder_base_columns_first`, então
    toda migração gerada lista `id` / `is_active` / `created_at` /
    `updated_at` antes das suas colunas — diffs consistentes entre
    pessoas.

!!! check "Coluna `NOT NULL` nova não explode mais (v0.67.0)"
    Adicionar uma coluna `NOT NULL` numa tabela que **já tem linhas**
    estoura no Postgres com `NotNullViolationError: column "x" contains
    null values` — porque um `default=` Python só roda no insert do ORM,
    nunca como DDL. O SDK agora instala um segundo hook,
    `backfill_non_nullable_defaults`: toda coluna adicionada que seja
    `nullable=False`, **sem** `server_default`, mas **com** um `default`
    escalar no model, recebe um `server_default` derivado desse default —
    então a migração gerada backfilla as linhas existentes na mesma
    instrução.

    ```python
    # No model — só o default Python:
    is_professional: Mapped[bool] = mapped_column(default=False)
    ```

    ```python
    # A migração gerada agora sai assim (note o server_default):
    op.add_column(
        "users",
        sa.Column(
            "is_professional",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    ```

    Cobre `bool` / `int` / `float` / `str` / `Enum` (usa `.value`). **Não**
    age quando o default é callable (`uuid4`, `func.now()`) ou inexistente
    — esses precisam de uma migração de dados escrita à mão, porque o SDK
    não tem como inferir um valor de backfill seguro.

    Já tem um `env.py` antigo? Atualize o import + wiring para os dois
    hooks compostos:

    ```python
    # alembic/env.py
    from tempest_fastapi_sdk.db.alembic_hooks import (
        backfill_non_nullable_defaults,
        compose_hooks,
        reorder_base_columns_first,
    )

    _process_revision_directives = compose_hooks(
        reorder_base_columns_first,
        backfill_non_nullable_defaults,
    )

    # ...e passe-o em context.configure(process_revision_directives=...)
    ```

    Para uma migração **já gerada** que estourou, adicione o
    `server_default=sa.text("...")` na mão no `op.add_column` (ou
    backfille + `alter_column` para remover o default depois).

## Migrar constraints compostas da convenção antiga

Se o seu banco foi criado antes da correção de nomes compostos, cada
unique, índice e foreign key **com mais de uma coluna** ainda tem o nome da
primeira coluna só. Os de uma coluna não mudaram. Ao atualizar o SDK, o gate
de drift acusa diferença — e o que o autogenerate propõe não é o que você
quer aplicar.

Num banco com `UniqueConstraint("title", "release_year")`,
`Index(None, "author", "title")` e uma FK composta, o
`alembic revision --autogenerate` gera (medido contra PostgreSQL 16 e Alembic
1.19.1):

```python
from alembic import op


def upgrade() -> None:
    """O que o autogenerate gera sobre o banco antigo."""
    with op.batch_alter_table("books", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_books_author"))
        batch_op.drop_constraint(batch_op.f("uq_books_title"), type_="unique")
        batch_op.create_index(
            batch_op.f("ix_books_author_books_title"),
            ["author", "title"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_books_title_release_year"),
            ["title", "release_year"],
        )
```

!!! danger "Não aplique esse drop + create no PostgreSQL"
    Dois problemas medidos, e um custo:

    - **Quebra quando uma FK depende da unique.** Com uma FK composta
      apontando para a unique que ele derruba, o PostgreSQL recusa:
      `cannot drop constraint uq_authors_tenant_id on table authors because
      other objects depend on it`.
    - **Não vê as foreign keys.** Uma FK composta cujo nome mudou não aparece
      no diff; ela fica com o nome antigo no banco, e é esse nome que o
      `parse_integrity_error` devolve.
    - **Reconstrói o índice.** Criar a unique de novo constrói o índice
      dela lendo a tabela inteira. O `RENAME` só troca o nome — e no
      PostgreSQL renomear a constraint renomeia junto o índice por trás dela
      (medido em `pg_indexes`).

O `legacy_constraint_renames` compara a `metadata` com a convenção antiga e
lista toda constraint composta cujo nome mudou — foreign keys incluídas —,
com o `RENAME` pronto. Rode uma vez, com todos os models importados:

```python
# scripts/constraint_renames.py
from sqlalchemy.dialects import postgresql

import src.db.models
from tempest_fastapi_sdk import BaseModel, legacy_constraint_renames

dialect = postgresql.dialect()
renames = legacy_constraint_renames(BaseModel.metadata)

print("def upgrade() -> None:")
for rename in renames:
    print(f"    op.execute({rename.statement(dialect)!r})")

print("def downgrade() -> None:")
for rename in renames:
    print(f"    op.execute({rename.inverse().statement(dialect)!r})")
```

Crie uma revisão **vazia** (`helper.revision(message="rename composite
constraints", autogenerate=False)`) e cole a saída nela:

```python
"""rename composite constraints to the per-column convention."""

from alembic import op

revision: str = "b7e1c2d3a4f5"
down_revision: str | None = "ae12cd34"


def upgrade() -> None:
    """Renomeia as constraints compostas para a convenção por coluna."""
    op.execute("ALTER INDEX ix_books_author RENAME TO ix_books_author_books_title")
    op.execute(
        "ALTER TABLE books RENAME CONSTRAINT uq_books_title "
        "TO uq_books_title_release_year"
    )


def downgrade() -> None:
    """Volta aos nomes da convenção antiga."""
    op.execute("ALTER INDEX ix_books_author_books_title RENAME TO ix_books_author")
    op.execute(
        "ALTER TABLE books RENAME CONSTRAINT uq_books_title_release_year "
        "TO uq_books_title"
    )
```

Depois do `upgrade`, o autogenerate não vê mais diferença nenhuma, e as
constraints de uma coluna ficaram intocadas (medido: `compare_metadata`
devolve `[]` sobre o banco renomeado, em `tests/db/test_naming_live.py`).

!!! tip "Por que colar, e não chamar a função dentro da migração"
    A migração tem que fazer amanhã o mesmo que faz hoje. Chamada no
    `upgrade()`, a função calcularia os renames a partir dos models do dia
    em que a migração roda — uma constraint composta criada depois entraria
    num arquivo antigo. As revisões anteriores não precisam de nada: o
    autogenerate gravou o nome em cada uma com `op.f(...)`.

!!! info "Detalhes que valem saber"
    - **O SDK também tem uma.** O `make_user_oauth_account_model` declara
      `UniqueConstraint("provider", "subject")` e
      `UniqueConstraint("user_id", "provider")`: `uq_<tabela>_provider` vira
      `uq_<tabela>_provider_subject`, e `uq_<tabela>_user_id` vira
      `uq_<tabela>_user_id_provider`. Com o model importado, a função os
      lista junto dos seus.
    - **Duas entradas com o mesmo `old_name`** são a colisão antiga: o
      PostgreSQL recusou o segundo `CREATE`, então o banco tem só uma delas.
      Fique com a entrada cujas colunas são as da constraint que existe.
    - **SQLite não renomeia constraint** — o nome mora no texto do
      `CREATE TABLE` —, e `statement()` levanta `ValueError` fora do
      PostgreSQL. No SQLite de desenvolvimento, aplique a migração em batch
      que o autogenerate gera (ela reconstrói a tabela e funciona, medido) ou
      recrie o banco.
    - **Quer adiar?** Dê à constraint o nome que ela já tem no banco:
      `UniqueConstraint("title", "release_year", name="uq_books_title")`.
      Nome explícito vence a convenção, e a função passa a ignorá-la.

**Recap:** `init` uma vez, `revision --autogenerate` por mudança, `upgrade`
no startup, `check` no CI, `safe_upgrade` para proteger dados. Banco criado
antes da correção de nomes compostos: `legacy_constraint_renames` escreve os
`RENAME`, e você cola numa revisão vazia.
