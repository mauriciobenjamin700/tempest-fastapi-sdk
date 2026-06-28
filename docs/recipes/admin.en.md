# Admin site


Django-style management UI mounted under `/admin`. Operators sign in with a user row from the database (no separate admin password store) and browse every registered model from the browser, so the database port can stay closed on private networks. The panel is feature-complete (Django-admin parity): a list view with search / per-field filters / sortable columns, full CRUD (create / edit / delete), bulk actions, CSV/JSON export, FK-select widgets, a dashboard with live row counts + system metrics, optional TOTP MFA at login, and an audit trail stamping `created_by` / `updated_by`. Still on the roadmap: file upload and inline/related editing.

Requires the `[admin]` extra:

```bash
pip install "tempest-fastapi-sdk[admin]"
```

#### 1. User model

Subclass `BaseUserModel` to get the four columns the admin auth backend expects (`email`, `hashed_password`, `is_admin`, `last_login_at`) on top of the standard `BaseModel` row:

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
- `GET  /admin/m/{slug}/` — list view with pagination + free-text search (`?q=`) + per-field filters (`?filter_<field>=value`) + clickable **column sorting** (`?sort=<column>&dir=asc|desc`).
- `GET  /admin/m/{slug}/export.csv` / `export.json` — **export** the current result set (honoring search/filters/sort) as CSV or JSON. Row cap via `make_admin_router(export_max_rows=…)` (default 5000).
- `POST /admin/m/{slug}/bulk` — **bulk actions** (delete / activate / deactivate + your **custom actions**) on the selected rows.
- `GET/POST /admin/m/{slug}/new` — **create** a record (when `can_create`).
- `GET  /admin/m/{slug}/{identity}` — detail view with Edit/Delete controls.
- `GET/POST /admin/m/{slug}/{identity}/edit` — **edit** a record (when `can_edit`).
- `POST /admin/m/{slug}/{identity}/delete` — **delete** a record (when `can_delete`).
- `GET  /admin/static/{path}` — bundled CSS/HTMX assets.

