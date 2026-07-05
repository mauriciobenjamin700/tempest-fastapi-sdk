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

The SDK currently covers (Sep 2025+, post-v0.31.x):

- **Auth** — JWT/bearer/role/permission/X-Token deps (JWT deps
  read the token from header → cookie → query string via
  `query_param=`, for cookieless `EventSource`/SSE clients), full
  bundled flow (`UserAuthService` + `make_auth_router` covering
  signup/activate/login/password-reset), `BaseUserModel` +
  `BaseUserTokenModel` (nullable `payload` column carrying flow
  context), email change/re-verify/recovery
  (`request_email_change`/`confirm_email_change`,
  `request_email_verification`/`confirm_email_verification`,
  `request_email_recovery` — password + MFA-if-enrolled, opt-in
  `AUTH_EMAIL_RECOVERY_ENABLED`; old-email security notice via
  `AUTH_EMAIL_CHANGE_NOTIFY_OLD`; `EMAIL_CHANGE` token purpose;
  JSON + backend HTML pages + bilingual templates), OAuth2/OIDC
  providers (`GoogleOAuthClient`, `GitHubOAuthClient`,
  `OIDCProvider`), CSRF middleware + `make_csrf_token_dependency`,
  opt-in DB-backed opaque refresh tokens
  (`BaseUserRefreshTokenModel`, `make_user_refresh_token_model`,
  `refresh_token_model=` on `UserAuthService`) with rotation,
  family-wide reuse detection and `POST /auth/logout`
  (`LogoutSchema`).
- **DB** — `AsyncDatabaseManager`, `BaseRepository[T]` with
  bulk ops (`bulk_create_values`, `bulk_upsert`, `bulk_update`,
  `add_all`, etc.), `AlembicHelper`, `BaseModel`, audit /
  soft-delete mixins, `reorder_base_columns_first` Alembic
  hook so generated migrations ship `id`/`is_active`/
  `created_at`/`updated_at` first. `alembic.ini` ships with
  `sqlalchemy.url` empty — URL resolves at runtime from env /
  settings / constructor.
- **Standardized exceptions** (`AppException` + subclasses) +
  `register_exception_handlers`.
- **Observability** — structured logging + per-level files +
  `/logs` endpoint, metrics (CPU/RAM/GPU/Disk), Prometheus
  `/metrics` endpoint + `PrometheusMiddleware`, request-id
  middleware with contextvar propagation, typed `HTTPClient`
  (httpx wrapper with retry/backoff/circuit-breaker /
  `X-Request-ID` propagation).
- **HTTP layer** — `RequestIDMiddleware`, `RateLimitMiddleware`,
  `IdempotencyMiddleware` (memory + Redis stores),
  `BodySizeLimitMiddleware`, hardened static files, CORS,
  health + tool-spec routers.
- **Pagination** — offset + cursor.
- **Settings mixins** — every `*Settings` carries
  `title`/`description`/`examples` on every field.
- **SSE** — `EventStream` (bounded queue + `overflow` backpressure —
  `drop_oldest`/`drop_newest`/`block`, `dropped_events` counter,
  `max_queue=0` to disable), `ServerSentEvent`, `sse_response`
  (`on_disconnect=` cleanup), `EventStream.response`, and `SSEBroker`
  (per-channel fan-out; `SSEBroker.response(channel)` bundles
  register + response + unregister-on-disconnect; in-memory
  single-process, or multi-worker via an injected Redis pub/sub bridge
  — same call site).
- **Throttle** — `AttemptThrottle` (any `ThrottleBackend`, e.g.
  `redis.asyncio.Redis`; no in-memory backend bundled).
- **Base CRUD layers** — `BaseService[Repo, Resp, UpdateT]` and
  `BaseController[Service, Resp, UpdateT]` with
  `get_by_id`/`get_or_none`/`list`/`paginate`/`count`/`exists`/`update`/
  `delete`; `update` is partial-aware (PUT/PATCH) and `UpdateT` is an
  optional 3rd generic (defaults to `BaseSchema`, PEP 696).
- **Base enums** — `BaseStrEnum` / `BaseIntEnum` with
  `values`/`keys`/`choices`/`to_dict`/`from_value`/`has_value`/`has_key`.
- **Validated field types** — `tempest_fastapi_sdk.utils` Annotated
  Pydantic types: `PositiveIntField`/`NonNegativeIntField`/`CentsField`/
  `PortField`/`PositiveFloatField`/`NonNegativeFloatField`/`PercentField`/
  `RatioField`/`LatitudeField`/`LongitudeField`/`PriceField`/
  `NonEmptyStrField`/`SlugField`/`HexColorField`.
