# List view: counts, import and bulk edits

Use to power up the list view: related counts, CSV/XLSX import, duplicate, bulk edit, lenses and counting large tables.

Part of the [Admin site](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/admin.md) recipe.

## Related counts on the list (`related_counts=`)

On the customer list, how many orders does each one have? Declare the
**child's** foreign-key column on the **parent's** admin:

```python
from tempest_fastapi_sdk import AdminModel, RelatedCount

from src.admin import site
from src.db.models import CustomerModel, OrderModel


site.register(
    AdminModel(
        model=CustomerModel,
        related_counts=[RelatedCount(OrderModel.customer_id, label="Orders")],
    )
)
site.register(AdminModel(model=OrderModel))
```

The list gains an **Orders** column with the count per row, and each number
links to the order list already filtered by the customer
(`?filter_customer_id=<id>`) — the filter applies even with `customer_id`
outside the orders' `list_filter`, shows as a **Filtered by** chip with
**Clear filter**, and the filtered list's export honours it too.

A whole page's counts come from **one** query,
`SELECT customer_id, COUNT(*) ... WHERE customer_id IN (<page ids>)
GROUP BY customer_id`, never one per row — the test counts the statements
of a 25-row page and finds a single `GROUP BY`.

- The query carries the child admin's `row_scope`: the number counts only
  the orders the principal reaches.
- Without `VIEW` on the child, the column goes — the number is that
  model's data.
- The link exists only when the child has a registered admin; a child
  without one shows the count without a link. When the child's admin hides
  the foreign-key column (`exclude_fields`, `__secret_columns__`) there is
  no link either, and `?filter_<fk>=` is ignored on the child's list:
  filtering on a column the screen never shows would answer, through the
  count, what it holds.
- `RelatedCount` takes the mapped attribute (so it knows the child model);
  the column must carry a `ForeignKey` to the parent's table, or the
  `AdminModel` raises `ValueError` at construction.

## CSV import (`can_import=True`)

The admin already exports the list (CSV/JSON). The counterpart: upload a
CSV to bulk-**create** records. Enable it with `can_import=True`:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import Product


