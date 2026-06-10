"""Database primitives exposed at module level.

Re-exports use the PEP 484 ``from x import Y as Y`` explicit
re-export form together with ``__all__`` so every type-checker
(mypy, pyright, pylance, basedpyright) accepts
``from tempest_fastapi_sdk.db import BaseUserTokenModel`` without
a "private import usage" / "is not exported" diagnostic.
"""

from tempest_fastapi_sdk.db.alembic_hooks import BASE_COLUMN_ORDER as BASE_COLUMN_ORDER
from tempest_fastapi_sdk.db.alembic_hooks import compose_hooks as compose_hooks
from tempest_fastapi_sdk.db.alembic_hooks import (
    reorder_base_columns_first as reorder_base_columns_first,
)
from tempest_fastapi_sdk.db.connection import (
    AsyncDatabaseManager as AsyncDatabaseManager,
)
from tempest_fastapi_sdk.db.migrations import AlembicHelper as AlembicHelper
from tempest_fastapi_sdk.db.mixins import AuditMixin as AuditMixin
from tempest_fastapi_sdk.db.mixins import MFAMixin as MFAMixin
from tempest_fastapi_sdk.db.mixins import SoftDeleteMixin as SoftDeleteMixin
from tempest_fastapi_sdk.db.model import NAMING_CONVENTION as NAMING_CONVENTION
from tempest_fastapi_sdk.db.model import BaseModel as BaseModel
from tempest_fastapi_sdk.db.outbox import BaseOutboxModel as BaseOutboxModel
from tempest_fastapi_sdk.db.outbox import OutboxRelay as OutboxRelay
from tempest_fastapi_sdk.db.outbox import OutboxStatus as OutboxStatus
from tempest_fastapi_sdk.db.repository import BaseRepository as BaseRepository
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
from tempest_fastapi_sdk.db.user_token_model import (
    BaseUserTokenModel as BaseUserTokenModel,
)
from tempest_fastapi_sdk.db.user_token_model import UserTokenPurpose as UserTokenPurpose
from tempest_fastapi_sdk.db.user_token_model import (
    make_user_token_model as make_user_token_model,
)

__all__: list[str] = [
    "BASE_COLUMN_ORDER",
    "NAMING_CONVENTION",
    "AlembicHelper",
    "AsyncDatabaseManager",
    "AuditMixin",
    "BaseModel",
    "BaseOutboxModel",
    "BaseRepository",
    "BaseUserModel",
    "BaseUserRecoveryCodeModel",
    "BaseUserTokenModel",
    "MFAMixin",
    "OutboxRelay",
    "OutboxStatus",
    "SlowQueryLogger",
    "SoftDeleteMixin",
    "TenantScopedRepository",
    "UserTokenPurpose",
    "compose_hooks",
    "make_user_recovery_code_model",
    "make_user_token_model",
    "reorder_base_columns_first",
]
