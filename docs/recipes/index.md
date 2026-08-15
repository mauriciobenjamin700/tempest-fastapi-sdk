# Receitas

Passo a passo curtos no estilo "quero conectar X". Cada página começa com **qual problema resolve**, **quando recorrer a ela** e um exemplo de código completo que você pode copiar literalmente.

!!! tip "Comece por aqui"
    - **Serviço novo do zero?** Siga o **[Tutorial »](../tutorial.md)** — linear, constrói a feature *Users* passo a passo.
    - **Só precisa de uma assinatura?** Pule para a **[Referência »](../reference.md)**.
    - **Conectando uma peça específica?** Você está no lugar certo — o [tour](#tour-do-sdk-um-exemplo-por-bloco) abaixo dá o mapa, e o [índice](#indice-das-receitas) leva à receita completa.
    - **Quer ver tudo junto num app real?** Vá para os **[exemplos completos](#exemplos-completos)**.
    - **Quer estudar num projeto guiado?** Veja os **[Projetos de aprendizado »](../learning/index.md)**.

## Tour do SDK — um exemplo por bloco

Um passeio por **tudo** que o `tempest-fastapi-sdk` oferece: cada bloco tem o conceito em uma linha, um exemplo mínimo runnable e o link pra receita completa. Leia de cima a baixo pra ter o mapa mental, ou pule pro que precisa — instale só os extras que usar (`uv add "tempest-fastapi-sdk[auth,cache,queue]>=0.171.0"`).

### Fundação

`BaseAppSettings`, `AsyncDatabaseManager`, `create_app` factory, `run()`.

```python
from tempest_fastapi_sdk import AsyncDatabaseManager, BaseAppSettings


class Settings(BaseAppSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./app.db"


settings = Settings()
db = AsyncDatabaseManager(settings.DATABASE_URL)
```

Veja o [Tutorial](../tutorial.md) e a receita de [Banco de dados](database.md).

### Schemas e campos validados

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

Receitas: [Campos validados](fields.md), [Helpers brasileiros](br-helpers.md).

### Repository, Service, Controller

`BaseRepository[Model]` (CRUD + bulk ops), `BaseService`, `BaseController`
com `get_by_id`/`list`/`paginate`/`update`/`delete` prontos.

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

Receitas: [Tutorial](../tutorial.md), [Banco de dados](database.md).

### Paginação

Offset e cursor, com header `Link`.

```python
from tempest_fastapi_sdk import BasePaginationFilterSchema, CursorPaginationFilterSchema
```

### Exceções padronizadas

`AppException` + subclasses → HTTP correto; `register_exception_handlers(app)`.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import NotFoundException, register_exception_handlers

app = FastAPI()


register_exception_handlers(app)
raise NotFoundException(message="user not found")   # -> 404 padronizado
```

### Autenticação completa

Fluxo bundled: signup/activate/login/reset/**troca e recuperação de
e-mail**/MFA + deps JWT (header/cookie/query).

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

Receitas: [Auth flow](auth-flow.md), [MFA](mfa.md),
[Refresh tokens](refresh-tokens.md), [Sessões](sessions.md).

### Cache

`AsyncRedisManager` + `@cached` + `CacheInvalidator` (namespace/tag).

```python
from tempest_fastapi_sdk.cache import AsyncRedisManager, cached

from src.core.settings import settings


redis = AsyncRedisManager(settings.REDIS_URL)


@cached(redis, ttl=300, namespace="products", tags=lambda a, k: [f"p:{k['pid']}"])
async def get_product(*, pid: str) -> dict: ...
```

Receita: [Cache](cache.md).

### Fila e tarefas em background

`MessageBroker` (pub/sub FastStream), `TaskQueue` (TaskIQ) + cron por
enum/helper, ambos escondendo a lib.

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

Receitas: [Fila e Tarefas](queue-tasks.md), [Outbox](outbox.md).

### Tempo real

SSE (`EventStream`/`SSEBroker` com backpressure), WebSocket router, Web Push.

```python
import asyncio

from fastapi import FastAPI

from tempest_fastapi_sdk import EventStream

task = asyncio.current_task()

app = FastAPI()


@app.get("/events")
async def events():
    stream = EventStream()
    ...
    return stream.response(on_disconnect=task.cancel)
```

Receitas: [SSE](sse.md), [WebSocket](websocket.md),
[Web Push](webpush.md), [Tempo real](realtime.md).

### Observabilidade

Logging estruturado + `/logs`, métricas CPU/RAM/GPU + Prometheus `/metrics`,
request-id, tracing OTel, health + tool-spec.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import RequestIDMiddleware, make_health_router

from src.api.dependencies.resources import db

app = FastAPI()


app.add_middleware(RequestIDMiddleware)
app.include_router(make_health_router(checks={"db": db.health_check}))
```

Receitas: [Logging](logging.md), [Métricas](metrics.md),
[Observabilidade](observability.md).

### Hardening HTTP

Rate limit (sliding window), idempotência, CSRF, CORS, limite de body,
static seguro.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import IdempotencyMiddleware, RateLimitMiddleware

app = FastAPI()


app.add_middleware(RateLimitMiddleware, store=..., max_requests=100, window_seconds=60)
app.add_middleware(IdempotencyMiddleware, store=...)
```

Receitas: [Camada HTTP](http.md), [Idempotência](idempotency.md),
[Segurança](security.md).

### Arquivos

`UploadUtils` (local/MinIO), `DownloadUtils`, `FileStoreUtils` (facade),
storage MinIO/S3, presigned URLs.

```python
import asyncio

from fastapi import UploadFile

from tempest_fastapi_sdk import FileStoreUtils

upload_file: UploadFile = ...  # comes from the endpoint signature


store = FileStoreUtils(source="./uploads")     # ou um AsyncMinIOClient


async def main() -> None:
    """Run this example."""
    key = await store.save(upload_file)


asyncio.run(main())
```

Receitas: [File store](file-store.md), [Uploads](uploads.md),
[Downloads](downloads.md), [Storage](storage.md).

### Extras de domínio

Feature flags, audit trail, multi-tenant, sync offline-first, sessões
server-side, HTTP client tipado, i18n de erros.

```python
from tempest_fastapi_sdk import FeatureFlags, make_flag_dependency
```

Receitas: [Feature flags](feature-flags.md), [Audit trail](audit-trail.md),
[Multi-tenant](multi-tenant.md), [Sync offline](offline-sync.md),
[HTTP client](http-client.md).

### IA generativa self-hosted

Checagem de hardware, LLM local, embeddings, RAG (web + PDF) — tudo no
seu hardware.

!!! info "Instalação"
    O SDK já vem com `tempest-fastapi-sdk`. A IA generativa self-hosted depende do extra `[genai]` — `uv add "tempest-fastapi-sdk[genai]"` (traz `torch`, `transformers`, `accelerate`, `safetensors` e `huggingface-hub`).

```python
import asyncio

from tempest_fastapi_sdk.genai import can_run, TextGenerator
from tempest_fastapi_sdk.genai.rag import PdfReader, build_context


async def main() -> None:
    """Run this example."""
    if can_run(model_id="Qwen/Qwen2.5-7B-Instruct").fits:
        gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct", quantization="int4")
        chunks = PdfReader().chunks("/kb/manual.pdf")
        answer = await gen.generate(build_context("como estornar?", chunks))


asyncio.run(main())
```

Receita: [IA generativa self-hosted](genai.md).

### Painel admin

`AdminSite` + `AdminModel` + `make_admin_router` (Jinja+HTMX, temas,
ações, upload, filtros).

Receita: [Painel admin](admin.md).

### SSR e visão

SSR tipado (`Page`/`html_response`) sobre `tempestweb`; visão computacional
(`Detector`/`Classifier`/`Segmenter`) via `ort-vision-sdk`.

Receitas: [SSR](../ssr.md), [Visão](vision.md).

### CLI e deploy

`tempest new` (scaffold), `tempest db` (migrations), `tempest user`,
`tempest secrets`, gates de qualidade; deploy seguro (migrations + graceful
shutdown).

```bash
tempest new my-service && cd my-service
tempest db init && tempest db upgrade
tempest check          # ruff + mypy + testes
```

Receitas: [CLI](cli.md), [Deploy seguro](deploy-safety.md).

### Recap

O SDK cobre o ciclo inteiro de um serviço FastAPI: fundação tipada →
persistência → auth → cache → background → tempo real → observabilidade →
hardening → arquivos → IA → admin → CLI/deploy. Cada seção acima aponta pra
receita com o guia completo. Comece pelo [Tutorial](../tutorial.md) e volte
aqui pra plugar cada capacidade conforme precisar.

## Índice das receitas

| Tema | Cobre |
| --- | --- |
| **[Agentes de IA »](agents.md)** | `Agent` (objetivo → traço + artefatos), `AgentBudget` (passos/tempo/chamadas), `AgentTool` + ferramentas prontas sobre imagem/visão/áudio/RAG, `InMemoryAgentRunSink` / `DbAgentRunSink`, `make_agent_router` |
| **[Agentes de IA (avançado) »](agents-advanced.md)** | saída estruturada tipada (`run_structured`), três camadas de memória (`scratchpad_tools` / `fact_tools` / `recall_prompt`), `Skill` sob demanda, `agent_tool` para delegação, `run_until` / `refine` |
| **[Agentes de IA (testes) »](agents-testing.md)** | `ScriptedBackend` / `replies` / `replies_with_tool` para escrever as decisões do modelo, `assert_completed` / `assert_used_tools` / `assert_artifact`, `FailingBackend`, e a camada `@model` separada |
| **[Arquivo no serviço (mixin) »](stored-files.md)** | `StoredFileServiceMixin` — `set_file` / `replace` / `clear_file` sobre `UploadUtils` |
| **[Artefatos versionados (modelos) »](artifact-registry.md)** | `ArtifactRegistry`, `ArtifactVersionMixin`, `build_manifest_entries`, `file_digest` — versão ativa sem redeploy |
| **[Audit trail »](audit-trail.md)** | `BaseAuditLogModel`, `add_audited` / `update_audited` / `delete_audited`, `snapshot_model` / `diff_snapshots` |
| **[Auth flow (signup/reset) »](auth-flow.md)** | `UserAuthService`, `make_auth_router` — signup / ativação / login / reset de senha, entrega de token (bearer/cookie/ambos), `BaseUserModel` |
| **[Auth por introspecção (resource server) »](introspection-auth.md)** | `IntrospectionAuth` — validar bearer opaco perguntando ao provedor de identidade upstream |
| **[Banco de dados »](database.md)** | `BaseModel`, `AsyncDatabaseManager`, `BaseRepository` (CRUD + filtros + bulk), paginação offset/cursor, mixins, `AlembicHelper`, `SlowQueryLogger` |
| **[Busca textual (LIKE + full-text) »](text-search.md)** | `search()` portátil (ILIKE escapado, `AND` entre palavras), `full_text_search()` com `websearch_to_tsquery` + `ts_rank` no PostgreSQL, `TextSearchLanguage` / `TextSearchWeight` / `TokenMatch`, condições que entram em `where=` |
| **[Cache »](cache.md)** | `AsyncRedisManager`, decorator `@cached`, `CacheInvalidator` (tag/namespace) |
| **[Camada HTTP »](http.md)** | `apply_cors`, `RequestIDMiddleware`, `RateLimitMiddleware`, `make_health_router`, dependências de JWT / role / permissão, verificador de assinatura de webhook, headers Link de paginação, router de tool-spec |
| **[Camada UI (páginas e componentes) »](ui.md)** | a camada `src/ui/` (páginas, layout, componentes, estilos), `Page` + `shell()` herdado, `Card` / `Alert` / `DataTable` / `Pagination` / `EmptyState` / `NavBar`, `Shell` / `Grid`, scaffold via `tempest new --extras "ssr"` |
| **[Camada UI (páginas e componentes) »](ui.md)** | a camada `src/ui/` (páginas, layout, componentes, estilos), `Page` + `shell()` herdado, `Card` / `Alert` / `DataTable` / `Pagination` / `EmptyState` / `NavBar`, `Shell` / `Grid`, scaffold via `tempest new --extras "ssr"` |
| **[Campos validados (tipos prontos) »](fields.md)** | tipos Pydantic Annotated — `PositiveIntField` / `CentsField` / `PriceField` / `SlugField` / `HexColorField` / `CPFField` / `UFField` |
| **[Chat (conversas + mensagens) »](chat.md)** | `ChatService`, `make_chat_router`, tabelas base + fan-out em tempo real via `SSEBroker` |
| **[CLI »](cli.md)** | `tempest new` / `db` (+ `seed`) / `user` / `secrets rotate` / `lint` / `fix` / `format` / `type` / `test` / `check` |
| **[Cliente de integração (OpenAPI) »](openapi-client.md)** | `tempest openapi-client` — schemas Pydantic + client tipado a partir da spec de um terceiro |
| **[Colunas de enum (seguras nos dois bancos) »](enum-columns.md)** | `Mapped[MeuEnum]` guardando o `value`, `ENUM` nativo no PostgreSQL e `CHECK` no SQLite, `enum_column()`, `op.replace_enum` + `sync_enum_types` para a migration que o autogenerate não vê |
| **[Comentários + avaliações »](reviews.md)** | `ReviewService`, `make_reviews_router`, notas 0–5 estrelas com agregação, comentários encadeados |
| **[Console SQL no admin »](admin-sql-console.md)** | `SqlShellService` + `SqlShellPolicy` (capacidades, tabelas permitidas/negadas, teto de linhas, `require_where`), análise real via `sqlglot`, auditoria de toda tentativa, página opt-in no admin |
| **[CSS tipado (StyleSheet e tokens) »](ui-css.md)** | `StyleSheet` / `Rule` / `Media`, `ThemeTokens` (tokens do `tempest_core` como CSS variables, claro e escuro), `make_css_router` com ETag/304, `app_stylesheet`, `cls()` que rejeita classe inexistente |
| **[CSS tipado (StyleSheet e tokens) »](ui-css.md)** | `StyleSheet` / `Rule` / `Media`, `ThemeTokens` (tokens do `tempest_core` como CSS variables, claro e escuro), `make_css_router` com ETag/304, `app_stylesheet`, `cls()` que rejeita classe inexistente |
| **[Deploy seguro »](deploy-safety.md)** | `AlembicHelper.safe_upgrade` (barra DROPs), `GracefulShutdownMiddleware` |
| **[Downloads »](downloads.md)** | `DownloadUtils` — `file_response`, `stream`, `build_content_disposition`, anti path-traversal |
| **[Email transacional »](email.md)** | `EmailUtils` — SMTP, corpo texto/HTML, anexos, templates Jinja2 |
| **[Erros no OpenAPI (Swagger) »](openapi-errors.md)** | `error_responses`, `@raises`, `TempestAPIRouter`, `ErrorResponseSchema`, `tempest openapi-errors --fix` |
| **[Escolhendo o modelo »](models.md)** | `TextModel` / `EmbeddingModel` / `RerankerModel` / `VisionModel` / `ImageModel` / `SpeechToTextModel` / `TextToSpeechModel` — ids do Hub com nome, e a tabela de caso de uso por trás de cada escolha |
| **[Feature flags »](feature-flags.md)** | `FeatureFlags`, backends env/Redis/composto, `make_flag_dependency` |
| **[Fila e Tarefas »](queue-tasks.md)** | FastStream (`AsyncBrokerManager`), TaskIQ (`AsyncTaskBrokerManager`), `AsyncTaskScheduler`, outbox transacional |
| **[File store (unificado) »](file-store.md)** | `FileStoreUtils` — upload + download + presign sobre um backend só |
| **[Formulários a partir de schemas Pydantic »](ui-forms.md)** | `form_for` / `form_spec_for` / `render_form`, `parse_form` + `FormResult` (erros por campo e valores preservados), mapeamento tipo → controle, `json_schema_extra={"ui": ...}`, `form_stylesheet` |
| **[Formulários a partir de schemas Pydantic »](ui-forms.md)** | `form_for` / `form_spec_for` / `render_form`, `parse_form` + `FormResult` (erros por campo e valores preservados), mapeamento tipo → controle, `json_schema_extra={"ui": ...}`, `form_stylesheet` |
| **[Frontend tempestweb + SDK »](tempestweb-frontend.md)** | Frontend tempestweb chamando o backend do SDK: `tempestweb.native.http`, `Idempotency-Key` + `IdempotencyMiddleware`, retry, mesma origem vs CORS |
| **[Geolocalização (distância + tempo) »](geo.md)** | `haversine_km`, `estimate_travel`, `OSRMBackend`, `NominatimBackend`, `GeoPointMixin` / `GeoRepositoryMixin` |
| **[Geração de imagem (local) »](image-generation.md)** | `ImageGenerator` (diffusers local — `generate` / `edit` img2img), `ImageGenerationConfig`, `GeneratedImage` com a seed que reproduz, `make_genai_router(image_generator=...)` → `POST /image` |
| **[Geração de PDF »](pdf.md)** | `PdfRenderer`, cinco documentos prontos (recibo/orçamento/relatório/contrato/comprovante) com schema Pydantic, `make_pdf_router`, `tempest pdf render`, política de assets |
| **[Guards de permissão (@requires) »](permission-guards.md)** | `@requires` + guards `(user) -> user` (com `meta: dict[str, Any]` opcional via `meta=` / `include_args=`), `TempestPermissionError`, `GuardContractWarning`, `tempest permissions --check` |
| **[Helpers brasileiros »](br-helpers.md)** | validação + normalização de CPF / CNPJ / CEP / telefone |
| **[HTTP client (saída) »](http-client.md)** | `HTTPClient` — httpx tipado com retry/backoff, circuit-breaker, X-Request-ID; `RetryPolicy`, `CircuitOpenError` |
| **[IA generativa self-hosted »](genai.md)** | `probe_hardware` / `can_run`, `TextGenerator`, `Embedder`, RAG (web + PDF), áudio (STT/TTS), `make_genai_router` |
| **[Idempotência »](idempotency.md)** | `IdempotencyMiddleware`, `MemoryIdempotencyStore` / `IdempotencyStore` (Redis) — replay seguro de POST/PUT/PATCH/DELETE |
| **[Jobs (trabalho longo com status) »](jobs.md)** | `BaseJobModel` + `JobStore` — uma linha por unidade de trabalho, `claim`/`succeed`/`fail`, `watch` para a tela, `reclaim_stale` |
| **[Logging »](logging.md)** | `LogUtils`, logging JSON estruturado, propagação de request-ID |
| **[Login social (OAuth2/OIDC) »](oauth.md)** | `GoogleOAuthClient`, `GitHubOAuthClient`, `OIDCProvider`, `OAuthUser`, `generate_oauth_state` |
| **[Management commands (tempest &lt;cmd&gt;) »](management-commands.md)** | registrar comandos próprios na CLI `tempest` do projeto |
| **[Métricas »](metrics.md)** | `MetricsUtils` — snapshots de CPU / RAM / disco / GPU |
| **[MFA (TOTP / 2FA) »](mfa.md)** | `MFAMixin`, `TOTPHelper`, endpoints enroll/confirm/verify/disable no `make_auth_router`, códigos de recuperação |
| **[Modelops (export, bench, quantização) »](modelops.md)** | `benchmark_onnx` (latência/RAM/GPU/energia), `export_onnx_to_ort`, `quantize_onnx_dynamic`, `quantize_hf_onnx`, `rank` + fronteira de Pareto, `tempest model` |
| **[Multi-tenant »](multi-tenant.md)** | `TenantScopedRepository` — isolamento por `tenant_id` em toda query |
| **[Observabilidade (tracing) »](observability.md)** | `setup_tracing` (OpenTelemetry), `SlowQueryLogger` |
| **[OpenPix (Pix via Woovi) »](openpix.md)** | `OpenPixEnvironment`, `OpenPixEvent`, `make_openpix_webhook_dependency`, `to_cents` |
| **[Outbox transacional »](outbox.md)** | `BaseOutboxModel`, `OutboxRelay`, `save_with_outbox` — eventos confiáveis |
| **[Painel admin »](admin.md)** | `AdminSite`, `AdminModel`, `make_admin_router`, `BaseUserModel` |
| **[Permissões object-level »](authz.md)** | `permission` (decorator de regra), `has_perm` / `check_permission`, `PermissionRegistry`, `make_permission_checker`, `PermissionMixin` |
| **[Pesos de modelos (ciclo no Hub) »](model-weights.md)** | `ModelRef` (`revision` / `local_files_only` / `trust_remote_code`), `resolve_revision`, `download_model` com preflight de disco, `list_cached_models` / `remove_cached_model`, `tempest model pull` / `cache-list` / `cache-rm` |
| **[Planilhas (.xlsx) »](spreadsheets.md)** | `SheetWriter` (cursor de linha), `Column` (largura/máscara/alinhamento), `SheetStyle` como dado puro, formatos `BR_*` fixados em pt-BR, `new_workbook` / `workbook_to_bytes` |
| **[Planos de query (EXPLAIN) »](query-plans.md)** | `explain_queries()` captura o bloco e explica na saída, `EXPLAIN ANALYZE` no PostgreSQL / `EXPLAIN QUERY PLAN` no SQLite, escrita nunca reexecutada, `report.slowest` |
| **[Reconhecimento facial »](faces.md)** | `FaceRecognizer` (detectar / embutir / comparar), `compare_faces`, packs de 16 MB ou 191 MB, sem opencv e sem torch |
| **[Refresh tokens (rotação/revogação) »](refresh-tokens.md)** | `BaseUserRefreshTokenModel`, `make_user_refresh_token_model`, `issue_token_pair`, rotação + detecção de reuso por família |
| **[Segurança »](security.md)** | `AttemptThrottle`, helpers de token opaco, `HardenedStaticFiles`, headers de segurança |
| **[Server-Sent Events (SSE) »](sse.md)** | `EventStream`, `sse_response`, `ServerSentEvent`, `SSEBroker` (fan-out por canal, ponte Redis) |
| **[Sessões server-side »](sessions.md)** | `SessionMiddleware`, `SessionAuth`, `make_session_router`, `MemorySessionStore` / `RedisSessionStore` |
| **[SPA React no FastAPI »](react-spa.md)** | `make_spa_router` — servir o build do Vite pelo mesmo processo, com history fallback |
| **[SSR (páginas tipadas) »](../ssr.md)** | `Page`, `html_response`, `make_htmx_router`, hospedar um build do `tempestweb` |
| **[Storage (MinIO/S3) »](storage.md)** | `AsyncMinIOClient`, `MinIOUploadStorage`, `presigned_get_url` / `presigned_put_url`, `list_objects` |
| **[Sync offline-first (delta) »](offline-sync.md)** | `BaseRepository.changes_since`, `SyncFilterSchema`, `SyncPaginationSchema`, deltas por cursor + soft-delete |
| **[System checks (check-config) »](system-checks.md)** | `run_system_checks`, `@check`, `CheckMessage`, `tempest check-config` — validar settings antes de servir |
| **[Tempo real »](realtime.md)** | Visão geral — quando escolher SSE, WebSocket ou Web Push |
| **[Testes »](testing.md)** | `test_session`, `test_database`, SQLite em memória, fixtures pytest |
| **[Tipagem (estático + runtime) »](typing.md)** | `strict_types` / `typed` / `require_annotations`, knob `[tool.tempest] typing_strictness`, ruff `ANN` |
| **[Transações (commit e savepoint) »](transactions.md)** | `transaction()` compartilhado pela sessão, `commit()` / `flush()` / `rollback()` no repositório, `autocommit=False`, `savepoint()` para o passo recuperável |
| **[Uploads (backends) »](uploads.md)** | `UploadUtils`, validação de extensão/MIME (`sniff_mime`), backends local / MinIO |
| **[Utilitários »](utilities.md)** | `utcnow`/`to_utc`, `modify_dict`, `get_client_ip`, tokens opacos (`generate_opaque_token`) |
| **[Visão computacional (ONNX) »](vision.md)** | `Detector` / `Classifier` / `Segmenter` + schemas de predição |
| **[Web Push »](webpush.md)** | `WebPushDispatcher`, schemas VAPID, broadcast com poda |
| **[WebAuthn / passkeys »](webauthn.md)** | `WebAuthnService`, `make_web_authn_credential_model`, registro + login sem senha, store de desafios em memória/Redis |
| **[WebSocket router »](websocket.md)** | `WebSocketHub`, `make_websocket_router`, `broadcast` / `send_to`, heartbeat, auth via bearer |

## Exemplos completos

As receitas mostram uma peça por vez. Estas páginas juntam **várias** num fluxo que roda de ponta a ponta — leia quando quiser ver as decisões de integração, não a API isolada.

| Exemplo | O que junta |
| --- | --- |
| **[Admin de loja completo »](../admin-showcase.md)** | audit history + autocomplete FK + inlines + cards de negócio + import CSV + RBAC granular + lenses |
| **[Checkout com Pix »](../integrated.md)** | auth JWT + campos validados (`PixKeyField`) + cache + outbox transacional + `MessageBroker` + `TaskQueue` + SSE + Web Push |
| **[Fluxos de GenAI »](../genai-examples.md)** | capacidade de hardware → LLM local → embeddings/RAG → áudio, self-hosted de ponta a ponta |
| **[Fullstack web (SSR, WASM, server) »](../fullstack-web.md)** | os três modos de falar com o `tempestweb`: SSR + HTMX, SPA WASM e server-mode |
| **[Marketplace de bairro »](../marketplace-local.md)** | geo (vendedores próximos, distância/tempo) + chat em tempo real + notificações ao vivo + avaliações com estrelas |

## Anatomia de uma receita

Toda receita segue o mesmo formato de quatro seções para você bater o olho:

1. **O que resolve** — um parágrafo em linguagem simples.
2. **Quando usar** — lista de situações + quando *não* usar.
3. **O código** — completo, executável, com anotações `# 1. setup` / `# 2. wire` / `# 3. test`.
4. **Pegadinhas** — ressalvas de produção, defaults de segurança, notas de escala.

Se você encontrar uma receita que não segue esse formato, [abra uma issue](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/new) — tratamos regressões de doc como regressões de código.
