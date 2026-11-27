# Serviço completo: settings, migrations, auth, upload e teste

Use esta página quando você quer **um serviço inteiro que roda**, com as peças
que quase todo backend precisa ligadas entre si: settings compostas pelos
mixins do SDK, schema migrado no startup, cadastro e login, upload só para
usuário logado e um teste ponta a ponta. Cada peça tem receita própria; aqui
ela aparece no lugar onde encaixa.

| Arquivo | O que faz | Receita da peça |
| --- | --- | --- |
| `src/core/settings.py` | compõe os mixins de settings | [Settings](settings.md) |
| `src/db/models.py` | tabela de usuários e de tokens | [Auth flow](auth-flow.md#usertokenmodel-concreto) |
| `src/db/migrations.py` | `sync_schema_async()` no lifespan | [Migrations](migrations.md#3-sincronize-o-schema-no-lifespan) |
| `src/api/dependencies/resources.py` | banco, `UserAuthService`, `UploadUtils` | [Uploads](uploads.md#referencia-rapida) |
| `src/api/routers/files.py` | upload protegido por `current_user_dependency()` | [Uploads — rota protegida](uploads.md#upload-so-para-usuario-logado) |
| `src/api/app.py` | lifespan + routers de health, auth e upload | [Testes](testing.md) |
| `tests/` | signup → login → `/auth/me` → upload | [Testes](testing.md) |

Tudo nesta página rodou com `tempest-fastapi-sdk` 0.374.0 do PyPI, Python
3.12, em 2026-11-27.

## Instalar as dependências

Crie o projeto e instale só os extras que ele usa.

```bash
uv init --bare --name servico servico
cd servico
uv add "tempest-fastapi-sdk[auth,cli-ops,sqlite,upload]" uvicorn
uv add --dev pytest pytest-asyncio ruff
```

| Extra | Para quê |
| --- | --- |
| `[auth]` | bcrypt + PyJWT do `UserAuthService` |
| `[cli-ops]` | o comando `tempest db` (sem o gate de qualidade) |
| `[sqlite]` | driver `aiosqlite` |
| `[upload]` | `aiofiles` + `python-multipart` do `UploadUtils` |

O `ruff` no grupo dev é o que os `post_write_hooks` do `alembic.ini` gerado
chamam para formatar cada revision. Acrescente ao `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
pythonpath = ["."]
```

## Ver a árvore do projeto

Estes são todos os arquivos; `alembic/` e `alembic.ini` saem do `tempest db init`.

```text
servico/
├── .env
├── main.py
├── pyproject.toml
├── alembic.ini
├── alembic/
├── src/
│   ├── __init__.py
│   ├── server.py
│   ├── core/
│   │   ├── __init__.py
│   │   └── settings.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── migrations.py
│   │   └── models.py
│   └── api/
│       ├── __init__.py
│       ├── app.py
│       ├── dependencies/
│       │   ├── __init__.py
│       │   └── resources.py
│       └── routers/
│           ├── __init__.py
│           └── files.py
└── tests/
    ├── __init__.py
    ├── conftest.py
    └── test_service.py
```

`src/__init__.py`, `src/api/__init__.py` e `tests/__init__.py` ficam vazios.

## Compor as settings

Herde os mixins em vez de declarar `DATABASE_URL`, `JWT_SECRET` ou `UPLOAD_DIR`
à mão: cada mixin traz o campo, o default e o `*_kwargs()` que monta o cliente.

```python
# src/core/settings.py
from tempest_fastapi_sdk import (
    AuthSettings,
    DatabaseSettings,
    EnvironmentSettings,
    JWTSettings,
    ServerSettings,
    UploadSettings,
)


class Settings(
    EnvironmentSettings,
    ServerSettings,
    DatabaseSettings,
    JWTSettings,
    AuthSettings,
    UploadSettings,
):
    """Configuração do serviço, composta pelos mixins do SDK."""


settings: Settings = Settings()
```

```python
# src/core/__init__.py
from src.core.settings import Settings as Settings
from src.core.settings import settings as settings

__all__: list[str] = ["Settings", "settings"]
```

```bash
# .env
ENV=development
JWT_SECRET=troque-por-um-segredo-de-32-caracteres-ou-mais
AUTH_AUTO_ACTIVATE=true
UPLOAD_DIR=./var/uploads
UPLOAD_MAX_SIZE_BYTES=5242880
UPLOAD_ALLOWED_EXTENSIONS=[".png", ".jpg", ".pdf"]
```

| Mixin | Variáveis que esta página usa |
| --- | --- |
| `EnvironmentSettings` | `ENV` (obrigatória; `production` recusa subir com valor de dev, `test` descarta o `.env`) |
| `ServerSettings` | `SERVER_HOST` (default `127.0.0.1`), `SERVER_PORT` (default `8000`) |
| `DatabaseSettings` | `DATABASE_URL` (default `sqlite+aiosqlite:///./app.db`) |
| `JWTSettings` | `JWT_SECRET` |
| `AuthSettings` | `AUTH_AUTO_ACTIVATE` (`true` pula o e-mail de ativação) |
| `UploadSettings` | `UPLOAD_DIR`, `UPLOAD_MAX_SIZE_BYTES`, `UPLOAD_ALLOWED_EXTENSIONS` |

!!! tip "Sem e-mail em dev"
    `AUTH_AUTO_ACTIVATE=true` deixa a conta ativa no `signup`, então o login
    funciona sem SMTP. Para o fluxo com e-mail de ativação, veja
    [Auth flow](auth-flow.md#so-o-backend-do-cadastro-a-rota-protegida).

## Declarar os modelos de usuário

Use quando você quer o cadastro do SDK: `BaseUserModel` é a tabela de contas e
`make_user_token_model` a de tokens de ativação e reset.

```python
# src/db/models.py
from tempest_fastapi_sdk import BaseModel as BaseModel
from tempest_fastapi_sdk import BaseUserModel, BaseUserTokenModel, make_user_token_model


class UserModel(BaseUserModel):
    """Conta de usuário: e-mail, hash da senha e flags de ativação."""

    __tablename__ = "users"


UserTokenModel: type[BaseUserTokenModel] = make_user_token_model(user_table="users")

__all__: list[str] = ["BaseModel", "UserModel", "UserTokenModel"]
```

O `BaseModel` reexportado aqui é o que o `alembic/env.py` gerado importa
(`from src.db.models import BaseModel`) para o autogenerate.

## Migrar o schema no startup

Use `sync_schema_async()` no lifespan para o serviço subir com o banco no head,
venha ele vazio, já migrado ou anterior ao Alembic.

```python
# src/db/migrations.py
from pathlib import Path

from tempest_fastapi_sdk import AlembicHelper, SchemaSyncOutcomeEnum

ALEMBIC_INI_PATH: Path = Path(__file__).resolve().parents[2] / "alembic.ini"


async def run_migrations(database_url: str) -> SchemaSyncOutcomeEnum:
    """Leva o schema do banco até o head das migrations.

    Args:
        database_url (str): URL do banco que o serviço usa.

    Returns:
        SchemaSyncOutcomeEnum: Qual dos caminhos rodou.
    """
    return await AlembicHelper(
        str(ALEMBIC_INI_PATH),
        db_url=database_url,
    ).sync_schema_async()
```

```python
# src/db/__init__.py
from src.db.migrations import run_migrations as run_migrations
from src.db.models import UserModel as UserModel
from src.db.models import UserTokenModel as UserTokenModel

__all__: list[str] = ["UserModel", "UserTokenModel", "run_migrations"]
```

Gere o ambiente do Alembic e a primeira revision:

```bash
uv run tempest db init
uv run tempest db revision -m "initial"
```

A revision gerada cria `users` e `user_tokens`. Sem ela, o
`sync_schema_async()` devolve `NO_MIGRATIONS` e não cria tabela nenhuma; os
três caminhos estão em [Migrations](migrations.md#3-sincronize-o-schema-no-lifespan).

## Montar os recursos compartilhados

Construa banco, auth e upload **uma vez por processo**, em
`api/dependencies/`, e injete por `Depends`.

```python
# src/api/dependencies/resources.py
from collections.abc import Callable, Coroutine
from typing import Any

from tempest_fastapi_sdk import AsyncDatabaseManager, UploadUtils, UserAuthService

from src.core import settings
from src.db import UserModel, UserTokenModel

db: AsyncDatabaseManager = AsyncDatabaseManager(**settings.database_kwargs())
auth_service: UserAuthService = UserAuthService(
    db=db,
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
    email=None,
)
get_current_user: Callable[..., Coroutine[Any, Any, Any]] = (
    auth_service.current_user_dependency()
)
uploads: UploadUtils = UploadUtils(**settings.upload_kwargs())


def get_uploads() -> UploadUtils:
    """Devolve a instância única de `UploadUtils` do processo.

    Returns:
        UploadUtils: O utilitário configurado pelas settings.
    """
    return uploads
```

```python
# src/api/dependencies/__init__.py
from src.api.dependencies.resources import auth_service as auth_service
from src.api.dependencies.resources import db as db
from src.api.dependencies.resources import get_current_user as get_current_user
from src.api.dependencies.resources import get_uploads as get_uploads

__all__: list[str] = ["auth_service", "db", "get_current_user", "get_uploads"]
```

- `email=None`: sem `EmailUtils`, o serviço não envia e-mail; com
  `AUTH_AUTO_ACTIVATE=true` a conta já nasce ativa.
- `current_user_dependency()` verifica o bearer token com o mesmo segredo que o
  `auth_service` usa para assinar, e devolve o `UserModel` dono do token.
- `UploadUtils(**settings.upload_kwargs())` lê `UPLOAD_DIR`, o teto de tamanho
  e as allowlists do `.env`.

## Proteger a rota de upload

Peça o usuário por `Depends(get_current_user)`: sem token válido a rota
responde `401` antes de ler o arquivo.

```python
# src/api/routers/files.py
from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile
from pydantic import BaseModel
from tempest_fastapi_sdk import UploadUtils

from src.api.dependencies import get_current_user, get_uploads
from src.db import UserModel

router: APIRouter = APIRouter(prefix="/api/files", tags=["files"])


class FileUploadResponseSchema(BaseModel):
    """Onde o arquivo foi gravado."""

    key: str


@router.post("", status_code=201)
async def upload_file(
    file: UploadFile,
    current: Annotated[UserModel, Depends(get_current_user)],
    uploads: Annotated[UploadUtils, Depends(get_uploads)],
) -> FileUploadResponseSchema:
    """Grava o arquivo na pasta do usuário logado.

    Args:
        file (UploadFile): O arquivo do formulário multipart.
        current (UserModel): O dono do bearer token.
        uploads (UploadUtils): O utilitário de upload do processo.

    Returns:
        FileUploadResponseSchema: A key relativa ao `UPLOAD_DIR`.
    """
    key = await uploads.save(file, subdir=str(current.id))
    return FileUploadResponseSchema(key=str(key))
```

```python
# src/api/routers/__init__.py
from src.api.routers.files import router as files_router

__all__: list[str] = ["files_router"]
```

`subdir=str(current.id)` grava cada arquivo na pasta do dono:
`var/uploads/<user_id>/<uuid>.png`.

## Montar o app com lifespan

O lifespan migra o schema antes de abrir o banco; `register_exception_handlers`
transforma as exceções do SDK nos status e no envelope `{detail, code, details}`.

```python
# src/api/app.py
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from tempest_fastapi_sdk import (
    make_auth_router,
    make_health_router,
    register_exception_handlers,
)

from src.api.dependencies import auth_service, db
from src.api.routers import files_router
from src.core import settings
from src.db import run_migrations


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Migra o schema, abre o banco e fecha no shutdown.

    Args:
        app (FastAPI): A aplicação que está subindo.

    Yields:
        None: Devolve o controle ao FastAPI enquanto o app atende.
    """
    await run_migrations(settings.DATABASE_URL)
    await db.connect()
    yield
    await db.disconnect()


def create_app() -> FastAPI:
    """Monta o app com health, auth e upload.

    Returns:
        FastAPI: A aplicação pronta para o uvicorn.
    """
    app: FastAPI = FastAPI(title="servico", lifespan=lifespan)
    register_exception_handlers(app)
    app.include_router(make_health_router(db=db))
    app.include_router(
        make_auth_router(auth_service, session_factory=db.session_dependency),
    )
    app.include_router(files_router)
    return app


app: FastAPI = create_app()
```

O `make_auth_router` monta `/auth/signup`, `/auth/login`, `/auth/me`,
`/auth/refresh` e o resto do fluxo; o `make_health_router` monta
`/health/liveness` e `/health/readiness`.

## Subir o servidor

Rode o uvicorn pelo `run_server`, que lê host e porta do `ServerSettings`.

```python
# src/server.py
from tempest_fastapi_sdk import run_server

from src.core import settings


def run() -> None:
    """Sobe o uvicorn com host e porta das settings."""
    run_server("src.api.app:app", settings=settings)
```

```python
# main.py
from src.server import run

if __name__ == "__main__":
    run()
```

```bash
SERVER_PORT=8765 uv run python main.py
```

## Chamar a API com curl

Com o servidor no ar, este é o ciclo inteiro; a saída abaixo é a de uma
execução real (tokens cortados).

```bash
B=http://127.0.0.1:8765
curl -s -X POST $B/auth/signup -H 'Content-Type: application/json' \
  -d '{"email": "ana@example.com", "password": "senha-forte-123"}'
TOKEN=$(curl -s -X POST $B/auth/login -H 'Content-Type: application/json' \
  -d '{"email": "ana@example.com", "password": "senha-forte-123"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
curl -s $B/auth/me -H "Authorization: Bearer $TOKEN"
curl -s -X POST $B/api/files -H "Authorization: Bearer $TOKEN" -F 'file=@foto.png;type=image/png'
curl -s -X POST $B/api/files -F 'file=@foto.png;type=image/png'
curl -s -X POST $B/api/files -H "Authorization: Bearer $TOKEN" -F 'file=@foto.png;filename=a.zip;type=application/zip'
curl -s $B/health/readiness
```

| Chamada | Status | Corpo |
| --- | --- | --- |
| `POST /auth/signup` | `201` | `{"user_id": "...", "activation_required": false, ...}` |
| `POST /auth/login` | `200` | `{"user_id": "...", "access_token": "eyJ...", ...}` |
| `GET /auth/me` | `200` | `{"id": "...", "is_active": true, "email": "ana@example.com", ...}` |
| `POST /api/files` com token | `201` | `{"key": "<user_id>/<uuid>.png"}` |
| `POST /api/files` sem token | `401` | `{"detail": "Authorization token is missing or invalid", "code": "UNAUTHORIZED", "details": {}}` |
| `POST /api/files` com `.zip` | `415` | `{"detail": "Invalid file type", "code": "INVALID_FILE_TYPE", "details": {"extension": "zip", "allowed": ["jpg", "pdf", "png"]}}` |
| `GET /health/readiness` | `200` | `{"status": "ready", "checks": {"database": true}}` |

Depois do primeiro start, o banco tem `alembic_version`, `users` e
`user_tokens`; reiniciar o servidor sobe sem erro e o mesmo login responde `200`.

## Testar ponta a ponta

Use o app de produção com o lifespan rodando: o teste exercita a migration, o
auth e o upload de verdade, num banco e numa pasta temporários.

```python
# tests/conftest.py
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

TEST_DIR: Path = Path(tempfile.mkdtemp(prefix="servico-test-"))
TEST_ENVIRONMENT: dict[str, str] = {
    "ENV": "test",
    "DATABASE_URL": f"sqlite+aiosqlite:///{TEST_DIR / 'test.db'}",
    "JWT_SECRET": "segredo-de-teste-com-32-caracteres-ok",
    "AUTH_AUTO_ACTIVATE": "true",
    "UPLOAD_DIR": str(TEST_DIR / "uploads"),
    "UPLOAD_MAX_SIZE_BYTES": "1024",
    "UPLOAD_ALLOWED_EXTENSIONS": '[".png"]',
}
os.environ.update(TEST_ENVIRONMENT)

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Roda o lifespan (migration + banco) e devolve um client sem socket.

    Yields:
        AsyncClient: Client ligado ao app de produção.
    """
    async with app.router.lifespan_context(app):
        transport: ASGITransport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as opened:
            yield opened
```

```python
# tests/test_service.py
from httpx import AsyncClient, Response

PNG: bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


async def test_signup_login_me_upload(client: AsyncClient) -> None:
    """Leva uma conta do cadastro até um upload protegido.

    Args:
        client (AsyncClient): O client do app.
    """
    credentials: dict[str, str] = {"email": "ana@example.com", "password": "senha-forte-123"}

    signup: Response = await client.post("/auth/signup", json=credentials)
    assert signup.status_code == 201

    login: Response = await client.post("/auth/login", json=credentials)
    assert login.status_code == 200
    headers: dict[str, str] = {"Authorization": f"Bearer {login.json()['access_token']}"}

    me: Response = await client.get("/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == "ana@example.com"

    upload: Response = await client.post(
        "/api/files",
        headers=headers,
        files={"file": ("foto.png", PNG, "image/png")},
    )
    assert upload.status_code == 201
    assert upload.json()["key"].startswith(f"{me.json()['id']}/")

    anonymous: Response = await client.post(
        "/api/files",
        files={"file": ("foto.png", PNG, "image/png")},
    )
    assert anonymous.status_code == 401

    wrong_type: Response = await client.post(
        "/api/files",
        headers=headers,
        files={"file": ("pacote.zip", b"PK", "application/zip")},
    )
    assert wrong_type.status_code == 415
    assert wrong_type.json()["code"] == "INVALID_FILE_TYPE"

    too_large: Response = await client.post(
        "/api/files",
        headers=headers,
        files={"file": ("grande.png", PNG + b"\x00" * 2048, "image/png")},
    )
    assert too_large.status_code == 413
    assert too_large.json()["code"] == "FILE_TOO_LARGE"
```

```bash
uv run pytest -v
```

```text
tests/test_service.py::test_signup_login_me_upload PASSED                [100%]

============================== 1 passed in 0.42s ===============================
```

- `ENV=test` no ambiente do processo faz o `EnvironmentSettings` **ignorar o
  `.env`**: a suíte não herda o banco nem o segredo de dev.
- As variáveis entram em `os.environ` **antes** do `from src.api.app import app`,
  porque `settings`, `db` e `uploads` são construídos no import.
- O banco é um arquivo SQLite temporário, não `:memory:`: a migration roda numa
  thread (`sync_schema_async`) com conexão própria, e um banco em memória não é
  compartilhado entre conexões. Com `sqlite+aiosqlite:///:memory:` o `signup`
  falha com `no such table: users`.

## Recap

- Settings: herde os mixins (`EnvironmentSettings`, `ServerSettings`,
  `DatabaseSettings`, `JWTSettings`, `AuthSettings`, `UploadSettings`) e use os
  `*_kwargs()` para montar os clientes.
- Banco: `tempest db init` + `tempest db revision -m "initial"` uma vez; no
  lifespan, `sync_schema_async()` antes do `db.connect()`.
- Auth: `UserAuthService` + `make_auth_router` dão signup, login e `/auth/me`;
  `current_user_dependency()` protege as suas rotas.
- Upload: `UploadUtils(**settings.upload_kwargs())` injetado por `Depends`;
  `415`, `413` e `401` saem prontos com `register_exception_handlers`.
- Teste: o app de produção com `lifespan_context`, ambiente fixado com
  `ENV=test` antes do import.
