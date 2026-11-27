---
description: "Use to get a Django-style admin panel under `/admin`; covers what it provides, installation and the index of per-task subpages."
---

# Admin site


Django-style management UI mounted under `/admin`. Operators sign in with a user row from your own database — there is no separate admin password store. Every registered model becomes browsable from the browser, so the database port can stay closed on private networks.

**What you get** (Django-admin parity):

- List view with search, rich per-field filters (enum / FK / date-range) and sortable columns.
- Full CRUD (create / edit / delete) and bulk actions.
- CSV/JSON/XLSX export and FK-select widgets.
- Dashboard with live row counts + system metrics.
- Optional TOTP MFA at login.
- File/image upload fields.
- Audit trail stamping `created_by` / `updated_by`.

Also covers in-place inline editing of 1-N children (`Inline(editable=True)`).

Requires the `[admin]` extra:

```bash
uv add "tempest-fastapi-sdk[admin]"
```

## Where each task lives

Use this table to open only the subpage for the task at hand; they are listed in reading order.

| Subpage | What it solves |
| --- | --- |
| [Mount the admin site](admin/setup.md) | Use to stand the panel up: user model, admin class registration (or `automap`) and the router with everything it mounts. |
| [Custom actions](admin/actions.md) | Use to add buttons that run your code (`@admin_action`): show a secret once, ask for input before running, create through a service. |
| [Form fields](admin/forms.md) | Use to tune the form: choices, file upload, autocomplete FKs, inlines, many-to-many, widgets, passwords and hidden columns. |
| [Audit history](admin/audit.md) | Use to show who changed what on the detail page, recent activity on the dashboard and undo from the history. |
| [List view: counts, import and bulk edits](admin/list.md) | Use to power up the list view: related counts, CSV/XLSX import, duplicate, bulk edit, lenses and counting large tables. |
| [Dashboard: cards, indicators and map](admin/dashboard.md) | Use to put business metrics on the dashboard: cards, indicator areas with thresholds and the geographic distribution map. |
| [Permissions: RBAC, row scope and impersonation](admin/permissions.md) | Use to control who sees and does what: RBAC per model and action, row scope on reads and writes, and logging in as another user. |
| [Timezone, concurrent edits and language](admin/operator.md) | Use to show dates in the operator's timezone, stop two edits from overwriting each other and switch the interface to Portuguese. |
| [Global search](admin/search.md) | Use to search a term across every model with `search_fields` from the search box at the top of the panel. |

## Recap

- `AdminSite` + `AdminModel` turn your models into a CRUD interface with no
  template of your own: the declaration *is* the screen.
- `@admin_action` puts a domain operation in the list, and the
  `AdminActionResult` it returns is what the operator reads back. The panel
  commits what the action left in the session; `schema=` asks for input
  before it runs, and `action_policy=` grants action by action, without
  `EDIT` on the model.
- `audit_model=` renders the who-changed-what timeline inside the detail, and
  `inlines=` brings 1-N children onto the same page.
- `autocomplete_fields=` swaps the `<select>` that does not scale for an HTMX
  search — required the moment a foreign key has thousands of rows.
- `dashboard_cards=` are business metrics, not system ones; `lenses=` are the
  saved views an operator would reach for again tomorrow.
- `location_maps=[BRLocationMap(...)]` draws where the rows of a model with `BRLocationMixin` are: a map of Brazil by state and city, offline.
- `access_policy=` is RBAC per (principal, action, model): without it, anyone
  who reaches the admin can do everything.
- `storage=StorageBrowserService(...)` puts the bucket on screen: list,
  preview (SVG and HTML only as source), upload, edit text, rename and
  delete — `read_only=True` leaves only the reads.
- `AdminTheme` covers the look through typed fields; `can_import=True` opens a
  two-step CSV import: the preview validates and rehearses without
  writing, and the confirm writes the signed file, all or nothing.
- `row_scope=` filters **which rows** the principal reaches on every route
  of the model; outside the scope is `404`, and a write that takes the row
  out of the scope is rolled back.
- The edit form brings the optimistic lock for free: a save over a stale
  version comes back with `409` and the operator's values, unwritten.
- `AdminSite(locale="pt-BR")` puts the panel in Portuguese; the filter's
  date shortcuts come out translated and in the model's zone.
- `exclude_fields=` takes a credential that is not a password off every
  surface of the panel; `readonly_fields` only locks the write.
- `display_timezone=` is what turns the datetime field into a widget: the
  screen reads and writes in the operator's zone, the column stays UTC.
- `m2m_fields=` edits a `secondary` as a multi-select, and a link the
  operator does not reach stays; `related_counts=` counts the children per
  row with one `GROUP BY` per page.
- `can_duplicate=True` opens the create form filled with the copy, and
  `bulk_edit_fields=` applies one value to the whole selection in a single
  `UPDATE`, rolled back when it takes a row out of the scope.
- `AdminSite(recent_activity=N)` turns on the dashboard feed of the latest
  audited changes; the `[spreadsheet]` extra adds `.xlsx` to the export and
  the import.
- `impersonate=` puts "Log in as" on the detail: the service provides the
  URL (`make_jwt_impersonation_handler` signs a short access token with
  `act`), the panel confirms, refuses an admin target, records and
  redirects — and only with `IMPERSONATE` granted by an `access_policy`.
- `count_strategy="estimate"` swaps the `COUNT` for PostgreSQL's statistic
  while nothing narrows the list; `"none"` leaves only previous/next.
- **Restore** in the history opens the edit form with the previous values;
  the save is a normal edit, with permission, validation and lock.
- The top bar's global search looks in every model with `search_fields`,
  honouring `VIEW` and `row_scope`.
