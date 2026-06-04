# Banco de dados

Tudo que toca PostgreSQL/SQLite via SQLAlchemy 2.0 async — modelo base, repository async, mixins, migrações, paginação por cursor.

## Mixins de auditoria & soft-delete


`SoftDeleteMixin` adiciona uma coluna de timestamp `deleted_at` com helpers `mark_deleted()` / `mark_restored()` / `is_deleted`. `AuditMixin` adiciona colunas UUID `created_by` / `updated_by` com helpers `stamp_created_by(user_id)` / `stamp_updated_by(user_id)`. Misture-os ao lado do `BaseModel`:

```python
# src/db/models/user.py
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AuditMixin, BaseModel, SoftDeleteMixin


class UserModel(BaseModel, SoftDeleteMixin, AuditMixin):
    """Users — soft-deletable and audited."""

    name: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str] = mapped_column()
```

A filtragem é responsabilidade de quem chama — o mixin não instala um filtro global. Esconda linhas soft-deleted dos endpoints de listagem passando `deleted_at=None` (ou filtrando na sua subclasse de repository). Carimbar as colunas de auditoria pertence à camada de service, onde o usuário atual está em escopo. Ambos os padrões vivem dentro do service:

```python
# src/services/user.py
from uuid import UUID

from tempest_fastapi_sdk import BaseService

from src.db.repositories import UserRepository
from src.schemas import UserResponse, UserUpdateSchema


class UserService(BaseService[UserRepository, UserResponse]):
    """Business logic for the user domain."""

    # ──────── soft-delete-aware read ────────

    async def list_alive(self) -> list[UserResponse]:
        """Return only rows where ``deleted_at IS NULL``.

        ``BaseRepository._apply_filters`` skips ``None`` values by design
        (a missing filter ≠ ``WHERE col IS NULL``), so an ``IS NULL`` clause
        must be issued as a raw SQLAlchemy query bound to the same session.
        """
        from sqlalchemy import select

        result = await self.repository.session.execute(
            select(UserModel).where(UserModel.deleted_at.is_(None))
        )
        instances = result.scalars().all()
        return [self.repository.map_to_response(i) for i in instances]

    # ──────── audit-stamped update ────────

    async def update(
        self,
        user_id: UUID,
        data: UserUpdateSchema,
        *,
        actor_id: UUID,
    ) -> UserResponse:
        """Apply a partial update and stamp ``updated_by`` with the actor."""
        instance = await self.repository.get_by_id(user_id)
        instance.update_from_dict(data.model_dump(exclude_unset=True))
        instance.stamp_updated_by(actor_id)
        updated = await self.repository.update(instance)
        return self.repository.map_to_response(updated)
```

Os dois métodos destacados sob os comentários divisores são o único código específico de soft-delete e auditoria que o consumidor escreve — as colunas e helpers (`mark_deleted` / `mark_restored` / `stamp_updated_by`) vêm dos mixins.

Use os helpers do mixin (`mark_deleted` / `mark_restored`) quando quiser a semântica de `deleted_at`; use `BaseRepository.soft_delete(id)` quando a flag `is_active` existente já basta.


## Paginação por cursor


A paginação por cursor escala melhor que a por offset em tabelas grandes (sem `COUNT(*)`, estável sob inserts concorrentes) ao custo de perder o acesso aleatório. O SDK fornece `CursorPaginationFilterSchema`, `CursorPaginationSchema[T]` e os helpers opacos `encode_cursor` / `decode_cursor`.

```python
# src/schemas/user.py
from tempest_fastapi_sdk import CursorPaginationFilterSchema, CursorPaginationSchema

from src.schemas.user import UserResponse


class UserCursorFilter(CursorPaginationFilterSchema):
    name: str | None = None  # ILIKE %value% via repository convention


UserCursorPage = CursorPaginationSchema[UserResponse]
```

Helper de repository (cursor sobre `created_at` + desempate por `id`):

