# Padrão de serviço

Esta página é a **referência citável** de como um serviço Tempest é
organizado: cada item do padrão, o porquê dele, e **o que o cobra hoje** —
um código do `tempest lint-layers`, um passo do `tempest check` ou um teste
que o `tempest new` já escreve no seu projeto. Quando o item não tem gate,
a página diz isso também.

O padrão é o do `transport-backend`, o serviço em que essas regras foram
medidas. A [#718](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/718)
registra por que ele virou referência: um serviço novo foi escrito por IA
**sem partir do `tempest new`**, e só a revisão contra o transport o trouxe
de volta para `lint-layers --strict`, `lint-schemas` e `openapi-errors`
limpos.

!!! tip "Comece pelo `tempest new`"
    Tudo o que esta página descreve já sai do `tempest new`. Escrever o
    serviço à mão, ou copiar outro, é exatamente o erro da #718. Para um
    domínio novo dentro de um projeto que já existe, siga a receita do
    `CLAUDE.md` que o scaffold escreve; para as regras de forma de arquivo,
    veja a [Arquitetura recomendada](recommended-architecture.md).

## O que o `tempest new` gera

!!! info "O comando `tempest` pede o extra `[cli]` (ou `[cli-ops]` no servidor)"
    Sem um dos dois, `tempest` sai com código 2 e imprime como instalar. Em
    desenvolvimento: `uv add --dev "tempest-fastapi-sdk[cli]"`. Detalhes em
    [Instalação → CLI](installation.md#cli).

```bash
tempest new demo --extras sqlite
```

A árvore de diretórios do projeto (`tree -d`, medida na branch desta
página):

```text
.
├── alembic
│   └── versions
├── src
│   ├── api
│   │   ├── dependencies
│   │   ├── docs
│   │   └── routers
│   ├── controllers
│   │   └── system
│   ├── core
│   │   ├── constants
│   │   │   └── messages
│   │   ├── enums
│   │   ├── exceptions
│   │   └── types
│   ├── db
│   │   ├── configs
│   │   ├── models
│   │   └── repositories
│   ├── schemas
│   ├── scripts
│   ├── services
│   └── utils
└── tests
    ├── integration
    │   └── system
    └── unit
        ├── architecture
        └── core
```

E o `tempest check` sobre ele, sem tocar em nada (`ENV=test`, que o
`conftest.py` gerado também fixa):

```text
$ ruff check --extend-select ANN001,ANN201,ANN202,ANN205,ANN206 .
All checks passed!
$ ruff format --check .
61 files already formatted
$ mypy --disallow-untyped-defs --disallow-incomplete-defs .
Success: no issues found in 59 source files
$ lint-layers
Layer rules: no findings.
$ openapi-errors
Every route's declared errors match its flow (src).
$ lint-schemas
Schema metadata: src.schemas is fully documented.
$ pytest -n auto -p no:cacheprovider
...
9 passed
```

Os nove testes são os que o scaffold escreve: três de integração da rota
raiz e do health, três guards de arquitetura e três do contrato de
exceções.

## O padrão, item a item

| Regra | Como fica | Quem cobra hoje |
| --- | --- | --- |
| Uma classe de camada por arquivo, por contexto | `src/<camada>/<contexto>/<arquivo>.py`, só a classe + `logger` + `__all__` | `LAY001`, `LAY002` |
| Nome de arquivo sem o sufixo da camada | `services/orders/checkout.py` → `CheckoutService` (nunca `checkout_service.py`) | `LAY003` |
| Até 1000 linhas por arquivo, código e teste, sem lista de exceção | service grande vira vários por responsabilidade; teste grande, um arquivo por tema | `SIZE001` |
| Sem `def` aninhado nem `_x` de módulo, em `src` e `tests` | callback com estado vira objeto chamável | `STR001`, `STR002` |
| `__init__` com import relativo; fora dele, import do pacote | `from .x import Y` / `from src.services import X` | `IMP001`, `IMP002` |
| `__init__.py` só reexporta | `from .x import Y as Y` + `__all__`; montagem em `router.py`, `factory.py`, `runner.py` | `INIT001` |
| Recurso é singleton de processo num lugar só | `api/dependencies/resources.py` (`db`, `get_session`, `get_*`) | — (convenção do scaffold) |
| Providers por camada; router nunca vê `AsyncSession` | `api/dependencies/controllers.py` chama os `build_*` | `RTR001`, `DI001`–`DI003` |
| Controller herda da base do SDK | `BaseController[Service]` (um service) ou `Controller` (vários, ou nenhum) | — (convenção do scaffold) |
| Login e cadastro vêm do SDK | `UserAuthService` + `make_auth_router(..., prefix="/api/auth")` | — (sem gate) |
| `/api` posto num lugar só | `routers/api.py` → `build_api_router()` | `RTR003`, `RTR004` |
| Router de domínio é `TempestAPIRouter` | `TempestAPIRouter(prefix=...)`, nunca `fastapi.APIRouter` | `RTR005` |
| Texto do OpenAPI fora do router | `api/docs/<router>.py`, um `EndpointDoc` por handler | `RTR006` |
| Exceções: um módulo por status, contrato no corpo da classe | `core/exceptions/{bad_request,unauthorized,forbidden,not_found,conflict}.py` | `tests/unit/core/test_exceptions_contract.py` |
| Contrato de exceção congelado | status, `code`, mensagem e `field` de cada classe | `tests/unit/core/test_exceptions_contract.py` |
| Prosa e constante fora do módulo que usa | `core/constants/` + `core/constants/messages/{errors,successes}.py` | — (convenção do scaffold) |
| `Protocol`, alias, `NamedTuple`, enum | `core/types/<domínio>.py`, `core/enums/<domínio>.py` | `LAY001` (fora da camada); `ENUM001` (sufixo) |
| Banco | `db/configs/{connection,migrations,names}.py` | — (convenção do scaffold) |
| Mapeamento no repository | `map_to_response` | `REPO001` (parcial) |
| Grafo montado fora do request | `scripts/composition.py` (`build_*`) | `DI003` (`from_session`) |
| Todo campo de schema documentado | `Field(title=..., description=..., examples=...)` | `lint-schemas` + `tests/unit/architecture/test_schemas.py` |
| Toda rota declara o que levanta | `@raises(...)` abaixo do decorator de rota | `openapi-errors` + `tests/unit/architecture/test_openapi_errors.py` |
| Testes | `Cenário:`/`Esperado:`, que conferem algo; `tests/{unit,integration}/<área>/` | `TST001`–`TST004` |
| Guards na própria suíte | `tests/unit/architecture/` | `test_layers.py`, `test_openapi_errors.py`, `test_schemas.py` |
| Avisos falham desde o primeiro commit | `layers_strict = true` em `[tool.tempest]` | `tempest check` |

Os códigos `LAY`, `STR`, `IMP`, `INIT`, `SIZE`, `DI`, `RTR003`–`RTR006` e `TST` são
**avisos**: só derrubam o `tempest check` com `layers_strict = true`, que o
`pyproject.toml` gerado já liga. A tabela de todos os códigos, com a saída
real de cada um, está em
[CLI › Regras de camada](recipes/cli/lint-layers.md).

!!! note "O que não tem gate"
    Singleton de recurso, `core/constants` e `db/configs` são
    convenção: o scaffold nasce assim e a receita do `CLAUDE.md` ensina
    assim, mas nenhuma regra falha se você desviar. Ficaram de fora porque
    a checagem precisaria julgar intenção (o que é "prosa", o que é
    "recurso de processo"), e regra larga demais acusa código correto.

    A base do controller e o fluxo de auth também ficaram sem gate. O
    `Controller` não tem método nenhum, então herdar dele não muda o que
    roda; o que a convenção protege — controller recebe service, nunca
    sessão nem outro controller — já é o `DI001`/`DI002`. E uma rota
    `/login` escrita à mão não se distingue, lendo a sintaxe, de uma que
    tem motivo para existir (um proxy para outro provedor): o gate
    precisaria julgar intenção de novo.

## `__init__.py` só reexporta

**Por quê:** o `__init__.py` roda no import de qualquer módulo do pacote.
Uma função, um router composto ou uma factory ali dentro roda para quem só
queria um schema, fica fora do índice de arquivos (ninguém procura
`build_admin_router` em `__init__.py`) e abre a porta para ciclo de import.
O pacote guarda docstring, reexport relativo e `__all__` — mais nada.

❌ O pacote monta o router:

```python
# src/api/routers/admin/__init__.py
from fastapi import APIRouter

from .orders import router as orders_router

admin_router: APIRouter = APIRouter(prefix="/admin", tags=["admin"])
admin_router.include_router(orders_router)
```

✅ O `router.py` monta, o `__init__.py` reexporta, e o `build_api_router`
inclui `build_admin_router()`:

```python
# src/api/routers/admin/router.py
from tempest_fastapi_sdk import TempestAPIRouter

from .orders import router as orders_router


def build_admin_router() -> TempestAPIRouter:
    """Assemble the admin routers under ``/admin``.

    Returns:
        TempestAPIRouter: ``/admin`` with every admin router included.
    """
    admin: TempestAPIRouter = TempestAPIRouter(prefix="/admin", tags=["admin"])
    admin.include_router(orders_router)
    return admin
```

```python
# src/api/routers/admin/__init__.py
from .router import build_admin_router as build_admin_router

__all__: list[str] = ["build_admin_router"]
```

Factory vai para `factory.py` (`src/integrations/payment/factory.py`), a
orquestração de seed para `runner.py` (`src/db/seed/runner.py`), cada um
reexportado do mesmo jeito. O `INIT001` acusa cada statement que faz mais
que reexportar — `def`, `class`, chamada, constante —, no código e na suíte;
`if TYPE_CHECKING:` e import opcional em `try` passam quando só importam.

## Autenticação: monte a do SDK, não escreva a sua

**Por quê:** cadastro, ativação, login, refresh, logout, `/me`, troca de
senha e de e-mail já existem no SDK, com a política de senha, o 409 de
e-mail duplicado, a revogação de sessão e o par JWT acertados. Um
`AuthService` escrito à mão reconstrói cada um desses e passa a driftar
uma release por vez.

O serviço compõe as peças, todas exportadas por `tempest_fastapi_sdk`:
`BaseUserModel` (a base do `UserModel`), `make_user_token_model(...)` e
`make_user_refresh_token_model(...)` (as tabelas de token), `AuthSettings`
(no `Settings`, ao lado do `JWTSettings`), `UserAuthService`,
`make_auth_router(...)` e, nas rotas do próprio serviço,
`UserAuthService.current_user_dependency(...)` ou
`make_jwt_user_dependency`.

❌ Rotas de auth escritas à mão:

```python
# src/api/routers/auth.py
from fastapi import APIRouter, Depends

from src.api.dependencies import get_auth_controller
from src.controllers import AuthController
from src.schemas import LoginSchema, TokenPairSchema

router: APIRouter = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(
    data: LoginSchema,
    controller: AuthController = Depends(get_auth_controller),
) -> TokenPairSchema:
    """Log a user in by email and password."""
    return await controller.login(data)
```

✅ O service sai do composition root e o router é montado no `create_app`,
como os outros routers do SDK:

```python
# src/scripts/composition.py
from tempest_fastapi_sdk import AsyncDatabaseManager, UserAuthService

from src.core import Settings
from src.db.models import UserModel, UserTokenModel


def build_auth_service(db: AsyncDatabaseManager, settings: Settings) -> UserAuthService:
    """Assemble the SDK's auth service over this project's tables.

    Args:
        db (AsyncDatabaseManager): The process-wide database manager.
        settings (Settings): Settings composing ``AuthSettings`` and
            ``JWTSettings``.

    Returns:
        UserAuthService: The service ``make_auth_router`` serves.
    """
    return UserAuthService(
        db=db,
        user_model=UserModel,
        token_model=UserTokenModel,
        auth_settings=settings,
        jwt_settings=settings,
        email=None,
    )
```

```python
# src/api/app.py
from fastapi import FastAPI
from tempest_fastapi_sdk import make_auth_router

from src.api.dependencies import db, get_session
from src.core import settings
from src.scripts import build_auth_service


def create_app() -> FastAPI:
    """Build the application with the SDK's auth routes under ``/api/auth``.

    Returns:
        FastAPI: The configured application.
    """
    app = FastAPI()
    app.include_router(
        make_auth_router(
            build_auth_service(db, settings),
            session_factory=get_session,
            prefix="/api/auth",
        ),
    )
    return app
```

Para estender sem reescrever rota: `signup_schema=` (subclasse de
`SignupSchema`; campo que é coluna entra na linha antes do insert),
`on_signup=` (hook `(session, user, payload)` dentro da transação do
insert) e `me_response_model=` (subclasse de `AuthUserSchema`, devolvida
pelo `GET /auth/me`). Os três aparecem juntos num exemplo completo na
[receita de auth](recipes/auth-flow/endpoints.md#conta-que-nasce-com-mais-que-e-mail-e-senha-v02780).

!!! warning "O prefixo inteiro vai no `prefix=`, com o router montado no app"
    O `make_auth_router` escreve o próprio prefixo no `Path` do cookie de
    refresh e nas URLs que ele monta, e não enxerga prefixo posto em volta
    dele. Medido com `AUTH_TOKEN_DELIVERY=cookie`, login seguido de
    `POST /api/auth/refresh` com o cookie jar do cliente:

    | `make_auth_router(...)` | `POST /api/auth/login` | cookie de refresh | `POST /api/auth/refresh` |
    | --- | --- | --- | --- |
    | `/auth` default, incluído num `APIRouter(prefix="/api")` | `200` | `Path=/auth` | `401` |
    | `prefix="/api/auth"`, `app.include_router(...)` | `200` | `Path=/api/auth` | `200` |

    Incluído dentro do `build_api_router` com o `/auth` default, a rota
    responde em `/api/auth/login`, mas o cookie sai com `Path=/auth` e o
    browser nunca o manda para `/api/auth/refresh`. Montar no app com
    `prefix="/api/auth"` não aciona o `RTR004`, que só lê o `prefix=` de
    `APIRouter(...)` e de `include_router(...)` — medido com
    `tempest lint-layers` sobre um projeto do `tempest new --extras auth`
    com essa montagem no `create_app`.

## Exceções: um módulo por status, contrato no corpo

**Por quê:** o par status + `code` é o que o cliente lê para decidir o que
mostrar, e a mensagem é o que o usuário vê. Quando a classe declara os
três no corpo, quem lê a classe **sem instanciar** — o `error_responses`
que documenta o OpenAPI, o `BaseRepository` que levanta o 404, o
`tempest openapi-errors` — vê o mesmo contrato que o cliente recebe. Um
`__init__` que monta a mensagem esconde isso, e obriga cada `raise` a
lembrar dos argumentos.

O módulo diz o status, então quem procura "o que devolve 409" abre
`conflict.py`:

```python
# src/core/exceptions/not_found.py
from typing import Any, ClassVar

from tempest_fastapi_sdk import NotFoundException

from src.core.constants.messages import errors


class ItemNotFoundException(NotFoundException):
    """Raised when an item id matches no stored record."""

    code = "ITEM_NOT_FOUND"
    message = errors.ITEM_NOT_FOUND
    field = "item_id"
    details_example: ClassVar[dict[str, Any]] = {"item_id": "3f2b8c1e"}
```

- `code` é o identificador estável. Sem ele a classe herda o genérico do
  SDK (`"NOT_FOUND"`), e o SDK avisa com `InheritedErrorCodeWarning`.
- `message` vem de uma constante, nunca de f-string.
- `field` nomeia o input culpado. Em credencial inválida, deixe sem: dizer
  qual metade do par errou ajuda quem está tentando.
- O dado que muda a cada `raise` vai em `details=`:

```python
from src.core.exceptions import ItemNotFoundException


def require_item(item_id: str, found: bool) -> None:
    """Refuse a lookup that missed.

    Args:
        item_id (str): The id that was looked up.
        found (bool): Whether the lookup matched a row.

    Raises:
        ItemNotFoundException: When ``found`` is false.
    """
    if not found:
        raise ItemNotFoundException(details={"item_id": item_id})
```

### O contrato congelado

O `tests/unit/core/test_exceptions_contract.py` gerado guarda, em
literais, o status, o `code`, a mensagem e o `field` de cada classe que
`src.core.exceptions` reexporta — e falha quando uma classe nova entra sem
entrada. Medido: com uma `OrderAlreadyPaidException` reexportada e sem
linha no contrato, o teste falha assim:

```text
E       AssertionError: assert {'ItemNotFoun...aidException'} == {'ItemNotFoundException'}
E         Extra items in the left set:
E         'OrderAlreadyPaidException'
1 failed, 2 passed
```

Renomear, mover ou reescrever uma exceção passa a ser uma mudança visível
no diff desse arquivo — que é uma mudança de API.

## Mensagens e constantes fora do módulo que usa

**Por quê:** string de mensagem espalhada por service e exceção é o
primeiro lugar onde a IA (e a gente) duplica texto. Com uma constante por
`code`, mudar a redação é uma linha, e o leitor vê de onde o texto veio:

```python
# src/core/constants/messages/errors.py
ITEM_NOT_FOUND: str = "Item not found."
"""Message of ``ItemNotFoundException``."""

__all__: list[str] = ["ITEM_NOT_FOUND"]
```

Importe o **módulo**, não a constante —
`from src.core.constants.messages import errors` — para o uso ler
`errors.ITEM_NOT_FOUND`. Constante que não é texto de cliente vai em
`core/constants/<domínio>.py`, reexportada pelo pacote. Enum vai em
`core/enums/<domínio>.py`; `Protocol`, alias e `NamedTuple` de resultado,
em `core/types/<domínio>.py` — nunca ao lado da classe num módulo de
camada, que guarda só ela (`LAY001`).

## Banco: `db/configs`

**Por quê:** a configuração do engine e a migração de boot são decisões
que valem para o processo inteiro, e cada uma tem **um** lugar:

```python
# src/db/configs/connection.py
from tempest_fastapi_sdk import AsyncDatabaseManager, DatabaseSettings


def make_db_manager(settings: DatabaseSettings) -> AsyncDatabaseManager:
    """Build the database manager for the given settings.

    Args:
        settings (DatabaseSettings): The project's settings.

    Returns:
        AsyncDatabaseManager: A manager with the bind parameters out of
        the error messages.
    """
    return AsyncDatabaseManager(**settings.database_kwargs(), hide_parameters=True)
```

`hide_parameters=True` tira os bind parameters do texto de toda exceção do
SQLAlchemy. Sem ele, um `IntegrityError` logado escreve
`[parameters: (...)]` com o e-mail, o documento ou o hash da senha da
linha — o mesmo defeito que a #718 achou no `tempest user create`.

O `db/configs/migrations.py` tem o `run_migrations`, que o lifespan chama
enquanto `DATABASE_MIGRATE_ON_STARTUP` está ligado (o default). Ele usa o
`AlembicHelper.sync_schema_async`, que cobre banco vazio, banco criado
antes do Alembic e banco já migrado, e recusa migration pendente que
apaga tabela, coluna ou constraint. Desligue quando várias réplicas sobem
juntas e um job roda `tempest db upgrade`.

!!! info "Nenhum dos dois importa o projeto"
    `src.db.models` importa `src.db.configs` pelos nomes de tabela. Se o
    `connection.py` importasse `src.core`, carregar os models (o que o
    Alembic faz em toda revisão) construiria as settings. Por isso
    `make_db_manager` recebe as settings por parâmetro, tipado com o mixin
    `DatabaseSettings` do SDK, e `run_migrations` recebe a URL.

## Texto do OpenAPI fora do router

**Por quê:** o router é fiação — método, caminho, `Depends`, `responses=`.
A prosa que o Swagger mostra muda por outros motivos, e misturada no
decorator ela some no diff da fiação. O `api/docs/<router>.py` guarda um
`EndpointDoc` por handler, em `UPPER_SNAKE`:

```python
# src/api/docs/base.py
from typing import TypedDict


class EndpointDoc(TypedDict):
    """Summary and description of one endpoint in the OpenAPI document."""

    summary: str
    description: str
```

```python
# src/api/docs/service_info.py
from .base import EndpointDoc

INDEX: EndpointDoc = {
    "summary": "Describe the service",
    "description": (
        "Returns the name the service runs under, read from the TITLE "
        "setting. Public, no authentication."
    ),
}
```

E o router espalha o dicionário no decorator:

```python
# src/api/routers/service_info.py
from fastapi import Depends
from tempest_fastapi_sdk import TempestAPIRouter

from src.api.dependencies import get_service_info_controller
from src.api.docs import service_info
from src.controllers import ServiceInfoController
from src.schemas import ServiceInfoSchema

router: TempestAPIRouter = TempestAPIRouter(tags=["root"])


@router.get("/", **service_info.INDEX)
async def index(
    controller: ServiceInfoController = Depends(get_service_info_controller),
) -> ServiceInfoSchema:
    """Placeholder root endpoint returning the service name.

    Args:
        controller (ServiceInfoController): The injected controller.

    Returns:
        ServiceInfoSchema: The service title.
    """
    return await controller.describe()
```

As duas chaves são os parâmetros `summary` e `description` de
`APIRouter.get`/`post`, então o mypy confere o espalhamento.

O formato errado é o que aparece quando o texto fica no router: constantes
`SUMMARY_*` soltas no topo do módulo, ou `summary="..."` escrito no
decorator. O `tempest check` acusa cada palavra-chave como `RTR006` — e,
no mesmo arquivo, o `fastapi.APIRouter` como `RTR005` (veja a próxima
seção):

```python
# src/api/routers/service_info.py
from fastapi import APIRouter, Depends

from src.api.dependencies import get_service_info_controller
from src.controllers import ServiceInfoController
from src.schemas import ServiceInfoSchema

SUMMARY_INDEX: str = "Describe the service"

router: APIRouter = APIRouter(tags=["root"])


@router.get("/", summary=SUMMARY_INDEX, description="Returns the service name.")
async def index(
    controller: ServiceInfoController = Depends(get_service_info_controller),
) -> ServiceInfoSchema:
    """Placeholder root endpoint returning the service name.

    Args:
        controller (ServiceInfoController): The injected controller.

    Returns:
        ServiceInfoSchema: The service title.
    """
    return await controller.describe()
```

```text
$ tempest lint-layers . --rule RTR005 --rule RTR006
src/api/routers/service_info.py:10: RTR005 (warning) APIRouter(...) builds this router (`router`); build it with `TempestAPIRouter` from tempest_fastapi_sdk, which turns each handler's `@raises(...)` into its `responses=` (a plain APIRouter ignores the tag).
src/api/routers/service_info.py:13: RTR006 (warning) route `index` passes `summary=` in its decorator; keep the OpenAPI text in an `EndpointDoc` constant of api/docs/<router>.py and spread it into the decorator (`**docs.INDEX`).
src/api/routers/service_info.py:13: RTR006 (warning) route `index` passes `description=` in its decorator; keep the OpenAPI text in an `EndpointDoc` constant of api/docs/<router>.py and spread it into the decorator (`**docs.INDEX`).
3 layer finding(s): 0 error(s), 3 warning(s).
```

Mesmo texto, outro lugar: mova cada par para um `EndpointDoc` em
`api/docs/service_info.py` e espalhe no decorator, como acima.

??? info "Por que `EndpointDoc` mora no template e não no SDK"
    O SDK já tem o `RouteDoc` (em `tempest_fastapi_sdk.api.route_docs`),
    mas ele é o catálogo bilíngue das rotas **do próprio SDK**: aceita
    `None` para cair no nome da função, e é lido por chave. O
    `EndpointDoc` do serviço é um `TypedDict` de dois campos obrigatórios,
    espalhado direto no decorator. Publicá-lo no SDK seria superfície
    pública para um tipo de duas linhas que cada serviço lê do próprio
    `api/docs` — o tipo de wrapper que a gente prefere não depender.

## Erros da rota: `TempestAPIRouter` + `@raises`

**Por quê:** o cliente precisa saber, pelo OpenAPI, quais erros uma rota
devolve — com o `code` de cada um. A lista mora ao lado do handler, num
`@raises(...)` logo **abaixo** do decorator de rota, e o
`TempestAPIRouter` do SDK a transforma no `responses=` da rota. Todo
router de domínio é um `TempestAPIRouter`, nunca um `fastapi.APIRouter`:

```python
# src/api/routers/product.py
from fastapi import Depends, status
from tempest_fastapi_sdk import TempestAPIRouter, raises

from src.api.dependencies import get_product_controller
from src.api.docs import product as product_docs
from src.controllers import ProductController
from src.core.exceptions import ProductNameTakenException, ProductSkuTakenException
from src.schemas import ProductCreateSchema, ProductResponseSchema

router: TempestAPIRouter = TempestAPIRouter(prefix="/products", tags=["products"])


@router.post("", status_code=status.HTTP_201_CREATED, **product_docs.CREATE_PRODUCT)
@raises(ProductNameTakenException, ProductSkuTakenException)
async def create_product(
    data: ProductCreateSchema,
    controller: ProductController = Depends(get_product_controller),
) -> ProductResponseSchema:
    """Create a product.

    Args:
        data (ProductCreateSchema): The creation payload.
        controller (ProductController): The injected controller.

    Returns:
        ProductResponseSchema: The created product.
    """
    return await controller.create(data)
```

O `409` dessa rota sai assim em `app.openapi()["paths"]["/api/products"]["post"]["responses"]`
(medido com `fastapi` 0.165.0, o piso do SDK, e as duas exceções com
`code`, `message` e docstring no corpo da classe):

```json
{
  "description": "PRODUCT_NAME_TAKEN | PRODUCT_SKU_TAKEN",
  "content": {
    "application/json": {
      "schema": {
        "$ref": "#/components/schemas/ErrorResponseSchema"
      },
      "examples": {
        "PRODUCT_NAME_TAKEN": {
          "summary": "Name in use.",
          "value": {
            "detail": "A product with this name already exists.",
            "code": "PRODUCT_NAME_TAKEN",
            "details": {},
            "field": "name"
          }
        },
        "PRODUCT_SKU_TAKEN": {
          "summary": "SKU in use.",
          "value": {
            "detail": "A product with this SKU already exists.",
            "code": "PRODUCT_SKU_TAKEN",
            "details": {}
          }
        }
      }
    }
  }
}
```

O `ErrorResponseSchema` entra em `components.schemas` e a resposta o
referencia por `$ref`; as exceções do mesmo status viram um exemplo cada,
chaveado pelo `code`.

Três detalhes, todos medidos no mesmo app:

- **Num `fastapi.APIRouter`, o `@raises` não faz nada.** A tag fica no
  handler, ninguém a lê, e a rota sai só com `200` e `422` — sem erro e sem
  aviso. Por isso o `tempest check` acusa um `APIRouter` em `routers/` como
  `RTR005`.
- **`responses=` explícito vale para sobrescrever um status.** A entrada
  escrita à mão substitui a gerada para aquele status code **inteira** —
  some o `$ref` e somem os exemplos daquele status. Use só quando quiser
  mesmo outro texto para um código.
- **O agregador continua `APIRouter`.** As respostas são montadas pelo
  router em que o handler é registrado, e o `include_router` as leva para
  cima sem mudar nada. Um `TempestAPIRouter` no `build_api_router` não
  acrescenta nada, e um `APIRouter` de domínio incluído num agregador
  `TempestAPIRouter` continua sem os erros.

O `tempest check` (passo `openapi-errors`) lê o `@raises` como declaração
da rota: exceção que o fluxo alcança e não está na lista falha o gate. A
receita completa, com catálogo de mensagens e `error_responses` para
routers que não são seus, está em
[Erros no OpenAPI](recipes/openapi-errors.md).

## Guards na própria suíte

**Por quê:** o `tempest check` já roda `lint-layers`, `openapi-errors` e
`lint-schemas`, mas nem todo mundo roda o `tempest check` — o editor roda
`pytest`, um hook de pre-commit roda `pytest`. O scaffold escreve os três
gates também como teste, em `tests/unit/architecture/`:

```python
# tests/unit/architecture/test_layers.py
from pathlib import Path

from tempest_fastapi_sdk.cli.layer_lint import lint_layers

PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]


def test_the_layer_rules_report_nothing() -> None:
    """Cenário: as regras de camada sobre o projeto inteiro.

    Esperado: nenhum achado, nem erro nem aviso.
    """
    findings = [finding.render(PROJECT_ROOT) for finding in lint_layers(PROJECT_ROOT)]
    assert findings == []
```

O `test_openapi_errors.py` faz o mesmo com `analyze_paths` sobre `src`, e
o `test_schemas.py` com `assert_schema_metadata(src.schemas)`. O teste de
camadas falha também em **aviso** — o mesmo critério do `layers_strict`.

A suíte é separada por tipo e área: `tests/unit/<área>/` para uma classe
isolada, `tests/integration/<área>/` para uma requisição pelo app, cada
pasta com `__init__.py`.

## Projeto existente: `tempest generate --standard`

O `tempest new` só escreve projeto novo. Um serviço gerado antes desta
versão continua com `core/exceptions.py`, `tests/test_smoke.py` e o
`AlembicHelper` no lifespan — e continua funcionando. Para trazer as
peças do padrão que não colidem com nada, rode na raiz do projeto:

```bash
tempest generate --standard
```

Num serviço com `src/core/exceptions.py` contendo `UserNotFoundError`
(`NotFoundException`) e `UserEmailAlreadyTakenError` (`ConflictException`),
a saída é:

```text
  + src/core/constants/__init__.py
  + src/core/constants/messages/__init__.py
  + src/core/constants/messages/errors.py
  + src/core/constants/messages/successes.py
  + src/core/enums/__init__.py
  + src/core/types/__init__.py
  + tests/unit/__init__.py
  + tests/unit/architecture/__init__.py
  + tests/unit/architecture/test_layers.py
  + tests/unit/architecture/test_openapi_errors.py
  + tests/unit/architecture/test_schemas.py
  + tests/unit/core/__init__.py
  + src/core/exceptions/conflict.py
  + src/core/exceptions/not_found.py
  + src/core/exceptions/__init__.py
  + tests/unit/core/test_exceptions_contract.py
  - src/core/exceptions.py
  > UserEmailAlreadyTakenError -> core/exceptions/conflict.py
  > UserNotFoundError -> core/exceptions/not_found.py
Standard: 16 written, 0 skipped, 2 exception(s) moved.
```

O que acontece:

- **Pacotes e guards que faltam** (`core/constants/`, `core/enums/`,
  `core/types/`, `tests/unit/architecture/`) saem dos mesmos templates do
  `tempest new`. Arquivo que já existe fica como está, a menos que você
  passe `--force`; e um pacote cujo módulo plano já existe
  (`core/constants.py`) **não** é escrito, porque o pacote novo
  esconderia o módulo em silêncio — o relatório diz o motivo.
- **`core/exceptions.py` vira o pacote `core/exceptions/`**, um módulo por
  status. O status de cada classe é lido **importando** o módulo, não
  adivinhado pela base: uma classe que herda de uma base 500 e declara
  `status_code = 409` vai para `conflict.py`. O `__init__.py` reexporta
  tudo, então `from src.core.exceptions import UserNotFoundError` continua
  funcionando, e o arquivo plano é apagado.
- **O contrato é congelado** em `tests/unit/core/test_exceptions_contract.py`
  com o status, o `code`, a mensagem e o `field` que cada classe tem
  hoje. Uma classe que monta a mensagem no `__init__` gera um aviso
  nomeando-a: o contrato falha para ela até o dado ir para o corpo da
  classe e o que muda a cada `raise` ir em `details=`.

!!! warning "A migração recusa antes de escrever"
    Se o `core/exceptions.py` tem algo além de import, classe e `__all__`
    (uma constante ou função de módulo não tem status para onde ir), se
    separar por status faria os módulos importarem uns aos outros, ou se
    o módulo não importa, o comando sai com código 1, a mensagem diz o
    arquivo e a linha, e **nada** é escrito.

Rodar de novo não muda nada: tudo que já existe é pulado. O resto do
padrão (`db/configs`, `scripts/composition.py`, `api/docs/`) mexe na
fiação do app e continua manual — um item por PR, na ordem da
[migração da arquitetura recomendada](recommended-architecture.md#migrando-um-servico-existente).
Ligue o `layers_strict = true` quando os avisos zerarem.

## Recap

- **Comece pelo `tempest new`**; um domínio novo segue a receita do
  `CLAUDE.md` gerado.
- **Exceção:** um módulo por status, `code`/`message`/`field` no corpo,
  sem `__init__`, dado do `raise` em `details=` — e uma linha no contrato
  congelado.
- **Texto de cliente em `core/constants/messages`**; enum, tipo e
  constante em pacotes por domínio.
- **`db/configs`** guarda o `make_db_manager` (`hide_parameters=True`) e o
  `run_migrations` do boot.
- **`api/docs`** guarda o `EndpointDoc` de cada handler.
- **`__init__.py` só reexporta**; composição em `router.py`, factory em
  `factory.py`, seed em `runner.py`. Arquivo sem o sufixo da camada e
  com até 1000 linhas.
- **Auth é a do SDK**: `UserAuthService` + `make_auth_router(..., prefix="/api/auth")`
  montado no app, estendido por `signup_schema`, `on_signup` e
  `me_response_model`.
- **`layers_strict = true`** e os guards em `tests/unit/architecture/`
  fazem o `tempest check` e o `pytest` falharem no primeiro desvio.
