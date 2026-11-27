# Só o backend: do cadastro à rota protegida

Use para testar cadastro → ativação → login → rota protegida sem frontend (curl, Swagger, app mobile) e o mesmo ciclo num teste.

Parte da receita [Bundled auth flow (signup / activate / login / reset)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/auth-flow.md).

Você ainda não tem frontend. Talvez esteja testando a API com `curl` ou pelo
Swagger, escrevendo um app mobile, ou quer que o link do e-mail ative a conta
direto no backend. O default do `AUTH_ACTIVATION_URL_TEMPLATE` aponta para
`http://localhost:3000/activate?token={token}` — uma página de frontend que,
no seu caso, não existe.

Esta seção roda o ciclo inteiro **só com a API**: cadastrar, pegar o token de
ativação, ativar, logar e chamar uma rota protegida. Cada saída abaixo saiu de
um `curl` contra o app desta página.

## Passo 1 — o app inteiro num arquivo

```bash
uv add "tempest-fastapi-sdk[auth,email,sqlite]" uvicorn
```

`[auth]` traz bcrypt e PyJWT, `[sqlite]` traz o driver `aiosqlite`, e
`[email]` traz o Jinja2 que as páginas HTML do [Modo E](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/auth-flow/modes.md#modo-e-backend-only-v0320)
renderizam. O `uvicorn` é o servidor. A classe `Settings` abaixo compõe
mixins — a tabela de todos eles está em [Settings](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/settings.md).

```python
# main.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    AuthSettings,
    AuthUserSchema,
    BaseUserModel,
    DatabaseSettings,
    JWTSettings,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
)


class Settings(DatabaseSettings, JWTSettings, AuthSettings):
    """Settings lidas do ambiente (e do `.env`)."""


class UserModel(BaseUserModel):
    """A tabela de contas."""

    __tablename__ = "users"


UserTokenModel = make_user_token_model(user_table="users")

settings: Settings = Settings()
db: AsyncDatabaseManager = AsyncDatabaseManager(settings.DATABASE_URL)
auth_service: UserAuthService = UserAuthService(
    db=db,
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
    email=None,
)
get_current_user = auth_service.current_user_dependency()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Abre o banco e cria as tabelas no startup.

    Args:
        app (FastAPI): A aplicação que está subindo.

    Yields:
        None: Devolve o controle ao FastAPI enquanto o app atende.
    """
    await db.connect()
    await db.create_tables()
    yield
    await db.disconnect()


app: FastAPI = FastAPI(lifespan=lifespan)
app.include_router(
    make_auth_router(auth_service, session_factory=db.session_dependency),
)


@app.get("/api/profile")
async def profile(current: UserModel = Depends(get_current_user)) -> AuthUserSchema:
    """Devolve a conta dona do bearer token.

    Args:
        current (UserModel): O usuário resolvido a partir do token.

    Returns:
        AuthUserSchema: A visão pública da conta.
    """
    return AuthUserSchema.model_validate(current)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

!!! note "`create_tables()` aqui é atalho de demonstração"
    O exemplo cabe num arquivo só, então cria as tabelas direto no
    lifespan. Num serviço, o lifespan chama `sync_schema_async()` sobre as
    migrations do Alembic: `create_tables()` não acrescenta coluna em
    tabela que já existe. Veja
    [Migrations »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/migrations.md#3-sincronize-o-schema-no-lifespan).

Três linhas fazem o trabalho desta seção:

- **`email=None`** — sem `EmailUtils`, o link de ativação volta **no corpo**
  do `signup` (campo `activation_url`). Não precisa de
  `AUTH_RETURN_TOKEN_IN_RESPONSE=true`: com `email=None` o link aparece
  mesmo assim, e é o único lugar onde ele aparece.
- **`db.create_tables()`** — cria `users` e `user_tokens` no SQLite
  (`./app.db`, o default do `DATABASE_URL`). Bom para dev; em produção o
  schema vem do Alembic, como em [UserTokenModel concreto](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/auth-flow/setup.md#usertokenmodel-concreto).
- **`current_user_dependency()`** — a dependency que protege `/api/profile`.
  Detalhe em [Pegando o `current_user`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/auth-flow/current-user.md).

## Passo 2 — o `.env` de dev sem frontend

```bash
# .env
AUTH_BACKEND_LINKS=true
AUTH_ACTIVATION_URL_TEMPLATE=http://127.0.0.1:8000/auth/activate/{token}
AUTH_PASSWORD_RESET_URL_TEMPLATE=http://127.0.0.1:8000/auth/password-reset/{token}
```

`AUTH_BACKEND_LINKS=true` liga o [Modo E](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/auth-flow/modes.md#modo-e-backend-only-v0320): o
backend passa a montar `GET /auth/activate/{token}`, uma página HTML que
ativa a conta quando alguém abre o link. O template aponta para essa rota,
então o `activation_url` que você recebe é um link que funciona sozinho.

!!! info "`127.0.0.1` só vale porque o link abre na sua máquina"
    Em dev, quem abre o link é você, no mesmo computador do servidor. Em
    produção o link abre no browser do **usuário**, e o host tem de ser o
    domínio público do backend — veja o aviso de prefixo no
    [Modo E](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/auth-flow/modes.md#modo-e-backend-only-v0320).

Suba o servidor:

```bash
uv run python main.py
```

## Passo 3 — cadastrar

```bash
curl -X POST 127.0.0.1:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email": "ana@example.com", "password": "senha-forte-123"}'
```

`201 Created`:

```json
{
  "user_id": "69ce003d-1384-43a6-86df-01e547a03f13",
  "activation_required": true,
  "activation_url": "http://127.0.0.1:8000/auth/activate/5wScVz_EX6rW-PGCYkaDYpv0dGjjm779J3-HbcVW6nswr_Lao7Q_ynzwicIrQzZe",
  "access_token": null,
  "refresh_token": null
}
```

O token é o último pedaço do `activation_url`. A conta existe, mas ainda
está inativa — logar agora responde `401` com
`{"detail": "invalid email or password"}`, a mesma frase de senha errada,
para não contar a ninguém que o e-mail tem conta.

## Passo 4 — ativar

Escolha **um** dos dois caminhos. O token é de uso único, então o segundo
uso responde erro.

!!! info "Uso único também sob concorrência"
    O resgate é um `UPDATE ... WHERE used_at IS NULL RETURNING` só, e o banco
    escolhe o vencedor. Oito trocas de senha simultâneas com o mesmo link,
    em sessões separadas, dão **um** sucesso e sete `InvalidTokenException`
    (`tests/auth/test_consume_token_race.py`, em SQLite e PostgreSQL); até a
    0.304.0 as oito passavam no PostgreSQL. Vale para ativação, troca de
    senha e troca de e-mail. `RETURNING` exige PostgreSQL ou SQLite 3.35+.

=== "Link (browser ou `curl`)"

    Abra o `activation_url` no browser, ou:

    ```bash
    curl -i http://127.0.0.1:8000/auth/activate/5wScVz_EX6rW-PGCYkaDYpv0dGjjm779J3-HbcVW6nswr_Lao7Q_ynzwicIrQzZe
    ```

    `200 OK` com `content-type: text/html` e a página **Conta ativada**. É
    o que o usuário do Modo E vê ao clicar no e-mail. Abrir o mesmo link de
    novo responde `400` com a página **Falha na ativação**.

    Esta página não entrega JWT (token em URL vaza para histórico e log), então
    o próximo passo é o login.

=== "JSON (Swagger, app mobile)"

    ```bash
    curl -X POST 127.0.0.1:8000/auth/activate/5wScVz_EX6rW-PGCYkaDYpv0dGjjm779J3-HbcVW6nswr_Lao7Q_ynzwicIrQzZe
    ```

    `200 OK` com o par JWT — a conta já sai ativada **e** logada:

    ```json
    {
      "user_id": "69ce003d-1384-43a6-86df-01e547a03f13",
      "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…",
      "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…"
    }
    ```

    Repetir responde `401` com `{"detail": "token already used"}`. Esta rota
    existe em todos os modos; a de `GET` só com `AUTH_BACKEND_LINKS=true` —
    sem ele, abrir o link no browser responde `405 Method Not Allowed`.

## Passo 5 — logar e chamar a rota protegida

```bash
curl -X POST 127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "ana@example.com", "password": "senha-forte-123"}'
```

`200 OK`:

```json
{
  "user_id": "69ce003d-1384-43a6-86df-01e547a03f13",
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…",
  "mfa_required": false,
  "mfa_token": null
}
```

Mande o `access_token` no header `Authorization`:

```bash
curl 127.0.0.1:8000/api/profile \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…"
```

`200 OK`:

```json
{
  "id": "69ce003d-1384-43a6-86df-01e547a03f13",
  "is_active": true,
  "created_at": "2026-09-27T22:16:42.190695Z",
  "updated_at": "2026-09-27T22:16:48.642307Z",
  "email": "ana@example.com",
  "is_admin": false,
  "last_login_at": "2026-09-27T22:16:48.641560Z",
  "email_verified_at": null
}
```

`email_verified_at` sai `null` porque, neste setup sem mailer, o link de
ativação voltou na resposta do cadastro: ativar por ele não prova que você lê a
caixa de entrada. Veja [O que conta como e-mail verificado](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/auth-flow/email-change.md#o-que-conta-como-e-mail-verificado).

Sem o header, a mesma rota responde `401` com
`{"detail": "Authorization token is missing or invalid"}`.

!!! tip "Pelo Swagger"
    Abra `http://127.0.0.1:8000/docs`. `POST /auth/signup` devolve o
    `activation_url`; cole o token em `POST /auth/activate/{token}`; copie o
    `access_token` da resposta, clique em **Authorize** (o esquema
    `HTTPBearer` que a dependency registra) e cole. A partir daí, o
    **Try it out** de `/api/profile` já vai autenticado.

