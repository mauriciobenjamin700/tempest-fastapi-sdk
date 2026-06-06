# Roadmap

What the SDK **doesn't ship yet** + what already landed. Sorted by impact, not implementation order — the current release is pulled by business pressure, not list position.

!!! tip "What the SDK already covers"
    Full auth (JWT/bearer/role/permission/X-Token + bundled signup/activate/login/reset via `UserAuthService` + `make_auth_router`), OAuth2/OIDC (Google/GitHub + generic), CSRF middleware, DB (`AsyncDatabaseManager` + `BaseRepository` + bulk ops + `AlembicHelper` + `BaseModel` + `BaseUserModel` + `BaseUserTokenModel` + audit/soft-delete mixins + Alembic hook reordering base columns), standardized exceptions, structured logging + per-level files + `/logs` endpoint, metrics (CPU/RAM/GPU/Disk + Prometheus `/metrics` + `PrometheusMiddleware`), rate limiting, idempotency (`IdempotencyMiddleware` + memory/Redis stores), body-size limit, pagination (offset + cursor), settings mixins with `title`/`description`/`examples`, SSE, throttle, local upload/download + pluggable storage (`LocalUploadStorage` + `MinIOUploadStorage`), MinIO/S3 (`AsyncMinIOClient`), WebPush, webhook signatures, BR validators (CPF/CNPJ/CEP/phone), admin panel (Jinja + HTMX), email (SMTP + Jinja2 templates), Redis cache, FastStream queue, TaskIQ tasks, hardened static files, server runner, health, tool-spec router, request-id middleware, CORS, typed HTTP client (`HTTPClient` httpx wrapper with retry/backoff/circuit-breaker), full CLI (`tempest new`, `tempest generate --docker`, `tempest db <subcommand>`, `tempest user <subcommand>`, quality gates).

## Tier S — every serious API needs these

| Feature | Status | Where |
|---------|--------|-------|
| `IdempotencyMiddleware` + `idempotency_keys` | ✅ v0.24.0 | `tempest_fastapi_sdk.api.middlewares.idempotency` |
| `UploadUtils` pluggable backends (`LocalUploadStorage`, `MinIOUploadStorage`) | ✅ v0.24.0 | `tempest_fastapi_sdk.utils.storage_backends` |
| `HTTPClient` (typed httpx wrapper) with retry/backoff/circuit-breaker | ✅ v0.28.0 | `tempest_fastapi_sdk.utils.http_client` |
| **OpenTelemetry tracing** — `setup_tracing(app, otlp_endpoint=…)` | ❌ pending | — |
| **Outbox pattern** — `BaseRepository.save_with_outbox(model, event)` | ❌ pending | — |

## Tier A — common in SaaS backends

| Feature | Status | Where |
|---------|--------|-------|
| `EmailUtils.render_template(path, ctx)` with Jinja2 | ✅ v0.24.0 | `EmailUtils.render_template` + bundled templates |
| OAuth2 / OIDC providers (`GoogleOAuthClient`, `GitHubOAuthClient`, `OIDCProvider`) | ✅ v0.29.0 | `tempest_fastapi_sdk.api.oauth` |
| `CSRFMiddleware` + `make_csrf_token_dependency` | ✅ v0.29.0 | `tempest_fastapi_sdk.api.middlewares.csrf` |
| `BodySizeLimitMiddleware` | ✅ v0.28.0 | `tempest_fastapi_sdk.api.middlewares.body_size` |
| `BaseRepository.bulk_create_values / bulk_upsert` | ✅ v0.28.0 | `BaseRepository` |
| Prometheus `/metrics` endpoint | ✅ v0.28.0 | `tempest_fastapi_sdk.api.routers.metrics` |
| Bundled signup / activate / login / password-reset | ✅ v0.31.0 | `tempest_fastapi_sdk.auth` |
| Backend-only mode (signup / activate / reset rendered by the backend) | ✅ v0.32.0 | `tempest_fastapi_sdk.auth` + HTML templates |
| `make_websocket_router` — bearer auth, heartbeat, broadcast | ✅ v0.33.0 | `tempest_fastapi_sdk.websockets` |
| Server-side sessions (alternative to JWT) | ✅ v0.34.0 | `tempest_fastapi_sdk.sessions` |
| 2FA / TOTP (`pyotp` wrapper + recovery codes) | ✅ v0.35.0 | `TOTPHelper` + `UserAuthService.mfa_*` + `BaseUserRecoveryCodeModel` |
| `tempest db` + `tempest user` CLI | ✅ v0.30.0 | `tempest_fastapi_sdk.cli.db` / `cli.user` |
| `BaseRepository.bulk_update` (filters + values) | ✅ pre-existing | `BaseRepository.bulk_update` |
| **Multi-tenant scope** — `TenantScopedRepository(tenant_id)` auto-injecting `WHERE tenant_id = …` on every repository query | ❌ planned v0.36.0 | — |

