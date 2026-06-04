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

from pydantic import Field
from pydantic_settings import BaseSettings


class ServerSettings(BaseSettings):
    """HTTP server bind configuration."""

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
    """Structured logging configuration."""

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
    """SQLAlchemy database connection configuration."""

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


class RedisSettings(BaseSettings):
    """Redis connection configuration."""

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


class RabbitMQSettings(BaseSettings):
    """RabbitMQ / FastStream broker configuration."""

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
    """JWT signing and verification configuration."""

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


class CORSSettings(BaseSettings):
    """CORS middleware configuration.

    .. warning::
        The default ``CORS_ORIGINS=["*"]`` is permissive on purpose
        so local development works out of the box. **Never** ship
        this default to production — set ``CORS_ORIGINS`` to the
        explicit list of trusted frontend origins. ``"*"`` is also
        incompatible with ``CORS_ALLOW_CREDENTIALS=True`` (browsers
        ignore credentialed requests sent to a wildcard origin).
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


class UploadSettings(BaseSettings):
    """File upload constraints.

    Mirrors the constructor arguments of
    :class:`tempest_fastapi_sdk.UploadUtils`.
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


class TokenSettings(BaseSettings):
    """Shared-secret ``X-Token`` configuration.

    Used by :func:`tempest_fastapi_sdk.make_token_dependency` for
    internal service-to-service authentication. Validation is performed
    with :func:`hmac.compare_digest`.
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


class TaskIQSettings(BaseSettings):
    """TaskIQ broker / result backend configuration.

    Use this when the TaskIQ broker is **not** the same RabbitMQ /
    Redis instance covered by :class:`RabbitMQSettings` /
    :class:`RedisSettings`.
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
        ge=8,
        title="Minimum password length (chars)",
        description=(
            "Signup + reset reject passwords shorter than this. "
            "Bumped from the OWASP 8-char floor to 12 because "
            "longer passwords are the single biggest brute-force "
            "deterrent."
        ),
        examples=[8, 12, 16],
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


class MinIOSettings(BaseSettings):
    """MinIO / S3-compatible object storage configuration.

    Consumed by :class:`tempest_fastapi_sdk.AsyncMinIOClient`. The
    same shape works for any S3-compatible target (AWS S3, MinIO,
    Backblaze B2, Cloudflare R2, Wasabi, DigitalOcean Spaces).
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
    "TaskIQSettings",
    "TokenSettings",
    "UploadSettings",
    "WebPushSettings",
]
