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
| F() / Q() expression wrappers for SQLAlchemy | ✅ v0.111.0 | `tempest_fastapi_sdk.db` (`F` / `Q`) |
| eager-load helper (`BaseRepository.get_by_id(id, with_=...)`) | ✅ v0.109.0 | `with_=` on `get`/`get_or_none`/`get_by_id`/`first`/`list` |
| Signals (`pre_save`/`post_save`/`pre_delete`/`post_delete`) on `BaseRepository` | ✅ v0.109.0 | `tempest_fastapi_sdk.db.signals` (`connect`/`on_signal`) |
| Object-level permissions framework (`user.has_perm("order.delete", obj=order)`) | ✅ v0.110.0 | `tempest_fastapi_sdk.authz` |
| Startup system checks (`tempest check-config`) | ✅ v0.112.0 | `tempest_fastapi_sdk.checks` |
| Management commands framework — project-registered `tempest <cmd>` | ✅ v0.113.0 | `[tool.tempest] commands` + `src/commands.py` |

## Admin panel — evolution

The admin panel already exists (`AdminSite` / `AdminModel` / `make_admin_router`, Jinja + HTMX, CSRF token). The items below take it from "functional CRUD" to "production admin", reusing primitives the SDK already ships (`AuditMixin`, `MetricsUtils`, `TOTPHelper`, `UploadUtils`).

| Feature | Why it matters | Reuses |
|---------|----------------|--------|
| **Per-column filter / search / sort** on the list view | Large lists are unusable without it — the first thing every operator asks for. | `BaseRepository` (filters + pagination) |
| **Bulk actions** (mass delete / activate) | Row-by-row actions don't scale; select N rows + one action is the standard admin flow. | `BaseRepository.bulk_update` / soft-delete |
| **Field widgets** (FK select ✅, date picker, file upload) + **FK autocomplete** ✅ v0.115.0 | FK as `<select>`, dates with a picker, upload via `UploadUtils`; large FKs become an HTMX search box (`autocomplete_fields`). | `UploadUtils` + storage backends |
| **Inline / related editing** ✅ v0.116.0 (read + navigate) | Children (1-N) listed on the parent's detail, with a link to the child admin and "Add" pre-filling the FK (`inlines=[Inline(...)]`). In-place editing on the same screen is a follow-up. | `BaseRepository` + relationships |
| **CSV / JSON export** | Operator exports the filtered result without opening the database. | list view + filters |
| **Audit log visible in the admin** ✅ v0.114.0 | Who changed what and when, straight in the UI — a per-row timeline in the detail view. | `BaseAuditLogModel` + `diff_snapshots` (`AdminModel(audit_model=...)`) |
| **Metrics dashboard** (system ✅) + **business cards** ✅ v0.117.0 | CPU/RAM/counters + value/trend/partition cards computed from your data (`AdminSite(dashboard_cards=[...])`). | `MetricsUtils` + `MetricCard` |
| **MFA on admin login** | Second factor on the most sensitive access in the system; a natural fit now that TOTP exists. | `TOTPHelper` + `MFAMixin` + recovery codes |

## Everything shipped so far