## Tier B — when the service grows

| Feature | Status |
|---------|--------|
| `SlowQueryLogger` — SQLAlchemy event logging queries > N ms with `EXPLAIN` | ❌ pending |
| `AlembicHelper.safe_upgrade()` — block destructive migrations without `--force` | ❌ pending |
| Graceful shutdown — drain in-flight requests on `SIGTERM` | ❌ pending |
| F() / Q() expression wrappers for SQLAlchemy | ❌ pending |
| eager-load helper (`BaseRepository.get_by_id(id, with_=...)`) | ❌ pending |
| Signals (`pre_save`/`post_save`/`pre_delete`) via SQLAlchemy events on `BaseRepository` | ❌ pending |
| Object-level permissions framework (`user.has_perm("order.delete", obj=order)`) | ❌ pending |
| Startup system checks (`tempest check-config`) | ❌ pending |
| Management commands framework — project-registered `tempest <cmd>` | ❌ pending |
| `tempest db seed` — load JSON/Python fixtures | ❌ pending |
| CLI: `tempest secrets rotate` | ❌ pending |

## Admin panel — evolution

The admin panel already exists (`AdminSite` / `AdminModel` / `make_admin_router`, Jinja + HTMX, CSRF token). The items below take it from "functional CRUD" to "production admin", reusing primitives the SDK already ships (`AuditMixin`, `MetricsUtils`, `TOTPHelper`, `UploadUtils`).

| Feature | Why it matters | Reuses |
|---------|----------------|--------|
| **Per-column filter / search / sort** on the list view | Large lists are unusable without it — the first thing every operator asks for. | `BaseRepository` (filters + pagination) |
| **Bulk actions** (mass delete / activate) | Row-by-row actions don't scale; select N rows + one action is the standard admin flow. | `BaseRepository.bulk_update` / soft-delete |
| **Field widgets** (FK select, date picker, file upload) | The form is generic today; FK as `<select>`, dates with a picker, and upload via `UploadUtils` remove manual typing and error. | `UploadUtils` + storage backends |
| **Inline / related editing** | Edit children (1-N) on the parent's screen — the Django-admin pattern that's missing. | `BaseRepository` + relationships |
| **CSV / JSON export** | Operator exports the filtered result without opening the database. | list view + filters |
| **Audit log visible in the admin** | Who changed what and when, straight in the UI. | `AuditMixin` (`created_by` / `updated_by`) |
| **Metrics dashboard** | A landing screen with CPU/RAM/counters instead of an empty page. | `MetricsUtils` |
| **MFA on admin login** | Second factor on the most sensitive access in the system; a natural fit now that TOTP exists. | `TOTPHelper` + `MFAMixin` + recovery codes |

## Everything shipped so far

### ✅ v0.23.0 — MinIO/S3 storage

`AsyncMinIOClient` via the `[minio]` extra — bucket lifecycle, object I/O, streaming download, presigned URLs.

### ✅ v0.24.0 — Pluggable uploads + idempotency + email templates

- `UploadStorage` protocol + `LocalUploadStorage` + `MinIOUploadStorage`
- `IdempotencyMiddleware` + `MemoryIdempotencyStore` + `RedisIdempotencyStore`
- `EmailUtils.render_template(template, ctx)` with Jinja2 + autoescape

