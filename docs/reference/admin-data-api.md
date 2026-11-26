# Referência: Admin, dados e API

O painel admin, a camada de dados (models, repository, sessão, migrations), a integração com o FastAPI, as exceções, os schemas base e as settings.

[« Índice da referência](../reference.md)

---

## Admin

### `tempest_fastapi_sdk.admin`

::: tempest_fastapi_sdk.admin.site.AdminSite
::: tempest_fastapi_sdk.admin.config.AdminModel
::: tempest_fastapi_sdk.admin.config.ChoiceSource
::: tempest_fastapi_sdk.admin.config.EditValidator
::: tempest_fastapi_sdk.admin.config.ReadonlyFieldsFor
::: tempest_fastapi_sdk.admin.config.AdminRowScope
::: tempest_fastapi_sdk.admin.config.AdminRowScopeError
::: tempest_fastapi_sdk.admin.config.AdminCountStrategyEnum
::: tempest_fastapi_sdk.admin.config.AdminCountStrategy
::: tempest_fastapi_sdk.admin.impersonation.AdminImpersonateHandler
::: tempest_fastapi_sdk.admin.impersonation.AdminImpersonateAccount
::: tempest_fastapi_sdk.admin.impersonation.make_jwt_impersonation_handler
::: tempest_fastapi_sdk.admin.impersonation.ADMIN_IMPERSONATION_TTL
::: tempest_fastapi_sdk.admin.impersonation.ADMIN_IMPERSONATION_EVENT
::: tempest_fastapi_sdk.admin.impersonation.ADMIN_IMPERSONATION_FAILED_EVENT
::: tempest_fastapi_sdk.admin.impersonation.IMPERSONATION_TOKEN_PLACEHOLDER
::: tempest_fastapi_sdk.admin.i18n.AdminLocale
::: tempest_fastapi_sdk.admin.i18n.ADMIN_LOCALES
::: tempest_fastapi_sdk.admin.i18n.DEFAULT_ADMIN_LOCALE
::: tempest_fastapi_sdk.admin.i18n.ADMIN_MESSAGES
::: tempest_fastapi_sdk.admin.i18n.admin_text
::: tempest_fastapi_sdk.admin.i18n.admin_plural
::: tempest_fastapi_sdk.admin.csv_import.ADMIN_IMPORT_MAX_BYTES
::: tempest_fastapi_sdk.admin.csv_import.ADMIN_IMPORT_PREVIEW_MAX_AGE_SECONDS
::: tempest_fastapi_sdk.admin.csv_import.ADMIN_IMPORT_PREVIEW_ROWS
::: tempest_fastapi_sdk.admin.config.Inline
::: tempest_fastapi_sdk.admin.config.Lens
::: tempest_fastapi_sdk.admin.config.RelatedCount
::: tempest_fastapi_sdk.admin.permissions.AdminPermissionEnum
::: tempest_fastapi_sdk.admin.permissions.AdminAccessPolicy
::: tempest_fastapi_sdk.admin.permissions.AdminActionPolicy
::: tempest_fastapi_sdk.admin.permissions.AdminSurfaceEnum
::: tempest_fastapi_sdk.admin.permissions.AdminSurfacePolicy
::: tempest_fastapi_sdk.admin.dashboard.MetricCard
::: tempest_fastapi_sdk.admin.dashboard.MetricValue
::: tempest_fastapi_sdk.admin.dashboard.MetricTrend
::: tempest_fastapi_sdk.admin.dashboard.MetricPartition
::: tempest_fastapi_sdk.admin.metrics.MetricsArea
::: tempest_fastapi_sdk.admin.metrics.MetricsProvider
::: tempest_fastapi_sdk.admin.metrics.Threshold
::: tempest_fastapi_sdk.admin.metrics.MetricLevelEnum
::: tempest_fastapi_sdk.admin.actions.admin_action
::: tempest_fastapi_sdk.admin.actions.resolve_admin_action
::: tempest_fastapi_sdk.admin.actions.ActionHandler
::: tempest_fastapi_sdk.admin.actions.SchemaActionHandler
::: tempest_fastapi_sdk.admin.actions.AnyActionHandler
::: tempest_fastapi_sdk.admin.actions.AdminActionContext
::: tempest_fastapi_sdk.admin.actions.AdminActionResult
::: tempest_fastapi_sdk.admin.actions.AdminSecret
::: tempest_fastapi_sdk.admin.actions.AdminCreateHandler
::: tempest_fastapi_sdk.admin.actions.CreateHandlerFunc
::: tempest_fastapi_sdk.admin.manual_payout.manual_payout_admin
::: tempest_fastapi_sdk.admin.manual_payout.make_mark_paid_action
::: tempest_fastapi_sdk.admin.manual_payout.make_reject_action
::: tempest_fastapi_sdk.admin.manual_payout.MARK_PAID_ACTION
::: tempest_fastapi_sdk.admin.manual_payout.RECEIPT_CONTENT_TYPES
::: tempest_fastapi_sdk.admin.manual_payout.REJECT_ACTION
::: tempest_fastapi_sdk.admin.manual_payout.STATUS_LABELS
::: tempest_fastapi_sdk.admin.mercado_pago_simulation.mercado_pago_simulation_actions
::: tempest_fastapi_sdk.admin.mercado_pago_simulation.DANGEROUS_TRANSITIONS
::: tempest_fastapi_sdk.admin.mercado_pago_simulation.DEFAULT_SIMULATION_TRANSITIONS
::: tempest_fastapi_sdk.admin.mercado_pago_simulation.SIMULATION_ACTION_LABELS
::: tempest_fastapi_sdk.admin.mercado_pago_simulation.SIMULATION_ACTION_PREFIX
::: tempest_fastapi_sdk.admin.auth.AdminAuthBackend
::: tempest_fastapi_sdk.admin.auth.UserModelAuthBackend
::: tempest_fastapi_sdk.admin.router.make_admin_router
::: tempest_fastapi_sdk.admin.router.ADMIN_SEARCH_RESULTS_PER_MODEL
::: tempest_fastapi_sdk.admin.router.ADMIN_SEARCH_MAX_QUERY_LENGTH
::: tempest_fastapi_sdk.admin.router.ADMIN_SEARCH_MAX_TERMS
::: tempest_fastapi_sdk.admin.discovery.discover_models
::: tempest_fastapi_sdk.admin.session.AdminSession
::: tempest_fastapi_sdk.admin.session.AdminSessionRevocations
::: tempest_fastapi_sdk.admin.session.AdminSubmissionNonces
::: tempest_fastapi_sdk.admin.session.admin_credential_digest
::: tempest_fastapi_sdk.admin.session.SignedCookieSessionStore
::: tempest_fastapi_sdk.admin.session.AdminLoginCSRF

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

