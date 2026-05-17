"""Composable settings mixins covering common service dependencies.

Each mixin is a fully-typed Pydantic model with sensible defaults so
projects can opt in by listing the mixins they need alongside their
own concrete ``Settings`` class:

    class Settings(DatabaseSettings, RedisSettings, BaseAppSettings):
        ...

The mixins MUST be placed before :class:`BaseAppSettings` in the MRO
so the latter's ``model_config`` wins. None of the mixins reads
environment variables on their own — they rely on the consumer's
``BaseAppSettings`` configuration.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class ServerSettings(BaseSettings):
    """HTTP server bind configuration.

    Attributes:
        HOST (str): Interface to bind to. ``127.0.0.1`` for internal
            services, ``0.0.0.0`` when consumed from a different
            origin (e.g. local frontend dev server).
        PORT (int): TCP port to listen on.
        DEBUG (bool): Whether to enable debug behavior (auto-reload,
            verbose error responses).
        LOG_LEVEL (str): Default logger level passed to
            :func:`tempest_fastapi_sdk.configure_logging`.
        LOG_JSON (bool): Whether logs should be emitted as JSON.
    """

    HOST: str = Field(default="127.0.0.1", description="Bind interface.")
    PORT: int = Field(default=8000, ge=1, le=65535, description="Listen port.")
    DEBUG: bool = Field(default=False, description="Enable debug mode.")
    LOG_LEVEL: str = Field(default="INFO", description="Root log level.")
    LOG_JSON: bool = Field(default=True, description="Emit logs as JSON.")


class DatabaseSettings(BaseSettings):
    """SQLAlchemy database connection configuration.

    Attributes:
        DATABASE_URL (str): Async SQLAlchemy URL (e.g.
            ``postgresql+asyncpg://...`` or ``sqlite+aiosqlite://...``).
        DATABASE_ECHO (bool): Whether to echo SQL statements.
        DATABASE_POOL_SIZE (int): Pool size (ignored on SQLite).
        DATABASE_MAX_OVERFLOW (int): Overflow capacity.
        DATABASE_POOL_RECYCLE (int): Seconds before a pooled
            connection is recycled.
    """

    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./app.db",
        description="Async SQLAlchemy connection URL.",
    )
    DATABASE_ECHO: bool = Field(default=False, description="Echo SQL.")
    DATABASE_POOL_SIZE: int = Field(default=10, ge=1)
    DATABASE_MAX_OVERFLOW: int = Field(default=20, ge=0)
    DATABASE_POOL_RECYCLE: int = Field(default=3600, ge=1)


class RedisSettings(BaseSettings):
    """Redis connection configuration.

    Attributes:
        REDIS_URL (str): ``redis://[user:pass@]host:port/db`` URL.
        REDIS_DECODE_RESPONSES (bool): Whether the client decodes
            bytes to ``str`` automatically.
    """

    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL.",
    )
    REDIS_DECODE_RESPONSES: bool = Field(default=True)


class RabbitMQSettings(BaseSettings):
    """RabbitMQ / FastStream broker configuration.

    Attributes:
        RABBITMQ_URL (str): ``amqp://user:pass@host:port/vhost`` URL.
        RABBITMQ_PREFETCH_COUNT (int): Number of messages to prefetch
            per consumer.
    """

    RABBITMQ_URL: str = Field(
        default="amqp://guest:guest@localhost:5672/",
        description="RabbitMQ AMQP URL.",
    )
    RABBITMQ_PREFETCH_COUNT: int = Field(default=10, ge=1)


class JWTSettings(BaseSettings):
    """JWT signing and verification configuration.

    Attributes:
        JWT_SECRET (str): Shared secret. MUST be at least 32 bytes
            for ``HS256``; production deployments should override the
            default at deploy time.
        JWT_ALGORITHM (str): Signing algorithm.
        JWT_ACCESS_TTL_SECONDS (int): Lifetime of access tokens.
        JWT_REFRESH_TTL_SECONDS (int): Lifetime of refresh tokens.
        JWT_ISSUER (str | None): Token issuer claim.
    """

    JWT_SECRET: str = Field(
        default="change-me-change-me-change-me-32",
        min_length=32,
        description="HMAC secret used to sign JWTs.",
    )
    JWT_ALGORITHM: str = Field(default="HS256")
    JWT_ACCESS_TTL_SECONDS: int = Field(default=3600, ge=1)
    JWT_REFRESH_TTL_SECONDS: int = Field(default=86_400 * 7, ge=1)
    JWT_ISSUER: str | None = Field(default=None)


class CORSSettings(BaseSettings):
    """CORS middleware configuration.

    .. warning::
        The default ``CORS_ORIGINS=["*"]`` is permissive on purpose
        so local development works out of the box. **Never** ship
        this default to production — set ``CORS_ORIGINS`` to the
        explicit list of trusted frontend origins (e.g.
        ``["https://app.example.com"]``) in your production
        configuration. ``"*"`` is also incompatible with
        ``CORS_ALLOW_CREDENTIALS=True`` (browsers ignore credentialed
        requests sent to a wildcard origin).

    Attributes:
        CORS_ORIGINS (list[str]): Allowed origins. **Override in
            production.** Defaults to ``["*"]`` for development only.
        CORS_ALLOW_CREDENTIALS (bool): Whether to allow cookies/auth
            headers cross-origin.
        CORS_ALLOW_METHODS (list[str]): Allowed HTTP methods.
        CORS_ALLOW_HEADERS (list[str]): Allowed request headers.
        CORS_EXPOSE_HEADERS (list[str]): Headers exposed to the
            browser JavaScript via ``Access-Control-Expose-Headers``.
        CORS_MAX_AGE (int): Preflight response cache TTL in seconds.
    """

    CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["*"])
    CORS_ALLOW_CREDENTIALS: bool = Field(default=False)
    CORS_ALLOW_METHODS: list[str] = Field(default_factory=lambda: ["*"])
    CORS_ALLOW_HEADERS: list[str] = Field(default_factory=lambda: ["*"])
    CORS_EXPOSE_HEADERS: list[str] = Field(
        default_factory=lambda: ["X-Request-ID"],
    )
    CORS_MAX_AGE: int = Field(default=600, ge=0)


__all__: list[str] = [
    "CORSSettings",
    "DatabaseSettings",
    "JWTSettings",
    "RabbitMQSettings",
    "RedisSettings",
    "ServerSettings",
]
