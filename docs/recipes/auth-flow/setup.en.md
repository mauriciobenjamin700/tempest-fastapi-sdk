# Minimum setup and `UserTokenModel`

Use to wire the flow: extras, the four objects (`AsyncDatabaseManager`, `EmailUtils`, `UserAuthService`, `make_auth_router`) and the concrete `UserTokenModel`.

Part of the [Bundled auth flow (signup / activate / login / reset)](../auth-flow.md) recipe.

## Minimum setup

Requires:

- `[auth]` (bcrypt + PyJWT) — always required.
- `[email]` (aiosmtplib + Jinja2 + email-validator) — optional; when missing, the link lands in the response body instead of an email.

```bash
uv add "tempest-fastapi-sdk[auth,email]>=0.151.1"
```

Two files, and the direction between them matters. The shared objects —
connection, email, service — live under `src/api/dependencies/`; the
`src/api/app.py` **imports** from there and only assembles the application:

```python
# src/api/dependencies/resources.py

from tempest_fastapi_sdk import AsyncDatabaseManager, EmailUtils, UserAuthService

from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel

db = AsyncDatabaseManager(settings.DATABASE_URL)

# EmailUtils — only instantiate when [email] is installed AND you want real
# email (modes A and B below). In modes C and D, pass email=None to the service.
emails = EmailUtils(
    host=settings.SMTP_HOST,
    port=settings.SMTP_PORT,
    username=settings.SMTP_USERNAME,
    password=settings.SMTP_PASSWORD,
    from_addr=settings.SMTP_FROM_ADDR,
    template_dir="emails",  # directory where your custom templates live
)

auth_service = UserAuthService(
    db=db,                    # required for current_user_dependency (see final section)
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,   # mixes AuthSettings (see section 4)
    jwt_settings=settings,    # mixes JWTSettings
    email=emails,             # or None — controls real send vs link in body
)
```

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import make_auth_router

from src.api.dependencies.resources import auth_service, db

app = FastAPI()

app.include_router(
    make_auth_router(
        auth_service,
        session_factory=db.session_dependency,
    ),
)
```

!!! tip "The same `get_session` the rest of the app uses"
    `session_factory` takes the session dependency the app already has,
    sub-dependencies included (`get_session(database=Depends(get_database))`):
    a generator function (async or not) is registered as
    `Depends(session_factory)`, so `app.dependency_overrides[get_session]`
    reaches `/auth/login` and the other auth routes too. Any other
    zero-argument callable returning an async iterator keeps working as
    before. The same rule holds for every SDK router that takes a
    `session_factory`: push, Web Push, chat, reviews, wallet, sessions,
    voice and app-errors.

!!! warning "The service cannot live in `app.py`"
    It is tempting to build the `UserAuthService` next to the `app` and
    import it back from the dependencies — and that is an import cycle:
    `app.py` imports the routers, the routers import `dependencies/`, and
    that would import `app.py` again. The error lands far from its cause
    (`ImportError: cannot import name 'auth_service' from partially
    initialized module`), so the direction is fixed from the start:
    **dependencies never import `app`**. It is also what keeps the service
    testable without starting the application.

!!! tip "Four-object TL;DR"
    `AsyncDatabaseManager` → connection. `EmailUtils` → SMTP + Jinja2. `UserAuthService` → business rules. `make_auth_router` → glues it all into the HTTP endpoints: 13 with default settings, plus the MFA, cookie, social-login, WebAuthn and backend-page ones when enabled.

---

## Concrete UserTokenModel

`BaseUserTokenModel` is abstract — your project owns the concrete table because the FK to `users` needs your table name. Example `src/db/models/user_token.py`:

```python
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from tempest_fastapi_sdk import BaseUserTokenModel


class UserTokenModel(BaseUserTokenModel):
    """Concrete token table for activation / reset / email-verification."""

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

!!! info "The `tempest` command needs the `[cli]` extra (or `[cli-ops]` on a server)"
    Without either, `tempest` exits with code 2 and prints how to install it.
    In development: `uv add --dev "tempest-fastapi-sdk[cli]"`. Details in
    [Installation → CLI](../../installation.md#cli).

Generate the migration (first time round, bootstrap Alembic with `tempest db init`):

```bash
# First time only — generates alembic/, alembic.ini and env.py:
uv run tempest db init

# Then the usual revision cycle:
uv run tempest db revision -m "users + user_tokens"
uv run tempest db upgrade
```