!!! info "Write CRUD + permissions"
    Create/edit/delete are gated by `AdminModel` flags: `can_create` / `can_edit` / `can_delete` (all `True` by default; a disabled view returns `404`). Every write POST carries the session CSRF token, verified server-side (`403` on mismatch). **Field widgets** are derived from the column type — text / textarea (long strings) / number / checkbox / `datetime-local` / date / `select` for enums — with required-field + per-field validation errors re-rendered on the form.

    **Bulk actions**: the list view shows per-row checkboxes + select-all and an action bar (delete / activate / deactivate) operating on the checked rows via `POST .../bulk` (CSRF + `can_delete`/`can_edit` flags), backed by `BaseRepository.delete_batch` / `bulk_update`.

    **FK-select**: a foreign-key column whose target has a registered `AdminModel` renders as a dropdown of the related rows (like Django's FK select) on the form, instead of a raw UUID input. The option label comes from the referenced admin's first `search_fields` entry (fallback: a `name`/`title`/`email` attribute, then the id). Capped at 1000 rows; FKs to unmanaged tables stay UUID inputs.

    **MFA login**: a principal with MFA enabled (`MFAMixin`'s `totp_secret`/`totp_enabled_at`) goes through a TOTP challenge at `/admin/mfa` after the password — only a valid code grants access. Enable it via `UserModelAuthBackend(UserModel, mfa_issuer=...)`; custom backends override `mfa_enabled`/`verify_mfa`.

    **Audit trail**: create/edit through the admin stamps `created_by`/`updated_by` (from `AuditMixin`) with the acting admin's id; the detail view shows an **Audit** panel with timestamps and — when the model has the audit columns — the actor (UUID resolved to a display name via the auth backend). Models without `AuditMixin` show timestamps only.

    Not yet included (later roadmap phases): file upload, inline/related editing.

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
    admin_action,
)


@admin_action(label="Send welcome")
async def send_welcome(ctx: AdminActionContext) -> AdminActionResult:
    """Runs on the selected rows; the message is shown on the list view."""
    users = await ctx.repository.list(filters={"id": ctx.ids})
    for user in users:
        await mailer.send_welcome(user.email)
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

!!! tip "Responsive by default"
    The bundled templates + CSS are responsive: on narrow screens (≤600px) the header stacks, search/filters/actions go full-width, tables get horizontal scroll (never breaking the layout), and the detail grid collapses to a single column. Column headers are clickable to toggle sort order (▲/▼).

#### 4. Session security defaults

`SignedCookieSessionStore` uses `itsdangerous.TimestampSigner` (HMAC-SHA256) to sign a single cookie:

- `HttpOnly` always set.
- `Secure` flagged when `cookie_secure=True` (default; flip off in local HTTP dev).
- `SameSite=Lax` (`"lax"`/`"strict"`/`"none"` accepted).
- Default lifetime `8h`; expired or tampered cookies are rejected silently.
- Per-session CSRF token is generated at login and required by every form POST (login, logout, create, edit, delete, bulk actions).
- `secret_key` must be at least 32 bytes — short keys raise `ValueError` at construction time.

#### 5. Plug in a custom auth backend

`AdminAuthBackend` is an ABC, so swap the default for LDAP / OAuth / external IAM by subclassing:

```python
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AdminAuthBackend, AdminAuthError


class OAuthAdminBackend(AdminAuthBackend):
    async def authenticate(
        self,
        session: AsyncSession,
        *,
        identifier: str,
        password: str,
    ) -> Any:
        principal = await my_oauth_client.authenticate(identifier, password)
        if not principal.has_role("admin"):
            raise AdminAuthError("not an admin")
        return principal

    async def load_principal(
        self,
        session: AsyncSession,
        principal_id: str,
    ) -> Any | None:
        return await my_oauth_client.get_user(principal_id)

    def principal_id(self, principal: Any) -> str:
        return principal.sub

    def display_name(self, principal: Any) -> str:
        return principal.email
```

Pass the instance via `auth_backend=` and the rest of the admin pipeline (sessions, dashboard, list, detail) keeps working unchanged.

#### 6. Customize the look — `AdminTheme`

The admin CSS is driven entirely by **CSS custom properties** on `:root`. Instead of forking the stylesheet, you pass an `AdminTheme` with **typed, documented parameters** — colors, logo, favicon, font, radius, footer, dark mode — and the SDK injects a `<style>` block in the `<head>` (after `admin.css`, so it wins).

```python
# src/admin/site.py
from tempest_fastapi_sdk import AdminSite, AdminTheme

theme: AdminTheme = AdminTheme(
    accent="#7c3aed",                       # primary color (links, buttons, active item)
    accent_hover="#6d28d9",                 # hover shade of the accent
    header_bg="#1e1b4b",                    # header/sidebar background
    radius="10px",                          # radius of buttons, inputs, cards, tables
    font_family="'Inter', system-ui, sans-serif",
    logo_url="/admin/static/logo.svg",      # header image (instead of the text brand)
    favicon_url="/admin/static/favicon.ico",
    footer_text="Servus | 2026",
    dark_mode=False,                         # dark content surfaces
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
| `header_bg` | `str` | `"#0f172a"` | Header background |
| `sidebar_bg` | `str \| None` | `None` | Sidebar background (falls back to `header_bg`) |
| `page_bg` | `str \| None` | `None` | Content background (mode default) |
| `radius` | `str` | `"6px"` | Radius of buttons, inputs, cards, tables |
| `font_family` | `str \| None` | `None` | `font-family` for the whole panel |
| `logo_url` | `str \| None` | `None` | Header image instead of the text brand |
| `logo_alt` | `str` | `"Logo"` | `alt` text for the logo image |
| `favicon_url` | `str \| None` | `None` | Browser-tab favicon |
| `footer_text` | `str` | `"Powered by tempest-fastapi-sdk"` | Footer text |
| `dark_mode` | `bool` | `False` | Dark content surfaces |
| `custom_css_url` | `str \| None` | `None` | Extra stylesheet, linked last |

!!! info "Dark mode"
    `dark_mode=True` switches the **content surfaces** (page background,
    text, table rows, inputs, borders) to a dark palette. The
    header/sidebar are already dark, so they are unaffected; `accent` and
    the other colors still apply. An explicit `page_bg` wins over dark mode.

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