## E o app mobile?

O app não tem página web para receber o link, mas pode receber um **deep
link**. O template é uma string `.format()` sem validação de esquema, então
aceita o do seu app:

```bash
# .env
AUTH_ACTIVATION_URL_TEMPLATE=meuapp://ativar?token={token}
```

O `signup` passa a devolver
`"activation_url": "meuapp://ativar?token=f8vX7Qa…"`. O app abre por esse
link, lê o `token` da query string e chama `POST /auth/activate/{token}` —
o caminho JSON do passo 4, que já devolve o par JWT. Aqui
`AUTH_BACKEND_LINKS` pode ficar desligado: quem trata o link é o app, não o
backend.

## O mesmo ciclo num teste, com o e-mail capturado

Em teste você quer o caminho de produção — link **no e-mail**, não no corpo.
O `FakeEmailUtils` é um `EmailUtils` que guarda cada mensagem em `outbox` em
vez de abrir conexão SMTP:

```python
# tests/test_auth_flow.py
import re
from collections.abc import AsyncIterator

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient, Response

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    AuthSettings,
    AuthUserSchema,
    BaseUserModel,
    JWTSettings,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
)
from tempest_fastapi_sdk.testing.fakes import FakeEmailUtils


class UserModel(BaseUserModel):
    """A tabela de contas."""

    __tablename__ = "users"


UserTokenModel = make_user_token_model(user_table="users")


@pytest.fixture
async def mailer() -> FakeEmailUtils:
    """Devolve um mailer cujas mensagens caem em `outbox`.

    Returns:
        FakeEmailUtils: O mailer fake.
    """
    return FakeEmailUtils()


@pytest.fixture
async def client(mailer: FakeEmailUtils) -> AsyncIterator[AsyncClient]:
    """Serve o router de auth sobre um SQLite em memória.

    Args:
        mailer (FakeEmailUtils): Onde o e-mail de ativação cai.

    Yields:
        AsyncClient: Um client ligado ao app, sem socket.
    """
    db: AsyncDatabaseManager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.create_tables()
    service: UserAuthService = UserAuthService(
        db=db,
        user_model=UserModel,
        token_model=UserTokenModel,
        auth_settings=AuthSettings(
            AUTH_BACKEND_LINKS=True,
            AUTH_ACTIVATION_URL_TEMPLATE="http://test/auth/activate/{token}",
        ),
        jwt_settings=JWTSettings(),
        email=mailer,
    )
    get_current_user = service.current_user_dependency()
    app: FastAPI = FastAPI()
    app.include_router(make_auth_router(service, session_factory=db.session_dependency))

    @app.get("/api/profile")
    async def profile(current: UserModel = Depends(get_current_user)) -> AuthUserSchema:
        """Devolve a conta dona do bearer token.

        Args:
            current (UserModel): O usuário resolvido a partir do token.

        Returns:
            AuthUserSchema: A visão pública da conta.
        """
        return AuthUserSchema.model_validate(current)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as opened:
        yield opened
    await db.disconnect()


async def test_signup_activate_login_profile(
    client: AsyncClient,
    mailer: FakeEmailUtils,
) -> None:
    """Leva a conta do cadastro até uma rota protegida.

    Args:
        client (AsyncClient): O client do app.
        mailer (FakeEmailUtils): A caixa de saída capturada.
    """
    credentials: dict[str, str] = {"email": "ana@example.com", "password": "senha-forte-123"}

    signup: Response = await client.post("/auth/signup", json=credentials)
    assert signup.status_code == 201
    assert signup.json()["activation_url"] is None

    match = re.search(r"http://test/auth/activate/\S+", mailer.outbox[0].body)
    assert match is not None
    link: str = match.group(0)

    page: Response = await client.get(link)
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]

    login: Response = await client.post("/auth/login", json=credentials)
    assert login.status_code == 200
    token: str = login.json()["access_token"]

    profile: Response = await client.get(
        "/api/profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert profile.status_code == 200
    assert profile.json()["is_active"] is True
```

