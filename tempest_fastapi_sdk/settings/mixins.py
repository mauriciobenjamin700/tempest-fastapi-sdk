"""Composable settings mixins covering common service dependencies.

Each mixin is a fully-typed Pydantic model with sensible defaults so
projects can opt in by listing the mixins they need alongside their
own concrete ``Settings`` class:

    class Settings(DatabaseSettings, RedisSettings, BaseAppSettings):
        ...

Every mixin inherits :class:`BaseAppSettings` (not raw
``pydantic_settings.BaseSettings``), so each one carries the canonical
``model_config`` — ``env_file=".env"``, ``extra="ignore"``,
``case_sensitive=True`` — materialized on its own class. This makes the
*value* of ``.env`` loading independent of mixin ordering: pydantic
materializes a *complete* ``model_config`` onto every settings class,
so a mixin listed before ``BaseAppSettings`` would otherwise overwrite
the whole config (resetting ``env_file`` to ``None``) even though it
never declared that key. Inheriting ``BaseAppSettings`` keeps ``.env``
in the materialized config no matter where the mixin sits.

Because the mixins now subclass ``BaseAppSettings``, ``BaseAppSettings``
**must be the last base** of the composed ``Settings``. Listing it
before any mixin violates Python's C3 linearization (a base cannot
precede its own subclass) and raises ``TypeError: Cannot create a
consistent method resolution order (MRO)`` at import — so keep
``BaseAppSettings`` at the end of the bases, as shown above.

Every field carries ``title``, ``description`` and ``examples`` so
JSON-Schema consumers (FastAPI ``/docs``, ``/redoc``, IDE tooling,
``pydantic.model_json_schema()``) render rich metadata out of the
box.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from pydantic import Field, field_validator

from tempest_fastapi_sdk.core.logging import (
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_MAX_BYTES,
)
from tempest_fastapi_sdk.settings.base import BaseAppSettings


class ServerSettings(BaseAppSettings):
    """HTTP server bind configuration.

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
        SERVER_HOST (str): Interface to bind to. Default: ``"127.0.0.1"``.
        SERVER_PORT (int): TCP port the application listens on. Default: ``8000``.
        SERVER_RELOAD (bool): Hot-reload on file changes (dev only). Default: ``False``.
        SERVER_DEBUG (bool): Generic application debug flag. Default: ``False``.
    """

    SERVER_HOST: str = Field(
        default="127.0.0.1",
        title="Server bind host",
        description=(
            "Interface to bind to. ``127.0.0.1`` for internal services, "
            "``0.0.0.0`` only when consumed from a different origin "
            "(e.g. local frontend dev server)."
        ),
        examples=["127.0.0.1", "0.0.0.0"],
    )
    SERVER_PORT: int = Field(
        default=8000,
        ge=1,
        le=65535,
        title="Server listen port",
        description="TCP port the application listens on.",
        examples=[8000, 8080, 9000],
    )
    SERVER_RELOAD: bool = Field(
        default=False,
        title="Uvicorn auto-reload",
        description=(
            "Whether uvicorn should hot-reload on file changes — development only."
        ),
        examples=[False, True],
    )
    SERVER_DEBUG: bool = Field(
        default=False,
        title="Application debug mode",
        description=(
            "Generic debug flag for the application (verbose error "
            "responses, extra logging hooks)."
        ),
        examples=[False, True],
    )


class LogSettings(BaseAppSettings):
    """Structured logging configuration.

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
        LOG_LEVEL (str): Default logger level for ``configure_logging``.
            Default: ``"INFO"``.
        LOG_JSON (bool): Emit stdout logs as JSON. Default: ``True``.
        LOG_DIR (str): Directory for per-level + ``500.log`` files; empty
            disables file logging. Default: ``"logs"``.
        LOG_MAX_BYTES (int): Size at which each log file rotates; ``0``
            disables rotation. Default: ``10_000_000``.
        LOG_BACKUP_COUNT (int): Rotated files kept per level.
            Default: ``5``.
    """

    LOG_LEVEL: str = Field(
        default="INFO",
        title="Root log level",
        description=(
            "Default logger level passed to "
            ":func:`tempest_fastapi_sdk.configure_logging`."
        ),
        examples=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    LOG_JSON: bool = Field(
        default=True,
        title="JSON log output",
        description="Whether stdout logs are emitted as JSON.",
        examples=[True, False],
    )
    LOG_DIR: str = Field(
        default="logs",
        title="Log directory",
        description=(
            "Directory for per-level + ``500.log`` files, relative to "
            "the service root. Empty disables file logging (stdout only)."
        ),
        examples=["logs", "/var/log/myapp", ""],
    )
    LOG_MAX_BYTES: int = Field(
        default=DEFAULT_LOG_MAX_BYTES,
        ge=0,
        title="Log rotation size",
        description=(
            "Size in bytes at which each per-level file rotates. ``0`` "
            "turns rotation off, leaving plain ``FileHandler``s for a "
            "host where ``logrotate`` or a sidecar owns retention. "
            "Rotating by default is the safe end of that choice: the "
            "service that never thought about log growth is exactly the "
            "one that fills the disk."
        ),
        examples=[10_000_000, 5_000_000, 0],
    )
    LOG_BACKUP_COUNT: int = Field(
        default=DEFAULT_LOG_BACKUP_COUNT,
        ge=0,
        title="Rotated files kept per level",
        description=(
            "How many rotated files to keep for each level. The disk "
            "budget is ``LOG_MAX_BYTES * (LOG_BACKUP_COUNT + 1)`` per "
            "level, times the six files this SDK writes."
        ),
        examples=[5, 3, 0],
    )

    def logging_kwargs(self) -> dict[str, Any]:
        """Map these settings onto :func:`configure_logging` kwargs.

        Without this, a service wires the call by hand and the two
        rotation knobs are the ones that get left out — they are the
        newest, and a missing one degrades to a default rather than to
        an error.

        ``log_dir`` is passed through as-is, including the empty string,
        which :func:`configure_logging` reads as "no file logging".

        Returns:
            dict[str, Any]: Keyword arguments ready to splat into
            ``configure_logging(**settings.logging_kwargs())``.
        """
        return {
            "level": self.LOG_LEVEL,
            "json_output": self.LOG_JSON,
            "log_dir": self.LOG_DIR,
            "max_bytes": self.LOG_MAX_BYTES,
            "backup_count": self.LOG_BACKUP_COUNT,
        }


class DatabaseSettings(BaseAppSettings):
    """SQLAlchemy database connection configuration.

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
        DATABASE_URL (str): Async SQLAlchemy connection URL.
            Default: ``"sqlite+aiosqlite:///./app.db"``.
        DATABASE_ECHO (bool): Print every SQL statement to the logger
            (dev only). Default: ``False``.
        DATABASE_POOL_SIZE (int): Number of persistent pool connections.
            Default: ``10``.
        DATABASE_MAX_OVERFLOW (int): Max extra connections opened past the
            pool size under load. Default: ``20``.
        DATABASE_POOL_RECYCLE (int): Seconds before a pooled connection is
            recycled. Default: ``3600``.
        DATABASE_SQLITE_WAL (bool): Put SQLite engines in WAL mode.
            Ignored on other backends. Default: ``True``.
        DATABASE_SQLITE_BUSY_TIMEOUT (float): Seconds a SQLite
            connection waits on a lock before failing. Ignored on other
            backends. Default: ``30.0``.
    """

    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./app.db",
        title="Database URL",
        description="Async SQLAlchemy connection URL.",
        examples=[
            "sqlite+aiosqlite:///./app.db",
            "postgresql+asyncpg://app:app@localhost:5432/app",
        ],
    )
    DATABASE_ECHO: bool = Field(
        default=False,
        title="Echo SQL statements",
        description="Print every SQL statement to the logger (dev only).",
        examples=[False, True],
    )
    DATABASE_POOL_SIZE: int = Field(
        default=10,
        ge=1,
        title="Connection pool size",
        description="Number of persistent connections (ignored on SQLite).",
        examples=[10, 20, 50],
    )
    DATABASE_MAX_OVERFLOW: int = Field(
        default=20,
        ge=0,
        title="Pool overflow capacity",
        description=(
            "Maximum extra connections opened past ``DATABASE_POOL_SIZE`` "
            "when under load."
        ),
        examples=[0, 10, 20],
    )
    DATABASE_POOL_RECYCLE: int = Field(
        default=3600,
        ge=1,
        title="Pool recycle interval (seconds)",
        description=(
            "Seconds before a pooled connection is recycled. Lower this "
            "if the database server closes idle connections aggressively."
        ),
        examples=[300, 1800, 3600],
    )

    DATABASE_SQLITE_WAL: bool = Field(
        default=True,
        title="SQLite WAL mode",
        description=(
            "Put SQLite engines in WAL mode so a reader and a writer "
            "stop excluding each other (web process plus worker on one "
            "file). Ignored on other backends."
        ),
        examples=[True, False],
    )
    DATABASE_SQLITE_BUSY_TIMEOUT: float = Field(
        default=30.0,
        ge=0.0,
        title="SQLite busy timeout (seconds)",
        description=(
            "Seconds a SQLite connection waits for a lock another "
            "connection holds before failing with ``database is "
            "locked``. Ignored on other backends."
        ),
        examples=[5.0, 30.0, 60.0],
    )

    def database_kwargs(self) -> dict[str, Any]:
        """Map these settings onto :class:`AsyncDatabaseManager` kwargs.

        Returns:
            dict[str, Any]: Keyword arguments ready to splat into
            ``AsyncDatabaseManager(**settings.database_kwargs())``.
        """
        return {
            "db_url": self.DATABASE_URL,
            "echo": self.DATABASE_ECHO,
            "pool_size": self.DATABASE_POOL_SIZE,
            "max_overflow": self.DATABASE_MAX_OVERFLOW,
            "pool_recycle": self.DATABASE_POOL_RECYCLE,
            "sqlite_wal": self.DATABASE_SQLITE_WAL,
            "sqlite_busy_timeout": self.DATABASE_SQLITE_BUSY_TIMEOUT,
        }


class RedisSettings(BaseAppSettings):
    """Redis connection configuration.

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
        REDIS_URL (str): ``redis://[user:pass@]host:port/db`` connection URL.
            Default: ``"redis://localhost:6379/0"``.
        REDIS_DECODE_RESPONSES (bool): Decode bytes to ``str`` automatically.
            Default: ``True``.
    """

    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        title="Redis URL",
        description="``redis://[user:pass@]host:port/db`` connection URL.",
        examples=[
            "redis://localhost:6379/0",
            "rediss://:secret@redis.internal:6380/1",
        ],
    )
    REDIS_DECODE_RESPONSES: bool = Field(
        default=True,
        title="Decode responses to str",
        description=(
            "Whether the client decodes bytes to ``str`` automatically. "
            "Set ``False`` for binary payloads."
        ),
        examples=[True, False],
    )

    def redis_kwargs(self) -> dict[str, Any]:
        """Map these settings onto :class:`AsyncRedisManager` kwargs.

        Returns:
            dict[str, Any]: Keyword arguments ready to splat into
            ``AsyncRedisManager(**settings.redis_kwargs())``.
        """
        return {
            "url": self.REDIS_URL,
            "decode_responses": self.REDIS_DECODE_RESPONSES,
        }


class RabbitMQSettings(BaseAppSettings):
    """RabbitMQ / FastStream broker configuration.

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
        RABBITMQ_URL (str): ``amqp://user:pass@host:port/vhost`` connection
            URL. Default: ``"amqp://guest:guest@localhost:5672/"``.
        RABBITMQ_PREFETCH_COUNT (int): Number of unacked messages a consumer
            can hold. Default: ``10``.
    """

    RABBITMQ_URL: str = Field(
        default="amqp://guest:guest@localhost:5672/",
        title="RabbitMQ AMQP URL",
        description="``amqp://user:pass@host:port/vhost`` connection URL.",
        examples=[
            "amqp://guest:guest@localhost:5672/",
            "amqps://app:secret@rabbit.internal:5671/prod",
        ],
    )
    RABBITMQ_PREFETCH_COUNT: int = Field(
        default=10,
        ge=1,
        title="Consumer prefetch count",
        description=(
            "Number of unacked messages a consumer can hold. Tune for "
            "throughput vs. fairness across consumers."
        ),
        examples=[1, 10, 50],
    )


class JWTSettings(BaseAppSettings):
    """JWT signing and verification configuration.

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
        JWT_SECRET (str): Shared secret used to sign JWTs (>= 32 bytes for
            HS256). Default: ``"change-me-change-me-change-me-32"``.
        JWT_ALGORITHM (str): JOSE algorithm used to sign and verify tokens.
            Default: ``"HS256"``.
        JWT_ACCESS_TTL_SECONDS (int): Lifetime of issued access tokens.
            Default: ``3600``.
        JWT_REFRESH_TTL_SECONDS (int): Lifetime of issued refresh tokens.
            Default: ``604800``.
        JWT_ISSUER (str | None): Value of the ``iss`` claim; ``None`` omits
            it. Default: ``None``.
    """

    JWT_SECRET: str = Field(
        default="change-me-change-me-change-me-32",
        min_length=32,
        title="JWT signing secret",
        description=(
            "Shared secret used to sign JWTs. MUST be at least 32 bytes "
            "for ``HS256``; production deployments **MUST** override the "
            "default at deploy time."
        ),
        examples=["change-me-change-me-change-me-32"],
    )
    JWT_ALGORITHM: str = Field(
        default="HS256",
        title="JWT signing algorithm",
        description="JOSE algorithm identifier used to sign and verify tokens.",
        examples=["HS256", "HS512", "RS256"],
    )
    JWT_ACCESS_TTL_SECONDS: int = Field(
        default=3600,
        ge=1,
        title="Access-token TTL (seconds)",
        description="Lifetime of access tokens issued by the service.",
        examples=[900, 3600, 7200],
    )
    JWT_REFRESH_TTL_SECONDS: int = Field(
        default=86_400 * 7,
        ge=1,
        title="Refresh-token TTL (seconds)",
        description="Lifetime of refresh tokens issued by the service.",
        examples=[86_400, 86_400 * 7, 86_400 * 30],
    )
    JWT_ISSUER: str | None = Field(
        default=None,
        title="JWT issuer claim",
        description=("Value of the ``iss`` claim. ``None`` omits the claim entirely."),
        examples=[None, "tempest-api", "https://auth.example.com"],
    )

    def jwt_kwargs(self) -> dict[str, Any]:
        """Map these settings onto :class:`JWTUtils` constructor kwargs.

        ``JWT_ACCESS_TTL_SECONDS`` becomes the ``default_ttl`` timedelta;
        the refresh TTL is not a ``JWTUtils`` parameter (it is consumed by
        the bundled auth flow) and is intentionally left out.

        Returns:
            dict[str, Any]: Keyword arguments ready to splat into
            ``JWTUtils(**settings.jwt_kwargs())``.
        """
        return {
            "secret": self.JWT_SECRET,
            "algorithm": self.JWT_ALGORITHM,
            "default_ttl": timedelta(seconds=self.JWT_ACCESS_TTL_SECONDS),
            "issuer": self.JWT_ISSUER,
        }


class CORSSettings(BaseAppSettings):
    """CORS middleware configuration.

    .. warning::
        The default ``CORS_ORIGINS=["*"]`` is permissive on purpose
        so local development works out of the box. **Never** ship
        this default to production — set ``CORS_ORIGINS`` to the
        explicit list of trusted frontend origins. ``"*"`` is also
        incompatible with ``CORS_ALLOW_CREDENTIALS=True`` (browsers
        ignore credentialed requests sent to a wildcard origin).

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
        CORS_ORIGINS (list[str]): Allowed origins; override in production.
            Default: ``["*"]``.
        CORS_ORIGIN_REGEX (str): Regex matched against the request
            ``Origin`` for session-varying origins (dev tunnels, preview
            deploys). Empty disables it. Default: ``""``.
        CORS_ALLOW_CREDENTIALS (bool): Allow cookies / auth headers
            cross-origin. Default: ``False``.
        CORS_ALLOW_METHODS (list[str]): HTTP verbs accepted by the preflight
            check. Default: ``["*"]``.
        CORS_ALLOW_HEADERS (list[str]): Request headers accepted by the
            preflight check. Default: ``["*"]``.
        CORS_EXPOSE_HEADERS (list[str]): Headers exposed to browser
            JavaScript. Default: ``["X-Request-ID"]``.
        CORS_MAX_AGE (int): How long the browser may cache the preflight
            response (seconds). Default: ``600``.
    """

    CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: ["*"],
        title="Allowed CORS origins",
        description=(
            "Allowed origins. **Override in production.** Defaults to "
            '``["*"]`` for development only.'
        ),
        examples=[
            ["*"],
            ["https://app.example.com", "https://admin.example.com"],
        ],
    )
    CORS_ORIGIN_REGEX: str = Field(
        default="",
        title="Allowed CORS origin regex",
        description=(
            "Regex matched against the request ``Origin`` for origins that "
            "vary per session (e.g. ngrok / Cloudflare dev tunnels, preview "
            "deployments). Empty disables it. Works alongside "
            '``CORS_ORIGINS`` and, unlike ``["*"]``, is compatible with '
            "``CORS_ALLOW_CREDENTIALS=True``."
        ),
        examples=["", r"https://.*\.ngrok-free\.app", r"https://.*\.vercel\.app"],
    )
    CORS_ALLOW_CREDENTIALS: bool = Field(
        default=False,
        title="Allow credentials cross-origin",
        description=(
            "Whether the browser may attach cookies / auth headers to "
            "cross-origin requests. Cannot be ``True`` with "
            '``CORS_ORIGINS=["*"]``.'
        ),
        examples=[False, True],
    )
    CORS_ALLOW_METHODS: list[str] = Field(
        default_factory=lambda: ["*"],
        title="Allowed HTTP methods",
        description="HTTP verbs accepted by the CORS preflight check.",
        examples=[["*"], ["GET", "POST", "PUT", "DELETE", "PATCH"]],
    )
    CORS_ALLOW_HEADERS: list[str] = Field(
        default_factory=lambda: ["*"],
        title="Allowed request headers",
        description="Headers accepted by the CORS preflight check.",
        examples=[["*"], ["Content-Type", "Authorization", "X-Request-ID"]],
    )
    CORS_EXPOSE_HEADERS: list[str] = Field(
        default_factory=lambda: ["X-Request-ID"],
        title="Headers exposed to JavaScript",
        description=(
            "Headers exposed to browser JavaScript via "
            "``Access-Control-Expose-Headers``."
        ),
        examples=[["X-Request-ID"], ["X-Request-ID", "X-RateLimit-Remaining"]],
    )
    CORS_MAX_AGE: int = Field(
        default=600,
        ge=0,
        title="Preflight cache TTL (seconds)",
        description="How long the browser may cache the CORS preflight response.",
        examples=[0, 600, 3600],
    )


class EmailSettings(BaseAppSettings):
    """SMTP / transactional email configuration.

    Mirrors the constructor arguments of
    :class:`tempest_fastapi_sdk.EmailUtils` so a service can wire it up
    with ``EmailUtils(**settings.email_kwargs())``.

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
        SMTP_HOST (str): Hostname of the SMTP server. Default: ``"localhost"``.
        SMTP_PORT (int): TCP port for the SMTP connection. Default: ``587``.
        SMTP_USERNAME (str | None): Auth username; ``None`` disables SMTP
            auth. Default: ``None``.
        SMTP_PASSWORD (str | None): Auth password, paired with the username.
            Default: ``None``.
        SMTP_FROM_ADDR (str): Default ``From`` address when the caller omits
            one. Default: ``"noreply@example.com"``.
        SMTP_USE_TLS (bool): Negotiate STARTTLS after connect (port 587).
            Default: ``True``.
        SMTP_USE_SSL (bool): Wrap the connection in TLS from the start
            (SMTPS, port 465). Default: ``False``.
        SMTP_TIMEOUT_SECONDS (float): Network timeout for SMTP operations.
            Default: ``30.0``.
    """

    SMTP_HOST: str = Field(
        default="localhost",
        title="SMTP server host",
        description="Hostname of the SMTP server.",
        examples=["localhost", "smtp.gmail.com", "email-smtp.us-east-1.amazonaws.com"],
    )
    SMTP_PORT: int = Field(
        default=587,
        ge=1,
        le=65535,
        title="SMTP TCP port",
        description=(
            "TCP port for the SMTP connection. ``587`` for STARTTLS, "
            "``465`` for SMTPS, ``25`` for plain SMTP."
        ),
        examples=[25, 465, 587, 1025],
    )
    SMTP_USERNAME: str | None = Field(
        default=None,
        title="SMTP auth username",
        description=(
            "Authentication username. ``None`` disables SMTP auth (dev "
            "MailHog / local relay)."
        ),
        examples=[None, "apikey", "noreply@example.com"],
    )
    SMTP_PASSWORD: str | None = Field(
        default=None,
        title="SMTP auth password",
        description="Authentication password. Pair with ``SMTP_USERNAME``.",
        examples=[None, "smtp-app-password"],
    )
    SMTP_FROM_ADDR: str = Field(
        default="noreply@example.com",
        title="Default From address",
        description=(
            "Default ``From`` address used when the caller doesn't pass "
            "one to :meth:`EmailUtils.send`."
        ),
        examples=["noreply@example.com", "alerts@example.com"],
    )
    SMTP_USE_TLS: bool = Field(
        default=True,
        title="Use STARTTLS",
        description=(
            "Whether STARTTLS should be negotiated after connect. Pair "
            "with port ``587``."
        ),
        examples=[True, False],
    )
    SMTP_USE_SSL: bool = Field(
        default=False,
        title="Use SMTPS (SSL from start)",
        description=(
            "Whether the connection should be wrapped in TLS from the "
            "start (SMTPS). Pair with port ``465``."
        ),
        examples=[False, True],
    )
    SMTP_TIMEOUT_SECONDS: float = Field(
        default=30.0,
        gt=0.0,
        title="SMTP timeout (seconds)",
        description="Network timeout for SMTP operations.",
        examples=[5.0, 30.0, 60.0],
    )

    def email_kwargs(self) -> dict[str, Any]:
        """Map these settings onto :class:`EmailUtils` constructor kwargs.

        The setting names follow SMTP conventions while
        :class:`tempest_fastapi_sdk.EmailUtils` uses transport-oriented
        names, so this method bridges the two:

        * ``SMTP_USE_TLS`` (STARTTLS after connect, port 587) maps to
          ``use_starttls``.
        * ``SMTP_USE_SSL`` (implicit TLS from connect, port 465) maps to
          ``use_tls``.

        Returns:
            dict[str, Any]: Keyword arguments ready to splat into
            ``EmailUtils(**settings.email_kwargs())``.

        Example:

            >>> from tempest_fastapi_sdk import EmailUtils
            >>> mailer = EmailUtils(**settings.email_kwargs())
        """
        return {
            "host": self.SMTP_HOST,
            "port": self.SMTP_PORT,
            "from_addr": self.SMTP_FROM_ADDR,
            "username": self.SMTP_USERNAME,
            "password": self.SMTP_PASSWORD,
            "use_tls": self.SMTP_USE_SSL,
            "use_starttls": self.SMTP_USE_TLS,
            "timeout": self.SMTP_TIMEOUT_SECONDS,
        }


class UploadSettings(BaseAppSettings):
    """File upload constraints.

    Mirrors the constructor arguments of
    :class:`tempest_fastapi_sdk.UploadUtils`.

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
        UPLOAD_DIR (str): Root directory where uploaded files are persisted.
            Default: ``"./var/uploads"``.
        UPLOAD_MAX_SIZE_BYTES (int): Hard limit per file; ``0`` disables the
            check. Default: ``10485760``.
        UPLOAD_ALLOWED_EXTENSIONS (set[str]): Allowed lowercase extensions;
            empty means any. Default: ``set()``.
        UPLOAD_ALLOWED_MIMETYPES (set[str]): Allowed MIME types; empty means
            any. Default: ``set()``.
    """

    UPLOAD_DIR: str = Field(
        default="./var/uploads",
        title="Upload root directory",
        description=(
            "Root directory where uploaded files are persisted (relative "
            "paths resolve from the process CWD)."
        ),
        examples=["./var/uploads", "/data/uploads"],
    )
    UPLOAD_MAX_SIZE_BYTES: int = Field(
        default=10 * 1024 * 1024,
        ge=0,
        title="Max upload size (bytes)",
        description="Hard limit per file. ``0`` disables the check.",
        examples=[0, 5 * 1024 * 1024, 10 * 1024 * 1024, 50 * 1024 * 1024],
    )
    UPLOAD_ALLOWED_EXTENSIONS: set[str] = Field(
        default_factory=set,
        title="Allowed file extensions",
        description=(
            "Lowercase file extensions (without the leading dot) allowed "
            'by default. Empty set means "any extension".'
        ),
        examples=[set(), {"png", "jpg", "pdf"}],
    )
    UPLOAD_ALLOWED_MIMETYPES: set[str] = Field(
        default_factory=set,
        title="Allowed MIME types",
        description=('MIME types allowed by default. Empty set means "any mime type".'),
        examples=[set(), {"image/png", "image/jpeg", "application/pdf"}],
    )

    def upload_kwargs(self) -> dict[str, Any]:
        """Map these settings onto :class:`UploadUtils` constructor kwargs.

        Uses ``UPLOAD_DIR`` as the local-disk ``source``; pass an
        ``AsyncMinIOClient`` to ``UploadUtils`` directly when storing in a
        bucket instead.

        Returns:
            dict[str, Any]: Keyword arguments ready to splat into
            ``UploadUtils(**settings.upload_kwargs())``.
        """
        return {
            "source": self.UPLOAD_DIR,
            "max_size_bytes": self.UPLOAD_MAX_SIZE_BYTES,
            "allowed_extensions": self.UPLOAD_ALLOWED_EXTENSIONS,
            "allowed_mimetypes": self.UPLOAD_ALLOWED_MIMETYPES,
        }


class TokenSettings(BaseAppSettings):
    """Shared-secret ``X-Token`` configuration.

    Used by :func:`tempest_fastapi_sdk.make_token_dependency` for
    internal service-to-service authentication. Validation is performed
    with :func:`hmac.compare_digest`.

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
        TOKEN_SECRET (str): Expected ``X-Token`` header value; empty
            disables the check. Default: ``""``.
    """

    TOKEN_SECRET: str = Field(
        default="",
        title="Shared X-Token secret",
        description=(
            "The expected ``X-Token`` header value. Empty string "
            "disables the check (dev only)."
        ),
        examples=["", "internal-svc-secret-please-rotate"],
    )


class WebPushSettings(BaseAppSettings):
    """Web Push / VAPID configuration.

    Mirrors the constructor arguments of
    :class:`tempest_fastapi_sdk.WebPushDispatcher`.

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
        VAPID_PUBLIC_KEY (str): URL-safe base64 VAPID public key.
            Default: ``""``.
        VAPID_PRIVATE_KEY (str): URL-safe base64 VAPID private key.
            Default: ``""``.
        VAPID_SUBJECT (str): ``mailto:`` / ``https://`` contact in the VAPID
            JWT. Default: ``"mailto:admin@example.com"``.
        WEBPUSH_DEFAULT_TTL_SECONDS (int): Default TTL for outgoing
            notifications. Default: ``86400``.
    """

    VAPID_PUBLIC_KEY: str = Field(
        default="",
        title="VAPID public key",
        description="URL-safe base64 VAPID public key.",
        examples=["", "BNc8R7r2…"],
    )
    VAPID_PRIVATE_KEY: str = Field(
        default="",
        title="VAPID private key",
        description="URL-safe base64 VAPID private key.",
        examples=["", "kQ9p3F…"],
    )
    VAPID_SUBJECT: str = Field(
        default="mailto:admin@example.com",
        title="VAPID subject (`sub` claim)",
        description=(
            "``mailto:`` or ``https://`` contact URL advertised in the VAPID JWT."
        ),
        examples=["mailto:admin@example.com", "https://example.com/contact"],
    )
    WEBPUSH_DEFAULT_TTL_SECONDS: int = Field(
        default=86_400,
        ge=0,
        title="Default push TTL (seconds)",
        description=(
            "Default TTL applied to outgoing notifications when the "
            "caller doesn't override."
        ),
        examples=[3600, 86_400, 86_400 * 7],
    )

    def webpush_kwargs(self) -> dict[str, Any]:
        """Map these settings onto :class:`WebPushDispatcher` kwargs.

        The **public** key is advertised to browser clients, not passed
        to the dispatcher, so it is intentionally omitted here.

        Returns:
            dict[str, Any]: Keyword arguments ready to splat into
            ``WebPushDispatcher(**settings.webpush_kwargs())``.
        """
        return {
            "vapid_private_key": self.VAPID_PRIVATE_KEY,
            "vapid_subject": self.VAPID_SUBJECT,
            "ttl_seconds": self.WEBPUSH_DEFAULT_TTL_SECONDS,
        }

    @property
    def enabled(self) -> bool:
        """Whether Web Push dispatch is configured.

        Dispatch needs a signing key; with an empty ``VAPID_PRIVATE_KEY``
        (the dev default) subscribe/unsubscribe still work but sending is
        skipped. Use this to gate notify calls.

        Returns:
            bool: ``True`` when a VAPID private key is set.
        """
        return bool(self.VAPID_PRIVATE_KEY)


class FirebaseSettings(BaseAppSettings):
    """Firebase Admin credentials for ID token verification.

    Mirrors the constructor arguments of
    :class:`tempest_fastapi_sdk.auth.FirebaseAuth`.

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    ``FIREBASE_CREDENTIALS_JSON`` and ``FIREBASE_CREDENTIALS_PATH``
    describe the same service account through different channels: the
    file is the usual local shape, the inline JSON is what a deployment
    without a mounted volume injects. When both are set the inline JSON
    wins, matching :class:`~tempest_fastapi_sdk.auth.FirebaseAuth`.
    Leaving both empty falls back to the environment's
    application-default credential.

    Attributes:
        FIREBASE_PROJECT_ID (str): Firebase project id. Optional when the
            service account already carries it. Default: ``""``.
        FIREBASE_CREDENTIALS_PATH (str): Path to a service-account JSON
            file. Default: ``""``.
        FIREBASE_CREDENTIALS_JSON (str): The service-account JSON inline.
            Default: ``""``.
    """

    FIREBASE_PROJECT_ID: str = Field(
        default="",
        title="Firebase project id",
        description=(
            "Firebase project the ID tokens are issued for. Optional when "
            "the service account already carries it."
        ),
        examples=["", "my-app-3f21c"],
    )
    FIREBASE_CREDENTIALS_PATH: str = Field(
        default="",
        title="Service account file",
        description="Filesystem path to the service-account JSON file.",
        examples=["", "credentials.json", "/run/secrets/firebase.json"],
    )
    FIREBASE_CREDENTIALS_JSON: str = Field(
        default="",
        title="Service account JSON (inline)",
        description=(
            "The service-account JSON itself, for deployments that inject "
            "it as an environment variable instead of mounting a file."
        ),
        examples=["", '{"type": "service_account", "project_id": "..."}'],
    )

    def firebase_kwargs(self) -> dict[str, Any]:
        """Map these settings onto :class:`FirebaseAuth` kwargs.

        Empty strings are dropped rather than forwarded, so an unset
        variable leaves the corresponding constructor default in place
        instead of configuring an empty path.

        Returns:
            dict[str, Any]: Keyword arguments ready to splat into
            ``FirebaseAuth(**settings.firebase_kwargs())``.
        """
        kwargs: dict[str, Any] = {}
        if self.FIREBASE_CREDENTIALS_JSON:
            kwargs["credentials_json"] = self.FIREBASE_CREDENTIALS_JSON
        if self.FIREBASE_CREDENTIALS_PATH:
            kwargs["credentials_path"] = self.FIREBASE_CREDENTIALS_PATH
        if self.FIREBASE_PROJECT_ID:
            kwargs["project_id"] = self.FIREBASE_PROJECT_ID
        return kwargs

    @property
    def enabled(self) -> bool:
        """Whether an explicit Firebase service account is configured.

        ``False`` does **not** mean verification is impossible: an
        application-default credential (``GOOGLE_APPLICATION_CREDENTIALS``
        or the metadata server on Google infrastructure) still works.
        Use this to gate wiring in projects that must run without
        Firebase at all.

        Returns:
            bool: ``True`` when a credentials path or inline JSON is set.
        """
        return bool(self.FIREBASE_CREDENTIALS_JSON or self.FIREBASE_CREDENTIALS_PATH)


class PushSettings(WebPushSettings, FirebaseSettings):
    """Every variable a service delivering to browsers *and* phones needs.

    Composing :class:`WebPushSettings` and :class:`FirebaseSettings`
    by hand works, but it hides a trap: both declare an ``enabled``
    property, so the MRO silently picks the Web Push one and a
    mobile-only service reads ``enabled is False`` while FCM is perfectly
    configured. This class exists to make that explicit — ``enabled``
    answers "can this service push at all", and the two halves are
    readable separately through :attr:`web_enabled` and
    :attr:`mobile_enabled`.

    Attributes:
        VAPID_PUBLIC_KEY (str): Inherited from :class:`WebPushSettings`.
        VAPID_PRIVATE_KEY (str): Inherited from :class:`WebPushSettings`.
        VAPID_SUBJECT (str): Inherited from :class:`WebPushSettings`.
        WEBPUSH_DEFAULT_TTL_SECONDS (int): Inherited from
            :class:`WebPushSettings`.
        FIREBASE_PROJECT_ID (str): Inherited from
            :class:`FirebaseSettings`.
        FIREBASE_CREDENTIALS_PATH (str): Inherited from
            :class:`FirebaseSettings`.
        FIREBASE_CREDENTIALS_JSON (str): Inherited from
            :class:`FirebaseSettings`.
    """

    @property
    def web_enabled(self) -> bool:
        """Whether Web Push dispatch is configured.

        Returns:
            bool: ``True`` when a VAPID private key is set.
        """
        return bool(self.VAPID_PRIVATE_KEY)

    @property
    def mobile_enabled(self) -> bool:
        """Whether an explicit Firebase service account is configured.

        ``False`` does not mean FCM is impossible — an
        application-default credential still works. It means this process
        was not handed one explicitly.

        Returns:
            bool: ``True`` when a credentials path or inline JSON is set.
        """
        return bool(self.FIREBASE_CREDENTIALS_JSON or self.FIREBASE_CREDENTIALS_PATH)

    @property
    def enabled(self) -> bool:
        """Whether the service can push to at least one platform.

        Returns:
            bool: ``True`` when either half is configured.
        """
        return self.web_enabled or self.mobile_enabled


class TaskIQSettings(BaseAppSettings):
    """TaskIQ broker / result backend configuration.

    Use this when the TaskIQ broker is **not** the same RabbitMQ /
    Redis instance covered by :class:`RabbitMQSettings` /
    :class:`RedisSettings`.

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
        TASKIQ_BROKER_URL (str): URL of the TaskIQ broker (AMQP, Redis,
            in-memory). Default: ``"amqp://guest:guest@localhost:5672/"``.
        TASKIQ_RESULT_BACKEND_URL (str | None): Optional result backend URL;
            ``None`` keeps results in-memory. Default: ``None``.
        TASKIQ_STORE_RESULTS (bool): Whether task results are stored at
            all. Default: ``True``. ``False`` leaves TaskIQ's
            ``DummyResultBackend`` in place on both transports — the
            shape of a cron-only service, where no caller waits on a
            return value.
        TASKIQ_RESULT_TTL_SECONDS (int): Seconds a stored result
            survives. Default: ``86400`` (one day); ``0`` keeps results
            forever, which is what ``taskiq_redis`` does on its own.
    """

    TASKIQ_BROKER_URL: str = Field(
        default="amqp://guest:guest@localhost:5672/",
        title="TaskIQ broker URL",
        description=("URL of the TaskIQ broker (AMQP, Redis, in-memory, etc.)."),
        examples=[
            "amqp://guest:guest@localhost:5672/",
            "redis://localhost:6379/2",
            "memory://",
        ],
    )
    TASKIQ_RESULT_BACKEND_URL: str | None = Field(
        default=None,
        title="TaskIQ result backend URL",
        description=(
            "Optional URL of the result backend; ``None`` keeps results "
            "in-memory (fine for fire-and-forget workloads)."
        ),
        examples=[None, "redis://localhost:6379/3"],
    )
    TASKIQ_STORE_RESULTS: bool = Field(
        default=True,
        title="Store task results",
        description=(
            "Whether task results are stored at all. ``False`` leaves "
            "TaskIQ's DummyResultBackend in place on both transports, "
            "which is what a cron-only service wants: no caller is "
            "waiting on a return value, so every stored result is cost "
            "with no reader."
        ),
        examples=[True, False],
    )
    TASKIQ_RESULT_TTL_SECONDS: int = Field(
        default=86_400,
        ge=0,
        title="Task result TTL (seconds)",
        description=(
            "How long a stored result survives. ``0`` keeps results "
            "forever, which is taskiq-redis' own default and leaves one "
            "permanent key per execution."
        ),
        examples=[86400, 3600, 0],
    )


class AuthSettings(BaseAppSettings):
    """Configuration for the bundled signup / activation / reset flows.

    Consumed by :class:`tempest_fastapi_sdk.auth.UserAuthService`
    and :func:`tempest_fastapi_sdk.make_auth_router`. Each flag
    has a sensible production default; flip ``AUTH_AUTO_ACTIVATE``
    or ``AUTH_RETURN_TOKEN_IN_RESPONSE`` only in dev / CI.

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
        AUTH_SIGNUP_ENABLED (bool): Kill-switch mounting
            ``POST /auth/signup``. Default: ``True``.
        AUTH_AUTO_ACTIVATE (bool): Mark users active on signup, skipping the
            activation email. Default: ``False``.
        AUTH_RETURN_TOKEN_IN_RESPONSE (bool): Include the activation / reset
            link in the JSON response. Default: ``False``.
        AUTH_ACTIVATION_TTL_SECONDS (int): How long an activation token stays
            valid. Default: ``604800``.
        AUTH_PASSWORD_RESET_TTL_SECONDS (int): How long a password-reset
            token stays valid. Default: ``3600``.
        AUTH_ACTIVATION_URL_TEMPLATE (str): Front-end activation URL;
            ``{token}`` is substituted.
            Default: ``"http://localhost:3000/activate?token={token}"``.
        AUTH_PASSWORD_RESET_URL_TEMPLATE (str): Front-end reset URL;
            ``{token}`` is substituted.
            Default: ``"http://localhost:3000/reset-password?token={token}"``.
        AUTH_ACTIVATION_TEMPLATE (str): Jinja2 activation email template
            filename. Default: ``"activation.html"``.
        AUTH_PASSWORD_RESET_TEMPLATE (str): Jinja2 password-reset email
            template filename. Default: ``"password_reset.html"``.
        AUTH_PASSWORD_MIN_LENGTH (int): Minimum accepted password length.
            Default: ``12``.
        AUTH_PASSWORD_MAX_BYTES (int): Maximum accepted password length in
            UTF-8 bytes. Default: ``72`` (the bcrypt limit).
        AUTH_PASSWORD_REQUIRE_COMPLEXITY (bool): Require character-class
            complexity (and >= 8 length). Default: ``False``.
        AUTH_BACKEND_LINKS (bool): Mount backend-rendered activation/reset
            HTML pages. Default: ``False``.
        AUTH_LOGIN_URL (str | None): Login URL shown on backend success
            pages; ``None`` hides the button. Default: ``None``.
        AUTH_ACTIVATION_SUCCESS_TEMPLATE (str): Backend activation success
            page template. Default: ``"activation_success.html"``.
        AUTH_ACTIVATION_ERROR_TEMPLATE (str): Backend activation error page
            template. Default: ``"activation_error.html"``.
        AUTH_PASSWORD_RESET_FORM_TEMPLATE (str): Backend reset form page
            template. Default: ``"password_reset_form.html"``.
        AUTH_PASSWORD_RESET_SUCCESS_TEMPLATE (str): Backend reset success
            page template. Default: ``"password_reset_success.html"``.
        AUTH_PASSWORD_RESET_ERROR_TEMPLATE (str): Backend reset error page
            template. Default: ``"password_reset_error.html"``.
        AUTH_MFA_ENABLED (bool): Kill-switch enabling the MFA endpoints and
            TOTP login flow. Default: ``False``.
        AUTH_MFA_ISSUER (str): Issuer label shown in the Authenticator app.
            Default: ``"Tempest"``.
        AUTH_MFA_RECOVERY_CODES_COUNT (int): Number of single-use recovery
            codes generated at enrollment. Default: ``10``.
        AUTH_MFA_TOKEN_TTL_SECONDS (int): Lifetime of the intermediate MFA
            login token. Default: ``300``.
        AUTH_MFA_VERIFY_WINDOW (int): TOTP clock-drift tolerance in 30s
            steps. Default: ``1``.
        AUTH_WEBAUTHN_ENABLED (bool): Kill-switch enabling the WebAuthn /
            passkey endpoints. Default: ``False``.
        AUTH_WEBAUTHN_RP_ID (str): Domain the credential is bound to.
            Default: ``""``.
        AUTH_WEBAUTHN_RP_NAME (str): Product name shown by the browser.
            Default: ``"Tempest"``.
        AUTH_WEBAUTHN_ALLOWED_ORIGINS (list[str]): Exact origins allowed
            to complete a ceremony. Default: ``[]`` (the ``fido2`` rule).
        AUTH_WEBAUTHN_USER_VERIFICATION (str): Whether the authenticator
            must verify the user. Default: ``"preferred"``.
        AUTH_WEBAUTHN_RESIDENT_KEY (str): Whether the credential must be
            discoverable. Default: ``"preferred"``.
        AUTH_WEBAUTHN_CHALLENGE_TTL_SECONDS (int): Lifetime of the
            between-request ceremony state. Default: ``300``.
        AUTH_SINGLE_ACTIVE_TOKEN (bool): Issuing an account token spends
            the user's other unused tokens of the same purpose, so only
            the newest link works. Default: ``True``.
        AUTH_OAUTH_ENABLED (bool): Kill-switch enabling the social-login
            (``/auth/oauth/*``) endpoints. Default: ``False``.
        AUTH_OAUTH_STATE_COOKIE_NAME (str): Cookie carrying the CSRF
            state between the redirect and the callback.
            Default: ``"oauth_state"``.
        AUTH_OAUTH_STATE_TTL_SECONDS (int): Lifetime of that state.
            Default: ``600``.
        AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL (bool): Attach a new provider
            identity to an existing account when the provider states the
            email is verified. Default: ``False``.
        AUTH_OAUTH_ALLOW_ACCOUNT_CREATION (bool | None): Whether the
            callback may create a user row. Default: ``None``
            (inherits ``AUTH_SIGNUP_ENABLED``).
    """

    AUTH_SIGNUP_ENABLED: bool = Field(
        default=True,
        title="Self-service signup kill-switch",
        description=(
            "When ``False``, ``make_auth_router`` does not mount "
            "``POST /auth/signup`` — the route is absent from the "
            "application and from the OpenAPI schema, so a closed "
            "system where accounts are created by an administrator "
            "does not expose a public registration door. Activation, "
            "password reset and every other endpoint are unaffected: "
            "an admin-created account still completes activation "
            "through ``/auth/activate/{token}``. The "
            "``allow_signup`` argument of ``make_auth_router`` "
            "overrides this per router."
        ),
        examples=[True, False],
    )
    AUTH_AUTO_ACTIVATE: bool = Field(
        default=False,
        title="Auto-activate on signup",
        description=(
            "When ``True``, signup immediately marks the user "
            "active and skips the activation email entirely. "
            "Useful for dev environments where users don't have "
            "real inboxes. Never enable in production."
        ),
        examples=[False, True],
    )
    AUTH_RETURN_TOKEN_IN_RESPONSE: bool = Field(
        default=False,
        title="Return token in HTTP response",
        description=(
            "When ``True``, signup / password-reset endpoints "
            "include the activation / reset link in the JSON "
            "response body instead of (or in addition to) "
            "sending the email. Useful when the SMTP host is "
            "MailHog and you don't want to round-trip through "
            "the inbox UI."
        ),
        examples=[False, True],
    )
    AUTH_ACTIVATION_TTL_SECONDS: int = Field(
        default=86_400 * 7,
        ge=60,
        title="Activation-token TTL (seconds)",
        description=(
            "How long an activation token stays valid after "
            "issuance. Defaults to 7 days."
        ),
        examples=[3600, 86_400, 86_400 * 7],
    )
    AUTH_PASSWORD_RESET_TTL_SECONDS: int = Field(
        default=3_600,
        ge=60,
        title="Password-reset token TTL (seconds)",
        description=(
            "How long a password-reset token stays valid. "
            "Defaults to 1 hour — shorter is safer; longer hurts "
            "UX."
        ),
        examples=[900, 3_600, 7_200],
    )
    AUTH_ACTIVATION_URL_TEMPLATE: str = Field(
        default="http://localhost:3000/activate?token={token}",
        title="Activation URL template",
        description=(
            "Front-end URL where the user is redirected to "
            "complete activation. The literal ``{token}`` is "
            "replaced with the issued token."
        ),
        examples=[
            "http://localhost:3000/activate?token={token}",
            "https://app.example.com/activate/{token}",
        ],
    )
    AUTH_PASSWORD_RESET_URL_TEMPLATE: str = Field(
        default="http://localhost:3000/reset-password?token={token}",
        title="Password-reset URL template",
        description=(
            "Front-end URL where the user completes the reset "
            "flow. ``{token}`` is replaced with the issued "
            "token."
        ),
        examples=[
            "http://localhost:3000/reset-password?token={token}",
            "https://app.example.com/reset?token={token}",
        ],
    )
    AUTH_ACTIVATION_TEMPLATE: str = Field(
        default="activation.html",
        title="Activation email template name",
        description=(
            "Jinja2 template filename rendered by "
            "``EmailUtils.render_template``. Resolved against "
            "the ``template_dir`` configured on ``EmailUtils``; "
            "the SDK ships a default ``activation.html`` you can "
            "shadow by placing one with the same name in your "
            "project's template directory."
        ),
        examples=["activation.html", "auth/welcome.html"],
    )
    AUTH_PASSWORD_RESET_TEMPLATE: str = Field(
        default="password_reset.html",
        title="Password-reset email template name",
        description=(
            "Jinja2 template filename rendered by "
            "``EmailUtils.render_template``. Same resolution "
            "rules as ``AUTH_ACTIVATION_TEMPLATE``."
        ),
        examples=["password_reset.html"],
    )
    AUTH_PASSWORD_MIN_LENGTH: int = Field(
        default=12,
        ge=1,
        title="Minimum password length (chars)",
        description=(
            "Signup + reset reject passwords shorter than this. "
            "Fully configurable — the default of 12 follows the "
            "current OWASP guidance (longer passwords are the single "
            "biggest brute-force deterrent), but a project can set any "
            "value from 1 up. This floor is the single source of "
            "truth: the request schemas do NOT impose their own length "
            "bound, so lowering it (e.g. to 4) takes effect on the "
            "router path too."
        ),
        examples=[4, 8, 12, 16],
    )
    AUTH_PASSWORD_MAX_BYTES: int = Field(
        default=72,
        ge=1,
        title="Maximum password length (UTF-8 bytes)",
        description=(
            "Signup + reset + change reject passwords longer than this, "
            "measured in **bytes** rather than characters because that is "
            "what the hash sees. The default of 72 is bcrypt's hard limit: "
            "``bcrypt.hashpw`` raises ``ValueError`` past it, which without "
            "this check surfaced as an HTTP 500 instead of a validation "
            "error. Note that 72 bytes is fewer than 72 characters for "
            "non-ASCII input — an emoji costs 4 bytes, an accented Latin "
            "letter 2. Raise it only if you swap the hasher for one without "
            "the limit."
        ),
        examples=[72, 128],
    )
    AUTH_PASSWORD_REQUIRE_COMPLEXITY: bool = Field(
        default=False,
        title="Require password character complexity",
        description=(
            "When ``False`` (default), any password meeting "
            "``AUTH_PASSWORD_MIN_LENGTH`` is accepted. When ``True``, "
            "signup + reset additionally require at least one lowercase "
            "letter, one uppercase letter, one digit, and one special "
            "character (any non-alphanumeric), AND the effective length "
            "floor is raised to at least 8 — a configured "
            "``AUTH_PASSWORD_MIN_LENGTH`` below 8 is ignored while this "
            "flag is on."
        ),
        examples=[False, True],
    )
    AUTH_BACKEND_LINKS: bool = Field(
        default=False,
        title="Backend-controlled activation/reset pages",
        description=(
            "When ``True``, ``make_auth_router`` mounts three extra "
            "endpoints — ``GET /auth/activate/{token}``, "
            "``GET /auth/password-reset/{token}`` and "
            "``POST /auth/password-reset/{token}`` (form-encoded) — "
            "that render HTML success/error pages directly from the "
            "backend. The email link points at the BACKEND, not the "
            "frontend, so the project does not need a SPA route to "
            "process tokens. Set ``AUTH_ACTIVATION_URL_TEMPLATE`` "
            "and ``AUTH_PASSWORD_RESET_URL_TEMPLATE`` to your "
            "backend's public URL when this is on."
        ),
        examples=[False, True],
    )
    AUTH_LOGIN_URL: str | None = Field(
        default=None,
        title="Login page URL (rendered in backend success pages)",
        description=(
            "When ``AUTH_BACKEND_LINKS=True``, the bundled HTML "
            "success pages render a 'go to login' button pointing "
            "at this URL. ``None`` hides the button — the user is "
            "told the action succeeded but no link is offered."
        ),
        examples=[None, "https://app.example.com/login"],
    )
    AUTH_DEFAULT_LOCALE: str = Field(
        default="pt-BR",
        title="Default language for bundled auth emails and pages",
        description=(
            "Language of the SDK-bundled activation / password-reset "
            "**emails** and the backend HTML **pages** when no other "
            "signal is available. Supported values: ``pt-BR`` (default) "
            "and ``en-US``. The value is normalized case-insensitively, "
            "so ``PT-BR``, ``pt_br`` and ``ptbr`` all resolve to "
            "``pt-BR``. This is the **last** resort: pages first "
            "honour the ``?lang=`` on the emailed link, then the user's "
            "stored ``locale``, then the browser's ``Accept-Language`` "
            "header; emails honour the stored ``locale`` (there is no "
            "link yet when the email is built)."
        ),
        examples=["pt-BR", "en-US"],
    )
    AUTH_STAMP_LOCALE_IN_LINK: bool = Field(
        default=True,
        title="Append ?lang= to the links inside bundled auth emails",
        description=(
            "Stamps the resolved locale onto the activation / password "
            "reset / email-change / verification link, so the page the "
            "link opens renders in the language of the email that "
            "carried it — the only signal available for an account that "
            "was just created and has no stored ``locale`` yet. Turn it "
            "off when the URL template points at a front-end route that "
            "rejects unknown query parameters; the language then falls "
            "back to ``Accept-Language``, which is what produced the "
            "bilingual flow this setting exists to fix."
        ),
        examples=[True, False],
    )

    @field_validator("AUTH_DEFAULT_LOCALE")
    @classmethod
    def _normalize_default_locale(cls, value: str) -> str:
        """Coerce ``AUTH_DEFAULT_LOCALE`` into a canonical supported tag.

        Args:
            value (str): The raw configured value.

        Returns:
            str: One of the supported locales (``"pt-BR"`` / ``"en-US"``).
        """
        from tempest_fastapi_sdk.auth.locale import normalize_locale

        return normalize_locale(value)

    AUTH_ACTIVATION_SUCCESS_TEMPLATE: str = Field(
        default="activation_success.html",
        title="Backend activation success page template",
        description=(
            "Jinja2 template rendered by "
            "``GET /auth/activate/{token}`` on success. Resolved "
            "against ``EmailUtils.template_dir``; SDK ships a "
            "default you can shadow."
        ),
        examples=["activation_success.html"],
    )
    AUTH_ACTIVATION_ERROR_TEMPLATE: str = Field(
        default="activation_error.html",
        title="Backend activation error page template",
        description=(
            "Jinja2 template rendered when the activation token is "
            "expired, already used, or unknown. Same resolution "
            "rules as ``AUTH_ACTIVATION_SUCCESS_TEMPLATE``."
        ),
        examples=["activation_error.html"],
    )
    AUTH_PASSWORD_RESET_FORM_TEMPLATE: str = Field(
        default="password_reset_form.html",
        title="Backend password-reset form template",
        description=(
            "Jinja2 template rendered by "
            "``GET /auth/password-reset/{token}`` — the HTML form "
            "where the user types the new password."
        ),
        examples=["password_reset_form.html"],
    )
    AUTH_PASSWORD_RESET_SUCCESS_TEMPLATE: str = Field(
        default="password_reset_success.html",
        title="Backend password-reset success page template",
        description=(
            "Jinja2 template rendered after a successful "
            "``POST /auth/password-reset/{token}``."
        ),
        examples=["password_reset_success.html"],
    )
    AUTH_PASSWORD_RESET_ERROR_TEMPLATE: str = Field(
        default="password_reset_error.html",
        title="Backend password-reset error page template",
        description=(
            "Jinja2 template rendered when the reset token is "
            "expired, already used, or unknown."
        ),
        examples=["password_reset_error.html"],
    )
    AUTH_EMAIL_CHANGE_TTL_SECONDS: int = Field(
        default=3_600,
        ge=60,
        title="Email-change token TTL (seconds)",
        description=(
            "How long a pending email-change confirmation token stays "
            "valid. Defaults to 1 hour — the confirmation link is sent "
            "to the NEW address and this bounds the window an attacker "
            "who intercepts it could act in."
        ),
        examples=[900, 3_600, 7_200],
    )
    AUTH_EMAIL_VERIFICATION_TTL_SECONDS: int = Field(
        default=86_400,
        ge=60,
        title="Email-verification token TTL (seconds)",
        description=(
            "How long a re-verification token for the user's CURRENT "
            "email stays valid. Defaults to 1 day."
        ),
        examples=[3_600, 86_400],
    )
    AUTH_EMAIL_CHANGE_URL_TEMPLATE: str = Field(
        default="http://localhost:3000/confirm-email?token={token}",
        title="Email-change confirmation URL template",
        description=(
            "Front-end URL where the user confirms a pending email "
            "change. ``{token}`` is replaced with the issued token. "
            "Point it at the backend when ``AUTH_BACKEND_LINKS=True``."
        ),
        examples=[
            "http://localhost:3000/confirm-email?token={token}",
            "https://app.example.com/confirm-email/{token}",
        ],
    )
    AUTH_EMAIL_VERIFICATION_URL_TEMPLATE: str = Field(
        default="http://localhost:3000/verify-email?token={token}",
        title="Email re-verification URL template",
        description=(
            "Front-end URL where the user confirms their current email. "
            "``{token}`` is replaced with the issued token."
        ),
        examples=[
            "http://localhost:3000/verify-email?token={token}",
            "https://app.example.com/verify-email/{token}",
        ],
    )
    AUTH_EMAIL_CHANGE_TEMPLATE: str = Field(
        default="email_change.html",
        title="Email-change confirmation email template name",
        description=(
            "Jinja2 template rendered for the confirmation email sent to "
            "the NEW address. Same resolution rules as "
            "``AUTH_ACTIVATION_TEMPLATE``."
        ),
        examples=["email_change.html"],
    )
    AUTH_EMAIL_VERIFICATION_TEMPLATE: str = Field(
        default="email_verification.html",
        title="Email re-verification email template name",
        description=(
            "Jinja2 template rendered for the re-verification email sent "
            "to the user's current address."
        ),
        examples=["email_verification.html"],
    )
    AUTH_EMAIL_CHANGED_NOTICE_TEMPLATE: str = Field(
        default="email_changed_notice.html",
        title="Email-changed security notice template name",
        description=(
            "Jinja2 template rendered for the security alert sent to the "
            "user's OLD address after an email change is confirmed. Sent "
            "only when ``AUTH_EMAIL_CHANGE_NOTIFY_OLD=True``."
        ),
        examples=["email_changed_notice.html"],
    )
    AUTH_EMAIL_CHANGE_NOTIFY_OLD: bool = Field(
        default=True,
        title="Notify the old email on a confirmed change",
        description=(
            "When ``True`` (default), a security alert is sent to the "
            "PREVIOUS email address once an email change is confirmed — "
            "the pattern banks and Google use so a hijacked account "
            "still surfaces the change to the rightful owner. Set "
            "``False`` to skip it."
        ),
        examples=[True, False],
    )
    AUTH_EMAIL_RECOVERY_ENABLED: bool = Field(
        default=False,
        title="Enable the email-recovery endpoint",
        description=(
            "When ``True``, ``make_auth_router`` mounts "
            "``POST /auth/email-recovery/request`` — an UNAUTHENTICATED "
            "endpoint that lets a user who lost access to their mailbox "
            "move to a new email by proving identity with their password "
            "(and a valid MFA code when TOTP is enrolled). Off by default "
            "because it is security-sensitive: enable it deliberately, and "
            "always with ``AUTH_EMAIL_CHANGE_NOTIFY_OLD=True`` so the old "
            "address is alerted."
        ),
        examples=[False, True],
    )
    AUTH_EMAIL_CHANGE_SUCCESS_TEMPLATE: str = Field(
        default="email_change_success.html",
        title="Backend email-change success page template",
        description=(
            "Jinja2 template rendered by ``GET /auth/email-change/{token}`` on success."
        ),
        examples=["email_change_success.html"],
    )
    AUTH_EMAIL_CHANGE_ERROR_TEMPLATE: str = Field(
        default="email_change_error.html",
        title="Backend email-change error page template",
        description=(
            "Jinja2 template rendered when the email-change token is "
            "expired, already used, unknown, or the target email was "
            "taken in the meantime."
        ),
        examples=["email_change_error.html"],
    )
    AUTH_EMAIL_VERIFICATION_SUCCESS_TEMPLATE: str = Field(
        default="email_verification_success.html",
        title="Backend email-verification success page template",
        description=(
            "Jinja2 template rendered by ``GET /auth/email-verify/{token}`` on success."
        ),
        examples=["email_verification_success.html"],
    )
    AUTH_EMAIL_VERIFICATION_ERROR_TEMPLATE: str = Field(
        default="email_verification_error.html",
        title="Backend email-verification error page template",
        description=(
            "Jinja2 template rendered when the verification token is "
            "expired, already used, or unknown."
        ),
        examples=["email_verification_error.html"],
    )
    AUTH_MFA_ENABLED: bool = Field(
        default=False,
        title="MFA endpoints kill-switch",
        description=(
            "When ``True``, ``make_auth_router`` mounts the four "
            "``POST /auth/mfa/*`` endpoints and the login flow "
            "issues an ``mfa_token`` for users with TOTP enabled. "
            "When ``False`` (default), MFA endpoints respond ``404`` "
            "and the login flow ignores any persisted TOTP secret — "
            "useful as a global kill-switch in case of "
            "Authenticator outage."
        ),
        examples=[False, True],
    )
    AUTH_MFA_ISSUER: str = Field(
        default="Tempest",
        title="MFA issuer label",
        description=(
            "Issuer shown next to the user's email inside the "
            "Authenticator app (Google Authenticator, 1Password, "
            "Authy, etc.). Use your product's user-facing name."
        ),
        examples=["Tempest", "Acme Inc.", "MyApp Production"],
    )
    AUTH_MFA_RECOVERY_CODES_COUNT: int = Field(
        default=10,
        ge=2,
        le=50,
        title="Recovery codes per enrollment",
        description=(
            "Number of single-use recovery codes generated when the "
            "user enrolls in MFA. Shown ONCE during enrollment; the "
            "SDK stores only the SHA-256 hash of each code."
        ),
        examples=[6, 10, 20],
    )
    AUTH_MFA_TOKEN_TTL_SECONDS: int = Field(
        default=300,
        ge=30,
        le=900,
        title="Intermediate MFA token TTL (seconds)",
        description=(
            "Lifetime of the short-lived JWT issued after step 1 of "
            "login (password OK) and consumed by step 2 (TOTP code). "
            "Defaults to 5 minutes — long enough for the user to "
            "open their Authenticator, short enough to neutralize "
            "interception."
        ),
        examples=[120, 300, 600],
    )
    AUTH_MFA_VERIFY_WINDOW: int = Field(
        default=1,
        ge=0,
        le=4,
        title="TOTP verification window (30s steps)",
        description=(
            "Tolerance in 30-second steps for clock drift between "
            "the user's device and the server. ``1`` (default) "
            "accepts previous + current + next step (90s window). "
            "Higher values weaken security; ``0`` is strict."
        ),
        examples=[0, 1, 2],
    )
    AUTH_WEBAUTHN_ENABLED: bool = Field(
        default=False,
        title="WebAuthn / passkey endpoints kill-switch",
        description=(
            "When ``True``, ``make_auth_router`` mounts the "
            "``/auth/webauthn/*`` endpoints. Requires a "
            "``WebAuthnService`` passed as ``webauthn=`` and the "
            "``[webauthn]`` extra. When ``False`` (default) the "
            "endpoints do not exist."
        ),
        examples=[False, True],
    )
    AUTH_WEBAUTHN_RP_ID: str = Field(
        default="",
        title="Relying-party ID (the credential's domain)",
        description=(
            "Domain the credential is bound to — the whole phishing "
            "resistance rests on it. Must be the site's origin domain "
            "or a registrable suffix of it (``example.com`` covers "
            "``app.example.com``; the reverse is invalid and the "
            "browser refuses the ceremony). Use ``localhost`` in "
            "development."
        ),
        examples=["example.com", "app.example.com", "localhost"],
    )
    AUTH_WEBAUTHN_RP_NAME: str = Field(
        default="Tempest",
        title="Relying-party display name",
        description=(
            "Product name shown by the browser and stored on the "
            "authenticator next to the credential."
        ),
        examples=["Tempest", "Acme Inc."],
    )
    AUTH_WEBAUTHN_ALLOWED_ORIGINS: list[str] = Field(
        default_factory=list,
        title="Origins accepted during a ceremony",
        description=(
            "Exact origins allowed to complete a ceremony. Empty "
            "(default) applies the ``fido2`` rule: ``https://`` plus "
            "the relying-party ID and its subdomains. Set it when the "
            "frontend runs somewhere that rule does not cover — a Vite "
            "dev server on ``http://localhost:5173``, for instance. "
            "Every entry here is a page allowed to spend the "
            "credential, so keep the list exact."
        ),
        examples=[[], ["https://app.example.com", "http://localhost:5173"]],
    )
    AUTH_WEBAUTHN_USER_VERIFICATION: Literal[
        "required",
        "preferred",
        "discouraged",
    ] = Field(
        default="preferred",
        title="User-verification requirement",
        description=(
            "Whether the authenticator must verify the *user* (PIN, "
            "biometric) and not merely their presence. ``required`` "
            "makes a passkey a genuine two-factor login on its own; "
            "``preferred`` (default) asks for it and accepts a key "
            "that cannot do it; ``discouraged`` skips the prompt."
        ),
        examples=["preferred", "required", "discouraged"],
    )
    AUTH_WEBAUTHN_RESIDENT_KEY: Literal[
        "required",
        "preferred",
        "discouraged",
    ] = Field(
        default="preferred",
        title="Resident (discoverable) credential requirement",
        description=(
            "Whether the authenticator must store the credential so it "
            "can be offered without the site naming an account — what "
            "makes usernameless login possible. ``required`` consumes "
            "one of a security key's limited credential slots; "
            "``preferred`` (default) asks for it without failing when "
            "the key cannot."
        ),
        examples=["preferred", "required", "discouraged"],
    )
    AUTH_WEBAUTHN_CHALLENGE_TTL_SECONDS: int = Field(
        default=300,
        ge=30,
        le=900,
        title="WebAuthn challenge TTL (seconds)",
        description=(
            "Lifetime of the server-side state between the *begin* and "
            "*complete* halves of a ceremony. Defaults to 5 minutes — "
            "long enough to find a security key, short enough that a "
            "captured challenge goes stale. The state is single-use "
            "regardless."
        ),
        examples=[120, 300, 600],
    )
    AUTH_SINGLE_ACTIVE_TOKEN: bool = Field(
        default=True,
        title="Only the newest link of each flow works",
        description=(
            "When ``True`` (default), issuing an account token spends "
            "every unused token of the **same purpose** the user still "
            "has, so only the most recent activation / password-reset / "
            "email-change link opens the account.\n\n"
            "This is the property that makes the victim's own correct "
            "reaction effective. An attacker requests a password reset "
            "for someone else; that person gets a recovery email they "
            "did not ask for, gets suspicious and resets the password "
            "themselves — and without this, the attacker's link stays "
            "valid until ``AUTH_PASSWORD_RESET_TTL_SECONDS``, so a "
            "token leaked through any side channel still resets the "
            "password after the incident looked handled.\n\n"
            "Set ``False`` only for a flow that deliberately keeps "
            "several links alive at once — and note that the SDK "
            "behaved this way before v0.274.0, so this is the flag that "
            "restores it."
        ),
        examples=[True, False],
    )
    AUTH_OAUTH_ENABLED: bool = Field(
        default=False,
        title="Social-login endpoints kill-switch",
        description=(
            "When ``True``, ``make_auth_router`` mounts the "
            "``/auth/oauth/*`` endpoints. Requires at least one "
            "configured client passed as ``oauth_clients=``, a "
            "``UserAuthService`` wired with an ``oauth_account_model``, "
            "and a user model carrying ``NameMixin``; each missing "
            "piece is refused at router construction. When ``False`` "
            "(default) the endpoints do not exist."
        ),
        examples=[False, True],
    )
    AUTH_OAUTH_STATE_COOKIE_NAME: str = Field(
        default="oauth_state",
        title="CSRF state cookie name",
        description=(
            "Cookie the login redirect writes and the callback compares "
            "against the ``state`` query parameter. The comparison is "
            "what makes a forged callback unusable, so the cookie is "
            "``HttpOnly`` and scoped to the auth prefix. It carries the "
            "provider key alongside the random value, so a state minted "
            "for one provider cannot be replayed at another's callback."
        ),
        examples=["oauth_state"],
    )
    AUTH_OAUTH_STATE_TTL_SECONDS: int = Field(
        default=600,
        ge=30,
        le=3600,
        title="CSRF state lifetime (seconds)",
        description=(
            "How long the state cookie survives, and therefore how long "
            "the user has to finish consenting at the provider. Ten "
            "minutes by default — long enough to type a password and "
            "clear a second factor, short enough that an abandoned tab "
            "cannot complete a login an hour later."
        ),
        examples=[300, 600, 900],
    )
    AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL: bool = Field(
        default=False,
        title="Attach a new provider identity to an existing account by email",
        description=(
            "When ``True``, a first-time callback whose email already "
            "belongs to a local account links the two instead of "
            "failing on the unique-email constraint — and **only** when "
            "the provider explicitly states it verified that address "
            "(``email_verified is True``; an unstated flag is not a "
            "yes). Default ``False``, because this is the setting that "
            "turns a provider's word about an email into control of an "
            "existing account: an IdP that lets a user set any address "
            "without proving it hands over every account whose email "
            "was guessed. Turn it on only for providers you trust to "
            "verify, and read the recipe's warning first."
        ),
        examples=[False, True],
    )
    AUTH_OAUTH_ALLOW_ACCOUNT_CREATION: bool | None = Field(
        default=None,
        title="Whether the OAuth callback may create an account",
        description=(
            "The OAuth callback is a signup door too: the first time an "
            "unknown identity arrives, it either creates a user row or "
            "refuses. ``None`` (default) inherits "
            "``AUTH_SIGNUP_ENABLED``, so closing the front door closes "
            "this one with it. Set ``True`` on a closed system that "
            "still wants onboarding through the provider; set ``False`` "
            "to keep an open signup form while restricting social login "
            "to identities an administrator already linked."
        ),
        examples=[None, True, False],
    )
    AUTH_TOKEN_DELIVERY: Literal["bearer", "cookie", "both"] = Field(
        default="bearer",
        title="How login/refresh return the JWT pair",
        description=(
            "Controls how ``make_auth_router`` delivers the "
            "``access_token`` / ``refresh_token`` pair.\n\n"
            "* ``bearer`` (default) — tokens returned in the JSON body "
            "only; the client stores them and sends "
            "``Authorization: Bearer <token>``. Backward-compatible "
            "behaviour.\n"
            "* ``cookie`` — tokens set as ``HttpOnly`` cookies on the "
            "same ``/auth/login`` / ``/auth/refresh`` / ``/auth/logout`` "
            "paths; the body omits the token values (they stay "
            "``null``). The auth dependency reads the access token from "
            "the cookie. Safer against XSS.\n"
            "* ``both`` — the bearer endpoints stay at ``/auth/*`` and a "
            "parallel set of cookie endpoints is mounted at "
            "``/auth/cookie/*``, so a project can serve web (cookie) and "
            "mobile/API (bearer) clients from one backend."
        ),
        examples=["bearer", "cookie", "both"],
    )
    AUTH_COOKIE_SECURE: bool = Field(
        default=True,
        title="Flag auth cookies as Secure",
        description=(
            "When ``True`` (default) the auth cookies carry the "
            "``Secure`` flag, so browsers only send them back over "
            "HTTPS. Set to ``False`` ONLY when the API is served over "
            "plain HTTP (no TLS terminator in front) — otherwise the "
            "browser drops the cookie and the session never persists. "
            "Only relevant when ``AUTH_TOKEN_DELIVERY`` is ``cookie`` or "
            "``both``."
        ),
        examples=[True, False],
    )
    AUTH_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = Field(
        default="lax",
        title="SameSite attribute for auth cookies",
        description=(
            "``lax`` (default) suits a frontend served from the same "
            "site as the API. A cross-site SPA (different origin) needs "
            "``none`` — which the browser only accepts together with "
            "``AUTH_COOKIE_SECURE=True`` (HTTPS). ``strict`` blocks the "
            "cookie on all cross-site navigations."
        ),
        examples=["lax", "strict", "none"],
    )
    AUTH_COOKIE_DOMAIN: str | None = Field(
        default=None,
        title="Domain for auth cookies",
        description=(
            "Explicit cookie ``Domain``. ``None`` (default) binds the "
            "cookie to the exact host that served the response. Set it "
            "(e.g. ``.example.com``) to share the session across "
            "subdomains."
        ),
        examples=[None, ".example.com"],
    )
    AUTH_ACCESS_COOKIE_NAME: str = Field(
        default="access_token",
        title="Access-token cookie name",
        description="Cookie name that carries the short-lived access token.",
        examples=["access_token"],
    )
    AUTH_REFRESH_COOKIE_NAME: str = Field(
        default="refresh_token",
        title="Refresh-token cookie name",
        description=(
            "Cookie name that carries the long-lived refresh token. "
            "Scoped to the refresh endpoint path so it is not sent on "
            "ordinary requests."
        ),
        examples=["refresh_token"],
    )


class OAuthSettings(BaseAppSettings):
    """Credentials for the bundled social-login providers.

    Splits cleanly from :class:`AuthSettings` along one line: this
    mixin says **who you are at the provider**, and the ``AUTH_OAUTH_*``
    fields of ``AuthSettings`` say **what the bundled router does**. The
    router never reads this mixin — it receives clients the application
    already built — so a project that gets its credentials from a
    secret manager instead of the environment can skip it entirely and
    still use every ``/auth/oauth/*`` endpoint.

    The redirect URI is derived rather than declared. It has to match
    what is registered at the provider *byte for byte*, and it is fully
    determined by the public base URL, the router prefix and the
    provider key — so declaring it separately is one more string to
    keep in sync for no gained freedom. Give the base URL; take the
    callback from :meth:`oauth_redirect_uri`.

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
        OAUTH_REDIRECT_BASE_URL (str): Public origin the provider will
            redirect back to, scheme included and no trailing path
            (e.g. ``"https://api.example.com"``). Default: ``""``.
        OAUTH_GOOGLE_CLIENT_ID (str): OAuth client id from the Google
            Cloud console. Default: ``""``.
        OAUTH_GOOGLE_CLIENT_SECRET (str): Matching client secret.
            Default: ``""``.
        OAUTH_GOOGLE_EXTRA_AUDIENCES (list[str]): The project's other
            Google client ids — the Android and iOS ones — whose tokens
            ``POST /auth/oauth/google/token`` accepts. Default: ``[]``.
        OAUTH_GITHUB_CLIENT_ID (str): Client id of the GitHub OAuth
            app. Default: ``""``.
        OAUTH_GITHUB_CLIENT_SECRET (str): Matching client secret.
            Default: ``""``.
    """

    OAUTH_REDIRECT_BASE_URL: str = Field(
        default="",
        title="Public base URL the provider redirects back to",
        description=(
            "Scheme and host this service is reachable at from a "
            "browser, with no trailing slash and no path — the callback "
            "path is appended by ``oauth_redirect_uri``. In "
            "development this is the tunnel or ``http://localhost:8000``, "
            "not the container's internal address: the provider "
            "redirects the *user agent*, not itself."
        ),
        examples=["", "https://api.example.com", "http://localhost:8000"],
    )
    OAUTH_GOOGLE_CLIENT_ID: str = Field(
        default="",
        title="Google OAuth client id",
        description=(
            "``Client ID`` of the OAuth 2.0 credential created under "
            "APIs & Services in the Google Cloud console. Public — it "
            "travels in the authorize URL."
        ),
        examples=["", "1234567890-abc123.apps.googleusercontent.com"],
    )
    OAUTH_GOOGLE_CLIENT_SECRET: str = Field(
        default="",
        title="Google OAuth client secret",
        description=(
            "``Client secret`` paired with the id above. Sent only "
            "server-to-server on the token exchange — never put it in a "
            "frontend bundle."
        ),
        examples=["", "GOCSPX-…"],
    )
    OAUTH_GOOGLE_EXTRA_AUDIENCES: list[str] = Field(
        default_factory=list,
        title="Google client ids of this project's other platforms",
        description=(
            "Google issues one client id per platform, so a token minted "
            "by the Android app carries the **Android** id in ``aud``, "
            "not the one above. List those ids here and the token-in-hand "
            "endpoint accepts them; leave them out and every mobile login "
            "is refused as issued to another application.\n\n"
            "Every value is an application allowed to log people into "
            "this service — put this project's ids here, and only those."
        ),
        examples=[[], ["1234567890-android.apps.googleusercontent.com"]],
    )
    OAUTH_GITHUB_CLIENT_ID: str = Field(
        default="",
        title="GitHub OAuth app client id",
        description=(
            "``Client ID`` of the OAuth app registered under Developer "
            "settings. Public — it travels in the authorize URL."
        ),
        examples=["", "Iv1.a1b2c3d4e5f6"],
    )
    OAUTH_GITHUB_CLIENT_SECRET: str = Field(
        default="",
        title="GitHub OAuth app client secret",
        description=(
            "``Client secret`` paired with the id above. Sent only "
            "server-to-server on the token exchange."
        ),
        examples=["", "ghp_…"],
    )

    def oauth_redirect_uri(self, provider: str, *, prefix: str = "/auth") -> str:
        """Build the callback URL to register with ``provider``.

        The value must match the provider's registered redirect URI
        exactly — a trailing slash or an ``http`` where the console
        holds ``https`` is rejected at the *authorize* step, before the
        user ever sees a consent screen, which makes it look like a
        credential problem. Deriving it here keeps the string that
        reaches the provider and the string the router serves as one
        expression.

        Args:
            provider (str): Provider key as the router routes it
                (``"google"``, ``"github"``, or whatever key the
                application registered an ``OIDCProvider`` under).
            prefix (str): The ``make_auth_router`` prefix. Defaults to
                ``"/auth"``; pass the same value you passed the router
                if you changed it.

        Returns:
            str: The absolute callback URL.
        """
        return (
            f"{self.OAUTH_REDIRECT_BASE_URL.rstrip('/')}"
            f"{prefix}/oauth/{provider}/callback"
        )

    def google_kwargs(self, *, prefix: str = "/auth") -> dict[str, Any]:
        """Map these settings onto ``GoogleOAuthClient``.

        ```python
        from tempest_fastapi_sdk import GoogleOAuthClient

        client: GoogleOAuthClient = GoogleOAuthClient(**settings.google_kwargs())
        ```

        Args:
            prefix (str): The ``make_auth_router`` prefix, forwarded to
                :meth:`oauth_redirect_uri`. Defaults to ``"/auth"``.

        Returns:
            dict[str, Any]: ``client_id``, ``client_secret``, the derived
            ``redirect_uri`` and ``extra_audiences`` — the last one being
            what lets a token minted by this project's Android or iOS
            client pass the token-in-hand endpoint's audience check.
            Scopes are left to the client's own defaults
            (``openid email profile``).
        """
        return {
            "client_id": self.OAUTH_GOOGLE_CLIENT_ID,
            "client_secret": self.OAUTH_GOOGLE_CLIENT_SECRET,
            "redirect_uri": self.oauth_redirect_uri("google", prefix=prefix),
            "extra_audiences": list(self.OAUTH_GOOGLE_EXTRA_AUDIENCES),
        }

    def github_kwargs(self, *, prefix: str = "/auth") -> dict[str, Any]:
        """Map these settings onto ``GitHubOAuthClient``.

        Args:
            prefix (str): The ``make_auth_router`` prefix, forwarded to
                :meth:`oauth_redirect_uri`. Defaults to ``"/auth"``.

        Returns:
            dict[str, Any]: ``client_id``, ``client_secret`` and the
            derived ``redirect_uri``. Scopes are left to the client's
            own defaults (``read:user user:email``).
        """
        return {
            "client_id": self.OAUTH_GITHUB_CLIENT_ID,
            "client_secret": self.OAUTH_GITHUB_CLIENT_SECRET,
            "redirect_uri": self.oauth_redirect_uri("github", prefix=prefix),
        }


class MinIOSettings(BaseAppSettings):
    """MinIO / S3-compatible object storage configuration.

    Consumed by :class:`tempest_fastapi_sdk.AsyncMinIOClient`. The
    same shape works for any S3-compatible target (AWS S3, MinIO,
    Backblaze B2, Cloudflare R2, Wasabi, DigitalOcean Spaces).

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
        MINIO_ENDPOINT (str): ``host[:port]`` without scheme.
            Default: ``"localhost:9000"``.
        MINIO_ACCESS_KEY (str): S3 access key / IAM user.
            Default: ``"minioadmin"``.
        MINIO_SECRET_KEY (str): S3 secret key. Default: ``"minioadmin"``.
        MINIO_SECURE (bool): Use HTTPS when ``True``. Default: ``False``.
        MINIO_REGION (str): S3 region. Default: ``"us-east-1"``.
        MINIO_DEFAULT_BUCKET (str): Bucket ensured and used as the implicit
            target. Default: ``"uploads"``.
        MINIO_PUBLIC_ENDPOINT (str | None): Public host presigned URLs are
            signed against, when the browser can't reach ``MINIO_ENDPOINT``
            directly. ``None`` reuses ``MINIO_ENDPOINT``. Default: ``None``.
        MINIO_PUBLIC_SECURE (bool | None): HTTPS for the public endpoint;
            ``None`` falls back to ``MINIO_SECURE``. Default: ``None``.
    """

    MINIO_ENDPOINT: str = Field(
        default="localhost:9000",
        title="MinIO endpoint",
        description="``host[:port]`` without scheme.",
        examples=[
            "localhost:9000",
            "minio.internal:9000",
            "s3.amazonaws.com",
        ],
    )
    MINIO_ACCESS_KEY: str = Field(
        default="minioadmin",
        title="Access key",
        description="S3 access key / IAM user.",
        examples=["minioadmin", "AKIAIOSFODNN7EXAMPLE"],
    )
    MINIO_SECRET_KEY: str = Field(
        default="minioadmin",
        title="Secret key",
        description="S3 secret key — keep out of source.",
        examples=["minioadmin", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"],
    )
    MINIO_SECURE: bool = Field(
        default=False,
        title="Use HTTPS",
        description=(
            "Use HTTPS when ``True``. Default ``False`` because local "
            "MinIO ships plain HTTP; **always** enable in production."
        ),
        examples=[False, True],
    )
    MINIO_REGION: str = Field(
        default="us-east-1",
        title="S3 region",
        description=(
            "S3 region. MinIO defaults to ``us-east-1``; AWS deployments "
            "should override."
        ),
        examples=["us-east-1", "us-west-2", "eu-west-1", "sa-east-1"],
    )
    MINIO_DEFAULT_BUCKET: str = Field(
        default="uploads",
        title="Default bucket name",
        description=(
            "Bucket created by :meth:`AsyncMinIOClient.ensure_bucket` "
            "and used as the implicit target for object operations."
        ),
        examples=["uploads", "media", "user-content"],
    )
    MINIO_PUBLIC_ENDPOINT: str | None = Field(
        default=None,
        title="Public endpoint for presigned URLs",
        description=(
            "Split-endpoint mode: when set, presigned upload/download "
            "URLs are signed against **this** host while every "
            "server-side operation keeps using ``MINIO_ENDPOINT``. Use it "
            "when the backend reaches MinIO over a fast private network "
            "(e.g. ``servus-storage:9000``) but the browser must hit a "
            "public, TLS-terminated host (e.g. "
            "``storage.example.com``). ``None`` (default) signs presigned "
            "URLs with ``MINIO_ENDPOINT`` — unchanged single-endpoint "
            "behaviour."
        ),
        examples=[None, "storage.example.com", "https://storage.example.com"],
    )
    MINIO_PUBLIC_SECURE: bool | None = Field(
        default=None,
        title="Use HTTPS for the public endpoint",
        description=(
            "Whether the public endpoint uses HTTPS. ``None`` (default) "
            "falls back to ``MINIO_SECURE``. Set explicitly when the "
            "private endpoint is plain HTTP but the public one is HTTPS. "
            "A ``https://`` scheme on ``MINIO_PUBLIC_ENDPOINT`` also "
            "implies HTTPS."
        ),
        examples=[None, True, False],
    )

    def minio_kwargs(self) -> dict[str, Any]:
        """Map these settings onto :class:`AsyncMinIOClient` kwargs.

        Returns:
            dict[str, Any]: Keyword arguments ready to splat into
            ``AsyncMinIOClient(**settings.minio_kwargs())``.
        """
        return {
            "endpoint": self.MINIO_ENDPOINT,
            "access_key": self.MINIO_ACCESS_KEY,
            "secret_key": self.MINIO_SECRET_KEY,
            "default_bucket": self.MINIO_DEFAULT_BUCKET,
            "secure": self.MINIO_SECURE,
            "region": self.MINIO_REGION,
            "public_endpoint": self.MINIO_PUBLIC_ENDPOINT,
            "public_secure": self.MINIO_PUBLIC_SECURE,
        }


class SessionSettings(BaseAppSettings):
    """Server-side session cookie + storage configuration.

    Consumed by :class:`tempest_fastapi_sdk.SessionAuth`,
    :class:`tempest_fastapi_sdk.SessionMiddleware`, and
    :func:`tempest_fastapi_sdk.make_session_router`. Defaults assume
    HTTPS in production (``SESSION_COOKIE_SECURE=True``) and a
    same-site SaaS topology (``SESSION_COOKIE_SAMESITE="lax"``) —
    relax both only for local HTTP development.

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
        SESSION_TTL_SECONDS (int): Lifetime of a server-side session.
            Default: ``86400``.
        SESSION_SLIDING (bool): Refresh ``expires_at`` on every request.
            Default: ``True``.
        SESSION_COOKIE_NAME (str): Name of the session cookie.
            Default: ``"tempest_session"``.
        SESSION_COOKIE_DOMAIN (str | None): Cookie ``Domain`` attribute;
            ``None`` scopes to the issuing host. Default: ``None``.
        SESSION_COOKIE_PATH (str): Cookie ``Path`` attribute. Default: ``"/"``.
        SESSION_COOKIE_SECURE (bool): Send the cookie only over HTTPS.
            Default: ``True``.
        SESSION_COOKIE_HTTPONLY (bool): Hide the cookie from page JavaScript.
            Default: ``True``.
        SESSION_COOKIE_SAMESITE (str): Cookie ``SameSite`` policy
            (``lax``/``strict``/``none``). Default: ``"lax"``.
        SESSION_ROTATE_ON_LOGIN (bool): Issue a new session id on login.
            Default: ``True``.
    """

    SESSION_TTL_SECONDS: int = Field(
        default=86_400,
        ge=60,
        title="Session TTL (seconds)",
        description=(
            "Lifetime of a server-side session, in seconds. The cookie's "
            "``Max-Age`` and the store's TTL both track this value. "
            "Defaults to 24 hours."
        ),
        examples=[3600, 86_400, 86_400 * 7],
    )
    SESSION_SLIDING: bool = Field(
        default=True,
        title="Slide TTL on activity",
        description=(
            "When ``True``, every resolved request refreshes "
            "``expires_at`` to ``now + SESSION_TTL_SECONDS`` so an "
            "active user is never logged out. When ``False``, the "
            "session expires exactly at ``created_at + TTL`` even if "
            "the user is online."
        ),
        examples=[True, False],
    )
    SESSION_COOKIE_NAME: str = Field(
        default="tempest_session",
        title="Cookie name",
        description=(
            "Name of the ``Set-Cookie`` header value carrying the "
            "plaintext session id. Pick something app-specific in "
            "production so it does not collide with sibling services "
            "on the same domain."
        ),
        examples=["tempest_session", "myapp_sid"],
    )
    SESSION_COOKIE_DOMAIN: str | None = Field(
        default=None,
        title="Cookie domain",
        description=(
            "``Domain`` attribute on the cookie. ``None`` (default) "
            "scopes the cookie to the exact host that issued it; set "
            "to ``.example.com`` to share across subdomains."
        ),
        examples=[None, ".example.com"],
    )
    SESSION_COOKIE_PATH: str = Field(
        default="/",
        title="Cookie path",
        description="``Path`` attribute on the cookie.",
        examples=["/", "/app"],
    )
    SESSION_COOKIE_SECURE: bool = Field(
        default=True,
        title="Cookie Secure flag",
        description=(
            "When ``True`` (default), browsers only send the cookie "
            "over HTTPS. Set to ``False`` ONLY for local plain-HTTP "
            "development."
        ),
        examples=[True, False],
    )
    SESSION_COOKIE_HTTPONLY: bool = Field(
        default=True,
        title="Cookie HttpOnly flag",
        description=(
            "When ``True`` (default), JavaScript on the page cannot "
            "read the cookie value — defense against XSS-driven "
            "session theft. There is essentially no reason to set "
            "this to ``False``."
        ),
        examples=[True, False],
    )
    SESSION_COOKIE_SAMESITE: str = Field(
        default="lax",
        pattern="^(lax|strict|none)$",
        title="Cookie SameSite policy",
        description=(
            "``lax`` (default) — sent on top-level cross-site GETs but "
            "not on cross-site POSTs. ``strict`` — never sent on "
            "cross-site requests. ``none`` — sent everywhere, **requires** "
            "``SESSION_COOKIE_SECURE=True``."
        ),
        examples=["lax", "strict", "none"],
    )
    SESSION_ROTATE_ON_LOGIN: bool = Field(
        default=True,
        title="Rotate session id on login",
        description=(
            "When ``True`` (default), :meth:`SessionAuth.login` "
            "issues a brand-new session id even when the same "
            "browser already had one — closes session-fixation "
            "vectors where an attacker plants a known id before login."
        ),
        examples=[True, False],
    )


class WebSocketSettings(BaseAppSettings):
    """WebSocket router configuration.

    Consumed by :func:`tempest_fastapi_sdk.make_websocket_router` and
    :class:`tempest_fastapi_sdk.WebSocketHub`. Defaults are tuned for
    typical browser ↔ FastAPI deployments — heartbeats every 30s,
    drop after 60s without pong, five concurrent connections per
    user.

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
        WS_HEARTBEAT_SECONDS (int): How often the server sends a ping frame.
            Default: ``30``.
        WS_HEARTBEAT_TIMEOUT_SECONDS (int): Max ping-to-pong delay before
            force-close. Default: ``60``.
        WS_MAX_CONNECTIONS_PER_USER (int): Cap on concurrent connections per
            user. Default: ``5``.
        WS_MAX_MESSAGE_BYTES (int): Reject inbound frames larger than this.
            Default: ``65536``.
    """

    WS_HEARTBEAT_SECONDS: int = Field(
        default=30,
        ge=1,
        title="Heartbeat interval (seconds)",
        description=(
            'How often the server sends a ``{"type": "ping"}`` frame '
            "to keep the connection alive through HTTP proxies that "
            "close idle sockets. Pair with "
            "``WS_HEARTBEAT_TIMEOUT_SECONDS`` so a stuck peer is "
            "evicted instead of held open forever."
        ),
        examples=[15, 30, 60],
    )
    WS_HEARTBEAT_TIMEOUT_SECONDS: int = Field(
        default=60,
        ge=1,
        title="Heartbeat timeout (seconds)",
        description=(
            "Maximum delay between the server's ``ping`` and the "
            "matching client ``pong`` before the connection is "
            "force-closed with WebSocket code ``4408``."
        ),
        examples=[30, 60, 120],
    )
    WS_MAX_CONNECTIONS_PER_USER: int = Field(
        default=5,
        ge=1,
        title="Max concurrent connections per user",
        description=(
            "Cap on how many WebSocket connections the same authenticated "
            "user may hold open at once. The oldest connection is closed "
            "with code ``4429`` when the cap is exceeded."
        ),
        examples=[3, 5, 20],
    )
    WS_MAX_MESSAGE_BYTES: int = Field(
        default=64 * 1024,
        ge=1,
        title="Max incoming frame size (bytes)",
        description=(
            "Reject inbound frames larger than this — protects the "
            "process from memory-exhaustion attacks via oversized "
            "messages. The connection is closed with code ``1009`` "
            "(message too big)."
        ),
        examples=[4 * 1024, 64 * 1024, 1024 * 1024],
    )


class GenAISettings(BaseAppSettings):
    """Where model weights live, and whether loads may use the network.

    The genai loaders read these three variables **as defaults**: an
    argument passed to a loader always wins, so a service pins the cache
    and the offline switch once — compose, chart, systemd unit — and a
    call that needs something different still says so per model.

    ```python
    from tempest_fastapi_sdk import BaseAppSettings, GenAISettings


    class Settings(GenAISettings, BaseAppSettings):
        pass
    ```

    ```bash
    # .env — read by the loaders even without the class above
    GENAI_CACHE_DIR=/models      # survives a container restart
    GENAI_OFFLINE=true           # never reach the Hub at load time
    GENAI_HF_TOKEN=hf_xxx        # gated repos, and no anonymous rate limit
    ```

    Declaring the class is what makes the values typed, documented and
    visible in `tempest check-config`; the loaders themselves read the
    environment directly, so a service that never declares it still gets
    the same behaviour.

    Attributes:
        GENAI_CACHE_DIR (str | None): Weight cache directory. ``None``
            keeps the ``huggingface_hub`` default (``$HF_HOME/hub``, else
            ``~/.cache/huggingface/hub``). Default: ``None``.
        GENAI_OFFLINE (bool): Load from that cache without touching the
            network. Default: ``False``.
        GENAI_HF_TOKEN (str | None): Hub token for gated or private
            repositories. Default: ``None``.
    """

    GENAI_CACHE_DIR: str | None = Field(
        default=None,
        title="Model weight cache directory",
        description=(
            "Where downloaded weights are written and read back from. "
            "``None`` uses the ``huggingface_hub`` default. Point it at a "
            "mounted volume in a container, or every restart re-downloads "
            "the model."
        ),
        examples=["/models", "/var/lib/models"],
    )
    GENAI_OFFLINE: bool = Field(
        default=False,
        title="Offline model loading",
        description=(
            "Load weights from the cache and never reach the Hub — what an "
            "air-gapped or deploy-frozen host wants. It also silences the "
            "Hub's anonymous rate-limit warning, which is printed by the "
            "revision check, not by a download."
        ),
        examples=[True, False],
    )
    GENAI_HF_TOKEN: str | None = Field(
        default=None,
        title="HuggingFace Hub token",
        description=(
            "Token for gated or private repositories. Without it downloads "
            "are anonymous and rate-limited."
        ),
        examples=["hf_xxx"],
    )


class OpenPixSettings(BaseAppSettings):
    """OpenPix / Woovi credentials and environment.

    Every other integration with a credential ships a mixin like this one;
    OpenPix did not, so a service repeated the AppID field and the base-URL
    lookup by hand. :meth:`openpix_kwargs` closes that, and it also hides the
    one thing about this API that is easy to get wrong: the AppID travels in
    ``Authorization`` **raw**, with no ``Bearer`` prefix.

    The environment is a string here rather than
    :class:`~tempest_fastapi_sdk.integrations.payment.openpix.OpenPixEnvironment`
    on purpose: settings must not pull the integrations namespace, which is
    lazy so ``import tempest_fastapi_sdk`` never pays for 373 generated names.
    :meth:`openpix_kwargs` imports the enum when it runs, so the base URL still
    has a single source of truth.

    Deliberately no ``enabled`` property, unlike the push mixins: three of them
    already define one, and a service that takes Pix **and** sends push would
    inherit a collision. Check ``bool(settings.OPENPIX_APP_ID)`` instead.

    Each attribute below is also the name of the environment variable that sets
    it (matched case-sensitively, no prefix).

    Attributes:
        OPENPIX_APP_ID (str): The AppID issued by OpenPix/Woovi, sent raw in
            ``Authorization``. Default: ``""`` (calls will be rejected).
        OPENPIX_ENVIRONMENT (Literal["production", "sandbox"]): Which API to
            talk to. Default: ``"sandbox"`` — pointing at production by
            accident charges real money, while pointing at sandbox by accident
            fails loudly.
    """

    OPENPIX_APP_ID: str = Field(
        default="",
        title="OpenPix AppID",
        description=(
            "AppID issued by OpenPix/Woovi. Sent raw in the ``Authorization`` "
            "header — no ``Bearer`` prefix."
        ),
        examples=["", "Q2xpZW50X0lkX2E1…"],
    )
    OPENPIX_ENVIRONMENT: Literal["production", "sandbox"] = Field(
        default="sandbox",
        title="OpenPix environment",
        description=(
            "Which API to talk to. Sandbox by default: charging real money by "
            "accident is worse than failing against the test host."
        ),
        examples=["sandbox", "production"],
    )

    def openpix_kwargs(self) -> dict[str, Any]:
        """Map these settings onto the ``HTTPClient`` the client wraps.

        :class:`~tempest_fastapi_sdk.integrations.payment.openpix.OpenPixClient`
        takes an already-configured ``HTTPClient`` and reads its credentials
        from ``default_headers``, so this returns what that client needs::

            client = OpenPixClient(HTTPClient(**settings.openpix_kwargs()))

        Returns:
            dict[str, Any]: ``base_url`` resolved from the environment, plus
            the ``Authorization`` header carrying the AppID.
        """
        from tempest_fastapi_sdk.integrations.payment.openpix.environment import (
            OpenPixEnvironment,
        )

        environment = OpenPixEnvironment(self.OPENPIX_ENVIRONMENT)
        return {
            "base_url": environment.base_url,
            "default_headers": {"Authorization": self.OPENPIX_APP_ID},
        }


class MercadoPagoSettings(BaseAppSettings):
    """Mercado Pago credentials.

    Two secrets with different jobs: the access token authenticates *you*
    to Mercado Pago, and the webhook secret authenticates *them* to you.
    Both are here because a service that charges needs both, and leaving
    the second one to be improvised is how an unverified webhook endpoint
    ships.

    Deliberately no environment field, unlike
    :class:`OpenPixSettings`. Measured on the pinned specification: Mercado
    Pago declares a **single** server, ``https://api.mercadopago.com``, so
    what separates a test charge from a real one is which token you hold.
    An environment enum here would suggest a safety net that does not
    exist — and would let someone believe a "sandbox" setting protects them
    while a production token moves real money.

    Each attribute below is also the name of the environment variable that
    sets it (matched case-sensitively, no prefix).

    Attributes:
        MERCADOPAGO_ACCESS_TOKEN (str): The access token, sent as
            ``Authorization: Bearer <token>``. Default: ``""`` (calls will
            be rejected).
        MERCADOPAGO_WEBHOOK_SECRET (str): The secret that signs incoming
            notifications. Default: ``""`` — and an empty secret makes
            ``verify_signature`` return ``False`` for everything, rather
            than accepting everything.
    """

    MERCADOPAGO_ACCESS_TOKEN: str = Field(
        default="",
        title="Mercado Pago access token",
        description=(
            "Access token issued by Mercado Pago. Sent as "
            "``Authorization: Bearer <token>``. A test token and a "
            "production token differ only in value — the host is the same."
        ),
        examples=["", "APP_USR-1234567890abcdef-…"],
    )
    MERCADOPAGO_WEBHOOK_SECRET: str = Field(
        default="",
        title="Mercado Pago webhook secret",
        description=(
            "Secret from the dashboard used to verify the ``x-signature`` "
            "header on incoming notifications. Empty rejects every "
            "delivery, which is the safe default."
        ),
        examples=[""],
    )

    def mercado_pago_kwargs(self) -> dict[str, Any]:
        """Map these settings onto the ``HTTPClient`` the client wraps.

        Returns:
            dict[str, Any]: ``base_url`` and the ``Authorization`` header,
            ready for::

                client = MercadoPagoClient(
                    HTTPClient(**settings.mercado_pago_kwargs())
                )

        Note the ``Bearer`` prefix — the opposite of OpenPix, which takes
        its AppID raw. Getting that backwards is a 401 on every call, and
        it is exactly the kind of thing this method exists to settle once.
        """
        from tempest_fastapi_sdk.integrations.payment.mercado_pago.environment import (
            DEFAULT_BASE_URL,
        )

        return {
            "base_url": DEFAULT_BASE_URL,
            "default_headers": {
                "Authorization": f"Bearer {self.MERCADOPAGO_ACCESS_TOKEN}"
            },
        }


__all__: list[str] = [
    "AuthSettings",
    "CORSSettings",
    "DatabaseSettings",
    "EmailSettings",
    "FirebaseSettings",
    "GenAISettings",
    "JWTSettings",
    "LogSettings",
    "MercadoPagoSettings",
    "MinIOSettings",
    "OAuthSettings",
    "OpenPixSettings",
    "PushSettings",
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
