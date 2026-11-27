# Settings: compor os mixins de configuração

Use esta página para montar a classe `Settings` do serviço: qual mixin traz
cada variável de ambiente, em que ordem listar as bases e como passar a
configuração para os clientes do SDK sem copiar campo por campo.

## Compor a classe `Settings`

Use quando o serviço lê configuração do ambiente (ou do `.env`): herde os
mixins de que precisa e declare só o que é seu.

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
    """Configuração do serviço: seis mixins e um campo próprio."""

    SIGNUP_BONUS_POINTS: int = 100


settings: Settings = Settings()

logger: logging.Logger = configure_logging(**settings.logging_kwargs())
db: AsyncDatabaseManager = AsyncDatabaseManager(**settings.database_kwargs())
jwt: JWTUtils = JWTUtils(**settings.jwt_kwargs())
uploads: UploadUtils = UploadUtils(**settings.upload_kwargs())


async def main() -> None:
    """Conecta no banco, emite um token e mostra o que veio do ambiente."""
    await db.connect()
    token: str = jwt.encode({"sub": "42"})
    print(settings.ENV, settings.SERVER_PORT, settings.SIGNUP_BONUS_POINTS)
    print(jwt.decode(token)["sub"], sorted(settings.UPLOAD_ALLOWED_EXTENSIONS))
    await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
```

O `.env.example` correspondente — copie para `.env` e ajuste:

```bash
# EnvironmentSettings: obrigatório. development, test ou production.
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

# JWTSettings: gere o segredo com
# python -c "import secrets; print(secrets.token_urlsafe(48))"
JWT_SECRET=troque-por-um-segredo-de-pelo-menos-32-bytes
JWT_ACCESS_TTL_SECONDS=3600
# JWT_ISSUER=

# UploadSettings: lista é JSON.
UPLOAD_DIR=./var/uploads
UPLOAD_MAX_SIZE_BYTES=10485760
UPLOAD_ALLOWED_EXTENSIONS=[".jpg", ".png", ".pdf"]
UPLOAD_ALLOWED_MIMETYPES=["image/jpeg", "image/png", "application/pdf"]

# Campo próprio do serviço.
SIGNUP_BONUS_POINTS=100
```

Salve o código como `settings_demo.py` ao lado do `.env`. Com
`uv add "tempest-fastapi-sdk[auth,upload,sqlite]"`, `python settings_demo.py`
imprime:

```text
development 8000 100
42 ['.jpg', '.pdf', '.png']
```

- Cada mixin traz os próprios campos, com default e validação, e o mapper
  `*_kwargs()` quando existe cliente para montar; a classe do serviço só soma.
- `SIGNUP_BONUS_POINTS` é o campo que nenhum mixin modela — é o único
  declarado à mão.
- Chave sem valor fica comentada (`# JWT_ISSUER=`): `KEY=` vazio derruba o
  boot em campo numérico (`int_parsing`) e vira `""`, não `None`, em campo
  `str | None`.

!!! tip "Não redeclare campo de mixin para trocar o default"

    Bind `0.0.0.0`, upload maior, log em JSON: isso é valor de ambiente e vai
    no `.env`. Redeclarar `SERVER_HOST: str = "0.0.0.0"` na classe esconde a
    configuração real de quem lê o `.env` e prende dev e produção ao mesmo
    valor.

## Listar `BaseAppSettings` por último

Use esta regra sempre que listar `BaseAppSettings` junto dos mixins: ele vai
no fim da lista de bases.

Todo mixin já herda `BaseAppSettings`, e Python não deixa uma classe vir antes
da própria subclasse na lista de bases. Na ordem errada a classe nem chega a
ser criada — o erro sai no import:

```python
# docs-guard: skip — este é o erro descrito abaixo
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

Medido na 0.374.0 (Python 3.12.7, pydantic 2.13.5, pydantic-settings 2.15.0).
Com `BaseAppSettings` no meio (`ServerSettings, BaseAppSettings, JWTSettings`)
a mensagem é a mesma, nomeando `JWTSettings`. Antes da v0.159.1 saía o
`TypeError` cru do Python (`Cannot create a consistent method resolution order
(MRO) for bases BaseAppSettings, ...`), com um `[metaclass]` enganoso no mypy;
a causa e a correção são as mesmas.

Listar `BaseAppSettings` é opcional: `class Settings(ServerSettings,
JWTSettings)` também é criada e lê o `.env`, porque cada mixin já carrega o
`model_config` dele. Listá-lo por último deixa explícito de onde vem a
configuração base.

| O que `BaseAppSettings` liga | Efeito |
| --- | --- |
| `env_file=".env"` | lê o `.env` do diretório atual; variável de ambiente ganha do arquivo |
| `extra="ignore"` | variável desconhecida é ignorada (typo também) |
| `case_sensitive=True` | o nome da variável é o nome do campo, exato |
| `frozen=True` | `settings` é imutável depois de criado |
| `hide_input_in_errors=True` | erro de validação não ecoa o ambiente no log |

## Montar clientes com `*_kwargs()`

Use o mapper do mixin para construir o cliente: a tradução campo → argumento
fica num lugar só e o `TypedDict` deixa o mypy conferir o splat.

```python
from tempest_fastapi_sdk import EmailSettings, EmailUtils


