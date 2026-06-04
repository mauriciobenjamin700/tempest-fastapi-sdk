# CLAUDE.md — tempest-fastapi-sdk

Project-specific guidance for Claude Code working in this repository.
The global instructions at `~/.claude/CLAUDE.md` apply too — this file
only documents what is *different* or *load-bearing* for this SDK.

## What this is

`tempest-fastapi-sdk` is a **PyPI-distributed library**, not a
deployable service. It ships the shared FastAPI/SQLAlchemy/Pydantic
building blocks every Tempest service imports.

Two structural consequences:

- **Flat layout.** The package directory `tempest_fastapi_sdk/` lives
  at the repo root, next to `pyproject.toml`. **No `src/` wrapper.**
  Tests live in `tests/` at the root. This contradicts the
  service-layout rule in the global `CLAUDE.md` on purpose — detecting
  a `src/tempest_fastapi_sdk/` directory is a defect, flag it before
  adding features.
- **Every public surface change ships docs in the same commit.**
  README install snippets, `CHANGELOG.md`, the MkDocs site under
  `docs/` (bilingual PT-BR + EN-US), and the API reference must all
  reflect the new shape **before** the `vX.Y.Z` tag is pushed. See
  the "Documentation must follow the code" section in the global
  `CLAUDE.md`.

## Release flow

```bash
# 1. bump version
sed -i 's/version = "X.Y.Z"/version = "X.Y.Z+1"/' pyproject.toml
sed -i 's/__version__: str = "X.Y.Z"/__version__: str = "X.Y.Z+1"/' tempest_fastapi_sdk/__init__.py

# 2. CHANGELOG entry under ## [X.Y.Z+1] — YYYY-MM-DD (Keep a Changelog format)

# 3. update relevant docs/recipes/*.md (and the .en.md mirror)

# 4. gate
UV_PYTHON=3.11 make check                 # ruff + mypy + 661+ tests
UV_PYTHON=3.11 uv run --group docs mkdocs build --strict
UV_PYTHON=3.11 make smoke                 # import-test the wheel

# 5. commit + tag + push
git add -A && git commit -m "feat: vX.Y.Z+1 — <subject>"
git tag vX.Y.Z+1
git push origin main && git push origin vX.Y.Z+1
```

CI on tag push runs `release-pypi.yml` (trusted-publishing — no
token), then `docs.yml` redeploys GitHub Pages. Don't push a tag
without the docs being green.

## Roadmap — features we still owe

The SDK currently covers: auth (JWT/bearer/role/permission/X-Token),
DB (async manager + repository + Alembic helper + base model +
mixins), exceptions, structured logging + per-level files + `/logs`
endpoint, metrics (CPU/RAM/GPU/Disk), rate limiting, pagination
(offset + cursor), settings mixins, SSE, throttle, upload/download,
WebPush, webhook signatures, BR validators (CPF/CNPJ/CEP/phone),
admin panel (Jinja + HTMX), email (SMTP), Redis cache, FastStream
queue, TaskIQ tasks, **MinIO / S3 object storage**
(`AsyncMinIOClient` via the `[minio]` extra — bucket lifecycle,
object I/O, streaming download, presigned URLs), hardened static
files, server runner, health, tool-spec router, request-id
middleware, CORS, CLI scaffolder.

The list below is the deliberate next-version plan. Each tier is
ordered by impact for a typical production FastAPI service.

### Tier S — every serious API needs these

| Feature | Why it matters |
|---------|----------------|
| **`IdempotencyMiddleware`** + `idempotency_keys` table | Required header for POST on payment/webhook/retry paths. Without it, retried requests duplicate rows. Stripe/AWS pattern. |
| **`UploadUtils` pluggable backends** (`LocalBackend`, `S3Backend(bucket, region)`, `GCSBackend`) | Today only writes to local disk — unusable in any multi-replica deploy. |
| **OpenTelemetry tracing** — `setup_tracing(app, otlp_endpoint=…)` | `RequestIDMiddleware` correlates logs but doesn't give cross-service spans. Needs auto-instrumentation for FastAPI/SQLAlchemy/httpx. |
| **`HTTPClient` (typed httpx wrapper)** | Retry + backoff, `X-Request-ID` propagation, circuit breaker, default timeouts. Today every service rolls raw httpx. |
| **Outbox pattern** — `BaseRepository.save_with_outbox(model, event)` | Persists event in the same tx as the INSERT; `AsyncBrokerManager` drains it. Without this, events are lost when the broker fails after commit. |

