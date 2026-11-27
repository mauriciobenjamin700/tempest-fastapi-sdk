# Autenticação de ponta a ponta

Use para ver signup, login e rota protegida com `PasswordUtils` e `JWTUtils` num exemplo só.

Parte da receita [Camada HTTP](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/http.md).

Signup + login + rota protegida de ponta a ponta usando `PasswordUtils` e `JWTUtils`. Requer o extra `[auth]`.

## Conecte os singletons utilitários

```python
# src/core/security.py
from datetime import timedelta

from tempest_fastapi_sdk import JWTUtils, PasswordUtils

from src.core.settings import settings


passwords = PasswordUtils(rounds=12)

tokens = JWTUtils(
    secret=settings.JWT_SECRET,
    algorithm=settings.JWT_ALGORITHM,
    default_ttl=timedelta(seconds=settings.JWT_ACCESS_TTL_SECONDS),
    issuer="my-app",
)
```

## Signup

Reutilize o `UserService.create` definido no tutorial — ele já faz hash da senha.

## Login

```python
# src/schemas/auth.py
from pydantic import EmailStr

from tempest_fastapi_sdk import BaseSchema


class LoginSchema(BaseSchema):
    email: EmailStr
    password: str


class TokenResponseSchema(BaseSchema):
    access_token: str
    token_type: str = "bearer"
```

```python
# src/services/auth.py
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import JWTUtils, PasswordUtils, UnauthorizedException

from src.db.repositories import UserRepository
from src.schemas.auth import LoginSchema, TokenResponseSchema


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        passwords: PasswordUtils,
        tokens: JWTUtils,
    ) -> None:
        self.repo = UserRepository(session)
        self.passwords = passwords
        self.tokens = tokens

    async def login(self, data: LoginSchema) -> TokenResponseSchema:
        user = await self.repo.get_or_none({"email": data.email})
        if user is None or not self.passwords.verify(
            data.password, user.password_hash
        ):
            # Same error for both cases — don't leak which one failed.
            raise UnauthorizedException(message="E-mail ou senha inválidos")
        token = self.tokens.encode({"sub": str(user.id)})
        return TokenResponseSchema(access_token=token)
```

```python
# src/api/dependencies/services.py
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.app import db
from src.core.security import passwords, tokens
from src.services.auth import AuthService


def get_auth_service(
    session: AsyncSession = Depends(db.session_dependency),
) -> AuthService:
    return AuthService(session, passwords, tokens)
```

```python
# src/api/routers/auth.py
from fastapi import APIRouter, Depends

from src.api.dependencies.services import get_auth_service
from src.schemas.auth import LoginSchema, TokenResponseSchema
from src.services.auth import AuthService


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponseSchema)
async def login(
    data: LoginSchema,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponseSchema:
    return await service.login(data)
```

## Proteja uma rota — dependência JWT

Use `make_jwt_user_dependency` para conectar o esquema bearer + decode do JWT + carga do usuário em uma chamada. A única costura é o `user_loader`, um callable async que mapeia o claim de subject do JWT para o seu `UserModel` de domínio — chamado como `user_loader(subject)` por default, ou `user_loader(subject, session)` quando você passa `session_dependency=` (recomendado: assim o usuário volta *attached* na sessão do request e pode ser mutado sem `InvalidRequestError`).

O token é procurado em **header → cookie → query string**, primeiro hit ganha: `Authorization: Bearer` sempre, mais o cookie de `cookie_name=` e o parâmetro de `query_param=` quando você os habilita (ambos `None` por default). `subject_claim=` troca o claim lido (default `"sub"`) e `error_message=` o texto do 401.

!!! warning "Devolver `None` do `user_loader` recusa a request (v0.252.0)"
    O loader é o lugar natural para recusar um usuário — conta desativada, subject que não existe mais, id malformado. Com `soft=False` (default), `None` vira **401**, a mesma resposta que o subject ausente já dava.

    Até a v0.251.0 esse `None` chegava ao handler e a rota respondia **200 com um usuário que o loader tinha recusado** — então desativar uma conta não tinha efeito nenhum até o access token expirar. Com `soft=True` o `None` continua chegando: é o propósito do flag.

    Levantar `UnauthorizedException` ou `NotFoundException` de dentro do loader continua funcionando, e é o que você quer quando a mensagem importa.

```python
# src/api/dependencies/auth.py
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from tempest_fastapi_sdk import make_jwt_user_dependency

from src.api.app import db
from src.core.security import tokens
from src.db.models import UserModel
from src.db.repositories import UserRepository


async def load_user(subject: str, session: AsyncSession) -> UserModel:
    """Resolve the JWT subject (a UUID string) to a persisted user.

    Receives the request-scoped session because the dependency below
    declares `session_dependency=`, so the user comes back attached to
    the same session the route's repositories use. SDK exceptions raised
    inside translate to the canonical 401/404 envelope.
    """
    repo = UserRepository(session)
    return await repo.get_by_id(UUID(subject))


get_current_user = make_jwt_user_dependency(
    tokens,
    load_user,
    session_dependency=db.session_dependency,
)
get_current_user_or_none = make_jwt_user_dependency(
    tokens,
    load_user,
    soft=True,
    session_dependency=db.session_dependency,
)
```

```python
# Use in any route

from fastapi import APIRouter, Depends

from src.api.dependencies.auth import get_current_user
from src.db.models import UserModel
from src.schemas import UserResponseSchema

router = APIRouter()


@router.get("/me", response_model=UserResponseSchema)
async def me(current: UserModel = Depends(get_current_user)) -> UserResponseSchema:
    return UserResponseSchema.model_validate(current)
```

## Auth suave (usuário opcional)

`get_current_user_or_none` acima já usa `soft=True` — ele retorna `None` em vez de levantar em um token ausente ou inválido, para que endpoints funcionem tanto autenticados quanto anônimos:

```python
from fastapi import APIRouter, Depends

from src.api.dependencies.auth import get_current_user_or_none
from src.db.models import UserModel
from src.schemas import FeedResponseSchema
from src.services import FeedService

feed_service = FeedService()
router = APIRouter()


@router.get("/feed")
async def feed(
    current: UserModel | None = Depends(get_current_user_or_none),
) -> FeedResponseSchema:
    return await feed_service.list(viewer=current)
```

Por baixo dos panos, `soft=True` chama `tokens.decode_or_none` (sem exceção em tokens expirados/inválidos) e pula o loader quando o subject está ausente.
