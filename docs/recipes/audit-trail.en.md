# Audit trail

`AuditMixin` records **who** last touched a row (`created_by` /
`updated_by`) and `BaseModel` records **when** (`created_at` /
`updated_at`). Neither keeps the **history** of changes. The audit trail
adds an append-only log: one row per create / update / delete, with the
actor, the action and a before/after diff of the changed columns.

The audit row is written in the **same transaction** as the change —
`add_audited` / `update_audited` add the audit row and the business row
and commit them together, the same pattern the outbox uses — so an audit
entry can never reference a change that was rolled back.

## The audit table

Subclass `BaseAuditLogModel` and pick a `__tablename__` (`audit_log` by
convention), like `BaseOutboxModel`:

```python
from tempest_fastapi_sdk import BaseAuditLogModel


class AuditLogModel(BaseAuditLogModel):
    """Append-only per-entity mutation log."""

    __tablename__ = "audit_log"
```

It inherits the four canonical columns (`id`, `is_active`, `created_at`,
`updated_at`) plus: `entity` (model name), `entity_id` (row id, as
text), `action` (`AuditAction`), `actor` (who did it, or `None`),
`changes` (the JSON diff) and `context` (optional metadata — request id,
reason). Domain event, IP, user agent and an FK author get their own
columns in [Event, request origin and author](#event-request-origin-and-author).

## Wiring it into the repository

Pass `audit_model=` to the repository and use the audited variants. They
write the business row **and** the audit row together:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository

from src.db.models import AuditLogModel, ProductModel


class ProductRepository(BaseRepository[ProductModel]):
    """Product repository with an audit trail."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository.

        Args:
            session (AsyncSession): The async database session.
        """
        super().__init__(session, model=ProductModel, audit_model=AuditLogModel)
```

### Create

```python
import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.db.models import ProductModel
from src.db.repositories import ProductRepository

# In a service the session comes from `db.get_session_context()`; here, SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

repo = ProductRepository(session)

# The actor is the id of whoever is already authenticated (`current_user.id`).
actor_id = UUID("2b1d0c2e-7f3a-4c56-9d18-2f9a4c5b6d70")


async def main() -> None:
    """Run this example."""
    product = await repo.add_audited(ProductModel(name="Widget"), actor=str(actor_id))
    # writes the product + a CREATE entry with {"after": {...}}


asyncio.run(main())
```

### Update — snapshot before mutating

`update_audited` needs the **previous** state to compute the diff. Take
the snapshot with `repo.snapshot(...)` before mutating the instance:

```python
from uuid import UUID

from src.db.repositories import ProductRepository


async def rename_product(
    repo: ProductRepository, product_id: UUID, name: str, actor: str
) -> None:
    """Rename a product, recording the diff in the audit trail.

    Args:
        repo (ProductRepository): The product repository.
        product_id (UUID): The product id.
        name (str): The new name.
        actor (str): Who performed the change.

    Raises:
        NotFoundException: If the product does not exist.
    """
    product = await repo.get_by_id(product_id)
    before = repo.snapshot(product)                  # ← before mutating
    product.name = name
    await repo.update_audited(product, before, actor=actor)
    # writes an UPDATE entry with {"name": {"before": "...", "after": "..."}}
```

### Delete

```python
import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.db.repositories import ProductRepository

# In a service the session comes from `db.get_session_context()`; here, SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

repo = ProductRepository(session)

product_id = UUID("6f1c3d84-2a55-4d0b-9d7e-0c1a2b3c4d5e")
actor_id = UUID("2b1d0c2e-7f3a-4c56-9d18-2f9a4c5b6d70")


async def main() -> None:
    """Run this example."""
    product = await repo.get_by_id(product_id)
    await repo.delete_audited(product, actor=str(actor_id))
    # deletes the row + writes a DELETE entry with {"before": {...}}


asyncio.run(main())
```

!!! warning "Same transaction"
    All three variants write the business row and the audit row
    **together**. Called on their own, they commit both at the end; inside
    a `repo.transaction()` block (or on a repository built with
    `autocommit=False`) they only `flush`, and the commit belongs to the
    block — if the block aborts, both rows disappear together. Either way,
    if the audit write fails the change is rolled back — never
    half-written. See [Transactions](transactions.md). Repositories without
    `audit_model` raise `RuntimeError` when the audited methods are called.

## Event, request origin and author

With only `actor` and `context`, "everything user X did" is a JSON scan,
and the IP and user agent live under keys each service names its own way.
Three pieces move them out of `context`:

- **`AuditRequestMixin`** — `event` (indexed), `ip` and `user_agent`
  columns on the audit table.
- **`AuditRequestContext.from_request(request, trusted_ip_header=...)`** —
  reads the client IP (through `get_client_ip`) and the `User-Agent`.
- **`record_event(...)`** — writes a domain event that changes no row
  (`action="event"`).

Plus one extension point: declare `actor_id` with an FK on your subclass
and pass `actor_id=` — no `new_entry` override.

```python
import asyncio
from collections.abc import AsyncGenerator
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Request
from sqlalchemy import ForeignKey, String, delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    AuditRequestContext,
    AuditRequestMixin,
    BaseAuditLogModel,
    BaseModel,
    BaseRepository,
)


