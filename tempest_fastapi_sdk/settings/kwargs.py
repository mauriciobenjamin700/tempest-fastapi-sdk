"""Typed dictionaries returned by settings ``*_kwargs()`` mappers.

Each :class:`~typing.TypedDict` matches the keyword arguments of the
constructor or factory the corresponding mapper is meant to be splatted
into (for example ``EmailUtils(**settings.email_kwargs())``). Keeping
these definitions in a dedicated module with zero heavy runtime imports
lets ``tempest_fastapi_sdk.settings`` stay free of optional extras while
giving mypy and basedpyright full checking on every ``**`` unpack.
"""

from collections.abc import Mapping
from datetime import timedelta
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

if TYPE_CHECKING:
    from faststream.security import BaseSecurity

_HostedProvider = Literal["anthropic", "openai", "google", "deepseek"]
_StructuredOutputMode = Literal["json_schema", "json_object"]


class LoggingKwargs(TypedDict):
    """Keyword arguments for :func:`~tempest_fastapi_sdk.configure_logging`.

    Returned by :meth:`~tempest_fastapi_sdk.settings.LogSettings.logging_kwargs`.

    Attributes:
        level (str): Root logger level (``LOG_LEVEL``).
        json_output (bool): Emit JSON records to stdout (``LOG_JSON``).
        log_dir (str): Directory for log files, or empty to disable file
            logging (``LOG_DIR``).
        max_bytes (int): Size-based rotation threshold in bytes
            (``LOG_MAX_BYTES``).
        backup_count (int): Rotated files kept per level
            (``LOG_BACKUP_COUNT``).
        rotate_when (str | None): Time-based rotation unit, or ``None`` for
            size-based rotation (``LOG_ROTATE_WHEN``).
        rotate_interval (int): Number of ``rotate_when`` units per period
            (``LOG_ROTATE_INTERVAL``).
        redact (bool): Apply the default redaction policy (``LOG_REDACT``).
    """

    level: str
    json_output: bool
    log_dir: str
    max_bytes: int
    backup_count: int
    rotate_when: str | None
    rotate_interval: int
    redact: bool


class DatabaseKwargs(TypedDict):
    """Keyword arguments for :class:`~tempest_fastapi_sdk.AsyncDatabaseManager`.

    Returned by :meth:`~tempest_fastapi_sdk.settings.DatabaseSettings.database_kwargs`.

    Attributes:
        db_url (str): Async SQLAlchemy connection URL (``DATABASE_URL``).
        echo (bool): Echo SQL statements (``DATABASE_ECHO``).
        pool_size (int): Persistent pool size (``DATABASE_POOL_SIZE``).
        max_overflow (int): Pool overflow capacity (``DATABASE_MAX_OVERFLOW``).
        pool_recycle (int): Connection recycle interval in seconds
            (``DATABASE_POOL_RECYCLE``).
        sqlite_wal (bool): Enable WAL mode on SQLite (``DATABASE_SQLITE_WAL``).
        sqlite_busy_timeout (float): SQLite lock wait timeout in seconds
            (``DATABASE_SQLITE_BUSY_TIMEOUT``).
        sqlite_foreign_keys (bool): Enforce SQLite foreign keys
            (``DATABASE_SQLITE_FOREIGN_KEYS``).
    """

    db_url: str
    echo: bool
    pool_size: int
    max_overflow: int
    pool_recycle: int
    sqlite_wal: bool
    sqlite_busy_timeout: float
    sqlite_foreign_keys: bool


class RedisKwargs(TypedDict):
    """Keyword arguments for :class:`~tempest_fastapi_sdk.cache.AsyncRedisManager`.

    Returned by :meth:`~tempest_fastapi_sdk.settings.RedisSettings.redis_kwargs`.

    Attributes:
        url (str): Redis connection URL (``REDIS_URL``).
        decode_responses (bool): Decode bytes to ``str``
            (``REDIS_DECODE_RESPONSES``).
    """

    url: str
    decode_responses: bool


