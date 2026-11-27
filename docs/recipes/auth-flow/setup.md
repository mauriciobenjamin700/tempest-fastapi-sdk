# Setup mínimo e `UserTokenModel`

Use para ligar o fluxo: extras, os quatro objetos (`AsyncDatabaseManager`, `EmailUtils`, `UserAuthService`, `make_auth_router`) e o `UserTokenModel` concreto.

Parte da receita [Bundled auth flow (signup / activate / login / reset)](../auth-flow.md).

## Setup mínimo

Requer:

- `[auth]` (bcrypt + PyJWT) — obrigatório, sempre.
- `[email]` (aiosmtplib + Jinja2 + email-validator) — opcional; quando ausente, os links vão no body da resposta em vez de e-mail.

```bash
uv add "tempest-fastapi-sdk[auth,email]>=0.151.1"
```

São dois arquivos, e a ordem entre eles importa. Os objetos compartilhados
— conexão, e-mail, service — moram em `src/api/dependencies/`; o
`src/api/app.py` **importa** de lá e só monta a aplicação:

```python
# src/api/dependencies/resources.py

from tempest_fastapi_sdk import AsyncDatabaseManager, EmailUtils, UserAuthService

from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel

db = AsyncDatabaseManager(settings.DATABASE_URL)

# EmailUtils — só instancie se [email] estiver instalado E você quiser e-mail
# real (modos A e B abaixo). Nos modos C e D, passe email=None pro service.
emails = EmailUtils(
    host=settings.SMTP_HOST,
    port=settings.SMTP_PORT,
    username=settings.SMTP_USERNAME,
    password=settings.SMTP_PASSWORD,
    from_addr=settings.SMTP_FROM_ADDR,
    template_dir="emails",  # diretório onde seus templates custom moram
)

auth_service = UserAuthService(
    db=db,                    # necessário pra current_user_dependency (ver seção final)
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,   # mistura AuthSettings (ver seção 4)
    jwt_settings=settings,    # mistura JWTSettings
    email=emails,             # ou None — controla envio real vs link no body
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

!!! tip "O mesmo `get_session` do resto do app"
    `session_factory` aceita a dependency de sessão que o app já usa, inclusive
    com sub-dependências (`get_session(database=Depends(get_database))`): uma
    função geradora (async ou não) é registrada como `Depends(session_factory)`,
    então `app.dependency_overrides[get_session]` vale também para
    `/auth/login` e as outras rotas de auth. Outro callable sem argumentos que
    devolve um iterador async continua funcionando como antes. A mesma regra
    vale para todo router do SDK que recebe `session_factory`: push, Web Push,
    chat, reviews, wallet, sessões, voz e app-errors.

!!! warning "O service não pode morar no `app.py`"
    É tentador criar o `UserAuthService` ao lado do `app` e importá-lo de
    volta nas dependencies — e é um ciclo de import: `app.py` importa os
    routers, os routers importam `dependencies/`, e esse importaria `app.py`
    de novo. O erro sai longe da causa (`ImportError: cannot import name
    'auth_service' from partially initialized module`), então a direção fica
    fixa desde o começo: **dependencies não importa `app`**. É também o que
    deixa o service testável sem subir a aplicação.

!!! tip "TL;DR de quatro objetos"
    `AsyncDatabaseManager` → conexão. `EmailUtils` → SMTP + Jinja2. `UserAuthService` → regras de negócio. `make_auth_router` → cola tudo nos endpoints HTTP: 13 com os settings default, mais os de MFA, cookie, login social, WebAuthn e páginas backend quando ligados.

---

## UserTokenModel concreto

`BaseUserTokenModel` é abstrato — projeto cria a tabela concreta porque a FK pra `users` precisa do nome da sua tabela. Exemplo `src/db/models/user_token.py`:

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

E importar em `src/db/models/__init__.py` pra Alembic ver:

```python
from src.db.models.user import UserModel
from src.db.models.user_token import UserTokenModel

__all__: list[str] = ["UserModel", "UserTokenModel"]
```

!!! info "O comando `tempest` pede o extra `[cli]` (ou `[cli-ops]` no servidor)"
    Sem um dos dois, `tempest` sai com código 2 e imprime como instalar. Em
    desenvolvimento: `uv add --dev "tempest-fastapi-sdk[cli]"`. Detalhes em
    [Instalação → CLI](../../installation.md#cli).

Gerar migration (a primeira vez, bootstrape o Alembic com `tempest db init`):

```bash
# Só na primeira vez — gera alembic/, alembic.ini e env.py:
uv run tempest db init

# A partir daí, ciclo normal de revisão:
uv run tempest db revision -m "users + user_tokens"
uv run tempest db upgrade
```
