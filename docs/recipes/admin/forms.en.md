---
description: "Use to tune the admin form: `choices`, uploads, FK autocomplete, inlines, many-to-many, widgets, password fields and `exclude_fields`."
---

# Form fields

Use to tune the form: choices, file upload, autocomplete FKs, inlines, many-to-many, widgets, passwords and hidden columns.

Part of the [Admin site](../admin.md) recipe.

## A choice field on a `String` column (`choices=`)

A `Mapped[MyEnum]` column already shows up in the form as a `<select>`. A
`String` column that stores an enum's value, or a vocabulary the panel should
restrict, shows up as free text, because the admin has no way to know the
values. That is the wallet's `kind`: the column is `String(64)` because the
application writes its own kinds ("SALE", "TIP") next to the SDK's.

`choices=` tells the admin which values to accept, without changing the
column:

```python
from tempest_fastapi_sdk import AdminModel, AdminSite
from tempest_fastapi_sdk.wallet.models import make_wallet_entry_model
from tempest_fastapi_sdk.wallet.schemas import WalletEntryKindEnum

WalletEntryModel = make_wallet_entry_model()

site = AdminSite(title="Carteira")
site.register(
    AdminModel(
        WalletEntryModel,
        choices={
            "kind": WalletEntryKindEnum,
            "reference_type": [("order", "Order"), ("payout", "Payout")],
        },
    )
)
```

- **Each entry** is an `Enum` class (each member's value, its humanized name
  as the label), a list of strings, or of `(value, label)` pairs.
- **The form becomes a `<select>`**: "Kind" shows Withdraw, Withdraw refund,
  Reversal and Opening balance; "Reference type", being optional, gets
  "— none —" first.
- **The server checks.** A POST with a value outside the list comes back
  `400` with "Choose one of the listed values for Kind." and saves nothing;
  the select is not the only barrier.
- **A name that is not a column** (a typo) raises `ValueError` at
  construction, and so does an empty list.
- **In the list filter**: a column that is also in `list_filter` gets the
  same `<select>`, with the same labels, instead of a free-text box.

!!! tip "No Activate / Deactivate"
    On a table where `is_active` means nothing (a financial record, say),
    `AdminModel(can_toggle_active=False)` drops both bulk actions from the
    list and refuses a hand-built `action=deactivate` with `400`.

!!! tip "Open vocabulary"
    If the application writes its own kinds into `kind`, join them with the
    SDK's in a list: `choices={"kind": [*WalletEntryKindEnum, "SALE", "TIP"]}`
    becomes the six options, each labelled with its own value.

## File / image upload field

A `String` column that stores a file's path/key can render as a **file
input** on the form. List the column in `upload_fields` and pass an
`upload_storage` (the SDK's existing backends — `LocalUploadStorage` /
`MinIOUploadStorage`). On submit the file is saved to storage and the
**returned key** is written to the column.

```python
from tempest_fastapi_sdk import AdminModel
from tempest_fastapi_sdk.utils import LocalUploadStorage

from src.admin import site
from src.db.models import DocumentModel


site.register(AdminModel(
    model=DocumentModel,
    upload_fields=[DocumentModel.attachment],   # String column holding the key
    upload_storage=LocalUploadStorage("media/"),  # or MinIOUploadStorage(...)
))
```

!!! info "Installation"
    The upload backends do not ship with `[admin]`. `LocalUploadStorage`
    needs the `[upload]` extra — `uv add "tempest-fastapi-sdk[upload]"`
    (pulls in `aiofiles`); `MinIOUploadStorage` needs `[minio]` —
    `uv add "tempest-fastapi-sdk[minio]"` (pulls in `minio`).

- The form becomes `multipart/form-data` automatically when `upload_fields` is set.
- **Create**: a file is required only when the column is `NOT NULL` with no default.
- **Edit**: no new file → keeps the current value (shows "Current: …"); a file → replaces it.
- The column stores the storage **key** (`<slug>/<field>/<uuid>.<ext>`); the record's page has a **Download** link for it.

!!! warning "`upload_fields` requires `upload_storage`"
    Registering `upload_fields` without `upload_storage` raises
    `ValueError` at `AdminModel` construction — without storage there's
    nowhere to write the file.

### One kind of file only (`upload_accept=`)