- **Runtime typing** — `strict_types` / `typed` / `require_annotations`
  decorators (over `pydantic.validate_call`); ruff `ANN` enabled in the
  SDK and `tempest new` templates (ANN401 off — `Any` is valid); a
  `[tool.tempest] typing_strictness` knob (`lenient`/`standard`/`strict`,
  `--strictness` override) layered onto `tempest lint`/`fix`/`type`/`check`.
- **Vision** (`[vision]` extra) — `tempest_fastapi_sdk.vision` wrapping
  `ort-vision-sdk`: lazy `Detector`/`Classifier`/`Segmenter` + prediction
  schemas + `to_detection_schemas`/`to_classification_schema`/
  `to_segmentation_schemas` mappers.
- **GenAI self-hosted** (`[genai]` extra: transformers+torch+accelerate;
  `[genai-quant]` = bitsandbytes) — `tempest_fastapi_sdk.genai`, delivered
  in slices. **Shipped (v0.96):** hardware capacity check — `probe_hardware`
  → `HardwareInfo` (CPU/RAM/CUDA-VRAM/MPS/disk, degrades without
  psutil/torch), `can_run`/`recommend` → `CapacityReport` (fits? device,
  estimate vs available, suggestion to quantize/offload), `estimate_model_bytes`/
  `bytes_per_param`/`fetch_num_params` (Hub metadata, no weight download),
  `ModelDtype`. Capacity fns import WITHOUT the extra. **Shipped (v0.98) —
  `TextGenerator`**: local causal LM (`generate`/`chat`/`stream` async via
  to_thread), auto device/dtype, int8/int4 quant (BitsAndBytesConfig), lazy
  `load` + `unload`/`unload_if_idle` (+ `idle_unload_seconds`),
  `resolve_device`/`auto_dtype_name`; torch/transformers lazy. **Planned:**
  `Embedder`, model/result cache, `BatchScheduler` (coalesce concurrent
  calls). Classes-only (no bundled router). Submodule import like
  queue/tasks/vision. **Shipped (v0.97) — RAG context**
  (`tempest_fastapi_sdk.genai.rag`, `[genai-rag]` extra = httpx +
  trafilatura + pymupdf): `WebSearchBackend` Protocol + `SearxngBackend`
  (SearXNG JSON API, leviathan pattern) + `WebSearch`; `ContentExtractor`
  (trafilatura, failures never raise); `PdfReader` (PyMuPDF detailed
  extraction → `Document`/`Chunk`, `read`/`chunks` with overlap);
  `build_context(question, sources)` → prompt block mixing web +
  PDF. All import lazily.
- **SSR** (`[ssr]` extra) — `tempest_fastapi_sdk.ssr`: typed Python
  pages rendered to HTML via `tempestweb`'s `render_to_html` /
  `render_document`. `Page` (typed `Component` base — `body()` +
  overridable `shell()` layout), `html_response` (widget tree →
  `HTMLResponse`, full document or bare HTMX fragment), and
  `make_htmx_router` (serves a wheel-bundled HTMX 2.x locally, no CDN).
  `tempestweb` imported lazily so `import tempest_fastapi_sdk` never
  needs the extra.
- **Upload** — `UploadUtils` with pluggable backends
  (`LocalUploadStorage`, `MinIOUploadStorage`, opt-in injected via
  `backend=`), download helpers, presigned URLs, plus `FileStoreUtils`
  — a unified facade bundling upload + download + presign over one
  shared backend (`uploader`/`downloader`/`backend`/`client` escape
  hatches).
- **MinIO / S3** — `AsyncMinIOClient` via `[minio]` extra
  (bucket lifecycle, object I/O, streaming download, presigned
  URLs).
- **Email** — SMTP via `EmailUtils` + Jinja2 template rendering
  with bundled defaults (`activation.html`, `password_reset.html`)
  shadowable by the project's `template_dir`.
- **WebPush** — `WebPushDispatcher` (`send`/`send_many`, 404/410
  pruning), subscription storage (`BaseWebPushSubscriptionModel` +
  `make_web_push_subscription_model`) + `WebPushSubscriptionService`
  (`subscribe`/`unsubscribe`/`list_for_user`/`notify_user` with
  auto-prune of gone endpoints) + `make_web_push_router` (opt-in
  `/subscribe` + `/unsubscribe`, aligned with `tempest-react-sdk`);
  webhook signatures.
