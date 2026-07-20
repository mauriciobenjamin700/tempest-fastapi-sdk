# Tour do SDK — exemplos de tudo

Um passeio guiado por **tudo** que o `tempest-fastapi-sdk` oferece: cada
bloco tem o conceito em uma linha, um exemplo mínimo runnable e o link
pra receita completa. Leia de cima a baixo pra ter o mapa mental, ou pule
pro que precisa.

!!! tip "Como usar este tour"
    Comece pelo [Tutorial](tutorial.md) (constrói a feature *Users* passo
    a passo). Este tour mostra como **cada** peça extra se encaixa nesse
    esqueleto. Instale só os extras que usar:
    `uv add "tempest-fastapi-sdk[auth,cache,queue]>=0.137.0"`.

## Fundação

`BaseAppSettings`, `AsyncDatabaseManager`, `create_app` factory, `run()`.

```python
from tempest_fastapi_sdk import AsyncDatabaseManager, BaseAppSettings


class Settings(BaseAppSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./app.db"


settings = Settings()
db = AsyncDatabaseManager(settings.DATABASE_URL)
```

Veja o [Tutorial](tutorial.md) e a receita de [Banco de dados](recipes/database.md).

## Schemas e campos validados

`BaseSchema` + tipos `Annotated` que se autodescrevem (dinheiro, %, slug,
lat/long, e brasileiros: CPF/CNPJ/CEP/telefone + **chave Pix**).

```python
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import CentsField, PixKeyField, SlugField


class ProductSchema(BaseSchema):
    slug: SlugField
    price_cents: CentsField          # int >= 0
    pix_key: PixKeyField             # CPF/CNPJ/e-mail/telefone/aleatória
```

Receitas: [Campos validados](recipes/fields.md), [Helpers brasileiros](recipes/br-helpers.md).

## Repository, Service, Controller

`BaseRepository[Model]` (CRUD + bulk ops), `BaseService`, `BaseController`
com `get_by_id`/`list`/`paginate`/`update`/`delete` prontos.

```python
from tempest_fastapi_sdk import BaseRepository, BaseService


class UserRepository(BaseRepository[UserModel]):
    def __init__(self, session):
        super().__init__(session, model=UserModel)


class UserService(BaseService[UserRepository, UserResponseSchema]):
    ...
```

Receitas: [Tutorial](tutorial.md), [Banco de dados](recipes/database.md).

## Paginação

Offset e cursor, com header `Link`.

```python
from tempest_fastapi_sdk import BasePaginationFilterSchema, CursorPaginationFilterSchema
```

## Exceções padronizadas

`AppException` + subclasses → HTTP correto; `register_exception_handlers(app)`.

```python
from tempest_fastapi_sdk import NotFoundException, register_exception_handlers

register_exception_handlers(app)
raise NotFoundException(message="user not found")   # -> 404 padronizado
```

## Autenticação completa

Fluxo bundled: signup/activate/login/reset/**troca e recuperação de
e-mail**/MFA + deps JWT (header/cookie/query).

```python
from tempest_fastapi_sdk import UserAuthService, make_auth_router

auth = UserAuthService(user_model=UserModel, token_model=UserTokenModel,
                       auth_settings=settings, jwt_settings=settings)
app.include_router(make_auth_router(auth, session_factory=db.session_dependency))
```

Receitas: [Auth flow](recipes/auth-flow.md), [MFA](recipes/mfa.md),
[Refresh tokens](recipes/refresh-tokens.md), [Sessões](recipes/sessions.md).

## Cache

`AsyncRedisManager` + `@cached` + `CacheInvalidator` (namespace/tag).

```python
from tempest_fastapi_sdk.cache import AsyncRedisManager, cached

redis = AsyncRedisManager(settings.REDIS_URL)


@cached(redis, ttl=300, namespace="products", tags=lambda a, k: [f"p:{k['pid']}"])
async def get_product(*, pid: str) -> dict: ...
```

Receita: [Cache](recipes/cache.md).

## Fila e tarefas em background

`MessageBroker` (pub/sub FastStream), `TaskQueue` (TaskIQ) + cron por
enum/helper, ambos escondendo a lib.

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

Receitas: [Fila e Tarefas](recipes/queue-tasks.md), [Outbox](recipes/outbox.md).

## Tempo real

SSE (`EventStream`/`SSEBroker` com backpressure), WebSocket router, Web Push.

```python
from tempest_fastapi_sdk import EventStream

@app.get("/events")
async def events():
    stream = EventStream()
    ...
    return stream.response(on_disconnect=task.cancel)
```

