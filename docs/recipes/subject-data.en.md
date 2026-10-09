# Data subject export and erasure (LGPD)

Brazil's LGPD (art. 18) — like GDPR arts. 15 and 17 — gives the data subject
the right to ask for a **copy** of everything you store about them and for its
**erasure**. Both requests share the same question underneath: *which rows
and which files belong to this person?*

Answering it by hand — a list of tables in a service, a `remove_object` loop
in a script — works on the day it is written. Next month someone adds a
`favorites` table with a `user_id`, forgets the list, and from then on the
export leaves data out and the erasure fails (or leaves rows behind). Nobody
notices until the real request arrives.

The `tempest_fastapi_sdk.privacy` module takes the list out of your hands:

- **`SubjectGraph`** reads the SQLAlchemy `MetaData` and derives the set of
  tables a `DELETE` of the subject's root row reaches through `ON DELETE
  CASCADE`. That set is what it exports, and any foreign key pointing into it
  **without** a cascade becomes a violation a test catches.
- **`SubjectObjectStorage`** keeps every file of the subject under its own
  prefix in MinIO/S3 and deletes the whole prefix in batch.

## Install

`SubjectGraph` needs only SQLAlchemy, which ships with the base package.
`SubjectObjectStorage` uses `AsyncMinIOClient`, which needs the `[minio]`
extra:

```bash
uv add "tempest-fastapi-sdk[minio]"
```

Importing `tempest_fastapi_sdk.privacy` does not require the extra; building
the client does.

## A database designed to erase

The cheapest erasure is the one the database does by itself: every table
holding subject data has a foreign key with `ondelete="CASCADE"` toward the
root row (directly or through another table), so one `DELETE` on the root
removes the rest. That is the design `SubjectGraph` reads back.

```python
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class UserModel(BaseModel):
    """Data subject: the root row of everything that is theirs."""

    email: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(120))


class AddressModel(BaseModel):
    """The subject's address."""

    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    street: Mapped[str] = mapped_column(String(120))


class OrderModel(BaseModel):
    """The subject's order."""

    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class OrderItemModel(BaseModel):
    """Order item: belongs to the subject through the order."""

    order_id: Mapped[UUID] = mapped_column(ForeignKey("order.id", ondelete="CASCADE"))
    sku: Mapped[str] = mapped_column(String(20))


class InvoiceModel(BaseModel):
    """Invoice: outlives the erasure, without the link to the subject."""

    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    number: Mapped[str] = mapped_column(String(20))
```

`OrderItemModel` does not point at `user`: it belongs to the subject
**through** the order. The graph follows the cascade at any depth.

`InvoiceModel` is the case where the law asks for the opposite: the invoice
must be kept even after erasure. `SET NULL` keeps the row and cuts the link.

## The guard: break CI, not the request

Build the graph pointing at the root and run the ready-made guard in a test:

```python
from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.privacy import SubjectGraph
from tempest_fastapi_sdk.testing import assert_subject_graph_valid


def test_subject_graph_supports_erasure() -> None:
    """Every FK to subject data cascades, or is justified."""
    assert_subject_graph_valid(SubjectGraph(BaseModel.metadata, root="user"))
```

With the models above it fails — and says where:

```text
AssertionError: subject graph rooted at 'user' has 1 erasure violation(s):
  - invoice.user_id -> user: ON DELETE SET NULL (expected CASCADE, or SET NULL listed in retained)
```

The invoice's `SET NULL` is deliberate, so it goes into `retained`, with the
reason next to it — whoever reads the code a year from now knows why that row
survives:

```python
from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.privacy import SubjectGraph
from tempest_fastapi_sdk.testing import assert_subject_graph_valid

graph = SubjectGraph(
    BaseModel.metadata,
    root="user",
    retained={"invoice.user_id": "5-year tax retention"},
)


def test_subject_graph_supports_erasure() -> None:
    """Every FK to subject data cascades, or is justified."""
    assert_subject_graph_valid(graph)
