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

Every field carries ``title``, ``description`` and ``examples`` so
JSON-Schema consumers (FastAPI ``/docs``, ``/redoc``, IDE tooling,
``pydantic.model_json_schema()``) render rich metadata out of the
box.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class ServerSettings(BaseSettings):
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


class LogSettings(BaseSettings):
    """Structured logging configuration.

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
        LOG_LEVEL (str): Default logger level for ``configure_logging``.
            Default: ``"INFO"``.
        LOG_JSON (bool): Emit stdout logs as JSON. Default: ``True``.
        LOG_DIR (str): Directory for per-level + ``500.log`` files; empty
            disables file logging. Default: ``"logs"``.
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


class DatabaseSettings(BaseSettings):
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
        }


class RedisSettings(BaseSettings):
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


class RabbitMQSettings(BaseSettings):
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


class JWTSettings(BaseSettings):
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


class CORSSettings(BaseSettings):
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


class EmailSettings(BaseSettings):
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


class UploadSettings(BaseSettings):
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


class TokenSettings(BaseSettings):
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


class WebPushSettings(BaseSettings):
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


class TaskIQSettings(BaseSettings):
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


class AuthSettings(BaseSettings):
    """Configuration for the bundled signup / activation / reset flows.

    Consumed by :class:`tempest_fastapi_sdk.auth.UserAuthService`
    and :func:`tempest_fastapi_sdk.make_auth_router`. Each flag
    has a sensible production default; flip ``AUTH_AUTO_ACTIVATE``
    or ``AUTH_RETURN_TOKEN_IN_RESPONSE`` only in dev / CI.

    Each attribute below is also the name of the environment variable
    that sets it (matched case-sensitively, no prefix).

    Attributes:
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
    """

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
            "``pt-BR``. Emails always use this locale (they have no "
            "request context); the backend HTML pages prefer the "
            "browser's ``Accept-Language`` header and fall back to this."
        ),
        examples=["pt-BR", "en-US"],
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


class MinIOSettings(BaseSettings):
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
        }


class SessionSettings(BaseSettings):
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


class WebSocketSettings(BaseSettings):
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


__all__: list[str] = [
    "AuthSettings",
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
