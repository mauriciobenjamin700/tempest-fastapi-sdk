# Testes

Use esta página para montar a suíte de um serviço do SDK: pytest +
pytest-asyncio + `httpx.AsyncClient`, com o lifespan do app rodando, login de
verdade e o LLM trocado por um fake.

!!! note "`from src... import` em qualquer subpasta de `tests/`"
    O `pyproject.toml` que o `tempest new` gera traz `pythonpath = ["."]` no `[tool.pytest.ini_options]`. Sem isso, o pytest põe no `sys.path` a primeira pasta **sem** `__init__.py` acima do arquivo de teste: `tests/repositories/test_user.py` sem `tests/repositories/__init__.py`, ou `pytest tests/repositories`, falha na coleta com `ModuleNotFoundError: No module named 'src'`. Projeto gerado antes da 0.323.0 ganha a linha à mão:

    ```toml
    [tool.pytest.ini_options]
    asyncio_mode = "auto"
    pythonpath = ["."]
    testpaths = ["tests"]
    ```

## Subir o app no teste com o lifespan — `conftest.py`

Use quando o teste chama rota HTTP: este `conftest.py` fixa o ambiente, aponta
o banco para um arquivo temporário e abre o lifespan, que migra e conecta o
banco antes do primeiro request.

```python
# tests/conftest.py
"""Fixtures da suíte: ambiente fixo, banco em arquivo temporário, lifespan rodando."""

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

TEST_DATABASE_DIRECTORY: Path = Path(
    tempfile.mkdtemp(prefix=f"tests-{os.environ.get('PYTEST_XDIST_WORKER', 'main')}-")
)
"""Um diretório por processo: cada worker do pytest-xdist migra o próprio banco."""

TEST_ENVIRONMENT: dict[str, str] = {
    "ENV": "test",
    "DATABASE_URL": f"sqlite+aiosqlite:///{TEST_DATABASE_DIRECTORY / 'test.db'}",
    "AUTH_AUTO_ACTIVATE": "true",
}
"""O ambiente da suíte. Fixado antes do import do app, que lê as settings ao carregar."""

os.environ.update(TEST_ENVIRONMENT)

import pytest_asyncio  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from src.api.app import create_app  # noqa: E402


@pytest_asyncio.fixture
async def app() -> AsyncIterator[FastAPI]:
    """Monta o app e roda o lifespan dele: migração no startup, banco conectado.

    Um app novo por teste, então um ``dependency_overrides`` não vaza para o
    teste seguinte.

    Yields:
        FastAPI: O app com o lifespan aberto; fecha no teardown.
    """
    application: FastAPI = create_app()
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP que fala com o app pela interface ASGI, sem rede.

    Args:
        app (FastAPI): O app com o lifespan já aberto.

    Yields:
        AsyncClient: O cliente, com ``base_url`` fixo.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        yield http


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    """Cadastra um usuário, faz login e devolve o header ``Authorization``.

    Args:
        client (AsyncClient): O cliente do app.

    Returns:
        dict[str, str]: ``{"Authorization": "Bearer <access_token>"}``.
    """
    credentials: dict[str, str] = {
        "email": f"{uuid4().hex}@example.com",
        "password": "strong-pass-12-chars",
    }
    await client.post("/auth/signup", json=credentials)
    login = await client.post("/auth/login", json=credentials)
    return {"Authorization": f"Bearer {login.json()['access_token']}"}
```

| Peça | Por quê |
| --- | --- |
| `os.environ.update(...)` antes dos imports | O `src/core/settings.py` do `tempest new` monta as settings ao ser importado, e o `AsyncDatabaseManager` nasce com o `DATABASE_URL` daquele instante. Variável fixada depois não chega ao app. |
| SQLite em **arquivo**, não `:memory:` | A migração do lifespan abre a própria conexão. Com `:memory:` ela cria as tabelas num banco que some, e o primeiro request falha com `sqlite3.OperationalError: no such table: users` (medido). |
| Um diretório por `PYTEST_XDIST_WORKER` | Cada worker do `tempest test` migra o próprio arquivo, e a suíte não toca o `app.db` do projeto. |
| `application.router.lifespan_context(application)` | Roda o lifespan do app sem servidor. O `ASGITransport` sozinho não roda (próxima seção). |
| `AUTH_AUTO_ACTIVATE=true` | Sem ele a conta nasce inativa e o login responde `401` `invalid email or password` (medido). |
| e-mail com `uuid4()` | O banco é um por processo, não um por teste: e-mail fixo dá `409` no segundo cadastro. |

