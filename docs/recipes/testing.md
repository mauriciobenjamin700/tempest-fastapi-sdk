# Testes

Você vai montar uma bateria de testes assíncrona — pytest + pytest-asyncio + SQLite em memória + `httpx.AsyncClient` — trocando o banco de produção por um descartável em cada teste.

pytest + pytest-asyncio + SQLite em memória + `httpx.AsyncClient`.

!!! tip "Por que `AsyncClient` em vez de `TestClient`?"
    `fastapi.testclient.TestClient` é síncrono — não suporta `async with`. Para testar endpoints async sem dor, use `httpx.AsyncClient(transport=ASGITransport(app=app))`, que monta o app via ASGI no mesmo event-loop dos seus testes. Os exemplos abaixo seguem esse padrão.

## Fixtures compartilhadas

```python
# tests/conftest.py
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AsyncDatabaseManager

import src.db.models  # noqa: F401 — side-effect: registers every model on BaseModel.metadata
from src.api.app import create_app


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


@pytest_asyncio.fixture
async def client(db: AsyncDatabaseManager) -> AsyncGenerator[AsyncClient, None]:
    """ASGI-backed async client with the prod DB swapped for the in-memory one."""
    app = create_app()
    # Override the session dependency to use the test DB.
    from src.api.app import db as production_db

    app.dependency_overrides[production_db.session_dependency] = db.session_dependency

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
```

## Teste de repository

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
    O modelo abstrato `BaseUserModel` declara as colunas **`email`**, **`hashed_password`**, **`is_admin`** e **`last_login_at`**, além de herdar **`id`**, **`is_active`**, **`created_at`** e **`updated_at`** do `BaseModel`. A coluna **`name`** usada nos exemplos **não** faz parte do `BaseUserModel` — ela é adicionada pelo próprio `UserModel` do projeto. Os campos não-default (`email` + `hashed_password`) são `nullable=False`, então omitir qualquer um deles dispara `IntegrityError` no flush. Note também que a coluna se chama **`hashed_password`** — não `password_hash`.

## Teste de endpoint

```python
# tests/api/test_users.py
from httpx import AsyncClient


class TestUsersAPI:
    async def test_signup(self, client: AsyncClient) -> None:
        response = await client.post(
            "/auth/signup",
            json={
                "email": "ana@example.com",
                "password": "strong-pass-12-chars",
                "name": "Ana",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert "user_id" in body
        # The activation link is only present when AUTH_RETURN_TOKEN_IN_RESPONSE=true
        # or no EmailUtils is wired — typical for the test environment.
        assert body["activation_required"] in {True, False}

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
| `create_test_engine` | `(database_url="sqlite+aiosqlite:///:memory:", *, echo=False) -> AsyncEngine` | Constrói um `AsyncEngine` descartável (StaticPool quando in-memory). |
| `create_test_session_factory` | `(engine) -> async_sessionmaker[AsyncSession]` | Constrói um `sessionmaker` vinculado ao engine (`expire_on_commit=False`). |
| `init_test_metadata` | `async (engine, metadata=None) -> None` | Cria todas as tabelas (default `BaseModel.metadata`). |
| `drop_test_metadata` | `async (engine, metadata=None) -> None` | Apaga todas as tabelas. |
| `test_database` | `async (database_url=..., *, metadata=None) -> AsyncIterator[async_sessionmaker[AsyncSession]]` | Context manager — entrega uma **session factory** num DB recém-criado, apaga e descarta na saída. |
| `test_session` | `async (database_url=..., *, metadata=None) -> AsyncIterator[AsyncSession]` | Context manager — entrega **um `AsyncSession`** em cima de um `test_database` novo. |

```python
# tests/conftest.py
from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tempest_fastapi_sdk.testing import test_database, test_session


@pytest_asyncio.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Yield a session factory backed by a fresh in-memory DB per test."""
    async with test_database() as factory:
        yield factory


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a single AsyncSession backed by a fresh in-memory DB."""
    async with test_session() as s:
        yield s
```

Use o context manager `test_session()` para testes ad-hoc que não precisam de fixture compartilhada:

```python
from tempest_fastapi_sdk.testing import test_session

from src.db.models import UserModel
from src.db.repositories import UserRepository


async def test_repo_directly() -> None:
    async with test_session() as session:
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

## A suíte em paralelo — `tempest test --fast`