class JWTKwargs(TypedDict):
    """Keyword arguments for :class:`~tempest_fastapi_sdk.JWTUtils`.

    Returned by :meth:`~tempest_fastapi_sdk.settings.JWTSettings.jwt_kwargs`.

    Attributes:
        secret (str): Shared signing secret (``JWT_SECRET``).
        algorithm (str): JOSE algorithm identifier (``JWT_ALGORITHM``).
        default_ttl (timedelta): Default access-token lifetime derived from
            ``JWT_ACCESS_TTL_SECONDS``.
        issuer (str | None): Optional ``iss`` claim (``JWT_ISSUER``).
    """

    secret: str
    algorithm: str
    default_ttl: timedelta
    issuer: str | None


class EmailKwargs(TypedDict):
    """Keyword arguments for :class:`~tempest_fastapi_sdk.EmailUtils`.

    Returned by :meth:`~tempest_fastapi_sdk.settings.EmailSettings.email_kwargs`.

    Attributes:
        host (str): SMTP host (``SMTP_HOST``).
        port (int): SMTP TCP port (``SMTP_PORT``).
        from_addr (str): Default sender address (``SMTP_FROM_ADDR``).
        username (str | None): Optional SMTP username (``SMTP_USERNAME``).
        password (str | None): Optional SMTP password (``SMTP_PASSWORD``).
        use_tls (bool): Implicit TLS from connect (``SMTP_USE_SSL``).
        use_starttls (bool): Negotiate STARTTLS after connect
            (``SMTP_USE_TLS``).
        timeout (float): Network timeout in seconds (``SMTP_TIMEOUT_SECONDS``).
        enabled (NotRequired[bool]): Present as ``False`` when
            ``EMAIL_ENABLED`` is off; omitted when enabled.
    """

    host: str
    port: int
    from_addr: str
    username: str | None
    password: str | None
    use_tls: bool
    use_starttls: bool
    timeout: float
    enabled: NotRequired[bool]


class UploadKwargs(TypedDict):
    """Keyword arguments for :class:`~tempest_fastapi_sdk.UploadUtils`.

    Returned by :meth:`~tempest_fastapi_sdk.settings.UploadSettings.upload_kwargs`.

    Attributes:
        source (str): Root directory for local uploads (``UPLOAD_DIR``).
        max_size_bytes (int): Hard file-size limit in bytes
            (``UPLOAD_MAX_SIZE_BYTES``).
        allowed_extensions (set[str]): Allowed lowercase extensions
            (``UPLOAD_ALLOWED_EXTENSIONS``).
        allowed_mimetypes (set[str]): Allowed MIME types
            (``UPLOAD_ALLOWED_MIMETYPES``).
    """

    source: str
    max_size_bytes: int
    allowed_extensions: set[str]
    allowed_mimetypes: set[str]


class WebPushKwargs(TypedDict):
    """Keyword arguments for :class:`~tempest_fastapi_sdk.WebPushDispatcher`.

    Returned by :meth:`~tempest_fastapi_sdk.settings.WebPushSettings.webpush_kwargs`.

    Attributes:
        vapid_private_key (str): URL-safe base64 VAPID private key
            (``VAPID_PRIVATE_KEY``).
        vapid_subject (str): ``mailto:`` or ``https://`` VAPID subject
            (``VAPID_SUBJECT``).
        ttl_seconds (int): Default notification TTL in seconds
            (``WEBPUSH_DEFAULT_TTL_SECONDS``).
    """

    vapid_private_key: str
    vapid_subject: str
    ttl_seconds: int


class FirebaseKwargs(TypedDict, total=False):
    """Keyword arguments for :class:`~tempest_fastapi_sdk.auth.FirebaseAuth`.

    Returned by :meth:`~tempest_fastapi_sdk.settings.FirebaseSettings.firebase_kwargs`.
    Every key is optional because unset credentials are omitted rather than
    forwarded as empty strings, and disabling the integration yields only
    ``{"enabled": False}``.

    Attributes:
        credentials_json (str): Inline service-account JSON
            (``FIREBASE_CREDENTIALS_JSON``).
        credentials_path (str): Path to a service-account JSON file
            (``FIREBASE_CREDENTIALS_PATH``).
        project_id (str): Firebase project id (``FIREBASE_PROJECT_ID``).
        enabled (bool): Present as ``False`` when ``FIREBASE_ENABLED`` is off.
    """

    credentials_json: str
    credentials_path: str
    project_id: str
    enabled: bool


