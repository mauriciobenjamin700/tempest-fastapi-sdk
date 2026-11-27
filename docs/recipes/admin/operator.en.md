---
description: "Use to show dates in the operator time zone, block concurrent edits with optimistic locking and switch the admin UI to Portuguese."
---

# Timezone, concurrent edits and language

Use to show dates in the operator's timezone, stop two edits from overwriting each other and switch the interface to Portuguese.

Part of the [Admin site](../admin.md) recipe.

## Operator timezone (`display_timezone=`)

A `TIMESTAMP(timezone=True)` column stores an **instant**, and the admin
form renders `<input type="datetime-local">`, which carries **no offset
at all**. Without saying which zone the box is read in, the panel is a
raw column editor: the submission arrives with no `tzinfo`, and what it
means is then the driver's call — with asyncpg, the process `TZ`. In a
container running UTC an operator in Brazil types `20:00` and the row
keeps `20:00Z`, three hours ahead
([measured in the consumer](https://github.com/And-All/alofans-api/issues/114)).

Declare the zone and the three views start agreeing:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import EventModel


site.register(
    AdminModel(
        model=EventModel,
        list_display=[EventModel.name, EventModel.date],
        display_timezone="America/Sao_Paulo",
    ),
)
```

What changes, and nothing else:

- **List and detail** convert every `datetime` to the zone and print the
  offset with it (`2026-06-15 20:00 -03:00`; the full instant is in the
  tooltip), so the screen is never ambiguous.
- **The form** pre-fills the box with the local hour
  (`2026-06-15T20:00`) and writes `Timezone: America/Sao_Paulo — stored
  as UTC.` underneath, because `datetime-local` has nowhere to say it.
- **The submission** is read as wall-clock time in that zone and stored
  in UTC. A value that already carries an offset is honored, not
  overridden.
- **The date-range filter** (`list_filter` on a `datetime` column) reads
  the day in that zone: "from 20/11 to 20/11" runs from midnight of 20/11
  to midnight of 21/11 in Sao Paulo, end exclusive. A row stamped 22:00 on
  20/11 (01:00 UTC on 21/11) lands on the 20th, the day the list shows.
  A filtered export uses the same range. Without `display_timezone`, the
  day is UTC.

!!! warning "Converting only on write is worse than not converting"
    The half-fix passes every test about writing and still moves the
    row: the form shows the UTC hour, the operator saves without
    touching anything, and the displayed value is re-read as local
    time. Every save of an untouched form pushes the row three hours.
    That is why both ends change together, and why the test that pins
    the rule is a real `POST` of a form nobody edited.

!!! tip "Declare it once on the `AdminSite`"
    The zone belongs to the panel, not to one table:
    `AdminSite(display_timezone=...)` applies to every model registered
    after it, and an `AdminModel` that declares its own keeps it.

!!! info "The export stays in UTC"
    CSV and JSON feed machines. A timestamp that changes meaning between
    the screen and the file is worse than one that always means the same
    thing, so the conversion lives in the views a person reads.

!!! tip "The zone is validated where it is declared"
    `display_timezone="America/Sao_Paolo"` (with the typo) raises
    `ValueError` when the `AdminModel` is built, not in the request that
    renders the form. A timezone resolved late has cost us before: the
    cron's offset string died inside the scheduler loop, silently.

### Date filter shortcuts

Every date-range filter (`list_filter` on a `date` or `datetime` column)
gains, below its two boxes, the links **Today**, **7 days**, **30 days**,
**This month** and **Last month** (in Portuguese with
`AdminSite(locale="pt-BR")`):

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import OrderModel


site.register(
    AdminModel(
        model=OrderModel,
        list_filter=[OrderModel.created_at, OrderModel.status],
        search_fields=[OrderModel.code],
        display_timezone="America/Sao_Paulo",
    ),
)
```

Each link fills `filter_created_at_from` and `filter_created_at_to` with
the range's days, both inclusive: today; the last 7 and 30 days ending
today; the current month from the 1st to its last day; the whole previous
month. "Today" is the day in the model's `display_timezone` (UTC without
one) — the same day the filter reads. The rest of the query stays as the
operator left it (search, the other filters, the sort, the lens); only the
page goes back to the first. The shortcut matching the active range is
marked. They are links built on the server, with no JavaScript.

## Concurrent edits: optimistic lock

Two operators open the same record; the first saves, the second saves
over it without knowing. The edit form prevents that on its own, for
every model with an `updated_at` column — and every `BaseModel` has one:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import CustomerModel


site.register(AdminModel(model=CustomerModel))
```

The form carries, in a hidden field, the `updated_at` it read when it
opened. On save, before any column is assigned, the panel runs
`UPDATE ... SET updated_at = updated_at WHERE id = :id AND updated_at =
:read` in the **same transaction** as the `UPDATE` that writes the edit —
there is no `SELECT` first asking whether it changed. Zero rows affected
means another save (through the panel or any other path that goes through
the ORM) got there first: nothing is written and the form comes back with
`409`, the warning `Someone else saved this record after you opened it.
Nothing was saved: your values are below — review them and save again to
overwrite.` and the values the operator typed.

The returned form already carries the row's **current** version. Saving
again, after reviewing, overwrites knowingly.

!!! info "Where the lock does not apply"
    - A model without an `updated_at` column, or a `POST` without the
      hidden field (a form opened before the SDK upgrade), saves as before.
    - The editable inline formset and the bulk/custom actions take no
      lock: the inline saves each child as posted, and an action acts on
      the row it finds.

## Interface in Portuguese (`locale=`)

Every piece of text the panel writes itself — labels, buttons, empty
states, pagination, flash messages and the errors the router shows on the
form (`This field is required.`, the conflict warning, the scope one) —
comes from a catalog with English and Brazilian Portuguese. Pick it on the
site:

```python
from tempest_fastapi_sdk import AdminSite

site = AdminSite(
    title="Backoffice",
    locale="pt-BR",
    display_timezone="America/Sao_Paulo",
)
```

The default is `"en"`, with the same text as always, so an existing panel
does not change. A `locale` outside `ADMIN_LOCALES` raises `ValueError`
when the `AdminSite` is built. `<html lang>` follows the locale, and the
default `index_subtitle` becomes `Administração do site` (passed by hand,
it stays as you wrote it).

What is **not** translated is what comes from you: model and field names,
`verbose_name`, action labels, `title`. Messages another component writes
also come out as it writes them — the auth backend's `AdminAuthError`
(`Invalid credentials`), the `StorageBrowserService` errors and the
password policy message.

To write text of your own from the same catalog — a custom action's
result, for instance — use `admin_text`:

```python
from tempest_fastapi_sdk.admin import admin_plural, admin_text

title = admin_text("pt-BR", "list.new", model="Pedido")
count = admin_plural("pt-BR", "list.records", 3)
```

`admin_text(locale, key, **params)` formats the text with `str.format`;
`admin_plural(locale, key, n)` reads `key.one` when `n == 1` and
`key.other` otherwise. A key missing from the locale falls back to
English, and an unknown key returns the key itself — the
`tests/test_admin_i18n_guard.py` guard keeps both languages complete.
