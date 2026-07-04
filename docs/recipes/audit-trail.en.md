# Audit trail

`AuditMixin` records **who** last touched a row (`created_by` /
`updated_by`) and `BaseModel` records **when** (`created_at` /
`updated_at`). Neither keeps the **history** of changes. The audit trail
adds an append-only log: one row per create / update / delete, with the
actor, the action and a before/after diff of the changed columns.

The audit row is written in the **same transaction** as the change
(reusing the outbox machinery), so an audit entry can never reference a
change that was rolled back.

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
ip, reason).

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
product = await repo.add_audited(ProductModel(name="Widget"), actor=str(user.id))
# writes the product + a CREATE entry with {"after": {...}}
```

### Update — snapshot before mutating

`update_audited` needs the **previous** state to compute the diff. Take
the snapshot with `repo.snapshot(...)` before mutating the instance:

```python
from uuid import UUID


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
await repo.delete_audited(product, actor=str(user.id))
# deletes the row + writes a DELETE entry with {"before": {...}}
```

!!! warning "Same transaction"
    All three variants commit the business row and the audit row
    **together**. If the audit write fails, the change is rolled back —
    never half-written. Repositories without `audit_model` raise
    `RuntimeError` when the audited methods are called.

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
