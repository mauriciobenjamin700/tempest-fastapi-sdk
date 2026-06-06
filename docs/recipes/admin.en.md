# Admin site


Django-style management UI mounted under `/admin`. Operators sign in with a user row from the database (no separate admin password store) and browse every registered model from the browser, so the database port can stay closed on private networks. Phase 1 ships read-only views; create/edit/delete land in 0.14.0 and inline + bulk actions in 0.15.0.

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
    index_subtitle="Site administration",
    site_url="https://myapp.com",   # optional outbound "View site" link
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

#### 3. Mount the router

```python
# src/api/app.py
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    UserModelAuthBackend,
    make_admin_router,
)

from src.admin.site import site
from src.core.settings import settings
from src.db.models import UserModel

db = AsyncDatabaseManager(settings.DATABASE_URL)
app = FastAPI()
app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.JWT_SECRET,          # scaffold reuses JWT_SECRET — at least 32 bytes
        prefix="/admin",
        cookie_secure=not settings.DEBUG,        # True in production HTTPS
    )
)
```

`make_admin_router` mounts:

- `GET  /admin/login`, `POST /admin/login`, `POST /admin/logout` — auth flow.
- `GET/POST /admin/mfa` — TOTP second-factor challenge between the password step and access, for MFA-enabled principals.
- `GET  /admin/` — dashboard: a card per model with its **live row count** + Browse/New, plus a **metrics panel** (CPU/RAM/disk via `MetricsUtils`). On by default, omitted without the `[metrics]` extra, disable with `make_admin_router(show_metrics=False)`.
- `GET  /admin/m/{slug}/` — list view with pagination + free-text search (`?q=`) + per-field filters (`?filter_<field>=value`) + clickable **column sorting** (`?sort=<column>&dir=asc|desc`).
- `GET  /admin/m/{slug}/export.csv` / `export.json` — **export** the current result set (honoring search/filters/sort) as CSV or JSON. Row cap via `make_admin_router(export_max_rows=…)` (default 5000).
- `POST /admin/m/{slug}/bulk` — **bulk actions** (delete / activate / deactivate) on the selected rows.
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

!!! tip "Responsive by default"
    The bundled templates + CSS are responsive: on narrow screens (≤600px) the header stacks, search/filters/actions go full-width, tables get horizontal scroll (never breaking the layout), and the detail grid collapses to a single column. Column headers are clickable to toggle sort order (▲/▼).

#### 4. Session security defaults

`SignedCookieSessionStore` uses `itsdangerous.TimestampSigner` (HMAC-SHA256) to sign a single cookie:

- `HttpOnly` always set.
- `Secure` flagged when `cookie_secure=True` (default; flip off in local HTTP dev).
- `SameSite=Lax` (`"lax"`/`"strict"`/`"none"` accepted).
- Default lifetime `8h`; expired or tampered cookies are rejected silently.
- Per-session CSRF token is generated at login and required by every form POST (only `logout` in Phase 1).
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

