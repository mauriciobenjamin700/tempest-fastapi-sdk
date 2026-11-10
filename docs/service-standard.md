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
| Sem `def` aninhado nem `_x` de módulo, em `src` e `tests` | callback com estado vira objeto chamável | `STR001`, `STR002` |
| `__init__` com import relativo; fora dele, import do pacote | `from .x import Y` / `from src.services import X` | `IMP001`, `IMP002` |
| Recurso é singleton de processo num lugar só | `api/dependencies/resources.py` (`db`, `get_session`, `get_*`) | — (convenção do scaffold) |
| Providers por camada; router nunca vê `AsyncSession` | `api/dependencies/controllers.py` chama os `build_*` | `RTR001`, `DI001`–`DI003` |
| `/api` posto num lugar só | `routers/api.py` → `build_api_router()` | `RTR003`, `RTR004` |
| Texto do OpenAPI fora do router | `api/docs/<router>.py`, um `EndpointDoc` por handler | — (convenção do scaffold) |
| Exceções: um módulo por status, contrato no corpo da classe | `core/exceptions/{bad_request,unauthorized,forbidden,not_found,conflict}.py` | `tests/unit/core/test_exceptions_contract.py` |
| Contrato de exceção congelado | status, `code`, mensagem e `field` de cada classe | `tests/unit/core/test_exceptions_contract.py` |
| Prosa e constante fora do módulo que usa | `core/constants/` + `core/constants/messages/{errors,successes}.py` | — (convenção do scaffold) |
| `Protocol`, alias, `NamedTuple`, enum | `core/types/<domínio>.py`, `core/enums/<domínio>.py` | `LAY001` (fora da camada); `ENUM001` (sufixo) |
| Banco | `db/configs/{connection,migrations,names}.py` | — (convenção do scaffold) |
| Mapeamento no repository | `map_to_response` | `REPO001` (parcial) |
| Grafo montado fora do request | `scripts/composition.py` (`build_*`) | `DI003` (`from_session`) |
| Todo campo de schema documentado | `Field(title=..., description=..., examples=...)` | `lint-schemas` + `tests/unit/architecture/test_schemas.py` |
| Toda rota declara o que levanta | `responses=error_responses(...)` | `openapi-errors` + `tests/unit/architecture/test_openapi_errors.py` |
| Testes | `Cenário:`/`Esperado:`, que conferem algo; `tests/{unit,integration}/<área>/` | `TST001`–`TST004` |
| Guards na própria suíte | `tests/unit/architecture/` | `test_layers.py`, `test_openapi_errors.py`, `test_schemas.py` |
| Avisos falham desde o primeiro commit | `layers_strict = true` em `[tool.tempest]` | `tempest check` |

Os códigos `LAY`, `STR`, `IMP`, `DI`, `RTR003`/`RTR004` e `TST` são
**avisos**: só derrubam o `tempest check` com `layers_strict = true`, que o
`pyproject.toml` gerado já liga. A tabela de todos os códigos, com a saída
real de cada um, está em
[CLI › Regras de camada](recipes/cli.md#regras-de-camada-tempest-lint-layers).

!!! note "O que não tem gate"
    Singleton de recurso, `api/docs`, `core/constants` e `db/configs` são
    convenção: o scaffold nasce assim e a receita do `CLAUDE.md` ensina
    assim, mas nenhuma regra falha se você desviar. Ficaram de fora porque
    a checagem precisaria julgar intenção (o que é "prosa", o que é
    "recurso de processo"), e regra larga demais acusa código correto.

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
from fastapi import APIRouter, Depends

from src.api.dependencies import get_service_info_controller
from src.api.docs import service_info
from src.controllers import ServiceInfoController
from src.schemas import ServiceInfoSchema

router: APIRouter = APIRouter(tags=["root"])


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

??? info "Por que `EndpointDoc` mora no template e não no SDK"
    O SDK já tem o `RouteDoc` (em `tempest_fastapi_sdk.api.route_docs`),
    mas ele é o catálogo bilíngue das rotas **do próprio SDK**: aceita
    `None` para cair no nome da função, e é lido por chave. O
    `EndpointDoc` do serviço é um `TypedDict` de dois campos obrigatórios,
    espalhado direto no decorator. Publicá-lo no SDK seria superfície
    pública para um tipo de duas linhas que cada serviço lê do próprio
    `api/docs` — o tipo de wrapper que a gente prefere não depender.

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

## Projeto existente não muda

O `tempest new` só escreve projeto novo. Um serviço gerado antes desta
versão continua com `core/exceptions.py`, `tests/test_smoke.py` e o
`AlembicHelper` no lifespan — e continua funcionando. Para trazê-lo ao
padrão, ataque um item por PR, na ordem da
[migração da arquitetura recomendada](recommended-architecture.md#migrando-um-servico-existente),
e ligue o `layers_strict = true` quando os avisos zerarem.

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
- **`layers_strict = true`** e os guards em `tests/unit/architecture/`
  fazem o `tempest check` e o `pytest` falharem no primeiro desvio.