```

What counts as a violation (`graph.violations()` returns one line per problem,
sorted):

| Situation | Why it breaks erasure |
| --- | --- |
| FK to a subject table with no `ondelete` (`NO ACTION`) or `RESTRICT` | the root `DELETE` fails |
| FK with `SET NULL` not listed in `retained` | the row survives without anyone deciding so |
| `retained` entry whose FK is not `SET NULL` | the justification does not describe the schema |
| `retained` entry on a `NOT NULL` column | the `SET NULL` fails at `DELETE` time |
| `retained` entry matching no FK | stale allowlist, from a table that is gone |

!!! tip "A new table joins by itself"
    A table added later with a cascading FK toward any subject table joins
    `graph.tables()` — and therefore the export — with no configuration. One
    added **without** a cascade breaks the guard in the PR that added it.

## Export and erase

The whole example, running against in-memory SQLite:

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
    """Data subject: the root row of everything that is theirs."""

    email: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(120))


class AddressModel(BaseModel):
    """The subject's address."""

    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    street: Mapped[str] = mapped_column(String(120))


class OrderModel(BaseModel):
    """The subject's order."""

    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class OrderItemModel(BaseModel):
    """Order item: belongs to the subject through the order."""

    order_id: Mapped[UUID] = mapped_column(ForeignKey("order.id", ondelete="CASCADE"))
    sku: Mapped[str] = mapped_column(String(20))


class InvoiceModel(BaseModel):
    """Invoice: outlives the erasure, without the link to the subject."""

    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    number: Mapped[str] = mapped_column(String(20))


graph = SubjectGraph(
    BaseModel.metadata,
    root="user",
    retained={"invoice.user_id": "5-year tax retention"},
)


async def main() -> None:
    """Create the database, export one subject, erase and check the erasure."""
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

Output (UUIDs and timestamps change on every run):

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

Piece by piece:

- **`graph.tables()`** — the root first, then each table in the order the
  cascade reaches it. `invoice` is left out: `SET NULL` does not delete the
  row.
- **`await graph.export(session, user_id)`** — a `dict` with **every** table of
  the graph, each mapped to the subject's rows (an empty list when there are
  none). Values are ready for `json.dumps`: dates and times as ISO 8601,
  `UUID` and `Decimal` as strings, `bytes` as base64, enums by value.
- **`password_hash` is missing.** A column whose name contains `hash`,
  `secret` or `password` is left out of the export (`DEFAULT_SECRET_MARKERS`).
  To mark a column whose name says nothing, use
  `mapped_column(..., info={"secret": True})` or
  `SubjectGraph(..., secret_columns={"user": ["cpf_token"]})`.
- **The `DELETE` is yours.** Erasing means deleting the root row; the
  database deletes the rest, through the cascades the guard guaranteed.
- **`await graph.count(session, user_id)`** — how many of the subject's rows
  remain per table. After the erasure, all `0`: that is the proof you keep in
  the request's record.

!!! warning "SQLite only cascades with `PRAGMA foreign_keys=ON`"
    Without `enable_sqlite_foreign_keys(engine)`, SQLite ignores
    `ondelete="CASCADE"`: the root `DELETE` succeeds and the child rows
    stay. The `count` after the erasure is what exposes it. The engines of
    `AsyncDatabaseManager` and of `tempest_fastapi_sdk.testing` already turn
    the pragma on.

## The subject's files

A deleted row whose file is still in the bucket is not erasure. The cheap way
to find "all of Ana's files" is for the **key** to say so:
`SubjectObjectStorage` writes everything under `<prefix>/<subject_id>/<name>`.

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
    """Write, list, sign and delete the files of one subject."""
    await client.ensure_bucket()
    key = await storage.put(42, "docs/rg.pdf", b"%PDF-1.7", content_type="application/pdf")
    print(key)
    print(await storage.names(42))
    print(await storage.presign(42, "docs/rg.pdf", expires=timedelta(minutes=10)))
    print(await storage.delete_all(42))


asyncio.run(main())
```

Output against a local MinIO (the URL signature changes on every run):

```text
users/42/docs/rg.pdf
['docs/rg.pdf']
http://localhost:9000/uploads/users/42/docs/rg.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256&...&X-Amz-Expires=600&...
1
```

