# Offline-first sync (delta)

Mobile apps and PWAs work offline: they capture data with no network and sync
when the connection returns. The backend only has to answer one question —
*"what changed since the last time we talked?"* — and that includes **deleted
records**, otherwise they stay orphaned on the device forever.

This recipe wires that bidirectional flow (push + pull) with
`BaseRepository.changes_since`, with no per-project cursor logic to rewrite.

## The problem

The client stores data locally (IndexedDB, SQLite) and keeps a **watermark**:
the instant of the last successful sync. On the next sync it wants to:

1. **Push** — send what it created/edited offline. Since it may resend (retry,
   flaky network), the write must be **idempotent**.
2. **Pull** — receive everything that changed on the server since the
   watermark, including deletions, to mirror locally.

## The model

Use the client-generated `id` as the primary key (idempotency for free) and mix
in `SoftDeleteMixin` so deletions become **tombstones** instead of vanishing
from the query.

```python
from uuid import UUID

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from tempest_fastapi_sdk import BaseModel, SoftDeleteMixin


class AnalysisModel(BaseModel, SoftDeleteMixin):
    """A syncable analysis, with the id coming from the device."""

    __tablename__ = "analyses"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    animal_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    notes: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
```

`BaseModel` already ships `id`, `created_at`, `updated_at` and `is_active`;
`SoftDeleteMixin` adds `deleted_at` + `is_deleted` + `mark_deleted()`.

## The repository

`changes_since` is the only new method you need. Create a thin repository to map
row → schema:

```python
from sqlalchemy.ext.asyncio import AsyncSession
from tempest_fastapi_sdk import BaseRepository


class AnalysisRepository(BaseRepository[AnalysisModel]):
    """Data access for syncable analyses."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, model=AnalysisModel)
```

## Idempotent push

The `id` belongs to the client, so "upsert by id" never duplicates on retry:

```python
from uuid import UUID


async def upsert_analysis(
    repo: AnalysisRepository,
    *,
    user_id: UUID,
    analysis_id: UUID,
    animal_id: str,
    notes: str,
) -> AnalysisModel:
    """Create or update an analysis, idempotent by client id.

    Args:
        repo (AnalysisRepository): The analyses repository.
        user_id (UUID): The record owner.
        analysis_id (UUID): The device-generated id (primary key).
        animal_id (str): The animal identifier (ear tag / herd id).
        notes (str): Free-text observations.

    Returns:
        AnalysisModel: The persisted row.
    """
    existing = await repo.get_or_none({"id": analysis_id, "user_id": user_id})
    if existing is not None:
        existing.animal_id = animal_id
        existing.notes = notes
        return await repo.update(existing)
    return await repo.add(
        AnalysisModel(
            id=analysis_id,
            user_id=user_id,
            animal_id=animal_id,
            notes=notes,
        )
    )
```

## Pull (the delta)

`changes_since(since)` returns only what changed after the watermark, ascending
by `updated_at`, cursor-paginated, **including tombstones**:

```python
from datetime import datetime
from uuid import UUID


async def pull_changes(
    repo: AnalysisRepository,
    *,
    user_id: UUID,
    since: datetime | None,
    cursor: str | None = None,
) -> dict[str, object]:
    """Return the analyses that changed since the client watermark.

    Args:
        repo (AnalysisRepository): The analyses repository.
        user_id (UUID): The owner scope — never sync without it.
        since (datetime | None): Last sync watermark. None does a full
            sync (first time).
        cursor (str | None): Cursor from the previous page; None takes
            the first page.

    Returns:
        dict[str, object]: The cursor envelope plus `server_time`.
    """
    return await repo.changes_since(
        since,
        filters={"user_id": user_id},
        cursor=cursor,
        limit=100,
    )
```

!!! danger "Always pass the owner scope"
    `changes_since` does **not** scope by user on its own. Always pass
    `filters={"user_id": user_id}` (or the tenant scope), otherwise a client
    pulls the whole world's delta.

## The endpoint

`SyncFilterSchema` and `SyncPaginationSchema` match the arguments and return of
`changes_since` exactly:

```python
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from tempest_fastapi_sdk import SyncFilterSchema, SyncPaginationSchema

router = APIRouter(prefix="/api/analyses", tags=["sync"])


@router.get("/changes")
async def get_changes(
    filters: Annotated[SyncFilterSchema, Query()],
    repo: AnalysisRepository = Depends(get_analysis_repository),
    user_id: UUID = Depends(get_current_user_id),
) -> SyncPaginationSchema[AnalysisResponseSchema]:
    """Pull endpoint: the delta since the client watermark."""
    page = await repo.changes_since(
        filters.since,
        filters={"user_id": user_id},
        cursor=filters.cursor,
        limit=filters.limit,
        include_deleted=filters.include_deleted,
    )
    return SyncPaginationSchema[AnalysisResponseSchema](
        items=[AnalysisResponseSchema.model_validate(r) for r in page["items"]],
        next_cursor=page["next_cursor"],
        has_more=page["has_more"],
        limit=page["limit"],
        server_time=page["server_time"],
    )
```

## The watermark protocol

This is the part that usually breaks. Follow it exactly:

1. **First sync:** call with `since=None`. Drain every page via `next_cursor`
   until `has_more` is `False`.
2. **Store the response's `server_time`** as the next `since` — do **not** use
   the max `updated_at` of the items, nor the device clock.
3. **Next sync:** send that `server_time` as `since`. The filter is
   `updated_at > since` (strict).

!!! tip "Why `server_time` and not the client clock"
    `server_time` is captured on the server **before** the query runs. Because
    it is a marker on the database's own clock, any row written afterwards has a
    larger `updated_at` and surfaces on the next pull — immune to device clock
    skew.

!!! warning "Tombstones are not optional"
    Keep `include_deleted=True` (the default). A pull that hides deleted rows
    leaves them stranded on the device forever, because the client never learns
    they are gone.

## Comparison filters

`changes_since` is sugar over a more general feature: the `<column>__<op>`
suffix on any `filters`. Available in `list`, `paginate`, `cursor_paginate`,
`count` and so on.

```python
# updated_at > watermark (timestamp precision)
await repo.list(filters={"updated_at__gt": watermark})

# range: 1 <= value <= 10
await repo.list(filters={"value__gte": 1, "value__lte": 10})

# not equal
await repo.list(filters={"status__ne": "archived"})
```

Operators: `gt`, `gte`, `lt`, `lte`, `ne`. A `None` value skips the condition,
like any other filter.

!!! note "Different from `start_in` / `end_in`"
    `start_in` / `end_in` filter by **whole day** over `created_at`. The `__gt`
    operators are by **timestamp**, on any column — which is what the sync
    watermark needs.

## Recap

- Use the **client id as the PK** → push becomes an idempotent upsert.
- Mix in **`SoftDeleteMixin`** → deletions become tombstones the pull delivers.
- **`changes_since(since, filters={"user_id": ...})`** is the whole pull: delta
  by `updated_at`, stable order, cursor and tombstones.
- Persist the response's **`server_time`** as the next `since` — not the device
  clock.
- Under the hood, the **`__gt/gte/lt/lte/ne`** operators work on any `filters`.
