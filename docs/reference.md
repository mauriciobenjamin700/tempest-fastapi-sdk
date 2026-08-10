# Referência da API

Gerada automaticamente a partir das docstrings do SDK via [`mkdocstrings`](https://mkdocstrings.github.io/). Todo símbolo exportado no `__all__` de `tempest_fastapi_sdk` e dos seus submódulos públicos está documentado aqui — com assinatura completa, parâmetros, tipo de retorno, exceções levantadas e link para o código-fonte.

!!! info "Cobertura verificada, não prometida"
    `tests/test_reference_coverage.py` renderiza esta página e compara os âncoras emitidos com o `__all__` de cada módulo público, então um símbolo novo que não chegue aqui quebra o `make check`.

    Três grupos ficam de fora **de propósito**, cada um com o motivo registrado no allowlist do teste: os aliases BR pré-0.76 sem sufixo `Field` (`CPF`, `CNPJ`, `CEP`, `CPFOrCNPJ`, `PhoneBR`) e `AsyncBrokerManager`, todos **deprecados** — documentá-los convidaria ao uso; e `Classifier` / `Detector` / `Segmenter`, que são reexports do `ort-vision-sdk` e pertencem à documentação daquele projeto.

!!! tip "Buscando"
    Use a barra de busca no topo da página (ou pressione `/`) para pular para um símbolo pelo nome. O índice full-text inclui as docstrings, então buscas como "soft delete" ou "request id" caem na classe certa.

---

## Superfície de topo

::: tempest_fastapi_sdk
    options:
      members_order: source
      show_root_toc_entry: false
      show_submodules: false
      filters:
        - "!^_"

---

## Agentes de IA

### `tempest_fastapi_sdk.agents`

::: tempest_fastapi_sdk.agents
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: alphabetical
      filters:
        - "!^_"

---

## Admin

### `tempest_fastapi_sdk.admin`

::: tempest_fastapi_sdk.admin.site.AdminSite
::: tempest_fastapi_sdk.admin.config.AdminModel
::: tempest_fastapi_sdk.admin.config.Inline
::: tempest_fastapi_sdk.admin.config.Lens
::: tempest_fastapi_sdk.admin.permissions.AdminPermission
::: tempest_fastapi_sdk.admin.permissions.AdminAccessPolicy
::: tempest_fastapi_sdk.admin.dashboard.MetricCard
::: tempest_fastapi_sdk.admin.dashboard.MetricValue
::: tempest_fastapi_sdk.admin.dashboard.MetricTrend
::: tempest_fastapi_sdk.admin.dashboard.MetricPartition
::: tempest_fastapi_sdk.admin.actions.admin_action
::: tempest_fastapi_sdk.admin.actions.AdminActionContext
::: tempest_fastapi_sdk.admin.actions.AdminActionResult
::: tempest_fastapi_sdk.admin.auth.AdminAuthBackend
::: tempest_fastapi_sdk.admin.auth.UserModelAuthBackend
::: tempest_fastapi_sdk.admin.router.make_admin_router
::: tempest_fastapi_sdk.admin.discovery.discover_models
::: tempest_fastapi_sdk.admin.session.AdminSession
::: tempest_fastapi_sdk.admin.session.SignedCookieSessionStore

---

### `tempest_fastapi_sdk.admin.sql_shell`

::: tempest_fastapi_sdk.admin.sql_shell
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

::: tempest_fastapi_sdk.admin.sql_shell.SqlAuditor

---

## API (integração FastAPI)

### `tempest_fastapi_sdk.api`

::: tempest_fastapi_sdk.api.handlers.register_exception_handlers
::: tempest_fastapi_sdk.api.handlers.make_app_exception_handler
::: tempest_fastapi_sdk.api.handlers.make_http_exception_handler
::: tempest_fastapi_sdk.api.handlers.make_unhandled_exception_handler
::: tempest_fastapi_sdk.api.error_docs.error_responses
::: tempest_fastapi_sdk.api.error_docs.raises
::: tempest_fastapi_sdk.api.error_docs.TempestAPIRouter
::: tempest_fastapi_sdk.api.error_docs.RaisesSpec
::: tempest_fastapi_sdk.api.error_docs.declared_raises

---

## Artefatos (registro de versões)

### `tempest_fastapi_sdk.artifacts`

::: tempest_fastapi_sdk.artifacts.model.ArtifactVersionMixin
::: tempest_fastapi_sdk.artifacts.registry.ArtifactRegistry
::: tempest_fastapi_sdk.artifacts.registry.ArtifactManifestEntry
::: tempest_fastapi_sdk.artifacts.registry.build_manifest_entries
::: tempest_fastapi_sdk.artifacts.digest.file_digest
::: tempest_fastapi_sdk.artifacts.digest.object_digest
::: tempest_fastapi_sdk.artifacts.actions.make_activate_artifact_action

---

## Banco de dados

### `tempest_fastapi_sdk.db`

::: tempest_fastapi_sdk.db.model.BaseModel
::: tempest_fastapi_sdk.db.user_model.BaseUserModel
::: tempest_fastapi_sdk.db.user_token_model.BaseUserTokenModel
::: tempest_fastapi_sdk.db.user_token_model.make_user_token_model
::: tempest_fastapi_sdk.db.user_recovery_code_model.BaseUserRecoveryCodeModel
::: tempest_fastapi_sdk.db.user_recovery_code_model.make_user_recovery_code_model
::: tempest_fastapi_sdk.db.repository.BaseRepository
::: tempest_fastapi_sdk.db.expressions.F
::: tempest_fastapi_sdk.db.expressions.Q
::: tempest_fastapi_sdk.db.expressions.build_filter_condition
::: tempest_fastapi_sdk.db.signals.RepositorySignal
::: tempest_fastapi_sdk.db.signals.connect
::: tempest_fastapi_sdk.db.signals.on_signal
::: tempest_fastapi_sdk.db.tenant.TenantScopedRepository
::: tempest_fastapi_sdk.db.mixins.SoftDeleteMixin
::: tempest_fastapi_sdk.db.mixins.AuditMixin
::: tempest_fastapi_sdk.db.mixins.MFAMixin
::: tempest_fastapi_sdk.db.mixins.LocaleColumnMixin
::: tempest_fastapi_sdk.db.connection.AsyncDatabaseManager
::: tempest_fastapi_sdk.db.migrations.AlembicHelper
::: tempest_fastapi_sdk.db.slow_query.SlowQueryLogger
::: tempest_fastapi_sdk.db.outbox.BaseOutboxModel
::: tempest_fastapi_sdk.db.outbox.OutboxRelay
::: tempest_fastapi_sdk.db.outbox.OutboxStatus
::: tempest_fastapi_sdk.db.audit.BaseAuditLogModel
::: tempest_fastapi_sdk.db.audit.AuditAction
::: tempest_fastapi_sdk.db.audit.snapshot_model
::: tempest_fastapi_sdk.db.audit.diff_snapshots
::: tempest_fastapi_sdk.db.migrations.DestructiveMigrationError
::: tempest_fastapi_sdk.db.connection.enable_sqlite_savepoints
::: tempest_fastapi_sdk.db.transaction.transaction
::: tempest_fastapi_sdk.db.transaction.savepoint
::: tempest_fastapi_sdk.db.transaction.in_transaction
::: tempest_fastapi_sdk.db.transaction.transaction_depth
::: tempest_fastapi_sdk.db.search.TextSearchLanguage
::: tempest_fastapi_sdk.db.search.TextSearchWeight
::: tempest_fastapi_sdk.db.search.TokenMatch
::: tempest_fastapi_sdk.db.search.like_search_condition
::: tempest_fastapi_sdk.db.search.full_text_condition
::: tempest_fastapi_sdk.db.search.full_text_rank
::: tempest_fastapi_sdk.db.search.supports_full_text
::: tempest_fastapi_sdk.db.enums.TempestEnum
::: tempest_fastapi_sdk.db.enums.enum_column
::: tempest_fastapi_sdk.db.enums.enum_values
::: tempest_fastapi_sdk.db.enums.enum_type_name
::: tempest_fastapi_sdk.db.enum_migrations.ReplaceEnumOp
::: tempest_fastapi_sdk.db.enum_migrations.EnumColumnRef
::: tempest_fastapi_sdk.db.enum_migrations.EnumTypeState
::: tempest_fastapi_sdk.db.enum_migrations.render_enum_types
::: tempest_fastapi_sdk.db.enum_migrations.sync_enum_types
::: tempest_fastapi_sdk.db.explain.explain_queries
::: tempest_fastapi_sdk.db.explain.ExplainReport
::: tempest_fastapi_sdk.db.explain.QueryPlan
::: tempest_fastapi_sdk.db.explain.ExplainDetail

---

## Cache

::: tempest_fastapi_sdk.cache.redis_manager.AsyncRedisManager
::: tempest_fastapi_sdk.cache.decorator.cached
::: tempest_fastapi_sdk.cache.invalidation.CacheInvalidator

---

## Chat

### `tempest_fastapi_sdk.chat`

::: tempest_fastapi_sdk.chat
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

---

## Comentários e avaliações

### `tempest_fastapi_sdk.reviews`

::: tempest_fastapi_sdk.reviews
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

---

## Computer vision

### `tempest_fastapi_sdk.vision`

::: tempest_fastapi_sdk.vision
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

---

## Core

### `tempest_fastapi_sdk.core`

::: tempest_fastapi_sdk.core.typed.strict_types
::: tempest_fastapi_sdk.core.typed.typed
::: tempest_fastapi_sdk.core.typed.require_annotations

---

## Exceções

### `tempest_fastapi_sdk.exceptions`

::: tempest_fastapi_sdk.exceptions.base.AppException
::: tempest_fastapi_sdk.exceptions.base.InheritedErrorCodeWarning
::: tempest_fastapi_sdk.exceptions.not_found.NotFoundException
::: tempest_fastapi_sdk.exceptions.conflict.ConflictException
::: tempest_fastapi_sdk.exceptions.unauthorized.UnauthorizedException
::: tempest_fastapi_sdk.exceptions.forbidden.ForbiddenException
::: tempest_fastapi_sdk.exceptions.validation.ValidationException
::: tempest_fastapi_sdk.exceptions.too_many_requests.TooManyRequestsException
::: tempest_fastapi_sdk.exceptions.jwt.InvalidTokenException
::: tempest_fastapi_sdk.exceptions.jwt.ExpiredTokenException
::: tempest_fastapi_sdk.exceptions.upload.FileTooLargeException
::: tempest_fastapi_sdk.exceptions.upload.InvalidFileTypeException
::: tempest_fastapi_sdk.exceptions.i18n.MessageCatalog
::: tempest_fastapi_sdk.exceptions.i18n.default_message_catalog
::: tempest_fastapi_sdk.exceptions.i18n.parse_accept_language

---

## Feature flags

::: tempest_fastapi_sdk.flags.service.FeatureFlags
::: tempest_fastapi_sdk.flags.backends.FeatureFlagBackend
::: tempest_fastapi_sdk.flags.backends.MemoryFeatureFlagBackend
::: tempest_fastapi_sdk.flags.backends.EnvFeatureFlagBackend
::: tempest_fastapi_sdk.flags.backends.RedisFeatureFlagBackend
::: tempest_fastapi_sdk.flags.backends.CompositeFeatureFlagBackend
::: tempest_fastapi_sdk.flags.dependencies.make_flag_dependency

---

## Fila e tarefas

### `tempest_fastapi_sdk.queue`

::: tempest_fastapi_sdk.queue
::: tempest_fastapi_sdk.queue.publisher.Publisher
::: tempest_fastapi_sdk.queue.consumer.Subscription
::: tempest_fastapi_sdk.queue.topology.QueueSpec
::: tempest_fastapi_sdk.queue.topology.DeadLetterSpec
::: tempest_fastapi_sdk.queue.topology.QueueType
::: tempest_fastapi_sdk.queue.topology.Transport
::: tempest_fastapi_sdk.queue.topology.UnsupportedTopologyError
::: tempest_fastapi_sdk.queue.reliability.ConsumerRetryPolicy
::: tempest_fastapi_sdk.queue.reliability.RetryTopology
::: tempest_fastapi_sdk.queue.reliability.retry_queues
::: tempest_fastapi_sdk.queue.reliability.delivery_attempt
::: tempest_fastapi_sdk.queue.reliability.QueueMetrics
::: tempest_fastapi_sdk.queue.dedup.DedupStore
::: tempest_fastapi_sdk.queue.dedup.DedupState
::: tempest_fastapi_sdk.queue.dedup.MemoryDedupStore
::: tempest_fastapi_sdk.queue.dedup.RedisDedupStore
::: tempest_fastapi_sdk.queue.dedup.ConcurrentDeliveryError
::: tempest_fastapi_sdk.queue.tracing.consume_span
::: tempest_fastapi_sdk.queue.tracing.inject_context
::: tempest_fastapi_sdk.queue.tracing.extract_context
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

### `tempest_fastapi_sdk.tasks`

::: tempest_fastapi_sdk.tasks
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

---

## Geolocalização

### `tempest_fastapi_sdk.geo`

::: tempest_fastapi_sdk.geo
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

::: tempest_fastapi_sdk.geo.distance.EARTH_RADIUS_KM
::: tempest_fastapi_sdk.geo.estimate.DEFAULT_CIRCUITY_FACTOR
::: tempest_fastapi_sdk.geo.estimate.DEFAULT_CAR_SPEED_KMH
::: tempest_fastapi_sdk.geo.estimate.DEFAULT_MODE_DURATION_FACTORS
::: tempest_fastapi_sdk.geo.routing.DEFAULT_OSRM_BASE_URL
::: tempest_fastapi_sdk.geo.routing.DEFAULT_MODE_PROFILES
::: tempest_fastapi_sdk.geo.geocoding.DEFAULT_NOMINATIM_BASE_URL
::: tempest_fastapi_sdk.geo.br.UF_CENTROIDS
---

## Modelops

### `tempest_fastapi_sdk.modelops`

::: tempest_fastapi_sdk.modelops
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

---

## Modelos self-hosted

A superfície de topo do `genai` — geração de texto, embeddings, VLM,
moderação, cache, métricas e router. Os submódulos que exigem outra
disciplina (`hub`, `image`, `inventory`, `rag`, `audio`) vêm depois.

### `tempest_fastapi_sdk.genai`

::: tempest_fastapi_sdk.genai
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: alphabetical
      filters:
        - "!^_"

### `tempest_fastapi_sdk.genai.rag`

::: tempest_fastapi_sdk.genai.rag
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: alphabetical
      filters:
        - "!^_"

### `tempest_fastapi_sdk.genai.audio`

::: tempest_fastapi_sdk.genai.audio
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: alphabetical
      filters:
        - "!^_"

### `tempest_fastapi_sdk.genai.hub`

::: tempest_fastapi_sdk.genai.hub
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

### `tempest_fastapi_sdk.genai.inventory`

::: tempest_fastapi_sdk.genai.inventory
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

### `tempest_fastapi_sdk.genai.image`

::: tempest_fastapi_sdk.genai.image
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

---

## OpenPix

### `tempest_fastapi_sdk.openpix`

::: tempest_fastapi_sdk.openpix.environment.OpenPixEnvironment
::: tempest_fastapi_sdk.openpix.events.OpenPixEvent
::: tempest_fastapi_sdk.openpix.money.to_cents
::: tempest_fastapi_sdk.openpix.money.reais_to_cents
::: tempest_fastapi_sdk.openpix.money.cents_to_reais
::: tempest_fastapi_sdk.openpix.webhooks.OpenPixWebhookEvent
::: tempest_fastapi_sdk.openpix.webhooks.make_openpix_webhook_dependency
::: tempest_fastapi_sdk.openpix.webhooks.webhook_verifier
::: tempest_fastapi_sdk.openpix.webhooks.decode_public_key

## OpenAPI code generation

### `tempest_fastapi_sdk.openapi`

::: tempest_fastapi_sdk.openapi.generate.generate_integration
::: tempest_fastapi_sdk.openapi.generate.GenerationResult
::: tempest_fastapi_sdk.openapi.generate.default_output_dir
::: tempest_fastapi_sdk.openapi.loader.load_spec
::: tempest_fastapi_sdk.openapi.loader.SpecError
::: tempest_fastapi_sdk.openapi.parse.parse_spec
::: tempest_fastapi_sdk.openapi.emit_schemas.emit_schemas
::: tempest_fastapi_sdk.openapi.emit_client.emit_client
::: tempest_fastapi_sdk.openapi.ir.SpecIR
::: tempest_fastapi_sdk.openapi.ir.SchemaIR
::: tempest_fastapi_sdk.openapi.ir.FieldIR
::: tempest_fastapi_sdk.openapi.ir.ClientIR
::: tempest_fastapi_sdk.openapi.ir.OperationIR
::: tempest_fastapi_sdk.openapi.ir.ParameterIR
::: tempest_fastapi_sdk.openapi.loader.parse_header_options
::: tempest_fastapi_sdk.openapi.generate.suggest_client_class
::: tempest_fastapi_sdk.api.middlewares.RequestIDMiddleware
::: tempest_fastapi_sdk.api.middlewares.idempotency.IdempotencyMiddleware
::: tempest_fastapi_sdk.api.middlewares.idempotency.MemoryIdempotencyStore
::: tempest_fastapi_sdk.api.middlewares.idempotency.RedisIdempotencyStore
::: tempest_fastapi_sdk.api.middlewares.idempotency.IDEMPOTENCY_HEADER
::: tempest_fastapi_sdk.api.middlewares.body_size.BodySizeLimitMiddleware
::: tempest_fastapi_sdk.api.middlewares.graceful.GracefulShutdownMiddleware
::: tempest_fastapi_sdk.api.middlewares.csrf.CSRFMiddleware
::: tempest_fastapi_sdk.api.middlewares.csrf.make_csrf_token_dependency
::: tempest_fastapi_sdk.api.middlewares.csrf.generate_csrf_token
::: tempest_fastapi_sdk.api.middlewares.rate_limit.RateLimitMiddleware
::: tempest_fastapi_sdk.api.middlewares.rate_limit.RateLimitStore
::: tempest_fastapi_sdk.api.middlewares.rate_limit.RateLimitResult
::: tempest_fastapi_sdk.api.middlewares.rate_limit.MemoryRateLimitStore
::: tempest_fastapi_sdk.api.middlewares.rate_limit.RedisRateLimitStore
::: tempest_fastapi_sdk.api.middlewares.rate_limit.key_by_ip
::: tempest_fastapi_sdk.api.middlewares.rate_limit.key_by_jwt_subject
::: tempest_fastapi_sdk.api.middlewares.rate_limit.key_by_jwt_claim
::: tempest_fastapi_sdk.api.middlewares.rate_limit.key_by_header
::: tempest_fastapi_sdk.utils.storage_backends.LocalUploadStorage
::: tempest_fastapi_sdk.utils.storage_backends.MinIOUploadStorage
::: tempest_fastapi_sdk.utils.http_client.HTTPClient
::: tempest_fastapi_sdk.utils.http_client.RetryPolicy
::: tempest_fastapi_sdk.utils.http_client.CircuitOpenError
::: tempest_fastapi_sdk.api.oauth.GoogleOAuthClient
::: tempest_fastapi_sdk.api.oauth.GitHubOAuthClient
::: tempest_fastapi_sdk.api.oauth.OIDCProvider
::: tempest_fastapi_sdk.api.oauth.OAuthUser
::: tempest_fastapi_sdk.api.oauth.OAuthTokens
::: tempest_fastapi_sdk.api.oauth.OAuthError
::: tempest_fastapi_sdk.api.oauth.generate_oauth_state
::: tempest_fastapi_sdk.api.middlewares.cors.apply_cors
::: tempest_fastapi_sdk.api.routers.health.make_health_router
::: tempest_fastapi_sdk.api.routers.logs.make_logs_router
::: tempest_fastapi_sdk.api.routers.logs.DEFAULT_MAX_RECORDS_PER_FILE
::: tempest_fastapi_sdk.api.routers.logs.render_entries_markdown
::: tempest_fastapi_sdk.api.routers.logs.render_entries_json
::: tempest_fastapi_sdk.api.routers.metrics.PrometheusMiddleware
::: tempest_fastapi_sdk.api.routers.metrics.make_prometheus_router
::: tempest_fastapi_sdk.api.routers.metrics.make_prometheus_registry
::: tempest_fastapi_sdk.api.routers.metrics.BusinessMetrics
::: tempest_fastapi_sdk.api.routers.metrics.DEFAULT_LATENCY_BUCKETS
::: tempest_fastapi_sdk.api.cookies.set_cookie
::: tempest_fastapi_sdk.api.cookies.clear_cookie
::: tempest_fastapi_sdk.api.cookies.SameSite
::: tempest_fastapi_sdk.api.static.HardenedStaticFiles
::: tempest_fastapi_sdk.api.static.DEFAULT_STATIC_SECURITY_HEADERS
::: tempest_fastapi_sdk.api.spa.make_spa_router
::: tempest_fastapi_sdk.api.spa.DEFAULT_ASSET_CACHE_CONTROL
::: tempest_fastapi_sdk.api.spa.DEFAULT_DOCUMENT_CACHE_CONTROL
::: tempest_fastapi_sdk.api.spa.DEFAULT_EXCLUDED_PREFIXES
::: tempest_fastapi_sdk.api.webhooks.WebhookSender
::: tempest_fastapi_sdk.api.webhooks.WebhookDelivery
::: tempest_fastapi_sdk.api.tracing.setup_tracing

### `tempest_fastapi_sdk.auth`

::: tempest_fastapi_sdk.auth.service.UserAuthService
::: tempest_fastapi_sdk.auth.introspection.IntrospectionAuth
::: tempest_fastapi_sdk.auth.router.make_auth_router
::: tempest_fastapi_sdk.auth.schemas.SignupSchema
::: tempest_fastapi_sdk.auth.schemas.SignupResponseSchema
::: tempest_fastapi_sdk.auth.schemas.LoginSchema
::: tempest_fastapi_sdk.auth.schemas.LoginResponseSchema
::: tempest_fastapi_sdk.auth.schemas.ActivationResponseSchema
::: tempest_fastapi_sdk.auth.schemas.PasswordResetRequestSchema
::: tempest_fastapi_sdk.auth.schemas.PasswordResetResponseSchema
::: tempest_fastapi_sdk.auth.schemas.PasswordResetConfirmSchema
::: tempest_fastapi_sdk.auth.schemas.ActivationToken
::: tempest_fastapi_sdk.auth.schemas.PasswordResetToken
::: tempest_fastapi_sdk.auth.schemas.MFAEnrollResponseSchema
::: tempest_fastapi_sdk.auth.schemas.MFAConfirmSchema
::: tempest_fastapi_sdk.auth.schemas.MFAVerifySchema
::: tempest_fastapi_sdk.auth.schemas.MFADisableSchema

### `tempest_fastapi_sdk.authz`

::: tempest_fastapi_sdk.authz.permissions.PermissionRegistry
::: tempest_fastapi_sdk.authz.permissions.has_perm
::: tempest_fastapi_sdk.authz.permissions.check_permission
::: tempest_fastapi_sdk.authz.permissions.permission
::: tempest_fastapi_sdk.authz.permissions.PermissionMixin
::: tempest_fastapi_sdk.authz.permissions.default_registry
::: tempest_fastapi_sdk.authz.dependencies.make_permission_checker
::: tempest_fastapi_sdk.authz.requires.requires
::: tempest_fastapi_sdk.authz.requires.Guard
::: tempest_fastapi_sdk.authz.requires.declared_guards
::: tempest_fastapi_sdk.authz.requires.guard_metadata
::: tempest_fastapi_sdk.authz.requires.guarded_user_param
::: tempest_fastapi_sdk.authz.requires.TempestPermissionError
::: tempest_fastapi_sdk.authz.requires.GuardContractWarning

### `tempest_fastapi_sdk.sessions`

::: tempest_fastapi_sdk.sessions.service.SessionAuth
::: tempest_fastapi_sdk.sessions.router.make_session_router
::: tempest_fastapi_sdk.sessions.middleware.SessionMiddleware
::: tempest_fastapi_sdk.sessions.dependencies.make_session_dependency
::: tempest_fastapi_sdk.sessions.store.SessionStore
::: tempest_fastapi_sdk.sessions.store.MemorySessionStore
::: tempest_fastapi_sdk.sessions.store.RedisSessionStore
::: tempest_fastapi_sdk.sessions.schemas.Session
::: tempest_fastapi_sdk.sessions.schemas.SessionLoginSchema
::: tempest_fastapi_sdk.sessions.schemas.SessionResponseSchema
::: tempest_fastapi_sdk.sessions.schemas.SessionSummarySchema

### `tempest_fastapi_sdk.storage`

::: tempest_fastapi_sdk.storage.minio_client.AsyncMinIOClient
::: tempest_fastapi_sdk.storage.minio_client.ObjectStat

### Alembic hooks

::: tempest_fastapi_sdk.db.alembic_hooks.reorder_base_columns_first
::: tempest_fastapi_sdk.db.alembic_hooks.backfill_non_nullable_defaults
::: tempest_fastapi_sdk.db.alembic_hooks.compose_hooks

---

## Schemas

### `tempest_fastapi_sdk.schemas`

::: tempest_fastapi_sdk.schemas.base.BaseSchema
::: tempest_fastapi_sdk.schemas.response.BaseResponseSchema
::: tempest_fastapi_sdk.schemas.pagination.BasePaginationFilterSchema
::: tempest_fastapi_sdk.schemas.pagination.BasePaginationSchema
::: tempest_fastapi_sdk.schemas.pagination.CursorPaginationFilterSchema
::: tempest_fastapi_sdk.schemas.pagination.CursorPaginationSchema
::: tempest_fastapi_sdk.schemas.pagination.SyncFilterSchema
::: tempest_fastapi_sdk.schemas.pagination.SyncPaginationSchema
::: tempest_fastapi_sdk.schemas.logs.LogEntrySchema
::: tempest_fastapi_sdk.schemas.errors.ErrorResponseSchema

---

## Server-Sent Events

### `tempest_fastapi_sdk.sse`

::: tempest_fastapi_sdk.sse.event_stream.EventStream
::: tempest_fastapi_sdk.sse.broker.SSEBroker
::: tempest_fastapi_sdk.sse.event_stream.ServerSentEvent
::: tempest_fastapi_sdk.sse.event_stream.sse_response

---

## Services & Controllers

::: tempest_fastapi_sdk.services.base.BaseService
::: tempest_fastapi_sdk.services.file_mixin.StoredFileServiceMixin
::: tempest_fastapi_sdk.controllers.base.BaseController

---

## Settings

### `tempest_fastapi_sdk.settings`

::: tempest_fastapi_sdk.settings.base.BaseAppSettings
::: tempest_fastapi_sdk.settings.base.AppSettingsMeta
::: tempest_fastapi_sdk.settings.mixins

---

## SSR / web

### `tempest_fastapi_sdk.ssr`

::: tempest_fastapi_sdk.ssr
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

::: tempest_fastapi_sdk.ssr.webapp.BuildMode
---

## System checks

### `tempest_fastapi_sdk.checks`

::: tempest_fastapi_sdk.checks.messages.CheckLevel
::: tempest_fastapi_sdk.checks.messages.CheckMessage
::: tempest_fastapi_sdk.checks.registry.CheckRegistry
::: tempest_fastapi_sdk.checks.registry.check
::: tempest_fastapi_sdk.checks.registry.register_check
::: tempest_fastapi_sdk.checks.registry.run_checks
::: tempest_fastapi_sdk.checks.registry.run_system_checks
::: tempest_fastapi_sdk.checks.registry.SystemCheckError

---

## Testing

### `tempest_fastapi_sdk.testing`

::: tempest_fastapi_sdk.testing.factories.ModelFactory
::: tempest_fastapi_sdk.testing.factories.seq

---

## Utils

### `tempest_fastapi_sdk.utils`

::: tempest_fastapi_sdk.utils.password.PasswordUtils
::: tempest_fastapi_sdk.utils.jwt.JWTUtils
::: tempest_fastapi_sdk.utils.token_types.token_type_allowed
::: tempest_fastapi_sdk.utils.totp.TOTPHelper
::: tempest_fastapi_sdk.utils.email.EmailUtils
::: tempest_fastapi_sdk.utils.upload.UploadUtils
::: tempest_fastapi_sdk.utils.download.DownloadUtils
::: tempest_fastapi_sdk.utils.file_store.FileStoreUtils
::: tempest_fastapi_sdk.utils.metrics.MetricsUtils
::: tempest_fastapi_sdk.utils.log.LogUtils
::: tempest_fastapi_sdk.utils.throttle.AttemptThrottle
::: tempest_fastapi_sdk.utils.locations.UF
::: tempest_fastapi_sdk.utils.locations.Region
::: tempest_fastapi_sdk.utils.locations.StateBR
::: tempest_fastapi_sdk.utils.locations.CityBR
::: tempest_fastapi_sdk.utils.locations.list_states
::: tempest_fastapi_sdk.utils.locations.get_state
::: tempest_fastapi_sdk.utils.locations.cities_by_uf
::: tempest_fastapi_sdk.utils.locations.states_by_region
::: tempest_fastapi_sdk.utils.locations.is_valid_uf
::: tempest_fastapi_sdk.utils.locations.normalize_uf
::: tempest_fastapi_sdk.utils.locations.is_valid_city
::: tempest_fastapi_sdk.utils.locations.normalize_city
::: tempest_fastapi_sdk.utils.locations.uf_choices
::: tempest_fastapi_sdk.utils.locations.region_choices
::: tempest_fastapi_sdk.utils.locations.city_choices

## Web Push

### `tempest_fastapi_sdk.webpush`

::: tempest_fastapi_sdk.webpush.dispatcher.WebPushDispatcher
::: tempest_fastapi_sdk.webpush.service.WebPushSubscriptionService
::: tempest_fastapi_sdk.webpush.router.make_web_push_router
::: tempest_fastapi_sdk.db.webpush_subscription_model.BaseWebPushSubscriptionModel
::: tempest_fastapi_sdk.webpush.schemas.WebPushSubscriptionSchema
::: tempest_fastapi_sdk.webpush.schemas.WebPushPayloadSchema

---

## WebSocket

### `tempest_fastapi_sdk.websockets`

::: tempest_fastapi_sdk.websockets.hub.WebSocketHub
::: tempest_fastapi_sdk.websockets.hub.WebSocketConnection
::: tempest_fastapi_sdk.websockets.router.make_websocket_router
::: tempest_fastapi_sdk.websockets.schemas.WSEnvelope

---
