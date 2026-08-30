"""Database primitives exposed at module level.

Re-exports use the PEP 484 ``from x import Y as Y`` explicit
re-export form together with ``__all__`` so every type-checker
(mypy, pyright, pylance, basedpyright) accepts
``from tempest_fastapi_sdk.db import BaseUserTokenModel`` without
a "private import usage" / "is not exported" diagnostic.
"""

from tempest_fastapi_sdk.db.alembic_hooks import BASE_COLUMN_ORDER as BASE_COLUMN_ORDER
from tempest_fastapi_sdk.db.alembic_hooks import (
    backfill_non_nullable_defaults as backfill_non_nullable_defaults,
)
from tempest_fastapi_sdk.db.alembic_hooks import compose_hooks as compose_hooks
from tempest_fastapi_sdk.db.alembic_hooks import (
    reorder_base_columns_first as reorder_base_columns_first,
)
from tempest_fastapi_sdk.db.audit import AuditAction as AuditAction
from tempest_fastapi_sdk.db.audit import BaseAuditLogModel as BaseAuditLogModel
from tempest_fastapi_sdk.db.audit import diff_snapshots as diff_snapshots
from tempest_fastapi_sdk.db.audit import snapshot_model as snapshot_model
from tempest_fastapi_sdk.db.backup import (
    BackupToolMissingError as BackupToolMissingError,
)
from tempest_fastapi_sdk.db.backup import DatabaseBackup as DatabaseBackup
from tempest_fastapi_sdk.db.backup import (
    UnsupportedBackupBackendError as UnsupportedBackupBackendError,
)
from tempest_fastapi_sdk.db.connection import (
    AsyncDatabaseManager as AsyncDatabaseManager,
)
from tempest_fastapi_sdk.db.connection import (
    enable_sqlite_savepoints as enable_sqlite_savepoints,
)
from tempest_fastapi_sdk.db.connection import (
    enable_sqlite_wal as enable_sqlite_wal,
)
from tempest_fastapi_sdk.db.connection import (
    is_memory_sqlite_url as is_memory_sqlite_url,
)
from tempest_fastapi_sdk.db.connection import (
    shared_memory_url as shared_memory_url,
)
from tempest_fastapi_sdk.db.device_token_model import (
    BaseDeviceTokenModel as BaseDeviceTokenModel,
)
from tempest_fastapi_sdk.db.device_token_model import (
    make_device_token_model as make_device_token_model,
)
from tempest_fastapi_sdk.db.enum_migrations import (
    EnumColumnRef as EnumColumnRef,
)
from tempest_fastapi_sdk.db.enum_migrations import (
    EnumTypeState as EnumTypeState,
)
from tempest_fastapi_sdk.db.enum_migrations import (
    ReplaceEnumOp as ReplaceEnumOp,
)
from tempest_fastapi_sdk.db.enum_migrations import (
    render_enum_types as render_enum_types,
)
from tempest_fastapi_sdk.db.enum_migrations import (
    sync_enum_types as sync_enum_types,
)
from tempest_fastapi_sdk.db.enums import ENUM_TYPE_SUFFIX as ENUM_TYPE_SUFFIX
from tempest_fastapi_sdk.db.enums import TempestEnum as TempestEnum
from tempest_fastapi_sdk.db.enums import enum_column as enum_column
from tempest_fastapi_sdk.db.enums import enum_type_name as enum_type_name
from tempest_fastapi_sdk.db.enums import enum_values as enum_values
from tempest_fastapi_sdk.db.explain import ExplainDetail as ExplainDetail
from tempest_fastapi_sdk.db.explain import ExplainReport as ExplainReport
from tempest_fastapi_sdk.db.explain import QueryPlan as QueryPlan
from tempest_fastapi_sdk.db.explain import explain_queries as explain_queries
from tempest_fastapi_sdk.db.expressions import F as F
from tempest_fastapi_sdk.db.expressions import Q as Q
from tempest_fastapi_sdk.db.expressions import WhereClause as WhereClause
from tempest_fastapi_sdk.db.migrations import AlembicHelper as AlembicHelper
from tempest_fastapi_sdk.db.migrations import (
    AmbiguousBaseRevisionError as AmbiguousBaseRevisionError,
)
from tempest_fastapi_sdk.db.migrations import (
    DestructiveMigrationError as DestructiveMigrationError,
)
from tempest_fastapi_sdk.db.migrations import (
    SchemaSyncOutcome as SchemaSyncOutcome,
)
from tempest_fastapi_sdk.db.mixins import AuditMixin as AuditMixin
from tempest_fastapi_sdk.db.mixins import LocaleColumnMixin as LocaleColumnMixin
from tempest_fastapi_sdk.db.mixins import MFAMixin as MFAMixin
from tempest_fastapi_sdk.db.mixins import NameMixin as NameMixin
from tempest_fastapi_sdk.db.mixins import SoftDeleteMixin as SoftDeleteMixin
from tempest_fastapi_sdk.db.model import NAMING_CONVENTION as NAMING_CONVENTION
from tempest_fastapi_sdk.db.model import BaseModel as BaseModel
from tempest_fastapi_sdk.db.model import to_snake_case as to_snake_case
from tempest_fastapi_sdk.db.outbox import BaseOutboxModel as BaseOutboxModel
from tempest_fastapi_sdk.db.outbox import OutboxRelay as OutboxRelay
from tempest_fastapi_sdk.db.outbox import OutboxStatus as OutboxStatus
from tempest_fastapi_sdk.db.repository import BaseRepository as BaseRepository
from tempest_fastapi_sdk.db.search import ColumnRef as ColumnRef
from tempest_fastapi_sdk.db.search import TextSearchLanguage as TextSearchLanguage
from tempest_fastapi_sdk.db.search import TextSearchWeight as TextSearchWeight
from tempest_fastapi_sdk.db.search import TokenMatch as TokenMatch
from tempest_fastapi_sdk.db.search import (
    full_text_condition as full_text_condition,
)
from tempest_fastapi_sdk.db.search import full_text_rank as full_text_rank
from tempest_fastapi_sdk.db.search import (
    like_search_condition as like_search_condition,
)
from tempest_fastapi_sdk.db.search import supports_full_text as supports_full_text
from tempest_fastapi_sdk.db.signals import RepositorySignal as RepositorySignal
from tempest_fastapi_sdk.db.signals import SignalHandler as SignalHandler
from tempest_fastapi_sdk.db.signals import clear_signals as clear_signals
from tempest_fastapi_sdk.db.signals import connect as connect
from tempest_fastapi_sdk.db.signals import disconnect as disconnect
from tempest_fastapi_sdk.db.signals import on_signal as on_signal
from tempest_fastapi_sdk.db.slow_query import SlowQueryLogger as SlowQueryLogger
from tempest_fastapi_sdk.db.tenant import (
    TenantScopedRepository as TenantScopedRepository,
)
from tempest_fastapi_sdk.db.transaction import in_transaction as in_transaction
from tempest_fastapi_sdk.db.transaction import savepoint as savepoint
from tempest_fastapi_sdk.db.transaction import transaction as transaction
from tempest_fastapi_sdk.db.transaction import (
    transaction_depth as transaction_depth,
)
from tempest_fastapi_sdk.db.user_model import BaseUserModel as BaseUserModel
from tempest_fastapi_sdk.db.user_oauth_account_model import (
    BaseUserOAuthAccountModel as BaseUserOAuthAccountModel,
)
from tempest_fastapi_sdk.db.user_oauth_account_model import (
    make_user_oauth_account_model as make_user_oauth_account_model,
)
from tempest_fastapi_sdk.db.user_recovery_code_model import (
    BaseUserRecoveryCodeModel as BaseUserRecoveryCodeModel,
)
from tempest_fastapi_sdk.db.user_recovery_code_model import (
    make_user_recovery_code_model as make_user_recovery_code_model,
)
from tempest_fastapi_sdk.db.user_refresh_token_model import (
    BaseUserRefreshTokenModel as BaseUserRefreshTokenModel,
)
from tempest_fastapi_sdk.db.user_refresh_token_model import (
    make_user_refresh_token_model as make_user_refresh_token_model,
)
from tempest_fastapi_sdk.db.user_token_model import (
    BaseUserTokenModel as BaseUserTokenModel,
)
from tempest_fastapi_sdk.db.user_token_model import UserTokenPurpose as UserTokenPurpose
from tempest_fastapi_sdk.db.user_token_model import (
    make_user_token_model as make_user_token_model,
)
from tempest_fastapi_sdk.db.user_webauthn_credential_model import (
    BaseWebAuthnCredentialModel as BaseWebAuthnCredentialModel,
)
from tempest_fastapi_sdk.db.user_webauthn_credential_model import (
    make_web_authn_credential_model as make_web_authn_credential_model,
)
from tempest_fastapi_sdk.db.voice_profile_model import (
    BaseVoiceProfileModel as BaseVoiceProfileModel,
)
from tempest_fastapi_sdk.db.voice_profile_model import (
    make_voice_profile_model as make_voice_profile_model,
)
from tempest_fastapi_sdk.db.webpush_subscription_model import (
    BaseWebPushSubscriptionModel as BaseWebPushSubscriptionModel,
)
from tempest_fastapi_sdk.db.webpush_subscription_model import (
    make_web_push_subscription_model as make_web_push_subscription_model,
)

