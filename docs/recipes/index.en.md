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
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository, BaseService

from src.db.models import UserModel
from src.schemas import UserResponseSchema


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
from fastapi import FastAPI

from tempest_fastapi_sdk import NotFoundException, register_exception_handlers

app = FastAPI()


register_exception_handlers(app)
raise NotFoundException(message="user not found")   # -> standardized 404
```

### Full authentication

Bundled flow: signup/activate/login/reset/**email change and recovery**/MFA
+ JWT deps (header/cookie/query).

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import UserAuthService, make_auth_router

from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel

app = FastAPI()


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

from src.core.settings import settings


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
from tempest_fastapi_sdk.tasks import Cron, CronOffset, TaskQueue

from src.core.settings import settings
from src.queue import OrderPaid


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
import asyncio

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from tempest_fastapi_sdk import EventStream

app = FastAPI()


@app.get("/events")
async def events() -> StreamingResponse:
    """Stream one tick per second until the client disconnects.

    Returns:
        StreamingResponse: The SSE response. ``on_disconnect`` cancels the
        publisher, so it never outlives the connection.
    """
    stream = EventStream()

    async def pump() -> None:
        """Publish a tick every second."""
        while True:
            await stream.publish({"tick": True}, event="tick")
            await asyncio.sleep(1)

    task = asyncio.create_task(pump())
    return stream.response(on_disconnect=task.cancel)
```

Recipes: [SSE](sse.md), [WebSocket](websocket.md),
[Web Push](webpush.md), [Real time](realtime.md).

### Observability

Structured logging + `/logs`, CPU/RAM/GPU metrics + Prometheus `/metrics`,
request-id, OTel tracing, health + tool-spec.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import RequestIDMiddleware, make_health_router

from src.api.dependencies.resources import db

app = FastAPI()


app.add_middleware(RequestIDMiddleware)
app.include_router(make_health_router(checks={"db": db.health_check}))
```

Recipes: [Logging](logging.md), [Metrics](metrics.md),
[Observability](observability.md).

### HTTP hardening

Rate limit (sliding window), idempotency, CSRF, CORS, body-size limit,
hardened static files.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import IdempotencyMiddleware, RateLimitMiddleware

app = FastAPI()


app.add_middleware(RateLimitMiddleware, store=..., max_requests=100, window_seconds=60)
app.add_middleware(IdempotencyMiddleware, store=...)
```

Recipes: [HTTP layer](http.md), [Idempotency](idempotency.md),
[Security](security.md).

### Files

`UploadUtils` (local/MinIO), `DownloadUtils`, `FileStoreUtils` (facade),
MinIO/S3 storage, presigned URLs.

```python
import asyncio

from fastapi import UploadFile

from tempest_fastapi_sdk import FileStoreUtils

upload_file: UploadFile = ...  # comes from the endpoint signature


store = FileStoreUtils(source="./uploads")     # or an AsyncMinIOClient


async def main() -> None:
    """Run this example."""
    key = await store.save(upload_file)


asyncio.run(main())
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
import asyncio

from tempest_fastapi_sdk.genai import can_run, TextGenerator
from tempest_fastapi_sdk.genai.rag import PdfReader, build_context


async def main() -> None:
    """Run this example."""
    if can_run(model_id="Qwen/Qwen2.5-7B-Instruct").fits:
        gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct", quantization="int4")
        chunks = PdfReader().chunks("/kb/manual.pdf")
        answer = await gen.generate(build_context("how to refund?", chunks))


asyncio.run(main())
```

Recipe: [Self-hosted generative AI](genai.md).

### Long work, with status and cancellable

`JobStore` gives the work a row the screen reads; `run_cancellable` actually
interrupts it when the user gives up. `StageMap` covers the case where the
stages decorate a record the screen already fetches.

