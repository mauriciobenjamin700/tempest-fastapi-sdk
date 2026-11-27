# Banco de dados

Esta é a camada que toda service Tempest usa para falar com PostgreSQL
(produção) ou SQLite (desenvolvimento/testes) sobre **SQLAlchemy 2.0
async**. Ela existe para que você nunca reescreva a mesma engine,
a mesma sessão por request, o mesmo CRUD e a mesma paginação em cada
projeto.

!!! info "Instalação"
    O núcleo de banco já vem com `tempest-fastapi-sdk`. Os drivers async
    entram por extra — `uv add "tempest-fastapi-sdk[postgres]"` (PostgreSQL,
    traz `asyncpg`) ou `[sqlite]` (SQLite em dev, traz `aiosqlite`).

São quatro peças, e você vai conhecê-las uma de cada vez:

| Peça | Símbolo | Para quê |
| --- | --- | --- |
| Modelo base | `BaseModel` | As quatro colunas canônicas (`id` / `is_active` / `created_at` / `updated_at`) + helpers de serialização. |
| Conexão | `AsyncDatabaseManager` | Engine, pool, sessão por request, `health_check`. |
| Repository | `BaseRepository[Model]` | CRUD async, filtros por convenção, operações em lote, paginação. |
| Migrações | `AlembicHelper` | Bootstrap do Alembic, autogenerate, gate de drift no CI. |

Mais três opcionais que entram quando o domínio pede: os **mixins**
(`SoftDeleteMixin`, `AuditMixin`, `MFAMixin`), a **paginação por cursor**
e o **`SlowQueryLogger`**.

!!! tip "Como ler as subpáginas"
    Elas são progressivas. Comece pelo modelo, conecte o banco, suba um
    repository, aprenda os filtros, então paginação, migrações e
    observabilidade. Cada bloco de código é um arquivo completo — copie,
    cole, rode. Se você só quer a referência da API, pule para
    [Referência »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference.md).

## Onde está cada tarefa

Use esta tabela para abrir só a subpágina da tarefa que você tem; elas estão na ordem de leitura.

| Subpágina | O que resolve |
| --- | --- |
| [1. O modelo base](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/database/models.md) | Use para declarar tabelas com `BaseModel`: nomes de tabela centralizados, convenção de nomes de constraints e os helpers que vêm de graça. |
| [2. Conectar ao banco](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/database/connection.md) | Use para abrir engine e sessão com `AsyncDatabaseManager`: sessão por request, lifespan, health check, uso fora de request e SQLite (WAL, chave estrangeira). |
| [3. O repository](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/database/repository.md) | Use para o CRUD async com `BaseRepository`: modo direto ou subclasse, erro de constraint, eager-loading, signals e expressões `F`/`Q`. |
| [4. Filtros por convenção](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/database/filters.md) | Use para filtrar por `campo__operador` e saber quando uma chave ou valor é recusado e como a paginação herda os operadores. |
| [5. Operações em lote](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/database/bulk.md) | Use para gravar muitas linhas de uma vez, mantendo ou contornando a unit-of-work, inclusive inserção idempotente (ON CONFLICT DO NOTHING). |
| [6. Soft-delete e auditoria (mixins)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/database/mixins.md) | Use para adicionar soft-delete, `created_by`/`updated_by` e o idioma preferido do usuário com mixins. |
| [7. Paginação](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/database/pagination.md) | Use para paginar por offset ("página 3 de 12") ou por cursor (tabela grande) e limitar colunas de ordenação e tamanho de página. |
| [8. Migrações Alembic](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/database/migrations.md) | Use para fazer bootstrap do Alembic, gerar e aplicar revisões, travar drift no CI e migrar constraints da convenção antiga. |
| [9. Detectar queries lentas](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/database/slow-queries.md) | Use para logar queries acima de um limite com `SlowQueryLogger`. |

## Recap

- `BaseModel` traz `id`, `is_active`, `created_at` e `updated_at`; você declara
  só as colunas do seu domínio, e o hook do Alembic mantém essa ordem nas
  migrações geradas.
- Constraint composta ganha nome com todas as colunas
  (`uq_books_title_release_year`); banco criado antes dessa correção se
  migra com os `RENAME` que `legacy_constraint_renames` escreve.
- Um `AsyncDatabaseManager` por aplicação, em `resources.py` — não um por
  request.
- `BaseRepository` serve instanciado para CRUD puro e subclassificado quando
  aparece query própria; filtro é um dict com convenção previsível, e `None`
  numa coluna simples é `IS NULL`.
- Operação em lote tem duas famílias: a que devolve instância (`add_all`,
  `update_many`) e a que não devolve, mas é uma ida ao banco.
- Mixin entra quando o domínio pede: soft-delete e auditoria custam coluna,
  e o filtro é seu — o `SoftDeleteMixin` não instala filtro global, então
  esconder linha apagada é passar `{"deleted_at": None}`.
- Paginação tem duas formas com propósitos diferentes: `paginate` para navegar
  por página, `cursor_paginate` para lista que cresce enquanto o usuário lê.
- Migração é `init` uma vez, `revision --autogenerate` por mudança, `upgrade`
  no deploy — e `SlowQueryLogger` no `engine` mostra a query lenta com
  `EXPLAIN` antes do usuário reclamar.

## Próximos passos

As subpáginas acima cobrem o núcleo. Os recursos avançados de banco têm receitas
dedicadas:

- [Multi-tenant »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/multi-tenant.md) — `TenantScopedRepository` para
  isolamento por tenant.
- [Audit trail »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/audit-trail.md) — `BaseAuditLogModel`, `add_audited` /
  `update_audited` / `delete_audited` (quem mudou o quê, na mesma tx).
- [Outbox transacional »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/outbox.md) — `BaseOutboxModel` + `OutboxRelay`,
  `save_with_outbox` para publicar eventos atomicamente com a escrita.
- [Offline sync »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/offline-sync.md) — `changes_since` + paginação de
  delta para clientes offline-first.
- [Deploy seguro »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/deploy-safety.md) — migrações destrutivas + shutdown
  gracioso.
- [Testes »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/testing.md) — SQLite em memória, fixtures, `create_tables`.