Com um banco descartável por teste, os testes já não dividem estado. Rodar
em série, então, é pagar o tempo de um núcleo com a máquina inteira parada.
O `--fast` espalha a suíte pelos núcleos com o
[pytest-xdist](https://pytest-xdist.readthedocs.io/).

Primeiro, o que a suíte precisa — o extra `[tests]` traz `pytest`,
`pytest-asyncio` e `pytest-xdist`:

```bash
uv add --dev "tempest-fastapi-sdk[tests]"
```

Depois, a suíte:

```bash
tempest test --fast              # -n auto: um worker por núcleo
tempest test --fast -w 4         # quatro workers
tempest test tests/api --fast    # o alvo continua sendo repassado
tempest check --fast             # o gate completo, com o passo de teste em paralelo
```

Por baixo, vira `pytest -n <workers> -p no:cacheprovider [alvo]`, e o
código de saída do pytest volta sem tradução. O cache do pytest fica
desligado porque vários workers escrevendo `.pytest_cache` ao mesmo tempo é
corrida à toa — e é esse cache que o `--lf` / `--ff` leem, então esses dois
continuam sendo coisa de execução serial.

!!! info "O `--fast` chega no `tempest-cli` 0.4.0"
    Os comandos `test` e `check` são do
    [`tempest-cli`](https://pypi.org/project/tempest-cli/), que o SDK monta
    na CLI dele. O SDK ainda declara `tempest-cli>=0.3.0`; até ele subir esse
    piso, garanta a versão no seu projeto:

    ```bash
    uv add --dev "tempest-cli>=0.4.0"
    ```

    Com o `tempest-cli` 0.3.0, `tempest test --fast` sai com
    `No such option: --fast` (saída 2).

### Sem o pytest-xdist

Antes de rodar qualquer coisa, o `--fast` pergunta ao **mesmo
interpretador que vai rodar o pytest** se ele importa o `xdist`. Faltando,
a mensagem nomeia o pacote e o extra, e a saída é `127`, sem traceback —
em vez do `unrecognized arguments: -n` que o pytest daria sozinho:

```console
$ tempest test --fast
error: --fast needs pytest-xdist, which is not installed in the environment pytest runs in (.venv/bin/pytest). Install it with 'uv add --dev pytest-xdist' — or a bundle that carries it, 'uv add --dev "tempest-cli[tools]"' or 'uv add --dev "tempest-fastapi-sdk[tests]"' — and retry, or drop --fast to run the suite serially.
```

### Quando usar

- **Use** na suíte que é barreira de merge — local, antes do push, e no CI.
  O ganho cresce com a suíte: um serviço de 2678 testes numa máquina de 12
  núcleos caiu de ~5 min para 1 min 28 s
  ([#328](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/328)).
  A suíte deste SDK (10 218 testes), numa máquina de 6 núcleos físicos e 12
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
    - Use `httpx.AsyncClient` + `ASGITransport`, nunca o `TestClient` síncrono.
    - A fixture `db` cria um SQLite em memória por teste com `create_tables()` / `drop_tables()` — **sem argumentos**, elas usam `BaseModel.metadata` internamente.
    - `dependency_overrides` troca o banco de produção pelo de teste no `client`.
    - Os helpers de `tempest_fastapi_sdk.testing` (`test_database` / `test_session`) dão fixtures prontas quando você não precisa de um `AsyncDatabaseManager` completo.
    - `tempest test --fast` roda a suíte em paralelo com o extra `[tests]`; teste que só falha ali se confere rodando-o sozinho.

**Próximo passo:** veja a [receita de banco de dados](database.md) para os padrões de `BaseRepository` e migrations que esses testes exercitam.

## Recap

- A bateria é pytest + pytest-asyncio + SQLite em memória +
  `httpx.AsyncClient`: banco descartável por teste, sem tocar no de produção.
- As fixtures compartilhadas ficam no `conftest.py` do seu projeto — o SDK
  entrega os helpers, não as fixtures, para não exigir `pytest` importável em
  runtime de produção.
- `create_test_engine`, `test_database` e `test_session` cobrem o caso em que
  você não quer um `AsyncDatabaseManager` inteiro (sem `lifespan`, sem probe de
  health).
- `ModelFactory` + `seq` tiram o boilerplate de campo obrigatório: default
  declarado uma vez, override por teste, e o índice da linha chega ao callable
  para coluna única continuar única em `create_many`.
- Teste de endpoint sobe o app com `AsyncClient` e substitui dependência por
  `dependency_overrides` — o mesmo lugar onde entra um
  [fake »](fakes.md) em vez do provedor real.
- `tempest test --fast` (e `tempest check --fast`) espalha a suíte pelos
  núcleos com o pytest-xdist do extra `[tests]`; teste que só falha em
  paralelo é problema de isolamento até rodar sozinho e falhar também.