### `tempest_fastapi_sdk.admin.storage`

::: tempest_fastapi_sdk.admin.storage
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

---

### `tempest_fastapi_sdk.admin.tasks`

::: tempest_fastapi_sdk.admin.tasks
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

---

## API (integração FastAPI)

### `tempest_fastapi_sdk.api`

::: tempest_fastapi_sdk.api.handlers.register_exception_handlers
::: tempest_fastapi_sdk.api.handlers.make_app_exception_handler
::: tempest_fastapi_sdk.api.handlers.make_http_exception_handler
::: tempest_fastapi_sdk.api.handlers.make_unhandled_exception_handler
::: tempest_fastapi_sdk.api.redaction.redact_database_errors
::: tempest_fastapi_sdk.api.redaction.RedactedError
::: tempest_fastapi_sdk.api.redaction.ExceptionRedactor
::: tempest_fastapi_sdk.api.error_docs.error_responses
::: tempest_fastapi_sdk.api.error_docs.raises
::: tempest_fastapi_sdk.api.error_docs.TempestAPIRouter
::: tempest_fastapi_sdk.api.error_docs.RaisesSpec
::: tempest_fastapi_sdk.api.error_docs.declared_raises
::: tempest_fastapi_sdk.api.openapi_pruning.prune_orphan_enum_schemas
::: tempest_fastapi_sdk.api.openapi_pruning.drop_orphan_enum_schemas
::: tempest_fastapi_sdk.api.dependencies.signed_url.make_signed_path_dependency
::: tempest_fastapi_sdk.api.dependencies.auth.make_mfa_required_dependency
::: tempest_fastapi_sdk.api.route_docs.RouteDocs
::: tempest_fastapi_sdk.api.route_docs.RouteDoc
::: tempest_fastapi_sdk.api.route_docs.resolve_docs_locale

---

## Banco de dados

### `tempest_fastapi_sdk.db`