- **Cache** — Redis manager + `@cached`.
- **Queue / tasks** — typed facades hiding FastStream + TaskIQ:
  `MessageBroker` (`.rabbitmq`/`.redis`/`.kafka`/`.nats`, `@mq.on(channel)`
  consumer, channel-first `publish(channel, message)`, `.broker` escape
  hatch) and `TaskQueue` (`.rabbitmq`/`.redis`/`.memory`, `@tq.task` →
  `Task.enqueue`/`.run`, folded `@tq.cron`/`@tq.interval` +
  `start_scheduler`, `tq.broker`/`tq.scheduler` for the CLIs). **Both
  decorator and class-based styles**: `Consumer` + `@subscribe` +
  `MessageBroker.register` (constructor form takes explicit
  `channel`+`schema`, no magic); `TaskDef` + `@task_method` +
  `TaskQueue.register`. **Cron without syntax**: `Cron`/`CronOffset`
  (`BRASILIA` etc.)/`Weekday` enums + `daily`/`weekdays`/`every_n_minutes`/
  `weekly`/`monthly`/… builders (dependency-free). `AsyncBrokerManager`
  renamed to **`AsyncQueueManager`** (v0.94.0; old alias kept); legacy
  `AsyncTaskBrokerManager`/`AsyncTaskScheduler` kept. Outbox
  (`BaseOutboxModel`/`OutboxRelay`/`save_with_outbox`) plugs its `publish`
  into `MessageBroker.publish`.
- **BR validators** — CPF/CNPJ/CEP/phone, with `*Field` Pydantic types
  (`CPFField`/`CNPJField`/`CPFOrCNPJField`/`PhoneBRField`/`CEPField`;
  pre-0.76 unsuffixed names kept as deprecated aliases). **PIX keys**
  (v0.95.0): `PixKeyField` validates+normalizes any of the 5 BACEN key
  types (CPF/CNPJ/email/E.164 phone/random UUID); `PixKeyType` +
  `detect_pix_key_type`/`is_valid_pix_key`/`normalize_pix_key`.
- **BR localities** — `UF` (StrEnum, 27 siglas) + `Region`
  (5 macro-regiões IBGE), `StateBR`/`CityBR` schemas, offline
  dataset of 27 states + 5606 municipalities (IBGE-derived,
  DF as 36 administrative regions), `list_states`/`get_state`/
  `cities_by_uf`/`states_by_region`, `is_valid_uf`/`normalize_uf`,
  `is_valid_city`/`normalize_city` (accent/case-insensitive),
  `UFField`/`CityNameField`, plus `ChoiceBR` + `uf_choices`/
  `region_choices`/`city_choices` (frontend `<select>` choices).
- **Rate limit** — `RateLimitMiddleware` (sliding window) with
  pluggable store (`MemoryRateLimitStore` / `RedisRateLimitStore`,
  atomic Lua) and per-principal key extractors (`key_by_ip`,
  `key_by_jwt_subject`, `key_by_jwt_claim`, `key_by_header`).
- **i18n error envelopes** — `MessageCatalog` +
  `default_message_catalog` (PT-BR + EN), `parse_accept_language`,
  `AppException.message_key` / `message_params`,
  `register_exception_handlers(..., catalog=..., default_locale=...)`.
- **Cache invalidation** — `@cached(namespace=..., tags=...)` +
  `CacheInvalidator` (`invalidate_namespace` / `invalidate_tag` /
  `invalidate_tags` / `invalidate_keys`).
- **Feature flags** — `tempest_fastapi_sdk.flags`: `FeatureFlags`
  over `Memory` / `Env` / `Redis` / `Composite` backends +
  `make_flag_dependency` route guard.
- **Audit trail** — `BaseAuditLogModel` + `AuditAction`,
  `snapshot_model` / `diff_snapshots`, `BaseRepository` opt-in
  (`audit_model=...` + `add_audited` / `update_audited` /
  `delete_audited`, same-tx).
- **Admin panel** — Jinja + HTMX (`AdminSite`, `AdminModel`,
  `make_admin_router`), typed theming via `AdminTheme` (colors /
  logo / favicon / font / radius / footer / dark mode /
  `custom_css_url`, injected as `:root` overrides), custom bulk actions
  (`@admin_action` + `AdminModel(actions=[...])`, `AdminActionContext` /
  `AdminActionResult`), file/image upload fields (`AdminModel(
  upload_fields=[...], upload_storage=...)`), rich list filters
  (bool/enum/FK select, date-range, text — auto by column type).
- **CLI** — `tempest new` (scaffolds layered service +
  docker-compose + multi-stage uv `Dockerfile`/`.dockerignore`),
  `tempest generate --docker` (regen compose) / `--dockerfile`
  (regen Dockerfile + .dockerignore) / `--src` (extra source layers),
  `tempest db init/revision/upgrade/downgrade/current/history/seed`,
  `tempest user create [--admin] / list`, `tempest secrets rotate`,
  plus quality gates (`lint`, `fix`, `format`, `fmt-check`, `type`,
  `test`, `check`).

The whole Tier S / Tier A / Tier B backlog that used to live here is
**shipped**, and so is the five-item next-version plan that followed it
(rate-limit per principal, i18n error envelopes, `@cached`
tag/namespace invalidation, feature flags, audit trail — all landed in
v0.54.0–v0.58.0). The covers list above is the source of truth; don't
re-plan finished work.

