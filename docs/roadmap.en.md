# Roadmap

This page lists what the SDK **does not ship yet** but has real demand in production services. Sorted by impact, not by implementation order — the current release is pulled by business pressure, not list position.

!!! tip "What the SDK already covers"
    Auth (JWT/bearer/role/permission/X-Token), DB (`AsyncDatabaseManager` + `BaseRepository` + `AlembicHelper` + `BaseModel` + audit/soft-delete mixins), standardized exceptions, structured logging + per-level files + `/logs` endpoint, metrics (CPU/RAM/GPU/Disk), rate limiting, pagination (offset + cursor), settings mixins, SSE, throttle, local upload/download, **MinIO/S3 object storage (`AsyncMinIOClient` via the `[minio]` extra)**, WebPush, webhook signatures, BR validators (CPF/CNPJ/CEP/phone), admin panel (Jinja + HTMX), email (SMTP), Redis cache, FastStream queue, TaskIQ tasks, hardened static files, server runner, health, tool-spec router, request-id middleware, CORS, CLI scaffolder.

## Tier S — every serious API needs these

| Feature | Why it matters |
|---------|----------------|
| **`IdempotencyMiddleware`** + `idempotency_keys` table | `Idempotency-Key` header required on POST for payments / webhooks / retries. Without it, retried requests duplicate rows. Stripe/AWS pattern. |
| **`UploadUtils` pluggable backends** — `LocalBackend`, `S3Backend(bucket, region)`, `GCSBackend` | Today `UploadUtils` only writes to local disk. ⚠️ **Standalone MinIO/S3 client shipped in v0.23.0** via `AsyncMinIOClient` (`[minio]` extra) — still needs the `UploadUtils` plug-in adapter. |
| **OpenTelemetry tracing** — `setup_tracing(app, otlp_endpoint=…)` | `RequestIDMiddleware` correlates logs but doesn't give cross-service spans. Needs auto-instrumentation for FastAPI/SQLAlchemy/httpx. |
| **`HTTPClient` (typed httpx wrapper)** | Retry + backoff, `X-Request-ID` propagation, circuit breaker, default timeouts. Today every service rolls raw httpx. |
| **Outbox pattern** — `BaseRepository.save_with_outbox(model, event)` | Persists event in the same tx as the `INSERT`; `AsyncBrokerManager` drains it. Without this, events are lost when the broker fails after commit. |

## Tier A — common in SaaS backends

| Feature | Why it matters |
|---------|----------------|
| **`EmailUtils.render_template(path, ctx)`** with Jinja2 | Welcome / reset / verify emails — today SMTP only accepts raw strings. |
| **OAuth2 / OIDC providers** — `GoogleOAuthClient`, `GitHubOAuthClient`, `OIDCProvider(discovery_url)` | `JWTUtils` only signs our own tokens; we have no social-login glue. |
| **`CSRFMiddleware` + `BodySizeLimitMiddleware`** | Admin currently has no CSRF token; no body limit means DoS via giant upload before `UploadUtils.max_size_bytes` is checked. |
| **`BaseRepository.bulk_create / bulk_update / bulk_upsert`** | Row-by-row inserts are the #1 N+1 bottleneck. SQLAlchemy 2.0 has `insert().values([...])` + `on_conflict_do_update`. |
| **Prometheus `/metrics` endpoint** | `MetricsUtils` already collects the data — needs the Prometheus exposition format for oncall scrape. |
| **Admin CSRF token + `make_csrf_token_dependency`** | Admin accepts POST without a token today. |

## Tier B — when the service grows

- **2FA / TOTP** (`pyotp` wrapper + optional `AdminModel.totp_secret`)
- **Multi-tenant scope** — `TenantScopedRepository(tenant_id)` auto-injects `WHERE tenant_id = …` on every query
- **`SlowQueryLogger`** — SQLAlchemy event logging queries > N ms with `EXPLAIN`
- **`AlembicHelper.safe_upgrade()`** — block destructive migrations (DROP COLUMN/TABLE) without `--force`
- **Graceful shutdown** — drain in-flight requests on `SIGTERM` before uvicorn dies
- **`make_websocket_router`** — bearer auth, heartbeat, broadcast (today SSE only)
- **CLI:** `tempest db seed`, `tempest user create-admin`, `tempest secrets rotate`

## Release plan

### ✅ v0.23.0 — MinIO/S3 storage (shipped)

- `AsyncMinIOClient` (extra `[minio]`) — bucket, object I/O, streaming, presigned URLs

### ✅ v0.24.0 — Pluggable uploads + idempotency + email templates (shipped)

- `UploadStorage` protocol + `LocalUploadStorage` + `MinIOUploadStorage`
- `IdempotencyMiddleware` + `MemoryIdempotencyStore` + `RedisIdempotencyStore`
- `EmailUtils.render_template(template, ctx)` with Jinja2 + autoescape

### v0.25.0 — CLI docker-compose generator

`tempest new` emits a `docker-compose.yaml` based on the installed extras — only spins up Postgres if `[admin]`/`[db]`, only spins up Redis if `[cache]`, etc.

### v0.26.0+ — observability + retries

- `setup_tracing(app, otlp_endpoint=…)` with OTel auto-instrumentation
- `HTTPClient` (typed httpx wrapper) — retry, backoff, `X-Request-ID` propagation
- Prometheus `/metrics` endpoint (built on `MetricsUtils`)

!!! note "This roadmap is honest, not aspirational"
    Items past v0.24.0 only land in the changelog when business
    pressure pulls the next one. This page is refreshed on every
    release — if something should be here and isn't, open an issue.

## How to request a feature

Open an issue at <https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues> describing:

1. The real use case (not the solution).
2. The workaround you use today.
3. Why the workaround hurts (perf, security, ergonomics, maintenance).

Issues with a concrete use case move up the queue — abstractions
without demand don't get in, even when they "would make sense".