::: tempest_fastapi_sdk.db.model.BaseModel
::: tempest_fastapi_sdk.db.user_model.BaseUserModel
::: tempest_fastapi_sdk.db.user_token_model.BaseUserTokenModel
::: tempest_fastapi_sdk.db.user_token_model.make_user_token_model
::: tempest_fastapi_sdk.db.user_recovery_code_model.BaseUserRecoveryCodeModel
::: tempest_fastapi_sdk.db.user_recovery_code_model.make_user_recovery_code_model
::: tempest_fastapi_sdk.db.user_webauthn_credential_model.BaseWebAuthnCredentialModel
::: tempest_fastapi_sdk.db.user_webauthn_credential_model.make_web_authn_credential_model
::: tempest_fastapi_sdk.db.user_oauth_account_model.BaseUserOAuthAccountModel
::: tempest_fastapi_sdk.db.user_oauth_account_model.make_user_oauth_account_model
::: tempest_fastapi_sdk.db.repository.BaseRepository
::: tempest_fastapi_sdk.db.repository.DEFAULT_SYNC_WATERMARK_LAG
::: tempest_fastapi_sdk.db.integrity.parse_integrity_error
::: tempest_fastapi_sdk.db.integrity.describe_database_error
::: tempest_fastapi_sdk.db.integrity.WITHHELD_NOTICE
::: tempest_fastapi_sdk.db.integrity.IntegrityFailure
::: tempest_fastapi_sdk.db.integrity.IntegrityViolationEnum
::: tempest_fastapi_sdk.db.naming.legacy_constraint_renames
::: tempest_fastapi_sdk.db.naming.ConstraintRename
::: tempest_fastapi_sdk.db.naming.ConstraintKindEnum
::: tempest_fastapi_sdk.db.naming.LEGACY_NAMING_CONVENTION
::: tempest_fastapi_sdk.db.expressions.F
::: tempest_fastapi_sdk.db.expressions.Q
::: tempest_fastapi_sdk.db.expressions.build_filter_condition
::: tempest_fastapi_sdk.db.expressions.resolve_filter_key
::: tempest_fastapi_sdk.db.expressions.FILTER_OPERATORS
::: tempest_fastapi_sdk.db.expressions.DroppedFilterWarning
::: tempest_fastapi_sdk.db.signals.RepositorySignalEnum
::: tempest_fastapi_sdk.db.signals.connect
::: tempest_fastapi_sdk.db.signals.on_signal
::: tempest_fastapi_sdk.db.tenant.TenantScopedRepository
::: tempest_fastapi_sdk.db.mixins.SoftDeleteMixin
::: tempest_fastapi_sdk.db.datetime_type.UtcDateTime
::: tempest_fastapi_sdk.db.mixins.AuditMixin
::: tempest_fastapi_sdk.db.mixins.MFAMixin
::: tempest_fastapi_sdk.db.mixins.NameMixin
::: tempest_fastapi_sdk.db.mixins.LocaleColumnMixin
::: tempest_fastapi_sdk.db.connection.AsyncDatabaseManager
::: tempest_fastapi_sdk.db.connection.session_dependency_for
::: tempest_fastapi_sdk.db.migrations.AlembicHelper
::: tempest_fastapi_sdk.db.slow_query.SlowQueryLogger
::: tempest_fastapi_sdk.db.outbox.BaseOutboxModel
::: tempest_fastapi_sdk.db.outbox.OutboxRelay
::: tempest_fastapi_sdk.db.outbox.OutboxStatusEnum
::: tempest_fastapi_sdk.db.webhook_inbox.BaseWebhookEventModel
::: tempest_fastapi_sdk.db.webhook_inbox.WebhookInbox
::: tempest_fastapi_sdk.db.webhook_inbox.WebhookEventStatusEnum
::: tempest_fastapi_sdk.db.webhook_inbox.InboxDrainReport
::: tempest_fastapi_sdk.db.webhook_inbox.make_webhook_event_model
::: tempest_fastapi_sdk.db.webhook_inbox.DEFAULT_WEBHOOK_MAX_ATTEMPTS
::: tempest_fastapi_sdk.db.webhook_inbox.DEFAULT_WEBHOOK_BACKOFF_SECONDS
::: tempest_fastapi_sdk.db.webhook_inbox.STALE_WEBHOOK_ERROR
::: tempest_fastapi_sdk.db.audit.BaseAuditLogModel
::: tempest_fastapi_sdk.db.audit.AuditActionEnum
::: tempest_fastapi_sdk.db.audit.AuditRequestMixin
::: tempest_fastapi_sdk.db.audit.AuditRequestContext
::: tempest_fastapi_sdk.db.audit.snapshot_model
::: tempest_fastapi_sdk.db.audit.diff_snapshots
::: tempest_fastapi_sdk.db.audit.audit_redacted_columns
::: tempest_fastapi_sdk.db.audit.redact_snapshot
::: tempest_fastapi_sdk.db.audit.AUDIT_REDACTED
::: tempest_fastapi_sdk.db.audit.DEFAULT_AUDIT_REDACT
::: tempest_fastapi_sdk.db.audit.AUDIT_EVENT_MAX_LENGTH
::: tempest_fastapi_sdk.db.audit.AUDIT_RESERVED_COLUMNS
::: tempest_fastapi_sdk.db.audit.AUDIT_IP_MAX_LENGTH
::: tempest_fastapi_sdk.db.audit.AUDIT_USER_AGENT_MAX_LENGTH
::: tempest_fastapi_sdk.db.migrations.DestructiveMigrationError
::: tempest_fastapi_sdk.db.migrations.AmbiguousBaseRevisionError
::: tempest_fastapi_sdk.db.migrations.SchemaSyncOutcomeEnum
::: tempest_fastapi_sdk.db.migrations.require_sqlite_foreign_keys_off
::: tempest_fastapi_sdk.db.alembic_env.run_alembic_env
::: tempest_fastapi_sdk.db.alembic_env.DEFAULT_REVISION_HOOKS
::: tempest_fastapi_sdk.db.connection.enable_sqlite_foreign_keys
::: tempest_fastapi_sdk.db.connection.enable_sqlite_savepoints
::: tempest_fastapi_sdk.db.connection.enable_sqlite_wal
::: tempest_fastapi_sdk.db.connection.is_memory_sqlite_url
::: tempest_fastapi_sdk.db.connection.shared_memory_url
::: tempest_fastapi_sdk.db.transaction.transaction
::: tempest_fastapi_sdk.db.transaction.savepoint
::: tempest_fastapi_sdk.db.transaction.in_transaction
::: tempest_fastapi_sdk.db.transaction.transaction_depth
::: tempest_fastapi_sdk.db.search.TextSearchLanguageEnum
::: tempest_fastapi_sdk.db.search.TextSearchWeightEnum
::: tempest_fastapi_sdk.db.search.TokenMatchEnum
::: tempest_fastapi_sdk.db.search.like_search_condition
::: tempest_fastapi_sdk.db.search.full_text_condition
::: tempest_fastapi_sdk.db.search.full_text_rank
::: tempest_fastapi_sdk.db.search.supports_full_text
::: tempest_fastapi_sdk.db.enums.TempestEnum
::: tempest_fastapi_sdk.db.enums.enum_column
::: tempest_fastapi_sdk.db.enums.enum_values
::: tempest_fastapi_sdk.db.enums.enum_type_name
::: tempest_fastapi_sdk.db.enum_migrations.ReplaceEnumOp
::: tempest_fastapi_sdk.db.enum_migrations.RenameEnumTypeOp
::: tempest_fastapi_sdk.db.enum_migrations.EnumColumnRef
::: tempest_fastapi_sdk.db.enum_migrations.EnumTypeState
::: tempest_fastapi_sdk.db.enum_migrations.render_enum_types
::: tempest_fastapi_sdk.db.enum_migrations.sync_enum_types
::: tempest_fastapi_sdk.db.enum_migrations.CreateEnumCheckOp
::: tempest_fastapi_sdk.db.enum_migrations.DropEnumCheckOp
::: tempest_fastapi_sdk.db.enum_migrations.sqlite_enum_checks
::: tempest_fastapi_sdk.db.enum_migrations.CreateEnumTypeOp
::: tempest_fastapi_sdk.db.enum_migrations.create_enum_types_on_add_column
::: tempest_fastapi_sdk.db.enum_migrations.DropEnumTypeOp
::: tempest_fastapi_sdk.db.enum_migrations.drop_enum_types_on_downgrade
::: tempest_fastapi_sdk.db.enum_migrations.type_bound_check_include_object
::: tempest_fastapi_sdk.db.explain.explain_queries
::: tempest_fastapi_sdk.db.explain.ExplainReport
::: tempest_fastapi_sdk.db.explain.QueryPlan
::: tempest_fastapi_sdk.db.explain.ExplainDetailEnum