`upload_accept` says which types an upload field takes. The input gets
`accept=` (the browser's file picker already filters) and the server checks
before writing: the declared type must be listed **and**, when the list
holds any type `sniff_mime` recognises by its bytes (`SNIFFABLE_MIMETYPES`:
PDF, PNG, JPEG, GIF, BMP, WebP, HEIC), the file must start as a listed type.
A PNG renamed `.pdf` is refused with a field error, and nothing reaches the
storage.

!!! warning "The check is per list, not per type"
    Mixing a type with no signature and one with a signature —
    `["text/plain", "application/pdf"]` — refuses plain text: text has no
    magic bytes, so it never sniffs as a listed type. Measured by calling the
    router's check: `text/plain` with `hello world` is refused under that
    list and accepted under `["text/plain"]` alone.

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AdminModel, BaseModel
from tempest_fastapi_sdk.utils import LocalUploadStorage


class InvoiceModel(BaseModel):
    """An invoice with its PDF."""

    __tablename__ = "invoices"

    pdf_key: Mapped[str | None] = mapped_column(String(255), nullable=True)


invoices: AdminModel[InvoiceModel] = AdminModel(
    InvoiceModel,
    upload_fields=["pdf_key"],
    upload_storage=LocalUploadStorage("media/"),
    upload_accept={"pdf_key": ["application/pdf"]},
)
```

On the record's page, each filled upload field shows a **Download** link: a
storage that mints temporary URLs (`MinIOUploadStorage`) redirects to one;
`LocalUploadStorage` serves the file as an attachment, with
`X-Content-Type-Options: nosniff`.

### A rule that depends on the row (`validate_edit=`)

`validate_edit(row, form)` runs on an edit submission, before any upload is
written. Returning `{"field": "message"}` refuses the edit with those errors
— the place for a rule like "a finished record's receipt does not change".
The preset below uses exactly that.

### A field locked per row (`readonly_fields_for=`)

`readonly_fields` locks the same field on every row. When a field may only
change while the row is in some state — the reason for a refusal, editable
until the record is final —, pass `readonly_fields_for(row)`: it takes the
stored row and returns the fields to lock **on it**, on top of
`readonly_fields`. The lock holds on both sides, the same way the static one
does: the field leaves that row's edit form, and a value posted for it by
hand is ignored on save (the answer is the usual `303`, and the stored value
does not change).

```python
from collections.abc import Sequence

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AdminModel, BaseModel


class TicketModel(BaseModel):
    __tablename__ = "tickets"

    status: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(String(128), nullable=True)


def note_locked_when_closed(row: TicketModel) -> Sequence[str]:
    return ["note"] if row.status == "closed" else []


tickets: AdminModel[TicketModel] = AdminModel(
    TicketModel,
    readonly_fields=["status"],
    readonly_fields_for=note_locked_when_closed,
)
```

!!! note "What it does not cover"
    Upload and password fields are handled apart by the form, so lock them
    with `validate_edit`. And the hook applies to the row's own edit form,
    not to the parent's inline formset.

### Preset: manual payout (`manual_payout_admin`)

`manual_payout_admin(ManualPayoutModel, upload_storage=...)` returns the
`AdminModel` of the table of withdrawals paid by hand
(`make_manual_payout_model`): read-only amounts, PDF-only receipt, download
link and the **Marcar como pago** ("mark as paid") action, which completes
only a `PENDING` row with a receipt.

With `ledger_factory=` (a function that takes the panel's session and
returns the `WalletLedger` over it), **Recusar e devolver** ("refuse and
refund") joins it: the administrator writes the reason in the row's
`rejection_reason` field and runs the action, which turns the row
`REJECTED` and gives the amount back to the wallet in a `WITHDRAW_REFUND`
line, in the same commit. A row without a reason stays `PENDING` and the
message lists its `correlation_id`; a row that already left `PENDING` does
not change, and the reason field leaves its edit form
(`readonly_fields_for`). Both actions call
`ManualPayoutService` and run the `hooks=` you pass — see
[Wallet » Completing or refusing a withdrawal by hand](../wallet.md#completing-or-refusing-a-withdrawal-by-hand-manualpayoutservice).

```python
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository, BaseUserModel
from tempest_fastapi_sdk.admin import AdminModel, manual_payout_admin
from tempest_fastapi_sdk.utils import LocalUploadStorage
from tempest_fastapi_sdk.wallet import (
    WalletBalanceMixin,
    WalletLedger,
    make_manual_payout_model,
    make_wallet_entry_model,
)


class UserModel(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "users"


WalletEntryModel = make_wallet_entry_model(user_table="users")
ManualPayoutModel = make_manual_payout_model(user_table="users")


def ledger(session: AsyncSession) -> WalletLedger:
    return WalletLedger(
        balances=BaseRepository(session, model=UserModel),
        entries=BaseRepository(session, model=WalletEntryModel),
    )


payouts: AdminModel[Any] = manual_payout_admin(
    ManualPayoutModel,
    upload_storage=LocalUploadStorage("receipts"),
    ledger_factory=ledger,
)
```

The whole flow — the withdrawal that
writes the row and e-mails the administrator — is in
[Mercado Pago: testing webhooks » Manual withdrawal](../mercado-pago-webhooks.md#manual-withdrawal).

!!! tip "Sidebar + burger navigation"
    Every authenticated page has a persistent **sidebar**: Dashboard, one
    link per registered model (grouped under "Models"), and — with
    `show_logs=True` — "Logs" under "System". The current page's entry is
    highlighted. On **desktop** the sidebar is always visible on the left;
    on **mobile** (≤768px) it becomes off-canvas, opened by the **burger**
    icon in the header and dismissed by tapping the scrim — pure CSS, no JS.

!!! info "Logs page (`show_logs=True`)"
    `GET /admin/logs` reads the structured JSON files that
    `configure_logging(log_dir=…)` writes. Pass the **same** `log_dir` to
    `make_admin_router`. The page offers a source filter
    (`all`/`debug`/`info`/`warning`/`error`/`critical`/`500`), substring
    search on the message, and pagination, with color-coded level badges.
    It is **opt-in** (`show_logs=False` by default) because the payload
    exposes tracebacks and request metadata — only enable it behind the
    admin login. With no files in `log_dir`, the page shows an empty state.

    The **Refresh** button reloads the page with the same source and the
    same search, back on the first page, which holds the newest records.
    The page reads the files on every request, so reloading is all it
    takes; the admin ships no JavaScript, and the button is a link. Below
    the title, **Read at** shows when the files were read (UTC), so the read
    visibly ran even when nothing new arrived. The button also shows in the
    empty state, which is exactly when you wait for the first record.

!!! tip "500 tracebacks and export-to-issue"
    Every record carrying a traceback (the SDK's handlers log with
    `exc_info=True`) becomes a clickable item: **the message itself is the
    trigger**, so clicking anywhere on the entry reveals the trace, with the
    request correlation fields (`path`, `method`, `status_code`,
    `request_id`) alongside it. Plain `<details>`/`<summary>`, no JS,
    collapsed by default so a page full of 500s stays scannable — a record
    without a traceback grows no toggle at all.

    `GET /admin/logs/export?format=md|json` downloads the **filtered**
    selection (the page's own `?source=` and `?q=`), newest first, up to 500
    records:

    - **`format=md`** — each traceback goes into a ```` ```pytb ```` fenced
      block, so it survives a paste into an issue or PR with its indentation
      intact. The header states the source, the count, the active filter and
      — when the 500-record cap bites — how many records matched in total, so
      a partial export never reads as a complete one.
    - **`format=json`** — the records verbatim, including every field the
      application logged through `extra=`, for tooling to consume.

    Below 600px the table becomes **stacked cards** (the header is dropped
    and each cell names itself), so the message and the traceback sit in the
    viewport with no horizontal scroll. The other list views keep the
    sideways scroll, which suits a list you skim.

    The export inherits the admin session gate: a traceback is exactly the
    payload that must not be world-readable. To build your own export,
    `render_entries_markdown` and `render_entries_json` are exported at the
    package level.

!!! tip "Responsive by default"
    The bundled templates + CSS are responsive: on narrow screens (≤600px) the header stacks, search/filters/actions go full-width, tables get horizontal scroll (never breaking the layout), and the detail grid collapses to a single column. Column headers are clickable to toggle sort order (▲/▼).

## Autocomplete FK (`autocomplete_fields=`)

By default a FK field whose target has a registered admin becomes a
`<select>` of **every** row (capped at 1000). On a large table that is
unusable. Pass `autocomplete_fields` and the field becomes a **search
box** (HTMX) that queries the target on demand:

```python
# src/admin/site.py

from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import Company, Employee


site.register(AdminModel(model=Company, search_fields=[Company.name]))
site.register(
    AdminModel(model=Employee, autocomplete_fields=[Employee.company_id])
)
```

On the Employee form, `company_id` no longer lists every company: you
type, the `/admin/m/employee/autocomplete/company_id?q=…` endpoint
searches the `Company` admin's `search_fields` (ILIKE, OR, up to 20
results) and shows the options; clicking one pins the id into the field.
On edit, the current company's label is pre-filled.

!!! info "Requirements"
    The FK target must have a registered `AdminModel` (its
    `search_fields` drive the search). Without `autocomplete_fields`, the
    FK stays a `<select>` (fine for small tables).

## Inlines — 1-N children on the detail (`inlines=`)

To see (and reach) the records that point at this one — a customer's
orders, a team's members — declare `inlines`. The parent's detail view
then lists each relation as a table, with a link to the child admin and
an **Add** button that pre-fills the parent FK.

```python
# src/admin/site.py

from tempest_fastapi_sdk import AdminModel, Inline

from src.admin import site
from src.db.models import Member, Team


site.register(
    AdminModel(
        model=Team,
        inlines=[Inline(Member, Member.team_id, list_display=[Member.name])],
    )
)
site.register(AdminModel(model=Member))   # the child needs an admin for links
```

A Team's detail now shows a **Member** table with its members; "Add"
opens the Member create form with `team_id` pre-filled (via a query
param). Columns come from the `Inline`'s `list_display` (or, if omitted,
the child admin's). Up to 50 rows per inline.

The child model must have a registered `AdminModel` for the links to
work; without one, the rows render read-only.

### In-place editing (`editable=True`)

To edit the children **without leaving the parent's detail view**, pass
`editable=True` (and `can_delete=True` to allow removal). The table
becomes a formset: one input row per existing child plus a blank row to
add another.

```python
from tempest_fastapi_sdk import AdminModel, Inline

from src.admin import site
from src.db.models import Member, Team


site.register(
    AdminModel(
        model=Team,
        inlines=[
            Inline(
                Member,
                Member.team_id,
                editable=True,
                can_delete=True,
            )
        ],
    )
)
site.register(AdminModel(model=Member))   # can_edit / can_delete apply here
```

On save (`POST /admin/m/<parent>/<id>/inlines/<child>`) everything happens
in one transaction: existing rows are updated, a blank row with any value
becomes a new child, and a checked delete box removes the row.

!!! tip "Transparent, no magic"
    - The **parent foreign key is implied** — forced to the parent, never
      an input; the user can't pick (or re-point to) the wrong parent.
    - Each row is **scoped to the parent**: a child whose foreign key
      doesn't match is ignored, never cross-edited.
    - **Upload/autocomplete** columns stay on the child's own form (they
      don't enter the compact formset).
    - Validation errors **re-render the formset in place**, with a
      per-field message and nothing inserted.
    - Needs the child's `AdminModel` + `can_edit` (and `can_delete` to
      delete). Up to 50 rows per inline.

