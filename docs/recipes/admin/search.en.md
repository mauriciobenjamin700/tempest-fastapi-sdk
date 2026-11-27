# Global search

Use to search a term across every model with `search_fields` from the search box at the top of the panel.

Part of the [Admin site](../admin.md) recipe.

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
    [Security](../security.md#public-scheme-behind-a-proxy-server_public_scheme).
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
    routes of `make_auth_router` (see [MFA](../mfa.md)), or when the account is
    created from the CLI, with `tempest user create --require-mfa`. Once
    enrolled, the next admin login asks for the TOTP code at `/admin/mfa`.

## 6. Task panel — what is scheduled and what ran

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

## 7. Storage browser — view, preview and edit files

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
    see [SQL console, tasks and logs](permissions.md#sql-console-tasks-and-logs-surface_policy).

!!! info "Limits"
    `max_upload_bytes` (25 MiB) applies to upload and replace,
    `max_text_bytes` (256 KiB) to text preview and editing, and `page_size`
    (100) to the listing. A key with an empty, `.` or `..` segment, a
    backslash, a control character or over 1024 bytes is refused before it
    reaches the store. Refusals come back as a red message on the same
    screen.

## 8. Customize the look — `AdminTheme`

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
