# Mount the admin site

Use to stand the panel up: user model, admin class registration (or `automap`), the router with everything it mounts, the session security defaults and a custom auth backend.

Part of the [Admin site](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/admin.md) recipe.

## 1. User model

Subclass `BaseUserModel` to get the four columns the admin auth backend expects (`email`, `hashed_password`, `is_admin`, `last_login_at`), plus the `email_verified_at` the [auth flow](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/auth-flow/email-change.md#what-counts-as-a-verified-email) fills, on top of the standard `BaseModel` row. Activating an account from the admin does not fill `email_verified_at`, and editing its `email` clears it:

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

    In-place inline editing of children: see [`Inline(editable=True)`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/admin/forms.md#in-place-editing-editabletrue).

## 4. Session security defaults

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
    [Security](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/security.md#public-scheme-behind-a-proxy-server_public_scheme).
    Until then, `GET /admin/login` logs a `WARNING` saying so.

### Signing in from a test

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

### Login failure budget

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

## 5. Plug in a custom auth backend

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

### Authorize by role and require MFA

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
    routes of `make_auth_router` (see [MFA](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/mfa.md)), or when the account is
    created from the CLI, with `tempest user create --require-mfa`. Once
    enrolled, the next admin login asks for the TOTP code at `/admin/mfa`.