site.register(AdminModel(model=Product, can_import=True))
```

An **Import CSV** link appears on the list view → an upload page. The CSV
needs a header row with the editable column names (the page lists them).
The import takes **two steps**:

1. **Preview.** The upload validates every row with the **same rules as
   the create form** (types, required fields, `choices`) and rehearses the
   inserts inside a transaction that is always rolled back — so the
   preview also shows what only the database would refuse (a duplicate in
   a `unique` column) and what the [`row_scope`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/admin/permissions.md#row-scope-row_scope)
   does not reach. Nothing is written. The page says how many rows go in,
   shows the first 20, and lists each refused row by number (the header is
   row 1, as in a spreadsheet) with each field's error.
2. **Confirm.** The **Confirm import of N records** button posts the CSV
   back in a hidden field **signed** with the router's `secret_key` and
   bound to the model and the operator who previewed it: what goes in is
   exactly the file that was seen. The rows run again, for real, in one
   transaction, and the commit is **all or nothing** — if the table
   changed since the preview and a row now fails, nothing goes in and the
   page comes back with the errors. On success the panel redirects to the
   list with `Created N record(s).`

With an error in **any** row the confirm button is disabled, and the
confirm `POST` refuses the same way: nothing is ever half imported.

!!! info "Limits of the signed step"
    - The file is capped at **512 KiB** (`ADMIN_IMPORT_MAX_BYTES`); a larger
      one gets a `400` asking to split it. Measured over six 512 KiB files
      (random hex, random printable ASCII, Latin-1, CJK, emoji,
      one-character lines), the signed field came to at most 571 099 bytes.
    - A preview stays valid for **1 hour**
      (`ADMIN_IMPORT_PREVIEW_MAX_AGE_SECONDS`). After that, or with the
      field tampered with, or signed for another model or another
      operator, the confirm answers `400` and writes nothing.
    - The confirm spends a single-use nonce: reloading the answer does not
      import again.

!!! warning "Repository signals fire on the confirm only"
    The preview inserts through the session (after the repository's
    `_prepare_insert` hook), without the repository's `add` — no
    `PRE_SAVE`/`POST_SAVE` handler fires for a preview. The confirm goes
    through `repository.add`, signals included. Since the single commit
    comes at the end, a row's `POST_SAVE` has already fired when a later
    row rolls the batch back; a handler with an outside effect (email,
    queue) should key on the commit, not on the signal.

!!! info "Opt-in + requires create"
    `can_import` defaults to `False` and requires `can_create` (importing
    is bulk creation). File-upload columns are not imported from CSV.

### Export and import `.xlsx`

With the `[spreadsheet]` extra installed, the list gains **Export XLSX**
next to CSV and JSON, and the import takes `.xlsx` as well as `.csv`:

```bash
uv add "tempest-fastapi-sdk[admin,spreadsheet]"
```

- **Export.** The CSV's rows — search, filters, `row_scope` and the
  `export_max_rows` cap — in one sheet, with the header frozen. A datetime
  is written as the **same ISO 8601 text in UTC** the CSV holds; numbers
  and booleans stay numbers and booleans. A text cell starting with `=`
  stays text (`SheetWriter` writes it as a string), so `=HYPERLINK(...)`
  typed into a name does not become a formula in the file. A control
  character XML cannot carry (`\x00`-`\x08`, `\x0b`, `\x0c`,
  `\x0e`-`\x1f`) is removed from the cell instead of failing the export
  with a `500`.
- **Import.** An `.xlsx` (by name, or because it opens as a ZIP) is read
  from its **first sheet**, row 1 as the header, and turned into the CSV
  text the import already validates: from there it is the same path —
  preview, `row_scope`, foreign keys, signed confirmation. What gets signed
  is that CSV, so the 512 KiB cap applies to the upload **and** to the CSV
  it becomes. A boolean becomes `true`/`false`, a datetime ISO 8601 (with
  no offset, as the sheet stores it, read in the model's zone like the
  CSV), and `3.0` becomes `3`.
- **Formula with no calculated value.** A sheet written by a library
  (openpyxl, pandas) stores the formula but not its result, and the cell
  would read empty. The preview refuses the file naming the cells —
  `A3: formula with no calculated value...`; opening and saving it in Excel
  or LibreOffice stores the value. A formula Excel evaluated to empty text
  (`=IF(A2="","",...)`) is valid and comes in empty, and only the columns
  with a header in row 1 are checked: a formula in a column the import
  ignores does not refuse the file.
- **Read limits.** The sheet is read with a cap of
  `ADMIN_XLSX_IMPORT_MAX_ROWS` rows (`ADMIN_IMPORT_MAX_BYTES // 16`,
  32 768) and `ADMIN_XLSX_IMPORT_MAX_UNCOMPRESSED_BYTES` decompressed
  (32 × the cap, 16 MiB), proportional to the CSV cap; past them the
  preview answers `400` naming both limits. A sheet of rows under 16 bytes
  of CSV may be refused although it would fit in 512 KiB.

!!! info "Without the extra"
    openpyxl is only loaded when an `.xlsx` is asked for. Without the extra
    the **Export XLSX** button is not rendered, the upload's `accept` goes
    back to CSV only, and the routes answer with the message that names the
    package: the export redirects to the list with
    `Exporting .xlsx needs the spreadsheet extra: pip install "tempest-fastapi-sdk[spreadsheet]"`
    and the import preview answers `400` with the equivalent.

## Duplicate a record (`can_duplicate=True`)

To create a record that resembles an existing one, `can_duplicate=True`
puts **Duplicate** on the detail:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import ProductModel


site.register(AdminModel(model=ProductModel, can_duplicate=True))
```

The button opens the **create** form filled with the record's values,
minus what a copy must not carry: the identity, the unique columns
(`unique=True`, `UniqueConstraint` and unique indexes — composite ones
included), passwords, uploads, secret columns and the audit columns
(`created_at`, `updated_at`, `created_by`, `updated_by`). The
[`m2m_fields`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/admin/forms.md#many-to-many-in-the-form-m2m_fields) links the principal
reaches come selected.

The form posts to the ordinary create, so the copy goes through **every**
check a new record does — validation, foreign keys in scope, `row_scope`
after the write. It needs `CREATE` (and `VIEW`, since it reads the source),
and the source must be in scope: outside it, `404`. It needs the column
form: with `can_create=False` or a `create_handler`, construction raises
`ValueError`.

## Bulk edit of one field (`bulk_edit_fields=`)

Archiving 40 orders at once without opening each one:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import OrderModel


site.register(
    AdminModel(
        model=OrderModel,
        bulk_edit_fields=[OrderModel.status, OrderModel.assignee_id],
        choices={OrderModel.status: ["open", "shipped", "archived"]},
    )
)
```

The selection's action menu gains **Edit field**. Check the rows, pick the
action, pick the field (a step skipped when there is only one) and type the
value into **the field's own widget** — the `choices` `<select>`, the
foreign-key one. The value is validated like the edit form: type, required,
`choices`, the foreign key within the target's scope, each row's
`validate_edit`, and a field that `readonly_fields_for` locks on any
selected row refuses the whole edit.

