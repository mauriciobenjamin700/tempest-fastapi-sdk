"""Database primitives exposed at module level."""

from tempest_fastapi_sdk.db.alembic_hooks import (
    BASE_COLUMN_ORDER,
    compose_hooks,
    reorder_base_columns_first,
)
from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager
from tempest_fastapi_sdk.db.migrations import AlembicHelper
from tempest_fastapi_sdk.db.mixins import AuditMixin, SoftDeleteMixin
from tempest_fastapi_sdk.db.model import NAMING_CONVENTION, BaseModel
from tempest_fastapi_sdk.db.repository import BaseRepository
from tempest_fastapi_sdk.db.user_model import BaseUserModel
from tempest_fastapi_sdk.db.user_token_model import (
    BaseUserTokenModel,
    UserTokenPurpose,
    make_user_token_model,
)

__all__: list[str] = [
    "BASE_COLUMN_ORDER",
    "NAMING_CONVENTION",
    "AlembicHelper",
    "AsyncDatabaseManager",
    "AuditMixin",
    "BaseModel",
    "BaseRepository",
    "BaseUserModel",
    "BaseUserTokenModel",
    "SoftDeleteMixin",
    "UserTokenPurpose",
    "compose_hooks",
    "make_user_token_model",
    "reorder_base_columns_first",
]
