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

#### 1. User model

Subclass `BaseUserModel` to get the four columns the admin auth backend expects (`email`, `hashed_password`, `is_admin`, `last_login_at`), plus the `email_verified_at` the [auth flow](auth-flow.md#what-counts-as-a-verified-email) fills, on top of the standard `BaseModel` row. Activating an account from the admin does not fill `email_verified_at`, and editing its `email` clears it:

```python
# src/db/models/user.py
from tempest_fastapi_sdk import BaseUserModel


class UserModel(BaseUserModel):
    __tablename__ = "users"   # scaffold convention; admin slug derives from __tablename__
```

`set_password()` / `check_password()` delegate to `PasswordUtils`; `normalize_email()` lowercases and strips. The default `is_active` (inherited from `BaseModel`) and `is_admin` (defaults to `False`) gate access — only `is_active=True` AND `is_admin=True` rows may sign in.

Bootstrap the first admin via your CLI / migration / seed script. The full script wires an `AsyncDatabaseManager`, opens one session, inserts the row and commits — exactly the same pattern your repositories follow at runtime:

```python
# scripts/create_admin.py
import asyncio

from tempest_fastapi_sdk import AsyncDatabaseManager

from src.core.settings import settings
from src.db.models import UserModel


async def main() -> None:
    db = AsyncDatabaseManager(settings.DATABASE_URL)
    await db.connect()
    try:
        async with db.get_session_context() as session:
            # ──────── the only admin-specific lines ────────
            admin = UserModel(email="root@example.com", is_admin=True)
            admin.set_password("hunter2")  # bcrypt via PasswordUtils
            session.add(admin)
            await session.commit()
    finally:
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
```

The four highlighted lines under the divider comment are the only admin-bootstrap code; everything around them is the standard async DB lifecycle the SDK already uses.

#### 2. Register your admin classes

`AdminModel` is a plain typed configuration instance — the constructor signature is the contract (no class-attribute / metaclass magic), and every field accepts a real SQLAlchemy column attribute (`UserModel.email`), so typos surface in your editor instead of at runtime. The defaults work out of the box; pass the fields you want to enrich the list view:

```python
# src/admin/site.py
from sqlalchemy import desc

from tempest_fastapi_sdk import AdminModel, AdminSite

from src.db.models import UserModel, OrderModel

site = AdminSite(
    title="MyApp Admin",
    brand="servus-backend-admin",     # centered header text (optional; defaults to title)
    index_subtitle="Site administration",
    site_url="https://myapp.com",     # optional outbound "View site" link
)

site.register(AdminModel(
    model=UserModel,
    list_display=[UserModel.email, UserModel.is_admin, UserModel.is_active, UserModel.last_login_at],
    list_filter=[UserModel.is_active, UserModel.is_admin],
    search_fields=[UserModel.email],
    readonly_fields=[UserModel.id, UserModel.hashed_password, UserModel.created_at, UserModel.updated_at],
    ordering=desc(UserModel.created_at),
    page_size=25,
))
```

Every field reference also accepts a plain string (`list_display=["email", ...]`) for dynamic configuration, and `ordering` accepts a column (ascending), `desc(column)` / `asc(column)`, or a Django-style `"-created_at"` string. `register` returns the instance and raises `ValueError` on a duplicate slug. Slugs default to the model's `__tablename__` so URLs and database tables stay in sync.

!!! info "Filters auto-pick a widget per column type"
    Each `list_filter` field renders the right widget by column type:
    **boolean** → Yes/No dropdown; **enum** → dropdown of the members;
    **FK** (whose target has a registered `AdminModel`) → dropdown of the
    related rows (labelled via `search_fields`); **date/datetime** → two
    date inputs (from/to, inclusive range); any other column → a text
    input (equality). All preserve search/sort/pagination in the URL.

!!! tip "Centered, customizable brand"
    The name shown in the center of the header comes from `brand`
    (optional). Without it, it falls back to `title` — so existing sites
    are unchanged. Use `brand` to show a distinct name (e.g.
    `"servus-backend-admin"`) centered at the top of every page. The
    sidebar is fixed and **overlays the header and footer** on desktop
    (higher z-index) — automatic from the bundled CSS, no config.

#### 2b. Shortcut — register every model at once (`automap`)

Instead of one `register` per table, point `automap` at the models
package and the SDK discovers and registers **every concrete
`BaseModel`** automatically. Abstract bases (`BaseUserModel` and friends
— no `__tablename__`) are skipped on their own:

```python
# src/admin/site.py
from tempest_fastapi_sdk import AdminModel, AdminSite

site = AdminSite(title="MyApp Admin", brand="servus-backend-admin")

# Load EVERY table under src/db/models in one shot:
site.automap("src.db.models")
```

Mix both styles: hand-register the models that need their own config,
then let `automap` fill in the rest (it skips already-registered slugs
by default):

```python
# UserModel gets a tuned config...

from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import UserModel


site.register(AdminModel(
    model=UserModel,
    list_display=[UserModel.email, UserModel.is_admin],
    search_fields=[UserModel.email],
))

# ...and automap registers the rest with defaults.
site.automap("src.db.models")
```

`automap` accepts: `exclude=[...]` (class, class name, or table name to
hide a model), `skip_registered=False` (raise `ValueError` on a
collision, like `register`), and `**admin_kwargs` applied to all
(`page_size=50`, `can_delete=False`, ...). To introspect without
registering, call the `discover_models("src.db.models")` function
directly.

!!! warning "Uniform config"
    `automap`'s `**admin_kwargs` apply to **every** discovered model.
    When a model needs its own `list_display` / `search_fields`,
    register it by hand **before** `automap` (with the default
    `skip_registered=True`).

#### 3. Mount the router

```python
# src/api/app.py
from fastapi import FastAPI

from tempest_fastapi_sdk import UserModelAuthBackend, make_admin_router

from src.admin.site import site
from src.api.dependencies import db   # singleton from src/api/dependencies/resources.py
from src.core.settings import settings
from src.db.models import UserModel

app = FastAPI()
app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.JWT_SECRET,          # scaffold reuses JWT_SECRET — at least 32 bytes
        prefix="/admin",
        cookie_secure=not settings.DEBUG,        # True in production HTTPS
        show_logs=True,                          # enables the logs page + sidebar entry
        log_dir=settings.LOG_DIR,                # same dir passed to configure_logging
    )
)
```

`make_admin_router` mounts:

- `GET  /admin/login`, `POST /admin/login`, `POST /admin/logout` — auth flow.
- `GET/POST /admin/mfa` — TOTP second-factor challenge between the password step and access, for MFA-enabled principals.
- `GET  /admin/` — dashboard: a card per model with its **live row count** + Browse/New, plus a **metrics panel** (CPU/RAM/disk via `MetricsUtils`). On by default, omitted without the `[metrics]` extra, disable with `make_admin_router(show_metrics=False)`.
- `GET  /admin/logs` — **application logs** (when `show_logs=True`): reads the structured JSON files written by `configure_logging(log_dir=…)`, with source filter (`?source=`), free-text search (`?q=`) and pagination. Color-coded level badges. Renders an empty state when no log files exist yet.
- `GET  /admin/logs/export` — **log export** (`?format=md|json`): downloads the filtered selection as markdown (traceback in a fenced block, ready to paste into an issue) or verbatim JSON.
- `GET  /admin/m/{slug}/` — list view with pagination + free-text search (`?q=`: each word matched as a case-insensitive substring in any of the `search_fields`, and every word must appear; `%` and `_` are literal) + per-field filters (`?filter_<field>=value`) + clickable **column sorting** (`?sort=<column>&dir=asc|desc`).
- `GET  /admin/m/{slug}/export.csv` / `export.json` / `export.xlsx` (the latter with the `[spreadsheet]` extra) — **export** the current result set (honoring search/filters/sort) as CSV or JSON. Row cap via `make_admin_router(export_max_rows=…)` (default 5000). In the CSV, a text cell starting with `=`, `+`, `-`, `@`, tab or CR is written with a leading `'`, so a spreadsheet shows the text instead of evaluating a formula; the JSON is unchanged.
- `POST /admin/m/{slug}/bulk` — **bulk actions** (delete / activate / deactivate + your **custom actions**) on the selected rows.
- `GET/POST /admin/m/{slug}/new` — **create** a record (when `can_create`).
- `GET  /admin/m/{slug}/{identity}` — detail view with Edit/Delete controls.
- `GET/POST /admin/m/{slug}/{identity}/edit` — **edit** a record (when `can_edit`).
- `POST /admin/m/{slug}/{identity}/delete` — **delete** a record (when `can_delete`).
- `GET/POST /admin/m/{slug}/import` + `POST /admin/m/{slug}/import/confirm` — **import** CSV/XLSX with a preview (when `can_import`).
- `GET  /admin/m/{slug}/{identity}/duplicate` — **duplicate** (when `can_duplicate`).
- `GET  /admin/m/{slug}/{identity}/restore/{entry_id}` — **restore** a change from the history (with `audit_model`).
- `GET/POST /admin/m/{slug}/{identity}/impersonate` — **log in as** the row's account (with `impersonate=` and the `IMPERSONATE` permission).
- `GET  /admin/search` — **global search** across models with `search_fields`.
- `GET  /admin/static/{path}` — the panel's bundled CSS (`admin.css`).
- `GET  /admin/_ssr/htmx.js` — the HTMX bundled in the SDK (`ssr/_static/htmx.min.js`), served without a session because the login page loads it too. The path follows `prefix`: with `prefix="/backoffice"` it becomes `/backoffice/_ssr/htmx.js`.

!!! info "The panel renders without any third party"
    No admin page fetches a script, stylesheet or font from outside your application: the HTMX `<script>` points at `{prefix}/_ssr/htmx.js`, which serves the file bundled in the wheel byte for byte. The exception is what you configure yourself: `AdminTheme`'s `favicon_url`, `logo_url` and `custom_css_url` load from wherever they point. A CDN outage or a tampered CDN file cannot reach the admin origin, which is the same origin as `POST /admin/sql` and the CRUD. It works with the `[admin]` extra alone — `[ssr]` is not needed.

    **CSP is still a separate step.** The admin sends no `Content-Security-Policy`, and it still uses an inline `<style>` (the `AdminTheme` theme), an inline `<script>` on the list view, an `onclick` on the FK autocomplete and `style=` attributes on the progress bars. Serving HTMX locally removes the third-party dependency, but it is not enough for a `script-src 'self'` policy without `'unsafe-inline'`: under it, those parts stop working.

!!! info "Write CRUD + permissions"
    Create/edit/delete are gated by `AdminModel` flags: `can_create` / `can_edit` / `can_delete` (all `True` by default; a disabled view returns `404`). Every write POST carries the session CSRF token, verified server-side (`403` on mismatch). **Field widgets** are derived from the column type — text / textarea (long strings) / number / checkbox / `datetime-local` / date / `select` for enums — with required-field + per-field validation errors re-rendered on the form. A write the **database** refuses (unique, FK, `NOT NULL`) comes back the same way: `400` with the repository's message (`Conflict creating <Model>`) above the form, never a `500`.

    **Bulk actions**: the list view shows per-row checkboxes + select-all and an action bar (delete / activate / deactivate) operating on the checked rows via `POST .../bulk` (CSRF + `can_delete`/`can_edit` flags), backed by `BaseRepository.delete_batch` / `bulk_update`.

    **FK-select**: a foreign-key column whose target has a registered `AdminModel` renders as a dropdown of the related rows (like Django's FK select) on the form, instead of a raw UUID input. The option label comes from the referenced admin's first `search_fields` entry (fallback: a `name`/`title`/`email` attribute, then the id). Capped at 1000 rows; FKs to unmanaged tables stay UUID inputs.

    **MFA login**: a principal with MFA enabled (`MFAMixin`'s `totp_secret`/`totp_enabled_at`) goes through a TOTP challenge at `/admin/mfa` after the password — only a valid code grants access. Enable it via `UserModelAuthBackend(UserModel, mfa_issuer=...)`; custom backends override `mfa_enabled`/`verify_mfa`.

    Each code is good for **one** sign-in: `UserModelAuthBackend` records the accepted step in `totp_last_step` (an `MFAMixin` column; a custom backend that keeps its own state overrides `claim_mfa_step`). And the challenge limits wrong codes per principal: `make_admin_router(mfa_throttle=...)`, by default 5 failures per 900 s on an `InMemoryThrottleBackend`; once spent, `/admin/mfa` answers `429` with `Retry-After` before looking at the code, even after a fresh login. With more than one worker, pass an `AttemptThrottle` on Redis.

    **Audit trail**: create/edit through the admin stamps `created_by`/`updated_by` (from `AuditMixin`) with the acting admin's id; the detail view shows an **Audit** panel with timestamps and — when the model has the audit columns — the actor (UUID resolved to a display name via the auth backend). Models without `AuditMixin` show timestamps only.

    In-place inline editing of children: see `Inline(editable=True)` below.

## Custom actions (`@admin_action`)

Beyond the 3 built-ins (activate / deactivate / delete), you register
**your own actions** — an async function decorated with `@admin_action`
and passed to `AdminModel(actions=[...])`. Each becomes an option in the
bulk-action dropdown, operating on the checked rows.

```python
from tempest_fastapi_sdk import (
    AdminActionContext,
    AdminActionResult,
    AdminModel,
    EmailUtils,
    admin_action,
)

from src.admin import site
from src.core.settings import settings
from src.db.models import UserModel

mailer = EmailUtils(**settings.email_kwargs())


@admin_action(label="Send welcome")
async def send_welcome(ctx: AdminActionContext) -> AdminActionResult:
    """Runs on the selected rows; the message is shown on the list view."""
    users = await ctx.repository.list(filters={"id": ctx.ids})
    for user in users:
        await mailer.send(
            user.email,
            subject="Welcome",
            body=f"Hi {user.email}! Your account is ready.",
        )
    return AdminActionResult(f"Sent {len(users)} emails.")


site.register(AdminModel(model=UserModel, actions=[send_welcome]))
```

The handler receives an `AdminActionContext`:

| Field | What it is |
| --- | --- |
| `ids` | Identities of the checked rows. |
| `repository` | The model's `BaseRepository`, on the request session. |
| `db_session` | The DB session (for work beyond the repository). |
| `request` | The inbound request. |
| `session` | The authenticated admin session. |
| `principal` | The admin user row that triggered the action. |

Return an `AdminActionResult(message, category="success"|"error"|"warning")`
to flash a banner on the list view (or `None` for no banner). The
function stays **directly callable/testable** — the decorator only
attaches metadata. Use `name=` to pin the identifier (default: the
function name) and `dangerous=True` to mark a destructive action.

The panel **commits** the request session when the handler returns, with or
without `secrets` in the result, and rolls it back when the handler raises.
So the action may change the row straight on the session, with no commit of
its own:

```python
from tempest_fastapi_sdk import AdminActionContext, AdminActionResult, admin_action

from src.db.models import UserModel


@admin_action(label="Confirmar e-mail")
async def verify_email(ctx: AdminActionContext) -> AdminActionResult:
    """Mark the e-mail confirmed; the panel commits at the end."""
    for user_id in ctx.ids:
        user = await ctx.db_session.get(UserModel, user_id)
        if user is not None:
            user.is_active = True
    return AdminActionResult(f"{len(ctx.ids)} conta(s) confirmada(s).")
```

!!! warning "Before 0.366.0, an action without `secrets` was not committed"
    The commit happened only on the `secrets` branch: an action that wrote
    through the session showed the success flash and lost the write at the
    end of the request (#784).

Each option of the bulk-action select carries its own confirm prompt. A
plain action (and Activate/Deactivate) asks "Apply the selected action to the
checked rows?"; a `dangerous=True` action (and the built-in Delete) names the
action and warns there is no way back — `Run "Recusar e devolver" on the
checked rows? This cannot be undone.`. The label goes in a `data-confirm`
attribute escaped by the template, never interpolated into JavaScript, so
quotes or `<` in the label do not break the page.

### Show a secret once (`secrets=`)

Some actions **create a credential**: an API key, an integration token, a
temporary password. The operator has to see the value once, and the flash
cannot carry it: it comes back through the redirect's query string
(`?flash=...`), which puts the value in the browser history, the access
log and the next page's `Referer`.

Return it in `AdminActionResult(secrets=[...])`, as an `AdminSecret`:

```python
from tempest_fastapi_sdk import (
    AdminActionContext,
    AdminActionResult,
    AdminModel,
    AdminSecret,
    admin_action,
    generate_opaque_token,
)

from src.admin import site
from src.db.models import ApiKeyModel, UserModel


@admin_action(label="Issue API key")
async def issue_api_key(ctx: AdminActionContext) -> AdminActionResult:
    """Create one key per selected user and show each one once."""
    users = await ctx.repository.list(filters={"id": ctx.ids})
    secrets: list[AdminSecret] = []
    for user in users:
        plaintext, digest = generate_opaque_token()
        ctx.db_session.add(ApiKeyModel(user_id=user.id, key_hash=digest))
        secrets.append(AdminSecret(f"Key for {user.email}", plaintext))
    return AdminActionResult(f"{len(secrets)} key(s) issued.", secrets=secrets)


site.register(AdminModel(model=UserModel, actions=[issue_api_key]))
```

With `secrets`, the panel **does not redirect**. The `POST`'s own response
is a page with the message and each value in a read-only field (focusing it
selects the text for copying), plus a button back to the list. What changes
compared to the flash:

- **Nothing reaches a URL or a header.** The value only exists in that
  response body.
- **`Cache-Control: no-store`** and `Pragma: no-cache`: neither the browser
  nor a proxy keeps a copy of the page. Reloading resubmits the `POST`,
  but the list form carries a single-use token and the resubmission does
  not run the action again (details in
  [Reloading does not issue again](#reloading-does-not-issue-again)).
- **`Referrer-Policy: no-referrer`**: the link back does not carry the
  action URL.
- **The request's session is committed before the page renders.** The
  `ctx.db_session.add(...)` above needs no commit of its own, and a secret
  on screen is never one whose row was rolled back.
- **`repr(AdminSecret)` leaves the value out**, so logging the result does
  not leak the secret.

!!! warning "Store only the digest"
    The panel shows the plaintext once, but storing it is your call.
    Persist the `digest` from `generate_opaque_token()` (SHA-256) and check
    an incoming key with `verify_opaque_token`. Put the digest column in
    `__secret_columns__`, so no panel screen displays it.

An action without `secrets` is unchanged: flashed on the list view, through
a redirect.

### Ask for input before running (`schema=`) *(v0.366.0+)*

Some actions need a value from the operator: the **reason** for an audited
access, an amount, a date. Pass a Pydantic model in `schema=` and the
handler receives `(data, ctx)`:

```python
from pydantic import BaseModel, Field

from tempest_fastapi_sdk import AdminActionContext, AdminActionResult, admin_action

from src.db.models import UserModel


class VerifyEmailInput(BaseModel):
    """What the operator fills in before the action runs."""

    reason: str = Field(min_length=10, max_length=500)


@admin_action(label="Confirmar e-mail", schema=VerifyEmailInput)
async def verify_email(
    data: VerifyEmailInput, ctx: AdminActionContext
) -> AdminActionResult:
    """Confirm the checked accounts and record the reason."""
    for user_id in ctx.ids:
        user = await ctx.db_session.get(UserModel, user_id)
        if user is not None:
            user.is_active = True
    return AdminActionResult(f"{len(ctx.ids)} confirmada(s): {data.reason}")
```

The flow, measured in `tests/admin/test_custom_action_flow.py`:

1. Picking the action on the list **runs nothing**: `POST /m/{slug}/bulk`
   answers `200` with the form generated from the schema (the same generator
   as `create_handler=`), the text `2 row(s) selected.`, the ids in hidden
   fields and a single-use nonce.
2. An invalid submission answers `400` with the field error
   (`String should have at least 10 characters`) and what was typed; the
   handler does not run and the nonce is not spent.
3. A valid submission runs the handler with the validated data and the ids,
   commits and returns to the list with the flash (`303`). Resubmitting the
   same form does not run it again: the nonce is spent, and the panel says
   `This form was already submitted; it did not run again.`.
4. An `AppException` from the handler rolls back and re-renders the form
   with the message and what was typed, with `400`.

An action without `schema=` is unchanged: it runs straight from the list.

### Create through a service (`create_handler=`)

An API key is not born from columns: the service generates the secret,
stores only the hash and shows the plaintext once. The column form would ask
for `key_hash` and `key_prefix` typed by hand — wrong. Moving the issuance to
a bulk action on the **users** list works, but whoever opens **Api Keys**
finds no button at all, and the action has no fields for name and expiry.

`create_handler` swaps the column form for a form generated from a
**Pydantic schema** and a handler that receives the validated schema:

```python
from uuid import UUID

from pydantic import BaseModel, Field
from tempest_fastapi_sdk import (
    AdminActionContext,
    AdminActionResult,
    AdminCreateHandler,
    AdminModel,
    AdminSecret,
    AdminSite,
    generate_opaque_token,
)

from src.db.models import ApiKeyModel


class ApiKeyAdminCreateSchema(BaseModel):
    name: str = Field(title="Key name", min_length=3)
    expires_in_days: int | None = Field(default=None, title="Expires in (days)")
    user_id: UUID | None = Field(default=None, title="User")


async def issue_api_key(
    data: ApiKeyAdminCreateSchema, ctx: AdminActionContext
) -> AdminActionResult:
    plaintext, digest = generate_opaque_token()
    ctx.db_session.add(
        ApiKeyModel(name=data.name, key_hash=digest, user_id=data.user_id)
    )
    return AdminActionResult("Key issued", secrets=[AdminSecret(data.name, plaintext)])


site = AdminSite(title="Admin")
site.register(
    AdminModel(
        ApiKeyModel,
        autocomplete_fields=["user_id"],
        create_handler=AdminCreateHandler(
            schema=ApiKeyAdminCreateSchema,
            handler=issue_api_key,
            label="New API key",
        ),
    )
)
```

- The list button becomes **+ New API key** and opens the schema's form:
  `title` is the label, the type picks the control (an enum is a select,
  `int` a number, `SecretStr` a password), a field without a default is
  required. A field whose name is in `autocomplete_fields` uses the same FK
  search as the column form.
- A validation error re-renders with `400`, each field's message and what
  was typed. An `AppException` raised by the handler comes back as a form
  error, also keeping what was typed, and the session is rolled back.
- With `secrets`, the answer is the `POST`'s own response, as in the action
  above. Without them, the panel redirects to the list with the message.
- `create_handler` replaces the column form: `can_create=True` alongside it
  raises `ValueError` at construction.

#### Reloading does not issue again

The form carries a single-use token, spent right before the handler runs. A
reload of the secret page resubmits the `POST`, and the panel answers `303`
to the list with the notice *This form was already submitted; it did not run
again.*, without calling the handler. Measured on Chromium: the reload
resubmitted the `POST`, landed on the list with the notice, and the table
held **one** key.

The same holds for the list's bulk actions, which now carry the token. A
`POST` without the token (a script, a template of your own) runs as before,
once per submission. The token is stored in the same backend as the session
revocations: with the default `InMemoryThrottleBackend` it holds per
process; behind more than one worker, pass the Redis client to
`AdminSessionRevocations` so the resubmission is refused on any of them.

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
[Wallet » Completing or refusing a withdrawal by hand](wallet.md#completing-or-refusing-a-withdrawal-by-hand-manualpayoutservice).

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
[Mercado Pago: testing webhooks » Manual withdrawal](mercado-pago-webhooks.md#manual-withdrawal).

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

## Audit history in the detail (`audit_model=`)

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

### Recent activity on the dashboard (`recent_activity=`)

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
### Undo from the history (Restore)

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

## Dashboard: business-metric cards (`dashboard_cards=`)

The dashboard already shows CPU/RAM/counters. For **your business**
metrics — orders today, revenue vs last week, users by plan — pass
`dashboard_cards`. Each card is a `MetricCard(label, compute)` where
`compute` is an async function that takes the session and returns one of
three types:

```python
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    AdminSite,
    MetricCard,
    MetricPartition,
    MetricTrend,
    MetricValue,
)

from src.db.models import OrderModel
from src.db.repositories import OrderRepository


async def orders_today(session: AsyncSession) -> MetricValue:
    total = await OrderRepository(session).count(filters={"start_in": date.today()})
    return MetricValue(total, unit="orders")


async def revenue_between(session: AsyncSession, start: date, end: date) -> float:
    """Sum the revenue of the orders created in [start, end)."""
    result = await session.execute(
        select(func.coalesce(func.sum(OrderModel.total), 0)).where(
            func.date(OrderModel.created_at) >= start,
            func.date(OrderModel.created_at) < end,
        )
    )
    return float(result.scalar_one())


async def revenue_trend(session: AsyncSession) -> MetricTrend:
    tomorrow = date.today() + timedelta(days=1)
    this_week = tomorrow - timedelta(days=7)
    last_week = this_week - timedelta(days=7)
    return MetricTrend(
        value=await revenue_between(session, this_week, tomorrow),
        previous=await revenue_between(session, last_week, this_week),
        unit="BRL",
    )


async def users_by_plan(session: AsyncSession) -> MetricPartition:
    return MetricPartition(segments=[("free", 120), ("pro", 30), ("enterprise", 4)])


site = AdminSite(
    title="Shop",
    dashboard_cards=[
        MetricCard("Orders today", orders_today, help_text="since midnight"),
        MetricCard("Revenue", revenue_trend),
        MetricCard("Users by plan", users_by_plan),
    ],
)
```

- **`MetricValue(value, unit=None)`** — a big number.
- **`MetricTrend(value, previous, unit=None)`** — a number + a ▲/▼ arrow
  and the percentage change vs the previous period (`delta` / `pct` /
  `direction` computed for you).
- **`MetricPartition(segments=[(label, value), ...])`** — a breakdown
  with proportional bars; `total` summed automatically.

The cards render at the top of the dashboard, computed on each load. A
card whose `compute` raises is **skipped** — a broken metric never
blanks the page — and the failure is logged with its traceback
(`logger.exception` on the `tempest_fastapi_sdk.admin.router` logger,
naming the card's `label`), so the card missing from the page shows up in
the log.

## Indicator areas: schema + service (`metrics_areas=`)

`MetricCard` receives the session and queries the database directly, card
by card. When your service **already knows** how to collect the
indicators, repeating the query in the admin is double work — and skips
the service layer. `MetricsArea` is the other shape: you hand over **a
schema with one field per indicator** and **a service whose method takes
no argument**, and the admin generates a page with one card per field,
listed under **Metrics** in the sidebar.

```python
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AdminSite, BaseUserModel, MetricsArea, Threshold


class UserModel(BaseUserModel):
    __tablename__ = "users"


class RideMetricsSchema(BaseModel):
    messages_to_route: float = Field(
        title="Messages until the route",
        description="Average messages before the user reached the route they wanted.",
    )
    accesses: int = Field(title="Accesses")
    accounts_created: int = Field(title="Accounts created")
    rides_created: int = Field(title="Rides created")


class RideMetricsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def collect(self) -> RideMetricsSchema:
        accounts = await self.session.scalar(select(func.count()).select_from(UserModel))
        return RideMetricsSchema(
            messages_to_route=4.2,
            accesses=1200,
            accounts_created=accounts or 0,
            rides_created=87,
        )


site = AdminSite(
    title="Transport",
    metrics_areas=[
        MetricsArea(
            "Rides",
            RideMetricsSchema,
            provider=RideMetricsService,
            method="collect",
            thresholds={
                "messages_to_route": Threshold(green=3, yellow=6, lower_is_better=True),
                "accounts_created": Threshold(green=100, yellow=40),
                "rides_created": Threshold(green=100, yellow=40),
            },
            units={"rides_created": "rides"},
        )
    ],
)
```

The page lives at `/admin/metrics/rides` (the slug comes from the title).
Piece by piece:

- **`provider`** builds the service from the admin's session. The class
  itself works when its `__init__` takes the session; a composition
  function (`build_ride_metrics_service`) works too. Sync or `async`.
- **`method`** is called **with no argument** and returns an instance of
  `schema`. Anything else becomes an error on the page.
- **Each field becomes a card**: `title` is the label (the field name when
  missing), `description` the line under the value. A number becomes a
  value card; a field typed `MetricValue`, `MetricTrend` or
  `MetricPartition` renders as that card.
- **`units`** puts the unit after the number.

### Thresholds: green, yellow, red

`Threshold(green=, yellow=)` paints the card of a numeric field (or the
current value of a `MetricTrend`) with a border and a badge — **OK**,
**Attention** or **Critical**:

| `Threshold(green=100, yellow=40)` (higher is better) | Band |
| --- | --- |
| `>= 100` | green |
| `>= 40` and `< 100` | yellow |
| `< 40` | red |

With `lower_is_better=True` the comparison flips — `Threshold(green=3,
yellow=6, lower_is_better=True)` is green up to 3, yellow up to 6 and red
above. The exact limit always lands in the better band. The colour comes
from the theme tokens (`--tempest-success`, `--tempest-warning`,
`--tempest-danger`), so light and dark stay consistent.

!!! tip "Fail early, not an uncoloured card"
    `thresholds` or `units` naming a field the schema does not have raises
    `ValueError` when the `MetricsArea` is built, and limits in the wrong
    order for the direction (`green < yellow` with higher-is-better) raise
    when the `Threshold` is built. A typo never becomes a card that just
    does not paint.

### When the service fails

If the `provider` or the method raises, the page answers `200` with a
notice in place of the cards, and the traceback goes to the log
(`logger.exception` on `tempest_fastapi_sdk.admin.router`, naming the
area). A broken service never turns the panel into a `500`.

### Who sees it

The area is the `AdminSurfaceEnum.METRICS` surface: with a
`surface_policy`, whoever the policy denies gets `403` on the page and no
links in the sidebar.

## Geographic distribution map (`location_maps=`) *(v0.367.0+)*

With [`BRLocationMixin`](geo.md#a-users-city-and-state-brlocationmixin-v03670)
on the model, the panel draws **where** the rows are: a map of Brazil with
each state shaded by its count and one bubble per city, the legend, the
state and city tables, and how many rows have no location yet. Register one
`BRLocationMap` per map in `AdminSite(location_maps=[...])`:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import AdminModel, BRLocationMap
from tempest_fastapi_sdk.geo import BRLocationMixin


class UserModel(BRLocationMixin, BaseUserModel):
    """Users with the city and state they live in."""

    __tablename__ = "users"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./painel.db")
site = AdminSite(
    title="Painel",
    location_maps=[
        BRLocationMap("Usuários por região", UserModel, value_label="usuários"),
        BRLocationMap(
            "Usuários ativos",
            UserModel,
            where=lambda: UserModel.is_active.is_(True),
            value_label="usuários",
        ),
    ],
)
site.register(AdminModel(UserModel))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the tables and a few users spread over three cities."""
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        admin = UserModel(email="admin@example.com", hashed_password="", is_admin=True)
        admin.set_password("troque-esta-senha")
        session.add(admin)
        places = [("São Paulo", "SP"), ("São Paulo", "SP"), ("Campinas", "SP")]
        places.append(("Recife", "PE"))
        for index, (city, state) in enumerate(places):
            user = UserModel(email=f"user{index}@example.com", hashed_password="")
            user.set_br_location(city, state)
            session.add(user)
        await session.commit()
    yield
    await db.disconnect()


app = FastAPI(lifespan=lifespan)
app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key="troque-por-um-segredo-de-32-bytes-ou-mais",
        cookie_secure=False,
    )
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

Each map becomes an entry in the sidebar's **Maps** section, at
`/admin/maps/<slug>` (the slug comes from the title, accents dropped:
`usuarios-por-regiao`). Measured with the example, by requests to the panel:

- the sidebar lists `/admin/maps/usuarios-por-regiao` and
  `/admin/maps/usuarios-ativos`;
- the page answers `200` with Total `5`, With location `4` and No location
  `1` (the administrator has no city);
- the bubbles, largest first: `São Paulo (SP): 2 usuários`,
  `Campinas (SP): 1 usuários`, `Recife (PE): 1 usuários`;
- each state's tooltip (`SP: 3 usuários`, `PE: 1 usuários`,
  `BA: 0 usuários`) and the legend with each shade's range.

What each argument does:

- `where=` takes a function that builds the filter on every request
  (`lambda: UserModel.is_active.is_(True)`), applied to every count.
- `city_limit=` (default 50) caps the bubbles and the table at the largest
  cities.
- `value_label=` is the word after each number in the tooltips and tables.

The map is an SVG built on the server, with the state outlines shipped in
the package: the page makes **no external request** (no map script, no tile
server). Its colours come from the theme variables, so dark mode
(`AdminTheme(dark_mode=True)`) already applies. Validated in a real browser
at 1366 px and 390 px, in the light and dark themes: on a phone the map
takes the full width and the tables move below it.

!!! info "Who sees it"
    The page asks the `access_policy` for `VIEW` on the model, of the
    model's registered `AdminModel` (or of a throwaway `AdminModel(model)`
    when it is not registered). Denied, the page answers `403` and leaves
    the sidebar.

!!! note "When the count fails"
    If `where=` or the database raises, the page still renders, with the
    notice `This map could not be counted` in place of the map and the
    traceback in the log — like the indicator areas.

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
   a `unique` column) and what the [`row_scope`](#row-scope-row_scope)
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
[`m2m_fields`](#many-to-many-in-the-form-m2m_fields) links the principal
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
  opened before reports the [conflict](#concurrent-edits-optimistic-lock).

Needs `can_edit` and `EDIT`. The value step carries a single-use nonce:
reloading the answer does not apply it again.

!!! warning "No repository signals"
    Like bulk activate/deactivate, the bulk edit is SQL Core: `PRE_SAVE` /
    `POST_SAVE` handlers do not fire. Each name in `bulk_edit_fields` must be
    an editable form column (not an upload, a password or `readonly_fields`),
    checked at construction.

## Granular RBAC (`access_policy=`)

By default, anyone who logs into the admin (`is_admin`) can do
everything each `AdminModel`'s `can_*` flags allow. To give an admin
access to only **some** models/actions — a "support" role that views
orders but can't delete, an "editor" that only touches content — pass an
`access_policy` to `make_admin_router`:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminModel,
    AdminPermissionEnum,
    UserModelAuthBackend,
    make_admin_router,
)

from src.admin import site
from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import AuditLog, UserModel

app = FastAPI()


def policy(user: UserModel, admin: AdminModel, action: AdminPermissionEnum) -> bool:
    if user.role == "superadmin":
        return True
    if user.role == "support":
        return action is AdminPermissionEnum.VIEW       # read-only
    return admin.model is not AuditLog              # editor: everything but AuditLog


app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.JWT_SECRET,
        access_policy=policy,
    ),
)
```

The policy is asked `(principal, admin, action)` for **every** action —
`VIEW` / `CREATE` / `EDIT` / `DELETE`, and `IMPERSONATE` on a model with
`impersonate=` (see [Log in as a user](#log-in-as-a-user-impersonate)). Denying yields `403`; denying
`VIEW` also **hides** the model from the dashboard and the sidebar nav,
and denying create/edit/delete hides the matching buttons. Enforced
across list, detail, create, edit, delete, bulk (delete → `DELETE`, rest
→ `EDIT`), export, import and FK autocomplete.

It also covers what one model's page shows of **another** model:

- the inline table on a parent's detail view appears only with `VIEW` on
  the child — a child with no registered `AdminModel` is asked about
  through a throwaway `AdminModel(Child)`, so a policy that decides by
  `admin.model` answers the same way;
- on an editable inline, the add row needs `CREATE` (and the child's
  `can_create`) and the delete box needs `DELETE`; a formset `POST` that
  asks for a denied action answers `403` and writes nothing;
- the FK `<select>`, the list view's FK filter and autocomplete need
  `VIEW` on the referenced model. Without it, the FK field becomes a text
  input and autocomplete answers `403`.

!!! info "Composes with the `can_*` flags"
    The policy is **additive** to the `AdminModel`'s
    `can_create`/`can_edit`/`can_delete` flags — both must allow.
    Without `access_policy`, nothing changes (every logged-in admin does
    everything), with one exception: `IMPERSONATE` only comes from a policy
    that grants it. Sync or `async`.

### One action without `EDIT` on the model (`action_policy=`) *(v0.366.0+)*

By default every custom action asks the `access_policy` for `EDIT` on the
model. On a read-only panel, granting **one** action ("Confirmar e-mail")
meant granting `EDIT` on the whole model — and, if someone turned `can_edit`
on by mistake, that same permission opened free editing of every field. Nor
could one role get one action without another.

Pass an `action_policy`: it is asked `(principal, admin, AdminAction)` for
each custom action, and the action then needs `VIEW` on the model plus its
approval, not `EDIT`:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminAction,
    AdminModel,
    AdminPermissionEnum,
    UserModelAuthBackend,
    make_admin_router,
)

from src.admin import site
from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import UserModel

app = FastAPI()


def policy(user: UserModel, admin: AdminModel, action: AdminPermissionEnum) -> bool:
    """Read-only panel: VIEW on every model, nothing else."""
    return action is AdminPermissionEnum.VIEW


def action_policy(user: UserModel, admin: AdminModel, action: AdminAction) -> bool:
    """Grant only the confirm-e-mail action, only to support."""
    return user.role == "support" and action.name == "verify_email"


app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.JWT_SECRET,
        access_policy=policy,
        action_policy=action_policy,
    ),
)
```

Measured in `tests/admin/test_custom_action_flow.py`:

- the granted action runs (`303`) and the denied one answers `403`, on the
  same model;
- the list's menu shows only the actions the policy grants — and, along the
  way, Activate / Deactivate and Delete appear only when the model enables
  them (`can_edit` + `can_toggle_active`, `can_delete`) **and** the principal
  has `EDIT` / `DELETE`; with no permitted action, the bulk form is gone;
- with `EDIT` denied, the edit form stays `403`, even with the action
  granted.

Without `action_policy`, nothing changes: a custom action needs `EDIT`, as
before.

### SQL console, tasks and logs (`surface_policy=`)

The SQL console, the task panel, the logs page and the storage browser
are not about one model, so `access_policy` has nothing to ask about. `surface_policy`
decides instead, asked `(principal, AdminSurfaceEnum)`:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminModel,
    AdminPermissionEnum,
    AdminSurfaceEnum,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import SqlShellService

from src.admin import site
from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import UserModel

app = FastAPI()


def policy(user: UserModel, admin: AdminModel, action: AdminPermissionEnum) -> bool:
    return user.role == "superadmin" or action is AdminPermissionEnum.VIEW


def surfaces(user: UserModel, surface: AdminSurfaceEnum) -> bool:
    return user.role == "superadmin"


app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.JWT_SECRET,
        access_policy=policy,
        surface_policy=surfaces,
        sql_shell=SqlShellService(db),
        show_logs=True,
    ),
)
```

A denial answers `403` on every route of the surface (the console's
`POST`, task cancel, the logs export and the storage writes and `raw` route
included) and drops its sidebar
entry.

!!! warning "With `access_policy`, the surfaces are closed by default"
    With neither policy, every admin opens every surface, as before.
    With `access_policy` and **no** `surface_policy`, nobody does: the
    console reads any table the `SqlShellPolicy` allows, and an operator
    restricted to some models would read the rest through it. Pass a
    `surface_policy` to give the surfaces back to whoever should have them.

## Row scope (`row_scope=`)

`access_policy` answers yes or no for the **whole model**. Multi-tenant
needs more: company A's support may see orders, but only company A's.
Working around it with one panel per company or a hand-filtered
repository is where data leaks — one route forgetting the filter is
enough. `row_scope=` puts the filter in the panel:

```python
from typing import Any

from sqlalchemy import ColumnElement, false, true

from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import TicketModel, UserModel


def own_company(user: UserModel, admin: AdminModel[Any]) -> ColumnElement[bool]:
    if user.is_superuser:
        return true()
    if user.company_id is None:
        return false()
    return TicketModel.company_id == user.company_id


site.register(AdminModel(model=TicketModel, row_scope=own_company))
```

The function receives the signed-in principal and the `AdminModel`
itself, and returns a SQLAlchemy condition. "Every row" is said
explicitly, with `sqlalchemy.true()`. It may be `async` (to look the
tenant up in another table, for instance). The condition is ANDed into
**every** route that reads or writes a row of the model:

- list, count (in the header and on the dashboard card) and export;
- detail, the edit form and its `POST`, delete and the upload download;
- bulk actions — delete, activate/deactivate — and custom actions: the
  selected ids outside the scope are dropped as if they did not exist;
- autocomplete, foreign-key `<select>` and foreign-key filter **when this
  model is the target**, and the inline table when it is the **child**;
- the editable inline formset (the parent and each child are read in
  scope);
- the CSV and `.xlsx` import preview and confirm, and the `.xlsx` export;
- duplicate (the source is read in scope), bulk edit, the `m2m_fields`
  options and links and the `related_counts` count (with the target's or
  child's scope), and the recent-activity feed.

A row outside the scope answers `404` — the same as a row that does not
exist, so the response never confirms it is there. On delete and the
built-in bulk actions, the condition is inside the `DELETE` / `UPDATE`
statement itself, not only in the read before it.

!!! danger "Returning `None` does not open everything"
    A scope function that forgets its `return` hands back `None`. The panel
    treats that as an error: it raises `AdminRowScopeError`, logs the model
    name and the request answers `500` — it fails closed instead of opening
    every row. To let everything through, return `true()`.

    And mind the principal without a tenant: `TicketModel.company_id ==
    user.company_id` with `company_id` `None` becomes `IS NULL`, which
    matches every row without a company. If that user should see nothing,
    return `false()` first, as in the example.

### On writes

The rule holds for the result: a row that is **created**, **edited** or
**imported** must still match the scope after the write. The panel writes
inside a transaction, counts the row against the condition in that same
transaction and, if it left, rolls back and returns the form with `This
record would fall outside the rows you may manage; nothing was saved.` —
company A's operator cannot create a company B ticket, nor "move" one of
theirs there by changing `company_id`. The same applies to:

- **`create_handler`**: the schema fields named like a foreign-key column
  go through the foreign-key check below before the handler runs. Then
  every row of the model the handler writes through the session —
  inserted **or changed**, as in an upsert that edits an existing row — is
  counted against the scope before the commit; outside it, rollback.
  Escaping the check: writes through SQL Core (`insert()`, `update()`,
  `bulk_create_values`), a service that opens a session of its own, and
  rows of **other** models the handler writes. A handler that commits on
  its own has already made the row durable: the panel cannot undo it,
  logs it at `ERROR` with the model, and says on the form that the record
  **was** saved outside the scope, instead of claiming nothing was saved.
- **bulk activate/deactivate**: if the new `is_active` takes any row out
  of the scope (a scope that filters on it), nothing changes and the list
  comes back with the warning.
- **bulk edit** (`bulk_edit_fields=`): the same rule — the `UPDATE` carries
  the scope in its `WHERE`, and a value that takes any row out of the scope
  rolls the whole transaction back.
- **many-to-many** (`m2m_fields=`): every posted id must be within the
  target's scope, and a link the operator does not reach is kept.
- **foreign keys**: on create, edit, inline or import, the value of a
  foreign key whose target model has a `row_scope` must be a row the
  principal reaches there. Outside the scope the field gets `Select a
  valid choice; that record does not exist.` — the same error as an id
  that does not exist, so a forged `POST` learns no other tenant's ids.
  The column is checked against its first `ForeignKey`: in a composite
  `ForeignKeyConstraint`, or on a column with several foreign keys, only
  that target is verified.

An uploaded file stored by a create or edit that ends up refused is
deleted (one attempt; a failure goes to the log).

On the edit form, a foreign key pointing at a row the operator does
**not** reach (or past `FK_OPTION_CAP`) stays selected: the `<select>`
gains one option with the current value, labelled with the id alone, and
saving without touching it keeps the reference — before, the browser
posted the first option and the save rewrote the reference silently.

In the detail's audit panel, the author of a change is shown by name when
the user model is not registered on the site, or has no `row_scope`. With
a `row_scope`, the name shows only if the principal has `VIEW` on that
model and reaches that user through the scope; otherwise only the id.

On delete, when the `DELETE ... AND <scope>` removes nothing (the row
left the scope after it was read), the whole block is rolled back —
whatever a `PRE_DELETE` handler wrote in the session is undone —, the
answer is `404` and no `POST_DELETE` fires. `POST_DELETE` receives the
instance outside the session, as with `BaseRepository.delete`, so it
reads the columns even with `expire_on_commit=True`.

!!! warning "What the scope does not cover"
    - `row_scope` applies to the `/m/{slug}/...` routes and to what they
      show of another model. What runs your code or free SQL stays out:
      the dashboard cards (`dashboard_cards=`), indicator areas, maps
      (`location_maps=`) and the SQL console.
    - In a custom action, `ctx.ids` are already filtered, but
      `ctx.repository` is the model's unfiltered repository and the panel
      does not check the outcome: the action can read beyond the ids and
      can **move** a row out of the scope. That is its own business.
    - The storage browser (`/storage`, the `STORAGE` surface) serves any
      key in the bucket. If the scoped model's uploads live in the same
      bucket, whoever has the surface bypasses the upload download's scope
      — deny `STORAGE` to those roles in `surface_policy`.
    - Create and import remain a `UNIQUE` oracle across tenants: a code
      that exists in company B conflicts for company A. The way out is a
      composite `UNIQUE` constraint with the tenant column
      (`UniqueConstraint("company_id", "code")`).

!!! tip "Together with `access_policy`"
    The two compose: `access_policy` decides **whether** the principal
    opens the model and which action it takes; `row_scope` decides
    **which rows**. A support role with `VIEW` and a per-company scope sees
    only its own company's list and gets `404` on any other's detail.

## Log in as a user (`impersonate=`)

Support needs to see the screen the customer sees. The admin does not know
how your service signs users in, so the job is split: you hand over a
function that turns `(operator, target)` into the URL that opens the app as
the target, and the panel owns the rest — the permission, the
confirmation, the refusal of a target that is itself an admin, the record
of who entered as whom, and the redirect.

For a service whose login issues `JWTUtils` access tokens, the handler is
ready-made:

```python
from datetime import timedelta
from typing import Any

from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminModel,
    AdminPermissionEnum,
    JWTUtils,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import make_jwt_impersonation_handler

from src.admin import site
from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import AuditLog, UserModel

app = FastAPI()
tokens = JWTUtils(settings.JWT_SECRET)

site.register(
    AdminModel(
        model=UserModel,
        audit_model=AuditLog,
        impersonate=make_jwt_impersonation_handler(
            tokens,
            redirect_url="https://app.example.com/impersonate#token={token}",
            ttl=timedelta(minutes=10),
        ),
    )
)


def policy(
    user: UserModel, admin: AdminModel[Any], action: AdminPermissionEnum
) -> bool:
    if action is AdminPermissionEnum.IMPERSONATE:
        return user.role == "support-lead"
    return True


app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.ADMIN_SECRET,
        access_policy=policy,
    ),
)
```

The signed token carries `sub` (the account's `id`), `typ` = `"access"` — so
`make_bearer_token_dependency` and `make_jwt_user_dependency` on the same
secret accept it like any access token —, the target's `email` when it has
one, and `act = {"sub": "<operator id>"}`, the RFC 8693 actor claim: the
app knows the session is an impersonation and who is behind it. The
default lifetime is `ADMIN_IMPERSONATION_TTL` (15 minutes). `redirect_url`
must contain `{token}` (otherwise `ValueError` at construction); prefer
putting the token in the fragment (`#`), which the browser sends neither to
the app's server nor in the `Referer`.

Apart from `act`, it is an ordinary access token: **every** route that
accepts the user's own token accepts this one. For a route that must not run
under an impersonated session — changing the password, moving money —,
`token_actor(claims)` returns the operator behind it (`None` on the user's
own session; a malformed `act` comes back as `"unknown"`, never as `None`):

```python
from typing import Any

from fastapi import Depends

from tempest_fastapi_sdk import ForbiddenException, token_actor

from src.api.dependencies.auth import bearer


async def own_session_only(
    claims: dict[str, Any] = Depends(bearer),
) -> dict[str, Any]:
    if token_actor(claims) is not None:
        raise ForbiddenException(message="Unavailable while impersonating.")
    return claims
```

When `impersonate=` sits on a model that is not the account — a profile, a
membership —, say which account it stands for with `impersonate_account=`
(sync or `async`). The handler receives that account, and the admin refusal
runs on it. The account must have an `id` (that is what goes into the
credential) and, when its class has a registered admin with a `row_scope`,
be inside that scope for the operator — otherwise a membership of company A
would hand out a user of company B. `None`, an account without `id` or one
outside the scope answer `404`, before any audit. A sync resolver runs
inside `AsyncSession.run_sync`, so it may read a lazy relationship
(`membership.user`); an `async` one must load what it reads (`selectinload`,
`AsyncAttrs.awaitable_attrs`):

```python
from tempest_fastapi_sdk import AdminModel, JWTUtils
from tempest_fastapi_sdk.admin import make_jwt_impersonation_handler

from src.admin import site
from src.core.settings import settings
from src.db.models import MembershipModel

site.register(
    AdminModel(
        model=MembershipModel,
        impersonate=make_jwt_impersonation_handler(
            JWTUtils(settings.JWT_SECRET),
            redirect_url="https://app.example.com/impersonate#token={token}",
        ),
        impersonate_account=lambda membership: membership.user,
    )
)
```

Another sign-in mechanism (magic link, cookie session) is just another
function of the same shape — `handler(operator, account) -> str`, sync or
`async`:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import UserModel
from src.services.magic_links import issue_magic_link


async def enter_as(operator: UserModel, target: UserModel) -> str:
    link = await issue_magic_link(target.id, actor_id=operator.id)
    return f"https://app.example.com/magic#link={link}"


site.register(AdminModel(model=UserModel, impersonate=enter_as))
```

As with the JWT, the credential goes in the fragment: in the path it would
land in the app server's access log.

!!! danger "The handler enters as the account it received"
    The admin-target refusal runs on the account the handler receives. A
    handler that enters as **something else** — `target.owner`, an id read
    from another table — goes around it and must refuse admin accounts on
    its own. Put `impersonate=` on the account's model, or use
    `impersonate_account=`.

The flow in the panel:

1. The row's detail gains a **Log in as** button.
2. It opens a confirmation page; confirming `POST`s the CSRF token and a
   single-use nonce — reloading the answer does not enter again, it goes
   back to the list with the repeated-submission notice.
3. With `audit_model`, the audit entry is written (committed) **before**
   the handler runs; if the write fails, the handler does not run — no
   credential is minted — and the request ends in an error, with no `303`.
4. The handler runs and the browser gets a `303` to the returned URL, with
   `Cache-Control: no-store` and `Referrer-Policy: no-referrer`. An
   `AppException` from the handler comes back to the confirmation with its
   message and is logged at `WARNING`. When the handler fails (any
   exception), a second entry, `admin.impersonate.failed` (reason in
   `context["reason"]`), says nothing was handed over; the timeline shows it
   as **log in as failed**.

!!! danger "`IMPERSONATE` never comes for free"
    Without an `access_policy` the admin allows everything — **except**
    impersonating: the button does not show and the route answers `403`.
    Acting as another account reaches past the admin's screens (and past
    its `row_scope` and audit), so only a policy that says yes to
    `AdminPermissionEnum.IMPERSONATE` grants it. Beware of a policy written
    as "everything but `DELETE`": it grants `IMPERSONATE` too. `EDIT` is not
    enough.

More rules on the way:

- **Scope.** The target must be inside the model's `row_scope`; outside it
  is `404`, as on every row route.
- **An admin is not a target.** An account that signs into the admin
  itself is refused (`403`, no form). Three questions go to the backend,
  any one refuses: `is_admin_account` on the row and on the resolved
  account (by default a truthy `is_admin`; on `UserModelAuthBackend`, also
  whatever your `is_authorized` answers — a backend that authorizes by role
  refuses that role), and `is_admin_identity(session, account_id)` on the
  `id` that goes into the credential. The last one catches a 1:1 profile
  sharing its primary key with an admin user: it asks `load_principal` and,
  when that finds nothing, `load_account` — which on `UserModelAuthBackend`
  reads the user row even when inactive or without MFA. On your own backend
  whose `load_principal` filters (inactive, MFA), **override
  `load_account`**: the default returns `None`, and an admin that
  `load_principal` drops would pass.
- **Record.** Every impersonation logs at `INFO` the operator, the model
  and the target's id — never the URL, which carries the credential. With
  `audit_model`, it also writes an `event` entry on the target, authored
  by the operator, **before** the handler (see step 3). With `AuditRequestMixin` on the table, the event
  (`admin.impersonate`), the IP and the user agent go to their own columns;
  without it, the name stays in `context["event"]`. The detail's timeline
  shows the entry as **logged in as**.

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

## Global search

When some model the operator can see has `search_fields`, the panel's top
bar gains a search box. It goes to `GET /admin/search?q=`, which looks for
the term in **every** model with `search_fields`, with the list search's
own condition (each word an `ILIKE`, in any of the fields), and shows up
to `ADMIN_SEARCH_RESULTS_PER_MODEL` (5) rows per model, each linking to its
detail. When there are more, the group's **See all** opens the model's
list already filtered with `?q=`. The cap is adjustable:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import UserModelAuthBackend, make_admin_router

from src.admin import site
from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import UserModel

app = FastAPI()

app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.ADMIN_SECRET,
        search_results_per_model=10,
    ),
)
```

The search honours what the rest of the panel honours: a model without
`VIEW` in the `access_policy` is left out, and each model is queried with
its own `row_scope` — a row outside the scope does not show. A model with
a `repository_class` is queried through the repository's `paginate` (one
`COUNT` per model), with the page capped at its `max_page_size`. An
empty term (or only spaces) renders the page without running a query, and a
term longer than `ADMIN_SEARCH_MAX_QUERY_LENGTH` (200) characters or with
more than `ADMIN_SEARCH_MAX_TERMS` (8) words answers `400` with a notice,
also without a query — each word becomes one `ILIKE` per field of every
model.

#### 4. Session security defaults

`SignedCookieSessionStore` uses `itsdangerous.TimestampSigner` (HMAC-SHA256) to sign a single cookie:

- `HttpOnly` always set.
- `Secure` flagged when `cookie_secure=True` (default; flip off in local HTTP dev).
- `SameSite=Lax` (`"lax"`/`"strict"`/`"none"` accepted).
- Default lifetime `8h`; expired or tampered cookies are rejected silently.
- The cookie is not enough on its own: every authenticated route reloads the principal through the `auth_backend` on each request. An account deleted, deactivated or stripped of `is_admin` after the login is sent to `/admin/login` (`303`) on the very next request, without waiting for the cookie to expire.
- `POST /admin/logout` ends the session on the server, not just in the browser: it marks the session's random id (`sid`, in the cookie) in an `AdminSessionRevocations`, and every request refuses a marked session. A copy of the cookie taken before the logout is sent to `/admin/login`. The default keeps the marks on an `InMemoryThrottleBackend`, per process, for the store's `max_age_seconds`. Behind more than one worker or replica, pass `make_admin_router(..., session_revocations=AdminSessionRevocations(redis_client))` (the same `redis.asyncio` client the throttle accepts), or a logout served by one process does not reach the others.
- The session is bound to the credential: at login the router writes into the cookie an HMAC (keyed with `secret_key`) of `AdminAuthBackend.credential_fingerprint(principal)` and checks it on every request. In `UserModelAuthBackend` the fingerprint covers `hashed_password` and `totp_secret`, so changing the password or re-enrolling MFA ends every session issued before it, on every replica, with no extra state. A custom backend returns `None` by default (no binding); override the method to opt in. Cookies issued before this version carry no HMAC and are refused once: the operator signs in again.
- Per-session CSRF token is generated at login and required by every session form POST (logout, create, edit, delete, bulk actions, SQL console runs, task cancellation).
- The login form runs before any session exists and carries a token of its own: `GET /admin/login` sets a signed 1 h cookie (`tempest_admin_login_csrf`, with a salt distinct from the session's) and repeats the token in a hidden field. The `POST` compares the two in constant time **before** the failure budget and the password, and refuses with `403` — the page comes back with a fresh token, and the refusal spends no attempt. Without it, a page on another site could make the operator's browser sign in to the **attacker's** account (login CSRF); `SameSite=Lax` does not prevent that, because it restricts the cookie the POST sends, not the one the response sets. The limit: the cookie is scoped to the admin prefix, so it cannot use the `__Host-` prefix, and a **sibling subdomain** under someone else's control (`evil.example.com` next to `admin.example.com`) can plant, for the parent domain, a signed cookie it got by loading the form itself. Serve the admin on a host without third-party subdomains.
- `secret_key` must be at least 32 bytes — short keys raise `ValueError` at construction time.

!!! danger "Login refused or looping? It's the cookie `Secure` flag over plain HTTP"
    If every `POST /admin/login` returns `403` with *The sign-in form expired,
    please try again*, the login form's token cookie is **not coming back**. If
    it returns `303` (looks like success), but the following
    `GET /admin/` redirects back to the login — forever — it is the session
    cookie that is not coming back. Either way the almost-certain cause is the
    same: `cookie_secure=True` while the admin is served over **plain HTTP** (no
    TLS terminator in front). The browser refuses to store a `Secure` cookie on a
    non-HTTPS connection, and both admin cookies (the login token's and the
    session's) follow `cookie_secure`.

    ```python
    # ❌ Tied to DEBUG: in production DEBUG=false → cookie_secure=True,
    #    but with no HTTPS in front the login loops.
    make_admin_router(..., cookie_secure=not settings.DEBUG)

    # ✅ Dedicated toggle, independent of DEBUG:
    make_admin_router(..., cookie_secure=settings.ADMIN_COOKIE_SECURE)
    ```

    **Right fix:** put HTTPS in front (nginx/Caddy terminating TLS) and keep
    `cookie_secure=True` — the admin session cookie must not travel in the clear.
    **Stopgap** only when the admin genuinely runs over HTTP (intranet, MVP):
    `cookie_secure=False`, aware the session ships without `Secure`. Don't tie
    this flag to `DEBUG` — turning debug on in production is worse than the
    original problem.

!!! warning "Browser says the login form is not secure? It's the scheme behind the proxy"
    With HTTPS in front and the proxy speaking HTTP to the app, the form's
    `action` comes out as `http://...` and the browser flags the password
    field as not secure. Set `SERVER_PUBLIC_SCHEME=https` and add
    `PublicSchemeMiddleware` — recipe in
    [Security](security.md#public-scheme-behind-a-proxy-server_public_scheme).
    Until then, `GET /admin/login` logs a `WARNING` saying so.

##### Signing in from a test

Since `POST /admin/login` requires the form's token, a test that posts the credentials alone gets `403`. `tempest_fastapi_sdk.testing` does what a browser does — loads the form, reads the hidden field (the cookie lands in the client's jar) and submits it:

```python
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk.testing import admin_login


async def test_dashboard_after_login(app: FastAPI) -> None:
    """Sign in through the form, as a browser does, and open the panel."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        response = await admin_login(client, "root@example.com", "hunter2")
        assert response.status_code == 303
        assert (await client.get("/admin/")).status_code == 200
```

With `TestClient`, use `admin_login_sync(client, ...)`. Both accept `httpx` and `httpx2` clients (Starlette's `TestClient` subclasses the latter when `httpx2` is installed) without a `cast`, and return the response in the type of the client you passed. Both take `prefix=` when the admin is not mounted at `/admin`, and `headers=` for, say, the trusted client-IP header.

!!! tip "`https://` in the `base_url`"
    With `cookie_secure=True` (the default), the admin cookies are `Secure`, and the test's HTTP client only sends them back over `https://`. Use `base_url="https://test"`, or mount the router with `cookie_secure=False` in the test.

##### Login failure budget

`POST /admin/login` counts wrong attempts against two budgets. The
identifier one is on by default — every admin already mounted gets the
protection with no configuration — and the IP one turns itself on as soon as
you say which header carries the client IP:

| Parameter | Key | Default | Cleared when |
| --- | --- | --- | --- |
| `login_throttle` | `login:<sha256 of the normalized identifier>` (`login_throttle_key`, the same as `/auth/login`) | 5 attempts / 900 s | the login succeeds |
| `login_ip_throttle` | `admin-login-ip:<client ip>` | 20 failures / 900 s **with** `trusted_ip_header`; off without it | never — it expires with the window |

Once either is spent, the form comes back as `429` with `Retry-After`
**before** the password is checked — the right password gets the same answer,
so the `429` is no oracle. An unknown identifier spends its budget the same
way, and the `429` does not tell which addresses exist. The order is
`make_auth_router`'s: the IP budget is read first (a blocked IP does not spend
the identifier's), and the identifier's is **reserved** before the password,
so a concurrent burst cannot all slip through a "not blocked yet" read.

```python
from redis.asyncio import Redis

from tempest_fastapi_sdk import (
    AttemptThrottle,
    UserModelAuthBackend,
    make_admin_router,
)

from src.admin.site import site
from src.api.dependencies import db
from src.core.settings import settings
from src.db.models import UserModel

redis: Redis = Redis.from_url(settings.REDIS_URL)

router = make_admin_router(
    site,
    db=db,
    auth_backend=UserModelAuthBackend(UserModel),
    secret_key=settings.JWT_SECRET,
    login_throttle=AttemptThrottle(redis, max_attempts=5, window_seconds=900),
    login_ip_throttle=AttemptThrottle(redis, max_attempts=20, window_seconds=900),
    trusted_ip_header="x-real-ip",
)
```

!!! warning "Without `trusted_ip_header`, the IP budget is off"
    Without the header the IP is the connection peer — behind nginx, nginx
    itself. An IP budget on there would be one budget for every operator, and
    twenty bad logins from **anyone** would lock everyone out for 15 minutes.
    So the default only turns it on with `trusted_ip_header` (the header your
    edge **overwrites**: `x-real-ip`, `cf-connecting-ip`). A
    `login_ip_throttle` passed explicitly always applies. The header's value
    must be **one** IP address (`parse_client_ip`); a list, a port, a name or
    arbitrary text falls back to the connection peer, so a client cannot mint
    a fresh budget per request.

!!! note "The per-identifier budget also locks out the account's owner"
    Whoever knows an operator's e-mail can spend their 5 attempts and keep
    them out for one window, even with the right password — the trade every
    per-account lock makes, and the same as `/auth/login`. The `429` clears
    by itself when the window expires.

!!! info "The default counts per process"
    The default budgets live in an `InMemoryThrottleBackend`: with more than
    one worker each has its own, and the ceiling becomes `5 * workers`. For a
    real ceiling pass an `AttemptThrottle` over Redis, as above. `False` turns
    a budget off (`login_ip_throttle=False`), for a service that already limits
    login in another layer.

#### 5. Plug in a custom auth backend

`AdminAuthBackend` is an ABC that is **generic in the principal** — the value `authenticate` returns and every other method consumes. Swap the default for LDAP / OAuth / external IAM by subclassing, and name your principal in the parameter:

```python
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AdminAuthBackend, AdminAuthError, GoogleOAuthClient

from src.core.settings import settings
from src.db.models import AdminModel

my_oauth_client = GoogleOAuthClient(
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    redirect_uri=settings.GOOGLE_REDIRECT_URI,
)


class OAuthAdminBackend(AdminAuthBackend[AdminModel]):
    """Trade the admin form's credential for an OAuth identity.

    OAuth has no password to check, so the form's `password` field
    carries the authorization code the provider redirected back with.
    The ABC names the parameter `password`; what this backend expects
    there is the code.
    """

    async def authenticate(
        self,
        session: AsyncSession,
        *,
        identifier: str,
        password: str,
    ) -> AdminModel:
        """Exchange the code, then accept only a known admin e-mail."""
        tokens = await my_oauth_client.exchange_code(password)
        identity = await my_oauth_client.fetch_user(tokens)
        if not identity.email_verified or identity.email != identifier:
            raise AdminAuthError("not an admin")
        result = await session.execute(
            select(AdminModel).where(AdminModel.email == identity.email)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise AdminAuthError("not an admin")
        return row

    async def load_principal(
        self,
        session: AsyncSession,
        principal_id: str,
    ) -> AdminModel | None:
        """Reload the admin row on every request."""
        return await session.get(AdminModel, UUID(principal_id))

    def principal_id(self, principal: AdminModel) -> str:
        """Return the identifier serialized into the cookie."""
        return str(principal.id)

    def display_name(self, principal: AdminModel) -> str:
        """Return what the admin header shows."""
        return principal.email
```

Pass the instance via `auth_backend=` and the rest of the admin pipeline (sessions, dashboard, list, detail) keeps working unchanged.

!!! tip "The `[AdminModel]` parameter is what makes the contract checkable"

    Without it the class resolves to `AdminAuthBackend[Any]` — how it behaved
    before v0.263.0, and still valid. What the parameter buys you is the error
    that shows up when `authenticate` returns one type and `display_name` reads
    another. Measured with both mypy and basedpyright: both reject a
    `display_name(self, principal: dict[str, str])` on a class declared
    `AdminAuthBackend[LdapEntry]` — mypy names Liskov outright
    (`This violates the Liskov substitution principle`), basedpyright reports
    `reportIncompatibleMethodOverride`.

!!! warning "An unparameterized subclass under `mypy --strict`"

    `mypy --strict` turns on `disallow_any_generics`, so `class MyBackend(AdminAuthBackend)`
    now reports `Missing type arguments for generic type "AdminAuthBackend"`.
    Runtime did not change — this is the type-checker only. Write your principal
    (`AdminAuthBackend[MyRow]`), or keep the exact previous behaviour with
    `AdminAuthBackend[Any]`. The `pyproject.toml` that `tempest new` writes sets
    `strict = true`.

##### Authorize by role and require MFA

When who gets into `/admin` is decided by a column of yours (`role ==
"STAFF"`) rather than `is_admin`, do not rewrite `authenticate` /
`load_principal` — that is where the timing-safe password check lives.
Override only `is_authorized`, which both consult:

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseUserModel, MFAMixin, UserModelAuthBackend


class UserModel(MFAMixin, BaseUserModel):
    """The service's account, with its own role."""

    __tablename__ = "users"

    role: Mapped[str] = mapped_column(String(16), default="MEMBER")


class StaffAdminBackend(UserModelAuthBackend):
    """Opens /admin to staff, and only with MFA on."""

    def is_authorized(self, user: BaseUserModel) -> bool:
        """Staff get in, whether or not is_admin is set."""
        return isinstance(user, UserModel) and user.role == "STAFF"


auth_backend: StaffAdminBackend = StaffAdminBackend(UserModel, require_mfa=True)
```

Pass `auth_backend` to `make_admin_router(...)` as usual. What changes:

- **The password still comes first.** A wrong password answers `"Invalid
  credentials"` for any account; only someone with the right password reads
  `"This account is not authorized for /admin"` (wrong role) or `"MFA
  enrollment required for /admin"` (no MFA).
- **`require_mfa=True`** refuses at login an authorized, active account that
  has not enrolled MFA (`mfa_enabled` false) — instead of letting it in on the
  password alone.
- **It holds for the session too.** `load_principal` checks the same two rules
  on every request: a revoked role, or MFA turned off after login, sends the
  user to `/admin/login` on the next request.
- `is_authorized` is synchronous on purpose: it receives the already-loaded
  row and must not hit the database.

!!! warning "The admin has no MFA enrollment screen"
    With `require_mfa=True`, someone who has not turned on the second factor
    sees `MFA enrollment required for /admin` on the login page itself
    (status `401`) and does not get in. Enrollment happens **outside** the
    admin: through the `POST /auth/mfa/enroll` + `POST /auth/mfa/confirm`
    routes of `make_auth_router` (see [MFA](mfa.md)), or when the account is
    created from the CLI, with `tempest user create --require-mfa`. Once
    enrolled, the next admin login asks for the TOTP code at `/admin/mfa`.

#### 6. Task panel — what is scheduled and what ran

`TaskPanelService` wires the admin to what the workers already produce: the
**schedule** declared on the broker and the **runs** a `JobStore` persisted.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import TaskPanelService
from tempest_fastapi_sdk.tasks import JobStore, TaskQueue

from src.core.settings import settings
from src.db.models import JobModel, UserModel

app: FastAPI = FastAPI()
db: AsyncDatabaseManager = AsyncDatabaseManager(settings.DATABASE_URL)
tq: TaskQueue = TaskQueue.rabbitmq(settings.RABBITMQ_URL)
jobs: JobStore[JobModel] = JobStore(db, model=JobModel)

app.include_router(
    make_admin_router(
        AdminSite(title="Ops"),
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.ADMIN_SECRET_KEY,
        tasks=TaskPanelService(queue=tq, job_store=jobs),
    )
)
```

That mounts three routes and a "Tasks" entry in the sidebar:

| Route | What it does |
| --- | --- |
| `GET /admin/tasks` | recent runs (filterable by status) + the declared schedule |
| `GET /admin/tasks/{job_id}` | one run: progress, stage, attempts, error |
| `POST /admin/tasks/{job_id}/cancel` | asks it to stop, while the status still allows it |

!!! warning "The panel does not show the broker's queue"
    It shows what is **persisted** (`JobStore` rows) and what is **declared**
    (the broker's registry). Queue depth is absent because TaskIQ exposes
    none — and a screen implying otherwise would be worse than no screen.

!!! tip "Either half is enough"
    `queue=` alone shows the schedule; `job_store=` alone shows the runs. A
    section with no source is not rendered, rather than rendering empty and
    implying there is nothing to see. Passing neither raises `ValueError` at
    construction.

!!! info "The `Schedule` column carries every declared trigger"
    A task may declare several triggers, and not every trigger is a cron:
    TaskIQ accepts `cron`, `interval` and `time` (one-shot) entries in the
    same list. The column shows one line per trigger, with the timezone
    beside the expression whenever the task declared one:

    | Declared | Shows as |
    | --- | --- |
    | `@tq.cron(daily(hour=2), cron_offset=CronOffsetEnum.BRASILIA)` | `0 2 * * *` `-03:00` |
    | `@tq.cron(daily(hour=2))` | `0 2 * * *` |
    | `@tq.interval(timedelta(seconds=30))` | `every 30s` |
    | `schedule=[{"time": when}]` | `once at 2026-01-01 12:00:00+00:00` |
    | `@tq.task` | `on demand` |

    Showing the offset is not decoration. A cron expression **reads as
    complete** without it, so nothing invites the reader to check — and
    `0 2 * * *` with `cron_offset=CronOffsetEnum.BRASILIA` fires at 05:00 UTC.
    Whoever opens the panel to learn when the backup runs would be three
    hours off, on the one screen whose only alternative source is the code.

    The panel shows what was **declared**, not TaskIQ's default: an
    expression with no offset appears with no offset, not as `(UTC)`.

!!! tip "Rendering the schedule yourself"
    `ScheduledTask.triggers` is the tuple of `TaskTrigger`, in registry
    order, and `TaskTrigger.cron_offset_label` formats the timezone
    (`"-03:00"` for a fixed offset, the IANA key for a named zone). The flat
    fields — `cron`, `interval_seconds`, `cron_offset`, `run_at` — summarize
    the first trigger of each kind, for callers that need only one. A
    schedule key the SDK does not model yet lands in `TaskTrigger.extra`
    rather than being dropped.

!!! note "Why there is no \"next run\" column"
    `pycron`, which arrives with TaskIQ, exposes `is_now` and `has_been` —
    and no `next()`. Computing the next run would mean sweeping candidate
    minutes on every render (up to ~44k iterations for a monthly cron) or
    taking a new dependency for one column. The panel shows the expression —
    with its offset beside it, because without one the expression is not
    legible.

For the paginated, filterable, exportable list the admin already knows how to
render, register the job model too — symmetric to the dead-letter panel:

```python
from tempest_fastapi_sdk import AdminSite
from tempest_fastapi_sdk.tasks import make_job_admin_model

from src.db.models import JobModel

site: AdminSite = AdminSite(title="Ops")
site.register(make_job_admin_model(JobModel))
```

#### 7. Storage browser — view, preview and edit files

The admin already writes to object storage (`upload_fields=`), but an
operator had no screen to see what a bucket holds, open a file, or remove a
stray upload. `StorageBrowserService` is that screen: it lists folders,
previews a file, and uploads, replaces, edits text in place, renames and
deletes.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import StorageBrowserService
from tempest_fastapi_sdk.storage import LocalObjectStorageClient

from src.core.settings import settings
from src.db.models import UserModel

app: FastAPI = FastAPI()
db: AsyncDatabaseManager = AsyncDatabaseManager(settings.DATABASE_URL)
store: LocalObjectStorageClient = LocalObjectStorageClient(
    "/var/lib/app/storage",
    public_url="https://api.example.com",
    signing_secret=settings.JWT_SECRET,
)

app.include_router(
    make_admin_router(
        AdminSite(title="Ops"),
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.ADMIN_SECRET_KEY,
        storage=StorageBrowserService(store, label="uploads"),
    )
)
```

MinIO/S3 is the same line: `StorageBrowserService(AsyncMinIOClient(...))`.
The service uses only five calls — `list_objects`, `stat_object`,
`stream_object`, `put_object` and `remove_object` (the `AdminStorageClient`
protocol) — which both SDK clients implement.

That mounts the routes below and a "Storage" entry in the sidebar:

| Route | What it does |
| --- | --- |
| `GET /admin/storage?prefix=` | one folder: sub-folders first, then files with size, type and date; paged |
| `GET /admin/storage/object?key=` | one file: metadata, preview, text editor and the write forms |
| `GET /admin/storage/raw?key=` | the bytes, for the preview (`&download=true` forces a download) |
| `POST /admin/storage/upload` | uploads a file into the current folder |
| `POST /admin/storage/object/replace` | swaps the content, keeping the key and metadata |
| `POST /admin/storage/object/edit` | saves the text editor |
| `POST /admin/storage/object/rename` | moves to another key, folder included |
| `POST /admin/storage/object/delete` | deletes |

Let's go piece by piece.

**Folders do not exist.** In object storage a folder is only the key prefix up
to a `/`. So a folder appears as soon as one file is written under it and
disappears when the last one leaves. On upload, the "Save as" field accepts
`a/b.txt` and creates the folders on the way.

**The preview depends on the type.** Images, video, audio and PDF are
rendered by the browser from the `raw` route. Text (`text/*`, JSON, YAML, XML,
CSV) is shown in the page itself, escaped, in a `<textarea>` editable up to
`max_text_bytes` (256 KiB by default). Anything else offers the download only.

!!! danger "SVG and HTML are never rendered"
    The `raw` route answers on the admin's origin. HTML rendered there runs
    script with the signed-in operator's session, and SVG carries `<script>`
    just the same. So the route grants `inline` only to the types in
    `INLINE_SAFE_MEDIA_TYPES` — SVG and HTML always arrive as `attachment` —
    and every response carries `X-Content-Type-Options: nosniff` and a CSP
    with `sandbox`. In the page, SVG and HTML are shown as escaped source.

**Text editing refuses a concurrent write.** The editor sends back the ETag
it opened at; if the file changed in between, the save is refused with
"changed since you opened it" instead of wiping the other write. And the
`\r\n` the browser sends becomes `\n` when the original held no `\r\n`, so
saving a Unix file unchanged does not rewrite every line.

**Renaming costs one download and one upload.** Object storage has no rename:
the service copies the bytes, type and metadata to the new key and deletes the
old one. A new key that is already taken is refused.

!!! tip "Read-only"
    `StorageBrowserService(store, read_only=True)` keeps listing, preview and
    download, hides the forms and answers `403` to every write. Who may open
    the screen is the `surface_policy`'s call, with `AdminSurfaceEnum.STORAGE` —
    see [SQL console, tasks and logs](#sql-console-tasks-and-logs-surface_policy).

!!! info "Limits"
    `max_upload_bytes` (25 MiB) applies to upload and replace,
    `max_text_bytes` (256 KiB) to text preview and editing, and `page_size`
    (100) to the listing. A key with an empty, `.` or `..` segment, a
    backslash, a control character or over 1024 bytes is refused before it
    reaches the store. Refusals come back as a red message on the same
    screen.

#### 8. Customize the look — `AdminTheme`

The admin CSS is driven entirely by **CSS custom properties** on `:root`. Instead of forking the stylesheet, you pass an `AdminTheme` with **typed, documented parameters** — colors, logo, favicon, font, radius, footer, dark mode — and the SDK injects a `<style>` block in the `<head>` (after `admin.css`, so it wins).

```python
# src/admin/site.py
from tempest_fastapi_sdk import AdminSite, AdminTheme

theme: AdminTheme = AdminTheme(
    accent="#7c3aed",                       # primary color (links, buttons, active item)
    accent_hover="#6d28d9",                 # hover shade of the accent
    header_bg="#1e1b4b",                    # header/sidebar background (text turns light on its own)
    radius="10px",                          # radius of buttons, inputs, cards, tables
    font_family="'Inter', system-ui, sans-serif",
    logo_url="/admin/static/logo.svg",      # header image (instead of the text brand)
    favicon_url="/admin/static/favicon.ico",
    footer_text="Servus | 2026",
    dark_mode=False,                         # dark surfaces
)

site: AdminSite = AdminSite(title="Servus Admin", brand="Servus", theme=theme)
```

`AdminTheme()` with no arguments is a **no-op**: it reproduces the stock look. You only set what you want to change.

!!! tip "The golden rule"
    Every `AdminTheme` field maps to a `:root` CSS variable (or to a piece
    of chrome, like the logo). It is all typed — the editor autocompletes
    the options and mypy validates — and no string ever needs to be a CSS
    class name or selector.

| Field | Type | Default | Effect |
|-------|------|---------|--------|
| `accent` | `str` | `"#2563eb"` | Primary color: links, buttons, active sidebar item |
| `accent_hover` | `str` | `"#1d4ed8"` | Hover/active shade of `accent` |
| `danger` | `str` | `"#b91c1c"` | Destructive actions and error messages |
| `header_bg` | `str` | `"#ffffff"` | Header background |
| `sidebar_bg` | ``str | None`` | `None` | Sidebar background (falls back to `header_bg`) |
| `header_fg` | ``str | None`` | `None` | Header and sidebar text color (`None` picks it from the background contrast) |
| `page_bg` | ``str | None`` | `None` | Content background (mode default) |
| `radius` | `str` | `"8px"` | Radius of buttons, inputs, cards, tables |
| `font_family` | ``str | None`` | `None` | `font-family` for the whole panel |
| `logo_url` | ``str | None`` | `None` | Header image instead of the text brand |
| `logo_alt` | `str` | `"Logo"` | `alt` text for the logo image |
| `favicon_url` | ``str | None`` | `None` | Browser-tab favicon |
| `footer_text` | `str` | `"Powered by tempest-fastapi-sdk"` | Footer text |
| `dark_mode` | `bool` | `False` | Dark surfaces (and the chrome, while `header_bg` is the default) |
| `custom_css_url` | ``str | None`` | `None` | Extra stylesheet, linked last |

!!! info "Dark mode"
    `dark_mode=True` switches the **content surfaces** (page background,
    cards, text, table rows, inputs, borders) to a dark palette. The header
    and sidebar go dark with them while `header_bg` is the light default;
    a `header_bg` of yours is kept. `accent` and the other colors still
    apply. An explicit `page_bg` wins over the content's dark mode (the
    surfaces stay light), but the default chrome still goes dark.

!!! info "Header text picked from the background"
    The header and sidebar are light by default. Give them a dark hex
    `header_bg` (or `sidebar_bg`) and their text turns light on its own —
    the SDK computes the background's luminance. A color that is not hex
    (`"rebeccapurple"`, `rgb(...)`) is treated as dark; when that guess is
    wrong, pin the color with `header_fg`.

!!! warning "Escape hatch for the rest"
    For anything the fields do not cover, point `custom_css_url` at your own
    stylesheet. It is linked **after** the theme, so it overrides
    everything — including `AdminTheme`.

!!! danger "Values are developer-set, not end-user input"
    The characters `< > { } "` are rejected in any string field
    (`ValueError` at construction), because they would break the injected
    `<style>` block or an HTML attribute. Never derive `AdminTheme` values
    from end-user input.

**Recap:** instantiate `AdminTheme` with the fields you want to change,
pass it via `AdminSite(theme=...)`, and the look changes across every page
(login, dashboard, list, detail, forms) without touching CSS. For full
control, `custom_css_url`.

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