class UserModel(BaseModel):
    """User account."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), nullable=False)


class AuditLogModel(AuditRequestMixin, BaseAuditLogModel):
    """Audit log with event, origin and an author linked to the user."""

    __tablename__ = "audit_log"

    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class ConsentModel(BaseModel):
    """Consent given by a user."""

    __tablename__ = "consents"

    user_id: Mapped[UUID] = mapped_column(nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)


class ConsentRepository(BaseRepository[ConsentModel]):
    """Consent repository with an audit trail."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository.

        Args:
            session (AsyncSession): The async database session.
        """
        super().__init__(session, model=ConsentModel, audit_model=AuditLogModel)


db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
app = FastAPI()


async def get_session() -> AsyncGenerator[AsyncSession]:
    """Provide one session per request.

    Yields:
        AsyncSession: The open session.
    """
    async with db.get_session_context() as session:
        yield session


@app.post("/users/{user_id}/consents")
async def grant_consent(
    user_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Record the user's consent and their export request.

    Args:
        user_id (UUID): The consenting user.
        request (Request): The request the IP and user agent come from.
        session (AsyncSession): The database session.

    Returns:
        dict[str, str]: The id of the created consent.
    """
    origin = AuditRequestContext.from_request(request, trusted_ip_header="x-real-ip")
    repo = ConsentRepository(session)
    consent = await repo.add_audited(
        ConsentModel(user_id=user_id, purpose="marketing"),
        actor=str(user_id),
        actor_id=user_id,
        event="consent.granted",
        request_context=origin,
    )
    await repo.record_event(
        "export.requested",
        actor=str(user_id),
        actor_id=user_id,
        request_context=origin,
    )
    return {"id": str(consent.id)}


async def main() -> None:
    """Run this example."""
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = UserModel(email="ana@example.com")
        session.add(user)
        await session.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        await client.post(
            f"/users/{user.id}/consents",
            headers={"X-Real-IP": "203.0.113.7", "User-Agent": "app/2.1"},
        )

    async with db.get_session_context() as session:
        await session.execute(delete(UserModel))
        await session.commit()
        rows = (await session.execute(select(AuditLogModel))).scalars().all()
        for row in sorted(rows, key=lambda r: r.action):
            print(row.action, row.event, row.entity_id != "", row.ip, row.user_agent, row.actor_id)

    await db.disconnect()


asyncio.run(main())
```

Output:

```text
create consent.granted True 203.0.113.7 app/2.1 None
event export.requested False 203.0.113.7 app/2.1 None
```

Piece by piece:

- **`AuditRequestMixin`** goes **before** `BaseAuditLogModel` and adds the
  three columns. `event` is indexed: "every consent granted" is an index
  lookup, not a JSON parse.
- **`actor_id`** is yours: the FK points at **your** users table, and the
  SDK does not need to know which. `ON DELETE SET NULL` is what leaves
  `actor_id` as `None` in the output — the account was erased, the trail
  stayed, without the link to the data subject.
- **`from_request`** requires `trusted_ip_header`, with no default:
  trusting a proxy header is a fact of your deployment, and the SDK does
  not guess. Pass the single header your edge overwrites (`"x-real-ip"`),
  or `None` to use the connection peer. A value that is not an IP address
  becomes `None`, and a `User-Agent` over 512 characters is truncated — the
  header belongs to the client, and a huge one must not break the
  business write.
- **`record_event`** writes `action="event"`, with `entity` set to the
  repository's model and an empty `entity_id` (or the id of the
  `subject=` you pass), and `changes` set to `{}` when you send no payload.
- `request_context=` and `ip=` / `user_agent=` are alternatives: passing
  both raises `ValueError`.

!!! warning "A table that already exists"
    The columns come from a mixin, not from `BaseAuditLogModel`, on
    purpose: whoever already has `audit_log` created keeps working **with
    no migration**, calling with `actor` / `context` only. Adding
    `AuditRequestMixin` (or `actor_id`) to an existing table is a schema
    change — generate the Alembic migration before deploying.

!!! info "A value for a column the table lacks is refused"
    Passing `event=`, `ip=`, `user_agent=` or `actor_id=` to a table
    without that column raises `ValueError` before the commit, and the
    whole transaction is rolled back — the business row included:

    ```text
    AuditLogModel has no column for: event, ip. Mix AuditRequestMixin in for event/ip/user_agent, or declare an actor_id column for actor_id.
    ```

    Silently losing an audit fact would be worse than failing the call.
    For the same reason, `record_event` requires the mixin: the event name
    is what the row records.

## Standalone helpers

Outside the repository, `snapshot_model(instance)` and
`diff_snapshots(before, after)` are available, and
`BaseAuditLogModel.for_create / for_update / for_delete` build the entry
(without adding it to the session) when you want to control the write
yourself.

## Recap

- `BaseAuditLogModel` (subclass with `__tablename__`) + `AuditAction`.
- `repo = Repository(session, model=..., audit_model=AuditLogModel)`.
- `add_audited` / `update_audited(model, before)` / `delete_audited` —
  business + audit in the same tx.
- `repo.snapshot(model)` before mutating; `snapshot_model` /
  `diff_snapshots` for manual use.
- `AuditRequestMixin` (opt-in) + `event=` / `ip=` / `user_agent=` /
  `request_context=AuditRequestContext.from_request(...)`; `actor_id=` for
  the FK you declare; `record_event(...)` for an event with no mutation.
