# Recipes

Bite-sized "I want to wire X" walkthroughs. Each page starts with **what problem it solves**, **when to reach for it**, and a complete code example you can copy verbatim.

!!! tip "Start here"
    - **Brand-new service?** Follow the **[Tutorial »](../tutorial.md)** — linear, builds the *Users* feature step by step.
    - **Just need a signature?** Jump to the **[Reference »](../reference.md)**.
    - **Wiring a specific piece?** You're in the right place — the [tour](#sdk-tour-one-example-per-block) below is the map, and the [index](#recipe-index) takes you to the full recipe.
    - **Want to see it all working together?** Head to the **[complete examples](#complete-examples)**.
    - **Prefer studying a guided project?** See the **[Learning projects »](../learning/index.md)**.

## SDK tour — one example per block

A walk through **everything** the `tempest-fastapi-sdk` offers: each block has the concept in one line, a minimal runnable example, and a link to the full recipe. Read top to bottom for the mental map, or jump to what you need — install only the extras you use (`uv add "tempest-fastapi-sdk[auth,cache,queue]>=0.171.0"`).

### Foundation

`BaseAppSettings`, `AsyncDatabaseManager`, the `create_app` factory, `run()`.

```python
from tempest_fastapi_sdk import AsyncDatabaseManager, BaseAppSettings


class Settings(BaseAppSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./app.db"


settings = Settings()
db = AsyncDatabaseManager(settings.DATABASE_URL)
```

See the [Tutorial](../tutorial.md) and the [Database](database.md) recipe.

### Schemas and validated fields

`BaseSchema` + self-describing `Annotated` types (money, %, slug, lat/long,
and Brazilian ones: CPF/CNPJ/CEP/phone + **Pix key**).

```python
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import CentsField, PixKeyField, SlugField


class ProductSchema(BaseSchema):
    slug: SlugField
    price_cents: CentsField          # int >= 0
    pix_key: PixKeyField             # CPF/CNPJ/email/phone/random
```

Recipes: [Validated fields](fields.md), [Brazilian helpers](br-helpers.md).

### Repository, Service, Controller

`BaseRepository[Model]` (CRUD + bulk ops), `BaseService`, `BaseController`
with `get_by_id`/`list`/`paginate`/`update`/`delete` ready.

```python
from tempest_fastapi_sdk import BaseRepository, BaseService


class UserRepository(BaseRepository[UserModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, model=UserModel)


class UserService(BaseService[UserRepository, UserResponseSchema]):
    ...
```

Recipes: [Tutorial](../tutorial.md), [Database](database.md).

### Pagination

Offset and cursor, with a `Link` header.

```python
from tempest_fastapi_sdk import BasePaginationFilterSchema, CursorPaginationFilterSchema
```

### Standardized exceptions

`AppException` + subclasses → the right HTTP status;
`register_exception_handlers(app)`.

```python
from tempest_fastapi_sdk import NotFoundException, register_exception_handlers

register_exception_handlers(app)
raise NotFoundException(message="user not found")   # -> standardized 404
```

### Full authentication

Bundled flow: signup/activate/login/reset/**email change and recovery**/MFA
+ JWT deps (header/cookie/query).

```python
from tempest_fastapi_sdk import UserAuthService, make_auth_router

auth = UserAuthService(user_model=UserModel, token_model=UserTokenModel,
                       auth_settings=settings, jwt_settings=settings)
app.include_router(make_auth_router(auth, session_factory=db.session_dependency))
```

Recipes: [Auth flow](auth-flow.md), [MFA](mfa.md),
[Refresh tokens](refresh-tokens.md), [Sessions](sessions.md).

### Cache

`AsyncRedisManager` + `@cached` + `CacheInvalidator` (namespace/tag).

```python
from tempest_fastapi_sdk.cache import AsyncRedisManager, cached

redis = AsyncRedisManager(settings.REDIS_URL)


@cached(redis, ttl=300, namespace="products", tags=lambda a, k: [f"p:{k['pid']}"])
async def get_product(*, pid: str) -> dict: ...
```

Recipe: [Cache](cache.md).

### Queues and background tasks

`MessageBroker` (FastStream pub/sub), `TaskQueue` (TaskIQ) + cron via
enum/helpers, both hiding the underlying lib.

```python
from tempest_fastapi_sdk.queue import MessageBroker
from tempest_fastapi_sdk.tasks import TaskQueue, Cron, CronOffset

mq = MessageBroker.rabbitmq(settings.RABBITMQ_URL)
tq = TaskQueue.rabbitmq(settings.TASKIQ_BROKER_URL)


@mq.on("orders.paid")
async def on_paid(event: OrderPaid) -> None: ...


@tq.cron(Cron.EVERY_WEEKDAY_9AM, cron_offset=CronOffset.BRASILIA)
async def digest() -> None: ...
```

Recipes: [Queues and Tasks](queue-tasks.md), [Outbox](outbox.md).

### Real time

SSE (`EventStream`/`SSEBroker` with backpressure), WebSocket router, Web Push.

```python
from tempest_fastapi_sdk import EventStream

@app.get("/events")
async def events():
    stream = EventStream()
    ...
    return stream.response(on_disconnect=task.cancel)
```

Recipes: [SSE](sse.md), [WebSocket](websocket.md),
[Web Push](webpush.md), [Real time](realtime.md).

### Observability

Structured logging + `/logs`, CPU/RAM/GPU metrics + Prometheus `/metrics`,
request-id, OTel tracing, health + tool-spec.

```python
from tempest_fastapi_sdk import make_health_router, RequestIDMiddleware

app.add_middleware(RequestIDMiddleware)
app.include_router(make_health_router(checks={"db": db.health_check}))
```

Recipes: [Logging](logging.md), [Metrics](metrics.md),
[Observability](observability.md).

### HTTP hardening

Rate limit (sliding window), idempotency, CSRF, CORS, body-size limit,
hardened static files.

```python
from tempest_fastapi_sdk import RateLimitMiddleware, IdempotencyMiddleware

app.add_middleware(RateLimitMiddleware, store=..., max_requests=100, window_seconds=60)
app.add_middleware(IdempotencyMiddleware, store=...)
```

Recipes: [HTTP layer](http.md), [Idempotency](idempotency.md),
[Security](security.md).

### Files

`UploadUtils` (local/MinIO), `DownloadUtils`, `FileStoreUtils` (facade),
MinIO/S3 storage, presigned URLs.

```python
from tempest_fastapi_sdk import FileStoreUtils

store = FileStoreUtils(source="./uploads")     # or an AsyncMinIOClient
key = await store.save(upload_file)
```

Recipes: [File store](file-store.md), [Uploads](uploads.md),
[Downloads](downloads.md), [Storage](storage.md).

### Domain extras

Feature flags, audit trail, multi-tenant, offline-first sync, server-side
sessions, typed HTTP client, i18n error envelopes.

```python
from tempest_fastapi_sdk import FeatureFlags, make_flag_dependency
```

Recipes: [Feature flags](feature-flags.md), [Audit trail](audit-trail.md),
[Multi-tenant](multi-tenant.md), [Offline sync](offline-sync.md),
[HTTP client](http-client.md).

### Self-hosted generative AI

Hardware check, local LLM, embeddings, RAG (web + PDF) — all on your own
hardware.

!!! info "Installation"
    The core ships with `tempest-fastapi-sdk`. Self-hosted generative AI needs the `[genai]` extra — `uv add "tempest-fastapi-sdk[genai]"` (pulls in `torch`, `transformers`, `accelerate`, `safetensors` and `huggingface-hub`).

```python
from tempest_fastapi_sdk.genai import can_run, TextGenerator
from tempest_fastapi_sdk.genai.rag import PdfReader, build_context

if can_run(model_id="Qwen/Qwen2.5-7B-Instruct").fits:
    gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct", quantization="int4")
    chunks = PdfReader().chunks("/kb/manual.pdf")
    answer = await gen.generate(build_context("how to refund?", chunks))
```

Recipe: [Self-hosted generative AI](genai.md).

### Admin panel

`AdminSite` + `AdminModel` + `make_admin_router` (Jinja+HTMX, themes,
actions, upload, filters).

Recipe: [Admin panel](admin.md).

### SSR and vision

Typed SSR (`Page`/`html_response`) over `tempestweb`; computer vision
(`Detector`/`Classifier`/`Segmenter`) via `ort-vision-sdk`.

Recipes: [SSR](../ssr.md), [Vision](vision.md).

### CLI and deploy

`tempest new` (scaffold), `tempest db` (migrations), `tempest user`,
`tempest secrets`, quality gates; safe deploy (migrations + graceful
shutdown).

```bash
tempest new my-service && cd my-service
tempest db init && tempest db upgrade
tempest check          # ruff + mypy + tests
```

Recipes: [CLI](cli.md), [Safe deploy](deploy-safety.md).

### Recap

The SDK covers the whole lifecycle of a FastAPI service: typed foundation
→ persistence → auth → cache → background → real time → observability →
hardening → files → AI → admin → CLI/deploy. Each section above points at
the recipe with the full guide. Start from the [Tutorial](../tutorial.md) and
come back here to plug in each capability as you need it.

## Recipe index

| Theme | Covers |
| --- | --- |
| **[Admin site »](admin.md)** | `AdminSite`, `AdminModel`, `make_admin_router`, `BaseUserModel` |
| **[Audit trail »](audit-trail.md)** | `BaseAuditLogModel`, `add_audited` / `update_audited` / `delete_audited`, `snapshot_model` / `diff_snapshots` |
| **[Auth flow (signup/reset) »](auth-flow.md)** | `UserAuthService`, `make_auth_router` — signup / activation / login / password reset, token delivery (bearer/cookie/both), `BaseUserModel` |
| **[Brazilian helpers »](br-helpers.md)** | CPF / CNPJ / CEP / phone validation + normalization |
| **[Cache »](cache.md)** | `AsyncRedisManager`, `@cached` decorator, `CacheInvalidator` (tag/namespace) |
| **[Chat (conversations + messages) »](chat.md)** | `ChatService`, `make_chat_router`, base tables + real-time fan-out via `SSEBroker` |
| **[CLI »](cli.md)** | `tempest new` / `db` (+ `seed`) / `user` / `secrets rotate` / `lint` / `fix` / `format` / `type` / `test` / `check` |
| **[Comments + ratings »](reviews.md)** | `ReviewService`, `make_reviews_router`, 0–5 star scores with aggregation, threaded comments |
| **[Computer vision (ONNX) »](vision.md)** | `Detector` / `Classifier` / `Segmenter` + prediction schemas |
| **[Database »](database.md)** | `BaseModel`, `AsyncDatabaseManager`, `BaseRepository` (CRUD + filters + bulk), offset/cursor pagination, mixins, `AlembicHelper`, `SlowQueryLogger` |
| **[Downloads »](downloads.md)** | `DownloadUtils` — `file_response`, `stream`, `build_content_disposition`, path-traversal safe |
| **[Errors in OpenAPI (Swagger) »](openapi-errors.md)** | `error_responses`, `@raises`, `TempestAPIRouter`, `ErrorResponseSchema`, `tempest openapi-errors --fix` |
| **[Feature flags »](feature-flags.md)** | `FeatureFlags`, env/Redis/composite backends, `make_flag_dependency` |
| **[File store (unified) »](file-store.md)** | `FileStoreUtils` — upload + download + presign over a single backend |
| **[Geolocation (distance + travel time) »](geo.md)** | `haversine_km`, `estimate_travel`, `OSRMBackend`, `NominatimBackend`, `GeoPointMixin` / `GeoRepositoryMixin` |
| **[HTTP client (outbound) »](http-client.md)** | `HTTPClient` — typed httpx with retry/backoff, circuit-breaker, X-Request-ID; `RetryPolicy`, `CircuitOpenError` |
| **[HTTP layer »](http.md)** | `apply_cors`, `RequestIDMiddleware`, `RateLimitMiddleware`, `make_health_router`, JWT / role / permission dependencies, webhook signature verifier, pagination Link headers, tool-spec router |
| **[Idempotency »](idempotency.md)** | `IdempotencyMiddleware`, `MemoryIdempotencyStore` / `IdempotencyStore` (Redis) — safe replay of POST/PUT/PATCH/DELETE |
| **[Integration client (OpenAPI) »](openapi-client.md)** | `tempest openapi-client` — Pydantic schemas + a typed client from a third party's spec |
| **[Introspection auth (resource server) »](introspection-auth.md)** | `IntrospectionAuth` — validate an opaque bearer by asking the upstream identity provider |
| **[Logging »](logging.md)** | `LogUtils`, structured JSON logging, request-ID propagation |
| **[Management commands (tempest &lt;cmd&gt;) »](management-commands.md)** | register your own commands on the project's `tempest` CLI |
| **[Metrics »](metrics.md)** | `MetricsUtils` — CPU / RAM / disk / GPU snapshots |
| **[MFA (TOTP / 2FA) »](mfa.md)** | `MFAMixin`, `TOTPHelper`, enroll/confirm/verify/disable endpoints on `make_auth_router`, recovery codes |
| **[Multi-tenant »](multi-tenant.md)** | `TenantScopedRepository` — `tenant_id` isolation on every query |
| **[Object-level permissions »](authz.md)** | `permission` (rule decorator), `has_perm` / `check_permission`, `PermissionRegistry`, `make_permission_checker`, `PermissionMixin` |
| **[Observability (tracing) »](observability.md)** | `setup_tracing` (OpenTelemetry), `SlowQueryLogger` |
| **[Offline-first sync (delta) »](offline-sync.md)** | `BaseRepository.changes_since`, `SyncFilterSchema`, `SyncPaginationSchema`, cursor deltas + soft-delete |
| **[Permission guards (@requires) »](permission-guards.md)** | `@requires` plus `(user) -> user` guards (with an optional `meta: dict[str, Any]` via `meta=` / `include_args=`), `TempestPermissionError`, `GuardContractWarning`, `tempest permissions --check` |
| **[Queue & Tasks »](queue-tasks.md)** | FastStream (`AsyncBrokerManager`), TaskIQ (`AsyncTaskBrokerManager`), `AsyncTaskScheduler`, transactional outbox |
| **[React SPA on FastAPI »](react-spa.md)** | `make_spa_router` — serve the Vite build from the same process, with history fallback |
| **[Real-time »](realtime.md)** | Overview — when to choose SSE, WebSocket or Web Push |
| **[Refresh tokens (rotation/revocation) »](refresh-tokens.md)** | `BaseUserRefreshTokenModel`, `make_user_refresh_token_model`, `issue_token_pair`, rotation + family reuse detection |
| **[Safe deploys »](deploy-safety.md)** | `AlembicHelper.safe_upgrade` (blocks DROPs), `GracefulShutdownMiddleware` |
| **[Security »](security.md)** | `AttemptThrottle`, opaque-token helpers, `HardenedStaticFiles`, security headers |
| **[Self-hosted generative AI »](genai.md)** | `probe_hardware` / `can_run`, `TextGenerator`, `Embedder`, RAG (web + PDF), audio (STT/TTS), `make_genai_router` |
| **[Server-Sent Events (SSE) »](sse.md)** | `EventStream`, `sse_response`, `ServerSentEvent`, `SSEBroker` (per-channel fan-out, Redis bridge) |
| **[Server-side sessions »](sessions.md)** | `SessionMiddleware`, `SessionAuth`, `make_session_router`, `MemorySessionStore` / `RedisSessionStore` |
| **[Social login (OAuth2/OIDC) »](oauth.md)** | `GoogleOAuthClient`, `GitHubOAuthClient`, `OIDCProvider`, `OAuthUser`, `generate_oauth_state` |
| **[SSR (typed pages) »](../ssr.md)** | `Page`, `html_response`, `make_htmx_router`, hosting a `tempestweb` build |
| **[Storage (MinIO/S3) »](storage.md)** | `AsyncMinIOClient`, `MinIOUploadStorage`, `presigned_get_url` / `presigned_put_url`, `list_objects` |
| **[Stored file (service mixin) »](stored-files.md)** | `StoredFileServiceMixin` — `set_file` / `replace` / `clear_file` over `UploadUtils` |
| **[System checks (check-config) »](system-checks.md)** | `run_system_checks`, `@check`, `CheckMessage`, `tempest check-config` — validate settings before serving |
| **[tempestweb frontend + SDK »](tempestweb-frontend.md)** | tempestweb frontend calling the SDK backend: `tempestweb.native.http`, `Idempotency-Key` + `IdempotencyMiddleware`, retry, same origin vs CORS |
| **[Testing »](testing.md)** | `test_session`, `test_database`, in-memory SQLite, pytest fixtures |
| **[Transactional email »](email.md)** | `EmailUtils` — SMTP, text/HTML body, attachments, Jinja2 templates |
| **[Transactional outbox »](outbox.md)** | `BaseOutboxModel`, `OutboxRelay`, `save_with_outbox` — reliable events |
| **[Typing (static + runtime) »](typing.md)** | `strict_types` / `typed` / `require_annotations`, `[tool.tempest] typing_strictness` knob, ruff `ANN` |
| **[Uploads (backends) »](uploads.md)** | `UploadUtils`, extension/MIME validation (`sniff_mime`), local / MinIO backends |
| **[Utilities »](utilities.md)** | `utcnow`/`to_utc`, `modify_dict`, `get_client_ip`, opaque tokens (`generate_opaque_token`) |
| **[Validated fields (ready-made types) »](fields.md)** | Annotated Pydantic types — `PositiveIntField` / `CentsField` / `PriceField` / `SlugField` / `HexColorField` / `CPFField` / `UFField` |
| **[Versioned artifacts (models) »](artifact-registry.md)** | `ArtifactRegistry`, `ArtifactVersionMixin`, `build_manifest_entries`, `file_digest` — swap the active version without a redeploy |
| **[Web Push »](webpush.md)** | `WebPushDispatcher`, VAPID schemas, broadcast with pruning |
| **[WebSocket router »](websocket.md)** | `WebSocketHub`, `make_websocket_router`, `broadcast` / `send_to`, heartbeat, bearer auth |

## Complete examples

The recipes show one piece at a time. These pages combine **several** into a flow that runs end to end — read them when you want the integration decisions, not the isolated API.

| Example | What it combines |
| --- | --- |
| **[Full store admin »](../admin-showcase.md)** | audit history + FK autocomplete + inlines + business cards + CSV import + granular RBAC + lenses |
| **[Fullstack web (SSR, WASM, server) »](../fullstack-web.md)** | the three ways to talk to `tempestweb`: SSR + HTMX, WASM SPA and server-mode |
| **[GenAI flows »](../genai-examples.md)** | hardware capacity → local LLM → embeddings/RAG → audio, self-hosted end to end |
| **[Neighborhood marketplace »](../marketplace-local.md)** | geo (nearby sellers, distance/time) + real-time chat + live notifications + star ratings |
| **[Pix checkout »](../integrated.md)** | JWT auth + validated fields (`PixKeyField`) + cache + transactional outbox + `MessageBroker` + `TaskQueue` + SSE + Web Push |

## Anatomy of a recipe

Every recipe follows the same four-section shape so you can skim:

1. **What it solves** — one paragraph in plain language.
2. **When to use it** — bullet list of situations + when *not* to.
3. **The code** — complete, runnable, with `# 1. setup` / `# 2. wire` / `# 3. test` annotations.
4. **Gotchas** — production caveats, security defaults, scaling notes.

If you spot a recipe that doesn't follow this shape, [open an issue](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/new) — we treat docs regressions like code regressions.
