# Roadmap

What the SDK **doesn't ship yet** + what already landed. Sorted by impact, not implementation order — the current release is pulled by business pressure, not list position.

!!! tip "What the SDK already covers"
    Full auth (JWT/bearer/role/permission/X-Token + bundled signup/activate/login/reset via `UserAuthService` + `make_auth_router`), OAuth2/OIDC (Google/GitHub + generic), CSRF middleware, DB (`AsyncDatabaseManager` + `BaseRepository` + bulk ops + `AlembicHelper` + `BaseModel` + `BaseUserModel` + `BaseUserTokenModel` + audit/soft-delete mixins + Alembic hook reordering base columns), standardized exceptions, structured logging + per-level files + `/logs` endpoint, metrics (CPU/RAM/GPU/Disk + Prometheus `/metrics` + `PrometheusMiddleware`), rate limiting, idempotency (`IdempotencyMiddleware` + memory/Redis stores), body-size limit, pagination (offset + cursor), settings mixins with `title`/`description`/`examples`, SSE, throttle, local upload/download + pluggable storage (`LocalUploadStorage` + `MinIOUploadStorage`), MinIO/S3 (`AsyncMinIOClient`), WebPush, webhook signatures, BR validators (CPF/CNPJ/CEP/phone), admin panel (Jinja + HTMX, Django-admin parity — list view with search/filters/sortable columns, full CRUD, bulk actions, CSV/JSON export, FK-select widgets, dashboard with counts + metrics, TOTP MFA at login, `created_by`/`updated_by` audit trail), email (SMTP + Jinja2 templates), Redis cache, FastStream queue, TaskIQ tasks, hardened static files, server runner, health, tool-spec router, request-id middleware, CORS, typed HTTP client (`HTTPClient` httpx wrapper with retry/backoff/circuit-breaker), full CLI (`tempest new`, `tempest generate --docker` — compose credentials resolved from `.env` via `${VAR:-default}`, not hardcoded —, `tempest db <subcommand>`, `tempest user <subcommand>`, quality gates).

## Tier S — every serious API needs these

| Feature | Status | Where |
|---------|--------|-------|
| `IdempotencyMiddleware` + `idempotency_keys` | ✅ v0.24.0 | `tempest_fastapi_sdk.api.middlewares.idempotency` |
| `UploadUtils` pluggable backends (`LocalUploadStorage`, `MinIOUploadStorage`) | ✅ v0.24.0 | `tempest_fastapi_sdk.utils.storage_backends` |
| `HTTPClient` (typed httpx wrapper) with retry/backoff/circuit-breaker | ✅ v0.28.0 | `tempest_fastapi_sdk.utils.http_client` |
| **OpenTelemetry tracing** — `setup_tracing(app, otlp_endpoint=…)` | ✅ v0.43.0 | `tempest_fastapi_sdk.api.tracing` |
| **Outbox pattern** — `BaseRepository.save_with_outbox(model, event)` | ✅ v0.44.0 | `BaseRepository.save_with_outbox` + `tempest_fastapi_sdk.db.outbox` |

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
| **Multi-tenant scope** — `TenantScopedRepository(tenant_id)` auto-injecting `WHERE tenant_id = …` on every repository query | ✅ v0.45.0 | `tempest_fastapi_sdk.db.tenant` |

## Tier B — when the service grows

| Feature | Status | Where |
|---------|--------|-------|
| `SlowQueryLogger` — SQLAlchemy event logging queries > N ms with `EXPLAIN` | ✅ v0.59.1 | `tempest_fastapi_sdk.db.slow_query` |
| `AlembicHelper.safe_upgrade()` — block destructive migrations without `--force` | ✅ v0.46.0 | `AlembicHelper.safe_upgrade` (`tempest_fastapi_sdk.db.migrations`) |
| Graceful shutdown — drain in-flight requests on `SIGTERM` | ✅ v0.46.0 | `GracefulShutdownMiddleware` (`tempest_fastapi_sdk.api.middlewares.graceful`) |
| `tempest db seed` — load JSON/Python fixtures | ✅ v0.47.0 | `tempest_fastapi_sdk.cli.db` |
| CLI: `tempest secrets rotate` | ✅ v0.47.0 | `tempest_fastapi_sdk.cli.secrets` |
| F() / Q() expression wrappers for SQLAlchemy | ❌ pending | — |
| eager-load helper (`BaseRepository.get_by_id(id, with_=...)`) | ❌ pending | — |
| Signals (`pre_save`/`post_save`/`pre_delete`) via SQLAlchemy events on `BaseRepository` | ❌ pending | — |
| Object-level permissions framework (`user.has_perm("order.delete", obj=order)`) | ❌ pending | — |
| Startup system checks (`tempest check-config`) | ❌ pending | — |
| Management commands framework — project-registered `tempest <cmd>` | ❌ pending | — |

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

The full release history — every version with its **Added** / **Changed** / **Fixed** entries — lives in the [changelog](changelog.md), in [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. It's the source of truth; this page only highlights what's still missing.

## What's next

Genuinely unreleased work (after v0.89.0). Ordered by impact, not by version number — the current release is pulled by business pressure.

| Release | Content |
|---------|---------|
| **v0.90.0+** | eager-load helper (`BaseRepository.get_by_id(id, with_=...)`) + signals (`pre_save`/`post_save`/`pre_delete`) via SQLAlchemy events on `BaseRepository` |
| **v0.90.0+** | Object-level permissions framework (`user.has_perm("order.delete", obj=order)`) |
| **future** | F() / Q() expression wrappers, startup system checks (`tempest check-config`), management commands framework (project-registered `tempest <cmd>`) |

!!! note "This roadmap is honest, not aspirational"
    Items past the next cuts only land on the changelog when business pressure pulls them. This page is refreshed on every release — if something belongs here and isn't, open an issue.

## Shipped in v0.105.0

The GenAI ergonomics plan plus the two application modules below have
**landed** (they used to be "planned" here):

| Feature | Status | Where |
|---------|--------|-------|
| **Typed `GenerationConfig`** | ✅ v0.105 | Validated generation params instead of `**kwargs`. [Recipe »](recipes/genai.md) |
| **`make_genai_router`** | ✅ v0.105 | Ready endpoints (`/generate`+SSE, `/chat`, `/embed`, `/rag`, `/transcribe`, `/tts`), mounts only what you inject. [Recipe »](recipes/genai.md) |
| **`RedisEmbeddingCache`** | ✅ v0.105 | Async vector cache shared across workers; `Embedder` accepts a sync or async cache. [Recipe »](recipes/genai.md) |
| **Chat (`tempest_fastapi_sdk.chat`)** | ✅ v0.105 | `ChatService` + base tables + `make_chat_router` + real time via `SSEBroker`. [Recipe »](recipes/chat.md) |
| **Comments + ratings (`reviews`)** | ✅ v0.105 | `ReviewService` (comment, 0–5 rating, aggregate) + `make_reviews_router`; `RatingField`. [Recipe »](recipes/reviews.md) |

## How to request a feature

Open an issue at <https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues> describing:

1. The real use case (not the solution).
2. The workaround you use today.
3. Why the workaround hurts (perf, security, ergonomics, maintenance).

Issues with concrete use cases move up the queue — abstractions without demand don't land, even when they "would make sense".