### ✅ v0.25.0 — CLI docker-compose generator

`tempest new` emits a `docker-compose.yaml` matching the chosen extras. Postgres always, `[cache]`→Redis, `[queue]`/`[tasks]`→RabbitMQ, `[minio]`→MinIO + bootstrap, `[email]`→MailHog. Pinned tags. `.env.example` receives an addendum.

### ✅ v0.26.0 — `tempest generate --docker` + image bumps

Regenerates compose in an existing project. Postgres 18 / Redis 8 / RabbitMQ 4. Pydantic schemas + settings carry `title`/`description`/`examples`.

### ✅ v0.28.0 — Observability + retries

- Prometheus `/metrics` endpoint + `PrometheusMiddleware`
- `HTTPClient` (typed httpx wrapper) with retry/backoff/circuit-breaker/`X-Request-ID` propagation
- `BodySizeLimitMiddleware`
- `BaseRepository.bulk_create_values` + `bulk_upsert`

### ✅ v0.29.0 — Security middlewares + OAuth providers

- `CSRFMiddleware` + `make_csrf_token_dependency`
- OAuth2/OIDC: `GoogleOAuthClient`, `GitHubOAuthClient`, `OIDCProvider`
- Fixed Postgres 18 mount path in docker-compose

### ✅ v0.29.1 — Scaffold with UserModel + admin wiring

`tempest new` now generates a concrete `UserModel` + wires the admin panel out of the box. Default extras `auth,admin`.

### ✅ v0.30.0 — `tempest db` + `tempest user`

- `tempest db init/revision/upgrade/downgrade/current/history`
- `tempest user create [--admin]` + `tempest user list [--admin]`
- `DATABASE_URL` resolution: flag → env → settings → ini

### ✅ v0.30.1 — Alembic reorder hook

`reorder_base_columns_first` hook emits `id`, `is_active`, `created_at`, `updated_at` at the top of every autogenerated `op.create_table`.

### ✅ v0.30.2 — Empty `sqlalchemy.url` in `alembic.ini`

Credentials no longer enter VCS. `env.py` resolves the URL at runtime.

### ✅ v0.30.3 — Quiet post-write hooks

`ruff_format` runs before `ruff_fix` + `--quiet` on both — no stdout noise during `tempest db revision`.

### ✅ v0.31.0 — Bundled auth flow

- `UserAuthService` — signup / activate / login / request_password_reset / confirm_password_reset
- `make_auth_router` — 5 endpoints ready to mount
- `BaseUserTokenModel` + `UserTokenPurpose` (activation/password_reset/email_verification)
- `AuthSettings` mixin — `AUTH_AUTO_ACTIVATE`, `AUTH_RETURN_TOKEN_IN_RESPONSE`, TTLs, URL templates
- Bundled Jinja2 templates (override by dropping a same-named file in `template_dir`)

### ✅ v0.31.1 — BaseSchema for tokens + full docstrings

`ActivationToken` / `PasswordResetToken` rewritten as `BaseSchema` (no more dataclass leak). Every auth DTO carries a thorough class docstring.

### ✅ v0.31.2 — `session: AsyncSession` everywhere in UserAuthService

`Any` removed — all 7 service signatures type `AsyncSession`.

## What's next

| Release | Content |
|---------|---------|
| **v0.32.0+** | OpenTelemetry tracing (`setup_tracing(app, otlp_endpoint=…)`) with FastAPI/SQLAlchemy/httpx auto-instrumentation |
| **v0.33.0+** | Outbox pattern (`BaseRepository.save_with_outbox(model, event)`) drained by `AsyncBrokerManager` |

!!! note "This roadmap is honest, not aspirational"
    Items past the next cuts only land on the changelog when business pressure pulls them. This page is refreshed on every release — if something belongs here and isn't, open an issue.

## How to request a feature

Open an issue at <https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues> describing:

1. The real use case (not the solution).
2. The workaround you use today.
3. Why the workaround hurts (perf, security, ergonomics, maintenance).

Issues with concrete use cases move up the queue — abstractions without demand don't land, even when they "would make sense".
