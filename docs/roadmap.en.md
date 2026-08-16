# Roadmap

What the SDK **doesn't ship yet** + what already landed. Sorted by impact, not implementation order — the current release is pulled by business pressure, not list position.

!!! tip "What the SDK already covers"
    Full auth (JWT/bearer/role/permission/X-Token + bundled signup/activate/login/reset via `UserAuthService` + `make_auth_router`), OAuth2/OIDC (Google/GitHub + generic), Firebase ID token verification (`FirebaseAuth` + `FirebaseIdentity` + `FirebaseUserResolver`, `[firebase]` extra), CSRF middleware, DB (`AsyncDatabaseManager` + `BaseRepository` + bulk ops + `AlembicHelper` + `BaseModel` + `BaseUserModel` + `BaseUserTokenModel` + audit/soft-delete mixins + Alembic hook reordering base columns), standardized exceptions, structured logging + per-level files + `/logs` endpoint, metrics (CPU/RAM/GPU/Disk + Prometheus `/metrics` + `PrometheusMiddleware`), rate limiting, idempotency (`IdempotencyMiddleware` + memory/Redis stores), body-size limit, pagination (offset + cursor), settings mixins with `title`/`description`/`examples`, SSE, throttle, local upload/download + pluggable storage (`LocalUploadStorage` + `MinIOUploadStorage`), MinIO/S3 (`AsyncMinIOClient`), WebPush + unified web/mobile push (`DeviceService` + `WebPushTransport` + `FCMTransport`), webhook signatures, BR validators (CPF/CNPJ/CEP/phone), admin panel (Jinja + HTMX, Django-admin parity — list view with search/filters/sortable columns, full CRUD, bulk actions, CSV/JSON export, FK-select widgets, dashboard with counts + metrics, TOTP MFA at login, `created_by`/`updated_by` audit trail), email (SMTP + Jinja2 templates), Redis cache, FastStream queue, TaskIQ tasks, hardened static files, server runner, health, tool-spec router, request-id middleware, CORS, typed HTTP client (`HTTPClient` httpx wrapper with retry/backoff/circuit-breaker), full CLI (`tempest new`, `tempest generate --docker` — compose credentials resolved from `.env` via `${VAR:-default}`, not hardcoded —, `tempest db <subcommand>`, `tempest user <subcommand>`, quality gates).

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

The Tier S/A/B backlog and the admin-panel evolution (Tiers 1–3 + refinement)
are **done**; so is the self-hosted genai roadmap (v0.139–0.154 — tool calling,
structured output, VLM, reranker, hybrid search, ONNX embeddings, generation
cache, token/context, vision router, metrics, moderation + pipeline
integration). The candidates below came out of the 2026-07-24 overview
analysis and are **all shipped too** — the last two, advanced rate limiting
and WebAuthn, landed in v0.216.0 and v0.217.0.

**The queue is empty.** The next theme comes from business pressure, not from
this page. Keeping an aspirational item here just because the section would
look short is exactly what the footer note forbids.

### ✅ GenAI hardening — transformers 5.x debt (shipped v0.155.0)

Constrained structured output now works on transformers 5.x
(`build_prefix_allowed_tokens_fn` reimplemented from the `lm-format-enforcer`
**core**, without the broken `integrations.transformers` module), and
`VisionTextGenerator` loads on tf5 (`AutoModelForImageTextToText` +
`torchvision` in the `[genai-vlm]` extra). Both validated on GPU (Qwen2.5-3B /
Qwen2-VL-2B). Remaining caveat: multimodal accuracy depends on per-family
processor wiring (non-blocker) — see `planning/genai/manual-validation.md`.

### Queue observability + genai tracing

Delivered in slices, one release per slice:

- ✅ **OTel spans on genai calls** (`generate`/`chat`/`embed`/`rag`) — shipped
  in **v0.156.0**. The ambient `genai_span` reuses the `TracerProvider` from
  `setup_tracing` (GenAI semantic conventions); no-op without the `[otel]`
  extra. See the genai recipe (*Distributed tracing*).
