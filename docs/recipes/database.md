# Database

Everything that touches PostgreSQL/SQLite via SQLAlchemy 2.0 async — base model, async repository, mixins, migrations, cursor pagination.

## Audit & soft-delete mixins


`SoftDeleteMixin` adds a `deleted_at` timestamp column with `mark_deleted()` / `mark_restored()` / `is_deleted` helpers. `AuditMixin` adds `created_by` / `updated_by` UUID columns with `stamp_created_by(user_id)` / `stamp_updated_by(user_id)` helpers. Mix them in alongside `BaseModel`:

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

Filtering is the caller's responsibility — the mixin doesn't install a global filter. Hide soft-deleted rows from list endpoints by passing `deleted_at=None` (or filtering in your repository subclass). Stamping audit columns belongs to the service layer where the current user is in scope. Both patterns live inside the service:

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
        """Return only rows where ``deleted_at IS NULL``."""
        instances = await self.repository.list(filters={"deleted_at": None})
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

The two highlighted methods under the divider comments are the only soft-delete- and audit-specific code the consumer writes — the columns and helpers (`mark_deleted` / `mark_restored` / `stamp_updated_by`) come from the mixins.

Use the mixin's helpers (`mark_deleted` / `mark_restored`) when you want the `deleted_at` semantics; use `BaseRepository.soft_delete(id)` when the existing `is_active` flag is enough.


## Cursor pagination


Cursor pagination scales better than offset pagination on big tables (no `COUNT(*)`, stable under concurrent inserts) at the cost of losing random-access. The SDK provides `CursorPaginationFilterSchema`, `CursorPaginationSchema[T]` and the opaque `encode_cursor` / `decode_cursor` helpers.

```python
# src/schemas/user.py
from tempest_fastapi_sdk import CursorPaginationFilterSchema, CursorPaginationSchema

from src.schemas.user import UserResponse


class UserCursorFilter(CursorPaginationFilterSchema):
    name: str | None = None  # ILIKE %value% via repository convention


UserCursorPage = CursorPaginationSchema[UserResponse]
```

Repository helper (cursor over `created_at` + `id` tie-break):

```python
# src/db/repositories/user.py
from sqlalchemy import asc, desc

from tempest_fastapi_sdk import BaseRepository, decode_cursor, encode_cursor

from src.db.models.user import UserModel
from src.schemas.user import UserResponse


class UserRepository(BaseRepository[UserModel]):
    model = UserModel

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
            cmp = (UserModel.created_at, UserModel.id) > (state["value"], state["id"])
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

The cursor is opaque base64-url-safe JSON — clients never inspect it; they pass back the value verbatim until `next_cursor` becomes `null`.


## Alembic migrations


Full workflow: bootstrap → first migration → apply → CI gate.

#### Bootstrap once per project

```python
# scripts/alembic_init.py
from tempest_fastapi_sdk import AlembicHelper

from src.core.settings import settings

helper = AlembicHelper(config_path="alembic.ini", db_url=settings.DB_URL)
helper.init(
    directory="alembic",
    metadata_module="app.db",        # exposes BaseModel
    metadata_attr="BaseModel",
    db_url=settings.DB_URL,
)
```

Run once: `uv run python scripts/alembic_init.py`.

This creates:

```text
alembic.ini                 # SDK-curated config (UTC timezone, date-prefixed file template)
alembic/
├── env.py                  # SDK template (already wires target_metadata, compare_type, batch mode)
├── script.py.mako
└── versions/
```

#### Author migrations

```python
# scripts/make_migration.py
import sys

from tempest_fastapi_sdk import AlembicHelper

from src.core.settings import settings

helper = AlembicHelper("alembic.ini", db_url=settings.DB_URL)
helper.revision(
    message=sys.argv[1],
    autogenerate=True,
)
```

```bash
uv run python scripts/make_migration.py "add users table"
```

Generated file lands at `alembic/versions/2026_05_16_1432-ae12cd34_add_users_table.py` — the date prefix means files sort chronologically and merge conflicts are obvious.

#### Apply on startup

```python
# src/api/app.py — extend lifespan
import asyncio

from tempest_fastapi_sdk import AlembicHelper


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Run pending migrations before serving traffic.
    helper = AlembicHelper("alembic.ini", db_url=settings.DB_URL)
    await asyncio.to_thread(helper.upgrade)

    await db.connect()
    yield
    await db.disconnect()
```

#### CI gate — schema must match models

```python
# scripts/check_migrations.py
import sys

from tempest_fastapi_sdk import AlembicHelper

from src.core.settings import settings

helper = AlembicHelper("alembic.ini", db_url=settings.DB_URL)
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

