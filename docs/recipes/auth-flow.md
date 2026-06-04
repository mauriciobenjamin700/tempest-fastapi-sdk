# Bundled auth flow (signup / activate / login / reset)

Desde v0.31.0 o SDK fornece o ciclo completo de conta local — signup com email/senha, ativação por link, login com JWT pair, reset de senha — via `UserAuthService` + `make_auth_router`. Cinco endpoints prontos pra mount, default Jinja2 templates bundled, settings flags pra controlar comportamento (auto-activate, return-token-no-body).

## Setup mínimo

Requer:

- `[auth]` (bcrypt + PyJWT)
- `[email]` (aiosmtplib + jinja2 + email-validator) — opcional; sem ele os links ficam no body do response

```python
from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    EmailUtils,
    UserAuthService,
    make_auth_router,
)
from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel

db = AsyncDatabaseManager(settings.DATABASE_URL)
emails = EmailUtils(
    host=settings.SMTP_HOST,
    port=settings.SMTP_PORT,
    from_addr=settings.SMTP_FROM_ADDR,
    template_dir="emails",  # opcional; SDK tem fallback bundled
)

auth_service = UserAuthService(
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
    email=emails,
)

app.include_router(
    make_auth_router(
        auth_service,
        session_factory=lambda: db.get_session(),
    ),
)
```

## UserTokenModel concreto

Mirror do `UserModel` — `BaseUserTokenModel` é abstrato, projeto cria o concreto. Ex `src/db/models/user_token.py`:

```python
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from tempest_fastapi_sdk import BaseUserTokenModel
from uuid import UUID


class UserTokenModel(BaseUserTokenModel):
    """Concrete token table for activation/reset/email-verification."""

    __tablename__ = "user_tokens"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
```

E importar em `src/db/models/__init__.py` pra Alembic ver:

```python
from src.db.models.user import UserModel
from src.db.models.user_token import UserTokenModel

__all__: list[str] = ["UserModel", "UserTokenModel"]
```

Gera migration:

```bash
uv run tempest db revision -m "users + user_tokens"
uv run tempest db upgrade
```

## Endpoints

| Método | Path | Body / Output | Comportamento |
|--------|------|---------------|---------------|
| POST | `/auth/signup` | `SignupSchema` → `SignupResponseSchema` | Cria user. Envia email (ou body) com link de ativação. Se `AUTH_AUTO_ACTIVATE=True`, user nasce ativo + JWT direto. |
| POST | `/auth/activate/{token}` | `ActivationResponseSchema` | Consome token + flip `is_active=True` + JWT pair. |
| POST | `/auth/login` | `LoginSchema` → `LoginResponseSchema` | Email + senha → JWT pair. Erros genéricos (não enumera). |
| POST | `/auth/password-reset/request` | `PasswordResetRequestSchema` → `PasswordResetResponseSchema` | Sempre 202. Link via email ou body. |
| POST | `/auth/password-reset/confirm` | `PasswordResetConfirmSchema` → `LoginResponseSchema` | Consome token + nova senha → JWT pair. |

## Settings (`AuthSettings`)

```bash
# .env
AUTH_AUTO_ACTIVATE=false                          # true = sem email, ativa direto
AUTH_RETURN_TOKEN_IN_RESPONSE=false               # true = link no body (dev / sem SMTP)
AUTH_ACTIVATION_TTL_SECONDS=604800                # 7 dias
AUTH_PASSWORD_RESET_TTL_SECONDS=3600              # 1 hora
AUTH_ACTIVATION_URL_TEMPLATE=https://app/activate?token={token}
AUTH_PASSWORD_RESET_URL_TEMPLATE=https://app/reset?token={token}
AUTH_ACTIVATION_TEMPLATE=activation.html          # nome do arquivo Jinja2
AUTH_PASSWORD_RESET_TEMPLATE=password_reset.html
AUTH_PASSWORD_MIN_LENGTH=12
```

## Modos de operação

### Modo 1: produção (email + activation obrigatório)

```bash
AUTH_AUTO_ACTIVATE=false
AUTH_RETURN_TOKEN_IN_RESPONSE=false
SMTP_HOST=smtp.mailgun.org
SMTP_FROM_ADDR=noreply@example.com
```

Fluxo: signup → email com link → user clica → ativa → login.

### Modo 2: dev sem SMTP (link no body)

```bash
AUTH_AUTO_ACTIVATE=false
AUTH_RETURN_TOKEN_IN_RESPONSE=true
```

Signup retorna `{activation_url: "https://app/activate?token=..."}` no body. Copie + cole.

### Modo 3: CI / testes (skip activation total)

```bash
AUTH_AUTO_ACTIVATE=true
```

Signup retorna `{access_token, refresh_token}` direto. Sem email, sem activation step.

## Templates customizados

SDK ship `activation.html` + `password_reset.html` bundled (style inline, mobile-friendly). Override colocando arquivo de mesmo nome no `template_dir` do `EmailUtils`:

```
emails/
├── activation.html          # override do SDK
└── password_reset.html      # override do SDK
```

Contexto disponível nos templates:

| Variável | Em |
|----------|-----|
| `user` | ambos (`user.email`, `user.name` quando o modelo tem coluna) |
| `activation_url` | `activation.html` |
| `reset_url` | `password_reset.html` |
| `expires_at` | ambos (datetime UTC) |

## Segurança

- Tokens armazenados como **hash SHA-256** (plaintext devolvido 1 vez na resposta/email).
- One-shot — `used_at` carimbado no consume; replay rejeitado.
- TTL-bounded — expirados rejeitados.
- Password-reset request sempre 202 + mensagem genérica — não enumera contas.
- Login com email errado vs senha errada produz `UnauthorizedException` idêntica.
- Password floor enforced 2x (schema + service).

## Próximos passos

- [Idempotência](idempotency.md) — proteja signup contra retentativas que duplicam linha.
- [Storage MinIO/S3](storage.md) — anexe avatar/foto de perfil no flow de signup.
- [Logging](logging.md) — request_id já propaga automaticamente.