---

## Cache

::: tempest_fastapi_sdk.cache.redis_manager.AsyncRedisManager
::: tempest_fastapi_sdk.cache.decorator.cached
::: tempest_fastapi_sdk.cache.invalidation.CacheInvalidator

---

## Core

### `tempest_fastapi_sdk.core`

::: tempest_fastapi_sdk.core.typed.strict_types
::: tempest_fastapi_sdk.core.typed.typed
::: tempest_fastapi_sdk.core.typed.require_annotations
::: tempest_fastapi_sdk.core.docs_locale.normalize_docs_locale
::: tempest_fastapi_sdk.core.docs_locale.DocsLocale

---

## Exceções

### `tempest_fastapi_sdk.exceptions`

::: tempest_fastapi_sdk.exceptions.base.AppException
::: tempest_fastapi_sdk.exceptions.base.InheritedErrorCodeWarning
::: tempest_fastapi_sdk.exceptions.not_found.NotFoundException
::: tempest_fastapi_sdk.exceptions.conflict.ConflictException
::: tempest_fastapi_sdk.exceptions.factories.not_found_exception
::: tempest_fastapi_sdk.exceptions.factories.conflict_exception
::: tempest_fastapi_sdk.exceptions.unauthorized.UnauthorizedException
::: tempest_fastapi_sdk.exceptions.forbidden.ForbiddenException
::: tempest_fastapi_sdk.exceptions.forbidden.MFARequiredException
::: tempest_fastapi_sdk.exceptions.validation.ValidationException
::: tempest_fastapi_sdk.exceptions.validation.OrderByNotAllowedException
::: tempest_fastapi_sdk.exceptions.validation.PageSizeTooLargeException
::: tempest_fastapi_sdk.exceptions.validation.PushEndpointNotPublicException
::: tempest_fastapi_sdk.exceptions.validation.UnknownFilterKeyException
::: tempest_fastapi_sdk.exceptions.validation.InvalidFilterValueException
::: tempest_fastapi_sdk.exceptions.value_errors.ValidationValueError
::: tempest_fastapi_sdk.exceptions.too_many_requests.TooManyRequestsException
::: tempest_fastapi_sdk.exceptions.integration.IntegrationDisabledException
::: tempest_fastapi_sdk.exceptions.integration.EmailDeliveryException
::: tempest_fastapi_sdk.exceptions.integration.ZapSendFailedException
::: tempest_fastapi_sdk.exceptions.integration.GoogleDriveException
::: tempest_fastapi_sdk.exceptions.integration.GoogleDriveFolderNotFoundException
::: tempest_fastapi_sdk.exceptions.storage.StorageIntegrityException
::: tempest_fastapi_sdk.exceptions.storage.PresignedUrlUnsupportedException
::: tempest_fastapi_sdk.exceptions.jwt.InvalidTokenException
::: tempest_fastapi_sdk.exceptions.jwt.ExpiredTokenException
::: tempest_fastapi_sdk.exceptions.signed_url.InvalidSignedURLException
::: tempest_fastapi_sdk.exceptions.signed_url.ExpiredSignedURLException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthProviderNotConfiguredException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthProviderDeniedException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthStateMismatchException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthCodeMissingException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthEmailMissingException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthEmailTakenException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthEmailUnverifiedException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthRegistrationDisabledException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthAccountInactiveException
::: tempest_fastapi_sdk.exceptions.oauth.OAuthAccountNotLinkedException
::: tempest_fastapi_sdk.exceptions.upload.FileTooLargeException
::: tempest_fastapi_sdk.exceptions.upload.FileTooSmallException
::: tempest_fastapi_sdk.exceptions.upload.InvalidFileTypeException
::: tempest_fastapi_sdk.exceptions.upload.UploadTooLargeException
::: tempest_fastapi_sdk.exceptions.tus.TusVersionException
::: tempest_fastapi_sdk.exceptions.tus.TusUploadNotFoundException
::: tempest_fastapi_sdk.exceptions.tus.TusUploadExpiredException
::: tempest_fastapi_sdk.exceptions.tus.TusOffsetMismatchException
::: tempest_fastapi_sdk.exceptions.tus.TusContentTypeException
::: tempest_fastapi_sdk.exceptions.tus.TusInvalidHeaderException
::: tempest_fastapi_sdk.exceptions.tus.TusUploadTooLargeException
::: tempest_fastapi_sdk.exceptions.tus.TusIdempotencyKeyReusedException
::: tempest_fastapi_sdk.exceptions.tus.TusBodyTimeoutException
::: tempest_fastapi_sdk.exceptions.tus.TusChunkTooSmallException
::: tempest_fastapi_sdk.exceptions.tus.TusQuotaExceededException
::: tempest_fastapi_sdk.exceptions.tus.TusUploadCompletedException
::: tempest_fastapi_sdk.exceptions.tus.TusUploadLockedException
::: tempest_fastapi_sdk.exceptions.image.ImageDecodeException
::: tempest_fastapi_sdk.exceptions.image.ImageTypeMismatchException
::: tempest_fastapi_sdk.exceptions.image.ImageFormatUnsupportedException
::: tempest_fastapi_sdk.exceptions.image.ImageTooLargeException
::: tempest_fastapi_sdk.exceptions.wallet.InsufficientBalanceException
::: tempest_fastapi_sdk.exceptions.wallet.ManualPayoutNotFoundException
::: tempest_fastapi_sdk.exceptions.wallet.ManualPayoutNotPendingException
::: tempest_fastapi_sdk.exceptions.wallet.ManualPayoutReceiptMissingException
::: tempest_fastapi_sdk.exceptions.wallet.PayoutRejectedException
::: tempest_fastapi_sdk.exceptions.wallet.PayoutUncertainException
::: tempest_fastapi_sdk.exceptions.payment.PixRefundExceedsBalanceException
::: tempest_fastapi_sdk.exceptions.payment.PixRefundReferenceConflictException
::: tempest_fastapi_sdk.exceptions.eventsourcing.WrongExpectedVersionException
::: tempest_fastapi_sdk.exceptions.i18n.MessageCatalog
::: tempest_fastapi_sdk.exceptions.i18n.default_message_catalog
::: tempest_fastapi_sdk.exceptions.i18n.parse_accept_language

