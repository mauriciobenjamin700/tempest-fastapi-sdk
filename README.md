# tempest-fastapi-sdk

[**📖 Documentação completa (PT-BR) →**](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/) · [**📖 Full documentation (EN-US) →**](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/)

> O site MkDocs é bilíngue (**PT-BR** padrão · **EN-US**) com seletor de idioma 🇧🇷/🇺🇸 no cabeçalho. — The MkDocs site is bilingual (**PT-BR** default · **EN-US**) with a 🇧🇷/🇺🇸 language switcher in the header.

- **PT-BR:** [Tutorial](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/tutorial/) · [Receitas](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/) · [Referência da API](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/)
- **EN-US:** [Tutorial](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/tutorial/) · [Recipes](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/) · [API reference](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/reference/)
- **🤖 For LLMs:** [llms.txt](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/llms.txt) (curated index) · [llms-full.txt](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/llms-full.txt) (the whole docs in one block) — [llmstxt.org](https://llmstxt.org) convention.

> 💡 `uv run mkdocs serve` (ou `make docs-serve`) é só para preview local — em produção use as URLs do GitHub Pages acima. / For local preview only — in production use the GitHub Pages URLs above.

Shared FastAPI/SQLAlchemy/Pydantic building blocks used across Tempest projects: base schemas, ORM model, async repository, pagination, settings, exceptions, Alembic helper, FastStream/TaskIQ broker managers, Redis cache, Server-Sent Events, Web Push, a Django-style **admin site** (`AdminSite` + `AdminModel`), **typed SSR pages** (`Page` + `html_response`, HTMX-ready), and the utility classes (`PasswordUtils`, `JWTUtils`, `EmailUtils`, `UploadUtils`, `DownloadUtils`, `FileStoreUtils`, `MetricsUtils`, `LogUtils`).

The goal is to start every new backend with the same opinionated foundation already in place — no copy-pasting `BaseModel`, no rewriting the same CRUD repository, no re-inventing the exception envelope.

---

## Table of contents

- [Install](#install)
  - [Optional extras](#optional-extras)
- [What's inside](#whats-inside)
- [Architecture overview](#architecture-overview)
- [Tutorial — building the *Users* feature](#tutorial--building-the-users-feature)
  - [1. Project layout](#1-project-layout)
  - [2. Settings, server, app factory & entry point](#2-settings-server-app-factory--entry-point)
  - [3. ORM model](#3-orm-model)
  - [4. Schemas](#4-schemas)
  - [5. Domain exceptions](#5-domain-exceptions)
  - [6. Repository](#6-repository)
  - [7. Service](#7-service)
  - [8. Controller](#8-controller)
  - [9. Dependency providers](#9-dependency-providers)
  - [10. Router](#10-router)
  - [11. Pagination](#11-pagination)
- [Recipes](#recipes)
  - [Authentication](#authentication-recipe)
  - [File uploads](#file-uploads-recipe)
  - [Transactional email](#transactional-email-recipe)
  - [Alembic migrations](#alembic-migrations-recipe)
  - [Utility helpers (`utcnow`, `to_utc`, `modify_dict`)](#utility-helpers-recipe)
  - [BR document & phone validation](#br-document--phone-validation-recipe)
  - [Testing](#testing-recipe)
  - [Application bootstrap (`create_app`)](#application-bootstrap-recipe)
  - [Structured logging & request IDs](#structured-logging--request-ids-recipe)
  - [Settings mixins composition](#settings-mixins-composition-recipe)
  - [Controllers & services layering](#controllers--services-layering-recipe)
  - [Audit & soft-delete mixins](#audit--soft-delete-mixins-recipe)
  - [Cursor pagination](#cursor-pagination-recipe)
  - [Redis cache (`AsyncRedisManager`)](#redis-cache-recipe)
  - [Server-Sent Events (SSE)](#server-sent-events-recipe)
  - [Web Push notifications](#web-push-notifications-recipe)
  - [Message queues — FastStream (`MessageBroker`)](#message-queues--faststream-recipe)
  - [Background tasks — TaskIQ (`TaskQueue`)](#background-tasks--taskiq-recipe)
  - [Periodic tasks scheduler (`AsyncTaskScheduler`)](#periodic-tasks-scheduler-recipe)
  - [System metrics (`MetricsUtils`)](#system-metrics-recipe)
  - [Programmatic server entry point (`run_server`)](#programmatic-server-entry-point-recipe)
  - [JWT bearer / current-user / role dependencies](#jwt-bearer--current-user--role-dependencies-recipe)
  - [CEP (Brazilian zipcode)](#cep-brazilian-zipcode-recipe)
  - [Cache decorator (`@cached`)](#cache-decorator-recipe)
  - [Tool-spec router (`make_tool_spec_router`)](#tool-spec-router-recipe)
  - [Webhook signature verification (`WebhookSignatureVerifier`)](#webhook-signature-verification-recipe)
  - [Pagination Link headers (`build_pagination_link_header`)](#pagination-link-headers-recipe)
  - [Rate limit middleware (`RateLimitMiddleware`)](#rate-limit-middleware-recipe)
  - [Outbox dispatcher pattern](#outbox-dispatcher-pattern-recipe)
  - [Base enums (`BaseStrEnum` / `BaseIntEnum`)](#base-enums-recipe)
  - [Runtime typing (`strict_types` / `typed` / `require_annotations`)](#runtime-typing-recipe)
  - [Hardened static files + cookie helpers](#hardened-static-files--cookie-helpers-recipe)
  - [Brute-force throttling (`AttemptThrottle`)](#brute-force-throttling-recipe)
  - [Opaque tokens (password reset / API keys)](#opaque-tokens-recipe)
  - [Client IP extraction (`get_client_ip`)](#client-ip-extraction-recipe)
  - [Command-line interface (`tempest new` / `lint` / `check`)](#command-line-interface-recipe)
  - [Admin site (`AdminSite` + `AdminModel`)](#admin-site-recipe)
  - [Migration guide 0.7 → 0.8](#migration-guide-07--08)
- [Reference](#reference)
- [Conventions](#conventions)
- [Development](#development)
- [Release](#release)
- [License](#license)

---

## Install

```bash
pip install tempest-fastapi-sdk
```

Via `pyproject.toml`:

```toml
dependencies = [
    "tempest-fastapi-sdk>=0.91.0",
]
```

Requires Python `>=3.11`.

### Optional extras

Feature-rich helpers pull in third-party dependencies that you only need when you actually use the helper. Pick the extras the service consumes:

| Extra | Pulls in | Unlocks |
| --- | --- | --- |
| `[auth]` | `bcrypt`, `PyJWT` | `PasswordUtils`, `JWTUtils` |
| `[email]` | `aiosmtplib`, `jinja2` | `EmailUtils` + `render_template()` |
| `[upload]` | `aiofiles`, `python-multipart` | `UploadUtils`, `LocalUploadStorage`, `MinIOUploadStorage` (when combined with `[minio]`) |
| `[cache]` | `redis` | `AsyncRedisManager` |
| `[webpush]` | `pywebpush`, `cryptography` | `WebPushDispatcher`, `WebPushSubscriptionService`, `BaseWebPushSubscriptionModel`, `make_web_push_router` |
| `[metrics]` | `psutil`, `nvidia-ml-py` | `MetricsUtils` |
| `[queue]` | `faststream[rabbit]` | `MessageBroker` (typed FastStream facade) |
| `[tasks]` | `taskiq`, `taskiq-aio-pika` | `TaskQueue` (typed TaskIQ facade) |
| `[admin]` | `jinja2`, `itsdangerous` | `AdminSite`, `AdminModel`, `make_admin_router` |
| `[minio]` | `minio` | `AsyncMinIOClient`, `ObjectStat`, `MinIOSettings` |
| `[http]` | `httpx` | `HTTPClient`, `RetryPolicy`, `CircuitOpenError`, OAuth2 / OIDC providers |
| `[prometheus]` | `prometheus-client` | `PrometheusMiddleware`, `make_prometheus_router`, `make_prometheus_registry` |
| `[mfa]` | `pyotp` | `TOTPHelper` + MFA/2FA endpoints on the bundled auth flow |
| `[vision]` | `ort-vision-sdk` | `Detector` / `Classifier` / `Segmenter` (ONNX) + prediction schemas |
| `[genai]` (+ `[genai-quant]`) | `transformers`, `torch`, `accelerate`, `safetensors`, `huggingface-hub` (+ `bitsandbytes`) | Self-hosted GenAI — hardware capacity check (`probe_hardware` / `can_run` / `recommend`); model runners upcoming |
| `[genai-rag]` | `httpx`, `trafilatura`, `pymupdf`, `pgvector` | RAG context for local LLMs — SearXNG web search, page extraction, PDF reading, `build_context`, vector store + `Retriever` |
| `[genai-audio]` | `faster-whisper`, `coqui-tts` | Self-hosted voice — `SpeechToText` (STT) + `TextToSpeech` (TTS) + `Language` presets (PT-BR/EN-US) |
| `[genai-ollama]` | `httpx` | Ollama backend — `OllamaGenerator` / `OllamaEmbedder` run text + embeddings against a local Ollama daemon instead of loading torch weights; drop into `make_genai_router` / `Retriever` |
| `[genai-chroma]` | `chromadb` | ChromaDB store — `ChromaVectorStore` (a `VectorStore`) + `ChatMemory` (recency-aware per-user long-term chat memory) |
| `[geo]` | `httpx` | Geolocation — `haversine_km`, `estimate_travel` (offline heuristic, no dep) + `OSRMBackend` (free OSRM routing) |
| `[ssr]` | `tempestweb` | `Page`, `html_response`, `make_htmx_router` — typed Python pages rendered to HTML |
| `[otel]` | `opentelemetry-sdk` + OTLP/gRPC exporter + FastAPI/SQLAlchemy/httpx instrumentors | `setup_tracing` — distributed tracing |
| `[sqlite]` | `aiosqlite` | SQLite async driver for `sqlite+aiosqlite://` URLs (dev default) |
| `[postgres]` | `asyncpg` | PostgreSQL async driver for `postgresql+asyncpg://` URLs (production) |
| `[all]` | everything above | every helper |

```bash
pip install "tempest-fastapi-sdk[auth,upload]"   # only what the service uses
pip install "tempest-fastapi-sdk[postgres]"       # add the async DB driver you deploy with
pip install "tempest-fastapi-sdk[all]"            # or pull everything
```

> **The SDK ships no database driver by default.** `sqlalchemy[asyncio]` is core, but the async DBAPI is your deploy choice — add `[sqlite]` (`aiosqlite`, dev default) or `[postgres]` (`asyncpg`, production). Without one, the engine raises `ModuleNotFoundError` for the driver on first connection. Services scaffolded with `tempest new` already pin `aiosqlite` and carry a commented `asyncpg` line in `pyproject.toml`.

Since `0.7.1` every optional dependency is imported lazily at first instantiation, so `import tempest_fastapi_sdk` works with any subset of extras — instantiating a helper whose extra is missing raises `ImportError` with a clear hint pointing at the right one.

---

## What's inside

| Module | Exports |
| --- | --- |
| `tempest_fastapi_sdk.schemas` | `BaseSchema`, `BaseResponseSchema`, `BasePaginationFilterSchema`, `BasePaginationSchema[T]`, `CursorPaginationFilterSchema`, `CursorPaginationSchema`, `LogEntrySchema`, `encode_cursor`, `decode_cursor`, `build_pagination_link_header` |
| `tempest_fastapi_sdk.db` | `BaseModel`, `BaseUserModel`, `BaseUserTokenModel`, `BaseUserRecoveryCodeModel`, `make_user_recovery_code_model`, `UserTokenPurpose`, `BaseRepository[ModelType]`, `TenantScopedRepository[ModelType]`, `AsyncDatabaseManager`, `AlembicHelper` (+ `safe_upgrade`), `DestructiveMigrationError`, `NAMING_CONVENTION`, `AuditMixin`, `SoftDeleteMixin`, `MFAMixin`, `BASE_COLUMN_ORDER`, `reorder_base_columns_first`, `compose_hooks`, `SlowQueryLogger`, `BaseOutboxModel`, `OutboxRelay`, `OutboxStatus`, `BaseRepository.save_with_outbox`, audit trail (`BaseAuditLogModel`, `AuditAction`, `snapshot_model`, `diff_snapshots`, `BaseRepository.add_audited` / `update_audited` / `delete_audited`), eager-loading (`get`/`get_or_none`/`get_by_id`/`first`/`list` accept `with_=[...]`, dotted for nested), lifecycle signals (`RepositorySignal`, `connect` / `on_signal` / `disconnect`, `PRE_SAVE`/`POST_SAVE`/`PRE_DELETE`/`POST_DELETE`), `BaseWebPushSubscriptionModel` + `make_web_push_subscription_model` |
| `tempest_fastapi_sdk.exceptions` | `AppException`, `NotFoundException`, `ConflictException`, `ValidationException`, `UnauthorizedException`, `ForbiddenException`, `InvalidTokenException`, `ExpiredTokenException`, `FileTooLargeException`, `InvalidFileTypeException`, `TooManyRequestsException`, i18n (`MessageCatalog`, `default_message_catalog`, `parse_accept_language`, `DEFAULT_LOCALE`) |
| `tempest_fastapi_sdk.settings` | `BaseAppSettings`, `ServerSettings`, `LogSettings`, `DatabaseSettings`, `RedisSettings`, `RabbitMQSettings`, `JWTSettings`, `CORSSettings`, `EmailSettings`, `UploadSettings`, `TokenSettings`, `WebPushSettings`, `TaskIQSettings`, `MinIOSettings`, `AuthSettings` |
| `tempest_fastapi_sdk.api` | `register_exception_handlers`, `app_exception_handler`, `apply_cors`, `make_health_router`, `make_logs_router`, `make_prometheus_router`, `make_prometheus_registry`, `PrometheusMiddleware`, `LogSource`, `make_tool_spec_router`, `make_token_dependency`, `make_bearer_token_dependency`, `make_jwt_user_dependency`, `make_role_dependency`, `make_permission_dependency`, `require_x_token`, `run_server`, `RequestIDMiddleware`, `RateLimitMiddleware` (+ `RateLimitStore`/`MemoryRateLimitStore`/`RedisRateLimitStore`/`RateLimitResult` and `key_by_ip`/`key_by_jwt_subject`/`key_by_jwt_claim`/`key_by_header`), `IdempotencyMiddleware`, `MemoryIdempotencyStore`, `RedisIdempotencyStore`, `BodySizeLimitMiddleware`, `CSRFMiddleware`, `make_csrf_token_dependency`, `GracefulShutdownMiddleware`, `WebhookSignatureVerifier`, `RSAWebhookSignatureVerifier`, OAuth2 (`GoogleOAuthClient`, `GitHubOAuthClient`, `OIDCProvider`), `HardenedStaticFiles`, `DEFAULT_STATIC_SECURITY_HEADERS`, `set_cookie`, `clear_cookie`, `SameSite`, `HealthCheck`, `setup_tracing` *(extra: `[otel]`)* |
| `tempest_fastapi_sdk.auth` *(extra: `[auth]`, opcional `[email]`, `[mfa]`)* | `UserAuthService`, `make_auth_router`, `SignupSchema` / `LoginSchema` / `RefreshSchema` / `PasswordResetRequestSchema` / `PasswordResetConfirmSchema` + responses, `ActivationToken`, `PasswordResetToken`, email change/verify/recovery (`EmailChangeRequestSchema` / `EmailChangeConfirmSchema` / `EmailRecoveryRequestSchema` / `EmailChangeToken` / `EmailVerificationToken`, old-email security notice, opt-in `AUTH_EMAIL_RECOVERY_ENABLED`), MFA schemas (`MFAEnrollResponseSchema` / `MFAConfirmSchema` / `MFAVerifySchema` / `MFADisableSchema`), opt-in DB-backed refresh tokens (`BaseUserRefreshTokenModel` / `make_user_refresh_token_model` / `LogoutSchema`) with rotation + reuse detection + `POST /auth/logout`, bilingual emails + backend pages (`AUTH_DEFAULT_LOCALE`, `normalize_locale` / `negotiate_locale`) — signup/activate/login/refresh/logout/reset + email change/recovery + TOTP 2FA out of the box |
| `tempest_fastapi_sdk.authz` | Object-level permissions: `permission` (rule decorator), `has_perm` / `check_permission`, `PermissionRegistry` (injectable superuser bypass + static-permission fallback, `order.*`/`*` wildcards), `make_permission_checker` (FastAPI route guard), `PermissionMixin` (`await user.has_perm(perm, obj=...)`), `default_registry` |
| `tempest_fastapi_sdk.controllers` | `BaseController` |
| `tempest_fastapi_sdk.services` | `BaseService` |
| `tempest_fastapi_sdk.core` | `configure_logging`, `JSONFormatter`, `get_request_id`/`set_request_id`/`clear_request_id`, `request_id_ctx`, `BaseStrEnum`, `BaseIntEnum`, `strict_types`/`typed`/`require_annotations` |
| `tempest_fastapi_sdk.admin` *(extra: `[admin]`)* | `AdminSite`, `AdminModel`, `make_admin_router`, `AdminAuthBackend`, `UserModelAuthBackend`, `AdminAuthError` |
| `tempest_fastapi_sdk.sse` | `EventStream` (bounded queue + `overflow` backpressure), `SSEBroker` (multi-worker fan-out via Redis, `.response()` lifecycle helper), `ServerSentEvent`, `sse_response` (`on_disconnect=` cleanup) |
| `tempest_fastapi_sdk.cache` *(extra: `[cache]`)* | `AsyncRedisManager`, `cached` (with `namespace` / `tags`), `CacheInvalidator`, `namespace_registry_key`, `tag_registry_key` |
| `tempest_fastapi_sdk.flags` | `FeatureFlags`, `FeatureFlagBackend`, `MemoryFeatureFlagBackend`, `EnvFeatureFlagBackend`, `RedisFeatureFlagBackend`, `CompositeFeatureFlagBackend`, `make_flag_dependency`, `coerce_flag` |
| `tempest_fastapi_sdk.webpush` *(extra: `[webpush]`)* | `WebPushDispatcher`, `WebPushSubscriptionService`, `make_web_push_router`, `WebPushError`, `WebPushGoneError`, `WebPushSubscriptionSchema`, `WebPushKeysSchema`, `WebPushPayloadSchema` |
| `tempest_fastapi_sdk.queue` *(extra: `[queue]`)* | `MessageBroker` (typed, transport-agnostic FastStream facade — `.rabbitmq`/`.redis`/`.kafka`/`.nats`, `@mq.on(channel)`, channel-first `publish`); class-based consumers (`Consumer` + `@subscribe` + `MessageBroker.register`); `AsyncQueueManager` (thin lifecycle wrapper, formerly `AsyncBrokerManager`) |
| `tempest_fastapi_sdk.storage` *(extra: `[minio]`)* | `AsyncMinIOClient`, `ObjectStat` — async MinIO/S3 facade |
| `tempest_fastapi_sdk.utils.http_client` *(extra: `[http]`)* | `HTTPClient`, `RetryPolicy`, `CircuitOpenError`, `REQUEST_ID_HEADER` — typed httpx wrapper |
| `tempest_fastapi_sdk.utils.storage_backends` *(extra: `[upload]`)* | `UploadStorage` protocol, `LocalUploadStorage`, `MinIOUploadStorage`, `UploadResult`, `ContentValidator` |
| `tempest_fastapi_sdk.tasks` *(extra: `[tasks]`)* | `TaskQueue` (typed TaskIQ facade — `.rabbitmq`/`.redis`/`.memory`, `@tq.task` → `Task.enqueue`/`.run`, `@tq.cron`/`@tq.interval`, `start_scheduler`), `Task`; class-based tasks (`TaskDef` + `@task_method` + `TaskQueue.register`); cron helpers (`Cron` / `CronOffset` / `Weekday` + `daily`/`weekdays`/`every_n_minutes`/… builders); `AsyncTaskBrokerManager` / `AsyncTaskScheduler` (legacy wrappers) |
| `tempest_fastapi_sdk.vision` *(extra: `[vision]`)* | `Detector`, `Classifier`, `Segmenter` (ONNX, lazy), `DetectionSchema`/`ClassificationSchema`/`SegmentationSchema`/`BoundingBoxSchema`/`ClassProbabilitySchema`, `to_detection_schemas`/`to_classification_schema`/`to_segmentation_schemas` |
| `tempest_fastapi_sdk.genai` *(extra: `[genai]`; capacity check imports without it)* | Self-hosted GenAI: capacity check (`probe_hardware`/`HardwareInfo`, `can_run`/`recommend`/`CapacityReport`, `estimate_model_bytes`/`fetch_num_params`, `ModelDtype`); local LLM `TextGenerator` (`generate`/`chat`/`stream`, typed `GenerationConfig`, int4/int8 quant, lazy load + idle unload); `Embedder` (+ `EmbeddingCache`/`InMemoryEmbeddingCache`/`RedisEmbeddingCache`/`AsyncEmbeddingCache`, `normalize=`) + `cosine_similarity`, `BatchScheduler` (coalesce concurrent calls), `ModelRegistry` (LRU model sharing); `make_genai_router` (opt-in endpoints — `/generate`+SSE, `/chat`, `/embed`, `/rag`, `/transcribe`, `/tts`); `resolve_device`/`auto_dtype_name`; Ollama backend (`[genai-ollama]`) — `OllamaGenerator`/`OllamaEmbedder` (HTTP, no torch) behind the `TextBackend` protocol, drop-in for `make_genai_router`/`Retriever`, plus vision (`generate(images=…)`) and `chat_with_tools`; **`AIChatPipeline`** (memory recall → web-search augment → generate w/ bounded tool-calling loop → TTS → memory index) + `Tool` + `make_ai_chat_router` (`POST /chat` + `/chat/stream` SSE) |
| `tempest_fastapi_sdk.chat` | Threaded chat: abstract tables `BaseConversationModel`/`BaseConversationParticipantModel`/`BaseMessageModel` (+ `make_*` factories), `ChatService` (`start_conversation`/`post_message`/`list_messages`/`list_conversations`), `make_chat_router` (opt-in), real-time fan-out via an injected `SSEBroker` |
| `tempest_fastapi_sdk.reviews` | Comments + 0–5 star ratings on any polymorphic target: `BaseCommentModel`/`BaseRatingModel` (+ `make_*`), `ReviewService` (`add_comment`/`list_comments`/`rate` upsert/`get_user_rating`/`aggregate`), `make_reviews_router` (opt-in), `RatingField` |
| `tempest_fastapi_sdk.genai.rag` *(extra: `[genai-rag]`)* | RAG context for local LLMs: `WebSearch` (`.retrieve` one-shot search→extract→context) / `SearxngBackend` / `WebSearchBackend` + `SearchResult`, `ContentExtractor` (trafilatura, `.extract_many` concurrent), `PdfReader` (PyMuPDF) + `Document`/`Chunk`/`PdfPage`, `chunk_text`, `build_context`; corpus RAG — `Retriever` + `VectorStore`/`InMemoryVectorStore`/`PgVectorStore` (pgvector)/`ChromaVectorStore` (`[genai-chroma]`); long-term chat memory — `ChatMemory` (recency-aware, per-user, Chroma-backed) + `MemoryHit`; audio (`[genai-audio]`) — `SpeechToText` (faster-whisper, `beam_size`/`vad_filter`/`language_probability`) + `TextToSpeech` (Coqui TTS) + `Transcription` |
| `tempest_fastapi_sdk.geo` *(extra: `[geo]`; offline layers import without it)* | Geolocation without a paid API: `haversine_km`, `estimate_travel` (offline road distance + per-mode time), offline geometry (`bounding_box`, `within_radius`, `nearest`, `initial_bearing`, `destination_point`, `point_in_polygon`, `polygon_area_km2`, `path_length_km`), DB radius search (`GeoPointMixin` + `GeoRepositoryMixin`/`PostGISRepositoryMixin`, `make_geo_point_model`), routing (`OSRMBackend`/`RoutingBackend` — `route` + `route(with_geometry=True)` + `matrix`, per-mode profiles), geocoding (`NominatimBackend`/`GeocodingBackend`), polyline codec (`encode_polyline`/`decode_polyline`), Brazil (`uf_centroid`/`UF_CENTROIDS`, `cep_to_coordinate`); `Coordinate`/`TravelEstimate`/`TravelMode` (car/motorcycle/bus/bicycle/pedestrian)/`BoundingBox`/`GeocodeResult`/`DistanceMatrix` |
| `tempest_fastapi_sdk.ssr` *(extra: `[ssr]`)* | `Page` (typed component base), `html_response` (widget tree → `HTMLResponse`, full document or HTMX fragment), `make_htmx_router` (serves bundled HTMX locally, no CDN) |
| `tempest_fastapi_sdk.utils` | `to_utc`, `utcnow`, `modify_dict`, `LogUtils`, `AttemptThrottle`/`ThrottleBackend`/`ThrottleStatus`, `generate_opaque_token`/`hash_opaque_token`/`verify_opaque_token`, `get_client_ip`/`get_client_ip_from_scope`, `PasswordUtils` *(extra: `[auth]`)*, `JWTUtils` *(extra: `[auth]`)*, `TOTPHelper` *(extra: `[mfa]`)*, `EmailUtils` *(extra: `[email]`)*, `UploadUtils`/`sniff_mime` *(extra: `[upload]`)*, `DownloadUtils`/`build_content_disposition` *(no extra)*, `FileStoreUtils` — unified upload+download+presign facade *(extra: `[upload]` local / `[minio]` MinIO)*, `MetricsUtils`/`CPUMetrics`/`MemoryMetrics`/`DiskMetrics`/`GPUMetrics`/`SystemMetrics` *(extra: `[metrics]`)*, validated field types (`PositiveIntField`, `NonNegativeIntField`, `CentsField`, `PortField`, `PositiveFloatField`, `NonNegativeFloatField`, `PercentField`, `RatingField`, `RatioField`, `LatitudeField`, `LongitudeField`, `PriceField`, `NonEmptyStrField`, `SlugField`, `HexColorField`), BR regex helpers (`CPFField`, `CNPJField`, `CPFOrCNPJField`, `PhoneBRField`, `CEPField`, `PixKeyField` — old names without the suffix kept as deprecated aliases — `PixKeyType`/`detect_pix_key_type`/`is_valid_pix_key`, `is_valid_*`, `normalize_*`, `only_digits`, `*_PATTERN`), BR states/cities (`UF`, `Region`, `StateBR`, `CityBR`, `ChoiceBR`, `UFField`, `CityNameField`, `list_states`, `get_state`, `cities_by_uf`, `states_by_region`, `uf_choices`/`region_choices`/`city_choices`, `is_valid_uf`/`normalize_uf`, `is_valid_city`/`normalize_city`) |
| `tempest_fastapi_sdk.cli` | `tempest` console script — `new <name>` (scaffold layered service), `lint` / `format` / `fmt-check` / `type` / `test` / `check` (run preferred quality gates), `version` / `--version` |

Core primitives are re-exported from `tempest_fastapi_sdk` at the top level — `from tempest_fastapi_sdk import BaseModel, BaseRepository, AppException` always works. The extras-gated managers in `tempest_fastapi_sdk.cache`, `tempest_fastapi_sdk.queue`, `tempest_fastapi_sdk.tasks`, `tempest_fastapi_sdk.vision`, `tempest_fastapi_sdk.genai` and `tempest_fastapi_sdk.ssr` — plus the domain modules `tempest_fastapi_sdk.chat` and `tempest_fastapi_sdk.reviews` — must be imported from their own submodule (`from tempest_fastapi_sdk.queue import MessageBroker`).

---

## Architecture overview

The SDK assumes a layered architecture where each layer has a single, narrow responsibility:

```text
HTTP request
    │
    ▼
┌─────────────┐    receive HTTP, validate input via schemas,
│   Router    │    call service, return response schema.
└──────┬──────┘    No business logic, no DB access.
       │
       ▼
┌─────────────┐    orchestrate use case across one or more services;
│ Controller  │    handle cross-service coordination only.
└──────┬──────┘    Optional — skip for simple CRUD.
       │
       ▼
┌─────────────┐    business rules, validation beyond Pydantic,
│   Service   │    domain decisions. Calls one or more repositories.
└──────┬──────┘    No HTTP types, no SQLAlchemy types.
       │
       ▼
┌─────────────┐    raw data access via SQLAlchemy. CRUD, filters,
│ Repository  │    pagination. Translates between ORM and schemas
└──────┬──────┘    via map_to_* methods. No business decisions.
       │
       ▼
┌─────────────┐    SQLAlchemy AsyncSession on top of asyncpg/aiosqlite.
│  Database   │
└─────────────┘
```

The SDK ships **`BaseModel`**, **`BaseRepository`**, **`BaseSchema`** and the exception/settings primitives. Routers, services and controllers are your code — the SDK gives you the conventions so they all look the same across projects.

---

## Tutorial — building the *Users* feature

We'll build a complete `Users` feature from scratch, end to end. Every file below is something you write in your project; SDK primitives are imported.

### 1. Project layout

The canonical layout every Python service shipped against this SDK should adopt — `main.py` is a one-liner, `src/server.py` exposes both `run()` and the importable `app` (or re-exports it from `src/api/app.py`), `api/dependencies/` is **always a package** (auth + factory providers), `controllers/` is mandatory even when it's only a thin pass-through, and `repositories/` lives **under** `db/`.

```text
my-service/
├── main.py                       # one-liner: from src.server import run; run()
└── src/
    ├── __init__.py               # re-exports `run` from src.server
    ├── server.py                 # programmatic uvicorn.run(...) + module-level `app`
    ├── core/
    │   ├── __init__.py
    │   ├── settings.py           # Settings(BaseAppSettings, mixins...)
    │   └── exceptions.py         # domain exceptions (UserNotFoundError, ...)
    ├── db/
    │   ├── __init__.py           # re-exports BaseModel + every model
    │   ├── models/
    │   │   ├── __init__.py
    │   │   └── user.py           # UserModel(BaseModel)
    │   └── repositories/
    │       ├── __init__.py
    │       └── user.py           # UserRepository(BaseRepository[UserModel])
    ├── schemas/
    │   ├── __init__.py
    │   └── user.py               # UserCreate/Update/Response/Filter
    ├── services/
    │   ├── __init__.py
    │   └── user.py               # UserService — business logic
    ├── controllers/
    │   ├── __init__.py
    │   └── user.py               # UserController — orchestration (thin pass-through OK)
    └── api/
        ├── __init__.py
        ├── app.py                # create_app() — middleware, CORS, exception handlers, routers
        ├── routers/
        │   ├── __init__.py
        │   └── users.py
        └── dependencies/         # ALWAYS a package, never a flat module
            ├── __init__.py
            ├── auth.py           # X-Token / current_user / require_role dependencies
            └── controllers.py    # get_<X>_controller / get_<X>_service factories
```

Each `__init__.py` re-exports every public symbol from its directory so consumers always do `from src.schemas import UserCreateSchema` (not `from src.schemas.user import UserCreateSchema`). This keeps refactors painless — move files around without breaking imports.

If your service has no controllers/services/repositories yet, **still ship empty packages with the right names** — uniformity matters more than skipping a directory. Drop `db/`, `utils/`, `queue/` or `tasks/` only when the service genuinely doesn't need persistence/utilities/messaging.

### 2. Settings, server, app factory & entry point

Four files map onto four responsibilities:

| File | Responsibility |
| --- | --- |
| `src/core/settings.py` | `Settings(BaseAppSettings, ...mixins)` — one source of truth for env vars. |
| `src/api/app.py` | `create_app()` factory + middleware + CORS + exception handlers + router includes + module-level `app` instance. |
| `src/server.py` | `run()` invoking `uvicorn.run("src.api.app:app", ...)` programmatically, plus re-exports `app` so external runners (gunicorn, uvicorn CLI) can import it. |
| `main.py` | Process entry point — a single line under `if __name__ == "__main__":` calling `run()`. |

```python
# src/core/settings.py
from tempest_fastapi_sdk import BaseAppSettings, DatabaseSettings, ServerSettings


class Settings(ServerSettings, DatabaseSettings, BaseAppSettings):
    """All environment-driven configuration lives here.

    BaseAppSettings ships `env_file=.env`, `extra=ignore`,
    `case_sensitive=True`, `frozen=True` and `str_strip_whitespace=True`.
    ServerSettings adds SERVER_HOST/PORT/RELOAD, DatabaseSettings adds
    DATABASE_URL/ECHO/POOL_*.
    """

    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_TTL_HOURS: int = 1

    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM_ADDR: str = "noreply@example.com"

    UPLOAD_DIR: str = "./var/uploads"


settings = Settings()
```

```python
# src/api/app.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    RequestIDMiddleware,
    make_health_router,
    register_exception_handlers,
)

from src.api.routers import users
from src.core.settings import settings


db = AsyncDatabaseManager(settings.DATABASE_URL)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Connect on startup, dispose on shutdown."""
    await db.connect()
    try:
        yield
    finally:
        await db.disconnect()


def create_app() -> FastAPI:
    """Build and configure the FastAPI app."""
    app = FastAPI(title="my-service", version="0.1.0", lifespan=lifespan)

    app.add_middleware(RequestIDMiddleware)
    register_exception_handlers(app)

    # Meta endpoints sit at the root prefix.
    app.include_router(make_health_router(db=db, version="0.1.0"))

    # Business endpoints sit under /api/<domain>.
    app.include_router(users.router, prefix="/api")
    return app


app = create_app()
```

```python
# src/server.py
from tempest_fastapi_sdk import run_server

from src.api.app import app  # noqa: F401 — re-exported for external runners
from src.core.settings import settings


def run() -> None:
    """Start the API server programmatically."""
    run_server("src.api.app:app", settings=settings)


__all__: list[str] = ["app", "run"]
```

`run_server` reads `SERVER_HOST` / `SERVER_PORT` / `SERVER_RELOAD` from `settings` (falling back to `127.0.0.1` / `8000` / `False`) and forwards any extra kwargs (`workers=`, `log_config=`, `ssl_*=`) verbatim to `uvicorn.run`. See the [Programmatic server entry point recipe](#programmatic-server-entry-point-recipe).

```python
# src/__init__.py
from src.server import run

__all__: list[str] = ["run"]
```

```python
# main.py
from src.server import run

if __name__ == "__main__":
    run()
```

Bind defaults: `127.0.0.1` for internal services (the SDK's `ServerSettings.SERVER_HOST` default), `0.0.0.0` only when the service is consumed by a separate origin (e.g. a frontend dev server). Never start uvicorn via `subprocess.run(["uvicorn", ...])` — always go through `run_server` (or `uvicorn.run("src.api.app:app", ...)` directly) so reload, signal handling and graceful shutdown behave correctly.

### 3. ORM model

```python
# src/db/models/user.py
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class UserModel(BaseModel):
    """One row per registered user.

    Inherits from BaseModel, so it automatically gets:
    - id (UUID v4, cross-DB portable via sqlalchemy.Uuid)
    - is_active (bool, soft-delete flag)
    - created_at, updated_at (timezone-aware TIMESTAMP, set by Python AND
      the DB so the instance attribute is populated right after flush)
    - __tablename__ = "user" (auto: class name without "Model" suffix,
      snake-cased; override by assigning __tablename__ explicitly)
    - __eq__/__hash__ by (type, id) so the same row across sessions
      compares equal
    - to_dict(exclude, include, remove_none) and
      update_from_dict(data, allowed_fields) helpers
    """

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
```

Re-export it:

```python
# src/db/models/__init__.py
from src.db.models.user import UserModel

__all__: list[str] = ["UserModel"]
```

```python
# src/db/__init__.py
from src.db.models import UserModel
from tempest_fastapi_sdk import BaseModel

__all__: list[str] = ["BaseModel", "UserModel"]
```

> **Tip:** Always import models in `src/db/__init__.py`. SQLAlchemy needs to "see" every model before `BaseModel.metadata` is complete, so Alembic autogenerate and `create_tables()` work correctly.

### 4. Schemas

The recommended naming pattern: one `*Create`, `*Update`, `*Response` and `*Filter` schema per resource.

```python
# src/schemas/user.py
from pydantic import EmailStr, Field

from tempest_fastapi_sdk import (
    BasePaginationFilterSchema,
    BaseResponseSchema,
    BaseSchema,
)


class UserCreateSchema(BaseSchema):
    """Payload for POST /users."""

    name: str = Field(min_length=1, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserUpdateSchema(BaseSchema):
    """Partial payload for PATCH /users/{id}. Every field optional."""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    email: EmailStr | None = None


class UserResponseSchema(BaseResponseSchema):
    """Outbound representation.

    Inherits id/is_active/created_at/updated_at from BaseResponseSchema
    (timestamps already normalized to UTC by the field validator).
    """

    name: str
    email: EmailStr


class UserFilterSchema(BasePaginationFilterSchema):
    """Query-string filters for GET /users.

    Inherits page/page_size/order_by/ascending/is_active from
    BasePaginationFilterSchema. Add domain-level filters below.
    """

    name: str | None = None              # ILIKE %name% search
    email: EmailStr | None = None        # exact-match filter
```

```python
# src/schemas/__init__.py
from src.schemas.user import (
    UserCreateSchema,
    UserFilterSchema,
    UserResponseSchema,
    UserUpdateSchema,
)

__all__: list[str] = [
    "UserCreateSchema",
    "UserFilterSchema",
    "UserResponseSchema",
    "UserUpdateSchema",
]
```

### 5. Domain exceptions

The SDK ships generic `NotFoundException`, `ConflictException`, etc. Subclass them per domain so the `isinstance` / `except DomainError` matching stays explicit. Class-level `message` / `code` / `status_code` are defaults the constructor falls back to — you can also override any of them at the raise site without subclassing:

```python
# src/core/exceptions.py
from tempest_fastapi_sdk import ConflictException, NotFoundException


class UserNotFoundError(NotFoundException):
    """Subclass kept only for ``except UserNotFoundError`` matching."""

    message: str = "Usuário não encontrado"
    code: str = "USER_NOT_FOUND"


class UserEmailAlreadyTakenError(ConflictException):
    message: str = "Já existe um usuário com esse e-mail"
    code: str = "USER_EMAIL_TAKEN"
```

For one-off codes you don't need a subclass — pass them to the constructor:

```python
raise NotFoundException(
    "Pedido não encontrado",
    code="ORDER_NOT_FOUND",
    details={"order_id": str(order_id)},
)
```

The SDK's exception handler ([`register_exception_handlers`](#2-settings-server-app-factory--entry-point)) serializes them to:

```json
{
    "detail": "Usuário não encontrado",
    "code": "USER_NOT_FOUND",
    "details": {}
}
```

The frontend branches on `code`, not on the (potentially translated) message.

### 6. Repository

For plain CRUD you don't need a subclass at all — instantiate `BaseRepository` directly and bind the model via the constructor:

```python
# anywhere a session is in scope
from tempest_fastapi_sdk import BaseRepository

from src.db.models import UserModel

repository = BaseRepository(session, model=UserModel)
await repository.add(
    UserModel(
        email="ana@example.com",
        name="Ana",
        password_hash="<bcrypt-hash>",
    )
)
```

Subclass when you want to bake in domain-specific messages, swap the not-found exception, override the mapper methods or add custom queries. The constructor signature (not class attributes) is the contract:

```python
# src/db/repositories/user.py
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository

from src.core.exceptions import UserNotFoundError
from src.db.models import UserModel
from src.schemas import UserResponseSchema


class UserRepository(BaseRepository[UserModel]):
    """Data-access layer for users."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session,
            model=UserModel,
            not_found_exception=UserNotFoundError,
            not_found_message="Usuário não encontrado",
            create_conflict_message="Já existe um usuário com esse e-mail",
            update_conflict_message="Conflito ao atualizar usuário",
        )

    def map_to_schema(self, instance: UserModel) -> UserResponseSchema:
        return UserResponseSchema.model_validate(instance)

    def map_to_response(self, instance: UserModel) -> UserResponseSchema:
        return self.map_to_schema(instance)
```

The base repo gives you 20+ methods for free — see the [reference table](#baserepository-methods) below. Add custom queries on top of the same `UserRepository`:

```python
# src/db/repositories/user.py  (continued)
class UserRepository(BaseRepository[UserModel]):
    # ... __init__ and mappers above ...

    # ──────── custom queries on top of the inherited bulk + read methods ────────

    async def get_by_email(self, email: str) -> UserModel:
        """Look up a user by email. Raises ``UserNotFoundError`` on miss."""
        return await self.get({"email": email})
```

The highlighted block (under the divider comment) is what you typically add per project — everything above it is the boilerplate the base class already takes care of.

### 7. Service

The service is where business rules live. It calls one or more repositories and never touches HTTP or SQLAlchemy types directly.

Inherit from `BaseService[RepositoryT, ResponseT]`. Doing so gives you `get_by_id`, `get_or_none`, `list`, `paginate`, `count`, `exists`, `update` and `delete` for free — every one is already wired to `repository.map_to_response` (sync or async). Override only the methods that need domain logic; add new ones for use cases the base doesn't cover (signup, password reset, etc.):

```python
# src/services/user.py
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseService, PasswordUtils

from src.core.exceptions import UserEmailAlreadyTakenError
from src.db.repositories import UserRepository
from src.schemas import UserCreateSchema, UserResponseSchema, UserUpdateSchema


class UserService(BaseService[UserRepository, UserResponseSchema]):
    """Business logic for the user domain.

    Inherits the canonical read-path methods (``get_by_id`` / ``list`` /
    ``paginate`` / ``count`` / ``exists`` / ``delete``) from
    :class:`BaseService` and adds the write-path methods that need
    domain rules (uniqueness check, password hashing, mass-assignment
    guard).
    """

    def __init__(
        self,
        repository: UserRepository,
        *,
        passwords: PasswordUtils,
    ) -> None:
        """Initialize the service.

        Args:
            repository (UserRepository): User-domain repository.
            passwords (PasswordUtils): Shared bcrypt helper.
        """
        super().__init__(repository)
        self.passwords: PasswordUtils = passwords

    # ──────── overrides: domain rules live here ────────

    async def signup(self, data: UserCreateSchema) -> UserResponseSchema:
        """Create a new user, enforcing email uniqueness + hashing the password."""
        if await self.repository.exists({"email": data.email}):
            raise UserEmailAlreadyTakenError()
        instance = self.repository.map_to_model(
            {
                **data.to_dict(exclude=["password"]),
                "password_hash": self.passwords.hash(data.password),
            },
        )
        instance = await self.repository.add(instance)
        return self.repository.map_to_response(instance)

    async def update(
        self,
        user_id: UUID,
        data: UserUpdateSchema,
    ) -> UserResponseSchema:
        """Apply a partial update, whitelisting the columns that may change."""
        instance = await self.repository.get_by_id(user_id)
        instance.update_from_dict(
            data.to_dict(),
            allowed_fields={"name", "email"},   # prevents mass-assignment
        )
        instance = await self.repository.update(instance)
        return self.repository.map_to_response(instance)

    async def soft_delete(self, user_id: UUID) -> None:
        """Flip ``is_active=False`` instead of hard-deleting."""
        await self.repository.soft_delete(user_id)
```

The methods you do **not** write — `get_by_id(user_id)`, `get_or_none(filters)`, `list(filters=None, order_by=None, ascending=True)`, `paginate(filters=None, order_by=None, page=1, page_size=20, ascending=True)`, `count(filters)`, `exists(filters)`, `delete(user_id)` — already exist on the base, already await an async `map_to_response`, and already return the typed `UserResponseSchema` declared in the generic parameter.

When the use case needs a custom pipeline (joins, projections, transactional fan-out), override the inherited method. The signature stays the same so the controller doesn't notice:

```python
class UserService(BaseService[UserRepository, UserResponseSchema]):
    # ... __init__ and overrides above ...

    async def list(  # override the inherited pass-through
        self,
        filters: dict[str, Any] | None = None,
        order_by: Any | None = None,
        ascending: bool = True,
    ) -> list[UserResponseSchema]:
        """List active users only — domain rule baked into the base method."""
        merged: dict[str, Any] = {**(filters or {}), "is_active": True}
        return await super().list(filters=merged, order_by=order_by, ascending=ascending)
```

### 8. Controller

Even when there's no orchestration to do, `controllers/` exists as a **thin pass-through** so the import graph stays uniform across services. The day a use case needs to coordinate two services (or fan out to a queue), the controller is already there.

Inherit from `BaseController[ServiceT, ResponseT]`. The base forwards `get_by_id`, `list`, `paginate`, `count`, `update` and `delete` to the service for you — you only declare methods that add cross-service coordination or that don't exist on the service (custom use cases like `signup`):

```python
# src/controllers/user.py
from uuid import UUID

from tempest_fastapi_sdk import BaseController

from src.schemas import UserCreateSchema, UserResponseSchema, UserUpdateSchema
from src.services.user import UserService


class UserController(BaseController[UserService, UserResponseSchema]):
    """Orchestrate user use cases.

    Today every method is a thin pass-through to ``UserService``. As
    soon as a use case needs to coordinate more than one service —
    e.g. signup also sends a welcome email and enqueues a CRM sync —
    the orchestration lives here, not in the router and not in the
    service.
    """

    # ──────── new methods for use cases the base doesn't cover ────────

    async def signup(self, data: UserCreateSchema) -> UserResponseSchema:
        """Create a user and (eventually) trigger downstream side effects."""
        return await self.service.signup(data)

    async def update(
        self,
        user_id: UUID,
        data: UserUpdateSchema,
    ) -> UserResponseSchema:
        """Domain-specific partial update — distinct from the base ``delete``."""
        return await self.service.update(user_id, data)

    async def soft_delete(self, user_id: UUID) -> None:
        """Soft-delete instead of the inherited hard ``delete``."""
        await self.service.soft_delete(user_id)
```

`get_by_id` / `list` / `paginate` / `count` are not redeclared — `BaseController` already exposes them. When the cross-service coordination day arrives, override the pass-through in place:

```python
class UserController(BaseController[UserService, UserResponseSchema]):
    # ... methods above ...

    async def signup(self, data: UserCreateSchema) -> UserResponseSchema:
        """Create the user, send a welcome email, enqueue the CRM sync."""
        user = await self.service.signup(data)
        await self.emails.send_welcome(user)            # second dependency
        await self.tasks.enqueue("crm.user.created", {"id": str(user.id)})
        return user
```

The router signature never changes — only the controller's body grows.

### 9. Dependency providers

`api/dependencies/` is **always a package**. `auth.py` hosts the shared-secret / current-user dependencies; `controllers.py` (or `services.py` when there is no controller layer yet) hosts the factory providers the routers depend on. Never construct controllers or services inline inside the router file.

```python
# src/api/dependencies/controllers.py
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import PasswordUtils

from src.api.app import db
from src.controllers.user import UserController
from src.db.repositories import UserRepository
from src.services.user import UserService


# Stateless utilities — instantiate once per process.
_passwords: PasswordUtils = PasswordUtils()


def get_user_controller(
    session: AsyncSession = Depends(db.session_dependency),
) -> UserController:
    """Wire repository → service → controller for a single request."""
    repository = UserRepository(session)
    service = UserService(repository=repository, passwords=_passwords)
    return UserController(service=service)
```

```python
# src/api/dependencies/__init__.py
from src.api.dependencies.controllers import get_user_controller

__all__: list[str] = ["get_user_controller"]
```

### 10. Router

Routers receive controllers via FastAPI `Depends` — no inline construction, no business logic, no DB calls. Business endpoints sit under `/api/<domain>` (the prefix is added at the include site in `src/api/app.py`); meta endpoints (`/health`, `/tool-spec`) stay at the root prefix.

```python
# src/api/routers/users.py
from uuid import UUID

from fastapi import APIRouter, Depends, status

from tempest_fastapi_sdk import BasePaginationSchema

from src.api.dependencies import get_user_controller
from src.controllers.user import UserController
from src.schemas import (
    UserCreateSchema,
    UserFilterSchema,
    UserResponseSchema,
    UserUpdateSchema,
)


router = APIRouter(prefix="/users", tags=["users"])


@router.post(
    "",
    response_model=UserResponseSchema,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    data: UserCreateSchema,
    controller: UserController = Depends(get_user_controller),
) -> UserResponseSchema:
    return await controller.signup(data)


@router.get("/{user_id}", response_model=UserResponseSchema)
async def get_user(
    user_id: UUID,
    controller: UserController = Depends(get_user_controller),
) -> UserResponseSchema:
    return await controller.get_by_id(user_id)


@router.patch("/{user_id}", response_model=UserResponseSchema)
async def update_user(
    user_id: UUID,
    data: UserUpdateSchema,
    controller: UserController = Depends(get_user_controller),
) -> UserResponseSchema:
    return await controller.update(user_id, data)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    controller: UserController = Depends(get_user_controller),
) -> None:
    await controller.soft_delete(user_id)


@router.get("", response_model=BasePaginationSchema[UserResponseSchema])
async def list_users(
    filters: UserFilterSchema = Depends(),
    controller: UserController = Depends(get_user_controller),
) -> BasePaginationSchema[UserResponseSchema]:
    result = await controller.paginate(
        filters=filters.get_conditions(),
        order_by=filters.order_by,
        page=filters.page,
        page_size=filters.page_size,
        ascending=filters.ascending,
    )
    return BasePaginationSchema[UserResponseSchema](**result)
```

### 11. Pagination

The pagination contract is enforced end-to-end by SDK primitives:

- `UserFilterSchema(BasePaginationFilterSchema)` parses `?page=&page_size=&order_by=&ascending=&is_active=&name=` from the query string and exposes `.get_conditions()` returning only the domain-level filters (without pagination keys).
- `UserRepository.paginate(...)` runs the query with the filter dict + ordering + offset/limit + count, returning the dict `{items, total, page, page_size, pages}` that you wrap in `BasePaginationSchema[UserResponseSchema]`.
- `BasePaginationSchema[UserResponseSchema]` wraps the result so OpenAPI documents the response shape correctly.

```http
GET /api/users?page=2&page_size=20&order_by=name&ascending=true&is_active=true&name=ana
```

Returns:

```json
{
    "items": [
        {"id": "...", "name": "Ana ...", "email": "...", ...},
        ...
    ],
    "total": 142,
    "page": 2,
    "page_size": 20,
    "pages": 8
}
```

---

## Recipes

### Authentication recipe

End-to-end signup + login + protected route using `PasswordUtils` and `JWTUtils`. Requires the `[auth]` extra.

#### Wire the utility singletons

```python
# src/core/security.py
from datetime import timedelta

from tempest_fastapi_sdk import JWTUtils, PasswordUtils

from src.core.settings import settings


passwords = PasswordUtils(rounds=12)

tokens = JWTUtils(
    secret=settings.JWT_SECRET,
    algorithm=settings.JWT_ALGORITHM,
    default_ttl=timedelta(hours=settings.JWT_TTL_HOURS),
    issuer="my-app",
)
```

#### Signup

Reuse the `UserService.signup` defined in the tutorial — it already hashes the password.

#### Login

```python
# src/schemas/auth.py
from pydantic import EmailStr

from tempest_fastapi_sdk import BaseSchema


class LoginSchema(BaseSchema):
    email: EmailStr
    password: str


class TokenResponseSchema(BaseSchema):
    access_token: str
    token_type: str = "bearer"
```

```python
# src/services/auth.py
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import JWTUtils, PasswordUtils, UnauthorizedException

from src.db.repositories import UserRepository
from src.schemas.auth import LoginSchema, TokenResponseSchema


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        passwords: PasswordUtils,
        tokens: JWTUtils,
    ) -> None:
        self.repo = UserRepository(session)
        self.passwords = passwords
        self.tokens = tokens

    async def login(self, data: LoginSchema) -> TokenResponseSchema:
        user = await self.repo.get_or_none({"email": data.email})
        if user is None or not self.passwords.verify(
            data.password, user.password_hash
        ):
            # Same error for both cases — don't leak which one failed.
            raise UnauthorizedException(message="E-mail ou senha inválidos")
        token = self.tokens.encode({"sub": str(user.id)})
        return TokenResponseSchema(access_token=token)
```

```python
# src/api/routers/auth.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.app import db
from src.core.security import passwords, tokens
from src.schemas.auth import LoginSchema, TokenResponseSchema
from src.services.auth import AuthService


router = APIRouter(prefix="/auth", tags=["auth"])


def get_auth_service(
    session: AsyncSession = Depends(db.session_dependency),
) -> AuthService:
    return AuthService(session, passwords, tokens)


@router.post("/login", response_model=TokenResponseSchema)
async def login(
    data: LoginSchema,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponseSchema:
    return await service.login(data)
```

#### Protect a route — JWT dependency

Use `make_jwt_user_dependency` to wire the bearer scheme + JWT decode + user load in one call. The single seam is `user_loader(subject)`, an async callable that maps the JWT subject claim to your domain `UserModel`.

```python
# src/api/dependencies/auth.py
from uuid import UUID

from tempest_fastapi_sdk import make_jwt_user_dependency

from src.api.app import db
from src.core.security import tokens
from src.db.models import UserModel
from src.db.repositories import UserRepository


async def load_user(subject: str) -> UserModel:
    """Resolve the JWT subject (a UUID string) to a persisted user.

    Opens its own session so the dependency stays request-scope-agnostic
    (the loader is called once per request, and SDK exceptions raised
    inside translate to the canonical 401/404 envelope).
    """
    async with db.get_session_context() as session:
        repo = UserRepository(session)
        return await repo.get_by_id(UUID(subject))


get_current_user = make_jwt_user_dependency(tokens, load_user)
get_current_user_or_none = make_jwt_user_dependency(tokens, load_user, soft=True)
```

```python
# Use in any route
@router.get("/me", response_model=UserResponseSchema)
async def me(current: UserModel = Depends(get_current_user)) -> UserResponseSchema:
    return UserResponseSchema.model_validate(current)
```

> **Using the bundled flow?** If you wire auth with `UserAuthService`
> (built with `db=`), skip the hand-written `load_user` and the extra
> `JWTUtils` — the service builds the dependency for you and verifies
> the token with the same secret it signs with:
>
> ```python
> get_current_user = auth_service.current_user_dependency()
> get_current_user_or_none = auth_service.current_user_dependency(soft=True)
> ```
>
> The authenticated user is loaded on the **request-scoped session**
> (`db.session_dependency`), so it is attached to the same session your
> repositories use — you can mutate it and `commit`/`refresh` without an
> `InvalidRequestError: Instance is not persistent within this Session`.
> Keep one shared session callable (`get_session = db.session_dependency`)
> for FastAPI to deduplicate them; if your repositories depend on a
> different `get_session` wrapper, pass it as
> `current_user_dependency(session_dependency=get_session)`.

#### Soft auth (optional user)

`get_current_user_or_none` above already uses `soft=True` — it returns `None` instead of raising on a missing or invalid token, so endpoints can work both authenticated and anonymous:

```python
@router.get("/feed")
async def feed(
    current: UserModel | None = Depends(get_current_user_or_none),
) -> FeedResponseSchema:
    return await feed_service.list(viewer=current)
```

Under the hood `soft=True` calls `tokens.decode_or_none` (no exception on expired/invalid tokens) and skips the loader when the subject is missing.

---

### File uploads recipe

Avatar endpoint with validation + cleanup. Requires the `[upload]` extra.

```python
# src/core/storage.py
from tempest_fastapi_sdk import UploadUtils

from src.core.settings import settings


avatar_storage = UploadUtils(
    f"{settings.UPLOAD_DIR}/avatars",          # local folder, or pass an AsyncMinIOClient
    max_size_bytes=5 * 1024 * 1024,            # 5 MiB
    allowed_extensions={"png", "jpg", "jpeg", "webp"},
    allowed_mimetypes={"image/png", "image/jpeg", "image/webp"},
    verify_magic_bytes=True,                   # sniff bytes, reject polyglots
)
```

`verify_magic_bytes=True` reads the first bytes of each upload and confirms the file *really is* one of the allowed types — an HTML+JS payload sent as `image/png` is rejected even though its extension and `Content-Type` header look valid. Only enable it when every accepted format is one `sniff_mime` recognizes (JPEG, PNG, GIF, BMP, WebP, PDF); otherwise a legitimate but unsniffable upload would be refused. For finer control, pass a `content_validator` predicate to `save()` (`save(file, content_validator=lambda b: sniff_mime(b) in {"image/png"})`), and pass `filename="..."` for a deterministic, addressable name (e.g. `f"{user_id}.jpg"`) instead of the default UUID.

```python
# src/api/routers/users.py (extension)
from fastapi import UploadFile

from src.api.dependencies import get_user_controller
from src.controllers.user import UserController
from src.core.storage import avatar_storage


@router.post("/{user_id}/avatar", response_model=UserResponseSchema)
async def upload_avatar(
    user_id: UUID,
    file: UploadFile,
    current: UserModel = Depends(get_current_user),
    controller: UserController = Depends(get_user_controller),
) -> UserResponseSchema:
    if current.id != user_id:
        raise ForbiddenException(message="Só pode editar o próprio avatar")
    path = await avatar_storage.save(file, subdir=str(user_id))
    return await controller.set_avatar(user_id, str(path))
```

Add `set_avatar` to both the service and the controller (the controller stays a thin pass-through unless orchestration is needed — e.g. firing an "avatar updated" event):

```python
# src/services/user.py
class UserService:
    async def set_avatar(self, user_id: UUID, path: str) -> UserResponseSchema:
        user = await self.repo.get_by_id(user_id)
        # Delete previous file when replacing (delete() is async).
        if user.avatar_path and user.avatar_path != path:
            await avatar_storage.delete(user.avatar_path)
        user.avatar_path = path
        user = await self.repo.update(user)
        return self.repo.map_to_response(user)


# src/controllers/user.py
class UserController:
    async def set_avatar(self, user_id: UUID, path: str) -> UserResponseSchema:
        return await self.service.set_avatar(user_id, path)
```

`UploadUtils.save()` raises `FileTooLargeException` (413) or `InvalidFileTypeException` (415) on rejection — the SDK's exception handler already returns the right status code with a `code` field on the response.

#### Serving the file back

Local-disk uploads are best served by an upstream (nginx / Caddy) so FastAPI doesn't stream bytes. For dev:

```python
from fastapi.staticfiles import StaticFiles

app.mount(
    "/static/uploads",
    StaticFiles(directory=settings.UPLOAD_DIR),
    name="uploads",
)
```

Construct the public URL in the response schema:

```python
class UserResponseSchema(BaseResponseSchema):
    name: str
    email: EmailStr
    avatar_url: str | None = None

    @field_validator("avatar_url", mode="before")
    @classmethod
    def _absolute_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # avatar_path stored as relative path → public URL
        return f"/static/uploads/{value}"
```

#### Serving private files through the API (`DownloadUtils`)

When a file must stay **behind auth** — invoices, contracts, medical scans — a public `/static` URL leaks it to anyone who learns the path. `DownloadUtils` streams the bytes through the endpoint itself, so the same `Depends(get_current_user)` / permission checks that guard every other route guard the download too. No public link is ever exposed. It needs **no extra** (uses Starlette's `FileResponse` / `StreamingResponse`, which ship with FastAPI).

```python
# src/core/storage.py
from tempest_fastapi_sdk import DownloadUtils

from src.core.settings import settings


invoice_files = DownloadUtils(f"{settings.UPLOAD_DIR}/invoices")  # local folder, or an AsyncMinIOClient
```

```python
# src/api/routers/invoices.py
from fastapi.responses import FileResponse

from src.api.dependencies import get_invoice_controller
from src.controllers.invoice import InvoiceController
from src.core.storage import invoice_files


@router.get("/{invoice_id}/file")
async def download_invoice(
    invoice_id: UUID,
    current: UserModel = Depends(get_current_user),
    controller: InvoiceController = Depends(get_invoice_controller),
) -> FileResponse:
    invoice = await controller.get_by_id(invoice_id)
    if invoice.owner_id != current.id:
        raise ForbiddenException(message="Fatura de outro usuário")
    # base_dir confines the read — a stored "../../etc/passwd" path 404s.
    return invoice_files.file_response(
        invoice.file_path,                 # relative to base_dir
        filename=f"fatura-{invoice.number}.pdf",
        as_attachment=True,                # force a download dialog
    )
```

Any relative path that escapes `base_dir` (`../` traversal, absolute paths, symlink escapes) raises `NotFoundException` (404) instead of leaking the file — the same 404 you get for a genuinely missing file, so callers never distinguish "forbidden" from "absent". `file_response` guesses the MIME type from the filename (override with `media_type=`), and `as_attachment=False` serves **inline** (e.g. preview a PDF in-browser).

For payloads built on the fly — a generated report, an in-memory zip, decrypted bytes — use `stream()` instead of touching disk:

```python
import io

from fastapi.responses import StreamingResponse

from src.core.storage import invoice_files


@router.get("/{invoice_id}/receipt.csv")
async def download_receipt(
    invoice_id: UUID,
    current: UserModel = Depends(get_current_user),
    controller: InvoiceController = Depends(get_invoice_controller),
) -> StreamingResponse:
    csv_bytes: bytes = await controller.render_receipt_csv(invoice_id, current.id)
    return invoice_files.stream(
        csv_bytes,                         # bytes, or a (sync/async) byte generator
        filename="recibo.csv",
    )
```

`stream()` accepts raw `bytes`, a sync `Iterable[bytes]`, or an `AsyncIterable[bytes]`, so a large export can be yielded chunk-by-chunk without buffering it all in memory. Both methods set a UTF-8-safe `Content-Disposition` (non-ASCII filenames survive via the RFC 5987 `filename*` parameter); `build_content_disposition()` is exported if you need to set that header on a hand-rolled response.

---

### Transactional email recipe

Password reset flow using `EmailUtils` + a short-lived JWT. Requires the `[email]` extra.

```python
# src/core/mailer.py
from tempest_fastapi_sdk import EmailUtils

from src.core.settings import settings


mailer = EmailUtils(
    host=settings.SMTP_HOST,
    port=settings.SMTP_PORT,
    from_addr=settings.SMTP_FROM_ADDR,
    username=settings.SMTP_USERNAME,
    password=settings.SMTP_PASSWORD,
    use_starttls=True,
)
```

```python
# src/services/password_reset.py
from datetime import timedelta

from tempest_fastapi_sdk import EmailUtils, JWTUtils, NotFoundException

from src.db.repositories import UserRepository


class PasswordResetService:
    def __init__(
        self,
        repo: UserRepository,
        tokens: JWTUtils,
        mailer: EmailUtils,
    ) -> None:
        self.repo = repo
        self.tokens = tokens
        self.mailer = mailer

    async def request_reset(self, email: str) -> None:
        """Send a password-reset link to `email`.

        Always returns silently — don't reveal whether the email
        is registered or not (avoids account enumeration).
        """
        user = await self.repo.get_or_none({"email": email})
        if user is None:
            return
        token = self.tokens.encode(
            {"sub": str(user.id), "purpose": "password_reset"},
            ttl=timedelta(minutes=15),
        )
        reset_url = f"https://my-app.com/reset-password?token={token}"
        await self.mailer.send(
            to=user.email,
            subject="Reset your password",
            body=f"Click here to reset your password: {reset_url}",
            html=f'<p>Click <a href="{reset_url}">here</a> to reset.</p>',
        )

    async def consume_reset(
        self,
        token: str,
        new_password: str,
        passwords: PasswordUtils,
    ) -> None:
        # `decode` raises InvalidTokenException / ExpiredTokenException
        # (both 401). Caught by the SDK handler.
        payload = self.tokens.decode(token)
        if payload.get("purpose") != "password_reset":
            raise InvalidTokenException()
        user = await self.repo.get_by_id(UUID(payload["sub"]))
        user.password_hash = passwords.hash(new_password)
        await self.repo.update(user)
```

---

### Alembic migrations recipe

Full workflow: bootstrap → first migration → apply → CI gate.

#### Bootstrap once per project

```python
# scripts/alembic_init.py
from tempest_fastapi_sdk import AlembicHelper

from src.core.settings import settings

helper = AlembicHelper(config_path="alembic.ini", db_url=settings.DATABASE_URL)
helper.init(
    directory="alembic",
    metadata_module="src.db.models",        # exposes BaseModel
    metadata_attr="BaseModel",
    db_url=settings.DATABASE_URL,
)
```

Run once: `uv run python scripts/alembic_init.py`.

This creates:

```text
alembic.ini                 # SDK-curated config (UTC timezone, date-prefixed file template)
alembic/
├── env.py                  # SDK template (already wires target_metadata, compare_type, batch mode)
├── script.py.mako
└── versions/
```

#### Author migrations

```python
# scripts/make_migration.py
import sys

from tempest_fastapi_sdk import AlembicHelper

from src.core.settings import settings

helper = AlembicHelper("alembic.ini", db_url=settings.DATABASE_URL)
helper.revision(
    message=sys.argv[1],
    autogenerate=True,
)
```

```bash
uv run python scripts/make_migration.py "add users table"
```

Generated file lands at `alembic/versions/2026_05_16_1432-ae12cd34_add_users_table.py` — the date prefix means files sort chronologically and merge conflicts are obvious.

#### Apply on startup

```python
# src/api/app.py — extend lifespan
import asyncio

from tempest_fastapi_sdk import AlembicHelper


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Run pending migrations before serving traffic.
    helper = AlembicHelper("alembic.ini", db_url=settings.DATABASE_URL)
    await asyncio.to_thread(helper.upgrade)

    await db.connect()
    yield
    await db.disconnect()
```

#### CI gate — schema must match models

```python
# scripts/check_migrations.py
import sys

from tempest_fastapi_sdk import AlembicHelper

from src.core.settings import settings

helper = AlembicHelper("alembic.ini", db_url=settings.DATABASE_URL)
if not helper.check():
    print("Schema drift detected — run make_migration.py and commit.")
    sys.exit(1)
print("Schema is in sync.")
```

```yaml
# .github/workflows/ci.yml
- name: Check migrations are in sync
  run: uv run python scripts/check_migrations.py
```

---

### Utility helpers recipe

Small stateless helpers from `tempest_fastapi_sdk.utils` that the SDK itself relies on and that show up across every service. Available without any extra.

| Helper | Signature | Purpose |
| --- | --- | --- |
| `utcnow()` | `() -> datetime` | Current time as a timezone-aware UTC datetime — the SDK uses this for `created_at` / `updated_at` defaults. |
| `to_utc(value)` | `(datetime) -> datetime` | Coerce naive datetimes to UTC (assumed UTC) and aware datetimes to UTC via `astimezone`. Used by `BaseResponseSchema` field validators. |
| `modify_dict(data, exclude=None, include=None)` | `(dict, list[str] \| None, dict \| None) -> dict` | Single-pass filter + merge. Drop sensitive keys before logging or merge computed fields when mapping payloads to ORM models. |

#### Timestamps the same way everywhere

`utcnow` is the canonical "now" for the SDK. Use it for soft-delete timestamps, JWT `iat` / `exp`, audit trails — anything where mixing naive and aware datetimes would burn you later.

```python
from datetime import timedelta

from tempest_fastapi_sdk import to_utc, utcnow


now = utcnow()                      # timezone-aware UTC
expires_at = now + timedelta(hours=1)

# Normalize whatever the caller gave you
incoming = request.json()["scheduled_for"]              # naive or aware
scheduled_for = to_utc(datetime.fromisoformat(incoming))
```

A naive datetime is tagged with UTC (not converted from local time) so it's predictable in headless workers and Docker containers where `time.timezone` is anyone's guess.

#### Drop sensitive keys before logging / mapping

`modify_dict` is the tiny utility that powers `BaseSchema.to_dict(exclude=..., include=...)` and `BaseModel.update_from_dict(...)`. Use it directly when you don't want to call into Pydantic round-trips:

```python
from tempest_fastapi_sdk import LogUtils, modify_dict

log = LogUtils("app.users")

payload = {"email": "ana@example.com", "password": "s3cr3t", "name": "Ana"}

# Strip password before logging
log.info("user_signup", **modify_dict(payload, exclude=["password"]))

# Merge a computed hash before persisting
user_row = modify_dict(
    payload,
    exclude=["password"],
    include={"password_hash": passwords.hash(payload["password"])},
)
```

`include` wins over `data`, so it doubles as a "set or override" helper without mutating the source dict.

#### Where every other helper is documented

Every helper has its own recipe — this section is the quick map:

| Helper | Recipe |
| --- | --- |
| `PasswordUtils`, `JWTUtils` | [Authentication recipe](#authentication-recipe) |
| `EmailUtils` | [Transactional email recipe](#transactional-email-recipe) |
| `UploadUtils` | [File uploads recipe](#file-uploads-recipe) |
| `DownloadUtils`, `build_content_disposition` | [Serving private files through the API](#serving-private-files-through-the-api-downloadutils) |
| `LogUtils` + `configure_logging` | [Structured logging & request IDs recipe](#structured-logging--request-ids-recipe) |
| `MetricsUtils` (CPU/memory/disk/GPU) | [System metrics recipe](#system-metrics-recipe) |
| `CPFField`, `CNPJField`, `CPFOrCNPJField`, `PhoneBRField`, `CEPField`, `is_valid_*`, `normalize_*`, `only_digits` | [BR document & phone validation recipe](#br-document--phone-validation-recipe) |

### BR document & phone validation recipe

`tempest_fastapi_sdk.utils.regex` ships ready-to-use regex patterns, validators, normalizers and Pydantic types for the identity/contact fields that show up in almost every Brazilian API. No extra required — pure stdlib + Pydantic (already a core dependency).

| Symbol | Kind | Purpose |
| --- | --- | --- |
| `CPF_PATTERN`, `CNPJ_PATTERN`, `CPF_CNPJ_PATTERN`, `PHONE_BR_PATTERN` | `re.Pattern[str]` | Compiled regex (masked or raw input). |
| `is_valid_cpf`, `is_valid_cnpj`, `is_valid_cpf_cnpj` | `(str) -> bool` | Format match **+** check-digit math. All-same-digit sequences rejected. |
| `is_valid_phone_br` | `(str) -> bool` | BR phone shape: optional `+55`, optional DDD, optional 9th digit. |
| `normalize_cpf`, `normalize_cnpj`, `normalize_cpf_cnpj`, `normalize_phone_br` | `(str) -> str` | Strip mask to digits-only; raise `ValueError` if invalid. |
| `only_digits` | `(str) -> str` | Strip every non-digit character. |
| `CPFField`, `CNPJField`, `CPFOrCNPJField`, `PhoneBRField`, `CEPField` | `Annotated[str, AfterValidator(...)]` | Drop-in Pydantic field types — validate + normalize automatically. The old names without the `Field` suffix (`CPF`, `CNPJ`, …) remain as deprecated aliases since v0.76. |

#### Schema usage

```python
from pydantic import EmailStr, Field

from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import CPFOrCNPJField, PhoneBRField


class CustomerCreateSchema(BaseSchema):
    """Payload for POST /customers.

    `document` accepts CPF or CNPJ in masked or raw form and is
    stored digits-only after validation. `phone` is normalized the
    same way. Invalid values surface as a Pydantic `ValidationError`
    (HTTP 422 via the SDK exception handler).
    """

    name: str = Field(min_length=1, max_length=128)
    email: EmailStr
    document: CPFOrCNPJField
    phone: PhoneBRField
```

Valid input:

```json
{
    "name": "Ana",
    "email": "ana@example.com",
    "document": "529.982.247-25",
    "phone": "+55 (11) 98888-7777"
}
```

After validation:

```python
CustomerCreateSchema(...).document  # "52998224725"
CustomerCreateSchema(...).phone     # "5511988887777"
```

#### Manual validation (services, controllers, queue handlers)

```python
from tempest_fastapi_sdk.utils import (
    is_valid_cpf_cnpj,
    normalize_cpf_cnpj,
    only_digits,
)

if not is_valid_cpf_cnpj(raw_document):
    raise ValidationException(message="Documento inválido")

document_digits = normalize_cpf_cnpj(raw_document)
```

#### Filtering by stored digits

The normalizers strip masks before saving, so repository filters and unique constraints all work on the canonical digits-only form:

```python
await repo.get({"document": normalize_cpf_cnpj(query)})
```

---

### Testing recipe

pytest + pytest-asyncio + in-memory SQLite + FastAPI TestClient.

#### Shared fixtures

```python
# tests/conftest.py
from collections.abc import AsyncGenerator

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AsyncDatabaseManager

from src.api.app import create_app
from src.db import BaseModel       # importing BaseModel ensures models are registered


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncDatabaseManager, None]:
    """Fresh in-memory DB per test."""
    manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await manager.connect()
    await manager.create_tables()
    try:
        yield manager
    finally:
        await manager.drop_tables()
        await manager.disconnect()


@pytest_asyncio.fixture
async def session(db: AsyncDatabaseManager) -> AsyncGenerator[AsyncSession, None]:
    """Managed session bound to the in-memory DB."""
    async with db.get_session_context() as session:
        yield session


@pytest_asyncio.fixture
async def client(db: AsyncDatabaseManager) -> AsyncGenerator[TestClient, None]:
    """FastAPI TestClient with the SDK manager overridden to use SQLite."""
    app = create_app()
    # Override the session dependency to use the test DB.
    from src.api.app import db as production_db

    app.dependency_overrides[production_db.session_dependency] = db.session_dependency

    async with TestClient(app) as client:
        yield client
```

#### Repository test

```python
# tests/repositories/test_user.py
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import UserNotFoundError
from src.db.models import UserModel
from src.db.repositories import UserRepository


class TestUserRepository:
    async def test_get_by_email_raises_when_missing(
        self, session: AsyncSession
    ) -> None:
        repo = UserRepository(session)
        with pytest.raises(UserNotFoundError):
            await repo.get({"email": "ghost@example.com"})

    async def test_add_and_get(self, session: AsyncSession) -> None:
        repo = UserRepository(session)
        user = await repo.add(
            UserModel(
                name="Ana", email="ana@example.com", password_hash="x"
            )
        )
        loaded = await repo.get_by_id(user.id)
        assert loaded.name == "Ana"
```

#### Endpoint test

```python
# tests/api/test_users.py
from fastapi.testclient import TestClient


class TestUsersAPI:
    def test_create_user(self, client: TestClient) -> None:
        response = client.post(
            "/api/users",
            json={
                "name": "Ana",
                "email": "ana@example.com",
                "password": "hunter22",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "ana@example.com"
        assert "password" not in body
        assert "password_hash" not in body

    def test_get_user_not_found(self, client: TestClient) -> None:
        response = client.get("/api/users/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404
        body = response.json()
        # SDK envelope is always {detail, code, details}
        assert body["code"] == "USER_NOT_FOUND"
```

#### `tempest_fastapi_sdk.testing` helpers

`tempest_fastapi_sdk.testing` ships framework-agnostic helpers that don't require `pytest` to be importable — wrap them in `@pytest.fixture` (or any other harness) inside the consuming project's `conftest.py`. Useful when a test doesn't need a full `AsyncDatabaseManager` (no `lifespan`, no health-check probes).

| Helper | Purpose |
| --- | --- |
| `create_test_engine(url="sqlite+aiosqlite:///:memory:", **engine_kwargs)` | Build a throw-away `AsyncEngine`. |
| `create_test_session_factory(engine)` | Build a `sessionmaker` bound to the engine. |
| `init_test_metadata(engine, metadata=None)` | Create every SQLAlchemy table on the engine (defaults to `BaseModel.metadata`). |
| `drop_test_metadata(engine, metadata=None)` | Drop every table. |
| `test_database(url="sqlite+aiosqlite:///:memory:", metadata=None)` | Async context manager — yields an engine with metadata pre-created, drops everything and disposes on exit. |
| `test_session(url="sqlite+aiosqlite:///:memory:", metadata=None)` | Async context manager — yields an `AsyncSession` on top of a fresh `test_database`. |

```python
# tests/conftest.py
from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tempest_fastapi_sdk.testing import test_database, test_session


@pytest_asyncio.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    """Yield a fresh in-memory SQLite engine for each test."""
    async with test_database() as e:
        yield e


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a managed AsyncSession bound to the in-memory engine."""
    async with test_session() as s:
        yield s
```

Use the one-shot `test_session()` context manager for ad-hoc tests that don't need cross-fixture sharing:

```python
from tempest_fastapi_sdk.testing import test_session

from src.db.models import UserModel
from src.db.repositories import UserRepository


async def test_repo_directly() -> None:
    async with test_session() as session:
        repo = UserRepository(session)
        await repo.add(UserModel(name="Ana", email="ana@example.com", password_hash="x"))
        assert await repo.count() == 1
```

Pass `metadata=` when the project mixes the SDK `BaseModel.metadata` with a second, isolated metadata (rare — keep one `BaseModel` per service whenever possible).

### Application bootstrap recipe

[Section 2 of the tutorial](#2-settings-server-app-factory--entry-point) shows the minimal `create_app()`. This recipe is the **extended** version, wiring everything `tempest_fastapi_sdk.api` ships — exception handlers, CORS, request-ID middleware, the health router with extra checks, a shared-secret token dependency and an extra Redis manager — all from the same canonical `src/api/app.py` location. The bootstrapping pattern stays identical; only the contents of `create_app()` grow.

```python
# src/api/app.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    RequestIDMiddleware,
    apply_cors,
    configure_logging,
    make_health_router,
    make_logs_router,
    make_token_dependency,
    register_exception_handlers,
)
from tempest_fastapi_sdk.cache import AsyncRedisManager

from src.core.settings import settings


configure_logging(
    level=settings.LOG_LEVEL,
    json_output=settings.LOG_JSON,
    log_dir=settings.LOG_DIR,
)

db = AsyncDatabaseManager(
    settings.DATABASE_URL,
    echo=settings.DATABASE_ECHO,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_recycle=settings.DATABASE_POOL_RECYCLE,
)
redis = AsyncRedisManager(settings.REDIS_URL)
require_token = make_token_dependency(settings.TOKEN_SECRET)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await db.connect()
    await redis.connect()
    try:
        yield
    finally:
        await redis.disconnect()
        await db.disconnect()


def create_app() -> FastAPI:
    """Build and configure the FastAPI app."""
    app = FastAPI(
        title="my-service",
        version=settings.VERSION,
        lifespan=lifespan,
    )

    app.add_middleware(RequestIDMiddleware)
    apply_cors(app, settings)
    register_exception_handlers(app)

    # Meta endpoints at the root prefix.
    app.include_router(
        make_health_router(
            db=db,
            checks={"redis": redis.health_check},
            version=settings.VERSION,
        ),
    )
    app.include_router(
        make_logs_router(
            log_dir=settings.LOG_DIR,
            token_secret=settings.TOKEN_SECRET,
        ),
    )

    # Business endpoints under /api/<domain>, guarded by the shared secret.
    from src.api.routers import users

    app.include_router(
        users.router,
        prefix="/api",
        dependencies=[Depends(require_token)],
    )
    return app


app = create_app()
```

Key points:

- `src/server.py` and `main.py` (one-liner) stay exactly as in [Section 2 of the tutorial](#2-settings-server-app-factory--entry-point) — only `create_app()` changes when you add primitives. Never start uvicorn via `subprocess.run(["uvicorn", ...])`; always import `app` from `src.api.app` or call `uvicorn.run("src.api.app:app", ...)` programmatically from `src/server.py`.
- `RequestIDMiddleware` reads/writes `X-Request-ID` and seeds `request_id_ctx` so every log line emitted during the request carries the correlation ID.
- `apply_cors(app, settings)` reads `CORSSettings` defaults; pass keyword overrides for one-off changes.
- `register_exception_handlers(app)` wires every `AppException` subclass to the canonical `{detail, code, details}` envelope.
- `make_health_router(db=db, checks={"redis": redis.health_check}, version=...)` mounts `GET /health/liveness` and `GET /health/readiness` (returns `503` when any check fails) at the root prefix.
- `make_token_dependency(secret)` returns an async dependency that validates `X-Token` via `hmac.compare_digest`; pass an empty string to disable in dev. The dependency lives next to the rest of the auth glue in `src/api/dependencies/auth.py` once it grows beyond the one-liner above.

### Structured logging & request IDs recipe

`configure_logging` installs a JSON handler on the root logger that emits one-line JSON records carrying the active request ID. `LogUtils` is a thin facade that adds level methods accepting structured `**fields`.

```python
from tempest_fastapi_sdk import LogUtils, configure_logging
from tempest_fastapi_sdk.core import get_request_id

# Imperative — call once during bootstrap.
configure_logging(level="INFO", json_output=True)

# Facade — handy for service-wide singletons.
log = LogUtils("app.users", level="INFO")
log.info("user_created", user_id=str(user.id), email=user.email)
log.warning("login_throttled", ip="1.2.3.4", attempts=5)

try:
    risky()
except RuntimeError:
    log.exception("risky_failed", op="reconcile")  # appends traceback

# Surface the correlation ID outside the log line if needed.
request_id = get_request_id()
```

JSON output (single line — formatted here for readability):

```json
{
  "timestamp": "2026-05-16T20:14:33.412+00:00Z",
  "level": "INFO",
  "logger": "app.users",
  "message": "user_created",
  "request_id": "d83e4b0c-7c2f-4bd6-aaa1-7d4f6cf5e5e9",
  "user_id": "9c1a5b2d-...",
  "email": "ana@example.com"
}
```

The middleware accepts a custom header name (`RequestIDMiddleware(app, header_name="X-Correlation-ID")`); the same header is echoed back on every response.

#### Per-level files + isolated `500.log`

Pass `log_dir` to `configure_logging` to keep stdout **and** write one JSON file per level under that directory. Each file receives only its own level (exact match — an `ERROR` never lands in `warning.log`), so every severity is an isolated, greppable stream. Uncaught 500s are additionally mirrored to a dedicated `500.log` (the catch-all handler flags them), so the gravest failures are never buried:

```python
from tempest_fastapi_sdk import configure_logging

# Keeps stdout AND writes logs/{debug,info,warning,error,critical,500}.log
configure_logging(level="INFO", json_output=True, log_dir="logs")
```

```text
logs/
├── debug.log      # only DEBUG records
├── info.log       # only INFO records
├── warning.log    # only WARNING records
├── error.log      # only ERROR records (a 500 lands here too)
├── critical.log   # only CRITICAL records
└── 500.log        # only uncaught-500 records (isolated)
```

The scaffold reads the directory from `LOG_DIR` (defaults to `"logs"`; set it empty to disable file logging). Add `logs/` to `.gitignore`.

#### Reading logs over HTTP — `make_logs_router`

`make_logs_router` mounts `GET /logs`, which parses the on-disk JSON files and returns a paginated `BasePaginationSchema[LogEntrySchema]` (newest first). It is gated by a shared-secret `X-Token` header — never expose it unauthenticated in production (the payload carries tracebacks and request metadata).

```python
from tempest_fastapi_sdk import make_logs_router

app.include_router(
    make_logs_router(log_dir="logs", token_secret=settings.TOKEN_SECRET),
)
```

```bash
# Latest 20 records across every level
curl -H "X-Token: $TOKEN_SECRET" "http://localhost:8000/logs"

# Only the isolated 500s, page 1, 50 per page
curl -H "X-Token: $TOKEN_SECRET" "http://localhost:8000/logs?source=500&page_size=50"

# Errors mentioning "timeout" in a time window
curl -H "X-Token: $TOKEN_SECRET" \
  "http://localhost:8000/logs?source=error&q=timeout&start=2026-05-31T00:00:00Z"
```

Query params: `source` (`all` | `debug` | `info` | `warning` | `error` | `critical` | `500`), `q` (case-insensitive message substring), `start` / `end` (ISO-8601 range), `page`, `page_size`.

### Settings mixins composition recipe

`BaseAppSettings` is the configured `pydantic-settings` base. The SDK also exposes composable mixins for the most common dependencies; pick the ones the service needs and put `BaseAppSettings` at the **end** of the MRO so its `model_config` wins.

```python
# src/core/settings.py
from pydantic import Field

from tempest_fastapi_sdk import (
    BaseAppSettings,
    CORSSettings,
    DatabaseSettings,
    EmailSettings,
    JWTSettings,
    LogSettings,
    RabbitMQSettings,
    RedisSettings,
    ServerSettings,
    TaskIQSettings,
    TokenSettings,
    UploadSettings,
    WebPushSettings,
)


class Settings(
    ServerSettings,
    LogSettings,
    DatabaseSettings,
    RedisSettings,
    RabbitMQSettings,
    TaskIQSettings,
    JWTSettings,
    CORSSettings,
    EmailSettings,
    UploadSettings,
    TokenSettings,
    WebPushSettings,
    BaseAppSettings,
):
    """Service-wide settings."""

    VERSION: str = Field(default="0.0.0")


settings = Settings()
```

Each mixin owns its own env-var prefix — pick only the ones the service needs:

| Mixin | Env vars |
| --- | --- |
| `ServerSettings` | `SERVER_HOST`, `SERVER_PORT`, `SERVER_RELOAD`, `SERVER_DEBUG` |
| `LogSettings` | `LOG_LEVEL`, `LOG_JSON`, `LOG_DIR` |
| `DatabaseSettings` | `DATABASE_URL`, `DATABASE_ECHO`, `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_POOL_RECYCLE` |
| `RedisSettings` | `REDIS_URL`, `REDIS_DECODE_RESPONSES` |
| `RabbitMQSettings` | `RABBITMQ_URL`, `RABBITMQ_PREFETCH_COUNT` |
| `TaskIQSettings` | `TASKIQ_BROKER_URL`, `TASKIQ_RESULT_BACKEND_URL` |
| `JWTSettings` | `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_ACCESS_TTL_SECONDS`, `JWT_REFRESH_TTL_SECONDS`, `JWT_ISSUER` |
| `CORSSettings` | `CORS_ORIGINS`, `CORS_ALLOW_CREDENTIALS`, `CORS_ALLOW_METHODS`, `CORS_ALLOW_HEADERS`, `CORS_EXPOSE_HEADERS`, `CORS_MAX_AGE` |
| `EmailSettings` | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_ADDR`, `SMTP_USE_TLS`, `SMTP_USE_SSL`, `SMTP_TIMEOUT_SECONDS` |
| `UploadSettings` | `UPLOAD_DIR`, `UPLOAD_MAX_SIZE_BYTES`, `UPLOAD_ALLOWED_EXTENSIONS`, `UPLOAD_ALLOWED_MIMETYPES` |
| `TokenSettings` | `TOKEN_SECRET` |
| `WebPushSettings` | `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`, `WEBPUSH_DEFAULT_TTL_SECONDS` |

> **Breaking change in 0.8.0:** `ServerSettings` previously exposed bare `HOST` / `PORT` / `DEBUG` / `LOG_LEVEL` / `LOG_JSON` fields. They were renamed to `SERVER_HOST` / `SERVER_PORT` / `SERVER_RELOAD` / `SERVER_DEBUG`, and `LOG_LEVEL` / `LOG_JSON` moved to the new `LogSettings` mixin. Update both your `.env` file (env var names) and any code reading `settings.HOST` etc.

### Controllers & services layering recipe

`BaseService[RepositoryT, ResponseT]` and `BaseController[ServiceT, ResponseT]` are generic skeletons matching the SDK layering (router → controller → service → repository). They expose pass-through CRUD methods so simple endpoints can subclass them without overriding anything; you override only methods that need orchestration.

`BaseService[RepositoryT, ResponseT]` accepts an optional third type argument — `BaseService[RepositoryT, ResponseT, UpdateT]` — that types the `update` payload (defaults to `BaseSchema`, so the two-argument form keeps working). What you inherit by subclassing it:

| Method | Returns | Notes |
| --- | --- | --- |
| `get_by_id(id)` | `ResponseT` | Awaits `repository.get_by_id` + `repository.map_to_response`. Raises `repository.not_found_exception` on miss. |
| `get_or_none(filters)` | `ResponseT \| None` | Same shape, returns `None` instead of raising. |
| `list(filters=None, order_by=None, ascending=True)` | `list[ResponseT]` | Returns `[]` on empty match (never raises). |
| `paginate(filters=None, order_by=None, page=1, page_size=20, ascending=True)` | `dict` with mapped `items` + `total`/`page`/`size`/`pages`. | Offset pagination via `repository.paginate`. |
| `count(filters=None)` | `int` | Pass-through to `repository.count`. |
| `exists(filters)` | `bool` | Pass-through to `repository.exists`. |
| `update(id, data)` | `ResponseT` | Fetch by id, copy the fields present in `data` (typed `UpdateT`, the optional 3rd generic param — defaults to `BaseSchema`) onto the row, persist, map. `to_dict()` drops unset/`None`, so it serves PUT and PATCH alike. |
| `delete(id)` | `None` | Hard delete via `repository.delete`. |

`map_to_response` is `await`-ed when it returns a coroutine, so async mappers work transparently — no method override needed.

What you inherit by subclassing `BaseController[ServiceT, ResponseT]`:

| Method | Forwards to | Notes |
| --- | --- | --- |
| `get_by_id(id)` | `service.get_by_id` | Same return type as the service. |
| `list(filters, order_by, ascending)` | `service.list` | Same. |
| `paginate(filters, order_by, page, page_size, ascending)` | `service.paginate` | Same. |
| `count(filters)` | `service.count` | Same. |
| `update(id, data)` | `service.update` | `data` typed by the optional 3rd generic param `UpdateT` (defaults to `BaseSchema`). |
| `delete(id)` | `service.delete` | Same. |

When a use case needs domain rules, override the inherited method in the service. When a use case needs to coordinate more than one service, override the inherited method (or add a new one) in the controller. The router never grows — it only depends on the controller.

```python
# src/services/user_service.py
from uuid import UUID

from tempest_fastapi_sdk import BaseService

from src.db.repositories import UserRepository
from src.schemas.user import UserCreate, UserResponse, UserUpdate
from src.utils.security import password_utils


class UserService(BaseService[UserRepository, UserResponse]):
    """Business logic for the user feature."""

    async def signup(self, data: UserCreate) -> UserResponse:
        # Business logic — hash the password, then delegate to the repo.
        instance = self.repository.map_to_model(
            {
                "name": data.name,
                "email": data.email,
                "password_hash": password_utils.hash(data.password),
            },
        )
        created = await self.repository.add(instance)
        return self.repository.map_to_response(created)


# src/controllers/user_controller.py
from tempest_fastapi_sdk import BaseController

from src.schemas.user import UserCreate, UserResponse
from src.services.user_service import UserService


class UserController(BaseController[UserService, UserResponse]):
    """Thin orchestration over UserService."""

    async def signup(self, data: UserCreate) -> UserResponse:
        # Pass-through today; the controller is the seam to add
        # cross-service coordination later (audit log, outbox event,
        # downstream notification, etc.) without touching the router.
        return await self.service.signup(data)


# src/api/dependencies/controllers.py
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.app import db
from src.controllers.user_controller import UserController
from src.db.repositories import UserRepository
from src.services.user_service import UserService


def get_user_controller(
    session: AsyncSession = Depends(db.session_dependency),
) -> UserController:
    return UserController(UserService(UserRepository(session)))


# src/api/routers/users.py
from fastapi import APIRouter, Depends, status

from src.api.dependencies.controllers import get_user_controller
from src.controllers.user_controller import UserController
from src.schemas.user import UserCreate, UserResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.post(
    "/",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    data: UserCreate,
    controller: UserController = Depends(get_user_controller),
) -> UserResponse:
    return await controller.signup(data)
```

Keep controllers present even when they only pass through — the import graph stays uniform across services, so adding cross-cutting policy later doesn't change the router signature.

### Audit & soft-delete mixins recipe

`SoftDeleteMixin` adds a `deleted_at` timestamp column with `mark_deleted()` / `mark_restored()` / `is_deleted` helpers. `AuditMixin` adds `created_by` / `updated_by` UUID columns with `stamp_created_by(user_id)` / `stamp_updated_by(user_id)` helpers. Mix them in alongside `BaseModel`:

```python
# src/db/models/user.py
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AuditMixin, BaseModel, SoftDeleteMixin


class UserModel(BaseModel, SoftDeleteMixin, AuditMixin):
    """Users — soft-deletable and audited."""

    name: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str] = mapped_column()
```

Filtering is the caller's responsibility — the mixin doesn't install a global filter. Hide soft-deleted rows from list endpoints by passing `deleted_at=None` (or filtering in your repository subclass). Stamping audit columns belongs to the service layer where the current user is in scope. Both patterns live inside the service:

```python
# src/services/user.py
from uuid import UUID

from tempest_fastapi_sdk import BaseService

from src.db.repositories import UserRepository
from src.schemas import UserResponse, UserUpdateSchema


class UserService(BaseService[UserRepository, UserResponse]):
    """Business logic for the user domain."""

    # ──────── soft-delete-aware read ────────

    async def list_alive(self) -> list[UserResponse]:
        """Return only rows where ``deleted_at IS NULL``."""
        instances = await self.repository.list(filters={"deleted_at": None})
        return [self.repository.map_to_response(i) for i in instances]

    # ──────── audit-stamped update ────────

    async def update(
        self,
        user_id: UUID,
        data: UserUpdateSchema,
        *,
        actor_id: UUID,
    ) -> UserResponse:
        """Apply a partial update and stamp ``updated_by`` with the actor."""
        instance = await self.repository.get_by_id(user_id)
        instance.update_from_dict(data.model_dump(exclude_unset=True))
        instance.stamp_updated_by(actor_id)
        updated = await self.repository.update(instance)
        return self.repository.map_to_response(updated)
```

The two highlighted methods under the divider comments are the only soft-delete- and audit-specific code the consumer writes — the columns and helpers (`mark_deleted` / `mark_restored` / `stamp_updated_by`) come from the mixins.

Use the mixin's helpers (`mark_deleted` / `mark_restored`) when you want the `deleted_at` semantics; use `BaseRepository.soft_delete(id)` when the existing `is_active` flag is enough.

### Cursor pagination recipe

Cursor pagination scales better than offset pagination on big tables (no `COUNT(*)`, stable under concurrent inserts) at the cost of losing random-access. The SDK provides `CursorPaginationFilterSchema`, `CursorPaginationSchema[T]` and the opaque `encode_cursor` / `decode_cursor` helpers.

```python
# src/schemas/user.py
from tempest_fastapi_sdk import CursorPaginationFilterSchema, CursorPaginationSchema

from src.schemas.user import UserResponse


class UserCursorFilter(CursorPaginationFilterSchema):
    name: str | None = None  # ILIKE %value% via repository convention


UserCursorPage = CursorPaginationSchema[UserResponse]
```

Repository helper (cursor over `created_at` + `id` tie-break):

```python
# src/db/repositories/user.py
from sqlalchemy import asc, desc

from tempest_fastapi_sdk import BaseRepository, decode_cursor, encode_cursor

from src.db.models.user import UserModel
from src.schemas.user import UserResponse


class UserRepository(BaseRepository[UserModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, model=UserModel)

    async def cursor_page(
        self,
        *,
        cursor: str | None,
        limit: int,
        ascending: bool,
        filters: dict[str, Any] | None = None,
    ) -> UserCursorPage:
        query = select(UserModel)
        if filters:
            query = self._apply_filters(query, filters)

        order = asc if ascending else desc
        query = query.order_by(order(UserModel.created_at), order(UserModel.id))

        if cursor is not None:
            state = decode_cursor(cursor)
            cmp = (UserModel.created_at, UserModel.id) > (state["value"], state["id"])
            query = query.where(cmp if ascending else ~cmp)

        query = query.limit(limit + 1)  # peek one ahead to set has_more
        result = await self.session.execute(query)
        rows = list(result.unique().scalars().all())
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = (
            encode_cursor(
                {"id": str(rows[-1].id), "value": rows[-1].created_at.isoformat()},
            )
            if has_more and rows
            else None
        )
        return UserCursorPage(
            items=[self.map_to_response(r) for r in rows],
            next_cursor=next_cursor,
            has_more=has_more,
            limit=limit,
        )
```

Router:

```python
@router.get("/", response_model=UserCursorPage)
async def list_users(
    f: UserCursorFilter = Depends(),
    controller: UserController = Depends(get_user_controller),
) -> UserCursorPage:
    return await controller.service.repository.cursor_page(
        cursor=f.cursor,
        limit=f.limit,
        ascending=f.ascending,
        filters=f.get_conditions(),
    )
```

The cursor is opaque base64-url-safe JSON — clients never inspect it; they pass back the value verbatim until `next_cursor` becomes `null`.

### Redis cache recipe

`AsyncRedisManager` wraps `redis.asyncio` with the same connect/disconnect/health-check surface as `AsyncDatabaseManager`. Install with `[cache]`.

```python
from tempest_fastapi_sdk.cache import AsyncRedisManager

cache = AsyncRedisManager(settings.REDIS_URL, decode_responses=True)

# Lifespan
await cache.connect()
...
await cache.disconnect()

# Direct use
await cache.client.set("user:123:name", "Ana", ex=300)
name = await cache.client.get("user:123:name")

# FastAPI dependency — yields the live client.
from fastapi import Depends
from redis.asyncio import Redis


@router.get("/cached")
async def cached_endpoint(
    redis: Redis = Depends(cache.client_dependency),
) -> dict[str, str]:
    value = await redis.get("greeting") or "hello"
    return {"value": value}
```

Wire the health check on the canonical router with `make_health_router(checks={"redis": cache.health_check})` so readiness probes fail when Redis is down.

### Server-Sent Events recipe

`EventStream` is an in-memory async queue feeding one SSE HTTP connection. `ServerSentEvent` encodes one frame; `sse_response` wraps the byte stream in a Starlette `StreamingResponse` with SSE-friendly headers.

```python
# src/api/routers/events.py
import asyncio

from fastapi import APIRouter

from tempest_fastapi_sdk import EventStream

router = APIRouter()


@router.get("/events")
async def events() -> "StreamingResponse":  # forward-declared by Starlette
    stream = EventStream(heartbeat_seconds=15.0)

    async def producer() -> None:
        for n in range(1, 4):
            await stream.publish({"n": n}, event="counter", id=str(n))
            await asyncio.sleep(1)
        await stream.close()

    task = asyncio.create_task(producer())
    # on_disconnect cancels the producer when the client drops — no leak.
    return stream.response(on_disconnect=task.cancel)
```

Browser side:

```javascript
const es = new EventSource("/events");
es.addEventListener("counter", (e) => console.log("got", JSON.parse(e.data)));
```

`heartbeat_seconds` emits a `: keepalive` SSE comment when idle so load-balancers don't close long-lived connections. `ServerSentEvent.data` accepts strings, bytes or any JSON-serializable Python object — non-strings are JSON-encoded automatically. Pass `retry=` to hint the browser at the reconnect delay (milliseconds).

The queue is **bounded** (`max_queue`, default `1000`): a slow client can't grow memory without limit. `overflow` picks the eviction policy — `"drop_oldest"` (default), `"drop_newest"`, or `"block"` (real backpressure); `EventStream.dropped_events` counts the discards. For broadcast, `SSEBroker.response(channel)` bundles `register` + `sse_response` + `unregister`-on-disconnect in one call. Authenticating an `EventSource` (which can't send an `Authorization` header): prefer a session cookie (`make_jwt_user_dependency(..., cookie_name="access_token")` + `withCredentials`); for cookieless clients pass a short-lived access token in the query string with `query_param="access_token"` (over TLS, scrubbed from logs). See the [SSE recipe](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/sse/) for the full guide.

### Web Push notifications recipe

`WebPushDispatcher` wraps the synchronous `pywebpush` library in `asyncio.to_thread` and surfaces the two errors the application cares about: `WebPushGoneError` (HTTP 404/410 — delete the subscription) and `WebPushError` (everything else). Install with `[webpush]`.

```python
# src/services/notifications.py
from tempest_fastapi_sdk import (
    WebPushDispatcher,
    WebPushGoneError,
    WebPushPayloadSchema,
    WebPushSubscriptionSchema,
)


dispatcher = WebPushDispatcher(
    settings.VAPID_PRIVATE_KEY,
    vapid_subject="mailto:ops@example.com",
    ttl_seconds=60,
)


async def notify_order_paid(
    subscription: WebPushSubscriptionSchema,
    order_id: str,
) -> None:
    payload = WebPushPayloadSchema(
        title="Pagamento confirmado",
        body=f"Pedido {order_id} aprovado.",
        icon="/static/icons/order.png",
        data={"orderId": order_id, "url": f"/orders/{order_id}"},
    )
    try:
        await dispatcher.send(subscription, payload)
    except WebPushGoneError:
        # Prune the subscription from your store.
        await subscriptions_repo.delete_by_endpoint(subscription.endpoint)


async def broadcast(subs: list[WebPushSubscriptionSchema], payload: WebPushPayloadSchema) -> None:
    gone = await dispatcher.send_many(subs, payload)
    if gone:
        await subscriptions_repo.delete_by_endpoints(gone)
```

`WebPushSubscriptionSchema` round-trips the exact JSON `PushSubscription.toJSON()` emits in the browser (it aliases `expiration_time` ↔ `expirationTime`), so you can store inbound subscriptions verbatim and replay them on dispatch.

### Message queues — FastStream recipe

`MessageBroker` is a typed, transport-agnostic facade over FastStream — you never import `faststream` in application code. Pick the transport with a constructor and address everything by a single **channel** string.

Install with `[queue]` (pulls `faststream[rabbit]`).

```python
# src/queue/__init__.py
from pydantic import BaseModel

from tempest_fastapi_sdk.queue import MessageBroker

from src.core.settings import settings


mq = MessageBroker.rabbitmq(settings.RABBITMQ_URL)  # or .redis / .kafka / .nats


class OrderPaid(BaseModel):
    order_id: str
    user_id: str


@mq.on("orders.paid")                       # consumer; type hint validates the message
async def handle_order_paid(event: OrderPaid) -> None:
    await mark_order_paid(event.order_id, event.user_id)


# src/api/app.py lifespan
await mq.connect()
...
await mq.disconnect()


# Publish from anywhere — channel first, message second
await mq.publish("orders.paid", OrderPaid(order_id="abc", user_id="x"))
```

`@mq.on(channel)` declares a consumer (the handler's Pydantic type hint validates each message); `publish(channel, message)` sends it. Lifecycle is `connect()` / `disconnect()` / `lifespan()` / `health_check()` / `is_connected`; the raw broker stays at `mq.broker`. Wire it on the health router with `make_health_router(checks={"queue": mq.health_check})`. See the [Queues and Tasks recipe](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/queue-tasks/) for the full guide.

### Background tasks — TaskIQ recipe

`TaskQueue` is a typed facade over TaskIQ (broker + scheduler in one object) — no `taskiq` import in application code. Install with `[tasks]` (pulls `taskiq` + `taskiq-aio-pika`).

```python
# src/tasks/__init__.py
from tempest_fastapi_sdk.tasks import TaskQueue

from src.core.settings import settings


tq = TaskQueue.rabbitmq(settings.TASKIQ_BROKER_URL)  # or .redis / .memory (tests)


@tq.task
async def send_welcome_email(to: str, name: str) -> None:
    await email_utils.send(to=to, subject="Welcome!", body=f"Hi, {name}.")


# src/api/app.py lifespan
await tq.connect()
...
await tq.disconnect()


# From a request handler: hand it to a worker and return immediately
await send_welcome_email.enqueue(to=user.email, name=user.name)
# In tests / reuse: run the body inline and get the real value back
await send_welcome_email.run(to="a@b.com", name="Ana")
```

`@tq.task` returns a typed `Task` with `enqueue()` (to a worker) and `run()` (inline, no broker). Periodic tasks live on the same object via `@tq.cron(...)` / `@tq.interval(...)`; `tq.broker` / `tq.scheduler` feed the standalone `taskiq worker` / `taskiq scheduler` CLIs. `TaskQueue.memory()` runs tasks synchronously in-process for tests.

### Periodic tasks scheduler recipe

`AsyncTaskScheduler` wraps `taskiq.TaskiqScheduler` + `LabelScheduleSource` so periodic tasks are declared with decorators alongside regular tasks and the scheduler is driven from the FastAPI lifespan. It **does not execute task bodies** — it kicks them into the same broker `AsyncTaskBrokerManager` wraps, so a worker process must be running to consume them. Requires the `[tasks]` extra.

```python
# src/tasks/__init__.py
from datetime import timedelta

from taskiq_aio_pika import AioPikaBroker

from tempest_fastapi_sdk.tasks import AsyncTaskBrokerManager, AsyncTaskScheduler

from src.core.settings import settings


# Use TASKIQ_BROKER_URL (from TaskIQSettings) when the scheduler /
# task broker is a different broker than the FastStream queue
# (RABBITMQ_URL). Reuse the same RabbitMQ URL when they share the
# broker — both env vars can point to the same value.
broker = AioPikaBroker(settings.TASKIQ_BROKER_URL)
tasks = AsyncTaskBrokerManager(broker)
scheduler = AsyncTaskScheduler(broker)


@tasks.task
async def reconcile_invoices(batch_size: int = 100) -> None:
    """Background task — kicked by handlers or the scheduler."""
    ...


@scheduler.cron("*/5 * * * *")          # every five minutes
async def heartbeat() -> None:
    """Liveness ping written to the audit log."""
    ...


@scheduler.cron("0 9 * * MON-FRI", cron_offset="-03:00")  # 09:00 BRT, weekdays
async def daily_digest() -> None:
    ...


@scheduler.interval(seconds=30)         # every 30s
async def poll_remote_queue() -> None:
    ...


@scheduler.interval(timedelta(minutes=15))
async def warm_cache() -> None:
    ...
```

Wire it into the app lifespan next to the broker manager:

```python
# src/api/app.py
@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await tasks.connect()
    await scheduler.connect()
    await scheduler.run_in_background()   # dev / single-process services
    try:
        yield
    finally:
        await scheduler.disconnect()
        await tasks.disconnect()
```

Decorator surface:

| Method | When to use |
| --- | --- |
| `@scheduler.cron("*/5 * * * *", cron_offset=None)` | Cron expression; pass `cron_offset` (string like `"-03:00"` or `timedelta`) to anchor to a timezone other than UTC. |
| `@scheduler.interval(seconds=30)` / `@scheduler.interval(timedelta(...))` | Fixed-interval recurrence. |
| `@scheduler.schedule([{...}, {...}])` | Raw TaskIQ schedule list — combine triggers, use one-shot `time`, etc. |
| `scheduler.register(func, schedule=[...], task_name=...)` | Register without decorator syntax (third-party callables). |

Production deployments with multiple workers should run the standalone scheduler CLI instead of `run_in_background()`, so only one scheduler is active across the cluster:

```bash
taskiq scheduler src.tasks:scheduler.scheduler
```

(`scheduler.scheduler` is the inner `TaskiqScheduler` instance exposed on `AsyncTaskScheduler`.) The worker process stays the same:

```bash
taskiq worker src.tasks:tasks.broker
```

Lifecycle controls mirror the broker manager: `connect()` / `disconnect()` / `lifespan()` / `run_in_background()` / `health_check()` / `is_connected`.

### System metrics recipe

`MetricsUtils` collects CPU, memory, disk and NVIDIA GPU usage via `psutil` + `pynvml`. Every method has a sync and an async variant (the async wrapper runs the same code via `asyncio.to_thread`). GPU sampling gracefully degrades to `[]` when `pynvml` or NVIDIA drivers are missing.

Install with `[metrics]`.

```python
from tempest_fastapi_sdk import MetricsUtils

# Synchronous, blocking call
snapshot = MetricsUtils.snapshot(disk_paths=["/", "/data"], cpu_interval=0.1)
print(snapshot.cpu.percent, snapshot.memory.percent)
for disk in snapshot.disks:
    print(disk.path, disk.percent)
for gpu in snapshot.gpus:
    print(gpu.name, gpu.utilization_percent, gpu.memory_used_bytes)

# Async — runs every collector concurrently via asyncio.gather
snapshot = await MetricsUtils.snapshot_async(disk_paths=["/"])


@router.get("/metrics")
async def metrics() -> dict[str, Any]:
    snap = await MetricsUtils.snapshot_async()
    return snap.to_dict()
```

Individual collectors are also available: `MetricsUtils.cpu(interval=...)`, `MetricsUtils.memory()`, `MetricsUtils.disk(path)`, `MetricsUtils.disks(paths)`, `MetricsUtils.gpus()` — and their `*_async` variants. Each returns a typed dataclass (`CPUMetrics`, `MemoryMetrics`, `DiskMetrics`, `GPUMetrics`, `SystemMetrics`) with a `to_dict()` helper for JSON serialization.

### Programmatic server entry point recipe

`run_server` is the canonical helper imported from `src/server.py`. It centralizes the `host` / `port` / `reload` defaults — pulling values from a `ServerSettings`-flavoured `settings` object when present — and keeps the entry point a single line.

```python
# src/server.py
from tempest_fastapi_sdk import run_server

from src.api.app import app  # noqa: F401 — re-exported for external runners
from src.core.settings import settings


def run() -> None:
    """Start the API server programmatically."""
    run_server("src.api.app:app", settings=settings)


__all__: list[str] = ["app", "run"]
```

```python
# main.py
from src.server import run

if __name__ == "__main__":
    run()
```

Resolution order for each kwarg is `explicit argument → settings.SERVER_* → SDK default` (`"127.0.0.1"` / `8000` / `False`). Extra uvicorn kwargs (`workers=`, `log_config=`, `ssl_*=`) are forwarded verbatim.

### JWT bearer / current-user / role dependencies recipe

Four dependency factories live in `tempest_fastapi_sdk.api.dependencies.auth` — pick the level of abstraction you need.

| Factory | What you get |
| --- | --- |
| `make_token_dependency(secret)` | Validate the `X-Token` shared-secret header (constant time). |
| `make_bearer_token_dependency(tokens, soft=False)` | Decode `Authorization: Bearer <jwt>` and return the claims dict. |
| `make_jwt_user_dependency(tokens, user_loader, soft=False, subject_claim="sub")` | Decode the bearer JWT, await `user_loader(subject)`, return the loaded user. |
| `make_role_dependency(tokens, ["admin"], require_all=False, roles_claim="roles")` / `make_permission_dependency(tokens, ["users:write"], require_all=True, permissions_claim="permissions")` | Decode the bearer JWT and gate the route on roles / permissions. |

```python
# src/api/dependencies/auth.py
from uuid import UUID

from tempest_fastapi_sdk import (
    JWTUtils,
    make_bearer_token_dependency,
    make_jwt_user_dependency,
    make_permission_dependency,
    make_role_dependency,
)

from src.api.app import db
from src.core.settings import settings
from src.db.models import UserModel
from src.db.repositories import UserRepository


tokens = JWTUtils(
    secret=settings.JWT_SECRET,
    algorithm=settings.JWT_ALGORITHM,
)


async def load_user(subject: str) -> UserModel:
    """Resolve the JWT subject (a UUID string) to a persisted user."""
    async with db.get_session_context() as session:
        repo = UserRepository(session)
        return await repo.get_by_id(UUID(subject))


require_bearer = make_bearer_token_dependency(tokens)
get_current_user = make_jwt_user_dependency(tokens, load_user)
get_current_user_or_none = make_jwt_user_dependency(tokens, load_user, soft=True)

require_admin = make_role_dependency(tokens, ["admin"])
require_users_write = make_permission_dependency(tokens, ["users:write"])
```

```python
# src/api/routers/users.py
from fastapi import APIRouter, Depends

from src.api.dependencies.auth import (
    get_current_user,
    require_admin,
    require_users_write,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
async def me(current: UserModel = Depends(get_current_user)) -> UserResponseSchema:
    return UserResponseSchema.model_validate(current)


@router.delete("/{user_id}", dependencies=[Depends(require_admin)])
async def delete_user(user_id: UUID) -> None:
    ...


@router.patch(
    "/{user_id}/permissions",
    dependencies=[Depends(require_users_write)],
)
async def update_perms(user_id: UUID) -> None:
    ...
```

`soft=True` returns `None` instead of raising on missing/invalid tokens — useful for endpoints that work both authenticated and anonymous. `subject_claim` defaults to `"sub"` but can be any custom claim (`"user_id"`, `"uid"`, ...). Role dependencies accept either a string or a list of strings on the JWT claim; `require_all=True` requires every listed role/permission, `False` (default for roles, override for permissions) requires any.

### CEP (Brazilian zipcode) recipe

`CEPField` is an `Annotated[str, AfterValidator(normalize_cep)]` type — drop it into a Pydantic schema and inbound values are accepted as `"01310-100"` or `"01310100"`, normalized to 8 digits, and rejected (`ValidationError` → HTTP 422 envelope) when they don't match the shape. CEPs have no check digits, so validation is format-only.

```python
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import CEPField


class AddressCreateSchema(BaseSchema):
    cep: CEPField
    street: str
    number: str
```

Imperative variants: `is_valid_cep(value)`, `normalize_cep(value)`, plus `CEP_PATTERN` for raw regex use. Use them inside services / queue handlers where you don't want a Pydantic round-trip.

### Cache decorator recipe

`@cached(redis, ttl=..., key_prefix=...)` memoizes the result of an async function in Redis. Cache keys are derived from the function's `__qualname__` plus a SHA-256 of args/kwargs; pass `key_prefix=` to namespace entries so invalidation works by prefix scan.

```python
from tempest_fastapi_sdk.cache import AsyncRedisManager, cached

from src.core.settings import settings


redis = AsyncRedisManager(settings.REDIS_URL)


@cached(redis, ttl=300, key_prefix="users:")
async def get_user_profile(user_id: str) -> dict[str, str]:
    """Hits Redis on warm cache; runs the body once every 5 minutes."""
    return await load_from_db(user_id)


# Selectively bypass the cache (read AND write) for some calls
@cached(
    redis,
    ttl=60,
    skip_cache=lambda args, kwargs: kwargs.get("fresh") is True,
)
async def list_orders(user_id: str, *, fresh: bool = False) -> list[dict]:
    ...
```

Defaults: `ttl=300` seconds (`0` disables expiry), `serializer=json.dumps` / `deserializer=json.loads`. Override `serializer` / `deserializer` for non-JSON payloads (Pydantic models — pass `model_dump_json` / `MyModel.model_validate_json`, or use `pickle.dumps` / `pickle.loads` for arbitrary objects). Corrupt cached values fall back to running the wrapped function and warn on the SDK logger.

### Tool-spec router recipe

`make_tool_spec_router(spec)` mounts a `GET /tool-spec` endpoint exposing a machine-readable manifest at the root prefix — meant to sit alongside `/health/liveness` so external callers can discover capabilities without parsing the full OpenAPI document.

```python
# src/api/app.py
from tempest_fastapi_sdk import (
    make_health_router,
    make_tool_spec_router,
)


def _tool_spec() -> dict[str, object]:
    """Computed per request — keeps version + counts in sync with state."""
    return {
        "service": "my-service",
        "version": settings.VERSION,
        "tools": [
            {"path": "/api/users", "method": "GET", "summary": "List users"},
            {"path": "/api/orders", "method": "POST", "summary": "Place order"},
        ],
    }


def create_app() -> FastAPI:
    app = FastAPI(...)
    app.include_router(make_health_router(db=db))
    app.include_router(make_tool_spec_router(_tool_spec))
    ...
    return app
```

Pass a dict (served verbatim), a sync callable (called every request) or an async callable (awaited). Override `path=` to expose the manifest at a different URL or `tag=` to group it under a different OpenAPI tag.

### Webhook signature verification recipe

`WebhookSignatureVerifier` validates HMAC-signed inbound webhooks (Stripe / GitHub style) and exposes a FastAPI dependency that reads the raw body, checks the signature with `hmac.compare_digest`, and yields the body bytes so the route handler can re-parse it without re-reading the stream.

```python
# src/api/dependencies/webhooks.py
from tempest_fastapi_sdk import WebhookSignatureVerifier

from src.core.settings import settings


github = WebhookSignatureVerifier(
    secret=settings.GITHUB_WEBHOOK_SECRET,
    algorithm="sha256",
    header_name="X-Hub-Signature-256",
    prefix="sha256=",
)
stripe = WebhookSignatureVerifier(
    secret=settings.STRIPE_WEBHOOK_SECRET,
    algorithm="sha256",
    header_name="Stripe-Signature",
    encoding="hex",
)
```

```python
# src/api/routers/webhooks.py
from fastapi import APIRouter, Depends

from src.api.dependencies.webhooks import github

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/github")
async def github_event(body: bytes = Depends(github.dependency())) -> None:
    payload = json.loads(body)
    ...
```

Supports `hex` (default) and `base64` encodings, any hashlib algorithm guaranteed across platforms, and an optional `prefix` (e.g. `"sha256="`) stripped before comparison. Use the imperative `verifier.verify(body, signature)` from queue handlers when validation happens outside the FastAPI pipeline.

For providers that sign with an RSA private key (Apple App Store, Google Play, custom enterprise services), swap `WebhookSignatureVerifier` for `RSAWebhookSignatureVerifier` — same `dependency()` / `verify()` surface, but it validates the signature against a PEM-encoded public key (`PKCS1v15` over SHA-256 by default; pass `hash_algorithm="sha512"` or `padding="pss"` to match the provider).

```python
from tempest_fastapi_sdk import RSAWebhookSignatureVerifier

apple = RSAWebhookSignatureVerifier(
    public_key_pem=settings.APPLE_PUBLIC_KEY_PEM,
    header_name="X-Apple-Signature",
    encoding="base64",
    hash_algorithm="sha256",
)
```

### Pagination Link headers recipe

`build_pagination_link_header` emits an RFC 8288 `Link` header with `first` / `prev` / `next` / `last` rels — pair it with (or use instead of) the `BasePaginationSchema` body wrapper for REST clients that expect GitHub-style headers. Existing query parameters on the base URL are preserved.

```python
from fastapi import Request, Response

from tempest_fastapi_sdk import (
    BasePaginationSchema,
    build_pagination_link_header,
)


@router.get("", response_model=list[UserResponseSchema])
async def list_users(
    request: Request,
    response: Response,
    filters: UserFilterSchema = Depends(),
    controller: UserController = Depends(get_user_controller),
) -> list[UserResponseSchema]:
    result = await controller.paginate(
        filters=filters.get_conditions(),
        order_by=filters.order_by,
        page=filters.page,
        page_size=filters.page_size,
        ascending=filters.ascending,
    )
    page = BasePaginationSchema[UserResponseSchema](**result)
    response.headers["Link"] = build_pagination_link_header(
        str(request.url),
        page=page.page,
        page_size=page.page_size,
        pages=page.pages,
    )
    response.headers["X-Total-Count"] = str(page.total)
    return page.items
```

Tweak `page_param=` / `size_param=` when your service uses non-standard query parameter names (e.g. `offset` / `limit`). Pass `extra_params={"sort": "name"}` to bake the current sort/filter state into every link.

### Rate limit middleware recipe

`RateLimitMiddleware` is a sliding-window limiter — each unique key (client IP by default) is allowed at most `max_requests` requests inside every `window_seconds` window. Exceeded requests get a `429 Too Many Requests` with a `Retry-After` header. Two axes are pluggable: the **store** (memory or Redis) and the **key** (IP, user, tenant, API key).

```python
# src/api/app.py
from tempest_fastapi_sdk import RateLimitMiddleware


def create_app() -> FastAPI:
    app = FastAPI(...)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=120,
        window_seconds=60.0,
        exempt_paths=("/health/liveness", "/health/readiness"),
    )
    ...
```

**Limit per principal.** By default the key is the client IP. The
`key_by_*` factories key on the authenticated user, an arbitrary token
claim or a header instead. Because the middleware runs *before* FastAPI
dependencies resolve, the `key_by_jwt_*` factories decode the bearer
from the raw request (`decode_or_none`) and fall back to the IP for
anonymous traffic:

| Factory | Key produced | Use |
| --- | --- | --- |
| `key_by_ip(trusted_header=...)` | `ip:<addr>` | Per IP (default). |
| `key_by_jwt_subject(jwt)` | `user:<sub>` | Per authenticated user. |
| `key_by_jwt_claim(jwt, "tenant_id", scope="tenant")` | `tenant:<id>` | Per token claim. |
| `key_by_header("x-api-key", scope="apikey")` | `apikey:<value>` | Per header value. |

**Share state across replicas.** The default `MemoryRateLimitStore`
counts in-process (single worker). Pass `RedisRateLimitStore(redis)` for
multi-replica deploys — an atomic Lua sliding-window log over a sorted
set, `fail_open=True` by default:

```python
# src/api/app.py
from redis.asyncio import Redis

from tempest_fastapi_sdk import (
    RateLimitMiddleware,
    RedisRateLimitStore,
    key_by_jwt_subject,
)

from src.api.dependencies.resources import get_jwt_utils


def create_app() -> FastAPI:
    redis: Redis = Redis.from_url("redis://localhost:6379/0")
    app = FastAPI(...)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=600,
        window_seconds=60.0,
        key_func=key_by_jwt_subject(get_jwt_utils()),        # ← per-user buckets
        store=RedisRateLimitStore(redis),                    # ← shared across replicas
        exempt_paths=("/health/liveness", "/health/readiness"),
    )
    return app
```

The sliding-window semantics are identical across both stores; only where the counters live changes. You can still push rate limiting to the edge (nginx / Cloudflare / AWS WAF) when you prefer.

### Outbox dispatcher pattern recipe

The transactional outbox pattern keeps a "to publish" table in the same database as the domain rows, so writing the row and recording the side-effect happen in a single transaction. A worker reads the outbox in order and publishes to RabbitMQ (FastStream) / TaskIQ, marking each row as dispatched only after the broker ACKs. Crashes between commit and publish replay safely on the next poll.

The SDK does **not** ship a dedicated `OutboxDispatcher` primitive — the implementation is short, opinionated, and benefits from staying in the service's `db/models/` + `tasks/` boundary. Use the recipe below.

```python
# src/db/models/outbox.py
from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class OutboxEventModel(BaseModel):
    """One row per domain event waiting to be published."""

    topic: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        index=True,
    )
    # is_active / created_at / updated_at come from BaseModel.
```

```python
# src/db/repositories/outbox.py
from sqlalchemy import select, update

from tempest_fastapi_sdk import BaseRepository

from src.db.models import OutboxEventModel


class OutboxRepository(BaseRepository[OutboxEventModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, model=OutboxEventModel)

    async def claim_pending(self, *, limit: int = 100) -> list[OutboxEventModel]:
        """Lock-free claim — fine for single-worker dispatcher."""
        stmt = (
            select(OutboxEventModel)
            .where(OutboxEventModel.status == "pending")
            .order_by(OutboxEventModel.created_at)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_dispatched(self, ids: list[str]) -> None:
        await self.session.execute(
            update(OutboxEventModel)
            .where(OutboxEventModel.id.in_(ids))
            .values(status="dispatched"),
        )
        await self.session.commit()
```

```python
# src/services/orders.py — produce side
from src.db.models import OrderModel, OutboxEventModel


class OrderService:
    async def place_order(self, data: OrderCreateSchema) -> OrderResponseSchema:
        order = OrderModel(**data.to_dict())
        self.repo.session.add(order)
        # Same transaction as the order row.
        self.repo.session.add(
            OutboxEventModel(
                topic="orders.placed",
                payload={"order_id": str(order.id), "amount": order.amount},
            ),
        )
        await self.repo.session.flush()
        await self.repo.session.commit()
        return self.repo.map_to_response(order)
```

```python
# src/tasks/__init__.py — dispatcher side
from tempest_fastapi_sdk.tasks import AsyncTaskScheduler

from src.api.app import broker as queue_broker  # FastStream AsyncBrokerManager
from src.api.app import db

scheduler = AsyncTaskScheduler(broker)


@scheduler.interval(seconds=5)
async def dispatch_outbox() -> None:
    """Poll the outbox and publish each pending event."""
    async with db.get_session_context() as session:
        repo = OutboxRepository(session)
        events = await repo.claim_pending(limit=100)
        if not events:
            return
        dispatched: list[str] = []
        for event in events:
            try:
                await queue_broker.publish(event.payload, event.topic)
                dispatched.append(str(event.id))
            except Exception:  # noqa: BLE001 — retry on next tick
                continue
        if dispatched:
            await repo.mark_dispatched(dispatched)
```

Trade-offs to keep in mind:

- **Order is best-effort.** When a batch contains one failing publish, every later event in the same batch still runs — but they're still published in `created_at` order. If strict ordering matters, break on the first failure.
- **Single dispatcher.** The naive `claim_pending` does not lock rows; running multiple dispatcher workers will double-publish. Use `SELECT ... FOR UPDATE SKIP LOCKED` on PostgreSQL when you need to scale out.
- **Retention.** Add a periodic `TRUNCATE`-style job to delete `dispatched` rows older than N days, otherwise the outbox table grows unbounded.
- **At-least-once.** Consumers must be idempotent — the dispatcher can crash after publishing but before `mark_dispatched`.

### Base enums recipe

`BaseStrEnum` / `BaseIntEnum` extend the stdlib `Enum` with helpers tuned for Pydantic + SQLAlchemy round-tripping (lookup by value, JSON-serializable `str` / `int` inheritance, `__contains__` that accepts raw values). Use them for every enum that crosses the API boundary.

```python
from tempest_fastapi_sdk import BaseIntEnum, BaseStrEnum


class OrderStatus(BaseStrEnum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"


class Priority(BaseIntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2

assert OrderStatus.PENDING == "pending"          # str inheritance
assert "paid" in OrderStatus                      # raw value membership
assert OrderStatus("paid") is OrderStatus.PAID    # canonical lookup
assert Priority.NORMAL + 1 == Priority.HIGH       # int math
```

Both bases add introspection + lookup helpers so you rarely reach for `enum` internals:

```python
OrderStatus.values()                       # ["pending", "paid", "shipped", "cancelled"]
OrderStatus.keys()                         # ["PENDING", "PAID", "SHIPPED", "CANCELLED"]
OrderStatus.to_dict()                      # {"PENDING": "pending", ...}
OrderStatus.choices()                      # [("pending", "PENDING"), ...] — for <select>/forms

OrderStatus.has_value("paid")              # True
OrderStatus.has_key("PAID")                # True

# from_value: build a member from a raw value OR member name (exact, then
# case-insensitive). Raises ValueError on no match, unless a default is given.
OrderStatus.from_value("paid")             # OrderStatus.PAID  (by value)
OrderStatus.from_value("PAID")             # OrderStatus.PAID  (by name)
OrderStatus.from_value("paid ")            # ValueError: 'paid ' is not a valid OrderStatus
OrderStatus.from_value("bogus", default=None)  # None — explicit fallback
```

Because they inherit from `str` / `int`, Pydantic serializes them transparently as their underlying value and SQLAlchemy can persist them via the standard `Enum` column without an extra converter.

### Runtime typing recipe

Type hints are erased at runtime — nothing stops a caller from passing the wrong type once the code ships. `strict_types` / `typed` validate arguments and return against the annotations on every call (built on `pydantic.validate_call`, already a dependency); `require_annotations` enforces at import time that a function *is* annotated. `Any` is always a valid annotation — these enforce that things ARE annotated, never that they avoid `Any`.

```python
from typing import Any

from tempest_fastapi_sdk import require_annotations, strict_types, typed


@strict_types
def add(a: int, b: int) -> int:
    return a + b

add(1, 2)            # 3
add("1", 2)          # pydantic.ValidationError — "1" is NOT coerced


@typed
def parse(a: int) -> int:
    return a

parse("1")           # 1 — coerced when Pydantic safely can


@require_annotations
def ok(value: Any) -> None:   # Any counts as a valid annotation
    return None

@require_annotations
def bad(a) -> int:            # TypeError at import: missing annotation for 'a'
    return a
```

Use the runtime decorators at trust boundaries (queue messages, external API payloads, CLI input) — not on hot internal paths, where they add redundant per-call overhead.

For the **static** side, ruff's `ANN` rule and mypy force annotations to exist with zero runtime cost. The `tempest` CLI gates read a `[tool.tempest] typing_strictness` knob (`lenient` / `standard` / `strict`, default `standard`) that layers ANN rules + mypy flags onto your config:

```toml
[tool.tempest]
typing_strictness = "standard"   # lenient | standard | strict
```

```bash
tempest check                    # uses the configured level
tempest check --strictness strict  # override for this run
```

`ANN401` (forbid `Any`) is never enabled at any level. Projects scaffolded by `tempest new` ship this configured. See the [Typing recipe](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/typing/) for the full guide.

### Hardened static files + cookie helpers recipe

`HardenedStaticFiles` extends Starlette's `StaticFiles` with three production-grade defaults: it resolves the served path against a symlink-free base, refuses any path that escapes that base (path-traversal defense in depth), and attaches a configurable set of security headers (`DEFAULT_STATIC_SECURITY_HEADERS` — `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-site`).

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import DEFAULT_STATIC_SECURITY_HEADERS, HardenedStaticFiles

app = FastAPI()
app.mount(
    "/static",
    HardenedStaticFiles(
        directory="public/",
        # Override or extend the defaults — merging is the caller's job.
        security_headers={
            **DEFAULT_STATIC_SECURITY_HEADERS,
            "Cache-Control": "public, max-age=86400, immutable",
        },
    ),
    name="static",
)
```

For cookie-based session flows, `set_cookie` / `clear_cookie` write headers that already include the safe combo (`HttpOnly`, `Secure`, `SameSite=Lax`) so the caller only picks the bits they want to deviate from. `SameSite` is a **type alias** `Literal["lax", "strict", "none"]` — pass the literal string, not an enum. The SDK does **not** auto-force `Secure` for `samesite="none"`: pass `samesite="none", secure=True` yourself in cross-site scenarios (the browser rejects `SameSite=None` without `Secure`).

```python
from fastapi import Response

from tempest_fastapi_sdk import clear_cookie, set_cookie


def login(response: Response, token: str) -> None:
    set_cookie(
        response,
        "session",                 # name (positional)
        token,                     # value (positional)
        max_age=3600,
        samesite="lax",            # "lax" (default), "strict" or "none"
        # secure=True,             # default — set False only for local HTTP
        # http_only=True,          # default
        path="/",
    )


def logout(response: Response) -> None:
    clear_cookie(response, "session", path="/")
```

### Brute-force throttling recipe

`AttemptThrottle` counts failed attempts per key (typically `<endpoint>:<identifier>` — login email, password-reset target, IP, etc.). The constructor takes a `backend` — any object matching the `ThrottleBackend` Protocol (`get`/`incr`/`expire`/`ttl`/`delete`), which `redis.asyncio.Redis` satisfies out of the box — plus `max_attempts` + `window_seconds`. No in-memory backend is bundled: pass the Redis client from `AsyncRedisManager` (or a [fakeredis](https://github.com/cunla/fakeredis-py) double in tests).

```python
from tempest_fastapi_sdk import AttemptThrottle, UnauthorizedException
from tempest_fastapi_sdk.cache import AsyncRedisManager

from src.core.settings import settings

cache = AsyncRedisManager(settings.REDIS_URL)
# cache.client is redis.asyncio.Redis — matches the ThrottleBackend Protocol
throttle = AttemptThrottle(
    cache.client,
    max_attempts=5,
    window_seconds=300,         # fixed window; also the TTL on the first failure
    namespace="login",          # key prefix so multiple throttles can coexist
    fail_open=True,             # a Redis outage degrades to "allow", never locks everyone out
)


async def login(email: str, password: str) -> User:
    key = f"login:{email}"
    await throttle.raise_if_blocked(key)            # raises 429 when over budget

    user = await users_repo.get_or_none({"email": email})
    if user is None or not password_utils.verify(password, user.hashed_password):
        await throttle.hit(key)                     # +1 failure, applies the TTL
        raise UnauthorizedException(message="Invalid credentials.")

    await throttle.reset(key)                       # success clears the counter
    return user
```

`throttle.status(key)` (peek without incrementing) and `throttle.hit(key)` (increment) both return a frozen `ThrottleStatus` dataclass — `attempts: int`, `blocked: bool`, `retry_after_seconds: int` — to build friendly error payloads. `raise_if_blocked` already raises `TooManyRequestsException`, which the SDK exception handler turns into the canonical `{detail, code, details}` envelope with HTTP 429 and a `Retry-After` header.

### Opaque tokens recipe

`generate_opaque_token` produces a high-entropy URL-safe token (default 32 bytes / 256 bits via `secrets.token_urlsafe`); `hash_opaque_token` stores it as an HMAC-SHA-256 digest so a leaked database row is useless on its own; `verify_opaque_token` performs constant-time comparison. Use them for password reset links, email confirmation, API keys, opaque session IDs — anything where the issued secret is never inspected by the recipient.

```python
from tempest_fastapi_sdk import (
    generate_opaque_token,
    hash_opaque_token,
    verify_opaque_token,
)
from src.core.settings import settings


def issue_reset_token(user_id: UUID) -> str:
    plain = generate_opaque_token()
    digest = hash_opaque_token(plain, secret=settings.OPAQUE_TOKEN_PEPPER)
    await reset_tokens_repo.add(
        PasswordResetToken(user_id=user_id, digest=digest, expires_at=...),
    )
    return plain  # send this in the email — never store it


async def consume_reset_token(plain: str, user_id: UUID) -> bool:
    record = await reset_tokens_repo.get_or_none({"user_id": user_id})
    if record is None or record.is_expired:
        return False
    return verify_opaque_token(
        plain,
        record.digest,
        secret=settings.OPAQUE_TOKEN_PEPPER,
    )
```

`secret=` is optional — passing the same pepper across `hash_*` / `verify_*` adds a service-wide secret so the digest column alone cannot be brute-forced. Defaults: 32 bytes of entropy, HMAC-SHA-256, constant-time compare. Override `nbytes=` for longer keys (API keys / refresh tokens).

### Client IP extraction recipe

`get_client_ip(request)` and `get_client_ip_from_scope(scope)` return the real client IP behind a proxy. By design they accept **one** trusted header name (`trusted_header=`) that your edge proxy is known to set (e.g. `"x-real-ip"` on Nginx) — without it, they use the direct peer address. Anti-spoofing belongs at the proxy (which overwrites the header with the real peer), not in Python, so the SDK only reads the header you declare trusted.

```python
from fastapi import Request

from tempest_fastapi_sdk import get_client_ip


@router.post("/login")
async def login(request: Request, payload: LoginIn) -> LoginOut:
    # Behind Nginx that overwrites X-Real-IP with the real peer:
    ip = get_client_ip(request, trusted_header="x-real-ip")
    await throttle.raise_if_blocked(f"login:{ip}")
    ...
```

Use `get_client_ip_from_scope(scope, trusted_header=...)` from middleware or websocket handlers where only the ASGI scope is in reach. If you expose the app directly to the internet, omit `trusted_header=` so the peer address is used.

### Command-line interface recipe

Installing `tempest-fastapi-sdk` exposes a `tempest` console script. It does two jobs: bootstrap a new layered service from the SDK's preferred skeleton, and run the four quality gates (`ruff check`, `ruff format`, `mypy`, `pytest`) without copy-pasting the same commands into every project.

```bash
tempest --help                                  # list every command
tempest --version                               # show the SDK version
```

On any usage error — unknown command, invalid option, missing required argument — the CLI prints that command's **full** `--help` (every parameter, default and description) right before the error line, instead of Click's terse `Try '... --help'` hint. The fix is on screen immediately.

#### Scaffold a new service

```bash
tempest new my_service                          # scaffold under ./my_service
tempest new my_service --path ~/projects        # custom parent dir
tempest new my_service \
    --bind-host 0.0.0.0 \                       # default HOST in .env.example
    --bind-port 9090 \                          # default PORT in .env.example
    --extras auth,upload                        # pinned SDK extras
tempest new my_service --force                  # overwrite existing dir
```

The skeleton matches the layered architecture documented in this README:

```text
my_service/
├── main.py                  # one-liner → src.server.run()
├── pyproject.toml           # pins tempest-fastapi-sdk + ruff/mypy/pytest
├── .env.example             # TITLE/VERSION/HOST/PORT/DATABASE_URL/JWT_SECRET/CORS_ORIGINS
├── .gitignore
├── Dockerfile               # multi-stage uv build, non-root, binds 0.0.0.0
├── .dockerignore
├── docker-compose.yaml      # Postgres + the services your extras need
├── README.md
├── src/
│   ├── server.py            # uvicorn.run() + module-level FastAPI app
│   ├── api/
│   │   ├── app.py           # create_app() wires SDK middleware + handlers
│   │   ├── routers/         # placeholder business router
│   │   └── dependencies/    # auth.py (require_token) + factories
│   ├── controllers/         # orchestration between services
│   ├── services/            # business logic
│   ├── schemas/             # Pydantic DTOs
│   ├── core/                # settings.py + exceptions.py
│   ├── db/
│   │   ├── models/
│   │   └── repositories/
│   └── utils/
└── tests/
    └── test_smoke.py        # asserts /api/ and /health/liveness boot
```

The generated `pyproject.toml` pins the current SDK version (`tempest-fastapi-sdk[auth]>=<version>` by default — change with `--extras`). The scaffolded `.env.example` uses the v0.8.0 settings naming (`SERVER_HOST`/`SERVER_PORT`/`SERVER_DEBUG`/`SERVER_RELOAD`/`LOG_LEVEL`/…), and `src/server.py` delegates to `tempest_fastapi_sdk.run_server` so uvicorn is imported lazily and tests can import the app without it. The API `title`, `version` and `description` shown in the OpenAPI docs (and the `/admin` header) come from the `TITLE` / `VERSION` / `DESCRIPTION` settings — set them in `.env`, no code edits. Validation rules: the project name must match `^[a-z][a-z0-9_]*$` and cannot collide with a Python keyword, so `tempest new Bad-Name` and `tempest new class` exit with code 2 before any file is written.

After scaffolding:

```bash
cd my_service
uv sync                                         # installs SDK + dev tools
cp .env.example .env
uv run python main.py                           # serves on the configured HOST:PORT
uv run pytest                                   # the bundled smoke test
```

#### Quality gates

The lint commands shell out to the project's tooling. They look for the executable on `PATH` first, and otherwise fall back to `uv run <tool>` so a project-local virtualenv works without manual activation.

```bash
tempest lint                                    # ruff check .
tempest fix                                     # ruff check --fix . + ruff format .   (writes)
tempest fix --unsafe                            # also apply ruff's --unsafe-fixes
tempest format                                  # ruff format .          (writes)
tempest fmt-check                               # ruff format --check .   (read-only)
tempest type                                    # mypy .
tempest test                                    # pytest
tempest test tests/api/                         # pytest with a path filter
tempest check                                   # lint + fmt-check + type + test, stops at first failure
```

`tempest fix` is the one-shot "organize the project" pass — sorts and dedupes imports, drops unused imports, normalizes string quotes, removes trailing whitespace, then runs `ruff format` to align indentation, line length, blank lines and trailing newlines. Run it before pushing when CI keeps catching style nits.

Every command returns the underlying tool's exit code, so `tempest check` is safe to wire into CI (`tempest check || exit 1`) or pre-commit hooks. When neither the executable nor `uv` is on `PATH`, the wrapper prints `error: '<tool>' is not on PATH and 'uv' is unavailable` and exits with `127` instead of failing silently.

#### Database — `tempest db`

Alembic wrapper backed by `AlembicHelper`. Reads `DATABASE_URL` from `--database-url` > env var > `src.core.settings.settings.DATABASE_URL` > `alembic.ini`.

```bash
tempest db init                                  # creates alembic.ini + alembic/env.py
tempest db revision -m "init users table"        # autogenerate (default)
tempest db revision -m "manual" --manual         # empty migration template
tempest db upgrade                               # alembic upgrade head
tempest db upgrade <rev>                         # upgrade to a specific revision
tempest db downgrade                             # roll back one step
tempest db current                               # print the applied revision
tempest db history -v                            # revisions newest → oldest, verbose
tempest db stamp head                            # mark the DB without running migrations
tempest db squash -m "init" --yes                # collapse history into 1 migration
tempest db backup                                # dump to backups/<db>_<ts>.<ext>
tempest db restore dump.dump --yes               # restore (clean + recreate)
tempest db seed                                  # run src.db.seeds:seed in one session
tempest db seed --seed src.db.fixtures:demo      # custom seed callable
```

`tempest db seed` runs a project seed callable (default `src.db.seeds:seed`, sync or async, taking one `AsyncSession`) inside a managed session — commit on success, rollback on error.

`tempest db squash` collapses an ever-growing migration history into one fresh root revision (drops the dev DB, regenerates from the models, re-applies; old revisions backed up under `versions/_squashed_<oldhead>/`). Reconcile production with `tempest db stamp head`.

`tempest db backup` / `tempest db restore` snapshot a database to a file and back — PostgreSQL via `pg_dump`/`pg_restore` (custom `.dump` or plain `.sql` by extension), SQLite via file copy. **PostgreSQL needs the client tools (`pg_dump`, `pg_restore`, `psql`) installed on the system** — e.g. `apt-get install postgresql-client` (Debian/Ubuntu), `dnf install postgresql` (Fedora), `brew install libpq` (macOS), `choco install postgresql` (Windows). See the [CLI recipe](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/cli/) for details.

#### Secrets — `tempest secrets`

Generates and rotates application secrets (`JWT_SECRET` / `TOKEN_SECRET` by default), rewriting the matching `.env` lines in place after a `.env.bak` backup; `--print` writes nothing and emits the values to stdout.

```bash
tempest secrets rotate                                # rotate JWT_SECRET + TOKEN_SECRET in .env
tempest secrets rotate --print                        # just print, write nothing
tempest secrets rotate --keys JWT_SECRET,SESSION_SECRET --env .env.prod
tempest secrets rotate --length 64 --no-backup
```

#### Users — `tempest user`

Seeds or lists users via the project's concrete `UserModel` (default `src.db.models:UserModel`). Bootstraps the first admin row so `/admin` login works without manual SQL.

```bash
tempest user create --email ana@example.com --password strong-pass-12 --no-admin
tempest user create --email admin@local --password admin-pass-12 --admin
tempest user create --email admin@local --admin       # prompt for password
tempest user create --email x@y --password p --model myapp.models.user:User
tempest user promote --email ana@example.com          # grant /admin access (is_admin=True)
tempest user revoke  --email ana@example.com          # revoke it (is_admin=False)
tempest user list                                     # everyone
tempest user list --admin                             # admins only
```

When `tempest user create` runs in an interactive terminal **without** `--admin`/`--no-admin`, it asks `Should this user be an administrator? [y/N]`. Non-interactive runs (CI, pipes) skip the prompt and create a regular user. `promote` / `revoke` look the user up by email (case-insensitive) and exit `1` with `no user found` when nothing matches.

#### Generate artifacts in an existing project — `tempest generate`

```bash
tempest generate --docker                             # regen docker-compose.yaml + .env.example from pinned extras
tempest generate --dockerfile                         # regen Dockerfile + .dockerignore (EXPOSE from .env SERVER_PORT)
tempest generate --src                                # add the src layers triggered by pinned extras
tempest generate --docker --dockerfile --src          # all at once
tempest generate --src --force                        # overwrite existing layer files
```

`--dockerfile` re-renders the multi-stage uv `Dockerfile` + `.dockerignore`; the `EXPOSE` / `SERVER_PORT` is read from the project's `.env` / `.env.example` (falling back to `8000`). `--src` reads the SDK extras pinned in `pyproject.toml` and writes only the layers that match — `[queue]` → `src/queue/` (FastStream broker + handlers), `[tasks]` → `src/tasks/` (TaskIQ broker + jobs). The source root (`src` or `app`) is auto-detected. It is idempotent: existing files are kept unless `--force` is passed. `tempest new --extras auth,queue` already scaffolds those layers — `generate --src` is for extras added after the project exists.

### Admin site recipe

Django-style management UI mounted under `/admin`. Operators sign in with a user row from the database (no separate admin password store) and browse every registered model from the browser, so the database port can stay closed on private networks. The panel is feature-complete (Django-admin parity): a list view with search / per-field filters / sortable columns, full CRUD (create / edit / delete), bulk actions, CSV/JSON export, FK-select widgets, a dashboard with live row counts + system metrics, optional TOTP MFA at login, and an audit trail stamping `created_by` / `updated_by`. Still on the roadmap: file upload and inline/related editing.

Requires the `[admin]` extra:

```bash
pip install "tempest-fastapi-sdk[admin]"
```

#### 1. User model

Subclass `BaseUserModel` to get the four columns the admin auth backend expects (`email`, `hashed_password`, `is_admin`, `last_login_at`) on top of the standard `BaseModel` row:

```python
# src/db/models/user.py
from tempest_fastapi_sdk import BaseUserModel


class UserModel(BaseUserModel):
    __tablename__ = "user"
```

`set_password()` / `check_password()` delegate to `PasswordUtils`; `normalize_email()` lowercases and strips. The default `is_active` (inherited from `BaseModel`) and `is_admin` (defaults to `False`) gate access — only `is_active=True` AND `is_admin=True` rows may sign in.

Bootstrap the first admin via your CLI / migration / seed script. The full script wires an `AsyncDatabaseManager`, opens one session, inserts the row and commits — exactly the same pattern your repositories follow at runtime:

```python
# scripts/create_admin.py
import asyncio

from tempest_fastapi_sdk import AsyncDatabaseManager

from src.core.settings import settings
from src.db.models import UserModel


async def main() -> None:
    db = AsyncDatabaseManager(settings.DATABASE_URL)
    await db.connect()
    try:
        async with db.get_session_context() as session:
            # ──────── the only admin-specific lines ────────
            admin = UserModel(email="root@example.com", is_admin=True)
            admin.set_password("hunter2")  # bcrypt via PasswordUtils
            session.add(admin)
            await session.commit()
    finally:
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
```

The four highlighted lines under the divider comment are the only admin-bootstrap code; everything around them is the standard async DB lifecycle the SDK already uses.

#### 2. Register your admin classes

`AdminModel` is a plain typed configuration instance — the constructor signature is the contract (no class-attribute / metaclass magic), and every field accepts a real SQLAlchemy column attribute (`UserModel.email`), so typos surface in your editor instead of at runtime. The defaults work out of the box; pass the fields you want to enrich the list view:

```python
# src/admin/site.py
from sqlalchemy import desc

from tempest_fastapi_sdk import AdminModel, AdminSite

from src.db.models import UserModel, OrderModel

site = AdminSite(
    title="MyApp Admin",
    index_subtitle="Site administration",
    site_url="https://myapp.com",   # optional outbound "View site" link
)

site.register(AdminModel(
    model=UserModel,
    list_display=[UserModel.email, UserModel.is_admin, UserModel.is_active, UserModel.last_login_at],
    list_filter=[UserModel.is_active, UserModel.is_admin],
    search_fields=[UserModel.email],
    readonly_fields=[UserModel.id, UserModel.hashed_password, UserModel.created_at, UserModel.updated_at],
    ordering=desc(UserModel.created_at),
    page_size=25,
))
```

Every field reference also accepts a plain string (`list_display=["email", ...]`) for dynamic configuration, and `ordering` accepts a column (ascending), `desc(column)` / `asc(column)`, or a Django-style `"-created_at"` string. `register` returns the instance and raises `ValueError` on a duplicate slug. Slugs default to the model's `__tablename__` so URLs and database tables stay in sync.

#### 3. Mount the router

```python
# src/api/app.py
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    UserModelAuthBackend,
    make_admin_router,
)

from src.admin.site import site
from src.core.settings import settings
from src.db.models import UserModel

db = AsyncDatabaseManager(settings.DATABASE_URL)
app = FastAPI()
app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.ADMIN_SECRET_KEY,    # at least 32 bytes
        prefix="/admin",
        cookie_secure=not settings.DEBUG,        # True in production HTTPS
    )
)
```

`make_admin_router` mounts:

- `GET  /admin/login`, `POST /admin/login`, `POST /admin/logout` — auth flow.
- `GET/POST /admin/mfa` — TOTP challenge (second factor) between password and access, for principals with MFA enabled.
- `GET  /admin/` — dashboard: a card per model with **row count** + Browse/New, plus a **metrics panel** (CPU/RAM/disk via `MetricsUtils`; on by default, omitted without the `[metrics]` extra, disabled with `make_admin_router(show_metrics=False)`).
- `GET  /admin/m/{slug}/` — list view with pagination + free-text search (`?q=`) + per-field filters (`?filter_<field>=value`) + clickable **column sorting** (`?sort=<column>&dir=asc|desc`).
- `GET  /admin/m/{slug}/export.csv` / `export.json` — **export** the current result set (honoring search/filters/sort) as CSV or JSON (row cap via `make_admin_router(export_max_rows=…)`, default 5000).
- `POST /admin/m/{slug}/bulk` — **bulk actions** (delete / activate / deactivate) on selected rows.
- `GET/POST /admin/m/{slug}/new` — **create** a record (when `can_create`).
- `GET  /admin/m/{slug}/{identity}` — detail view with Edit/Delete buttons + an Audit panel.
- `GET/POST /admin/m/{slug}/{identity}/edit` — **edit** a record (when `can_edit`).
- `POST /admin/m/{slug}/{identity}/delete` — **delete** a record (when `can_delete`).
- `GET  /admin/static/{path}` — bundled CSS/HTMX assets.

Write operations (create/edit/delete/bulk) are gated by the `AdminModel` flags `can_create` / `can_edit` / `can_delete` (all `True` by default; a disabled view returns `404`), carry a per-session CSRF token validated server-side, derive form widgets from the column type, render an FK-select dropdown for foreign keys whose target has a registered `AdminModel`, and stamp `created_by` / `updated_by` (from `AuditMixin`) with the acting admin's id.

#### 4. Session security defaults

`SignedCookieSessionStore` uses `itsdangerous.TimestampSigner` (HMAC-SHA256) to sign a single cookie:

- `HttpOnly` always set.
- `Secure` flagged when `cookie_secure=True` (default; flip off in local HTTP dev).
- `SameSite=Lax` (`"lax"`/`"strict"`/`"none"` accepted).
- Default lifetime `8h`; expired or tampered cookies are rejected silently.
- Per-session CSRF token is generated at login and required by every form POST (login, logout, create, edit, delete, bulk actions).
- `secret_key` must be at least 32 bytes — short keys raise `ValueError` at construction time.

#### 5. Plug in a custom auth backend

`AdminAuthBackend` is an ABC, so swap the default for LDAP / OAuth / external IAM by subclassing:

```python
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AdminAuthBackend, AdminAuthError


class OAuthAdminBackend(AdminAuthBackend):
    async def authenticate(
        self,
        session: AsyncSession,
        *,
        identifier: str,
        password: str,
    ) -> Any:
        principal = await my_oauth_client.authenticate(identifier, password)
        if not principal.has_role("admin"):
            raise AdminAuthError("not an admin")
        return principal

    async def load_principal(
        self,
        session: AsyncSession,
        principal_id: str,
    ) -> Any | None:
        return await my_oauth_client.get_user(principal_id)

    def principal_id(self, principal: Any) -> str:
        return principal.sub

    def display_name(self, principal: Any) -> str:
        return principal.email
```

Pass the instance via `auth_backend=` and the rest of the admin pipeline (sessions, dashboard, list, detail) keeps working unchanged.

### Migration guide 0.7 → 0.8

0.8.0 renames every field on `ServerSettings`, extracts log fields to a new `LogSettings` mixin, and adds eleven other primitives. The renames are the only **breaking** changes — every new primitive is opt-in.

#### 1. Rename env vars

| Old | New | Mixin |
| --- | --- | --- |
| `HOST` | `SERVER_HOST` | `ServerSettings` |
| `PORT` | `SERVER_PORT` | `ServerSettings` |
| `DEBUG` | `SERVER_DEBUG` | `ServerSettings` |
| *(new)* | `SERVER_RELOAD` | `ServerSettings` |
| `LOG_LEVEL` | `LOG_LEVEL` | **moved to** `LogSettings` |
| `LOG_JSON` | `LOG_JSON` | **moved to** `LogSettings` |

Mechanical `sed` on every `.env` / `docker-compose.yml` / deployment manifest:

```bash
sed -i \
  -e 's/^HOST=/SERVER_HOST=/' \
  -e 's/^PORT=/SERVER_PORT=/' \
  -e 's/^DEBUG=/SERVER_DEBUG=/' \
  .env .env.example .env.test
```

`LOG_LEVEL` and `LOG_JSON` keep their names — only the mixin moves.

#### 2. Rename code references

```bash
# `settings.HOST` → `settings.SERVER_HOST`, same for PORT/DEBUG
grep -rn "settings\.\(HOST\|PORT\|DEBUG\)\b" src/ tests/
```

Replace each match with the `SERVER_*` form. If a service was using the
old `settings.DEBUG` flag for application-level debug behavior, switch
to `settings.SERVER_DEBUG`; if it was only being read for uvicorn
auto-reload, switch to `settings.SERVER_RELOAD`.

#### 3. Mix `LogSettings` into the project `Settings`

```diff
 from tempest_fastapi_sdk import (
     BaseAppSettings,
     CORSSettings,
     DatabaseSettings,
     JWTSettings,
+    LogSettings,
     RabbitMQSettings,
     RedisSettings,
     ServerSettings,
 )


 class Settings(
     ServerSettings,
+    LogSettings,
     DatabaseSettings,
     RedisSettings,
     RabbitMQSettings,
     JWTSettings,
     CORSSettings,
     BaseAppSettings,
 ):
     ...
```

Skip this step if the service never read `settings.LOG_LEVEL` /
`settings.LOG_JSON` — `configure_logging` accepts the values as
keyword arguments directly.

#### 4. (Optional) Adopt the new primitives

Pick what fits. None of these are required.

- Replace the hand-written `src/server.py` `uvicorn.run(...)` with
  [`run_server(...)`](#programmatic-server-entry-point-recipe).
- Replace the hand-written `get_current_user` with
  [`make_jwt_user_dependency(tokens, load_user)`](#jwt-bearer--current-user--role-dependencies-recipe).
- Move `SMTP_*` / `UPLOAD_*` / `TOKEN_SECRET` / `VAPID_*` /
  `TASKIQ_*` fields out of the project's `Settings` and onto the
  matching SDK mixin ([Settings mixins composition](#settings-mixins-composition-recipe)).
- Adopt the
  [`Outbox dispatcher pattern`](#outbox-dispatcher-pattern-recipe) if
  you already write side-effects from the same transaction as your
  domain rows.

#### 5. Verify

```bash
uv sync                      # picks up new pyproject deps
uv run pytest -q             # full suite
uv run ruff check src tests  # confirm no `HOST`/`PORT`/`DEBUG` references slipped
```

If `pytest` fails with a Pydantic `ValidationError` referencing
`HOST` / `PORT` / `DEBUG`, an env var was not renamed (look at the
process environment or `.env`).

---

## Reference

### BaseRepository methods

| Method | Purpose | Raises on miss |
| --- | --- | --- |
| `get(filters, for_update=False)` | Single record matching filters | ✅ `not_found_exception` |
| `get_or_none(filters, for_update=False)` | Single record or `None` | — |
| `get_by_id(id, for_update=False)` | Shortcut for `get({"id": id})` | ✅ `not_found_exception` |
| `exists(filters)` | `bool` without loading the row | — |
| `first(filters, order_by, ascending)` | First match ordered | — |
| `list(filters, order_by, ascending)` | All rows; `[]` when empty | — |
| `paginate(filters=None, order_by=None, page=1, page_size=20, ascending=True, query=None)` | `dict` with `items, total, page, page_size, pages` | — |
| `count(filters)` | Row count | — |
| `add(model)` | Insert single | `ConflictException` on integrity |
| `add_all(models)` | Insert batch | `ConflictException` on integrity |
| `update(model)` | Commit mutated instance | `ConflictException` on integrity |
| `update_many(models)` | Commit batch | `ConflictException` on integrity |
| `bulk_update(filters, values)` | `UPDATE ... WHERE` mass mutation; rejects empty filters | `ConflictException` on integrity |
| `delete(id)` | Hard delete by PK | ✅ `not_found_exception` |
| `delete_many(filters)` | Hard delete by filter | — |
| `delete_batch(ids)` | Hard delete several PKs | — |
| `soft_delete(id)` | Flip `is_active=False`; returns row | ✅ `not_found_exception` |
| `restore(id)` | Flip `is_active=True`; returns row | ✅ `not_found_exception` |
| `map_to_schema(instance)` | Override in subclass | `NotImplementedError` |
| `map_to_response(instance)` | Override in subclass | `NotImplementedError` |
| `map_to_model(data)` | Default: `self.model(**data)` | — |

### Filter dict conventions

The dict passed to `get` / `list` / `paginate` / `count` / `exists` / `delete_many` / `bulk_update` understands these patterns out of the box:

| Filter shape | Generated SQL |
| --- | --- |
| `{"col": value}` | `col = value` |
| `{"col": [a, b]}` | `col IN (a, b)` |
| `{"col": True}` / `{"col": False}` | `col IS TRUE` / `col IS FALSE` |
| `{"name": "ana"}` (string field literally named `name`) | `name ILIKE '%ana%'` |
| `{"col": date(2024, 1, 1)}` (date value) | `date(col) = '2024-01-01'` |
| `{"start_in": date(...)}` | `date(date_col_or_created_at) >= ...` |
| `{"end_in": date(...)}` | `date(date_col_or_created_at) <= ...` |
| `{"col": None}` | filter is skipped (omit-when-None semantics) |

Pass the dict from `BasePaginationFilterSchema.get_conditions()` for query-string-driven filters.

### Lifecycle managers

Every SDK-shipped manager follows the same core shape: `connect()` to start,
`disconnect()` to stop, and `health_check()` to plug into
`make_health_router(checks=...)`. Brokers and the scheduler additionally
expose `lifespan()` (async context manager) and `is_connected`
(read-only state). The tables below list the manager-specific surface.

#### `AsyncDatabaseManager`

| Method / Property | Purpose |
| --- | --- |
| `__init__(url, *, echo=False, pool_size=10, max_overflow=20, pool_recycle=3600, **engine_kwargs)` | Build the manager (engine is lazy). |
| `await connect()` | Create the engine + sessionmaker. |
| `await disconnect()` | Dispose the engine. |
| `is_connected` (property) | `True` while the engine is alive. |
| `db_url_safe` (property) | DSN with password redacted (safe to log). |
| `await get_session()` | Return a single `AsyncSession`. |
| `async with get_session_context()` | Yield an `AsyncSession`; commits on success, rolls back on raise. |
| `async session_dependency()` | FastAPI `Depends`-compatible generator. |
| `await create_tables()` | Issue `CREATE TABLE` for every model on `BaseModel.metadata` (tests / local dev — production schemas go through Alembic). |
| `await drop_tables()` | Issue `DROP TABLE` for every model on `BaseModel.metadata`. |
| `await health_check()` | Ping with `SELECT 1`; returns `bool`. |

#### `AsyncRedisManager` *(extra: `[cache]`)*

| Method / Property | Purpose |
| --- | --- |
| `__init__(url, *, decode_responses=True, **client_kwargs)` | Wrap a `redis.asyncio.Redis` client. |
| `await connect()` | Build the client. |
| `await disconnect()` | Close the client + release the pool. |
| `client` (property) | Live `Redis` instance; raises `RuntimeError` before `connect`. |
| `async with get_client_context()` | Yield the same client inside an `async with`. |
| `async client_dependency()` | FastAPI `Depends`-compatible generator. |
| `await health_check()` | `PING` returns `bool`. |

Pair with `@cached(redis, ttl=..., key_prefix=...)` for function-level memoization — see the [Cache decorator recipe](#cache-decorator-recipe).

#### `AsyncBrokerManager` *(extra: `[queue]`)*

| Method / Property | Purpose |
| --- | --- |
| `__init__(broker)` | Wrap any FastStream broker (`RabbitBroker`, `KafkaBroker`, ...). |
| `await connect()` | Start the broker; idempotent. |
| `await disconnect()` | Stop the broker. |
| `broker` (attribute) | The wrapped FastStream broker — use `broker.publisher(...)` / `broker.subscriber(...)` directly. |
| `await publish(message, *args, **kwargs)` | Forward to `broker.publish`; raises before `connect`. |
| `async with lifespan()` | Connect on enter, disconnect on exit. |
| `async broker_dependency()` | FastAPI `Depends`-compatible generator yielding the broker. |
| `await health_check()` | `True` while the broker is started. |
| `is_connected` (property) | Read-only state. |

#### `AsyncTaskBrokerManager` *(extra: `[tasks]`)*

| Method / Property | Purpose |
| --- | --- |
| `__init__(broker)` | Wrap any TaskIQ broker (`AioPikaBroker`, `RedisBroker`, `InMemoryBroker`, ...). |
| `await connect()` | `broker.startup()`; idempotent. |
| `await disconnect()` | `broker.shutdown()`. |
| `broker` (attribute) | The wrapped TaskIQ broker. |
| `task(*args, **kwargs)` | Decorator forwarding to `broker.task`. |
| `register_task(func, *, task_name=None, **kwargs)` | Register without decorator syntax. |
| `async with lifespan()` | Connect on enter, disconnect on exit. |
| `async broker_dependency()` | FastAPI `Depends`-compatible generator. |
| `await health_check()` | `True` while the broker is started. |
| `is_connected` (property) | Read-only state. |

#### `AsyncTaskScheduler` *(extra: `[tasks]`)*

| Method / Property | Purpose |
| --- | --- |
| `__init__(broker, sources=None)` | Wrap `TaskiqScheduler` + `LabelScheduleSource` (default). |
| `broker` (attribute) | Same broker tasks are kicked into. |
| `sources` (attribute) | List of `ScheduleSource` instances. |
| `scheduler` (attribute) | Underlying `taskiq.TaskiqScheduler`. |
| `@cron(expr, *, cron_offset=None, task_name=None, **labels)` | Register a cron-scheduled task. |
| `@interval(seconds_or_timedelta, *, task_name=None, **labels)` | Register a fixed-interval task. |
| `@schedule(spec, *, task_name=None, **labels)` | Register with raw TaskIQ schedule list. |
| `register(func, *, schedule, task_name=None, **labels)` | Decorator-free registration. |
| `await connect()` | `scheduler.startup()` + every `source.startup()`. |
| `await disconnect()` | Cancel the background loop (if any) and shut down. |
| `await run_in_background()` | Spawn an in-process `SchedulerLoop` task (dev / single-process). |
| `async with lifespan()` | Connect on enter, disconnect on exit. |
| `await health_check()` | `True` while started and (when applicable) the loop is alive. |
| `is_connected` (property) | Read-only state. |

Production deployments should run the standalone CLI instead of `run_in_background()`:

```bash
taskiq worker src.tasks:tasks.broker
taskiq scheduler src.tasks:scheduler.scheduler
```

### Error envelope

Every `AppException` (and any subclass) is serialized by `register_exception_handlers` into:

```json
{
    "detail": "Human-readable message",
    "code": "MACHINE_READABLE_CODE",
    "details": {"any": "structured context"}
}
```

| Exception | Default `status_code` | Default `code` |
| --- | --- | --- |
| `AppException` | 500 | `INTERNAL_SERVER_ERROR` |
| `NotFoundException` | 404 | `NOT_FOUND` |
| `ConflictException` | 409 | `CONFLICT` |
| `ValidationException` | 422 | `VALIDATION_ERROR` |
| `UnauthorizedException` | 401 | `UNAUTHORIZED` |
| `InvalidTokenException` | 401 | `INVALID_TOKEN` |
| `ExpiredTokenException` | 401 | `TOKEN_EXPIRED` |
| `ForbiddenException` | 403 | `FORBIDDEN` |
| `FileTooLargeException` | 413 | `FILE_TOO_LARGE` |
| `InvalidFileTypeException` | 415 | `INVALID_FILE_TYPE` |

Subclasses set `message`/`code`/`status_code` as class attributes; instances can override `message` and attach a `details` dict at construction time.

---

## Conventions

- **Layered architecture**: routers → controllers → services → repositories. Never skip layers.
- **Async-first**: every I/O method is `async`. Use SQLAlchemy 2.0 patterns (`select`, not `session.query()`).
- **Collections return `[]`**: never raise on empty results. Only single-resource lookups raise `NotFoundException`.
- **Soft delete by default**: `is_active=False` instead of physical delete when applicable.
- **UTC everywhere**: timestamps normalized via `to_utc`; `BaseResponseSchema` enforces this on field validators.
- **Double quotes**: enforced by `ruff format`.
- **Full typing**: every parameter, return value and class attribute is typed. `Any` is explicit, never implicit.
- **Docstrings in English**: Google-style covering description / args / returns / raises.
- **Frontend branches on `code`**, not on the (translatable) message.

---

## Development

```bash
make install     # uv sync --all-extras
make test        # pytest with coverage
make lint        # ruff check .
make fmt         # ruff format .
make type        # mypy --strict
make check       # lint + fmt-check + type + test (every gate)
make ci          # check + build + smoke install in a clean venv (mirrors GitHub Actions)
make build       # uv build → dist/
make clean       # remove caches and build artifacts
```

Run `make` (or `make help`) to list every target. The Makefile is just thin wrappers around `uv` — direct invocations still work too:

```bash
uv sync --all-extras
uv run pytest
uv run ruff check .
uv run mypy tempest_fastapi_sdk
uv build
```

The CI gate (`.github/workflows/ci.yml`) runs the equivalent of `make ci` on every push and pull request against `main`. The wheel-build smoke step installs the freshly built artifact into a clean Python 3.13 virtualenv and imports the top-level surface to guard against the empty-wheel / missing-package-data class of bugs.

---

## Release

Releases are published to [PyPI](https://pypi.org/project/tempest-fastapi-sdk/) automatically when a `v*.*.*` tag is pushed.

The pipeline (`.github/workflows/release-pypi.yml`) does three things:

1. **Version sanity check.** The tag (`v0.1.0`), `pyproject.toml`'s `version` field and `tempest_fastapi_sdk.__version__` must all match. Mismatched releases abort before any artifact is built.
2. **Build + verify.** Run the full validation suite (tests + lint + mypy), build sdist/wheel with `uv build`, check metadata with `twine check`, and verify the wheel actually contains the package files + the bundled `env.py.template` Alembic template.
3. **Publish via Trusted Publishing.** Uses OIDC against PyPI — no long-lived API tokens stored in repo secrets.

### Cutting a release

```bash
make release VERSION=0.2.0
```

This single target:

1. Refuses to run if the working tree is dirty.
2. Bumps `pyproject.toml` and `tempest_fastapi_sdk/__init__.py` to the requested version.
3. Runs `make check` (lint + format + mypy + tests) so a broken commit never gets tagged.
4. Commits the bump as `chore: release v0.2.0` and creates the `v0.2.0` tag locally.
5. Prints the two `git push` commands you still need to run — pushing is left manual on purpose so you can review the commit one last time.

```bash
# Review then push
git show v0.2.0
git push origin main
git push origin v0.2.0
```

GitHub Actions picks up the tag, runs the release pipeline and publishes the artifacts to PyPI.

Manual flow (no Makefile) — same result:

```bash
$EDITOR pyproject.toml                              # version = "0.2.0"
$EDITOR tempest_fastapi_sdk/__init__.py             # __version__ = "0.2.0"
git commit -am "chore: release v0.2.0"
git tag v0.2.0
git push origin main v0.2.0
```

### One-time PyPI Trusted Publishing setup

PyPI needs to know which workflow is allowed to publish on the project's behalf. Done once per project:

1. Create the project on [PyPI](https://pypi.org) (name: `tempest-fastapi-sdk`). For brand-new projects, register a placeholder release manually first or use [`pending` publishers](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/) so the first OIDC-driven upload can create it.
2. On the project's PyPI settings page, add a **Trusted Publisher** pointing to:
   - **Owner**: `mauriciobenjamin700`
   - **Repository**: `tempest-fastapi-sdk`
   - **Workflow filename**: `release-pypi.yml`
   - **Environment**: `pypi` (must match `release-pypi.yml`'s `environment.name`)
3. In the GitHub repository, create an environment named `pypi` (Settings → Environments → New environment). Optionally restrict deployments to tags matching `v*.*.*` for an extra safety net.

After this, every `v*.*.*` tag triggers a publish. No PYPI tokens are stored anywhere.

---

## Roadmap

The full, prioritized roadmap lives in the documentation site —
[**Roadmap (PT-BR)**](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/roadmap/) ·
[**Roadmap (EN-US)**](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/roadmap/).

Short version — recently shipped and what's next:

- **v0.23.0** — MinIO/S3 storage (`AsyncMinIOClient`).
- **v0.24.0** — pluggable upload backends + `IdempotencyMiddleware`
  + Jinja2 email templates.
- **v0.25.0** — `tempest new` generates `docker-compose.yaml`.
- **v0.26.0** — `tempest generate --docker` regenerates compose
  in place; Postgres 18 / Redis 8 / RabbitMQ 4 image bumps;
  Pydantic schemas + settings carry `title`/`description`/`examples`.
- **v0.28.0** — Prometheus `/metrics` + `PrometheusMiddleware`,
  typed `HTTPClient` (retry / backoff / circuit-breaker /
  `X-Request-ID` propagation), `BodySizeLimitMiddleware`,
  `bulk_create_values` + `bulk_upsert` on `BaseRepository`.
- **v0.29.0** — `CSRFMiddleware` + OAuth2/OIDC providers
  (`GoogleOAuthClient`, `GitHubOAuthClient`, `OIDCProvider`).
- **v0.29.1** — scaffold ships `UserModel` + admin wiring;
  `[auth,admin]` is the default extras pair.
- **v0.30.0** — `tempest db` + `tempest user` CLI subcommands
  cover migrations + admin bootstrap end-to-end.
- **v0.30.1** — Alembic `reorder_base_columns_first` hook
  emits base columns first in every autogenerated `create_table`.
- **v0.30.2** — `alembic.ini` no longer stamps the DB URL;
  resolved at runtime from env / settings / constructor.
- **v0.30.3** — silenced post-write hook noise on
  `tempest db revision`.
- **v0.31.0** — bundled auth flow (`UserAuthService` +
  `make_auth_router` covering signup / activate / login /
  password-reset with default email templates bundled).
- **v0.31.1** — `ActivationToken` / `PasswordResetToken`
  rewritten as `BaseSchema` (no more dataclass leak); every
  auth DTO carries a full docstring.
- **v0.31.2** — `UserAuthService` methods type `session` as
  `AsyncSession` everywhere (no more `Any`).

---

## License

MIT
