# Audit history

Use to show who changed what on the detail page, recent activity on the dashboard and undo from the history.

Part of the [Admin site](../admin.md) recipe.

The panel already stamps `created_by` / `updated_by`. To see **what**
changed (not just who/when), pass an `audit_model` — the same
`BaseAuditLogModel` table the `BaseRepository` already writes — and the
detail view gains a per-row **timeline**.

First, the audit table and a repository that feeds it:

```python
# src/db/models/audit.py
from tempest_fastapi_sdk import BaseAuditLogModel


class AuditLog(BaseAuditLogModel):
    __tablename__ = "audit_log"
```

```python
# write the trail on writes (create/update/delete)

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.db.audit import snapshot_model

from src.db.models import AuditLog, OrderModel, UserModel

current_user = UserModel(email="ana@example.com")
# In a service the session comes from `db.get_session_context()`; here, SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))


repo = BaseRepository(session, model=OrderModel, audit_model=AuditLog)


async def main() -> None:
    """Run this example."""
    order = await repo.add_audited(OrderModel(...), actor=str(current_user.id))

    before = snapshot_model(order)
    order.status = "shipped"
    await repo.update_audited(order, before, actor=str(current_user.id))


asyncio.run(main())
```

Then wire the same `audit_model` into the admin:

```python
# src/admin/site.py

from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import AuditLog, OrderModel


site.register(AdminModel(model=OrderModel, audit_model=AuditLog))
```

Each order's detail now shows a **History** block: one entry per change
(`create` / `update` / `delete`, color-coded), actor and date, and a
field-by-field diff (before → after). The 50 most recent entries, newest
first.

!!! info "How the match works"
    The viewer looks up audit rows where `entity` == the model name
    (e.g. `"OrderModel"`) and `entity_id` == the record id — exactly what
    `add_audited` / `update_audited` / `delete_audited` write. Without
    `audit_model`, the detail view is unchanged (only the
    `created_by`/`updated_by` stamps).

## Recent activity on the dashboard (`recent_activity=`)

The timeline above is per record. To see what changed across the panel
without opening record after record, turn the dashboard feed on in the
`AdminSite`:

```python
from tempest_fastapi_sdk import AdminModel, AdminSite

from src.db.models import AuditLog, OrderModel, TicketModel

site = AdminSite(title="Shop", recent_activity=20)
site.register(AdminModel(model=OrderModel, audit_model=AuditLog))
site.register(AdminModel(model=TicketModel, audit_model=AuditLog))
```

The dashboard gains a **Recent activity** block: the last 20 entries of
every model that declares `audit_model`, merged newest first, each with
the action, the model, the record's label (a link to its detail), the date
and the author.

The feed follows the same rules as the rest of the panel:

- a model the principal has no `VIEW` on in the `access_policy` is left out;
- an entry for a row outside the model's `row_scope` is left out — its
  label too. On a scoped model, an entry for a row that no longer exists
  (a `delete`) goes as well, since nothing proves it was reachable; with no
  scope it shows without a link;
- the author follows the detail's audit panel rule: with a `row_scope` on
  the user model, the name shows only for users the principal reaches;
  otherwise the id.

!!! info "Off by default — the cost"
    `recent_activity=None` is the default because the feed costs queries on
    **every** dashboard load: one per audit table, one per audited model (to
    keep only the rows in scope and read their labels) and one author lookup
    per distinct author. To make up for the entries the scope hides, the
    panel reads up to `10 × recent_activity` entries per table; when the
    scope hides more than that among the newest, the feed shows fewer than
    asked.
## Undo from the history (Restore)

Every `update` entry in the timeline gets a **Restore** link. It writes
nothing: it opens the row's edit form with the values from **before** that
change already filled in — every other field keeps today's value — and a
note naming the fields that came from the history. The operator reviews
and saves through the usual path, so the `EDIT` permission, form
validation, `validate_edit`, the foreign-key check, `row_scope` and the
optimistic lock all apply (the form carries the row's **current** version:
if another save landed after it was opened, the answer is `409`). The new
timeline entry comes from wherever your other edits are audited — the
panel does not write audit rows on its own.

The link shows only to principals with `EDIT` on the model, and only on an
`update` entry with at least one field that can go back. These are left
out and keep their current value:

- upload fields (`upload_fields`) and password fields (`password_fields`);
- hidden fields — `exclude_fields` and the model's secrets — and any value
  the audit stored as `[redacted]`;
- fields outside the edit form (`readonly_fields`, `readonly_fields_for`).

An old value that is invalid today — an option dropped from `choices`, for
example — is filled in (the `<select>` gains the option, so the browser
does not silently post the first one instead) and becomes a field error on
save, with nothing written.