class Settings(EmailSettings):
    """Só e-mail."""


settings: Settings = Settings()
mailer: EmailUtils = EmailUtils(**settings.email_kwargs())
```

`settings.email_kwargs()` devolve `EmailKwargs`, já com o par STARTTLS ×
TLS implícito resolvido a partir de `SMTP_USE_TLS`/`SMTP_USE_SSL`. A coluna
`*_kwargs()` da tabela abaixo diz o mapper de cada mixin.

## Tabela de mixins

Use para achar o mixin que modela uma variável e o extra que o cliente
correspondente pede.

Os 29 mixins de `tempest_fastapi_sdk.settings`, contados do `__all__` na
0.374.0. O mixin em si não exige extra — todos importam com a instalação base;
a coluna "Extra" é o que o **cliente** que consome a configuração pede.

| Mixin | Extra | Variáveis principais (total) | `*_kwargs()` → `TypedDict` |
| --- | --- | --- | --- |
| `AuthSettings` | `[auth]` (`[mfa]`, `[webauthn]` opcionais) | `AUTH_SIGNUP_ENABLED`, `AUTH_AUTO_ACTIVATE`, `AUTH_TOKEN_DELIVERY`, `AUTH_MFA_ENABLED` (73) | — |
| `CORSSettings` | — | `CORS_ORIGINS`, `CORS_ALLOW_CREDENTIALS`, `CORS_ALLOW_METHODS` (7) | — |
| `DatabaseSettings` | `[sqlite]` ou `[postgres]` | `DATABASE_URL`, `DATABASE_ECHO`, `DATABASE_POOL_SIZE` (8) | `database_kwargs()` → `DatabaseKwargs` |
| `EmailSettings` | `[email]` | `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM_ADDR`, `SMTP_USE_TLS` (9) | `email_kwargs()` → `EmailKwargs` |
| `EnvironmentSettings` | — | `ENV` (1, obrigatório) | — |
| `FirebaseSettings` | `[firebase]` | `FIREBASE_PROJECT_ID`, `FIREBASE_CREDENTIALS_PATH` (4) | `firebase_kwargs()` → `FirebaseKwargs` |
| `GenAISettings` | `[genai]` | `GENAI_CACHE_DIR`, `GENAI_OFFLINE`, `GENAI_HF_TOKEN` (3) | — |
| `GoogleDriveSettings` | `[google-drive]` | `GOOGLE_DRIVE_FOLDER_ID`, `GOOGLE_DRIVE_CREDENTIALS_PATH` (4) | `google_drive_kwargs()` → `GoogleDriveStorageKwargs` |
| `HostBridgeSettings` | — | `HOST_ALLOWED_BASE_PATHS`, `HOST_COMMAND_TIMEOUT` (5) | `hostbridge_kwargs()` → `HostBridgeKwargs` |
| `HostedGenAISettings` | `[genai-hosted]` | `GENAI_PROVIDER`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY` (16) | `anthropic_kwargs()` → `AnthropicKwargs`, `openai_kwargs()` → `OpenAIKwargs`, `openai_speech_kwargs()` → `OpenAISpeechKwargs`, `deepseek_kwargs()` → `DeepSeekKwargs`, `gemini_kwargs()` → `GeminiKwargs`, `hosted_kwargs()` → `HostedGenAIKwargs` |
| `JWTSettings` | `[auth]` | `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_ACCESS_TTL_SECONDS` (5) | `jwt_kwargs()` → `JWTKwargs` |
| `KafkaSettings` | `[queue-kafka]` | `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_SECURITY_PROTOCOL` (10) | `kafka_kwargs()` → `KafkaKwargs` |
| `LogSettings` | — | `LOG_LEVEL`, `LOG_JSON`, `LOG_DIR`, `LOG_REDACT` (8) | `logging_kwargs()` → `LoggingKwargs` |
| `MercadoPagoSettings` | `[http]` | `MERCADOPAGO_ACCESS_TOKEN`, `MERCADOPAGO_WEBHOOK_SECRET` (3) | `mercado_pago_kwargs()` → `HTTPClientKwargs` |
| `MinIOSettings` | `[minio]` | nome antigo de `StorageSettings`; compor emite `DeprecationWarning` (20) | — |
| `OAuthSettings` | `[http]` | `OAUTH_REDIRECT_BASE_URL`, `OAUTH_GOOGLE_CLIENT_ID`, `OAUTH_GITHUB_CLIENT_ID` (6) | `google_kwargs()` → `GoogleOAuthKwargs`, `github_kwargs()` → `GitHubOAuthKwargs` |
| `OpenPixSettings` | `[http]` | `OPENPIX_APP_ID`, `OPENPIX_ENVIRONMENT` (2) | `openpix_kwargs()` → `HTTPClientKwargs` |
| `PushSettings` | `[webpush]` + `[firebase]` | `WebPushSettings` + `FirebaseSettings` juntos (8) | — |
| `RabbitMQSettings` | `[queue]` | `RABBITMQ_URL`, `RABBITMQ_PREFETCH_COUNT` (2) | — |
| `RedisSettings` | `[cache]` | `REDIS_URL`, `REDIS_DECODE_RESPONSES` (2) | `redis_kwargs()` → `RedisKwargs` |
| `RevenueCatSettings` | `[http]` | `REVENUECAT_API_KEY`, `REVENUECAT_WEBHOOK_SECRET` (4) | `revenuecat_kwargs()` → `HTTPClientKwargs` |
| `ServerSettings` | — | `SERVER_HOST`, `SERVER_PORT`, `SERVER_RELOAD`, `TRUSTED_IP_HEADER` (7) | — |
| `SessionSettings` | — | `SESSION_TTL_SECONDS`, `SESSION_COOKIE_NAME`, `SESSION_COOKIE_SECURE` (9) | `session_cookie_kwargs()` → `SessionCookieKwargs`, `session_cookie_delete_kwargs()` → `SessionCookieDeleteKwargs` |
| `StorageSettings` | `[minio]` para `s3`; `local` sem extra | `STORAGE_BACKEND`, `STORAGE_ENDPOINT`, `STORAGE_DEFAULT_BUCKET`, `STORAGE_LOCAL_DIR` (20) | `storage_kwargs()` → `StorageKwargs`, `minio_kwargs()` → `StorageKwargs`, `local_storage_kwargs()` → `LocalStorageKwargs` |
| `TaskIQSettings` | `[tasks]` ou `[tasks-redis]` | `TASKIQ_BROKER_URL`, `TASKIQ_RESULT_BACKEND_URL` (7) | — |
| `TokenSettings` | — | `TOKEN_SECRET` (1) | — |
| `UploadSettings` | `[upload]` | `UPLOAD_DIR`, `UPLOAD_MAX_SIZE_BYTES`, `UPLOAD_ALLOWED_EXTENSIONS` (4) | `upload_kwargs()` → `UploadKwargs` |
| `WebPushSettings` | `[webpush]` | `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` (4) | `webpush_kwargs()` → `WebPushKwargs` |
| `WebSocketSettings` | `[websocket]` | `WS_HEARTBEAT_SECONDS`, `WS_MAX_CONNECTIONS_PER_USER` (4) | — |