### `ASGITransport` sozinho não roda o lifespan

Use esta seção para entender o erro de quem monta só o cliente.
`AsyncClient(transport=ASGITransport(app=app))` sem o `lifespan_context` não
entra no lifespan: a migração não roda e o banco fica vazio. Medido em
2026-11-27 (httpx 0.28.1, FastAPI 0.165.0, Starlette 1.10.0), contra o app do
`tempest new --extras auth`, com um espião no `lifespan_context`:

| Forma | Lifespan entrou? | `POST /auth/signup` |
| --- | --- | --- |
| só `ASGITransport` | não | `client.post` **levanta** `sqlite3.OperationalError: no such table: users` |
| `lifespan_context` + `ASGITransport` | sim | `201` |

O erro sai do `client.post`, não vira resposta `500`: o `ASGITransport`
repassa a exceção do app por padrão.

!!! info "E o `asgi-lifespan`?"
    O `LifespanManager` do pacote `asgi-lifespan` também abre o lifespan, mas
    não é dependência do SDK. O `lifespan_context` vem com o FastAPI, que
    você já tem.

## Testar signup, login e `/me`

Use quando o serviço monta o `make_auth_router`: o teste cadastra, faz login e
chama a rota protegida com o token.

O app do exemplo é o do `tempest new --extras auth` com o auth montado como na
[receita de auth](auth-flow.md): um `UserTokenModel` criado por
`make_user_token_model(user_table=USER_TABLE_NAME)` (com a migração gerada),
`AuthSettings` no `Settings`, o `UserAuthService` abaixo e, em `create_app()`,
`app.include_router(make_auth_router(auth_service, session_factory=db.session_dependency))`.

```python
# src/api/dependencies/auth.py
"""Auth do serviço: o ``UserAuthService`` e a dependência de usuário logado."""

from tempest_fastapi_sdk import UserAuthService, make_token_dependency

from src.core import settings
from src.db.models import UserModel, UserTokenModel

from .resources import db

require_token = make_token_dependency(
    settings.TOKEN_SECRET,
    allow_unauthenticated=settings.ENV != "production",
)

auth_service: UserAuthService = UserAuthService(
    db=db,
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
    email=None,
)
"""Signup, login e tokens; ``make_auth_router`` monta as rotas em cima dele."""

get_current_user = auth_service.current_user_dependency()
"""``Depends(get_current_user)`` exige ``Authorization: Bearer <access_token>``."""

__all__: list[str] = ["auth_service", "get_current_user", "require_token"]
```

O teste:

```python
# tests/integration/test_auth.py
from uuid import uuid4

from httpx import AsyncClient


async def test_signup_login_and_me(client: AsyncClient) -> None:
    """Cenário: conta nova, login com a senha dela, ``/auth/me`` com o token.

    Esperado: ``201`` no cadastro, ``200`` no login e o mesmo e-mail em ``/me``.
    """
    email: str = f"{uuid4().hex}@example.com"
    credentials: dict[str, str] = {"email": email, "password": "strong-pass-12-chars"}

    signup = await client.post("/auth/signup", json=credentials)
    assert signup.status_code == 201, signup.text

    login = await client.post("/auth/login", json=credentials)
    assert login.status_code == 200, login.text
    access_token: str = login.json()["access_token"]

    me = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me.status_code == 200, me.text
    assert me.json()["email"] == email


async def test_me_without_token_is_401(client: AsyncClient) -> None:
    """Cenário: ``/auth/me`` sem header ``Authorization``.

    Esperado: ``401``.
    """
    response = await client.get("/auth/me")
    assert response.status_code == 401, response.text
```

