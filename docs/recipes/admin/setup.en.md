---
description: "Use to stand up the admin panel: a `BaseUserModel` user model, registering admin classes or `automap`, and mounting the router."
---

# Mount the admin site

Use to stand the panel up: user model, admin class registration (or `automap`) and the router with everything it mounts.

Part of the [Admin site](../admin.md) recipe.

## 1. User model

Subclass `BaseUserModel` to get the four columns the admin auth backend expects (`email`, `hashed_password`, `is_admin`, `last_login_at`), plus the `email_verified_at` the [auth flow](../auth-flow/email-change.md#what-counts-as-a-verified-email) fills, on top of the standard `BaseModel` row. Activating an account from the admin does not fill `email_verified_at`, and editing its `email` clears it:

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

## 2. Register your admin classes

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

## 2b. Shortcut — register every model at once (`automap`)

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

## 3. Mount the router

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
