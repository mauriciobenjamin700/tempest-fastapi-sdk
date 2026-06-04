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

## Banco de dados

### `tempest_fastapi_sdk.db`

::: tempest_fastapi_sdk.db.model.BaseModel
::: tempest_fastapi_sdk.db.user_model.BaseUserModel
::: tempest_fastapi_sdk.db.repository.BaseRepository
::: tempest_fastapi_sdk.db.mixins.SoftDeleteMixin
::: tempest_fastapi_sdk.db.mixins.AuditMixin
::: tempest_fastapi_sdk.db.connection.AsyncDatabaseManager
::: tempest_fastapi_sdk.db.migrations.AlembicHelper

---

## Schemas

### `tempest_fastapi_sdk.schemas`

::: tempest_fastapi_sdk.schemas.base.BaseSchema
::: tempest_fastapi_sdk.schemas.response.BaseResponseSchema
::: tempest_fastapi_sdk.schemas.pagination.BasePaginationFilterSchema
::: tempest_fastapi_sdk.schemas.pagination.BasePaginationSchema
::: tempest_fastapi_sdk.schemas.pagination.CursorPaginationFilterSchema
::: tempest_fastapi_sdk.schemas.pagination.CursorPaginationSchema
::: tempest_fastapi_sdk.schemas.logs.LogEntrySchema

---

## Services & Controllers

::: tempest_fastapi_sdk.services.base.BaseService
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
::: tempest_fastapi_sdk.api.middlewares.csrf.CSRFMiddleware
::: tempest_fastapi_sdk.api.middlewares.csrf.make_csrf_token_dependency
::: tempest_fastapi_sdk.api.middlewares.csrf.generate_csrf_token
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

### `tempest_fastapi_sdk.storage`

::: tempest_fastapi_sdk.storage.minio_client.AsyncMinIOClient
::: tempest_fastapi_sdk.storage.minio_client.ObjectStat

### Alembic hooks

::: tempest_fastapi_sdk.db.alembic_hooks.reorder_base_columns_first
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
::: tempest_fastapi_sdk.admin.auth.AdminAuthBackend
::: tempest_fastapi_sdk.admin.auth.UserModelAuthBackend
::: tempest_fastapi_sdk.admin.router.make_admin_router

---

## Cache

::: tempest_fastapi_sdk.cache.redis_manager.AsyncRedisManager

---

## Server-Sent Events

### `tempest_fastapi_sdk.sse`

::: tempest_fastapi_sdk.sse.event_stream.EventStream
::: tempest_fastapi_sdk.sse.event_stream.ServerSentEvent
::: tempest_fastapi_sdk.sse.event_stream.sse_response

---

## Web Push

### `tempest_fastapi_sdk.webpush`

::: tempest_fastapi_sdk.webpush.dispatcher.WebPushDispatcher
::: tempest_fastapi_sdk.webpush.schemas.WebPushSubscriptionSchema
::: tempest_fastapi_sdk.webpush.schemas.WebPushPayloadSchema

---

## Utils

### `tempest_fastapi_sdk.utils`

::: tempest_fastapi_sdk.utils.password.PasswordUtils
::: tempest_fastapi_sdk.utils.jwt.JWTUtils
::: tempest_fastapi_sdk.utils.email.EmailUtils
::: tempest_fastapi_sdk.utils.upload.UploadUtils
::: tempest_fastapi_sdk.utils.metrics.MetricsUtils
::: tempest_fastapi_sdk.utils.log.LogUtils
::: tempest_fastapi_sdk.utils.throttle.AttemptThrottle
