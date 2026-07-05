# SDK tour — examples of everything

A guided walk through **everything** the `tempest-fastapi-sdk` offers:
each block has the concept in one line, a minimal runnable example, and a
link to the full recipe. Read top to bottom for the mental map, or jump to
what you need.

!!! tip "How to use this tour"
    Start with the [Tutorial](tutorial.md) (builds the *Users* feature step
    by step). This tour shows how **each** extra piece slots into that
    skeleton. Install only the extras you use:
    `uv add "tempest-fastapi-sdk[auth,cache,queue]>=0.99.0"`.

## Foundation

`BaseAppSettings`, `AsyncDatabaseManager`, the `create_app` factory, `run()`.

```python
from tempest_fastapi_sdk import AsyncDatabaseManager, BaseAppSettings


class Settings(BaseAppSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./app.db"


settings = Settings()
db = AsyncDatabaseManager(settings.DATABASE_URL)
```

See the [Tutorial](tutorial.md) and the [Database](recipes/database.md) recipe.

## Schemas and validated fields

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

Recipes: [Validated fields](recipes/fields.md), [Brazilian helpers](recipes/br-helpers.md).

## Repository, Service, Controller

`BaseRepository[Model]` (CRUD + bulk ops), `BaseService`, `BaseController`
with `get_by_id`/`list`/`paginate`/`update`/`delete` ready.

```python
from tempest_fastapi_sdk import BaseRepository, BaseService


class UserRepository(BaseRepository[UserModel]):
    def __init__(self, session):
        super().__init__(session, model=UserModel)


class UserService(BaseService[UserRepository, UserResponseSchema]):
    ...
```

Recipes: [Tutorial](tutorial.md), [Database](recipes/database.md).

## Pagination

Offset and cursor, with a `Link` header.

```python
from tempest_fastapi_sdk import BasePaginationFilterSchema, CursorPaginationFilterSchema
```

## Standardized exceptions

`AppException` + subclasses → the right HTTP status;
`register_exception_handlers(app)`.

```python
from tempest_fastapi_sdk import NotFoundException, register_exception_handlers

register_exception_handlers(app)
raise NotFoundException(message="user not found")   # -> standardized 404
```

## Full authentication

Bundled flow: signup/activate/login/reset/**email change and recovery**/MFA
+ JWT deps (header/cookie/query).

```python
from tempest_fastapi_sdk import UserAuthService, make_auth_router

auth = UserAuthService(user_model=UserModel, token_model=UserTokenModel,
                       auth_settings=settings, jwt_settings=settings)
app.include_router(make_auth_router(auth, session_factory=db.session_dependency))
```

Recipes: [Auth flow](recipes/auth-flow.md), [MFA](recipes/mfa.md),
[Refresh tokens](recipes/refresh-tokens.md), [Sessions](recipes/sessions.md).

## Cache

`AsyncRedisManager` + `@cached` + `CacheInvalidator` (namespace/tag).

```python
from tempest_fastapi_sdk.cache import AsyncRedisManager, cached

redis = AsyncRedisManager(settings.REDIS_URL)


@cached(redis, ttl=300, namespace="products", tags=lambda a, k: [f"p:{k['pid']}"])
async def get_product(*, pid: str) -> dict: ...
```

Recipe: [Cache](recipes/cache.md).

## Queues and background tasks

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

Recipes: [Queues and Tasks](recipes/queue-tasks.md), [Outbox](recipes/outbox.md).

## Real time

SSE (`EventStream`/`SSEBroker` with backpressure), WebSocket router, Web Push.

```python
from tempest_fastapi_sdk import EventStream

@app.get("/events")
async def events():
    stream = EventStream()
    ...
    return stream.response(on_disconnect=task.cancel)
```

Recipes: [SSE](recipes/sse.md), [WebSocket](recipes/websocket.md),
[Web Push](recipes/webpush.md), [Real time](recipes/realtime.md).

