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
        SERVER_HOST (str): Interface to bind to. ``127.0.0.1`` for
            internal services, ``0.0.0.0`` only when consumed from a
            different origin (e.g. local frontend dev server).
        SERVER_PORT (int): TCP port to listen on.
        SERVER_RELOAD (bool): Whether uvicorn should hot-reload on
            file changes — development only.
        SERVER_DEBUG (bool): Generic debug flag for the application
            (verbose error responses, extra logging hooks).
    """

    SERVER_HOST: str = Field(default="127.0.0.1", description="Bind interface.")
    SERVER_PORT: int = Field(default=8000, ge=1, le=65535, description="Listen port.")
    SERVER_RELOAD: bool = Field(
        default=False,
        description="Enable uvicorn auto-reload.",
    )
    SERVER_DEBUG: bool = Field(
        default=False,
        description="Enable application debug mode.",
    )


class LogSettings(BaseSettings):
    """Structured logging configuration.

    Attributes:
        LOG_LEVEL (str): Default logger level passed to
            :func:`tempest_fastapi_sdk.configure_logging`.
        LOG_JSON (bool): Whether logs should be emitted as JSON.
        LOG_DIR (str): Directory for per-level + ``500.log`` files,
            relative to the service root. Empty disables file logging
            (stdout only). Defaults to ``"logs"``.
    """

    LOG_LEVEL: str = Field(default="INFO", description="Root log level.")
    LOG_JSON: bool = Field(default=True, description="Emit logs as JSON.")
    LOG_DIR: str = Field(
        default="logs",
        description=(
            "Directory for per-level + 500.log files. Empty disables "
            "file logging (stdout only)."
        ),
    )


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


class EmailSettings(BaseSettings):
    """SMTP / transactional email configuration.

    Mirrors the constructor arguments of
    :class:`tempest_fastapi_sdk.EmailUtils` so a service can wire it up
    with ``EmailUtils(**settings.email_kwargs())``.

    Attributes:
        SMTP_HOST (str): Hostname of the SMTP server.
        SMTP_PORT (int): TCP port. ``587`` for STARTTLS, ``465`` for
            SMTPS, ``25`` for plain SMTP.
        SMTP_USERNAME (str | None): SMTP authentication username.
            ``None`` disables authentication.
        SMTP_PASSWORD (str | None): SMTP authentication password.
        SMTP_FROM_ADDR (str): Default ``From`` address used when the
            caller doesn't pass one.
        SMTP_USE_TLS (bool): Whether STARTTLS should be negotiated.
        SMTP_USE_SSL (bool): Whether the connection should be wrapped
            in TLS from the start (SMTPS).
        SMTP_TIMEOUT_SECONDS (float): Network timeout for SMTP
            operations.
    """

    SMTP_HOST: str = Field(default="localhost", description="SMTP server host.")
    SMTP_PORT: int = Field(default=587, ge=1, le=65535)
    SMTP_USERNAME: str | None = Field(default=None)
    SMTP_PASSWORD: str | None = Field(default=None)
    SMTP_FROM_ADDR: str = Field(
        default="noreply@example.com",
        description="Default From address.",
    )
    SMTP_USE_TLS: bool = Field(default=True, description="Use STARTTLS.")
    SMTP_USE_SSL: bool = Field(default=False, description="Wrap in TLS from start.")
    SMTP_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0.0)


class UploadSettings(BaseSettings):
    """File upload constraints.

    Mirrors the constructor arguments of
    :class:`tempest_fastapi_sdk.UploadUtils`.

    Attributes:
        UPLOAD_DIR (str): Root directory where uploaded files are
            persisted (relative paths resolve from the process CWD).
        UPLOAD_MAX_SIZE_BYTES (int): Hard limit per file. ``0``
            disables the check.
        UPLOAD_ALLOWED_EXTENSIONS (set[str]): Lowercase file
            extensions (without the leading dot) allowed by default.
            Empty set means "any extension".
        UPLOAD_ALLOWED_MIMETYPES (set[str]): MIME types allowed by
            default. Empty set means "any mime type".
    """

    UPLOAD_DIR: str = Field(default="./var/uploads")
    UPLOAD_MAX_SIZE_BYTES: int = Field(default=10 * 1024 * 1024, ge=0)
    UPLOAD_ALLOWED_EXTENSIONS: set[str] = Field(default_factory=set)
    UPLOAD_ALLOWED_MIMETYPES: set[str] = Field(default_factory=set)


class TokenSettings(BaseSettings):
    """Shared-secret ``X-Token`` configuration.

    Used by :func:`tempest_fastapi_sdk.make_token_dependency` for
    internal service-to-service authentication. Validation is performed
    with :func:`hmac.compare_digest`.

    Attributes:
        TOKEN_SECRET (str): The expected ``X-Token`` value. Empty
            string disables the check (dev only).
    """

    TOKEN_SECRET: str = Field(
        default="",
        description="Shared secret. Empty disables the check (dev only).",
    )


class WebPushSettings(BaseSettings):
    """Web Push / VAPID configuration.

    Mirrors the constructor arguments of
    :class:`tempest_fastapi_sdk.WebPushDispatcher`.

    Attributes:
        VAPID_PUBLIC_KEY (str): URL-safe base64 VAPID public key.
        VAPID_PRIVATE_KEY (str): URL-safe base64 VAPID private key.
        VAPID_SUBJECT (str): ``mailto:`` or ``https://`` contact URL
            advertised in the VAPID JWT.
        WEBPUSH_DEFAULT_TTL_SECONDS (int): Default TTL applied to
            outgoing notifications when the caller doesn't override.
    """

    VAPID_PUBLIC_KEY: str = Field(
        default="",
        description="URL-safe base64 public key.",
    )
    VAPID_PRIVATE_KEY: str = Field(
        default="",
        description="URL-safe base64 private key.",
    )
    VAPID_SUBJECT: str = Field(
        default="mailto:admin@example.com",
        description="VAPID `sub` claim.",
    )
    WEBPUSH_DEFAULT_TTL_SECONDS: int = Field(default=86_400, ge=0)


class TaskIQSettings(BaseSettings):
    """TaskIQ broker / result backend configuration.

    Use this when the TaskIQ broker is **not** the same RabbitMQ /
    Redis instance covered by :class:`RabbitMQSettings` /
    :class:`RedisSettings`.

    Attributes:
        TASKIQ_BROKER_URL (str): URL of the TaskIQ broker (AMQP, Redis,
            in-memory, etc.).
        TASKIQ_RESULT_BACKEND_URL (str | None): Optional URL of the
            result backend; ``None`` keeps results in-memory (fine for
            fire-and-forget workloads).
    """

    TASKIQ_BROKER_URL: str = Field(
        default="amqp://guest:guest@localhost:5672/",
        description="TaskIQ broker URL.",
    )
    TASKIQ_RESULT_BACKEND_URL: str | None = Field(default=None)


__all__: list[str] = [
    "CORSSettings",
    "DatabaseSettings",
    "EmailSettings",
    "JWTSettings",
    "LogSettings",
    "RabbitMQSettings",
    "RedisSettings",
    "ServerSettings",
    "TaskIQSettings",
    "TokenSettings",
    "UploadSettings",
    "WebPushSettings",
]