Repare no `assert signup.json()["activation_url"] is None`: com um
`EmailUtils` configurado, o link **sai do corpo** e vai só para o e-mail —
é o que prova que o teste exercita o caminho de produção. As fixtures
`async` pedem `asyncio_mode = "auto"` no `[tool.pytest.ini_options]` do
`pyproject.toml` (com `pytest-asyncio` instalado).

!!! note "Instalou só `[auth]`?"
    O caminho JSON funciona com `[auth,sqlite]`. Com `AUTH_BACKEND_LINKS=true`
    e sem o Jinja2 do `[email]`, o `make_auth_router` recusa subir e diz o
    extra que falta — antes, o router subia, e o primeiro clique no link
    ativava a conta e respondia `500`.

**Recap.**

- Sem frontend, o link vai no corpo do `signup` enquanto `email=None`; com
  e-mail real, só no e-mail.
- `POST /auth/activate/{token}` ativa e já devolve o par JWT; `GET` no mesmo
  path é a página HTML do Modo E, que pede login depois.
- `AUTH_ACTIVATION_URL_TEMPLATE` aponta para quem vai abrir o link: o
  frontend, o backend (Modo E) ou o app (deep link).
- O `access_token` vai em `Authorization: Bearer …` em qualquer rota
  protegida por `current_user_dependency()`.
