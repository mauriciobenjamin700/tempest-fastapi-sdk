# Bundled auth flow (signup / activate / login / reset)

Since v0.31.0 the SDK ships the full local-account lifecycle — email + password signup, link-based activation, JWT-pair login, password reset — via `UserAuthService` + `make_auth_router`. Five endpoints ready to mount, default Jinja2 templates bundled, settings flags control behavior (auto-activate, return-token-in-body).

## Minimum setup

Requires:

- `[auth]` (bcrypt + PyJWT)
- `[email]` (aiosmtplib + jinja2 + email-validator) — optional; without it the links land in the response body

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
    template_dir="emails",  # optional; SDK has a bundled fallback
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

## Concrete UserTokenModel

Mirrors `UserModel` — `BaseUserTokenModel` is abstract, the project ships the concrete table. Example `src/db/models/user_token.py`:

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

Re-export from `src/db/models/__init__.py` so Alembic picks it up:

```python
from src.db.models.user import UserModel
from src.db.models.user_token import UserTokenModel

__all__: list[str] = ["UserModel", "UserTokenModel"]
```

Generate the migration:

```bash
uv run tempest db revision -m "users + user_tokens"
uv run tempest db upgrade
```

## Endpoints

| Method | Path | Body / Output | Behavior |
|--------|------|---------------|----------|
| POST | `/auth/signup` | `SignupSchema` → `SignupResponseSchema` | Creates user. Emails (or response-body) the activation link. With `AUTH_AUTO_ACTIVATE=True`, user is born active + JWT pair returned. |
| POST | `/auth/activate/{token}` | `ActivationResponseSchema` | Consumes token + flips `is_active=True` + issues JWT pair. |
| POST | `/auth/login` | `LoginSchema` → `LoginResponseSchema` | Email + password → JWT pair. Generic errors (no enumeration). |
| POST | `/auth/password-reset/request` | `PasswordResetRequestSchema` → `PasswordResetResponseSchema` | Always 202. Link via email or response body. |
| POST | `/auth/password-reset/confirm` | `PasswordResetConfirmSchema` → `LoginResponseSchema` | Consumes token + new password → JWT pair. |

## Settings (`AuthSettings`)

```bash
# .env
AUTH_AUTO_ACTIVATE=false                          # true = no email, instant activation
AUTH_RETURN_TOKEN_IN_RESPONSE=false               # true = link in body (dev / no SMTP)
AUTH_ACTIVATION_TTL_SECONDS=604800                # 7 days
AUTH_PASSWORD_RESET_TTL_SECONDS=3600              # 1 hour
AUTH_ACTIVATION_URL_TEMPLATE=https://app/activate?token={token}
AUTH_PASSWORD_RESET_URL_TEMPLATE=https://app/reset?token={token}
AUTH_ACTIVATION_TEMPLATE=activation.html          # Jinja2 filename
AUTH_PASSWORD_RESET_TEMPLATE=password_reset.html
AUTH_PASSWORD_MIN_LENGTH=12
```

## Operating modes

### Mode 1: production (email + required activation)

```bash
AUTH_AUTO_ACTIVATE=false
AUTH_RETURN_TOKEN_IN_RESPONSE=false
SMTP_HOST=smtp.mailgun.org
SMTP_FROM_ADDR=noreply@example.com
```

Flow: signup → email with link → user clicks → activated → login.

### Mode 2: dev without SMTP (link in body)

```bash
AUTH_AUTO_ACTIVATE=false
AUTH_RETURN_TOKEN_IN_RESPONSE=true
```

Signup returns `{activation_url: "https://app/activate?token=..."}` in the body. Copy + paste.

### Mode 3: CI / tests (skip activation entirely)

```bash
AUTH_AUTO_ACTIVATE=true
```

Signup returns `{access_token, refresh_token}` immediately. No email, no activation step.

## Custom templates

SDK ships `activation.html` + `password_reset.html` bundled (inline styles, mobile-friendly). Override by dropping a same-named file in `EmailUtils.template_dir`:

```
emails/
├── activation.html          # overrides the SDK default
└── password_reset.html      # overrides the SDK default
```

Variables available in templates:

| Variable | In |
|----------|-----|
| `user` | both (`user.email`, `user.name` when the model has the column) |
| `activation_url` | `activation.html` |
| `reset_url` | `password_reset.html` |
| `expires_at` | both (UTC datetime) |

## Security

- Tokens stored as **SHA-256 hash** (plaintext returned once via response/email).
- One-shot — `used_at` stamped on consume; replay rejected.
- TTL-bounded — expired rejected.
- Password-reset request always 202 + generic message — no account enumeration.
- Login with wrong email vs wrong password produces identical `UnauthorizedException`.
- Password floor enforced twice (schema + service).

## Next steps

- [Idempotency](idempotency.en.md) — protect signup against retries that duplicate rows.
- [MinIO/S3 Storage](storage.en.md) — attach avatar/profile picture during signup.
- [Logging](logging.en.md) — `request_id` propagates automatically.