Todos importam de `tempest_fastapi_sdk.settings` e também da raiz
(`from tempest_fastapi_sdk import JWTSettings`). O nome do campo é o
nome da variável de ambiente, sem prefixo. A lista completa de campos, com
default e descrição, está na [referência](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/top.md).

## Recusar valor de desenvolvimento em produção

Use `EnvironmentSettings` para o boot falhar quando `ENV=production` ainda
carrega um default de desenvolvimento.

Com o exemplo do topo, `ENV=production` e o `JWT_SECRET` placeholder:

```text
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
  Value error, ENV=production refuses development values:
- DATABASE_URL: points at SQLite (the development default)
- JWT_SECRET: still the public placeholder declared by JWTSettings [type=value_error]
```

O guard só checa os mixins que a classe lista — cada um declara o que é
valor de desenvolvimento para ele.

## Recap

- Herde os mixins; declare à mão só o campo que nenhum mixin modela.
- `BaseAppSettings` é opcional, mas se aparecer vai **por último** — na
  ordem errada, `TypeError` no import.
- Construa clientes com `Cliente(**settings.x_kwargs())`.
- Mantenha um `.env.example` com toda chave que o serviço lê.
- Componha `EnvironmentSettings` para produção recusar default de dev.

Próximos passos: [Tutorial](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/tutorial.md#2-settings-server-factory-do-app-entrypoint),
[Fluxo de auth](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/auth-flow.md), [Uploads](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/uploads.md).
