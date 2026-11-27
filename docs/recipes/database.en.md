---
description: "Index of the database layer (SQLAlchemy 2.0 async, PostgreSQL/SQLite): model, connection, repository, filters, bulk, mixins, pagination, migrations and slow queries."
---

# Database

This is the layer every Tempest service uses to talk to PostgreSQL
(production) or SQLite (development/tests) over **SQLAlchemy 2.0 async**.
It exists so you never rewrite the same engine, the same per-request
session, the same CRUD and the same pagination in every project.

!!! info "Installation"
    The database core ships with `tempest-fastapi-sdk`. The async drivers
    come via extras — `uv add "tempest-fastapi-sdk[postgres]"` (PostgreSQL,
    pulls in `asyncpg`) or `[sqlite]` (SQLite in dev, pulls in `aiosqlite`).

There are four pieces, and you'll meet them one at a time:

| Piece | Symbol | What for |
| --- | --- | --- |
| Base model | `BaseModel` | The four canonical columns (`id` / `is_active` / `created_at` / `updated_at`) + serialization helpers. |
| Connection | `AsyncDatabaseManager` | Engine, pool, per-request session, `health_check`. |
| Repository | `BaseRepository[Model]` | Async CRUD, convention-based filters, bulk ops, pagination. |
| Migrations | `AlembicHelper` | Alembic bootstrap, autogenerate, CI drift gate. |

Plus three opt-ins that show up when the domain asks for them: the
**mixins** (`SoftDeleteMixin`, `AuditMixin`, `MFAMixin`), **cursor
pagination**, and the **`SlowQueryLogger`**.

!!! tip "How to read the subpages"
    They are progressive. Start with the model, connect the database, stand up
    a repository, learn the filters, then pagination, migrations and
    observability. Every code block is a complete file — copy, paste, run.
    If you only want the API reference, jump to
    [Reference »](../reference.md).

## Where each task lives

Use this table to open only the subpage for the task at hand; they are listed in reading order.

| Subpage | What it solves |
| --- | --- |
| [1. The base model](database/models.md) | Use to declare tables with `BaseModel`: centralized table names, the constraint naming convention and the helpers you get for free. |
| [2. Connect to the database](database/connection.md) | Use to open the engine and sessions with `AsyncDatabaseManager`: per-request session, lifespan, health check, use outside a request and SQLite (WAL, foreign keys). |
| [3. The repository](database/repository.md) | Use for async CRUD with `BaseRepository`: direct or subclass mode, constraint errors, eager-loading, signals and `F`/`Q` expressions. |
| [4. Convention-based filters](database/filters.md) | Use to filter with `field__operator` and know when a key or value is refused and how pagination inherits the operators. |
| [5. Bulk operations](database/bulk.md) | Use to write many rows at once, keeping or bypassing the unit of work, including idempotent inserts (ON CONFLICT DO NOTHING). |
| [6. Soft-delete and auditing (mixins)](database/mixins.md) | Use to add soft-delete, `created_by`/`updated_by` and the user's preferred language through mixins. |
| [7. Pagination](database/pagination.md) | Use to paginate by offset ("page 3 of 12") or by cursor (large tables) and restrict sortable columns and page size. |
| [8. Alembic migrations](database/migrations.md) | Use to bootstrap Alembic, generate and apply revisions, block drift in CI and migrate constraints from the old convention. |
| [9. Detect slow queries](database/slow-queries.md) | Use to log queries above a threshold with `SlowQueryLogger`. |

## Recap

- `BaseModel` brings `id`, `is_active`, `created_at` and `updated_at`; you
  declare only your domain columns, and the Alembic hook keeps that order in
  generated migrations.
- A composite constraint is named after every column
  (`uq_books_title_release_year`); a database created before that fix
  migrates with the `RENAME`s `legacy_constraint_renames` writes.
- One `AsyncDatabaseManager` per application, in `resources.py` — not one per
  request.
- `BaseRepository` works instantiated for plain CRUD and subclassed once real
  queries appear; a filter is a dict with predictable conventions, and `None`
  on a bare column is `IS NULL`.
- Bulk work comes in two families: the one that hands the instances back
  (`add_all`, `update_many`) and the one that does not, but is a single trip to
  the database.
- A mixin joins when the domain asks for it: soft-delete and auditing cost a
  column, and the filter is yours — `SoftDeleteMixin` installs no global
  filter, so hiding a deleted row means passing `{"deleted_at": None}`.
- Pagination has two shapes with different purposes: `paginate` to walk pages,
  `cursor_paginate` for a list that grows while the user reads it.
- Migrations are `init` once, `revision --autogenerate` per change, `upgrade` on
  deploy — and `SlowQueryLogger` on the engine shows the slow query with
  `EXPLAIN` before a user complains.

## Next steps

The subpages above cover the core. The advanced database features have dedicated
recipes:

- [Multi-tenant »](multi-tenant.md) — `TenantScopedRepository` for
  per-tenant isolation.
- [Audit trail »](audit-trail.md) — `BaseAuditLogModel`, `add_audited` /
  `update_audited` / `delete_audited` (who changed what, in the same tx).
- [Transactional outbox »](outbox.md) — `BaseOutboxModel` + `OutboxRelay`,
  `save_with_outbox` to publish events atomically with the write.
- [Offline sync »](offline-sync.md) — `changes_since` + delta pagination
  for offline-first clients.
- [Safe deploys »](deploy-safety.md) — destructive migrations + graceful
  shutdown.
- [Testing »](testing.md) — in-memory SQLite, fixtures, `create_tables`.