## Observability

Structured logging + `/logs`, CPU/RAM/GPU metrics + Prometheus `/metrics`,
request-id, OTel tracing, health + tool-spec.

```python
from tempest_fastapi_sdk import make_health_router, RequestIDMiddleware

app.add_middleware(RequestIDMiddleware)
app.include_router(make_health_router(checks={"db": db.health_check}))
```

Recipes: [Logging](recipes/logging.md), [Metrics](recipes/metrics.md),
[Observability](recipes/observability.md).

## HTTP hardening

Rate limit (sliding window), idempotency, CSRF, CORS, body-size limit,
hardened static files.

```python
from tempest_fastapi_sdk import RateLimitMiddleware, IdempotencyMiddleware

app.add_middleware(RateLimitMiddleware, store=..., max_requests=100, window_seconds=60)
app.add_middleware(IdempotencyMiddleware, store=...)
```

Recipes: [HTTP layer](recipes/http.md), [Idempotency](recipes/idempotency.md),
[Security](recipes/security.md).

## Files

`UploadUtils` (local/MinIO), `DownloadUtils`, `FileStoreUtils` (facade),
MinIO/S3 storage, presigned URLs.

```python
from tempest_fastapi_sdk import FileStoreUtils

store = FileStoreUtils(source="./uploads")     # or an AsyncMinIOClient
key = await store.save(upload_file)
```

Recipes: [File store](recipes/file-store.md), [Uploads](recipes/uploads.md),
[Downloads](recipes/downloads.md), [Storage](recipes/storage.md).

## Domain extras

Feature flags, audit trail, multi-tenant, offline-first sync, server-side
sessions, typed HTTP client, i18n error envelopes.

```python
from tempest_fastapi_sdk import FeatureFlags, make_flag_dependency
```

Recipes: [Feature flags](recipes/feature-flags.md), [Audit trail](recipes/audit-trail.md),
[Multi-tenant](recipes/multi-tenant.md), [Offline sync](recipes/offline-sync.md),
[HTTP client](recipes/http-client.md).

## Self-hosted generative AI

Hardware check, local LLM, embeddings, RAG (web + PDF) — all on your own
hardware.

```python
from tempest_fastapi_sdk.genai import can_run, TextGenerator
from tempest_fastapi_sdk.genai.rag import PdfReader, build_context

if can_run(model_id="Qwen/Qwen2.5-7B-Instruct").fits:
    gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct", quantization="int4")
    chunks = PdfReader().chunks("/kb/manual.pdf")
    answer = await gen.generate(build_context("how to refund?", chunks))
```

Recipe: [Self-hosted generative AI](recipes/genai.md).

## Admin panel

`AdminSite` + `AdminModel` + `make_admin_router` (Jinja+HTMX, themes,
actions, upload, filters).

Recipe: [Admin panel](recipes/admin.md).

## SSR and vision

Typed SSR (`Page`/`html_response`) over `tempestweb`; computer vision
(`Detector`/`Classifier`/`Segmenter`) via `ort-vision-sdk`.

Recipes: [SSR](ssr.md), [Vision](recipes/vision.md).

## CLI and deploy

`tempest new` (scaffold), `tempest db` (migrations), `tempest user`,
`tempest secrets`, quality gates; safe deploy (migrations + graceful
shutdown).

```bash
tempest new my-service && cd my-service
tempest db init && tempest db upgrade
tempest check          # ruff + mypy + tests
```

Recipes: [CLI](recipes/cli.md), [Safe deploy](recipes/deploy-safety.md).

## Recap

The SDK covers the whole lifecycle of a FastAPI service: typed foundation
→ persistence → auth → cache → background → real time → observability →
hardening → files → AI → admin → CLI/deploy. Each section above points at
the recipe with the full guide. Start from the [Tutorial](tutorial.md) and
come back here to plug in each capability as you need it.
