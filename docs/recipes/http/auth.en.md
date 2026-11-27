---
description: "Use to see signup, login and a protected route with `PasswordUtils` and `JWTUtils` in one example."
---

# End-to-end authentication

Use to see signup, login and a protected route with `PasswordUtils` and `JWTUtils` in one example.

Part of the [HTTP layer](../http.md) recipe.

End-to-end signup + login + protected route using `PasswordUtils` and `JWTUtils`. Requires the `[auth]` extra.

## Wire the utility singletons

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

Reuse the `UserService.create` defined in the tutorial — it already hashes the password.

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

## Protect a route — JWT dependency

Use `make_jwt_user_dependency` to wire the bearer scheme + JWT decode + user load in one call. The single seam is the `user_loader`, an async callable that maps the JWT subject claim to your domain `UserModel` — called as `user_loader(subject)` by default, or `user_loader(subject, session)` once you pass `session_dependency=` (recommended: the user then comes back attached to the request session and can be mutated without an `InvalidRequestError`).

The token is looked up in **header → cookie → query string**, first hit wins: `Authorization: Bearer` always, plus the `cookie_name=` cookie and the `query_param=` query parameter when you enable them (both `None` by default). `subject_claim=` changes which claim is read (default `"sub"`) and `error_message=` the 401 text.

!!! warning "Returning `None` from `user_loader` refuses the request (v0.252.0)"
    The loader is the natural place to refuse a user — deactivated account, a subject that no longer exists, a malformed id. With `soft=False` (the default), `None` becomes a **401**, the same answer an absent subject already got.

    Up to v0.251.0 that `None` reached the handler and the route answered **200 with a user the loader had rejected** — so deactivating an account had no effect at all until the access token expired. With `soft=True` the `None` still arrives: that is what the flag is for.

    Raising `UnauthorizedException` or `NotFoundException` from inside the loader still works, and is what you want when the message matters.

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

## Soft auth (optional user)

`get_current_user_or_none` above already uses `soft=True` — it returns `None` instead of raising on a missing or invalid token, so endpoints can work both authenticated and anonymous:

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

Under the hood `soft=True` calls `tokens.decode_or_none` (no exception on expired/invalid tokens) and skips the loader when the subject is missing.