__all__: list[str] = [
    "BASE_COLUMN_ORDER",
    "ENUM_TYPE_SUFFIX",
    "NAMING_CONVENTION",
    "AlembicHelper",
    "AmbiguousBaseRevisionError",
    "AsyncDatabaseManager",
    "AuditAction",
    "AuditMixin",
    "BackupToolMissingError",
    "BaseAuditLogModel",
    "BaseDeviceTokenModel",
    "BaseModel",
    "BaseOutboxModel",
    "BaseRepository",
    "BaseUserModel",
    "BaseUserOAuthAccountModel",
    "BaseUserRecoveryCodeModel",
    "BaseUserRefreshTokenModel",
    "BaseUserTokenModel",
    "BaseVoiceProfileModel",
    "BaseWebAuthnCredentialModel",
    "BaseWebPushSubscriptionModel",
    "ColumnRef",
    "DatabaseBackup",
    "DestructiveMigrationError",
    "EnumColumnRef",
    "EnumTypeState",
    "ExplainDetail",
    "ExplainReport",
    "F",
    "LocaleColumnMixin",
    "MFAMixin",
    "NameMixin",
    "OutboxRelay",
    "OutboxStatus",
    "Q",
    "QueryPlan",
    "ReplaceEnumOp",
    "RepositorySignal",
    "SchemaSyncOutcome",
    "SignalHandler",
    "SlowQueryLogger",
    "SoftDeleteMixin",
    "TempestEnum",
    "TenantScopedRepository",
    "TextSearchLanguage",
    "TextSearchWeight",
    "TokenMatch",
    "UnsupportedBackupBackendError",
    "UserTokenPurpose",
    "WhereClause",
    "backfill_non_nullable_defaults",
    "clear_signals",
    "compose_hooks",
    "connect",
    "diff_snapshots",
    "disconnect",
    "enable_sqlite_savepoints",
    "enable_sqlite_wal",
    "enum_column",
    "enum_type_name",
    "enum_values",
    "explain_queries",
    "full_text_condition",
    "full_text_rank",
    "in_transaction",
    "is_memory_sqlite_url",
    "like_search_condition",
    "make_device_token_model",
    "make_user_oauth_account_model",
    "make_user_recovery_code_model",
    "make_user_refresh_token_model",
    "make_user_token_model",
    "make_voice_profile_model",
    "make_web_authn_credential_model",
    "make_web_push_subscription_model",
    "on_signal",
    "render_enum_types",
    "reorder_base_columns_first",
    "savepoint",
    "shared_memory_url",
    "snapshot_model",
    "supports_full_text",
    "sync_enum_types",
    "to_snake_case",
    "transaction",
    "transaction_depth",
]
