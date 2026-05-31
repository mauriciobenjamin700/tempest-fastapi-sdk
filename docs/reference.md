# API Reference

Auto-generated from the SDK's docstrings via [`mkdocstrings`](https://mkdocstrings.github.io/). Every public symbol exported by `tempest_fastapi_sdk` is documented here with its full signature, parameters, return type, raised exceptions and source location.

!!! tip "Searching"
    Use the search bar at the top of the page (or press `/`) to jump to a symbol by name. The full-text index includes docstrings, so searches like "soft delete" or "request id" land on the right class.

---

## Top-level surface

::: tempest_fastapi_sdk
    options:
      members_order: source
      show_root_toc_entry: false
      show_submodules: false
      filters:
        - "!^_"
        - "!^[a-z_]+$"

---

## Database

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

---

## Services & Controllers

::: tempest_fastapi_sdk.services.base.BaseService
::: tempest_fastapi_sdk.controllers.base.BaseController

---

## Exceptions

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

## API (FastAPI integration)

### `tempest_fastapi_sdk.api`

::: tempest_fastapi_sdk.api.handlers.register_exception_handlers
::: tempest_fastapi_sdk.api.middlewares.RequestIDMiddleware
::: tempest_fastapi_sdk.api.middlewares.cors.apply_cors
::: tempest_fastapi_sdk.api.routers.health.make_health_router

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