class KafkaKwargs(TypedDict):
    """Keyword arguments for :meth:`~tempest_fastapi_sdk.queue.MessageBroker.kafka`.

    Returned by :meth:`~tempest_fastapi_sdk.settings.KafkaSettings.kafka_kwargs`.

    Attributes:
        security (BaseSecurity | None): FastStream security configuration for
            the chosen protocol/mechanism, or ``None`` under ``PLAINTEXT``.
        client_id (NotRequired[str]): Optional Kafka client identifier
            (``KAFKA_CLIENT_ID``), present only when configured.
    """

    security: "BaseSecurity | None"
    client_id: NotRequired[str]


class GoogleOAuthKwargs(TypedDict):
    """Keyword arguments for :class:`~tempest_fastapi_sdk.GoogleOAuthClient`.

    Returned by :meth:`~tempest_fastapi_sdk.settings.OAuthSettings.google_kwargs`.

    Attributes:
        client_id (str): Google OAuth client id (``OAUTH_GOOGLE_CLIENT_ID``).
        client_secret (str): Google OAuth client secret
            (``OAUTH_GOOGLE_CLIENT_SECRET``).
        redirect_uri (str): Derived OAuth callback URL.
        extra_audiences (list[str]): Additional accepted ID-token audiences
            (``OAUTH_GOOGLE_EXTRA_AUDIENCES``).
    """

    client_id: str
    client_secret: str
    redirect_uri: str
    extra_audiences: list[str]


class GitHubOAuthKwargs(TypedDict):
    """Keyword arguments for :class:`~tempest_fastapi_sdk.GitHubOAuthClient`.

    Returned by :meth:`~tempest_fastapi_sdk.settings.OAuthSettings.github_kwargs`.

    Attributes:
        client_id (str): GitHub OAuth client id (``OAUTH_GITHUB_CLIENT_ID``).
        client_secret (str): GitHub OAuth client secret
            (``OAUTH_GITHUB_CLIENT_SECRET``).
        redirect_uri (str): Derived OAuth callback URL.
    """

    client_id: str
    client_secret: str
    redirect_uri: str


class LocalStorageKwargs(TypedDict):
    """Keyword arguments for :class:`~tempest_fastapi_sdk.LocalObjectStorageClient`.

    Returned by
    :meth:`~tempest_fastapi_sdk.settings.StorageSettings.local_storage_kwargs`.

    Attributes:
        base_dir (str): Local storage root directory (``STORAGE_LOCAL_DIR``).
        public_url (str): Public base URL for signed links
            (``STORAGE_LOCAL_PUBLIC_URL``).
        signing_secret (str): Secret signing local upload/download URLs
            (``STORAGE_LOCAL_SIGNING_SECRET``).
        default_bucket (str): Default bucket name (``STORAGE_DEFAULT_BUCKET``).
        route_prefix (str): Mount prefix for signed local routes
            (``STORAGE_LOCAL_ROUTE_PREFIX``).
        max_upload_bytes (int): Maximum body size accepted by local ``PUT``
            (``STORAGE_LOCAL_MAX_UPLOAD_BYTES``).
        encryption_keys (NotRequired[dict[str, bytes]]): Decoded 32-byte
            AES-256-GCM keys, present only when
            ``STORAGE_LOCAL_ENCRYPTION_KEYS`` is non-empty.
        allow_plaintext_read (NotRequired[bool]): Whether unencrypted files
            may be read alongside encrypted ones, present only when
            ``STORAGE_LOCAL_ENCRYPTION_KEYS`` is non-empty.
        enabled (NotRequired[bool]): Present as ``False`` when
            ``STORAGE_ENABLED`` is off; omitted when enabled.
    """

    base_dir: str
    public_url: str
    signing_secret: str
    default_bucket: str
    route_prefix: str
    max_upload_bytes: int
    encryption_keys: NotRequired[dict[str, bytes]]
    allow_plaintext_read: NotRequired[bool]
    enabled: NotRequired[bool]