```python
import asyncio
from uuid import UUID

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.tasks import (
    BaseJobModel,
    JobStore,
    StageInterruptedError,
    run_cancellable,
)


class JobModel(BaseJobModel):
    """A unit of long-running work."""

    __tablename__ = "jobs"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
store: JobStore[JobModel] = JobStore(db, model=JobModel)


async def transcribe(path: str) -> str:
    """Long, cancellable work (async I/O).

    Args:
        path (str): The file to process.

    Returns:
        str: The text.
    """
    await asyncio.sleep(0)
    return path


async def run(job_id: UUID) -> None:
    """Run the job, giving up if it is cancelled midway.

    Args:
        job_id (UUID): The job to run.
    """
    if await store.claim(job_id) is None:
        return
    try:
        text: str = await run_cancellable(
            transcribe("audio.wav"),
            interrupted=store.cancellation_watch(job_id),
        )
    except StageInterruptedError:
        return
    await store.succeed(job_id)
    print(text)
```

Recipe: [Jobs (long work with status)](jobs.md).

### Hosted AI, and what it cost

`OpenAICompatGenerator` speaks any `/chat/completions` (DeepSeek, Groq,
OpenRouter, vLLM, Azure); `AIUsageStore` keeps one row per paid call, for
the "which account spent what" question.