- **`put` returns the full key** (`users/42/docs/rg.pdf`). The name may hold
  subfolders; it cannot start with `/` nor hold an empty, `.` or `..`
  segment — nothing that leaves the subject's prefix. The subject id cannot
  hold a `/`.
- **The prefix ends in `/`.** Subject `7` never matches the files of `77`.
- **`delete_all` lists the prefix and deletes in batch**, with
  `AsyncMinIOClient.remove_objects` (new in this version), which sends one
  `DeleteObjects` per 1000 keys instead of one `DELETE` per file. It returns
  how many objects it deleted; running it again returns `0`.
- If the store refuses some key, `delete_all` raises `SubjectErasureError`
  with the list (`errors`: key, S3 code and message). The other keys are
  already gone; running it again only retries what is left.

!!! info "Measured against a real MinIO"
    `tests/privacy/test_storage_live.py` (`make test-docker`) writes 1203
    objects for subject `7`, one for `77` and one for `8`, calls
    `delete_all(7)` and checks: 1203 deleted in **two** `DeleteObjects`
    requests (1000 + 203), nothing left under `7`'s prefix, `77` and `8`
    untouched.

## Putting both together in a service

Delete the files **before** the root row. If the store fails, the subject's
row still exists and the same request can be retried; the prefix depends only
on the id, so the retry finds exactly what is left.

```python
from typing import Any
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.privacy import SubjectGraph, SubjectObjectStorage


class SubjectDataService:
    """Serves data subject export and erasure."""

    def __init__(
        self,
        session: AsyncSession,
        graph: SubjectGraph,
        storage: SubjectObjectStorage,
    ) -> None:
        """Keep the dependencies.

        Args:
            session (AsyncSession): The request's session.
            graph (SubjectGraph): The subject graph.
            storage (SubjectObjectStorage): The subject's files.
        """
        self.session: AsyncSession = session
        self.graph: SubjectGraph = graph
        self.storage: SubjectObjectStorage = storage

    async def export(self, user_id: UUID) -> dict[str, Any]:
        """Build the copy of the subject's data.

        Args:
            user_id (UUID): The subject id.

        Returns:
            dict[str, Any]: Rows per table and the list of files.
        """
        return {
            "tables": await self.graph.export(self.session, user_id),
            "files": await self.storage.names(user_id),
        }

    async def erase(self, user_id: UUID) -> dict[str, int]:
        """Erase the subject's files and rows.

        Args:
            user_id (UUID): The subject id.

        Returns:
            dict[str, int]: Remaining rows per table; all ``0``.
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
    retained={"invoice.user_id": "5-year tax retention"},
)
```

`graph.condition(table, user_id)` is the `WHERE` clause selecting the
subject's rows in that table — the primary key on the root; on the others, a
nested `IN (SELECT ...)` along the cascade path. It works in `select`,
`update` and `delete`.

## What is left out

- **Links without a foreign key.** A column holding the subject id without an
  FK (the `actor` and the text `entity_id` of the
  [audit trail](audit-trail.md), a user id inside JSON) is not in the graph,
  the export or the cascade. Handle those separately.
- **Rows reached only through a cycle.** In a self-referencing table (comment
  replies), a row of **another** subject that only belongs to the graph by
  pointing at the subject's comment is not exported as theirs — but the
  cascade deletes it along. Decide whether that is what you want.
- **A root with a composite primary key** is rejected with `ValueError`.
- **Search, cache and backups.** Search indexes, Redis keys and database
  backups are outside the cascade's reach.

## Recap

- `SubjectGraph(metadata, root=...)` derives from the schema the set of tables
  the root `DELETE` reaches by cascade; a new cascading table joins by itself.
- `assert_subject_graph_valid(graph)` in a test breaks CI when an FK to
  subject data does not cascade; a deliberate `SET NULL` goes into `retained`
  with its reason.
- `await graph.export(session, id)` returns the subject's rows per table,
  JSON-ready and without secret columns.
- Erasing means deleting the root; `await graph.count(session, id)` proves
  nothing is left.
- `SubjectObjectStorage` keeps files under `<prefix>/<id>/` and `delete_all`
  deletes the prefix in batch, without touching another subject.