The full release history — every version with its **Added** / **Changed** / **Fixed** entries — lives in the [changelog](changelog.md), in [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. It's the source of truth; this page only highlights what's still missing.

## What's next

The "What's next" queue left from the Tier S/A/B backlog is **cleared** —
the last item (management commands) shipped in v0.113.0. Upcoming
releases are again pulled by business pressure.

Natural candidates when demand shows up (admin panel evolution, already
The admin-panel evolution is **essentially complete**: all of Tiers 1
and 2, and Tier 3 with CSV import (v0.118), granular RBAC (v0.119) and
lenses (v0.120). The only item left is on-demand polish: **in-place**
inline editing of 1-N relations (today listed + navigable).

!!! note "This roadmap is honest, not aspirational"
    Items past the next cuts only land on the changelog when business pressure pulls them. This page is refreshed on every release — if something belongs here and isn't, open an issue.

## Shipped in v0.122.0

Admin refinement — consistency / UX polish:

| Feature | Status | Where |
|---------|--------|-------|
| **Admin polish** | ✅ v0.122 | Fixed the undefined `--tempest-border` (borders fell back to text color) + cards/autocomplete that used the dark sidebar bg; detail reordered (inlines right after the fields, audit/history last) and `JSON` columns pretty-printed on the detail. |

## Shipped in v0.121.0

Admin refinement — new field widgets:

| Feature | Status | Where |
|---------|--------|-------|
| **JSON + time widgets** | ✅ v0.121 | `JSON` columns render as a monospaced JSON editor (pretty-printed on load, parsed + validated on submit); `Time` columns render as `<input type=time>`. [Recipe »](recipes/admin.md) |

## Shipped in v0.120.0

Admin panel — lenses / saved views (Tier 3), closing the admin evolution:

| Feature | Status | Where |
|---------|--------|-------|
| **Lenses** | ✅ v0.120 | `AdminModel(lenses=[Lens("Open", filters={"status": "open"}, order_by="-created_at")])` → tabs above the list; clicking one applies its filters (ANDed with the user's search/filters) + ordering via `?lens=<slug>`. An "All" tab returns to the default. [Recipe »](recipes/admin.md) |

## Shipped in v0.119.0

Admin panel — granular RBAC (Tier 3):

| Feature | Status | Where |
|---------|--------|-------|
| **Granular RBAC** | ✅ v0.119 | `make_admin_router(access_policy=...)` — a `(principal, admin, AdminPermission)` → bool hook consulted for every action (VIEW/CREATE/EDIT/DELETE). Deny → `403`, and the model drops off the dashboard/nav for VIEW. Composes with the `can_*` flags (both must allow). Restricts a non-super admin to subsets of model/action. [Recipe »](recipes/admin.md) |

## Shipped in v0.118.0

Admin panel — CSV import (Tier 3), the counterpart to export:

| Feature | Status | Where |
|---------|--------|-------|
| **CSV import** | ✅ v0.118 | `AdminModel(can_import=True)` exposes `GET/POST /m/{slug}/import`: upload a CSV and each row is validated/coerced like the create form and becomes a record. Report with the created count + per-row errors (best-effort: one bad row never aborts the others). "Import CSV" link on the list view. [Recipe »](recipes/admin.md) |

## Shipped in v0.117.0

Admin panel — business-metric cards on the dashboard (closes Tier 2 of
the admin evolution):

| Feature | Status | Where |
|---------|--------|-------|
| **Dashboard business metrics** | ✅ v0.117 | `AdminSite(dashboard_cards=[MetricCard(label, compute)])` renders cards at the top of the dashboard, computed from your data: `MetricValue` (a number), `MetricTrend` (vs previous period, with delta/%/direction) and `MetricPartition` (breakdown with bars). Distinct from the CPU/RAM panel. A card whose compute raises is skipped (never blanks the page). [Recipe »](recipes/admin.md) |

## Shipped in v0.116.0

Admin panel — inlines / nested relations (Tier 2 of the admin evolution):

| Feature | Status | Where |
|---------|--------|-------|
| **Inlines (read + navigate)** | ✅ v0.116 | `AdminModel(inlines=[Inline(Child, Child.parent_id)])` lists the 1-N children on the parent's detail view as a table, with a link to the child admin and "Add" pre-filling the FK (via a create query param). Reuses the child admin's `list_display`/CRUD. In-place editing on the same screen is a follow-up. [Recipe »](recipes/admin.md) |

## Shipped in v0.115.0

Admin panel — autocomplete FK fields (Tier 2 of the admin evolution):

| Feature | Status | Where |
|---------|--------|-------|
| **Autocomplete FK** | ✅ v0.115 | `AdminModel(autocomplete_fields=[...])` swaps the all-rows `<select>` for an HTMX search box — no 1000-row cap, no raw-UUID fallback. The `/m/{slug}/autocomplete/{field}` endpoint searches the target admin's `search_fields` (ILIKE, OR), capped at 20; edit pre-fills the current label. [Recipe »](recipes/admin.md) |

## Shipped in v0.114.0

Admin panel — per-row audit-history viewer (the first Tier 1 item of the
admin evolution):

| Feature | Status | Where |
|---------|--------|-------|
| **Audit history viewer** | ✅ v0.114 | `AdminModel(audit_model=...)` renders a per-row change timeline in the detail view, read from the `BaseAuditLogModel` (matched on `entity` + `entity_id`), with a field-by-field before/after diff and actor/date per entry. Pair it with `BaseRepository(audit_model=...)` + `add_audited`/`update_audited`/`delete_audited`. [Recipe »](recipes/admin.md) |

## Shipped in v0.113.0

Management-commands framework — a service plugs its own commands into the
`tempest` CLI:

| Feature | Status | Where |
|---------|--------|-------|
| **Management commands** | ✅ v0.113 | Expose a `typer.Typer` named `commands` in `src/commands.py` (auto-detected; or `[tool.tempest] commands = "..."`) → it becomes `tempest <cmd>`, alongside the built-ins. Collision with a built-in → built-in wins (warning). Plain Typer (args/options/types/groups). [Recipe »](recipes/management-commands.md) |

## Shipped in v0.112.0

Django-style system-check framework + the `tempest check-config` CLI:

| Feature | Status | Where |
|---------|--------|-------|
| **System checks** | ✅ v0.112 | `tempest_fastapi_sdk.checks`: `@check` registers a `(settings) -> [CheckMessage]` validator; built-ins for empty/weak secret, CORS `*`+credentials, SQLite-in-prod, DEBUG, `0.0.0.0` bind. `tempest check-config` runs them all (auto-detects settings, `--tag`/`--fail-level`, exits non-zero on ERROR); `run_system_checks(settings)` aborts a misconfigured boot in the lifespan. [Recipe »](recipes/system-checks.md) |

## Shipped in v0.111.0

Django-style `F` / `Q` wrappers over SQLAlchemy, wired into
`BaseRepository`:

| Feature | Status | Where |
|---------|--------|-------|
| **`F` (column expression)** | ✅ v0.111 | `F("stock") - 1` computes in the database in one statement — atomic update, no race. Arithmetic from either side and between columns; resolved in `bulk_update`. [Recipe »](recipes/database.md) |
| **`Q` (composable conditions)** | ✅ v0.111 | `Q(status="open") \| Q(...)`, `&`, `~` for the `OR`/`NOT` the filter dict can't express; same conventions (`field__gte`, `name` ILIKE, list → `IN`). `where=` on `list`/`first`/`get`/`get_or_none`/`count`/`exists`/`paginate`/`delete_many`. [Recipe »](recipes/database.md) |

## Shipped in v0.110.0

Object-level authorization — the question the static guard can't answer:
"may this user edit **this** object?".

| Feature | Status | Where |
|---------|--------|-------|
| **Object-level permissions** | ✅ v0.110 | `tempest_fastapi_sdk.authz`: register a `(user, obj) -> bool` rule with `@permission("order.delete")`, check with `has_perm`/`check_permission`, guard the route with `make_permission_checker`. Superuser bypass + static fallback injectable via `PermissionRegistry`; wildcards (`order.*`/`*`); sync or async handlers; `PermissionMixin` gives `await user.has_perm(...)`. [Recipe »](recipes/authz.md) |

## Shipped in v0.109.0

Two `BaseRepository` upgrades, both pulled from "What's next" above:

| Feature | Status | Where |
|---------|--------|-------|
| **Eager-load (`with_=`)** | ✅ v0.109 | `get`/`get_or_none`/`get_by_id`/`first`/`list` accept `with_=["author", "books.reviews"]` (dotted paths for nested); uses `selectinload`, so N related rows cost one extra query, not N. Kills the `MissingGreenlet` error from touching a relationship outside the async context. [Recipe »](recipes/database.md) |
| **Lifecycle signals** | ✅ v0.109 | `tempest_fastapi_sdk.db.signals`: `connect`/`on_signal`/`disconnect` register sync or async handlers per model for `PRE_SAVE`/`POST_SAVE`/`PRE_DELETE`/`POST_DELETE`. They fire on the unit-of-work path (`add`/`update`/`delete`/…); the set-based bulk methods bypass by design. A `PRE_SAVE` handler that raises vetoes the write. [Recipe »](recipes/database.md) |

## Shipped in v0.107.0 / v0.108.0

End-to-end self-hosted GenAI parity — an AI chat app running in-process, so a
separate inference microservice becomes an organizational choice rather than a
necessity:

| Feature | Status | Where |
|---------|--------|-------|
| **Ollama backend** (`OllamaGenerator` / `OllamaEmbedder`) | ✅ v0.107 | Pure HTTP (no torch), drop-in on `make_genai_router` / `Retriever`. Extra `[genai-ollama]`. [Recipe »](recipes/genai.md) |
| **Ollama vision + tool-calling** | ✅ v0.108 | `generate(images=…)` + per-message `images` on `chat()` + `chat_with_tools()`. [Recipe »](recipes/genai.md) |
| **STT parity** | ✅ v0.108 | `beam_size` / `vad_filter` (default + per-call override) + `language_probability` on `Transcription`. [Recipe »](recipes/genai.md) |
| **`ChromaVectorStore`** | ✅ v0.108 | `VectorStore` over ChromaDB (ephemeral / persistent / injected client). Extra `[genai-chroma]`. [Recipe »](recipes/genai.md) |
| **`ChatMemory`** | ✅ v0.108 | Per-user long-term memory over Chroma: embed + upsert with quota eviction, search with similarity floor + recency decay. [Recipe »](recipes/genai.md) |
| **`AIChatPipeline`** | ✅ v0.108 | Orchestrator: memory → web-search → generate (with a tool-calling loop) → TTS → index. `Tool` + `make_ai_chat_router` (`/chat` + `/chat/stream` SSE, stateless). [Recipe »](recipes/genai.md) |

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
