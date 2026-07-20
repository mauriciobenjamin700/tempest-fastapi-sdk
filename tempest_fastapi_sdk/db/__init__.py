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
from tempest_fastapi_sdk.db.expressions import F as F
from tempest_fastapi_sdk.db.expressions import Q as Q
from tempest_fastapi_sdk.db.migrations import AlembicHelper as AlembicHelper
from tempest_fastapi_sdk.db.migrations import (
    DestructiveMigrationError as DestructiveMigrationError,
)
from tempest_fastapi_sdk.db.mixins import AuditMixin as AuditMixin
from tempest_fastapi_sdk.db.mixins import LocaleColumnMixin as LocaleColumnMixin
from tempest_fastapi_sdk.db.mixins import MFAMixin as MFAMixin
from tempest_fastapi_sdk.db.mixins import SoftDeleteMixin as SoftDeleteMixin
from tempest_fastapi_sdk.db.model import NAMING_CONVENTION as NAMING_CONVENTION
from tempest_fastapi_sdk.db.model import BaseModel as BaseModel
from tempest_fastapi_sdk.db.outbox import BaseOutboxModel as BaseOutboxModel
from tempest_fastapi_sdk.db.outbox import OutboxRelay as OutboxRelay
from tempest_fastapi_sdk.db.outbox import OutboxStatus as OutboxStatus
from tempest_fastapi_sdk.db.repository import BaseRepository as BaseRepository
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
from tempest_fastapi_sdk.db.user_model import BaseUserModel as BaseUserModel
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
from tempest_fastapi_sdk.db.webpush_subscription_model import (
    BaseWebPushSubscriptionModel as BaseWebPushSubscriptionModel,
)
from tempest_fastapi_sdk.db.webpush_subscription_model import (
    make_web_push_subscription_model as make_web_push_subscription_model,
)

__all__: list[str] = [
    "BASE_COLUMN_ORDER",
    "NAMING_CONVENTION",
    "AlembicHelper",
    "AsyncDatabaseManager",
    "AuditAction",
    "AuditMixin",
    "BackupToolMissingError",
    "BaseAuditLogModel",
    "BaseModel",
    "BaseOutboxModel",
    "BaseRepository",
    "BaseUserModel",
    "BaseUserRecoveryCodeModel",
    "BaseUserRefreshTokenModel",
    "BaseUserTokenModel",
    "BaseWebPushSubscriptionModel",
    "DatabaseBackup",
    "DestructiveMigrationError",
    "F",
    "LocaleColumnMixin",
    "MFAMixin",
    "OutboxRelay",
    "OutboxStatus",
    "Q",
    "RepositorySignal",
    "SignalHandler",
    "SlowQueryLogger",
    "SoftDeleteMixin",
    "TenantScopedRepository",
    "UnsupportedBackupBackendError",
    "UserTokenPurpose",
    "backfill_non_nullable_defaults",
    "clear_signals",
    "compose_hooks",
    "connect",
    "diff_snapshots",
    "disconnect",
    "make_user_recovery_code_model",
    "make_user_refresh_token_model",
    "make_user_token_model",
    "make_web_push_subscription_model",
    "on_signal",
    "reorder_base_columns_first",
    "snapshot_model",
]