class StorageKwargs(TypedDict):
    """Keyword arguments for :class:`~tempest_fastapi_sdk.AsyncMinIOClient`.

    Returned by :meth:`~tempest_fastapi_sdk.settings.StorageSettings.storage_kwargs`
    and its pre-rename alias
    :meth:`~tempest_fastapi_sdk.settings.StorageSettings.minio_kwargs`.

    Attributes:
        endpoint (str): S3-compatible ``host[:port]`` (``STORAGE_ENDPOINT``).
        access_key (str): S3 access key (``STORAGE_ACCESS_KEY``).
        secret_key (str): S3 secret key (``STORAGE_SECRET_KEY``).
        default_bucket (str): Default bucket name (``STORAGE_DEFAULT_BUCKET``).
        secure (bool): Use HTTPS for server-side requests (``STORAGE_SECURE``).
        region (str): S3 region (``STORAGE_REGION``).
        public_endpoint (str | None): Optional public endpoint for presigned
            URLs (``STORAGE_PUBLIC_ENDPOINT``).
        public_secure (bool | None): Optional HTTPS override for presigned
            URLs (``STORAGE_PUBLIC_SECURE``).
        accel_redirect (bool): Deliver objects via nginx ``X-Accel-Redirect``
            (``STORAGE_ACCEL_REDIRECT``).
        accel_prefix (str): Internal nginx location prefix
            (``STORAGE_ACCEL_PREFIX``).
        enabled (NotRequired[bool]): Present as ``False`` when
            ``STORAGE_ENABLED`` is off; omitted when enabled.
    """

    endpoint: str
    access_key: str
    secret_key: str
    default_bucket: str
    secure: bool
    region: str
    public_endpoint: str | None
    public_secure: bool | None
    accel_redirect: bool
    accel_prefix: str
    enabled: NotRequired[bool]


class AnthropicKwargs(TypedDict):
    """Keyword arguments for :class:`~tempest_fastapi_sdk.genai.AnthropicGenerator`.

    Returned by
    :meth:`~tempest_fastapi_sdk.settings.HostedGenAISettings.anthropic_kwargs`.

    Attributes:
        model (str): Anthropic model id (``ANTHROPIC_MODEL``).
        api_key (str): Anthropic API key (``ANTHROPIC_API_KEY``).
        base_url (str): Anthropic API root URL (``ANTHROPIC_BASE_URL``).
    """

    model: str
    api_key: str
    base_url: str


class OpenAIKwargs(TypedDict):
    """Keyword arguments for ``OpenAICompatGenerator`` on OpenAI.

    Returned by :meth:`~tempest_fastapi_sdk.settings.HostedGenAISettings.openai_kwargs`.

    Attributes:
        model (str): OpenAI model id (``OPENAI_MODEL``).
        api_key (str): OpenAI API key (``OPENAI_API_KEY``).
        base_url (str): OpenAI API root URL (``OPENAI_BASE_URL``).
        provider (str): Provider label (``"openai"``).
        max_tokens_field (str): Output budget field name
            (``"max_completion_tokens"``).
        structured_output (Literal["json_schema", "json_object"]): Structured
            output wire mode (``"json_schema"``).
    """

    model: str
    api_key: str
    base_url: str
    provider: str
    max_tokens_field: str
    structured_output: _StructuredOutputMode


class OpenAISpeechKwargs(TypedDict):
    """Keyword arguments for :class:`~tempest_fastapi_sdk.genai.OpenAICompatSpeech`.

    Returned by
    :meth:`~tempest_fastapi_sdk.settings.HostedGenAISettings.openai_speech_kwargs`.

    Attributes:
        model (str): OpenAI transcription model id (``OPENAI_STT_MODEL``).
        api_key (str): OpenAI API key (``OPENAI_API_KEY``).
        base_url (str): OpenAI API root URL (``OPENAI_BASE_URL``).
        provider (str): Provider label (``"openai"``).
    """

    model: str
    api_key: str
    base_url: str
    provider: str


class DeepSeekKwargs(TypedDict):
    """Keyword arguments for ``OpenAICompatGenerator`` on DeepSeek.

    Returned by
    :meth:`~tempest_fastapi_sdk.settings.HostedGenAISettings.deepseek_kwargs`.

    Attributes:
        model (str): DeepSeek model id (``DEEPSEEK_MODEL``).
        api_key (str): DeepSeek API key (``DEEPSEEK_API_KEY``).
        base_url (str): DeepSeek API root URL (``DEEPSEEK_BASE_URL``).
        provider (str): Provider label (``"deepseek"``).
        structured_output (Literal["json_schema", "json_object"]): Structured
            output wire mode (``"json_object"``).
        effort_field (str | None): Effort field name (``None`` for DeepSeek).
        accepts_documents (bool): Whether document parts are supported
            (``False``).
    """

    model: str
    api_key: str
    base_url: str
    provider: str
    structured_output: _StructuredOutputMode
    effort_field: str | None
    accepts_documents: bool