Receitas: [SSE](recipes/sse.md), [WebSocket](recipes/websocket.md),
[Web Push](recipes/webpush.md), [Tempo real](recipes/realtime.md).

## Observabilidade

Logging estruturado + `/logs`, métricas CPU/RAM/GPU + Prometheus `/metrics`,
request-id, tracing OTel, health + tool-spec.

```python
from tempest_fastapi_sdk import make_health_router, RequestIDMiddleware

app.add_middleware(RequestIDMiddleware)
app.include_router(make_health_router(checks={"db": db.health_check}))
```

Receitas: [Logging](recipes/logging.md), [Métricas](recipes/metrics.md),
[Observabilidade](recipes/observability.md).

## Hardening HTTP

Rate limit (sliding window), idempotência, CSRF, CORS, limite de body,
static seguro.

```python
from tempest_fastapi_sdk import RateLimitMiddleware, IdempotencyMiddleware

app.add_middleware(RateLimitMiddleware, store=..., max_requests=100, window_seconds=60)
app.add_middleware(IdempotencyMiddleware, store=...)
```

Receitas: [Camada HTTP](recipes/http.md), [Idempotência](recipes/idempotency.md),
[Segurança](recipes/security.md).

## Arquivos

`UploadUtils` (local/MinIO), `DownloadUtils`, `FileStoreUtils` (facade),
storage MinIO/S3, presigned URLs.

```python
from tempest_fastapi_sdk import FileStoreUtils

store = FileStoreUtils(source="./uploads")     # ou um AsyncMinIOClient
key = await store.save(upload_file)
```

Receitas: [File store](recipes/file-store.md), [Uploads](recipes/uploads.md),
[Downloads](recipes/downloads.md), [Storage](recipes/storage.md).

## Extras de domínio

Feature flags, audit trail, multi-tenant, sync offline-first, sessões
server-side, HTTP client tipado, i18n de erros.

```python
from tempest_fastapi_sdk import FeatureFlags, make_flag_dependency
```

Receitas: [Feature flags](recipes/feature-flags.md), [Audit trail](recipes/audit-trail.md),
[Multi-tenant](recipes/multi-tenant.md), [Sync offline](recipes/offline-sync.md),
[HTTP client](recipes/http-client.md).

## IA generativa self-hosted

Checagem de hardware, LLM local, embeddings, RAG (web + PDF) — tudo no
seu hardware.

!!! info "Instalação"
    O SDK já vem com `tempest-fastapi-sdk`. A IA generativa self-hosted depende do extra `[genai]` — `uv add "tempest-fastapi-sdk[genai]"` (traz `torch`, `transformers`, `accelerate`, `safetensors` e `huggingface-hub`).

```python
from tempest_fastapi_sdk.genai import can_run, TextGenerator
from tempest_fastapi_sdk.genai.rag import PdfReader, build_context

if can_run(model_id="Qwen/Qwen2.5-7B-Instruct").fits:
    gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct", quantization="int4")
    chunks = PdfReader().chunks("/kb/manual.pdf")
    answer = await gen.generate(build_context("como estornar?", chunks))
```

Receita: [IA generativa self-hosted](recipes/genai.md).

## Painel admin

`AdminSite` + `AdminModel` + `make_admin_router` (Jinja+HTMX, temas,
ações, upload, filtros).

Receita: [Painel admin](recipes/admin.md).

## SSR e visão

SSR tipado (`Page`/`html_response`) sobre `tempestweb`; visão computacional
(`Detector`/`Classifier`/`Segmenter`) via `ort-vision-sdk`.

Receitas: [SSR](ssr.md), [Visão](recipes/vision.md).

## CLI e deploy

`tempest new` (scaffold), `tempest db` (migrations), `tempest user`,
`tempest secrets`, gates de qualidade; deploy seguro (migrations + graceful
shutdown).

```bash
tempest new my-service && cd my-service
tempest db init && tempest db upgrade
tempest check          # ruff + mypy + testes
```

Receitas: [CLI](recipes/cli.md), [Deploy seguro](recipes/deploy-safety.md).

## Recap

O SDK cobre o ciclo inteiro de um serviço FastAPI: fundação tipada →
persistência → auth → cache → background → tempo real → observabilidade →
hardening → arquivos → IA → admin → CLI/deploy. Cada seção acima aponta pra
receita com o guia completo. Comece pelo [Tutorial](tutorial.md) e volte
aqui pra plugar cada capacidade conforme precisar.