## Many-to-many in the form (`m2m_fields=`)

A `relationship(..., secondary=...)` — an article's tags, a user's roles —
is not a column, so the form did not show it. Declare it in `m2m_fields`:

```python
from sqlalchemy import Column, ForeignKey, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship
from tempest_fastapi_sdk import AdminModel, BaseModel

from src.admin import site

article_tags = Table(
    "article_tags",
    BaseModel.metadata,
    Column("article_id", ForeignKey("articles.id"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id"), primary_key=True),
)


class Tag(BaseModel):
    __tablename__ = "tags"
    name: Mapped[str] = mapped_column(String(64))


class Article(BaseModel):
    __tablename__ = "articles"
    title: Mapped[str] = mapped_column(String(120))
    tags: Mapped[list[Tag]] = relationship(secondary=article_tags)


site.register(AdminModel(model=Tag, search_fields=[Tag.name]))
site.register(AdminModel(model=Article, m2m_fields=[Article.tags]))
```

Create and edit gain a `<select multiple>` of the tags (labelled like the
foreign-key `<select>`: the target admin's first `search_fields`), and the
detail lists the linked tags, linking to them when the target has an admin.
Saving **replaces the set** in the same transaction as the row: what was
not selected is unlinked, and when the save is refused (validation, scope,
conflict) no link changes.

With a scope, the rule is the foreign key's on writes:

- the options are only the rows the principal reaches through the
  target's `row_scope` (and need `VIEW` on it — without `VIEW` the field is
  not shown, and the stored links stay as they are);