class GeminiKwargs(TypedDict):
    """Keyword arguments for ``OpenAICompatGenerator`` on Gemini.

    Returned by :meth:`~tempest_fastapi_sdk.settings.HostedGenAISettings.gemini_kwargs`.

    Attributes:
        model (str): Gemini model id (``GOOGLE_MODEL``).
        api_key (str): Gemini API key (``GOOGLE_API_KEY``).
        base_url (str): Gemini OpenAI-compatible API root URL
            (``GOOGLE_BASE_URL``).
        provider (str): Provider label (``"google"``).
        structured_output (Literal["json_schema", "json_object"]): Structured
            output wire mode (``"json_schema"``).
        effort_values (Mapping[str, str]): Mapping from SDK effort names to
            Gemini ``reasoning_effort`` values.
        accepts_documents (bool): Whether document parts are supported
            (``False``).
    """

    model: str
    api_key: str
    base_url: str
    provider: str
    structured_output: _StructuredOutputMode
    effort_values: Mapping[str, str]
    accepts_documents: bool


class HostedGenAIKwargs(TypedDict):
    """Keyword arguments for :func:`~tempest_fastapi_sdk.genai.hosted_generator`.

    Returned by :meth:`~tempest_fastapi_sdk.settings.HostedGenAISettings.hosted_kwargs`.

    Attributes:
        model (str): Hosted model id.
        api_key (str): Resolved API key for the provider.
        base_url (str): Resolved API root URL for the provider.
        provider (Literal["anthropic", "openai", "google", "deepseek"]):
            Resolved hosted provider key.
    """

    model: str
    api_key: str
    base_url: str
    provider: _HostedProvider


class HTTPClientKwargs(TypedDict):
    """Keyword arguments for :class:`~tempest_fastapi_sdk.HTTPClient`.

    Returned by :meth:`~tempest_fastapi_sdk.settings.OpenPixSettings.openpix_kwargs`,
    :meth:`~tempest_fastapi_sdk.settings.MercadoPagoSettings.mercado_pago_kwargs`,
    and :meth:`~tempest_fastapi_sdk.settings.RevenueCatSettings.revenuecat_kwargs`.

    Attributes:
        base_url (str): Provider API root URL.
        default_headers (dict[str, str]): Default HTTP headers carrying the
            provider's authentication format.
    """

    base_url: str
    default_headers: dict[str, str]


class HostBridgeKwargs(TypedDict):
    """Keyword arguments for :class:`~tempest_fastapi_sdk.hostbridge.HostBridgeConfig`.

    Returned by
    :meth:`~tempest_fastapi_sdk.settings.HostBridgeSettings.hostbridge_kwargs`.

    Attributes:
        allowed_base_paths (tuple[str, ...]): Directories the host file
            surface may access (``HOST_ALLOWED_BASE_PATHS``).
        powershell_binary (str): PowerShell executable name
            (``HOST_POWERSHELL_BINARY``).
        cmd_binary (str): ``cmd`` executable name (``HOST_CMD_BINARY``).
        default_command_timeout (int): Default command timeout in seconds
            (``HOST_COMMAND_TIMEOUT``).
        max_file_read_bytes (int): Ceiling for a single file read in bytes
            (``HOST_MAX_FILE_READ_BYTES``).
    """

    allowed_base_paths: tuple[str, ...]
    powershell_binary: str
    cmd_binary: str
    default_command_timeout: int
    max_file_read_bytes: int


__all__: list[str] = [
    "AnthropicKwargs",
    "DatabaseKwargs",
    "DeepSeekKwargs",
    "EmailKwargs",
    "FirebaseKwargs",
    "GeminiKwargs",
    "GitHubOAuthKwargs",
    "GoogleOAuthKwargs",
    "HTTPClientKwargs",
    "HostBridgeKwargs",
    "HostedGenAIKwargs",
    "JWTKwargs",
    "KafkaKwargs",
    "LocalStorageKwargs",
    "LoggingKwargs",
    "OpenAIKwargs",
    "OpenAISpeechKwargs",
    "RedisKwargs",
    "StorageKwargs",
    "UploadKwargs",
    "WebPushKwargs",
]