The write is **one** `UPDATE ... WHERE id IN (...) AND <scope>` in a
transaction:

- only the selected ids the principal reaches go in;
- when the new value takes any row out of the scope, the whole transaction
  is rolled back and the list shows the scope warning;
- with `audit_model`, each row gets its own `update` entry with its diff,
  in the same transaction;
- `updated_by` gets the operator and `updated_at` moves — an edit form
  opened before reports the [conflict](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/admin/operator.md#concurrent-edits-optimistic-lock).

Needs `can_edit` and `EDIT`. The value step carries a single-use nonce:
reloading the answer does not apply it again.

!!! warning "No repository signals"
    Like bulk activate/deactivate, the bulk edit is SQL Core: `PRE_SAVE` /
    `POST_SAVE` handlers do not fire. Each name in `bulk_edit_fields` must be
    an editable form column (not an upload, a password or `readonly_fields`),
    checked at construction.

## Lenses — saved views (`lenses=`)

A lens is a named preset of filters + ordering, shown as a tab above the
list. Instead of the operator re-entering "status=open, priority>=3,
oldest first" every time, they click the tab:

```python
from tempest_fastapi_sdk import AdminModel, Lens

from src.admin import site
from src.db.models import Ticket


site.register(
    AdminModel(
        model=Ticket,
        lenses=[
            Lens("Open", filters={"status": "open"}),
            Lens(
                "Urgent",
                filters={"status": "open", "priority__gte": 3},
                order_by="-created_at",
            ),
        ],
    )
)
```

The list gains **All / Open / Urgent** tabs. Clicking one applies the
lens's filters (ANDed with whatever search/filters the user already
set) and its ordering (`order_by`, `-col` = desc, unless the user
clicked a header). The active lens is preserved across pagination, sort
and export; "All" clears it.

!!! info "Same filter conventions"
    `filters` uses the same dict as the repository (`field__gte`, `name`
    ILIKE, iterable → `IN`, …). The tab slug (`?lens=`) is derived from the
    name (lowercased, hyphenated).

## Counting large tables (`count_strategy=`)

The list runs `COUNT(*)` on every page to show "N records" and "Page X of
Y". On a table with tens of millions of rows, that count is the expensive
part of the page. `count_strategy=` changes it:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import EventModel, LedgerEntryModel


site.register(AdminModel(model=EventModel, count_strategy="estimate"))
site.register(AdminModel(model=LedgerEntryModel, count_strategy="none"))
```

- `"exact"` (default): the usual `COUNT`.
- `"estimate"`: on PostgreSQL, reads `pg_class.reltuples` — the statistic
  `ANALYZE` (and autovacuum) keeps, one catalog lookup whatever the table
  size — and the screen shows `~40 records` and `Page 1 of ~4`, with a hint
  in the `title`. The page fetches one extra row to know whether a next
  page exists.
- `"none"`: no count. The screen shows only **Prev** / **Next** and
  `Page N`; next shows when fetching `page_size + 1` rows brought the extra
  row.

The model's dashboard card follows the same strategy (`~40`, or no number
with `"none"`). `AdminCountStrategyEnum` holds the three values if you
prefer the enum to the string.

!!! info "When `estimate` counts for real"
    The statistic describes the **whole table**. With a search, a filter, a
    lens or a `row_scope` other than `true()`, what the operator sees is a
    different set, and the estimate would be of rows they cannot reach — in
    the scope case, the size of other tenants' data. Those count exactly
    (`COUNT` with the same `WHERE`). It also counts exactly outside
    PostgreSQL (SQLite included), on a table PostgreSQL never analyzed
    (`reltuples = -1`) and on a single-table-inheritance subclass (the
    table's statistic counts the sibling classes).

!!! warning "With `repository_class=`, it is `exact`"
    `estimate` and `none` build their own `SELECT` (and `estimate` reads
    the catalog), which would go around a `repository_class` that filters —
    tenant isolation in an overridden `paginate`, for example. With a
    `repository_class` configured, the list and the card use your
    repository's `paginate` / `count`, as `exact` does.

Measured on `postgres:16-alpine`, capturing one page's statements: with
`"estimate"` over 40 analyzed rows the page ran the page `SELECT` and
`SELECT reltuples FROM pg_class WHERE oid = to_regclass($1)`, no `count(`;
with `?q=` the `count(*)` came back; on a freshly created table
(`reltuples = -1`) `count(*)` ran after the catalog read and the header
came out without `~`.