---

## Schemas

### `tempest_fastapi_sdk.schemas`

::: tempest_fastapi_sdk.schemas.base.BaseSchema
::: tempest_fastapi_sdk.schemas.response.BaseResponseSchema
::: tempest_fastapi_sdk.schemas.pagination.DEFAULT_MAX_PAGE_SIZE
::: tempest_fastapi_sdk.schemas.pagination.DEFAULT_MAX_CURSOR_LIMIT
::: tempest_fastapi_sdk.schemas.pagination.BasePaginationFilterSchema
::: tempest_fastapi_sdk.schemas.pagination.BasePaginationSchema
::: tempest_fastapi_sdk.schemas.pagination.CursorPaginationFilterSchema
::: tempest_fastapi_sdk.schemas.pagination.CursorPaginationSchema
::: tempest_fastapi_sdk.schemas.pagination.SyncFilterSchema
::: tempest_fastapi_sdk.schemas.search.DEFAULT_MAX_SEARCH_TERM
::: tempest_fastapi_sdk.schemas.search.BaseSearchSchema
::: tempest_fastapi_sdk.schemas.pagination.SyncPaginationSchema
::: tempest_fastapi_sdk.schemas.logs.LogEntrySchema
::: tempest_fastapi_sdk.schemas.logs.LogFilesClearedSchema
::: tempest_fastapi_sdk.schemas.errors.ErrorResponseSchema

---

## Services & Controllers

::: tempest_fastapi_sdk.services.base.BaseService
::: tempest_fastapi_sdk.services.file_mixin.StoredFileServiceMixin
::: tempest_fastapi_sdk.controllers.base.Controller
::: tempest_fastapi_sdk.controllers.base.BaseController

---

## Settings

### `tempest_fastapi_sdk.settings`

::: tempest_fastapi_sdk.settings.base.BaseAppSettings
::: tempest_fastapi_sdk.settings.base.AppSettingsMeta
::: tempest_fastapi_sdk.settings.base.reject_conflicting_env_aliases
::: tempest_fastapi_sdk.settings.version.read_project_version
::: tempest_fastapi_sdk.settings.kwargs
::: tempest_fastapi_sdk.settings.mixins
::: tempest_fastapi_sdk.settings.google_drive.GoogleDriveSettings
::: tempest_fastapi_sdk.settings.google_drive.GoogleDriveStorageKwargs