- ✅ **`TaskQueue`: retry + dead-letter + per-task metrics** — shipped in
  **v0.157.0**. `RetryPolicy` + `enable_retries`, `DeadLetterSink` +
  `dead_letter` (target is yours, no backend assumed), `TaskMetrics` into the
  shared `/metrics`. Opt-in middleware, imports without the `[tasks]` extra.
  See the queue recipe (*Task reliability and observability*).
- ✅ **Task panel in the admin** — shipped in **v0.158.0** as dead-letter +
  inventory (not a Flower clone). `BaseDeadLetterModel` +
  `make_dead_letter_model`, `DbDeadLetterSink` (persists terminal failures),
  `make_dead_letter_admin_model` (read-mostly AdminModel + **requeue** action),
  `task_inventory` (registered tasks). No live queue introspection — TaskIQ
  exposes none (Flower is Celery-specific); it shows what is real and
  persisted. See the queue recipe (*Dead-letter panel in the admin*).

### HTTP performance layer

- ✅ **`ResponseCacheMiddleware`** — shipped in **v0.159.0**. ETag / conditional
  GET (`304`) always on + opt-in server-side cache
  (`ResponseCacheStore`/`Memory`/`Redis`), respects `no-store`/`private`/
  `Set-Cookie`, keyed by `vary=`. See the HTTP recipe (*HTTP response cache*).
- ✅ **Advanced rate limiting** — shipped in **v0.216.0**. `RateLimitRule`
  (sliding window, or a token bucket when `burst` is set),
  `StaticRateLimitPolicy` / `PlanRateLimitPolicy` (+ `plan_by_jwt_claim` /
  `plan_by_header` / `key_by_plan_principal`) and `MemoryQuotaStore` /
  `RedisQuotaStore`, which decide the whole list before writing any rule — a
  rejected request never spends the others' budget. `RateLimit-*` response
  headers. See the HTTP recipe (*Tolerating bursts: the token bucket*).

### ✅ Modern auth — WebAuthn / passkeys (shipped v0.217.0)

`WebAuthnService` runs both ceremonies (registration and login) over `fido2`,
`BaseWebAuthnCredentialModel` + `make_web_authn_credential_model` hold the
public keys, and `make_auth_router(webauthn=...)` mounts the six routes when
`AUTH_WEBAUTHN_ENABLED` is on. Beyond what the library verifies, the SDK
refuses a signature counter that did not advance (the cloned-authenticator
signal), spends the challenge on use, and never reveals whether an account
exists in `begin`. Memory or Redis (`GETDEL`) challenge store. Extra
`[webauthn]`. [Recipe »](recipes/webauthn.md)

**Out of scope by decision:** OpenAI-compatible client (self-hosted focus),
GraphQL/gRPC (REST by decision).

!!! note "This roadmap is honest, not aspirational"
    Items past the next cuts only land on the changelog when business pressure pulls them. This page is refreshed on every release — if something belongs here and isn't, open an issue.

## Shipped in v0.173.0

Modelops — export, measure and quantize the models a service serves:

