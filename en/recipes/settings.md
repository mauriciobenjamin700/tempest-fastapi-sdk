# Settings: composing the configuration mixins

Use this page to build the service's `Settings` class: which mixin brings each
environment variable, in which order to list the bases, and how to hand the
configuration to the SDK clients without copying field by field.

## Compose the `Settings` class

Use it when the service reads configuration from the environment (or from
`.env`): inherit the mixins you need and declare only what is yours.

```python
import asyncio
import logging

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseAppSettings,
    DatabaseSettings,
    EnvironmentSettings,
    JWTSettings,
    JWTUtils,
    LogSettings,
    ServerSettings,
    UploadSettings,
    UploadUtils,
    configure_logging,
)


class Settings(
    EnvironmentSettings,
    ServerSettings,
    LogSettings,
    DatabaseSettings,
    JWTSettings,
    UploadSettings,
    BaseAppSettings,
):
    """Service configuration: six mixins and one field of its own."""

    SIGNUP_BONUS_POINTS: int = 100


settings: Settings = Settings()

logger: logging.Logger = configure_logging(**settings.logging_kwargs())
db: AsyncDatabaseManager = AsyncDatabaseManager(**settings.database_kwargs())
jwt: JWTUtils = JWTUtils(**settings.jwt_kwargs())
uploads: UploadUtils = UploadUtils(**settings.upload_kwargs())


async def main() -> None:
    """Connect to the database, issue a token and show what the environment set."""
    await db.connect()
    token: str = jwt.encode({"sub": "42"})
    print(settings.ENV, settings.SERVER_PORT, settings.SIGNUP_BONUS_POINTS)
    print(jwt.decode(token)["sub"], sorted(settings.UPLOAD_ALLOWED_EXTENSIONS))
    await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
```

The matching `.env.example` — copy it to `.env` and adjust:

```bash
# EnvironmentSettings: required. development, test or production.
ENV=development

# ServerSettings
SERVER_HOST=127.0.0.1
SERVER_PORT=8000
SERVER_RELOAD=false

# LogSettings
LOG_LEVEL=INFO
LOG_JSON=false
LOG_DIR=logs
LOG_REDACT=true

# DatabaseSettings
DATABASE_URL=sqlite+aiosqlite:///./app.db

# JWTSettings: generate the secret with
# python -c "import secrets; print(secrets.token_urlsafe(48))"
JWT_SECRET=replace-with-a-secret-of-at-least-32-bytes
JWT_ACCESS_TTL_SECONDS=3600
# JWT_ISSUER=

# UploadSettings: lists are JSON.
UPLOAD_DIR=./var/uploads
UPLOAD_MAX_SIZE_BYTES=10485760
UPLOAD_ALLOWED_EXTENSIONS=[".jpg", ".png", ".pdf"]
UPLOAD_ALLOWED_MIMETYPES=["image/jpeg", "image/png", "application/pdf"]

# The service's own field.
SIGNUP_BONUS_POINTS=100
```

Save the code as `settings_demo.py` next to the `.env`. With
`uv add "tempest-fastapi-sdk[auth,upload,sqlite]"`, `python settings_demo.py`
prints:

```text
development 8000 100
42 ['.jpg', '.pdf', '.png']
```

- Each mixin brings its own fields, with defaults and validation, plus the
  `*_kwargs()` mapper when there is a client to build; the service class only
  adds them up.
- `SIGNUP_BONUS_POINTS` is the field no mixin models — the only one declared
  by hand.
- A key with no value stays commented out (`# JWT_ISSUER=`): an empty `KEY=`
  fails the boot on a numeric field (`int_parsing`) and becomes `""`, not
  `None`, on a `str | None` field.

!!! tip "Do not redeclare a mixin field to change its default"

    Binding `0.0.0.0`, a bigger upload, JSON logs: those are environment
    values and belong in `.env`. Redeclaring `SERVER_HOST: str = "0.0.0.0"` in
    the class hides the real configuration from whoever reads `.env` and pins
    dev and production to the same value.

## List `BaseAppSettings` last

Apply this rule whenever you list `BaseAppSettings` next to the mixins: it
goes at the end of the base list.

Every mixin already inherits `BaseAppSettings`, and Python does not let a
class come before its own subclass in the base list. In the wrong order the
class is never created — the error is raised at import:

```python
# docs-guard: skip — this is the error described below
from tempest_fastapi_sdk import BaseAppSettings, JWTSettings, ServerSettings


class Settings(BaseAppSettings, ServerSettings, JWTSettings):
    pass
```

```text
TypeError: Settings: BaseAppSettings must be the LAST base — ServerSettings
already subclasses it, so listing BaseAppSettings before it is an invalid
method resolution order (MRO). Move BaseAppSettings to the end of the base
list: class Settings(ServerSettings, BaseAppSettings).
```

