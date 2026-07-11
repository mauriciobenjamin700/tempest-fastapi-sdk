# Referência da API

Gerada automaticamente a partir das docstrings do SDK via [`mkdocstrings`](https://mkdocstrings.github.io/). Todo símbolo público exportado por `tempest_fastapi_sdk` está documentado aqui com sua assinatura completa, parâmetros, tipo de retorno, exceções levantadas e localização no código-fonte.

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
        - "!^[a-z_]+$"

---

## Core

### `tempest_fastapi_sdk.core`

::: tempest_fastapi_sdk.core.typed.strict_types
::: tempest_fastapi_sdk.core.typed.typed
::: tempest_fastapi_sdk.core.typed.require_annotations

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
::: tempest_fastapi_sdk.db.signals.RepositorySignal
::: tempest_fastapi_sdk.db.signals.connect
::: tempest_fastapi_sdk.db.signals.on_signal
::: tempest_fastapi_sdk.db.tenant.TenantScopedRepository
::: tempest_fastapi_sdk.db.mixins.SoftDeleteMixin
::: tempest_fastapi_sdk.db.mixins.AuditMixin
::: tempest_fastapi_sdk.db.mixins.MFAMixin
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

---

## Services & Controllers

::: tempest_fastapi_sdk.services.base.BaseService
::: tempest_fastapi_sdk.services.file_mixin.StoredFileServiceMixin
::: tempest_fastapi_sdk.controllers.base.BaseController

---

## Exceções

### `tempest_fastapi_sdk.exceptions`

::: tempest_fastapi_sdk.exceptions.base.AppException
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

## API (integração FastAPI)

### `tempest_fastapi_sdk.api`

::: tempest_fastapi_sdk.api.handlers.register_exception_handlers
::: tempest_fastapi_sdk.api.handlers.make_app_exception_handler
::: tempest_fastapi_sdk.api.handlers.make_http_exception_handler
::: tempest_fastapi_sdk.api.handlers.make_unhandled_exception_handler
::: tempest_fastapi_sdk.api.middlewares.RequestIDMiddleware
::: tempest_fastapi_sdk.api.middlewares.idempotency.IdempotencyMiddleware
::: tempest_fastapi_sdk.api.middlewares.idempotency.MemoryIdempotencyStore
::: tempest_fastapi_sdk.api.middlewares.idempotency.RedisIdempotencyStore
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
::: tempest_fastapi_sdk.api.middlewares.cors.apply_cors
::: tempest_fastapi_sdk.api.routers.health.make_health_router
::: tempest_fastapi_sdk.api.routers.logs.make_logs_router
::: tempest_fastapi_sdk.api.routers.metrics.PrometheusMiddleware
::: tempest_fastapi_sdk.api.routers.metrics.make_prometheus_router
::: tempest_fastapi_sdk.api.routers.metrics.make_prometheus_registry
::: tempest_fastapi_sdk.api.tracing.setup_tracing

### `tempest_fastapi_sdk.auth`

::: tempest_fastapi_sdk.auth.service.UserAuthService
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
::: tempest_fastapi_sdk.authz.dependencies.make_permission_checker

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

## Settings

### `tempest_fastapi_sdk.settings`

::: tempest_fastapi_sdk.settings.base.BaseAppSettings
::: tempest_fastapi_sdk.settings.mixins

---

## Admin

### `tempest_fastapi_sdk.admin`

::: tempest_fastapi_sdk.admin.site.AdminSite
::: tempest_fastapi_sdk.admin.config.AdminModel
::: tempest_fastapi_sdk.admin.config.Inline
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

---

## Cache

::: tempest_fastapi_sdk.cache.redis_manager.AsyncRedisManager
::: tempest_fastapi_sdk.cache.decorator.cached
::: tempest_fastapi_sdk.cache.invalidation.CacheInvalidator

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

## Server-Sent Events

### `tempest_fastapi_sdk.sse`

::: tempest_fastapi_sdk.sse.event_stream.EventStream
::: tempest_fastapi_sdk.sse.broker.SSEBroker
::: tempest_fastapi_sdk.sse.event_stream.ServerSentEvent
::: tempest_fastapi_sdk.sse.event_stream.sse_response

---

## WebSocket

### `tempest_fastapi_sdk.websockets`

::: tempest_fastapi_sdk.websockets.hub.WebSocketHub
::: tempest_fastapi_sdk.websockets.hub.WebSocketConnection
::: tempest_fastapi_sdk.websockets.router.make_websocket_router
::: tempest_fastapi_sdk.websockets.schemas.WSEnvelope

---

## Web Push

### `tempest_fastapi_sdk.webpush`

::: tempest_fastapi_sdk.webpush.dispatcher.WebPushDispatcher
::: tempest_fastapi_sdk.webpush.service.WebPushSubscriptionService
::: tempest_fastapi_sdk.webpush.router.make_web_push_router
::: tempest_fastapi_sdk.db.webpush_subscription_model.BaseWebPushSubscriptionModel
::: tempest_fastapi_sdk.webpush.schemas.WebPushSubscriptionSchema
::: tempest_fastapi_sdk.webpush.schemas.WebPushPayloadSchema

---

## Computer vision

### `tempest_fastapi_sdk.vision`

::: tempest_fastapi_sdk.vision.schemas.DetectionSchema
::: tempest_fastapi_sdk.vision.schemas.ClassificationSchema
::: tempest_fastapi_sdk.vision.schemas.SegmentationSchema
::: tempest_fastapi_sdk.vision.schemas.BoundingBoxSchema
::: tempest_fastapi_sdk.vision.schemas.ClassProbabilitySchema
::: tempest_fastapi_sdk.vision.mapping.to_detection_schemas
::: tempest_fastapi_sdk.vision.mapping.to_classification_schema
::: tempest_fastapi_sdk.vision.mapping.to_segmentation_schemas

---

## Geolocalização

### `tempest_fastapi_sdk.geo`

::: tempest_fastapi_sdk.geo.schemas.Coordinate
::: tempest_fastapi_sdk.geo.schemas.TravelEstimate
::: tempest_fastapi_sdk.geo.enums.TravelMode
::: tempest_fastapi_sdk.geo.distance.haversine_km
::: tempest_fastapi_sdk.geo.estimate.estimate_travel
::: tempest_fastapi_sdk.geo.estimate.duration_factor
::: tempest_fastapi_sdk.geo.routing.RoutingBackend
::: tempest_fastapi_sdk.geo.routing.OSRMBackend

---

## Utils

### `tempest_fastapi_sdk.utils`

::: tempest_fastapi_sdk.utils.password.PasswordUtils
::: tempest_fastapi_sdk.utils.jwt.JWTUtils
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
