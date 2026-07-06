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

## GenAI — upcoming slices

The `tempest_fastapi_sdk.genai` module already covers hardware checks, RAG
(web + PDF + vector store), a local LLM (`TextGenerator`), embeddings
(`Embedder` + `cosine_similarity`), scale (`BatchScheduler` /
`ModelRegistry`) and audio (STT/TTS + PT-BR/EN-US presets). Next
refinements, reusing what's there:

| Feature | Status | What it is |
|---------|--------|-----------|
| **Typed `GenerationConfig`** | ❌ planned | Pydantic schema for generation params (`max_new_tokens`, `temperature`, `top_p`, `stop`, …) passed to `TextGenerator` instead of loose `**kwargs` — self-describing, validated, reusable. |
| **`make_genai_router`** | ❌ planned | Opt-in FastAPI router with ready endpoints (`/generate`, `/embed`, `/rag`, `/transcribe`, `/tts`) wired to the `genai` objects — like `make_auth_router`. Token streaming over SSE. |
| **`RedisEmbeddingCache`** | ❌ planned | `EmbeddingCache` over `AsyncRedisManager` (today only `InMemoryEmbeddingCache`) — a vector cache shared across workers. |

## Planned application modules

Ready domain modules on top of the SDK primitives (`BaseModel` /
`BaseRepository` / `BaseService` / pagination / auth), in the spirit of
auth and admin: the service inherits the concrete table and mounts the
router.

### Base chat service (messages)

| Piece | Sketch |
|-------|--------|
| `BaseConversationModel` / `BaseMessageModel` | Abstract tables (project inherits + picks the user FK): `conversation_id`, `sender_id`, `body`, `created_at`. |
| `ChatService` | `start_conversation(participants)`, `post_message(conversation_id, sender, body)`, `list_messages(conversation_id, paginate)`, `list_conversations(user)`. Cursor pagination (history), message soft-delete. |
| `make_chat_router` (opt-in) | `POST /chat/conversations`, `POST /chat/conversations/{id}/messages`, `GET .../messages` (cursor). Auth via the SDK user dependency. |
| Real time | Push new messages via `SSEBroker` (channel = `conversation_id`) — reuses the existing SSE. |

Reuses: `BaseModel`, `BaseRepository`, cursor pagination, `current_user`,
`SSEBroker`.

### Comments + ratings (0–5 stars)

| Piece | Sketch |
|-------|--------|
| `BaseCommentModel` | Polymorphic comment (`target_type` + `target_id`), `author_id`, `body`, optional thread (`parent_id`). |
| `BaseRatingModel` | **0–5 star** rating per user per target (`target_type` + `target_id` + `user_id` unique), with `RatingField` (`Annotated[int, 0..5]`). |
| `ReviewService` | `add_comment(...)`, `rate(target, user, stars)` (upsert — one vote per user), `aggregate(target)` → average + count + distribution (how many 1★…5★). |
| `make_reviews_router` (opt-in) | `POST /reviews/{type}/{id}/comments`, `POST /reviews/{type}/{id}/rating`, `GET /reviews/{type}/{id}` (paginated comments + star aggregate). |

Reuses: `BaseModel`, `BaseRepository` (+ bulk/aggregation), validated
fields (new `RatingField` in `utils.fields`), pagination, auth.

!!! note "Business-pulled order"
    These modules land when demand asks — chat and reviews are strong
    candidates, cutting across products (marketplace, support, social).
    GenAI refines in parallel.

## How to request a feature

Open an issue at <https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues> describing:

1. The real use case (not the solution).
2. The workaround you use today.
3. Why the workaround hurts (perf, security, ergonomics, maintenance).

Issues with concrete use cases move up the queue — abstractions without demand don't land, even when they "would make sense".
