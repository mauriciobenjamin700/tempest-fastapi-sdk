"""Application settings primitives exposed at module level.

Re-exports follow the PEP 484 ``from x import Y as Y`` explicit
re-export form **in addition to** ``__all__`` — this is the union
of the two patterns recognized by every type-checker (mypy, pyright,
pylance, basedpyright) without requiring a project-level
``pyrightconfig.json``. Consumers can rely on
``from tempest_fastapi_sdk.settings import AuthSettings`` with no
"private import usage" / "X is not exported from module Y"
complaint from any IDE.
"""

from tempest_fastapi_sdk.settings.base import BaseAppSettings as BaseAppSettings
from tempest_fastapi_sdk.settings.mixins import AuthSettings as AuthSettings
from tempest_fastapi_sdk.settings.mixins import CORSSettings as CORSSettings
from tempest_fastapi_sdk.settings.mixins import DatabaseSettings as DatabaseSettings
from tempest_fastapi_sdk.settings.mixins import EmailSettings as EmailSettings
from tempest_fastapi_sdk.settings.mixins import JWTSettings as JWTSettings
from tempest_fastapi_sdk.settings.mixins import LogSettings as LogSettings
from tempest_fastapi_sdk.settings.mixins import MinIOSettings as MinIOSettings
from tempest_fastapi_sdk.settings.mixins import RabbitMQSettings as RabbitMQSettings
from tempest_fastapi_sdk.settings.mixins import RedisSettings as RedisSettings
from tempest_fastapi_sdk.settings.mixins import ServerSettings as ServerSettings
from tempest_fastapi_sdk.settings.mixins import SessionSettings as SessionSettings
from tempest_fastapi_sdk.settings.mixins import TaskIQSettings as TaskIQSettings
from tempest_fastapi_sdk.settings.mixins import TokenSettings as TokenSettings
from tempest_fastapi_sdk.settings.mixins import UploadSettings as UploadSettings
from tempest_fastapi_sdk.settings.mixins import WebPushSettings as WebPushSettings
from tempest_fastapi_sdk.settings.mixins import WebSocketSettings as WebSocketSettings

__all__: list[str] = [
    "AuthSettings",
    "BaseAppSettings",
    "CORSSettings",
    "DatabaseSettings",
    "EmailSettings",
    "JWTSettings",
    "LogSettings",
    "MinIOSettings",
    "RabbitMQSettings",
    "RedisSettings",
    "ServerSettings",
    "SessionSettings",
    "TaskIQSettings",
    "TokenSettings",
    "UploadSettings",
    "WebPushSettings",
    "WebSocketSettings",
]