### Tier A — common in SaaS backends

| Feature | Why it matters |
|---------|----------------|
| **`EmailUtils.render_template(path, ctx)`** with Jinja2 | Welcome / reset / verify emails — today SMTP only accepts raw strings. |
| **OAuth2 / OIDC providers** — `GoogleOAuthClient`, `GitHubOAuthClient`, `OIDCProvider(discovery_url)` | `JWTUtils` only signs our own tokens; we have no social-login glue. |
| **`CSRFMiddleware` + `BodySizeLimitMiddleware`** | Admin module currently has no CSRF token; no body limit means DoS via giant upload before `UploadUtils.max_size_bytes` is checked. |
| **`BaseRepository.bulk_create / bulk_update / bulk_upsert`** | Row-by-row inserts are the #1 N+1 bottleneck. SQLAlchemy 2.0 has `insert().values([...])` + `on_conflict_do_update`. |
| **Prometheus `/metrics` endpoint** | `MetricsUtils` already collects the data — needs the Prometheus exposition format for oncall scrape. |
| **Admin CSRF token + `make_csrf_token_dependency`** | Admin accepts POST without a token today. |

### Tier B — when the service grows

- **2FA / TOTP** (`pyotp` wrapper + optional `AdminModel.totp_secret`)
- **Multi-tenant scope** — `TenantScopedRepository(tenant_id)` auto-injects `WHERE tenant_id = …` on every query
- **`SlowQueryLogger`** — SQLAlchemy event logging queries > N ms with EXPLAIN
- **`AlembicHelper.safe_upgrade()`** — block destructive migrations (DROP COLUMN/TABLE) without `--force`
- **Graceful shutdown** — drain in-flight requests on SIGTERM before uvicorn dies
- **`make_websocket_router`** — bearer auth, heartbeat, broadcast (today SSE only)
- **CLI:** `tempest db seed`, `tempest user create-admin`, `tempest secrets rotate`

### Planned release cadence

- **v0.23.0 — observability + retries** (high return, low cost)
    - `setup_tracing(app, otlp_endpoint=…)` with OTel auto-instrumentation
    - `HTTPClient` (typed httpx wrapper)
    - Prometheus `/metrics` endpoint
- **v0.24.0 — cloud uploads + idempotency** (unblocks multi-replica deploys)
    - `UploadUtils` with pluggable backends + `S3Backend`
    - `IdempotencyMiddleware` + `idempotency_keys` table on `BaseModel`
    - `EmailUtils.render_template`

Anything beyond v0.24.0 is bumped from the roadmap when business
pressure picks the next item — keep the roadmap honest, not
aspirational.

## Conventions specific to this repo

- **Typed examples in docs.** Every code block in `README.md`,
  `docs/`, `tempest_fastapi_sdk/cli/_templates/*.tmpl` MUST have full
  type annotations (params + return). User explicitly rejected
  "magic Django-style" untyped APIs.
- **No emojis in code or docs** unless the user explicitly asks.
- **Bilingual docs.** Every page lives twice: `docs/<page>.md`
  (PT-BR, default) and `docs/<page>.en.md` (EN-US). The MkDocs
  `mkdocs-static-i18n` plugin renders both. Forgetting the `.en.md`
  mirror is a structural defect, not a polish item.
- **Bind defaults: `127.0.0.1`** in CLI-generated templates;
  `0.0.0.0` only when a frontend on a different origin consumes
  the service.
- **Logging tests must pass `file_output=False`** to avoid stray
  `logs/` folders in cwd. The default behavior writes to disk
  (since v0.22.0).