### Next-version plan

**Theme: Admin panel — close the gap vs Django Admin / Laravel Nova /
SQLAdmin.** The current admin is a complete Phase-1 surface (list /
detail / CRUD, ILIKE search, boolean-only filters, sort, offset
pagination, CSV/JSON export, bulk activate/deactivate/delete, TOTP MFA,
audit stamps, 8 fixed widgets, FK `<select>` capped at 1000 rows).
Competitor admins go further; several of those gaps map to **engines
the SDK already ships but the admin does not surface** — so the work is
mostly wiring, not greenfield.

Build in tiers. Ship each item, document it (same-commit docs rule),
then move it up to the covers list.

**Shipped — `AdminTheme` (v0.72.0).** Typed appearance overrides
(colors / logo / favicon / font / radius / footer / dark mode /
`custom_css_url`) injected as `:root` CSS-variable overrides via
`AdminSite(theme=...)`. This is the "beautiful + typed customization"
foundation the user asked for first; the functional Tier 1 items below
inherit the look for free. Now in the covers list.

**Shipped — custom actions (v0.84.0).** `@admin_action` decorator +
`AdminModel(actions=[...])` + `AdminActionContext`/`AdminActionResult`;
custom entries render in the bulk dropdown (namespaced `custom:<name>`),
run on the checked rows, and flash a banner on the list view. Now in the
covers list.

**Shipped — file / image upload field (v0.85.0).** `AdminModel(
upload_fields=[...], upload_storage=...)` renders String columns as file
inputs, streams the upload to `LocalUploadStorage` / `MinIOUploadStorage`,
and stores the returned key. Now in the covers list.

**Shipped — rich filters (v0.86.0).** `list_filter` fields auto-pick a
widget by column type: bool / enum / FK → select, date/datetime →
inclusive date-range (two inputs → `__gte`/`__lte`), other → text.
Now in the covers list.

**Tier 1 — high value, reuses an existing engine (low effort):**

1. **Audit history viewer (NEXT)** — a per-row change timeline wired to the
   existing `BaseAuditLogModel` + `diff_snapshots`. Today the detail
   view only shows `created_by` / `updated_by` stamps, not full history.
   Engine is already shipped.

**Tier 2 — high value, medium effort:**

2. **Autocomplete FK fields** — HTMX search endpoint backing FK inputs,
   removing the 1000-row `<select>` cap and the plain-UUID fallback
   (Django `autocomplete_fields`, Nova search).
3. **Inlines / nested relations** — edit child rows inside the parent's
   detail / edit view (Django `StackedInline` / `TabularInline`).
4. **Dashboard business metrics / charts** — value / trend / partition
   cards (Nova metrics), wired to the existing metrics module. Distinct
   from today's system CPU/RAM/disk panel.

**Tier 3 — nice-to-have:**

5. **RBAC granular** — per-model / per-action admin permissions beyond
   today's `is_admin` + `can_create` / `can_edit` / `can_delete`.
6. **CSV import** — bulk upload counterpart to the existing export.
7. **Lenses** — saved alternate views / queries per model (Nova).

Origin: competitor gap analysis (Django Admin, Laravel Nova, SQLAdmin,
Starlette-Admin) run 2026-06-26. Do **not** treat the tiers as locked —
business pressure can still jump a non-admin item to the front (like the
BR localities dataset in v0.53.0, which was never on any list). Keep
this honest, not aspirational.

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
- **Explicit re-exports in every `__init__.py`.** Every public
  symbol that an `__init__.py` re-exports MUST use **both**:

  1. The PEP 484 `from x import Y as Y` form (explicit re-export),
     and
  2. A `__all__: list[str]` listing the same symbol.

  Reason: third-party consumers run a mixed bag of type-checkers
  (mypy, pyright, pylance, basedpyright) on different strictness
  settings and without project-aware `pyrightconfig.json`. Either
  form ALONE is theoretically PEP 484 compliant, but in practice
  basedpyright + Pylance strict still flag `from foo import Bar`
  inside an `__init__.py` as "private import usage" unless the
  symbol is aliased with `as Bar`. Always pair the two so any
  IDE — with or without a project config — accepts
  `from tempest_fastapi_sdk.<module> import Symbol` without a
  diagnostic. Example:

  ```python
  # tempest_fastapi_sdk/foo/__init__.py
  from tempest_fastapi_sdk.foo.bar import Bar as Bar
  from tempest_fastapi_sdk.foo.baz import Baz as Baz

  __all__: list[str] = ["Bar", "Baz"]
  ```

  Plain `from tempest_fastapi_sdk.foo.bar import Bar` (without
  `as Bar`) inside an `__init__.py` is a structural defect — flag
  it before adding features. When adding a new public symbol,
  update **both** the import alias and `__all__` in the same
  patch.