Measured on 0.374.0 (Python 3.12.7, pydantic 2.13.5, pydantic-settings
2.15.0). With `BaseAppSettings` in the middle (`ServerSettings,
BaseAppSettings, JWTSettings`) the message is the same, naming `JWTSettings`.
Before v0.159.1 Python's raw `TypeError` came out (`Cannot create a consistent
method resolution order (MRO) for bases BaseAppSettings, ...`), with a
misleading `[metaclass]` error from mypy; the cause and the fix are the same.

Listing `BaseAppSettings` is optional: `class Settings(ServerSettings,
JWTSettings)` is created too and reads `.env`, because each mixin already
carries its `model_config`. Listing it last makes it explicit where the base
configuration comes from.

| What `BaseAppSettings` turns on | Effect |
| --- | --- |
| `env_file=".env"` | reads `.env` from the current directory; an environment variable wins over the file |
| `extra="ignore"` | an unknown variable is ignored (a typo too) |
| `case_sensitive=True` | the variable name is the field name, exactly |
| `frozen=True` | `settings` is immutable once built |
| `hide_input_in_errors=True` | a validation error does not echo the environment into the log |

## Build clients with `*_kwargs()`

Use the mixin's mapper to build the client: the field → argument translation
lives in one place and the `TypedDict` lets mypy check the splat.

```python
from tempest_fastapi_sdk import EmailSettings, EmailUtils


class Settings(EmailSettings):
    """E-mail only."""