- a link the operator does **not** reach is not shown in the form and
  **stays** after the save — they do not delete what they cannot see;
- an id posted outside the scope, or one that does not exist, refuses the
  field with `Select a valid choice; that record does not exist.`, the same
  error, so a forged `POST` learns nothing about another tenant's ids.

!!! info "Checked at construction"
    A name in `m2m_fields` that is not a `relationship` with `secondary`
    (a column, a one-to-many) raises `ValueError` when the `AdminModel` is
    built. One-to-many has
    [`inlines=`](#inlines-1-n-children-on-the-detail-inlines); many-to-one
    is already the foreign-key `<select>`. The options stop at
    `FK_OPTION_CAP` (1000) rows, read with `LIMIT` in the SQL; a reachable
    link the cap left out comes as one more option, already selected, like
    the single foreign key — saving without touching the field keeps it.
    Those extra options stop at the cap too, read with the scope in the same
    query; a link that still does not show stays stored: the save only
    unlinks what the form offered and came back unselected.

!!! info "Locked field"
    An `m2m_fields` name in `readonly_fields`, or in what
    `readonly_fields_for` returns for the row, leaves the form and the save
    does not touch its links. The form the history's **Restore** opens
    carries the current links like the edit form, so restoring another
    field unlinks nothing.

## Field widgets by column type

The create/edit form picks the widget from the column type, no config:
`bool` → checkbox, `Enum` → select, `int`/`float`/`Decimal` → number,
`datetime`/`date`/**`time`** → native inputs, long string → textarea,
**`JSON` column** → a monospaced JSON editor (pretty-printed on open,
`json.loads` + validation on submit — invalid JSON becomes a field
error, not a string stored in its place). A FK becomes a `<select>` (or
autocomplete, via `autocomplete_fields`), and `upload_fields` become file
inputs.

## Password in the form (`password_fields=`)

A model inheriting `BaseUserModel` **could not be created through the
panel**. The form does not show `hashed_password` (nobody types a bcrypt
digest into an `<input type="text">`), the column is `NOT NULL`, and the
insert answered a 400 reading `Conflict creating <Model>` — which an
operator reads as "e-mail already taken".

`password_fields=` closes the loop: the box takes plaintext and the save
stores the hash.

```python
# src/admin.py
from tempest_fastapi_sdk import AdminModel, AdminSite, PasswordPolicy

from src.db.models import LocalUserModel

site = AdminSite(title="Panel")
site.register(
    AdminModel(
        LocalUserModel,
        password_fields=[LocalUserModel.hashed_password],
        password_policy=PasswordPolicy(min_length=12, require_complexity=True),
        list_display=[LocalUserModel.name, LocalUserModel.email],
    ),
)
```

What changes, field by field:

- **Create:** `<input type="password" autocomplete="new-password">`,
  required while the column is `NOT NULL`. The value goes through
  `check_password_policy` and, when rejected, becomes an error **on the
  field** — no longer a `ConflictException`. The label drops the `hashed_`
  prefix (`hashed_password` → **Password**): the box takes plaintext, and
  calling it "Hashed Password" would describe the column, not what you type.
- **Edit:** the box starts empty, and empty means *leave it alone*. Filled
  in, it rotates the hash. The digest **never** pre-fills the input.
- **Hashing:** uses the model's own `set_password` when it has one (that is
  what `BaseUserModel` ships, and where a project swaps the algorithm);
  otherwise `PasswordUtils`.
- **Never shown:** out of `list_display` (even a hand-declared one), out of
  the detail view and out of the CSV/JSON export.

!!! warning "`password_fields` does not combine with `can_import=True`"
    Hashing a plaintext column read from a file is a separate decision, and
    the importer would post every row with no password at all. The
    combination raises `ValueError` when the `AdminModel` is built — at
    boot, not in production.

!!! tip "The policy comes from your settings"
    `PasswordPolicy.from_settings(settings)` reads
    `AUTH_PASSWORD_MIN_LENGTH`, `AUTH_PASSWORD_MAX_BYTES` and
    `AUTH_PASSWORD_REQUIRE_COMPLEXITY` off the `Settings` you already
    compose from `AuthSettings` — so the panel enforces the same rule as
    `signup`. With no argument, the `AuthSettings` defaults apply.

## A column that never shows (`exclude_fields=`)

Not every credential is a password. A Web Push subscription stores
`endpoint`, `p256dh` and `auth` — whoever holds the three can push to that
device —, and a user stores a `push_token` and a `totp_secret`.
`readonly_fields` does not help: it locks the input and still **shows** the
value in the detail view, to every admin.

`exclude_fields=` takes the column out of the whole panel:

```python
# src/admin.py
from tempest_fastapi_sdk import AdminModel, AdminSite

from src.db.models import LocalUserModel, WebPushSubscriptionModel

site = AdminSite(title="Panel")
site.register(
    AdminModel(
        LocalUserModel,
        exclude_fields=[LocalUserModel.totp_secret, LocalUserModel.push_token],
    ),
)
site.register(
    AdminModel(
        WebPushSubscriptionModel,
        exclude_fields=[
            WebPushSubscriptionModel.endpoint,
            WebPushSubscriptionModel.p256dh,
            WebPushSubscriptionModel.auth,
        ],
        can_create=False,
    ),
)
```

The column leaves **every** surface at once: the list, the detail view, the
create and edit forms (a `POST` that sends the field by hand is ignored), the
CSV import, the CSV/JSON export, the sortable header (`?sort=endpoint` is ignored and the
default ordering applies), the inline table on the parent's detail —
even when the `Inline` names the column in its own `list_display` —, the
before/after rows of the audit timeline, and the label another model's FK
`<select>` shows. All of them read the same set, `hidden_field_names()`,
which is `hashed_password` + the model's `__secret_columns__` +
`password_fields` + `exclude_fields`.

!!! info "The SDK models' credentials leave on their own"
    `MFAMixin.totp_secret`, `BaseWebPushSubscriptionModel`'s
    `endpoint`/`p256dh`/`auth`, `BaseDeviceTokenModel`'s
    `token`/`p256dh`/`auth`, `BaseUserRefreshTokenModel.token_hash` and
    `BaseUserRecoveryCodeModel.code_hash` are declared in
    `__secret_columns__`. A plain `AdminModel(model=...)` — or `automap` —
    already hides them from every surface above, and the audit stores them
    as `[redacted]`, with no `exclude_fields` or `__audit_redact__`.
    `exclude_fields` stays the way to hide a column of **your** model, like
    the `push_token` above. A `NOT NULL` secret column with no default still
    shows as a blank input on the **create** form (never on edit), so a push
    subscription can still be created through the panel. Your own model
    declares its secrets with
    `__secret_columns__: ClassVar[frozenset[str]] = frozenset({"api_key"})`.

A configuration that contradicts itself fails when the `AdminModel` is
built, at boot, instead of rendering the secret:

- a name that is not a column of the model;
- an excluded column another option also names: `list_display`,
  `list_filter`, `search_fields`, `readonly_fields`, `upload_fields`,
  `autocomplete_fields`, `password_fields`, `identity_field`, `ordering` or
  a `Lens` (`filters` or `order_by`);
- a `NOT NULL` column with no default excluded while `can_create=True` —
  the form could never fill it, and every create would end in `Conflict
  creating <Model>`. That is the push subscription above, hence
  `can_create=False`.

### And the audit table?

Hiding the timeline is not enough: with `audit_model=`, `add_audited` /
`update_audited` would write the value into the audit table itself, readable
by query or through the panel's SQL console. That is why an `AdminModel`
with `audit_model=` **refuses** an excluded column the model does not list in
`__audit_redact__`:

```python
# src/db/models.py
from typing import ClassVar

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class WebPushSubscriptionModel(BaseModel):
    """A device's Web Push subscription."""

    __audit_redact__: ClassVar[frozenset[str]] = frozenset(
        {"endpoint", "p256dh", "auth"}
    )

    platform: Mapped[str] = mapped_column(String(16))
    endpoint: Mapped[str] = mapped_column(String(512))
    p256dh: Mapped[str] = mapped_column(String(128))
    auth: Mapped[str] = mapped_column(String(64))
```

The audit entry writes `"[redacted]"` in place of the value. On update the
diff is computed on the real values before the swap, so rotating the key
still shows — `{"before": "[redacted]", "after": "[redacted]"}` —, and
`None` stays `None`, so "not set" remains distinguishable from "set".
`hashed_password` is always redacted, declared or not.

!!! note "`can_import=True` is still allowed"
    Unlike `password_fields`, there is no hidden decision in the import: the
    excluded column is simply not importable, just as it is not editable.