Uma rota sua protegida por `Depends(get_current_user)` se testa igual: o
fixture `auth_headers` do `conftest.py` já devolve o header pronto, e a
próxima seção o usa.

## Trocar o LLM por um fake numa rota

Use quando a rota chama `chat_structured` ou `generate_structured`: o
[`FakeStructuredBackend`](fakes.md#saida-estruturada-fakestructuredbackend)
entra pelo `dependency_overrides` e devolve a instância que o teste escolheu,
sem modelo e sem rede.

A rota recebe o gerador por `Depends`, nunca o constrói por dentro — é isso
que deixa o teste trocá-lo:

```python
# src/api/dependencies/genai.py
"""O gerador de LLM do serviço, entregue por ``Depends``."""

from tempest_fastapi_sdk.genai import OllamaGenerator

generator: OllamaGenerator = OllamaGenerator(model="gpt-oss:20b")
"""Um gerador por processo; o teste troca ``get_generator``, não este objeto."""


def get_generator() -> OllamaGenerator:
    """Devolve o gerador do processo.

    Returns:
        OllamaGenerator: O gerador compartilhado.
    """
    return generator
```

```python
# src/api/routers/extraction.py
"""``POST /api/invoices/extract`` — o modelo lê a nota e devolve os campos."""

from fastapi import Depends
from pydantic import BaseModel
from tempest_fastapi_sdk import TempestAPIRouter
from tempest_fastapi_sdk.genai import OllamaGenerator

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.genai import get_generator
from src.db.models import UserModel


class InvoiceSchema(BaseModel):
    """Os campos que o modelo preenche."""

    number: str
    total_cents: int


class DocumentSchema(BaseModel):
    """O texto da nota enviado pelo cliente."""

    text: str


router: TempestAPIRouter = TempestAPIRouter(prefix="/invoices", tags=["invoices"])


@router.post("/extract")
async def extract_invoice(
    payload: DocumentSchema,
    user: UserModel = Depends(get_current_user),
    generator: OllamaGenerator = Depends(get_generator),
) -> InvoiceSchema:
    """Extrai número e total de uma nota.

    Args:
        payload (DocumentSchema): O texto da nota.
        user (UserModel): Quem chama; a rota exige login.
        generator (OllamaGenerator): O gerador, trocável por ``dependency_overrides``.

    Returns:
        InvoiceSchema: Os campos que o modelo preencheu.
    """
    return await generator.chat_structured(
        [
            {"role": "system", "content": "Extraia o número e o total da nota."},
            {"role": "user", "content": payload.text},
        ],
        InvoiceSchema,
        think="low",
    )
```

O teste enfileira a resposta, troca a dependência e confere o que a rota
mandou ao modelo:

```python
# tests/integration/test_extraction.py
from fastapi import FastAPI
from httpx import AsyncClient
from tempest_fastapi_sdk.testing.fakes import FakeStructuredBackend

from src.api.dependencies.genai import get_generator
from src.api.routers.extraction import InvoiceSchema


async def test_extract_returns_what_the_model_filled(
    app: FastAPI,
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Cenário: a rota de extração com o gerador trocado pelo fake.

    Esperado: o JSON da resposta é a instância enfileirada, e o fake
    registrou o documento e o ``think`` que a rota mandou.
    """
    fake: FakeStructuredBackend = FakeStructuredBackend()
    fake.queue(InvoiceSchema(number="42", total_cents=1990))
    app.dependency_overrides[get_generator] = lambda: fake

    response = await client.post(
        "/api/invoices/extract",
        json={"text": "Nota 42, total R$ 19,90"},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"number": "42", "total_cents": 1990}
    assert fake.prompts == ["Nota 42, total R$ 19,90"]
    assert fake.requests[0].schema is InvoiceSchema
    assert fake.requests[0].think == "low"
```

O `conftest.py` e os dois arquivos de teste acima rodaram juntos num projeto
do `tempest new --extras auth` com estas peças, em 2026-11-27: `3 passed` em
série e com `-n 2`, os dois sob `-W error`.

## Testar um repository com banco em memória

Use quando o teste fala com o repository direto, sem HTTP e sem lifespan:
um `AsyncDatabaseManager` em memória por teste, com as tabelas criadas pelo
metadata.

```python
# tests/repositories/conftest.py
from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AsyncDatabaseManager

import src.db.models  # noqa: F401 — side-effect: registers every model on BaseModel.metadata


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncDatabaseManager, None]:
    """Fresh in-memory DB per test."""
    manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await manager.connect()
    await manager.create_tables()
    try:
        yield manager
    finally:
        await manager.drop_tables()
        await manager.disconnect()


@pytest_asyncio.fixture
async def session(db: AsyncDatabaseManager) -> AsyncGenerator[AsyncSession, None]:
    """Managed session bound to the in-memory DB."""
    async for s in db.session_dependency():
        yield s
```

Aqui o `:memory:` serve: quem cria as tabelas é o `create_tables()` na mesma
conexão, não uma migração.

### O teste do repository

```python
# tests/repositories/test_user.py
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import UserNotFoundError
from src.db.models import UserModel
from src.db.repositories import UserRepository


class TestUserRepository:
    async def test_get_by_email_raises_when_missing(
        self, session: AsyncSession
    ) -> None:
        repo = UserRepository(session)
        with pytest.raises(UserNotFoundError):
            await repo.get({"email": "ghost@example.com"})

    async def test_add_and_get(self, session: AsyncSession) -> None:
        repo = UserRepository(session)
        user = await repo.add(
            UserModel(
                email="ana@example.com",
                name="Ana",
                hashed_password="<bcrypt-hash>",
            )
        )
        loaded = await repo.get_by_id(user.id)
        assert loaded.email == "ana@example.com"
```

!!! warning "Campos do `BaseUserModel`"
    O modelo abstrato `BaseUserModel` declara as colunas **`email`**, **`hashed_password`**, **`is_admin`**, **`last_login_at`** e **`email_verified_at`**, além de herdar **`id`**, **`is_active`**, **`created_at`** e **`updated_at`** do `BaseModel`. A coluna **`name`** usada nos exemplos **não** faz parte do `BaseUserModel` — ela é adicionada pelo próprio `UserModel` do projeto. Os campos não-default (`email` + `hashed_password`) são `nullable=False`, então omitir qualquer um deles dispara `IntegrityError` no flush. Note também que a coluna se chama **`hashed_password`** — não `password_hash`.

## Conferir o envelope de erro de uma rota

Use quando a rota levanta uma `AppException`: o teste usa o `client` do
`conftest.py` e confere o `code` do envelope.

```python
# tests/api/test_users.py
from httpx import AsyncClient


class TestUsersAPI:
    async def test_get_user_not_found(self, client: AsyncClient) -> None:
        response = await client.get(
            "/api/users/00000000-0000-0000-0000-000000000000",
        )
        assert response.status_code == 404
        body = response.json()
        # SDK envelope is always {detail, code, details}. The `code` value
        # is set by the project's UserNotFoundError subclass — use whichever
        # constant your project chose (see Tutorial §5).
        assert "code" in body
```

!!! note "O `code` na resposta de erro"
    O SDK serializa toda `AppException` no envelope `{detail, code, details}`. O valor exato de `code` depende da subclasse de domínio que **o projeto** define — `UserNotFoundError(NotFoundException, code="USER_NOT_FOUND")` é só uma convenção do tutorial. Veja o passo 5 do tutorial pra criar suas próprias subclasses.

## Helpers de `tempest_fastapi_sdk.testing`

`tempest_fastapi_sdk.testing` traz helpers agnósticos de framework que não exigem que o `pytest` seja importável — embrulhe-os em `@pytest.fixture` dentro do `conftest.py` do projeto consumidor. Úteis quando um teste não precisa de um `AsyncDatabaseManager` completo (sem `lifespan`, sem probes de health-check).

| Helper | Assinatura | Propósito |
| --- | --- | --- |
| `create_test_engine` | `(database_url="sqlite+aiosqlite:///:memory:", *, echo=False, foreign_keys=True) -> AsyncEngine` | Constrói um `AsyncEngine` descartável (StaticPool quando in-memory); em SQLite confere chave estrangeira e aplica o savepoint do manager. |
| `create_test_session_factory` | `(engine) -> async_sessionmaker[AsyncSession]` | Constrói um `sessionmaker` vinculado ao engine (`expire_on_commit=False`). |
| `init_test_metadata` | `async (engine, metadata=None) -> None` | Cria todas as tabelas (default `BaseModel.metadata`). |
| `drop_test_metadata` | `async (engine, metadata=None) -> None` | Apaga todas as tabelas. |
| `make_test_database` | `async (database_url=..., *, metadata=None) -> AsyncIterator[async_sessionmaker[AsyncSession]]` | Context manager — entrega uma **session factory** num DB recém-criado, apaga e descarta na saída. |
| `make_test_session` | `async (database_url=..., *, metadata=None) -> AsyncIterator[AsyncSession]` | Context manager — entrega **um `AsyncSession`** em cima de um `make_test_database` novo. |

```python
# tests/conftest.py
from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tempest_fastapi_sdk.testing import make_test_database, make_test_session


@pytest_asyncio.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Yield a session factory backed by a fresh in-memory DB per test."""
    async with make_test_database() as factory:
        yield factory


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a single AsyncSession backed by a fresh in-memory DB."""
    async with make_test_session() as s:
        yield s
```

Use o context manager `make_test_session()` para testes ad-hoc que não precisam de fixture compartilhada:

```python
from tempest_fastapi_sdk.testing import make_test_session

from src.db.models import UserModel
from src.db.repositories import UserRepository


async def test_repo_directly() -> None:
    async with make_test_session() as session:
        repo = UserRepository(session)
        await repo.add(
            UserModel(
                email="ana@example.com",
                name="Ana",
                hashed_password="<bcrypt-hash>",
            )
        )
        assert await repo.count() == 1
```

!!! warning "`test_session` e `test_database` viraram `make_test_session` e `make_test_database`"
    O pytest coleta como teste **toda função importada** num módulo de teste
    cujo nome comece com `test`. Com os nomes antigos, um
    `from tempest_fastapi_sdk.testing import test_session` fazia a suíte
    ganhar um item fantasma, `test_x.py::test_session`, que "passava" sem
    testar nada e emitia `PytestReturnNotNoneWarning` — falha de verdade em
    quem roda com `-W error` ou `filterwarnings = ["error"]`.

    Os nomes antigos continuam importáveis como alias deprecado: cada
    chamada emite `DeprecationWarning`, e os dois carregam `__test__ = False`,
    então o pytest não os coleta mais. Troque o import pelo nome novo; o
    alias sai numa versão futura.

### O engine de teste confere chave estrangeira

`create_test_engine` — e com ele `make_test_database` e `make_test_session` — monta o
engine SQLite com a mesma configuração do `AsyncDatabaseManager`, menos o WAL:

- **chave estrangeira conferida** (`PRAGMA foreign_keys=ON` em toda
  conexão): filho órfão levanta `IntegrityError` e `ON DELETE CASCADE` apaga
  os filhos, como no PostgreSQL;
- **savepoint de verdade**: um `begin_nested()` que sai limpo não comita a
  transação de fora (antes, o `RELEASE SAVEPOINT` virava o commit final no
  SQLite).

Consequência prática: **teste que grava um filho semeia o pai antes.** Um
`user_id=uuid4()` solto, que antes passava no SQLite e falharia no
PostgreSQL, agora falha aqui também:

```python
import pytest
from sqlalchemy import ForeignKey
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from tempest_fastapi_sdk.testing import make_test_session


class Base(DeclarativeBase):
    """Base declarativa só deste exemplo."""


class Org(Base):
    """Organização: a linha pai."""

    __tablename__ = "orgs"

    id: Mapped[int] = mapped_column(primary_key=True)


class Member(Base):
    """Membro de uma organização."""

    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))


async def test_member_needs_an_existing_org() -> None:
    async with make_test_session(metadata=Base.metadata) as session:
        session.add(Member(id=1, org_id=999))
        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            await session.commit()


async def test_member_of_a_seeded_org() -> None:
    async with make_test_session(metadata=Base.metadata) as session:
        session.add(Org(id=1))
        await session.flush()
        session.add(Member(id=1, org_id=1))
        await session.commit()
        assert await session.get(Member, 1) is not None
```

O `flush()` depois do pai importa quando os dois models não têm
`relationship()`: sem ele, o unit of work do SQLAlchemy manda os INSERTs na
ordem do `add`, e o do filho pode sair primeiro.

Para desligar — fixture que reproduz de propósito um banco legado com órfão:

```python
from sqlalchemy.ext.asyncio import AsyncEngine

from tempest_fastapi_sdk.testing import create_test_engine

engine: AsyncEngine = create_test_engine(foreign_keys=False)
```

!!! note "Só vale para SQLite"
    `foreign_keys` só vale para SQLite. Com a URL de outro backend o engine
    sai sem listener nenhum — o PostgreSQL sempre confere a chave.

### Todo campo de schema documentado — `assert_schema_metadata`

`assert_schema_metadata(modulo)` falha listando cada campo exportado sem
`title`, `description` e `examples` (o campo que guarda outro schema
precisa só dos dois primeiros). O projeto do `tempest new` já traz o teste:

```python
# tests/test_schemas.py
import src.schemas
from tempest_fastapi_sdk.testing import assert_schema_metadata


def test_exported_schemas_carry_metadata() -> None:
    """Cenário: os schemas que ``src.schemas`` exporta.

    Esperado: todo campo traz ``title``, ``description`` e ``examples``.
    """
    assert_schema_metadata(src.schemas)
```

`schema_metadata_findings(modulo)` devolve os achados sem levantar, e
`tempest lint-schemas` imprime a mesma lista na linha de comando — o que
conta como campo folha, a herança e a saída real estão na
[receita do CLI](cli/lint-schemas.md).

## Factories de modelo — `ModelFactory` + `seq`

Construir instâncias com todos os campos obrigatórios em cada teste é
repetitivo. `ModelFactory` amarra o modelo + valores default à sessão;
`build()` devolve uma instância solta, `create()` persiste (add + flush +
refresh) e `create_many(n)` cria várias. Overrides por chamada vencem os
defaults.

```python
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk.testing import ModelFactory, seq

from src.db.models import UserModel


# Num serviço, a sessão real vem de `db.get_session_context()`; aqui, do SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

users = ModelFactory(
    session,
    UserModel,
    email=seq("user{n}@example.com"),  # único por linha
    hashed_password="x",
    is_admin=False,
)


async def main() -> None:
    """Run this example."""
    alice = await users.create(is_admin=True)  # 1 linha, um campo trocado
    team = await users.create_many(5)  # 5 linhas, e-mails únicos
    draft = users.build(email="temp@x.com")  # instância não salva


asyncio.run(main())
```

**Sem mágica**: a factory nunca adivinha valor de campo obrigatório —
você declara os defaults. Um default (ou override) **callable** recebe o
índice da linha (int incremental) e vira gerador por-linha; `seq(...)` é
o atalho pro caso `"{n}"`. Usa `flush` (não `commit`), então as linhas
ficam visíveis na transação do teste e o rollback fica com a fixture.

Passe `metadata=` quando o projeto mistura a `BaseModel.metadata` do SDK com uma segunda metadata isolada (raro — mantenha um `BaseModel` por serviço sempre que possível).

## A suíte em paralelo — o padrão do `tempest test`

Com um banco descartável por teste, os testes já não dividem estado. Rodar
em série, então, é pagar o tempo de um núcleo com a máquina inteira parada.
Por isso `tempest test` e `tempest check` espalham a suíte pelos núcleos com o
[pytest-xdist](https://pytest-xdist.readthedocs.io/) **por padrão**.

Primeiro, o que a suíte precisa — o extra `[tests]` traz `pytest`,
`pytest-asyncio` e `pytest-xdist` (o projeto do `tempest new` já declara o
`pytest-xdist` no grupo `dev`):

```bash
uv add --dev "tempest-fastapi-sdk[tests]"
```

!!! info "Os gates do `tempest` pedem o extra `[cli]`"
    `lint`, `fix`, `format`, `fmt-check`, `type`, `test`, `check` e
    `pr-prompt` vêm do `[cli]`; com só o `[cli-ops]` (o do servidor), eles
    saem com código 2 apontando o `[cli]`. Instale com
    `uv add --dev "tempest-fastapi-sdk[cli]"` — detalhes em
    [Instalação → CLI](../installation.md#cli).

Depois, a suíte:

```bash
tempest test                     # -n auto: um worker por núcleo
tempest test -w 4                # quatro workers
tempest test tests/api           # o alvo continua sendo repassado
tempest test --serial            # em série: para --lf / --ff ou depurador
tempest check                    # o gate completo, com o passo de teste em paralelo
```

Por baixo, vira `pytest -n <workers> -p no:cacheprovider [alvo]`, e o
código de saída do pytest volta sem tradução. O cache do pytest fica
desligado porque vários workers escrevendo `.pytest_cache` ao mesmo tempo é
corrida à toa — e é esse cache que o `--lf` / `--ff` leem, então esses dois
pedem `--serial`. `--workers` com `--serial` é erro de uso (saída `2`).

!!! info "Paralelo por padrão desde o `tempest-cli` 0.6.0"
    Os comandos `test` e `check` são do
    [`tempest-cli`](https://pypi.org/project/tempest-cli/), que o SDK monta
    na CLI dele, e o extra `[cli]` declara `tempest-cli>=0.7.0`. Até o 0.5,
    o paralelo pedia `--fast`; a flag continua aceita e passou a significar
    "exija o pytest-xdist".

!!! warning "Banco por worker"
    Cada teste que monta o app roda o lifespan, que migra o banco do
    `DATABASE_URL`. Dois workers migrando o **mesmo** arquivo SQLite falharam
    com `table alembic_version already exists`. O `tests/conftest.py` que o
    `tempest new` gera aponta o `DATABASE_URL` de cada worker para um arquivo
    próprio, num diretório temporário — e, de quebra, a suíte não toca o
    `app.db` do projeto.

### Sem o pytest-xdist

O padrão pergunta ao **mesmo interpretador que vai rodar o pytest** se ele
importa o `xdist`. Faltando, avisa numa linha e roda em série (saída
medida):

```console
$ tempest test
note: pytest-xdist is not installed in the environment pytest runs in (.venv/bin/pytest), so the suite runs serially. Install it with 'uv add --dev pytest-xdist' — or a bundle that carries it, 'uv add --dev "tempest-cli[tools]"' or 'uv add --dev "tempest-fastapi-sdk[tests]"' — to run it in parallel.
```

O `--fast` explícito exige o plugin: sem ele, a mensagem nomeia o pacote e
a saída é `127`, sem rodar nada:

```console
$ tempest test --fast
error: --fast needs pytest-xdist, which is not installed in the environment pytest runs in (.venv/bin/pytest). Install it with 'uv add --dev pytest-xdist' — or a bundle that carries it, 'uv add --dev "tempest-cli[tools]"' or 'uv add --dev "tempest-fastapi-sdk[tests]"' — and retry, or pass --serial to run the suite serially.
```

### Quando usar

- **Use** na suíte que é barreira de merge — local, antes do push, e no CI.
  O ganho cresce com a suíte: um serviço de 2678 testes numa máquina de 12
  núcleos caiu de ~5 min para 1 min 28 s
  ([#328](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/328)).
  A suíte deste SDK, com 10 218 testes no desenvolvimento da v0.302.0 (o
  número cresce a cada release), numa máquina de 6 núcleos físicos e 12
  threads, foi de 2127 s em série para 411 s com `--fast` — cerca de 5,2x,
  medido numa execução de cada.
- **`auto` conta núcleo físico quando o `psutil` está instalado** (regra do
  pytest-xdist): na máquina acima, `auto` subiu 6 workers. Sem `psutil`, conta
  CPU lógica. Para usar as threads, `-w logical`.
- **Não use** para rodar um arquivo ou um teste só: subir os workers custa
  mais do que o teste.

### Teste que só falha em paralelo

O paralelismo expõe teste que depende de ordem ou de máquina ociosa: dois
testes escrevendo o mesmo arquivo, a mesma porta, um global de módulo, ou um
`sleep` fixo esperando algo que, com todos os núcleos ocupados, demora mais.
Antes de tratar a falha como regressão, rode o teste **sozinho e em série**:

```bash
tempest test "tests/test_scheduler.py::test_lease_expires"
```

- **Passou sozinho**: o defeito é o isolamento do teste, não a mudança em
  revisão. Conserte o teste — arquivo em `tmp_path`, porta livre, espera por
  condição em vez de `sleep`.
- **Falhou sozinho também**: é regressão de verdade.

!!! warning "O extra carrega um teto herdado: `pytest<10`"
    O `[tests]` não declara teto nenhum, mas herda os do `requires-dist` de
    quem ele traz: o `pytest-asyncio` 1.4.0 exige `pytest<10,>=8.4`, e o
    próprio `pytest` exige `pluggy<2`. Todo serviço daqui já depende do
    `pytest-asyncio`, então o teto não é novo — só passou a estar escrito. O
    `pytest-xdist` 3.8.0 (`execnet>=2.1`, `pytest>=7.0.0`) não traz teto.

!!! check "Recapitulando"
    - Teste de rota abre o lifespan com `app.router.lifespan_context(app)` e fala com o app por `ASGITransport`; o transport sozinho não roda o lifespan.
    - O ambiente é fixado antes do import do app, e o banco é um arquivo temporário por worker, não `:memory:`.
    - `dependency_overrides` troca o gerador de LLM pelo `FakeStructuredBackend` numa rota.
    - A fixture `db` cria um SQLite em memória por teste com `create_tables()` / `drop_tables()` — **sem argumentos**, elas usam `BaseModel.metadata` internamente — para teste de repository sem HTTP.
    - Os helpers de `tempest_fastapi_sdk.testing` (`make_test_database` / `make_test_session`) dão fixtures prontas quando você não precisa de um `AsyncDatabaseManager` completo.
    - `tempest test` roda a suíte em paralelo por padrão (`--serial` para desligar), com um banco por worker; teste que só falha ali se confere rodando-o sozinho.

**Próximo passo:** veja a [receita de banco de dados](database.md) para os padrões de `BaseRepository` e migrations que esses testes exercitam.

## Recap

- A bateria é pytest + pytest-asyncio + `httpx.AsyncClient`, sem tocar no
  banco de produção: teste de rota roda o lifespan sobre um SQLite em arquivo
  temporário, teste de repository usa SQLite em memória.
- Signup → login → `/auth/me` roda pelo `make_auth_router` de verdade; o
  fixture `auth_headers` entrega o header para as suas rotas protegidas.
- As fixtures compartilhadas ficam no `conftest.py` do seu projeto — o SDK
  entrega os helpers, não as fixtures, para não exigir `pytest` importável em
  runtime de produção.
- `create_test_engine`, `make_test_database` e `make_test_session` cobrem o caso em que
  você não quer um `AsyncDatabaseManager` inteiro (sem `lifespan`, sem probe de
  health) — e conferem chave estrangeira, então o teste semeia o pai antes do
  filho.
- `ModelFactory` + `seq` tiram o boilerplate de campo obrigatório: default
  declarado uma vez, override por teste, e o índice da linha chega ao callable
  para coluna única continuar única em `create_many`.
- Teste de endpoint sobe o app com `AsyncClient` e substitui dependência por
  `dependency_overrides` — o mesmo lugar onde entra um
  [fake »](fakes.md) em vez do provedor real, como o `FakeStructuredBackend`
  no lugar do `OllamaGenerator`.
- `tempest test` (e `tempest check`) espalha a suíte pelos núcleos com o
  pytest-xdist do extra `[tests]`, por padrão; teste que só falha em
  paralelo é problema de isolamento até rodar sozinho e falhar também.