settings: Settings = Settings()
mailer: EmailUtils = EmailUtils(**settings.email_kwargs())
```

`settings.email_kwargs()` returns `EmailKwargs`, with the STARTTLS ×
implicit TLS pair already resolved from `SMTP_USE_TLS`/`SMTP_USE_SSL`. The
`*_kwargs()` column of the table below names each mixin's mapper.

## Mixin table

Use it to find the mixin that models a variable and the extra its client
needs.

The 29 mixins of `tempest_fastapi_sdk.settings`, counted from `__all__` on
0.374.0. The mixin itself needs no extra — they all import with the base
install; the "Extra" column is what the **client** consuming the
configuration needs.

| Mixin | Extra | Main variables (total) | `*_kwargs()` → `TypedDict` |
| --- | --- | --- | --- |
| `AuthSettings` | `[auth]` (`[mfa]`, `[webauthn]` optional) | `AUTH_SIGNUP_ENABLED`, `AUTH_AUTO_ACTIVATE`, `AUTH_TOKEN_DELIVERY`, `AUTH_MFA_ENABLED` (73) | — |
| `CORSSettings` | — | `CORS_ORIGINS`, `CORS_ALLOW_CREDENTIALS`, `CORS_ALLOW_METHODS` (7) | — |
| `DatabaseSettings` | `[sqlite]` or `[postgres]` | `DATABASE_URL`, `DATABASE_ECHO`, `DATABASE_POOL_SIZE` (8) | `database_kwargs()` → `DatabaseKwargs` |
| `EmailSettings` | `[email]` | `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM_ADDR`, `SMTP_USE_TLS` (9) | `email_kwargs()` → `EmailKwargs` |
| `EnvironmentSettings` | — | `ENV` (1, required) | — |
| `FirebaseSettings` | `[firebase]` | `FIREBASE_PROJECT_ID`, `FIREBASE_CREDENTIALS_PATH` (4) | `firebase_kwargs()` → `FirebaseKwargs` |
| `GenAISettings` | `[genai]` | `GENAI_CACHE_DIR`, `GENAI_OFFLINE`, `GENAI_HF_TOKEN` (3) | — |
| `GoogleDriveSettings` | `[google-drive]` | `GOOGLE_DRIVE_FOLDER_ID`, `GOOGLE_DRIVE_CREDENTIALS_PATH` (4) | `google_drive_kwargs()` → `GoogleDriveStorageKwargs` |
| `HostBridgeSettings` | — | `HOST_ALLOWED_BASE_PATHS`, `HOST_COMMAND_TIMEOUT` (5) | `hostbridge_kwargs()` → `HostBridgeKwargs` |
| `HostedGenAISettings` | `[genai-hosted]` | `GENAI_PROVIDER`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY` (16) | `anthropic_kwargs()` → `AnthropicKwargs`, `openai_kwargs()` → `OpenAIKwargs`, `openai_speech_kwargs()` → `OpenAISpeechKwargs`, `deepseek_kwargs()` → `DeepSeekKwargs`, `gemini_kwargs()` → `GeminiKwargs`, `hosted_kwargs()` → `HostedGenAIKwargs` |
| `JWTSettings` | `[auth]` | `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_ACCESS_TTL_SECONDS` (5) | `jwt_kwargs()` → `JWTKwargs` |
| `KafkaSettings` | `[queue-kafka]` | `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_SECURITY_PROTOCOL` (10) | `kafka_kwargs()` → `KafkaKwargs` |
| `LogSettings` | — | `LOG_LEVEL`, `LOG_JSON`, `LOG_DIR`, `LOG_REDACT` (8) | `logging_kwargs()` → `LoggingKwargs` |
| `MercadoPagoSettings` | `[http]` | `MERCADOPAGO_ACCESS_TOKEN`, `MERCADOPAGO_WEBHOOK_SECRET` (3) | `mercado_pago_kwargs()` → `HTTPClientKwargs` |
| `MinIOSettings` | `[minio]` | former name of `StorageSettings`; composing it emits a `DeprecationWarning` (20) | — |
| `OAuthSettings` | `[http]` | `OAUTH_REDIRECT_BASE_URL`, `OAUTH_GOOGLE_CLIENT_ID`, `OAUTH_GITHUB_CLIENT_ID` (6) | `google_kwargs()` → `GoogleOAuthKwargs`, `github_kwargs()` → `GitHubOAuthKwargs` |
| `OpenPixSettings` | `[http]` | `OPENPIX_APP_ID`, `OPENPIX_ENVIRONMENT` (2) | `openpix_kwargs()` → `HTTPClientKwargs` |
| `PushSettings` | `[webpush]` + `[firebase]` | `WebPushSettings` + `FirebaseSettings` together (8) | — |
| `RabbitMQSettings` | `[queue]` | `RABBITMQ_URL`, `RABBITMQ_PREFETCH_COUNT` (2) | — |
| `RedisSettings` | `[cache]` | `REDIS_URL`, `REDIS_DECODE_RESPONSES` (2) | `redis_kwargs()` → `RedisKwargs` |
| `RevenueCatSettings` | `[http]` | `REVENUECAT_API_KEY`, `REVENUECAT_WEBHOOK_SECRET` (4) | `revenuecat_kwargs()` → `HTTPClientKwargs` |
| `ServerSettings` | — | `SERVER_HOST`, `SERVER_PORT`, `SERVER_RELOAD`, `TRUSTED_IP_HEADER` (7) | — |
| `SessionSettings` | — | `SESSION_TTL_SECONDS`, `SESSION_COOKIE_NAME`, `SESSION_COOKIE_SECURE` (9) | `session_cookie_kwargs()` → `SessionCookieKwargs`, `session_cookie_delete_kwargs()` → `SessionCookieDeleteKwargs` |
| `StorageSettings` | `[minio]` for `s3`; `local` needs none | `STORAGE_BACKEND`, `STORAGE_ENDPOINT`, `STORAGE_DEFAULT_BUCKET`, `STORAGE_LOCAL_DIR` (20) | `storage_kwargs()` → `StorageKwargs`, `minio_kwargs()` → `StorageKwargs`, `local_storage_kwargs()` → `LocalStorageKwargs` |
| `TaskIQSettings` | `[tasks]` or `[tasks-redis]` | `TASKIQ_BROKER_URL`, `TASKIQ_RESULT_BACKEND_URL` (7) | — |
| `TokenSettings` | — | `TOKEN_SECRET` (1) | — |
| `UploadSettings` | `[upload]` | `UPLOAD_DIR`, `UPLOAD_MAX_SIZE_BYTES`, `UPLOAD_ALLOWED_EXTENSIONS` (4) | `upload_kwargs()` → `UploadKwargs` |
| `WebPushSettings` | `[webpush]` | `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` (4) | `webpush_kwargs()` → `WebPushKwargs` |
| `WebSocketSettings` | `[websocket]` | `WS_HEARTBEAT_SECONDS`, `WS_MAX_CONNECTIONS_PER_USER` (4) | — |

They all import from `tempest_fastapi_sdk.settings` and from the root too
(`from tempest_fastapi_sdk import JWTSettings`). The field name is the
environment variable name, with no prefix. The full field list, with defaults
and descriptions, is in the [reference](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/reference/top.md).

## Refuse development values in production

Use `EnvironmentSettings` so the boot fails when `ENV=production` still carries
a development default.

With the example at the top, `ENV=production` and the placeholder
`JWT_SECRET`:

```text
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
  Value error, ENV=production refuses development values:
- DATABASE_URL: points at SQLite (the development default)
- JWT_SECRET: still the public placeholder declared by JWTSettings [type=value_error]
```

The guard only checks the mixins the class lists — each one declares what a
development value is for itself.

## Recap

- Inherit the mixins; declare by hand only the field no mixin models.
- `BaseAppSettings` is optional, but when it appears it goes **last** — in
  the wrong order, `TypeError` at import.
- Build clients with `Client(**settings.x_kwargs())`.
- Keep a `.env.example` with every key the service reads.
- Compose `EnvironmentSettings` so production refuses dev defaults.

Next steps: [Tutorial](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/tutorial.md#2-settings-server-app-factory-entry-point),
[Auth flow](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/auth-flow.md), [Uploads](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/uploads.md).