```python
# src/db/repositories/user.py
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import asc, desc, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository, decode_cursor, encode_cursor

from src.db.models.user import UserModel
from src.schemas.user import UserResponse


class UserRepository(BaseRepository[UserModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, model=UserModel)

    async def cursor_page(
        self,
        *,
        cursor: str | None,
        limit: int,
        ascending: bool,
        filters: dict[str, Any] | None = None,
    ) -> UserCursorPage:
        query = select(UserModel)
        if filters:
            query = self._apply_filters(query, filters)

        order = asc if ascending else desc
        query = query.order_by(order(UserModel.created_at), order(UserModel.id))

        if cursor is not None:
            state = decode_cursor(cursor)
            # The cursor was encoded as `created_at.isoformat()` — decode back
            # to a datetime so the tuple comparison stays type-consistent on
            # Postgres (which rejects str-vs-timestamp comparisons).
            cursor_ts = datetime.fromisoformat(state["value"])
            cursor_id = UUID(state["id"])
            # SQLAlchemy needs `tuple_()` to express (col_a, col_b) > (val_a, val_b)
            # — bare Python tuples on mapped columns raise at composition time.
            cmp = tuple_(UserModel.created_at, UserModel.id) > tuple_(
                cursor_ts, cursor_id
            )
            query = query.where(cmp if ascending else ~cmp)

        query = query.limit(limit + 1)  # peek one ahead to set has_more
        result = await self.session.execute(query)
        rows = list(result.unique().scalars().all())
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = (
            encode_cursor(
                {"id": str(rows[-1].id), "value": rows[-1].created_at.isoformat()},
            )
            if has_more and rows
            else None
        )
        return UserCursorPage(
            items=[self.map_to_response(r) for r in rows],
            next_cursor=next_cursor,
            has_more=has_more,
            limit=limit,
        )
```

Router:

```python
@router.get("/", response_model=UserCursorPage)
async def list_users(
    f: UserCursorFilter = Depends(),
    controller: UserController = Depends(get_user_controller),
) -> UserCursorPage:
    return await controller.service.repository.cursor_page(
        cursor=f.cursor,
        limit=f.limit,
        ascending=f.ascending,
        filters=f.get_conditions(),
    )
```

O cursor é JSON opaco em base64 url-safe — os clientes nunca o inspecionam; eles devolvem o valor literalmente até que `next_cursor` vire `null`.


## Migrações Alembic


Fluxo completo: bootstrap → primeira migração → aplicar → gate de CI.

#### Bootstrap uma vez por projeto

```python
# scripts/alembic_init.py
from tempest_fastapi_sdk import AlembicHelper

from src.core.settings import settings

helper = AlembicHelper(config_path="alembic.ini", db_url=settings.DATABASE_URL)
helper.init(
    directory="alembic",
    metadata_module="src.db.models",        # exposes BaseModel
    metadata_attr="BaseModel",
    db_url=settings.DATABASE_URL,
)
```

Rode uma vez: `uv run python scripts/alembic_init.py`.

Isso cria:

```text
alembic.ini                 # config curada pelo SDK (timezone UTC, template de arquivo com prefixo de data)
alembic/
├── env.py                  # template do SDK (já conecta target_metadata, compare_type, modo batch)
├── script.py.mako
└── versions/
```

#### Escreva as migrações

```python
# scripts/make_migration.py
import sys

from tempest_fastapi_sdk import AlembicHelper

from src.core.settings import settings

helper = AlembicHelper("alembic.ini", db_url=settings.DATABASE_URL)
helper.revision(
    message=sys.argv[1],
    autogenerate=True,
)
```

```bash
uv run python scripts/make_migration.py "add users table"
```

O arquivo gerado cai em `alembic/versions/2026_05_16_1432-ae12cd34_add_users_table.py` — o prefixo de data faz os arquivos ordenarem cronologicamente e torna os conflitos de merge óbvios.

!!! check "Migrações já saem lint-clean"
    O `alembic.ini` que o `init()` escreve inclui um bloco
    `[post_write_hooks]` que roda `ruff check --fix` e depois
    `ruff format` em cada revisão recém-gerada. Sem isso, os arquivos que
    o Alembic emite falham no `tempest lint` com `W291` (espaço em branco
    no fim da linha `Revises: ` quando `down_revision` é `None`) e `E501`
    (linhas `sa.Column(...)` longas demais). Os hooks usam a config de
    `ruff` do **seu** projeto, então toda regra autofixável (`I`, `UP`,
    `E501`, …) é corrigida na hora da geração. Requer `ruff` no `PATH` —
    já é dependência de dev em todo scaffold `tempest new`.

#### Aplique no startup

```python
# src/api/app.py — estenda o lifespan
import asyncio

from tempest_fastapi_sdk import AlembicHelper


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Run pending migrations before serving traffic.
    helper = AlembicHelper("alembic.ini", db_url=settings.DATABASE_URL)
    await asyncio.to_thread(helper.upgrade)

    await db.connect()
    yield
    await db.disconnect()
```

#### Gate de CI — o schema deve casar com os modelos

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

---
