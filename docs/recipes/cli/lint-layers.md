---
description: "Consulte cada regra de arquitetura cobrada por `tempest lint-layers` (router, service, imports, tamanho de arquivo, suíte), com código, aviso ou erro e correção."
---

# Regras de camada: `tempest lint-layers`

Consulte cada regra de arquitetura que o `tempest lint-layers` cobra, com o código e como corrigir.

Parte da receita [CLI](../cli.md).

O `CLAUDE.md` que o `tempest new` escreve diz como um service e um
repository têm forma. Prosa não é gate: um serviço com dez services sem
`BaseService`, repositories embrulhando o `list` do SDK e funções soltas
num `services/feeds.py` passou `ruff`, `mypy` e `pytest` limpos (#520).
O `tempest lint-layers` é o gate dessas regras (e da convenção de nome de enum) — e o `tempest check` o roda
como passo próprio, depois do `mypy` e antes do `pytest`:

```bash
tempest lint-layers            # o projeto do diretório atual (lê src/ ou app/)
tempest lint-layers src        # o diretório de código direto
```

Ele lê o código com `ast` e **nunca importa o projeto**, então roda mesmo
com import quebrado. Cada violação é uma linha `arquivo:linha: CÓDIGO
mensagem`, e a mensagem diz o que mudar:

| Código | Onde | O que acusa |
| --- | --- | --- |
| `SVC001` | `services/` | classe pública que não herda `BaseService` (direto, via outro service do projeto ou via um `*Service` importado do `tempest_fastapi_sdk`) |
| `SVC002` | `services/` | segunda classe pública no módulo, ou qualquer função de módulo — helper vai para `utils/` |
| `SVC003` | `services/` | `__init__` que só chama `super().__init__` com os próprios parâmetros e/ou guarda `self.repository` |
| `REPO001` | `repositories/` | método cujo corpo é só `return [await] self.<método do BaseRepository>(...)` |
| `ENUM001` | todo o código | classe pública que herda `BaseStrEnum` (direto ou via outra classe do projeto) e cujo nome não termina em `Enum` — a mensagem diz o nome esperado; renomear não pede migration, porque o `enum_type_name` tira um `Enum` final (#518) |
| `RTR001` | `routers/` | chamada a uma classe `*Controller` ou `*Service` (nome com inicial maiúscula) em qualquer ponto do módulo — corpo de rota, função auxiliar, nível de módulo. A construção vai para um provider em `api/dependencies/controllers.py` e chega à rota por `Depends` |
| `RTR002` | `routers/` | rota (`@<router>.get` / `post` / `put` / `patch` / `delete` / `head` / `options` / `trace` / `api_route`) cuja anotação de retorno ou `response_model=` cita um model ORM, em qualquer profundidade (`list[ProductModel]`, `Optional[ProductModel]`, união com `None`) |

Os três casos do #520, num projeto gerado pelo `tempest new`, saem assim
no `tempest check` (trecho da saída real; exit code `1`):

```text
$ mypy --disallow-untyped-defs --disallow-incomplete-defs .
Success: no issues found in 29 source files
$ lint-layers
src/db/repositories/news.py:18: REPO001 `NewsRepository.list_all` only forwards to BaseRepository.list; delete it and call `repository.list(...)` with those arguments at the call site.
src/services/feeds.py:9: SVC002 module-level function `page_envelope` in services/; move it to utils/, or make it a method of the service that uses it.
src/services/feeds.py:24: SVC001 `NewsService` does not inherit BaseService; subclass `BaseService[Repository, ResponseSchema]` and drop what it reimplements. A service with no repository opts out with `# tempest: no-base-service` on the class line; a type that is not a service belongs in schemas/ or core/.
src/services/feeds.py:27: SVC003 `NewsService.__init__` only stores the repository, which BaseService already does; delete it.
src/services/feeds.py:37: SVC002 `TrendService` shares this module with `NewsService`; one public class per module in services/ — move it to its own file.
14 layer finding(s).
```

O projeto como o `tempest new` gera passa limpo, e continua limpo com os
exemplos de `ProductService` / `ProductRepository` / router de produto do
`CLAUDE.md` copiados para dentro dele — o teste da suíte faz exatamente
isso.

!!! tip "Service sem repository: opt-out explícito"
    Um service que não tem repository para entregar ao `BaseService` (um
    catálogo estático lido de arquivo, uma fachada sobre API externa)
    marca a linha do `class` com `# tempest: no-base-service`. O marcador
    tira a classe do `SVC001` e do `SVC003`; o `SVC002` continua valendo.

    ```python
    # src/services/catalogue.py
    import json
    from pathlib import Path


    class CatalogueService:  # tempest: no-base-service
        """Catálogo estático lido de um JSON — não há repository."""

        def __init__(self, path: Path) -> None:
            self.items: list[dict[str, str]] = json.loads(path.read_text())
    ```

    É um comentário, não um atributo de classe, porque é uma instrução para
    o checker e nada em runtime lê — o mesmo motivo de `# noqa` ser
    comentário.

!!! info "O que o `REPO001` deixa passar de propósito"
    Um método que **sobrescreve** um nome do `BaseRepository` (`delete`
    encaminhando para `soft_delete`) é política, não wrapper: quem chama
    `service.delete` passa a apagar logicamente. E um método que chama
    outro método definido na própria classe não é wrapper do SDK.

## Router: controller por `Depends`, resposta em schema

As duas regras de `routers/` cobrem os desvios que mais aparecem num
router: montar o controller na mão dentro da rota e devolver a linha ORM.
Este router, escrito num projeto do `tempest new`, faz os dois:

```python
# src/api/routers/product.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.resources import get_session
from src.controllers import ProductController
from src.db.models import ProductModel
from src.db.repositories import ProductRepository
from src.services import ProductService

router: APIRouter = APIRouter(prefix="/products", tags=["products"])


@router.get("/{product_id}")
async def get_product(
    product_id: int,
    session: AsyncSession = Depends(get_session),
) -> ProductModel:
    """Read a product."""
    controller = ProductController(ProductService(ProductRepository(session)))
    return await controller.get_by_id(product_id)
```

O `tempest lint-layers` sai com exit code `1` e três linhas (saída real):

```text
src/api/routers/product.py:15: RTR002 route `get_product` returns the ORM model `ProductModel` (return annotation); return the response schema the repository maps it to (`map_to_response`) and annotate the route with that schema.
src/api/routers/product.py:20: RTR001 `ProductController(...)` is built inside a router; move the construction to a provider in api/dependencies/controllers.py and receive it in the route as `Depends(get_...)`. A call that has to stay opts out with `# tempest: router-builds` on its line.
src/api/routers/product.py:20: RTR001 `ProductService(...)` is built inside a router; move the construction to a provider in api/dependencies/services.py and receive it in the route as `Depends(get_...)`. A call that has to stay opts out with `# tempest: router-builds` on its line.
3 layer finding(s).
```

A correção é a forma que o `CLAUDE.md` do projeto ensina: o provider
`get_product_controller` em `src/api/dependencies/controllers.py` monta a
cadeia, a rota recebe `controller: ProductController =
Depends(get_product_controller)` e anota o retorno com
`ProductResponseSchema`.

**O que conta como model ORM.** Um nome importado de um módulo cujo
caminho tem o segmento `models` (`src.db.models`, `src.db.models.user`),
ou uma classe pública definida num diretório `models/` do projeto — desde
que a rota não a importe de um módulo `schemas`. Um schema que por acaso
tem o mesmo nome de um model continua limpo.

!!! tip "Construção que precisa ficar no router: `# tempest: router-builds`"
    O marcador é a exceção que você assume por escrito, para a review ler
    o motivo. Ele vale para as chamadas daquela linha, não para o módulo
    inteiro:

    ```python
    # src/api/routers/reports.py
    from fastapi import APIRouter

    from src.services import ReportService

    report_service: ReportService = ReportService()  # tempest: router-builds
    router: APIRouter = APIRouter(prefix="/reports")
    ```

    O `RTR002` não tem opt-out: a correção tem uma forma só — devolver o
    schema que o `map_to_response` do repository produz.

!!! warning "O que as regras de router não veem"
    Elas leem sintaxe e nunca importam o projeto, então ficam de fora:
    rota sem anotação de retorno e sem `response_model=`; model guardado
    fora de qualquer `models/` e importado de módulo sem esse segmento; as
    bases ORM do próprio SDK (`BaseUserModel`) usadas direto na rota;
    controller montado por alias (`factory = ProductController; factory()`)
    ou classe cujo nome não termina em `Controller` / `Service`.

    **Onde o `@requires` fica não é regra.** A receita de
    [guards de permissão](../permission-guards.md) aplica o `@requires` na
    rota como primeira forma, e o próprio `requires` documenta handler de
    rota, método de controller e de service como lugares válidos. Acusar o
    `@requires` na rota acusaria a documentação do SDK; a escolha entre os
    dois lugares é revisão — o critério está em
    [Arquitetura › Guards](../../architecture.md#guards-dependencia-no-router-ou-requires-no-metodo).

!!! note "Requer `tempest-cli>=0.7.0`"
    O passo entra no `tempest check` pelo `extra_steps` do
    `register_commands` (`tempest-cli` 0.5.0), e o `--rule` / `--strict` do
    `check` chega a ele pelo `run_with_options` (`tempest-cli` 0.7.0); o
    extra `[cli]` declara `tempest-cli>=0.7.0`, então ele já vem. O
    `tempest-cli check` sozinho, sem o SDK, não conhece estas regras: elas são
    do SDK, e ali o `--rule` sai com exit code `2`.

## Camada em pacote de contexto (aviso)

Quatro regras cobram a forma das quatro camadas — `controllers/`,
`services/`, `repositories/` e `models/`. Entram como **aviso**:

| Código | Onde | O que acusa |
| --- | --- | --- |
| `LAY001` | `controllers/`, `repositories/`, `models/` | segunda classe no módulo (qualquer nome, privada também) ou função de módulo. Em `services/` quem acusa é o `SVC002`, como erro |
| `LAY002` | as quatro camadas | módulo direto na raiz da camada: `models/route.py` vai para `models/<contexto>/route.py`. Qualquer nome de contexto vale; `__init__.py` fica de fora |
| `LAY003` | as quatro camadas | nome de arquivo que repete a camada: `services/orders/checkout_service.py` vira `services/orders/checkout.py`, e a classe dentro continua `CheckoutService`. Vale para `_controller`, `_service`, `_repository` e `_model` |
| `REPO002` | `repositories/` | import de um nome `*Repository` de outro módulo de repository (import absoluto com segmento `repositories`, ou relativo). Nome usado só como base de classe — o repository base do projeto — passa |

Um `models/route.py` com dois models e um repository que importa outro
saem assim (saída real, exit code `0`, porque são só avisos):

```text
src/db/models/route.py:1: LAY002 (warning) models/route.py sits at the root of the layer; move it to models/<context>/route.py (a subpackage per domain context, re-exported by models/__init__.py).
src/db/models/route.py:5: LAY001 (warning) `StopModel` shares this module with `RouteModel`; one class per module in models/ — move it to its own file in the same context package.
src/db/repositories/orders/order.py:1: REPO002 (warning) imports `CustomerRepository` from another repository; a repository reads its own tables (join in the query) and a service combines two repositories.
3 layer finding(s): 0 error(s), 3 warning(s).
```

!!! check "O projeto do `tempest new` passa em todas"
    O scaffold já sai em pacotes de contexto (`controllers/system/`,
    `db/models/users/`), com `__init__` relativo, `/api` só no
    `build_api_router` e testes com `Cenário:`/`Esperado:`: o
    `tempest lint-layers` não acusa nada, nem aviso (medido sem extras, com
    `auth,queue,tasks,ssr` e com `admin,email,minio,cache,tasks-redis`; o
    `tests/cli/test_layer_lint.py` fixa os três).

## O que service e controller recebem (aviso)

| Código | Onde | O que acusa |
| --- | --- | --- |
| `DI001` | `services/`, `controllers/` | parâmetro do `__init__` anotado com `AsyncSession` (em qualquer profundidade: união com `None`, anotação string). A sessão chega ao repository; o service recebe o repository |
| `DI002` | `services/`, `controllers/` | `__init__` de service recebendo um `*Service`, ou de controller recebendo um `*Controller`. Juntar dois services é trabalho do controller |
| `DI003` | todo o código | função ou método chamado `from_session`. A montagem fica no provider de `api/dependencies/` (dentro da requisição) ou numa função `build_*` do composition root (fora dela) |

Este service tem as três formas (`tempest lint-layers --rule DI001 --rule
DI002 --rule DI003`, saída real):

```python
# src/services/wallet/settlement.py
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.wallet.wallet import WalletService


class SettlementService:
    """Liquida o saldo retido."""

    def __init__(self, session: AsyncSession, wallet_service: WalletService) -> None:
        """Guarda a sessão e o service da carteira."""
        self.session = session
        self.wallet_service = wallet_service

    @classmethod
    def from_session(cls, session: AsyncSession) -> "SettlementService":
        """Monta o service sobre uma sessão."""
        return cls(session, WalletService(session))
```

```text
src/services/wallet/settlement.py:9: DI001 (warning) `SettlementService.__init__` takes the session (`session`); take the repository instead and let the provider in api/dependencies/ build it from the session.
src/services/wallet/settlement.py:9: DI002 (warning) `SettlementService.__init__` takes `WalletService` (`wallet_service`), a service of the same layer; combine the two one layer up, or declare the exception with its reason.
src/services/wallet/settlement.py:15: DI003 (warning) `from_session` assembles the graph inside the class; build it in a provider of api/dependencies/ for a request, or in a `build_*` function of the composition root outside one.
3 layer finding(s): 0 error(s), 3 warning(s).
```

Quando dois services precisam mesmo andar juntos — no transport, a
liquidação e o crédito na carteira tinham de cair na mesma transação —, o
caminho é a exceção declarada, mirando só aquele parâmetro (para a carteira
do SDK, desde a v0.330.0 o service recebe o
[`WalletLedger`](../wallet.md#o-ledger-nao-o-service-walletledger) e a exceção
deixa de ser necessária):

```python
ARCHITECTURE_EXCEPTIONS: dict[str, str] = {
    "DI002 src/services/wallet/settlement.py::SettlementService.wallet_service": (
        "a liquidação e o crédito caem na mesma transação"
    ),
}
```

!!! tip "Montagem fora da requisição"
    Task, consumer, agente e seed não têm `Depends`. Em vez de cada classe
    ganhar um `from_session`, junte as funções `build_*` num composition
    root e reutilize-o em todos esses lugares e na suíte. A receita de
    [agentes com banco](../agents-db.md) mostra a forma.

## Função aninhada e helper privado (aviso)

Estas duas regras leem o código **e a suíte** (`tests/` ao lado do
`src/`). No transport, a maior parte das ocorrências estava nos testes.

| Código | O que acusa |
| --- | --- |
| `STR001` | função definida dentro de outra função — inclusive dentro de método ou de classe local. Callback que guarda estado vira classe com `__call__`; o que só fixa um argumento vira `functools.partial` sobre uma função de módulo |
| `STR002` | função de módulo cujo nome começa com um `_` só (`__getattr__` e métodos passam). Vira método da classe que a usa, função pública em `utils/` ou, na suíte, fixture ou módulo de helper público |

Com estes dois arquivos (saída real):

```python
# src/utils/retry.py
from collections.abc import Awaitable, Callable


def with_retry(attempts: int) -> Callable[[], Awaitable[None]]:
    """Repete a chamada até dar certo."""

    async def wrapper() -> None:
        """Tenta de novo."""

    return wrapper
```

```python
# tests/test_app.py
def _make_payload() -> dict[str, str]:
    """Monta o corpo da requisição."""
    return {"name": "Picos"}
```

```text
src/utils/retry.py:7: STR001 (warning) `with_retry.wrapper` is defined inside a function; a callback that keeps state becomes a class with `__call__`, and one that binds an argument goes through functools.partial over a module-level function.
tests/test_app.py:1: STR002 (warning) module-level `_make_payload` is a helper with no home; make it a method of the class that uses it or a public function in utils/ (in tests: a fixture or a public helper module).
2 layer finding(s): 0 error(s), 2 warning(s).
```

## Import entre pacotes do projeto (aviso)

| Código | O que acusa |
| --- | --- |
| `IMP001` | `__init__.py` importando módulo do próprio pacote com import absoluto. Vizinho se importa relativo: `from .orders import OrderService` |
| `IMP002` | fora de `__init__.py`, import de um nome pelo submódulo onde ele mora quando um pacote acima já o reexporta. O reexport é resolvido de forma transitiva, inclusive por `from .x import *`, e a sugestão é o pacote mais alto que reexporta o nome **sem conter** o módulo que importa — um service nunca é mandado importar o `__init__` da própria camada |

Os dois leem o código e a suíte. Com estes quatro arquivos
(`tempest lint-layers --rule IMP001 --rule IMP002`, saída real):

```python
# src/services/__init__.py
from src.services.orders import OrderService as OrderService

__all__: list[str] = ["OrderService"]
```

```python
# src/services/orders/__init__.py
from .order import OrderService as OrderService

__all__: list[str] = ["OrderService"]
```

```python
# src/services/orders/order.py
class OrderService:
    """Pedidos."""
```

```python
# src/controllers/orders/order.py
from src.services.orders.order import OrderService


class OrderController:
    """Orquestra pedidos."""

    def __init__(self, service: OrderService) -> None:
        """Guarda o service."""
        self.service = service
```

```text
src/controllers/orders/order.py:1: IMP002 (warning) `OrderService` is re-exported by `src.services`; import it from there: `from src.services import OrderService`.
src/services/__init__.py:1: IMP001 (warning) `src.services.orders` is a module of this package; import it relatively in __init__.py: `from .orders import ...`.
2 layer finding(s): 0 error(s), 2 warning(s).
```

!!! warning "Quando o pacote fecharia ciclo"
    Às vezes o import pelo pacote da camada fecha um ciclo: no transport,
    task e script importavam `api.dependencies.resources` direto, porque
    importar o pacote `api.dependencies` fecharia ciclo com `src.tasks`. Esse é o caso da exceção
    declarada (`"IMP002 <caminho>::<nome>": "<motivo>"`), não de reescrever o
    import.

## O que um pacote guarda e o tamanho do arquivo (aviso)

| Código | Onde | O que acusa |
| --- | --- | --- |
| `INIT001` | todo `__init__.py`, do código e da suíte | statement que faz mais que reexportar: `def`, `class`, chamada, constante. Passam docstring, import, o `__all__` (inclusive `+=`) e `if`/`try` feitos só disso (`if TYPE_CHECKING:`, import opcional) |
| `SIZE001` | todo módulo, do código e da suíte | arquivo com mais de 1000 linhas, acusado na linha 1001. Não há lista de arquivos isentos |

Um `admin/__init__.py` que compõe os sub-routers, um service com o sufixo da
camada no nome e um arquivo de teste de 1040 linhas saem assim
(`tempest lint-layers --rule INIT001 --rule SIZE001 --rule LAY003`, saída
real, exit code `0`):

```text
src/api/routers/admin/__init__.py:5: INIT001 (warning) `admin_router: APIRouter = APIRouter(prefix="/admin")` does work in __init__.py, which only re-exports (`from .x import Y as Y`) and binds `__all__`; move it to a module of the package (router.py, factory.py, runner.py) and re-export it.
src/api/routers/admin/__init__.py:6: INIT001 (warning) `admin_router.include_router(orders_router)` does work in __init__.py, which only re-exports (`from .x import Y as Y`) and binds `__all__`; move it to a module of the package (router.py, factory.py, runner.py) and re-export it.
src/services/orders/checkout_service.py:1: LAY003 (warning) checkout_service.py repeats the layer its directory already names; rename it to checkout.py — the class inside keeps its suffix.
tests/integration/orders/test_orders.py:1001: SIZE001 (warning) test_orders.py has 1040 lines, over 1000; split it by responsibility — a service into several services, a test file into one file per theme, an __init__.py into a subpackage per domain.
4 layer finding(s): 0 error(s), 4 warning(s).
```

A composição vai para `admin/router.py` (`build_admin_router()`), reexportada
pelo `__init__.py`; uma factory, para `factory.py`; a orquestração de seed,
para `runner.py`. O arquivo grande se quebra por responsabilidade — o
service em vários services, o teste em um arquivo por tema.

## Router: um por arquivo, `/api` uma vez (aviso)

| Código | Onde | O que acusa |
| --- | --- | --- |
| `RTR003` | `routers/` | mais de um router no módulo (`APIRouter(...)`, `TempestAPIRouter(...)` ou subclasse do projeto) — um achado por router depois do primeiro |
| `RTR004` | todo o código | `prefix` igual a `/api` (ou abaixo dele) numa construção de router ou em `include_router(...)` fora de uma função `build_api_router` |

No transport, `/api` estava repetido em 22 `include_router`. Um router de
pedidos com dois routers e o `/api` embutido sai assim (saída real):

```python
# src/api/routers/orders.py
from tempest_fastapi_sdk import TempestAPIRouter

router: TempestAPIRouter = TempestAPIRouter(prefix="/api/orders", tags=["orders"])
admin_router: TempestAPIRouter = TempestAPIRouter(prefix="/api/admin/orders", tags=["admin"])
```

```text
src/api/routers/orders.py:4: RTR004 (warning) prefix `/api/orders` set outside build_api_router(); mount the domain routers under `/api` once, in routers/api.py, and give each router only its own prefix.
src/api/routers/orders.py:5: RTR003 (warning) a second router in this module (`admin_router`); one router per file in routers/ — move it to its own module.
src/api/routers/orders.py:5: RTR004 (warning) prefix `/api/admin/orders` set outside build_api_router(); mount the domain routers under `/api` once, in routers/api.py, and give each router only its own prefix.
3 layer finding(s): 0 error(s), 3 warning(s).
```

A forma que as duas regras pedem: cada domínio num arquivo, só com o próprio
prefixo (`/orders`, `/admin/orders`), e o `/api` posto uma vez:

```python
# src/api/routers/api.py
from fastapi import APIRouter

from src.api.routers.admin_orders import router as admin_orders_router
from src.api.routers.orders import router as orders_router


def build_api_router() -> APIRouter:
    """Monta o router da API: todo domínio fica sob ``/api``."""
    api: APIRouter = APIRouter(prefix="/api")
    api.include_router(orders_router)
    api.include_router(admin_orders_router)
    return api
```

## Router de domínio: `TempestAPIRouter` e texto em `api/docs` (aviso)

| Código | Onde | O que acusa |
| --- | --- | --- |
| `RTR005` | `routers/` | `fastapi.APIRouter(...)` fora de uma função `build_api_router` — num `APIRouter` o `@raises` não vira `responses=` |
| `RTR006` | `routers/` | `summary=` ou `description=` no decorator de uma rota, literal ou constante `SUMMARY_*` — uma linha por palavra-chave |

O agregador do `build_api_router` fica de fora do `RTR005` de propósito: os
erros de uma rota são montados pelo router em que o handler é registrado, e a
classe do router que o inclui não muda nada. O router com as duas formas que
agentes escreviam (saída real):

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
src/api/routers/service_info.py:10: RTR005 (warning) APIRouter(...) builds this router (`router`); build it with `TempestAPIRouter` from tempest_fastapi_sdk, which turns each handler's `@raises(...)` into its `responses=` (a plain APIRouter ignores the tag).
src/api/routers/service_info.py:13: RTR006 (warning) route `index` passes `summary=` in its decorator; keep the OpenAPI text in an `EndpointDoc` constant of api/docs/<router>.py and spread it into the decorator (`**docs.INDEX`).
src/api/routers/service_info.py:13: RTR006 (warning) route `index` passes `description=` in its decorator; keep the OpenAPI text in an `EndpointDoc` constant of api/docs/<router>.py and spread it into the decorator (`**docs.INDEX`).
3 layer finding(s): 0 error(s), 3 warning(s).
```

A forma certa — `TempestAPIRouter`, `@raises` abaixo do decorator e um
`EndpointDoc` em `api/docs/<router>.py` — está em
[Padrão de serviço](../../service-standard.md#erros-da-rota-tempestapirouter-raises).

## A forma da suíte (aviso)

As quatro regras `TST` leem os arquivos que o pytest coleta no `tests/` do
projeto (`test_*.py` e `*_test.py`):

| Código | O que acusa |
| --- | --- |
| `TST001` | teste (`test*` de módulo ou método `test*` de classe `Test*`) cuja docstring não traz `Cenário:` **e** `Esperado:` — a situação que o teste monta e o que ele prova |
| `TST002` | teste que não confere nada: sem `assert`, sem `with pytest.raises` / `warns` / `deprecated_call`, sem chamada `assert*` (`mock.assert_awaited_once()`) |
| `TST003` | `pytest.raises(Exception)` ou `BaseException`, sozinho ou em tupla — qualquer bug passa |
| `TST004` | `parametrize` cujo id leva um valor sorteado no import |

Este arquivo tem as quatro (saída real):

```python
# tests/test_orders.py
from uuid import uuid4

import pytest


@pytest.mark.parametrize("order_id", [str(uuid4())])
def test_order_is_created(order_id: str) -> None:
    """The order is created."""
    with pytest.raises(Exception):
        int(order_id)


def test_receipt_is_sent() -> None:
    """Cenário: um pedido pago.

    Esperado: o recibo sai sem erro.
    """
    str(uuid4())
```

```text
tests/test_orders.py:6: TST004 (warning) parametrize puts a `uuid4()` drawn at import into the test id; each pytest-xdist worker collects different ids and the run aborts — use fixed literals.
tests/test_orders.py:7: TST001 (warning) `test_order_is_created` docstring lacks Cenário:, Esperado:; say the situation the test builds (Cenário:) and what it proves (Esperado:).
tests/test_orders.py:9: TST003 (warning) pytest.raises(Exception) passes for any bug; name the exception the code raises (and `match=` its message when it matters).
tests/test_orders.py:13: TST002 (warning) `test_receipt_is_sent` checks nothing (no assert, no pytest.raises, no assert_* call); a test that cannot fail proves nothing.
4 layer finding(s): 0 error(s), 4 warning(s).
```

!!! info "Quando o valor sorteado quebra o `pytest -n`"
    Cada worker do pytest-xdist importa o módulo por conta própria. Se o id
    do teste depende de um valor sorteado, cada worker coleta ids
    diferentes e a execução aborta com "Different tests were collected".
    Medido com `pytest -n 2`: `random.randint()`, `time.time()`,
    `secrets.token_hex()`, `os.urandom()`, `str(uuid4())`, `uuid4().hex`,
    `f"{uuid4()}"` e `pytest.param(..., id=str(uuid4()))` abortam. Um
    `uuid4()` ou `datetime.now()` **cru** passa — o pytest dá a esses
    objetos o id pela posição (`v0`, `v1`) —, e por isso o `TST004` só os
    acusa quando viram texto ou entram em `ids=` / `id=`.

## Aviso, erro e exceção declarada

Toda regra tem uma severidade. **Erro** derruba o passo (exit code `1`);
**aviso** sai com `(warning)` depois do código e não derruba. Regra nova
entra como aviso numa release e vira erro na seguinte — assim um serviço
existente tem uma release inteira para migrar. Para migrar uma regra por
vez, selecione-a com `--rule` (repetível, aceita minúsculas):

```bash
tempest lint-layers --rule SVC002              # só uma regra
tempest lint-layers --rule SVC002 --rule RTR001
tempest lint-layers --strict                   # aviso também derruba
```

O `tempest check` roda o passo sem `--rule`. Para o gate derrubar em aviso
também, ligue no `pyproject.toml` — o `--strict`/`--no-strict` da linha de
comando vence a config:

```toml
[tool.tempest]
layers_strict = true
```

**Exceção que o projeto assume fica num lugar só**, com motivo, revisada no
diff como código: o dict `ARCHITECTURE_EXCEPTIONS` em
`tests/architecture_exceptions.py`. A chave é `"<CÓDIGO> <caminho>"` (todas
as violações daquele código no arquivo) ou `"<CÓDIGO> <caminho>::<alvo>"`
(só a que fala daquele nome — a classe, `Classe.método` ou a função). O
caminho é relativo à raiz do projeto. O arquivo também é lido com `ast`,
nunca importado:

```python
"""Exceções de arquitetura que o projeto assume, cada uma com motivo."""

ARCHITECTURE_EXCEPTIONS: dict[str, str] = {
    "SVC001 src/services/catalogue/catalogue.py::CatalogueService": (
        "catálogo estático, sem repository para o BaseService"
    ),
    "REPO001 src/db/repositories/legacy/legacy.py": "wrapper mantido até a 2.0",
}
```

Com esse arquivo, um `src/services/catalogue/catalogue.py` que define
`CatalogueService` (sem `BaseService`) e uma função de módulo
`load_items`, e nenhum `legacy.py`, o `tempest lint-layers` sai assim
(saída real, exit code `1` por causa do `SVC002`):

```text
src/services/catalogue/catalogue.py:5: SVC002 module-level function `load_items` in services/; move it to utils/, or make it a method of the service that uses it.
tests/architecture_exceptions.py:7: EXC002 (warning) `REPO001 src/db/repositories/legacy/legacy.py` matches no finding; the code it excused is gone or moved — delete the entry, or fix its path.
2 layer finding(s): 1 error(s), 1 warning(s).
```

O `SVC001` do `CatalogueService` sumiu, porque a exceção o cobre. A entrada
de `REPO001` não casa com nada e vira o aviso `EXC002`: exceção não
sobrevive ao código que ela desculpava.

| Código | Severidade | O que acusa |
| --- | --- | --- |
| `EXC001` | erro, sempre (mesmo com `--rule`) | entrada que o checker não consegue usar: chave fora da forma, código desconhecido, motivo vazio ou que não é string literal, chave repetida, arquivo sem o dict |
| `EXC002` | aviso | entrada que não desculpa nenhuma violação — o código mudou ou o caminho está errado |

`--rule` com código desconhecido sai com exit code `2` e a lista dos
códigos válidos. O `tempest check` aceita as mesmas duas opções e as entrega
ao passo `lint-layers` — `tempest check --rule STR002 --strict` roda o gate
inteiro com o passo filtrado e o aviso derrubando (exit code `1` com um
`STR002`, `0` sem `--strict`; medido num projeto do `tempest new`).

!!! tip "Marcador ou exceção declarada?"
    Os marcadores `# tempest: no-base-service` e
    `# tempest: router-builds` continuam valendo. A exceção declarada serve
    para qualquer regra, inclusive as que não têm marcador, e junta num
    arquivo só o que antes ficava espalhado — a review lê o motivo de
    todas de uma vez.