| Feature | Status | Where |
|---------|--------|-------|
| **CPU/RAM/GPU/energy benchmarking** | ✅ v0.173 | `benchmark` times any callable; `benchmark_onnx`/`benchmark_torch`/`benchmark_models` build on it. Discarded warm-up plus N repetitions, median and IQR first (latency is heavy-tailed), p95/p99, throughput, RSS peak and delta, GPU memory. [Modelops »](recipes/modelops.md) |
| **Energy measurement with provenance** | ✅ v0.173 | `NvmlPowerSampler` (prefers the driver's total-energy counter, falls back to integrating power), `NvidiaSmiPowerSampler`, `RaplEnergySampler` (CPU package energy via powercap, wraparound handled) and `NullPowerSampler`. Every number carries an `EnergySource` — none of them is wall-plug. A CPU run resolves no GPU sampler. |
| **Ranking: composite score + Pareto** | ✅ v0.173 | `composite_scores` with weights renormalized over the dimensions actually measured, `pareto_points` skipping unmeasured axes instead of assuming the best, `rank` → `BenchmarkReport` with the effective weights and the host description. |
| **`.onnx` → `.ort` and graph optimization** | ✅ v0.173 | `export_onnx_to_ort` (file or directory, `FIXED`/`RUNTIME` style, `target_platform`, type reduction, `.required_operators.config` for the minimal build), `export_torch_to_onnx` and `optimize_onnx_graph`. |
| **ONNX and HuggingFace quantization** | ✅ v0.173 | `quantize_onnx_dynamic`, `quantize_onnx_static` (calibration reader from any iterable of feeds), and the transformers-export path on ONNX Runtime's own tooling: `optimize_hf_onnx` (`O1`–`O4`), `quantize_hf_onnx` (arm64/avx2/avx512/avx512_vnni) — plus `quantize_hf_bnb` (int4/int8 in PyTorch). No `optimum` dependency, so nothing here caps your `transformers` version. |
| **`tempest model` CLI** | ✅ v0.173 | `analyze` / `bench` / `optimize` / `quantize` / `export-ort` / `hardware`, with `--json` on the reporting commands. A missing extra exits 2 with the install line. [CLI »](recipes/cli.md) |

## Shipped in v0.168.0

Permission guards with metadata:

| Feature | Status | Where |
|---------|--------|-------|
| **Second parameter `meta: dict[str, Any]`** | ✅ v0.168 | A guard may declare a second parameter and receive metadata, which turns a generic guard into a route-specific check: write `has_role` once and let each call site declare `meta={"role": "manager"}`. One-parameter guards are untouched — the second argument only goes to guards that declare it. [Reference »](recipes/permission-guards.md) |
| **`include_args=True`** | ✅ v0.168 | Merges the call's arguments (path params, body, other dependencies) into the same dict, so an ownership guard reads `meta["order_id"]` without the route handing anything over. User excluded, omitted parameters contribute their default, `Depends(...)` markers dropped, `meta=` literals winning over same-named arguments. |
| **Misuse still caught at import** | ✅ v0.168 | `TempestPermissionError` when `meta=` is not a mapping, when `meta=`/`include_args=` have no guard to receive them, and when a guard asks for 3+ parameters. `guard_metadata(fn)` exposes the declared literals. |
| **New static checks** | ✅ v0.168 | `tempest permissions` gained `meta-unused` (error), `guard-meta-missing`, `guard-meta-annotation` and `meta-key-collision` (warnings); `guard-arity` now accepts 1 or 2 parameters. The `meta-unused` verdict is held back when a guard in the decoration could not be resolved. |

## Shipped in v0.167.0

Permission guards — decorator plus a two-layer linter:

| Feature | Status | Where |
|---------|--------|-------|
| **`@requires(*guards, user_param=None)`** | ✅ v0.167 | Runs guards that take the user and return the user (or `None`) before the body, on a route, a controller or a service, sync or `async`. A guard denies by raising an `AppException`; a non-`None` return replaces the user the next guard and the body see — that is how `require_active` narrows `Optional[UserT]` to `UserT`. The user parameter resolves from the annotation (`BaseModel`/`BaseUserModel`), `user_param=` breaks a tie. The signature is preserved, so dependency injection and the OpenAPI schema stay untouched. [Reference »](recipes/permission-guards.md) |
| **Misuse caught at import time** | ✅ v0.167 | `TempestPermissionError` for `@requires()` with no guard, a non-callable guard, wrong arity, an `async` guard on a sync function, and a missing or ambiguous user parameter. The application refuses to start with a check that never fires. |
| **Contract warning at call time** | ✅ v0.167 | `GuardContractWarning` when a guard raises outside the `AppException` hierarchy (the API would answer 500 with no `code`) or returns a non-user value such as `False` (a denial that would be ignored). The original exception still propagates. |
| **`tempest permissions --check` / `--strict` / `--path`** | ✅ v0.167 | Static check (`ast`, without importing the app) for what runtime cannot see: a guard whose `raise` no test exercises, a guard never wired. Errors `no-guards`/`user-param-missing`/`user-param-ambiguous`/`guard-arity`/`guard-async-in-sync`/`guard-returns-bool`/`guard-foreign-exception`; warnings `guard-never-denies`/`guard-missing-annotation`/`guard-return-type`/`guard-unresolved`. An ambiguous or out-of-scope guard is reported, never guessed. |
| **OpenAPI error-docs integration** | ✅ v0.167 | `tempest openapi-errors` follows the `@requires` guards, so a guard's exception shows up as `undocumented` until the route declares it — and `--fix` writes it. `declared_guards` / `guarded_user_param` expose a route's guards for auditing. |

## Shipped in v0.166.0

Errors in OpenAPI — automatic fix:

| Feature | Status | Where |
|---------|--------|-------|
| **`tempest openapi-errors --fix`** | ✅ v0.166 | Writes the declarations `--check` was pointing at: injects `responses=error_responses(...)` into the route, extends an existing declaration (`error_responses` or `@raises`) preserving its order, and adds the missing imports. Edits anchored on AST positions, output passed through `ruff check --select I --fix` + `ruff format`. [Reference »](recipes/openapi-errors.md#step-5-fix-writes-the-declarations-for-you) |
| **Only ever adds, on a clean tree** | ✅ v0.166 | `unreachable` findings are never removed — reachability cannot see a dynamic raise, so deleting on its word would remove a correct declaration. Requires a clean git tree, so `git diff` is the review and `git checkout` the undo; `--dry-run` prints the formatted diff and runs on a dirty tree. |
| **Formatting with the project's config** | ✅ v0.166.1 | The scratch file handed to ruff is created next to the file being rewritten, so the project's `line-length` and `isort` sections apply — and what gets written passes its own CI's `ruff format --check`. When no working ruff is found (`PATH`, `python -m ruff`, `uv run ruff`, each probed with `--version`), the command writes anyway and says so. |

## Shipped in v0.163.0

A React SPA served by FastAPI itself:

| Feature | Status | Where |
|---------|--------|-------|
| **`make_spa_router(dist_dir)`** | ✅ v0.163 | Serves a Vite/React `dist/` with history fallback, an inverted cache policy (document `no-store`, hashed assets `immutable`) and API prefixes excluded from the fallback. [Recipe »](recipes/react-spa.md) |
| **`tempest generate --dockerfile` with an SPA stage** | ✅ v0.163 | Detects `web/`, `frontend/`, `client/` or `ui/` and emits a Node stage running `npm ci && npm run build` ahead of the Python stage, copying only `dist/` into the final image. |

## Shipped in v0.161.0

Code generation from an OpenAPI specification:

| Feature | Status | Where |
|---------|--------|-------|
| **`tempest openapi-client <spec>`** | ✅ v0.161 | Point it at the spec (URL or file, JSON or YAML) and get `<src\|app>/integrations/<name>/` with `schemas.py` + `client.py`. The end of transcribing a third party's documentation by hand. `--name`/`--out`/`--header`/`--schemas-only`/`--force`/`--no-format`. [Reference »](recipes/openapi-client.md) |
| **Schemas with metadata** | ✅ v0.161 | One `BaseSchema` class per component, with the **spec's** `title`/`description`/`examples` on every `Field` — the generated module is the integration's documentation. Python names + the wire name as `alias` + `populate_by_name`; reserved words resolved (`class` → `class_`); optional collections as empty lists; enums as `BaseStrEnum`/`BaseIntEnum`; `allOf` flattened; recursion via `model_rebuild()`. Nothing is invented where the spec documents nothing. [Reference »](recipes/openapi-client.md#schemaspy) |
| **Typed HTTP client** | ✅ v0.161 | One `async` method per operation, over an **injected** `HTTPClient` — retry/backoff/circuit-breaker/credentials stay with the caller, and `httpx.MockTransport` tests the whole integration offline. Typed path/query params, validated body and response, full Google docstrings. [Reference »](recipes/openapi-client.md#clientpy) |
| **Output that passes your gates** | ✅ v0.161 | The emitted code passes `ruff check` + `ruff format --check` **before** the formatting pass (tested against the raw output), so `--no-format` or a machine without ruff still yields a usable package. Regenerating an unchanged spec produces a byte-for-byte identical file, so the `git diff` of a `--force` is the integration's changelog. |
| **It never guesses** | ✅ v0.161 | An unrepresentable construct (`not`, external `$ref`, Swagger 2.0, non-JSON body, header param) becomes `Any` + a `# openapi: unsupported` comment + a line in the command's summary. A wrong schema that looks right is worse than a documented gap. |

## Shipped in v0.160.0

Errors documented in OpenAPI:

| Feature | Status | Where |
|---------|--------|-------|
| **`ErrorResponseSchema`** | ✅ v0.160 | The `{detail, code, details}` envelope the handlers already emitted now exists as an exported schema — before there was nothing to point a hand-written `responses={409: ...}` at. [Reference »](recipes/openapi-errors.md#errorresponseschema) |
| **`error_responses(*exceptions)`** | ✅ v0.160 | Builds FastAPI's `responses=` from the exception classes. Groups by status (OpenAPI allows one response object per status) and distinguishes the `code`s through an `examples` map — Swagger/ReDoc render it as a selector, so two 404s with different codes stay visible. `summary` from `__doc__`, `detail` from `message` or a `MessageCatalog`. [Reference »](recipes/openapi-errors.md#step-2-error_responsesexceptions) |
| **`@raises(...)` + `TempestAPIRouter`** | ✅ v0.160 | The same declaration next to the handler; the router expands the tag into `responses=` before the route is constructed (so the model reaches `components.schemas` as a `$ref`). An explicit `responses=` wins per status. [Reference »](recipes/openapi-errors.md#step-3-raises-tempestapirouter) |
| **`InheritedErrorCodeWarning`** | ✅ v0.160 | A subclass that declares no `code` of its own and inherits a generic SDK one warns at class creation — the silent defect that had a subclass emitting `code: "CONFLICT"` for months in production. Does not fire for a domain `code`, nor when `message_key` is declared. [Reference »](recipes/openapi-errors.md#the-warning-that-catches-the-silent-defect) |
| **`tempest openapi-errors --check`** | ✅ v0.160 | Compares, per route, the declaration against what is reachable through `router -> controller -> service -> repository`. Static (`ast`, no application import), reads `raise` statements **and** `Raises:` sections. Reports `undocumented` + `unreachable`, exits non-zero as a CI gate. [Reference »](recipes/openapi-errors.md#step-4-tempest-openapi-errors-check) |

## Shipped in v0.129.0

SSR — typed attribute builders:

| Feature | Status | Where |
|---------|--------|-------|
| **`htmx()` / `aria()` / `data()`** | ✅ v0.129 | Assemble a widget's open `attrs: dict[str, str]` from typed arguments — `hx-*`/`aria-*`/`data-*` move from stringly-typed dicts to autocompleted, statically-checked call sites. Return exactly the dict you'd write (mergeable). No magic, no new dependency. [Reference »](ssr.md#typed-attributes-htmx-aria-data) |

## Shipped in v0.128.0

SSR — serve a compiled tempestweb build:

| Feature | Status | Where |
|---------|--------|-------|
| **`make_web_app_router` + `build_web_app` + `detect_build_mode`** | ✅ v0.128 | Host a `tempestweb build` artifact straight from FastAPI: `make_web_app_router(dir)` serves the **wasm** (static SPA) build as an `APIRouter` with a history fallback, correct MIME, shell/SW cache rules, no imposed CSP (Pyodide); `build_web_app(dir)` hosts the **server** (WebSocket/SSE) build as a sub-app. Only serves a prebuilt `dist/` — building stays in the tempestweb CLI. `[ssr]`. [Recipe »](ssr.md) |

## Shipped in v0.127.0

Admin — in-place inline editing:

| Feature | Status | Where |
|---------|--------|-------|
| **`Inline(editable=True, can_delete=True)`** | ✅ v0.127 | The parent's detail view renders the 1-N children as an editable formset (one input row per child + a blank add row) that posts back to `/inlines/<child>` — edit, add and delete without leaving the page. The parent foreign key is implied (forced to the parent, never an input), rows are scoped to the parent, upload/autocomplete columns stay on the child's own form, and validation errors re-render in place. Requires the child's registered admin + its `can_edit`/`can_delete`. [Recipe »](recipes/admin.md) |

## Shipped in v0.126.0

Testing utilities — model factories:

| Feature | Status | Where |
|---------|--------|-------|
| **`ModelFactory` + `seq`** | ✅ v0.126 | Binds a model + defaults to the session: `build` (unsaved), `create`/`create_many` (add+flush+refresh). A **callable** default/override receives the row index → unique fields; `seq("u{n}@x")` is the shortcut. No magic: you declare the defaults. `from tempest_fastapi_sdk.testing import ModelFactory, seq`. [Recipe »](recipes/testing.md) |

## Shipped in v0.125.0

Outbound webhooks — sign + deliver with retry:

| Feature | Status | Where |
|---------|--------|-------|
| **`WebhookSender`** | ✅ v0.125 | POSTs the JSON event signed with the same `WebhookSignatureVerifier`; retries transient failures (5xx/429/connection) with backoff, not 4xx. `send`/`send_many` → `WebhookDelivery`. Injected httpx; pairs with the outbox. [Recipe »](recipes/http.md) |

## Shipped in v0.124.0

Observability — custom business metrics on `/metrics`:

| Feature | Status | Where |
|---------|--------|-------|
| **`BusinessMetrics`** | ✅ v0.124 | Typed `counter`/`gauge`/`histogram` factory on the shared registry (optional namespace, name-dedup); lands on the same `GET /metrics`. Returned objects are the real `prometheus_client` metrics — no magic. [Recipe »](recipes/metrics.md) |

## Shipped in v0.123.0

More `field__op` filter operators (in `Q` and the repository dict):

| Feature | Status | Where |
|---------|--------|-------|
| **`in`/`notin`/`isnull`/`contains`/`startswith`/`endswith` operators** | ✅ v0.123 | Join `gt`/`gte`/`lt`/`lte`/`ne`; `build_filter_condition` (backs `Q` + dict). [Recipe »](recipes/database.md) |

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
| **Inlines (read + navigate)** | ✅ v0.116 | `AdminModel(inlines=[Inline(Child, Child.parent_id)])` lists the 1-N children on the parent's detail view as a table, with a link to the child admin and "Add" pre-filling the FK (via a create query param). Reuses the child admin's `list_display`/CRUD. In-place editing on the same screen: `editable=True` (v0.127). [Recipe »](recipes/admin.md) |

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
| **`Q` (composable conditions)** | ✅ v0.111 | `Q(status="open") \| Q(...)`, `&`, `~` for the `OR`/`NOT` the filter dict can't express; same conventions (`field__gte`, `name` ILIKE, iterable → `IN`). `where=` on `list`/`first`/`get`/`get_or_none`/`count`/`exists`/`paginate`/`delete_many`. [Recipe »](recipes/database.md) |

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