```python
import asyncio
from uuid import uuid4

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.genai import (
    AIUsageStore,
    BaseAIUsageModel,
    OpenAICompatGenerator,
    TokenUsage,
)


class AIUsageModel(BaseAIUsageModel):
    """One billed AI call."""

    __tablename__ = "ai_usage"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
gen = OpenAICompatGenerator(
    "deepseek-chat",
    api_key="sk-...",
    base_url="https://api.deepseek.com",
)
store: AIUsageStore[AIUsageModel] = AIUsageStore(
    db, model=AIUsageModel, price_input_per_1k=0.00014
)


async def main() -> None:
    """Run this example."""
    text: str
    usage: TokenUsage | None
    text, usage = await gen.generate_with_usage("Summarize this.")
    await store.record(subject_id=uuid4(), service="summary", usage=usage)
    print(text)


asyncio.run(main())
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
| **[Admin SQL console »](admin-sql-console.md)** | `SqlShellService` + `SqlShellPolicy` (capabilities, allowed/denied tables, row cap, `require_where`), real parsing via `sqlglot`, every attempt audited, opt-in admin page |
| **[AI agents »](agents.md)** | `Agent` (goal → trace + artifacts), `AgentBudget` (steps/time/calls), `AgentTool` + ready-made tools over image/vision/audio/RAG, `InMemoryAgentRunSink` / `DbAgentRunSink`, `make_agent_router` |
| **[AI agents (advanced) »](agents-advanced.md)** | typed structured output (`run_structured`), three memory layers (`scratchpad_tools` / `fact_tools` / `recall_prompt`), on-demand `Skill`, `agent_tool` delegation, `run_until` / `refine` |
| **[AI agents (architecture) »](agents-architecture.md)** | how to lay out a service with agents: the `ai` layer beside `services`, `runtime` with one generator per process, `tools` apart from `agents`, `views` and `policy`, the controller that seeds identity, and when to trade one agent for skills |
| **[AI agents (concepts) »](agents-concepts.md)** | the loop step by step, the transcript the model receives on each turn, the vocabulary (step, observation, artifact, budget, `stop_reason`), why the context grows and what that costs, and when to reach for a tool, a skill, delegation or a loop |
| **[AI agents (database) »](agents-db.md)** | a tool that queries the database: `db.get_session_context()` per call, the `from_session` convention, one `AsyncDatabaseManager` per process, and what `AgentContext` carries (`state` with who is asking, `artifacts`, `deadline`) |
| **[AI agents (testing) »](agents-testing.md)** | `ScriptedBackend` / `replies` / `replies_with_tool` to script the model's decisions, `assert_completed` / `assert_used_tools` / `assert_artifact`, `FailingBackend`, and the separate `@model` layer |
| **[App error reports »](app-errors.md)** | `make_app_error_model` (nullable user FK, `SET NULL`, indexed `created_at`), `AppErrorService` (truncate-never-refuse), opt-in admin listing, half-open date range |
| **[Audit trail »](audit-trail.md)** | `BaseAuditLogModel`, `add_audited` / `update_audited` / `delete_audited`, `snapshot_model` / `diff_snapshots` |
| **[Auth flow (signup/reset) »](auth-flow.md)** | `UserAuthService`, `make_auth_router` — signup / activation / login / password reset, token delivery (bearer/cookie/both), `BaseUserModel` |
| **[Brazilian helpers »](br-helpers.md)** | CPF / CNPJ / CEP / phone validation + normalization, including mobile-only (`is_valid_mobile_phone_br`, `MobilePhoneBRField`) and `parse_phone_br` (area code, number, E.164) |
| **[Cache »](cache.md)** | `AsyncRedisManager` (+ `client_proxy`, for stores built at import time), `@cached` decorator, `CacheInvalidator` (tag/namespace) |
| **[Chat (conversations + messages) »](chat.md)** | `ChatService`, `make_chat_router`, base tables + real-time fan-out via `SSEBroker` |
| **[Choosing a model »](models.md)** | `TextModel` / `EmbeddingModel` / `RerankerModel` / `VisionModel` / `ImageModel` / `SpeechToTextModel` / `TextToSpeechModel` — named Hub ids and the use-case table behind each pick |
| **[CLI »](cli.md)** | `tempest new` / `db` (+ `seed`) / `user` / `secrets rotate` / `lint` / `fix` / `format` / `type` / `test` / `check` |
| **[Comments + ratings »](reviews.md)** | `ReviewService`, `make_reviews_router`, 0–5 star scores with aggregation, threaded comments |
| **[Computer vision (ONNX) »](vision.md)** | `Detector` / `Classifier` / `Segmenter` + prediction schemas |
| **[Database »](database.md)** | `BaseModel`, `AsyncDatabaseManager`, `BaseRepository` (CRUD + filters + bulk), offset/cursor pagination, mixins, `AlembicHelper`, `SlowQueryLogger` |
| **[Downloads »](downloads.md)** | `DownloadUtils` — `file_response`, `stream`, `build_content_disposition`, path-traversal safe |
| **[Enum columns (safe on both databases) »](enum-columns.md)** | `Mapped[MyEnum]` storing the `value`, native `ENUM` on PostgreSQL and a `CHECK` on SQLite, `enum_column()`, `op.replace_enum` + `sync_enum_types` for the migration autogenerate cannot see |
| **[Errors in OpenAPI (Swagger) »](openapi-errors.md)** | `error_responses`, `@raises`, `TempestAPIRouter`, `ErrorResponseSchema`, `tempest openapi-errors --fix` |
| **[Face recognition »](faces.md)** | `FaceRecognizer` (detect / embed / compare), `compare_faces`, 16 MB or 191 MB packs, no opencv and no torch |
| **[Fakes (no real provider) »](fakes.md)** | `FakePixProvider`, `FakeTextBackend`, `FakeModerationBackend`, `FakePushDispatcher`, `FakeEmailUtils`, `FakeGeocodingBackend`, `FakeRoutingBackend`, `FakeWebSearchBackend` — eight seams with no credential and no network, steerable (`advance`, `flag`, `fail_next`) and inspectable |
| **[Feature flags »](feature-flags.md)** | `FeatureFlags`, env/Redis/composite backends, `make_flag_dependency` |
| **[File store (unified) »](file-store.md)** | `FileStoreUtils` — upload + download + presign over a single backend |
| **[Firebase auth (ID token) »](firebase-auth.md)** | `FirebaseAuth`, `FirebaseIdentity`, `FirebaseUserResolver` — verify the ID token a mobile app sends, idempotent initialization, one `code` per failure, `[firebase]` extra |
| **[Forms from Pydantic schemas »](ui-forms.md)** | `form_for` / `form_spec_for` / `render_form`, `parse_form` + `FormResult` (per-field errors, input preserved), type-to-control mapping, `json_schema_extra={"ui": ...}`, `form_stylesheet` |
| **[Geolocation (distance + travel time) »](geo.md)** | `haversine_km`, `estimate_travel`, `OSRMBackend`, `NominatimBackend`, `GeoPointMixin` / `GeoRepositoryMixin` |
| **[HTTP client (outbound) »](http-client.md)** | `HTTPClient` — typed httpx with retry/backoff, circuit-breaker, X-Request-ID; `RetryPolicy`, `CircuitOpenError` |
| **[HTTP layer »](http.md)** | `apply_cors`, `RequestIDMiddleware`, `RateLimitMiddleware` (429 in the SDK error envelope), `make_health_router`, JWT / role / permission dependencies, webhook signature verifier, pagination Link headers, tool-spec router |
| **[Idempotency »](idempotency.md)** | `IdempotencyMiddleware`, `MemoryIdempotencyStore` / `IdempotencyStore` (Redis) — safe replay of POST/PUT/PATCH/DELETE |
| **[Image generation (local) »](image-generation.md)** | `ImageGenerator` (local diffusers — `generate` / `edit` img2img), `ImageGenerationConfig`, `GeneratedImage` carrying the reproducing seed, `make_genai_router(image_generator=...)` → `POST /image` |
| **[Integration client (OpenAPI) »](openapi-client.md)** | `tempest openapi-client` — Pydantic schemas + a typed client from a third party's spec |
| **[Introspection auth (resource server) »](introspection-auth.md)** | `IntrospectionAuth` — validate an opaque bearer by asking the upstream identity provider |
| **[Jobs (long work with status) »](jobs.md)** | `BaseJobModel` + `JobStore` — one row per unit of work, `claim`/`succeed`/`fail`, `watch` for the screen, `reclaim_stale`; cooperative cancellation (`cancel` + `run_cancellable`); `StageMap` for several stages on the record itself |
| **[Logging »](logging.md)** | `LogUtils`, structured JSON logging, request-ID propagation |
| **[Management commands (tempest &lt;cmd&gt;) »](management-commands.md)** | register your own commands on the project's `tempest` CLI |
| **[Mercado Pago (Pix, cards, boleto) »](mercado-pago.md)** | `MercadoPagoClient` (143 operations generated from the provider's own OpenAPI), `to_cents` / `from_cents` (reais, not cents), `verify_signature`, `MercadoPagoSettings`, `x_idempotency_key` per call |
| **[Metrics »](metrics.md)** | `MetricsUtils` — CPU / RAM / disk / GPU snapshots |
| **[MFA (TOTP / 2FA) »](mfa.md)** | `MFAMixin`, `TOTPHelper`, enroll/confirm/verify/disable endpoints on `make_auth_router`, recovery codes |
| **[Model weights (Hub lifecycle) »](model-weights.md)** | `ModelRef` (`revision` / `local_files_only` / `trust_remote_code`), `resolve_revision`, `download_model` with a disk preflight, `list_cached_models` / `remove_cached_model`, `tempest model pull` / `cache-list` / `cache-rm` |
| **[Modelops (export, bench, quantization) »](modelops.md)** | `benchmark_onnx` (latency/RAM/GPU/energy), `export_onnx_to_ort`, `quantize_onnx_dynamic`, `quantize_hf_onnx`, `rank` + Pareto frontier, `tempest model` |
| **[Multi-tenant »](multi-tenant.md)** | `TenantScopedRepository` — `tenant_id` isolation on every query |
| **[Object-level permissions »](authz.md)** | `permission` (rule decorator), `has_perm` / `check_permission`, `PermissionRegistry`, `make_permission_checker`, `PermissionMixin` |
| **[Observability (tracing) »](observability.md)** | `setup_tracing` (OpenTelemetry), `SlowQueryLogger` |
| **[Offline-first sync (delta) »](offline-sync.md)** | `BaseRepository.changes_since`, `SyncFilterSchema`, `SyncPaginationSchema`, cursor deltas + soft-delete |
| **[OpenPix (Pix via Woovi) »](openpix.md)** | Layered architecture, opening a charge, verified webhook + API read-back, reconciliation, refunds, `OpenPixEnvironment`, `to_cents` |
| **[OpenPix (subscriptions and plans) »](openpix-subscriptions.md)** | `SubscriptionPayload`, `RECURRENT` vs `PIX_RECURRING` (Pix Automático), lifecycle and instalments, the plan that lives in your database |
| **[PDF generation »](pdf.md)** | `PdfRenderer`, five bundled documents (receipt/quote/report/contract/voucher) with Pydantic schemas, `make_pdf_router`, `tempest pdf render`, asset policy |
| **[Permission guards (@requires) »](permission-guards.md)** | `@requires` plus `(user) -> user` guards (with an optional `meta: dict[str, Any]` via `meta=` / `include_args=`), `TempestPermissionError`, `GuardContractWarning`, `tempest permissions --check` |
| **[Pix protocol (one contract, many providers) »](pix-protocol.md)** | `PixProvider` (Protocol: `create` / `get` / `cancel` / `parse_webhook`), `PixCharge` / `PixChargeRequest` / `PixPayer` field by field, canonical `PaymentStatus` beside the raw `provider_status`, the six `PixEventType`s, `OpenPixPixProvider` — plus how to write your own adapter, with an in-memory fake for testing without a network |
| **[Push (web + mobile) »](push.md)** | `DeviceService`, `PushDispatcher`, `WebPushTransport` / `FCMTransport`, `BaseDeviceTokenModel`, `make_push_router` — one call for browsers and phones, with unified pruning of dead devices |
| **[Query plans (EXPLAIN) »](query-plans.md)** | `explain_queries()` captures the block and explains on exit, `EXPLAIN ANALYZE` on PostgreSQL / `EXPLAIN QUERY PLAN` on SQLite, writes never re-executed, `report.slowest` |
| **[Queue & Tasks »](queue-tasks.md)** | FastStream (`AsyncBrokerManager`), TaskIQ (`AsyncTaskBrokerManager`), `AsyncTaskScheduler`, transactional outbox |
| **[React SPA on FastAPI »](react-spa.md)** | `make_spa_router` — serve the Vite build from the same process, with history fallback |
| **[Real-time »](realtime.md)** | Overview — when to choose SSE, WebSocket or Web Push |
| **[Refresh tokens (rotation/revocation) »](refresh-tokens.md)** | `BaseUserRefreshTokenModel`, `make_user_refresh_token_model`, `issue_token_pair`, rotation + family reuse detection |
| **[Safe deploys »](deploy-safety.md)** | `AlembicHelper.safe_upgrade` (blocks DROPs), `GracefulShutdownMiddleware` |
| **[Security »](security.md)** | `AttemptThrottle`, opaque-token helpers, `HardenedStaticFiles`, security headers |
| **[Self-hosted generative AI »](genai.md)** | `probe_hardware` / `can_run`, `TextGenerator`, `Embedder`, RAG (web + PDF), audio (STT/TTS + batching), `make_genai_router`; hosted backend (`OpenAICompatGenerator`, any `/chat/completions`) with `TokenUsage`, cached prefix included; list output (`parse_structured_list`, retry at a rising temperature) and object output (`extract_json_object`); per-user usage accounting (`AIUsageStore`) |
| **[Server-Sent Events (SSE) »](sse.md)** | `EventStream`, `sse_response`, `ServerSentEvent`, `SSEBroker` (per-channel fan-out, Redis bridge) |
| **[Server-side sessions »](sessions.md)** | `SessionMiddleware`, `SessionAuth`, `make_session_router`, `MemorySessionStore` / `RedisSessionStore` |
| **[Social login (OAuth2/OIDC) »](oauth.md)** | `AUTH_OAUTH_ENABLED` + `oauth_clients=` (four `/auth/oauth/*` routes), `OAuthSettings`, `make_user_oauth_account_model`, `NameMixin`, `GoogleOAuthClient`, `GitHubOAuthClient`, `OIDCProvider`, `OAuthUser`, `OAuthClient`, `generate_oauth_state` |
| **[Spreadsheets (.xlsx) »](spreadsheets.md)** | `SheetWriter` (row cursor), `Column` (width/mask/alignment), `SheetStyle` as plain data, `BR_*` formats pinned to pt-BR, `new_workbook` / `workbook_to_bytes` |
| **[SSR (typed pages) »](../ssr.md)** | `Page`, `html_response`, `make_htmx_router`, hosting a `tempestweb` build |
| **[Storage (MinIO/S3) »](storage.md)** | `AsyncMinIOClient`, `MinIOUploadStorage`, `presigned_get_url` / `presigned_put_url`, `list_objects` |
| **[Stored file (service mixin) »](stored-files.md)** | `StoredFileServiceMixin` — `set_file` / `replace` / `clear_file` over `UploadUtils` |
| **[Stripe (cards + subscriptions) »](stripe.md)** | `StripeClient`, `stripe_http_client`, `to_minor_units` / `from_minor_units`, `make_stripe_webhook_dependency`, `StripeEvent` — form-encoded writes, idempotency by default, zero-decimal currencies |
| **[System checks (check-config) »](system-checks.md)** | `run_system_checks`, `@check`, `CheckMessage`, `tempest check-config` — validate settings before serving |
| **[tempestweb frontend + SDK »](tempestweb-frontend.md)** | tempestweb frontend calling the SDK backend: `tempestweb.native.http`, `Idempotency-Key` + `IdempotencyMiddleware`, retry, same origin vs CORS |
| **[Testing »](testing.md)** | `test_session`, `test_database`, in-memory SQLite, pytest fixtures |
| **[Text search (LIKE + full-text) »](text-search.md)** | portable `search()` (escaped ILIKE, `AND` across words), `full_text_search()` with `websearch_to_tsquery` + `ts_rank` on PostgreSQL, `TextSearchLanguage` / `TextSearchWeight` / `TokenMatch`, conditions that feed `where=` |
| **[Transactional email »](email.md)** | `EmailUtils` — SMTP, text/HTML body, attachments, Jinja2 templates |
| **[Transactional outbox »](outbox.md)** | `BaseOutboxModel`, `OutboxRelay`, `save_with_outbox` — reliable events |
| **[Transactions (commit and savepoint) »](transactions.md)** | session-shared `transaction()`, `commit()` / `flush()` / `rollback()` on the repository, `autocommit=False`, `savepoint()` for the recoverable step |
| **[Transcription pipeline (audio → summary) »](transcription-pipeline.md)** | the three stages stitched together: `StageMap` on the record itself, cancelling a running transcription from inside `on_progress`, `generate_with_usage` + `AIUsageStore` to know who paid, `generate_structured_list` for the stage that returns a list |
| **[Typed CSS (stylesheet and tokens) »](ui-css.md)** | `StyleSheet` / `Rule` / `Media`, `ThemeTokens` (`tempest_core` tokens as CSS variables, light and dark), `make_css_router` with ETag/304, `app_stylesheet`, a `cls()` that rejects an unknown class |
| **[Typing (static + runtime) »](typing.md)** | `strict_types` / `typed` / `require_annotations`, `[tool.tempest] typing_strictness` knob, ruff `ANN` |
| **[UI layer (pages and components) »](ui.md)** | the `src/ui/` layer (pages, layout, components, styles), `Page` + inherited `shell()`, `Card` / `Alert` / `DataTable` / `Pagination` / `EmptyState` / `NavBar`, `Shell` / `Grid`, scaffolding with `tempest new --extras "ssr"` |
| **[Uploads (backends) »](uploads.md)** | `UploadUtils`, extension/MIME validation (`sniff_mime`), local / MinIO backends |
| **[Utilities »](utilities.md)** | `utcnow`/`to_utc`, `modify_dict`, `get_client_ip`, opaque tokens (`generate_opaque_token`) |
| **[Validated fields (ready-made types) »](fields.md)** | Annotated Pydantic types — `PositiveIntField` / `CentsField` / `PriceField` / `SlugField` / `HexColorField` / `CPFField` / `UFField` |
| **[Versioned artifacts (models) »](artifact-registry.md)** | `ArtifactRegistry`, `ArtifactVersionMixin`, `build_manifest_entries`, `file_digest` — swap the active version without a redeploy |
| **[Web Push »](webpush.md)** | `WebPushDispatcher`, VAPID schemas, broadcast with pruning |
| **[WebAuthn / passkeys »](webauthn.md)** | `WebAuthnService`, `make_web_authn_credential_model`, registration + passwordless login, memory/Redis challenge store |
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
