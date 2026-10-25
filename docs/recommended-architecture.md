# Arquitetura recomendada

A página de [Arquitetura](architecture.md) diz **quais camadas existem**. Esta
diz **como cada arquivo dentro delas tem forma**, de um jeito que funciona
igual para quem chega no projeto e para uma IA escrevendo código nele: o nome
do arquivo diz o que tem dentro, o grafo de dependência é previsível e a
suíte explica o que cada teste prova.

Cada regra aqui tem **um código** que o `tempest check` cobra. Esse é o
ponto: num serviço real (o `transport-backend`, registrado na
[#616](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/616)),
as regras que só existiam no `CLAUDE.md` estavam quebradas em centenas de
lugares quando o primeiro guard foi escrito. Prosa não é gate.

!!! tip "O `tempest new` já sai assim"
    O projeto que o `tempest new` gera passa em todas as regras desta
    página, erros e avisos. Se você está começando, esta página explica o
    que já está no seu disco. Se tem um serviço antigo, vá para
    [Migrando um serviço existente](#migrando-um-servico-existente).

## Como o gate cobra

`tempest lint-layers` lê o código com `ast` e **nunca importa o projeto**.
Cada violação é uma linha `arquivo:linha: CÓDIGO mensagem`, e a mensagem diz
o que mudar. As regras desta página entram como **aviso**: aparecem com
`(warning)` e não derrubam o `tempest check`, até você ligar o modo estrito:

```toml
[tool.tempest]
layers_strict = true
```

Para migrar uma regra por vez, filtre:

```bash
tempest lint-layers --rule IMP002
```

A tabela completa dos códigos, com a saída real de cada um, está em
[CLI › Regras de camada](recipes/cli.md#regras-de-camada-tempest-lint-layers).

## 1. Uma classe por arquivo, num pacote de contexto

**Por quê:** o nome do arquivo vira índice. `services/billing/invoice.py`
contém o `InvoiceService` e mais nada — quem procura o service de fatura
acha sem abrir nada, e um diff que toca o arquivo toca só ele. Um
`models/route.py` com cinco models obriga a abrir o arquivo para descobrir o
que tem lá.

As quatro camadas com classe — `controllers/`, `services/`,
`db/repositories/` e `db/models/` — guardam cada módulo num **pacote de
contexto** (um substantivo plural do domínio: `products`, `orders`,
`billing`), com uma classe por módulo.

❌ Antes:

```python
# src/db/models/route.py
from sqlalchemy.orm import Mapped, mapped_column
from tempest_fastapi_sdk import BaseModel


class RouteModel(BaseModel):
    """Rota de entrega."""

    __tablename__ = "routes"

    name: Mapped[str] = mapped_column()


class StopModel(BaseModel):
    """Parada de uma rota."""

    __tablename__ = "stops"

    address: Mapped[str] = mapped_column()
```

✅ Depois — um arquivo por model, e o pacote reexportando:

```python
# src/db/models/routing/route.py
from sqlalchemy.orm import Mapped, mapped_column
from tempest_fastapi_sdk import BaseModel


class RouteModel(BaseModel):
    """Rota de entrega."""

    __tablename__ = "routes"

    name: Mapped[str] = mapped_column()
```

```python
# src/db/models/routing/__init__.py
from .route import RouteModel as RouteModel
from .stop import StopModel as StopModel

__all__: list[str] = ["RouteModel", "StopModel"]
```

| Código | Acusa |
| --- | --- |
| `LAY001` | segunda classe ou função de módulo em `controllers/`, `repositories/` ou `models/` |
| `LAY002` | módulo direto na raiz de uma das quatro camadas |
| `SVC002` (erro) | o mesmo em `services/` |

O nome do contexto é livre: a lista que o `CLAUDE.md` do projeto sugere
guia, não restringe.

## 2. Import pelo pacote da camada; `__init__` relativo

**Por quê:** o caminho interno de um pacote é detalhe dele. Quem importa
`from src.services.billing.invoice import InvoiceService` quebra no dia em que
o arquivo muda de lugar; quem importa `from src.services import InvoiceService`
não. E o `__init__.py` que importa os vizinhos com caminho absoluto repete o
próprio nome do pacote em cada linha.

✅ O pacote importa os vizinhos relativo; o resto do código importa pelo
pacote:

```python
# src/services/__init__.py
from .billing import InvoiceService as InvoiceService
from .products import ProductService as ProductService

__all__: list[str] = ["InvoiceService", "ProductService"]
```

```python
# src/controllers/billing/invoice.py
from tempest_fastapi_sdk import BaseController

from src.schemas import InvoiceResponseSchema
from src.services import InvoiceService


class InvoiceController(BaseController[InvoiceService, InvoiceResponseSchema]):
    """Coordenação de faturas."""
```

| Código | Acusa |
| --- | --- |
| `IMP001` | `__init__.py` importando módulo do próprio pacote com caminho absoluto |
| `IMP002` | import pelo submódulo de um nome que um pacote acima já reexporta (seguido de forma transitiva, inclusive por `import *`) |

!!! warning "Quando o pacote fecharia ciclo"
    A sugestão do `IMP002` é o pacote mais alto que reexporta o nome **sem
    conter** o módulo que importa. Mesmo assim, às vezes importar pelo
    pacote fecha ciclo — no transport, task e script importavam
    `api.dependencies.resources` direto, porque importar o pacote
    `api.dependencies` fecharia ciclo com `src.tasks`. Esse caso é
    [exceção declarada](#excecao-declarada), não reescrita do import.

## 3. O que service e controller recebem

**Por quê:** um service que recebe a `AsyncSession` monta os próprios
repositories, e então ninguém de fora consegue trocá-los num teste. Um
service que recebe outro service esconde uma orquestração que é o trabalho
do controller. E uma classe com `from_session` sabe montar o próprio grafo:
o conhecimento de "o que precisa de quê" se espalha por cada classe, e cada
call site repete a montagem.

❌ Antes:

```python
# src/services/wallet/settlement.py
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repositories import SettlementRepository


class SettlementService:
    """Liquida o saldo retido."""

    def __init__(self, session: AsyncSession) -> None:
        """Guarda a sessão."""
        self.repository = SettlementRepository(session)

    @classmethod
    def from_session(cls, session: AsyncSession) -> "SettlementService":
        """Monta o service sobre uma sessão."""
        return cls(session)
```

✅ Depois — o service recebe o repository, e o grafo é montado num lugar só,
o **composition root**:

```python
# src/scripts/composition.py
from sqlalchemy.ext.asyncio import AsyncSession

from src.controllers import SettlementController
from src.db.repositories import SettlementRepository
from src.services import SettlementService


def build_settlement_controller(session: AsyncSession) -> SettlementController:
    """Monta o controller de liquidação sobre uma sessão.

    Args:
        session (AsyncSession): A sessão por onde o repository lê.

    Returns:
        SettlementController: O controller pronto.
    """
    return SettlementController(SettlementService(SettlementRepository(session)))
```

O provider de requisição em `api/dependencies/controllers.py` chama o
`build_settlement_controller(session)` com a sessão que o `Depends` resolveu;
uma task, um consumer de fila, um seed e a suíte chamam a mesma função. O
grafo é escrito uma vez.

| Código | Acusa |
| --- | --- |
| `DI001` | parâmetro anotado com `AsyncSession` no `__init__` de service ou controller |
| `DI002` | service recebendo `*Service`, controller recebendo `*Controller` |
| `DI003` | função ou método `from_session`, em qualquer lugar |

O caso real em que dois services precisam andar juntos existe — no
transport, a liquidação e o crédito na carteira caem na mesma transação. Ele
vira [exceção declarada](#excecao-declarada), mirando só aquele parâmetro.
Para a carteira do SDK, desde a v0.330.0 nem isso: a escrita de saldo é o
[`WalletLedger`](recipes/wallet.md#o-ledger-nao-o-service-walletledger), uma
camada de dados que o service recebe como recebe um repository.

## 4. Repository não importa repository

**Por quê:** uma consulta sobre duas tabelas é um `select()` com `join`, no
repository dono da consulta. Um repository que importa outro para "reusar"
uma query cria dependência entre camadas de dados que nenhum service vê, e
combinar dois repositories é exatamente o trabalho do service.

| Código | Acusa |
| --- | --- |
| `REPO002` | import de um `*Repository` dentro de `repositories/` (o nome usado só como base de classe — o repository base do projeto — passa) |

## 5. Um router por arquivo; `/api` uma vez

**Por quê:** com o `/api` repetido em cada `include_router` (22 vezes, no
transport), mudar o prefixo da API ou montar um domínio fora dela vira caça
ao prefixo. E dois routers num arquivo escondem um deles de quem procura
pelo nome.

✅ Cada domínio declara só o próprio prefixo; o `/api` aparece num lugar:

```python
# src/api/routers/api.py
from fastapi import APIRouter

from .orders import router as orders_router
from .service_info import router as service_info_router

API_PREFIX: str = "/api"
"""Prefixo de toda rota de negócio."""


def build_api_router() -> APIRouter:
    """Monta o router que o ``create_app`` inclui.

    Returns:
        APIRouter: ``/api`` com todo router de domínio incluído.
    """
    api: APIRouter = APIRouter(prefix=API_PREFIX)
    api.include_router(service_info_router)
    api.include_router(orders_router)
    return api
```

| Código | Acusa |
| --- | --- |
| `RTR003` | mais de um `APIRouter(...)` num módulo de `routers/` |
| `RTR004` | `prefix` `/api` (ou abaixo dele) fora de uma função `build_api_router` |

## 6. Sem função aninhada nem helper privado de módulo

**Por quê:** uma função definida dentro de outra não tem nome importável,
não tem teste próprio e carrega estado por closure, invisível na assinatura.
Uma função de módulo `_x` é um helper sem casa: ninguém a reutiliza, e ela
some do índice do pacote.

❌ Antes:

```python
# src/utils/notify.py
from collections.abc import Awaitable, Callable


def make_sender(channel: str) -> Callable[[str], Awaitable[None]]:
    """Monta um enviador para o canal."""

    async def send(message: str) -> None:
        """Envia a mensagem."""
        print(channel, message)

    return send
```

✅ Depois — estado vira objeto chamável; argumento fixo vira
`functools.partial` sobre uma função de módulo:

```python
# src/utils/notify.py
from collections.abc import Coroutine
from functools import partial
from typing import Any


async def send(message: str, *, channel: str) -> None:
    """Envia a mensagem pelo canal.

    Args:
        message (str): O texto.
        channel (str): Para onde vai.
    """
    print(channel, message)


class ChannelSender:
    """Enviador ligado a um canal, com estado visível."""

    def __init__(self, channel: str) -> None:
        """Guarda o canal.

        Args:
            channel (str): Para onde as mensagens vão.
        """
        self.channel: str = channel

    async def __call__(self, message: str) -> None:
        """Envia uma mensagem pelo canal guardado.

        Args:
            message (str): O texto.
        """
        await send(message, channel=self.channel)


send_by_email: partial[Coroutine[Any, Any, None]] = partial(send, channel="email")
"""O mesmo enviador, com o canal já fixo."""
```

| Código | Acusa |
| --- | --- |
| `STR001` | função definida dentro de outra (no código **e** na suíte) |
| `STR002` | função de módulo com um `_` só no início (no código **e** na suíte) |

Na suíte, o helper privado vira fixture ou módulo de helper público — no
transport, eram 174 funções aninhadas e 76 helpers `_x` só nos testes.

## 7. Task é só registro

**Por quê:** o processo da API não deveria importar o código das tasks — e,
com ele, o grafo inteiro que elas usam. Um `tasks.py` de 400 linhas mistura
registro, regra e agendamento.

A camada de tasks que o `tempest new --extras tasks` gera separa assim:

| Arquivo | Papel |
| --- | --- |
| `tasks/names.py` | `*_TASK_NAME` e `*_SCHEDULE` (com `every_n_minutes` / `daily`); não importa nada do projeto |
| `tasks/example.py` | só o registro: `@tq.cron(EXAMPLE_SCHEDULE, name=EXAMPLE_TASK_NAME)`, chamando o script |
| `scripts/example.py` | o trabalho, chamável com ou sem worker |
| `tasks/dispatch.py` | `TaskDispatcher`: a API enfileira **pelo nome**, sem importar a task |
| `tasks/worker.py` | importa os módulos de task e expõe `broker` / `scheduler` |

O envio pelo nome usa
[`TaskQueue.enqueue_by_name`](recipes/queue-tasks.md#enfileirar-pelo-nome-enqueue_by_name),
e a política de retry da task continua valendo — o worker repõe os labels
com que ela foi registrada.

!!! danger "Nome de task é para a vida toda"
    Mensagem já enfileirada guarda o nome. Renomear `EXAMPLE_TASK_NAME` deixa
    as mensagens antigas sem dono: o worker registra
    `task "<nome>" is not found` e as descarta. Ao reorganizar as tasks,
    **mova o código e preserve o nome**.

## 8. Schema documentado

**Por quê:** o cliente lê o `openapi.json`. Campo sem `title`,
`description` e `examples` chega lá só com nome e tipo — no transport, 370
campos.

O campo folha leva os três; o campo que guarda outro schema, só `title` e
`description`. Essa é a única regra que precisa importar o projeto, então
ela roda na suíte (`assert_schema_metadata`) e no comando
`tempest lint-schemas` — detalhes em
[CLI › Metadado de schema](recipes/cli.md#metadado-de-schema-tempest-lint-schemas).

## 9. A suíte explica o que prova

**Por quê:** um teste é documentação executável. A docstring diz a
situação que ele monta e o que ele prova; um teste que não confere nada
passa sempre; `pytest.raises(Exception)` passa com qualquer bug; e um valor
sorteado no id do `parametrize` faz cada worker do `pytest -n` coletar
testes diferentes — no transport, a suíte não rodava em paralelo por isso
(4 min em série, 47 s em paralelo depois da correção).

✅ A forma:

```python
# tests/test_orders.py
from fastapi.testclient import TestClient

from src.api.app import create_app


def test_a_duplicate_order_is_refused() -> None:
    """Cenário: o mesmo pedido enviado duas vezes.

    Esperado: o segundo responde ``409`` com ``ORDER_DUPLICATED``.
    """
    with TestClient(create_app()) as client:
        payload = {"code": "A-1"}
        assert client.post("/api/orders", json=payload).status_code == 201
        duplicate = client.post("/api/orders", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "ORDER_DUPLICATED"
```

| Código | Acusa |
| --- | --- |
| `TST001` | docstring de teste sem `Cenário:` e `Esperado:` |
| `TST002` | teste sem `assert`, sem `pytest.raises`/`warns` e sem chamada `assert*` |
| `TST003` | `pytest.raises(Exception)` ou `BaseException` |
| `TST004` | id de `parametrize` com valor sorteado no import |

O `TST004` segue a medição, não a suposição: `uuid4()` e `datetime.now()`
**crus** recebem id pela posição e passam; viram problema quando viram
texto (`str(uuid4())`, `uuid4().hex`) ou entram em `ids=`.

## Exceção declarada

Toda regra admite o caso que você assume de propósito — **com o motivo
escrito**, num lugar só, revisado no diff como código:

```python
# tests/architecture_exceptions.py
ARCHITECTURE_EXCEPTIONS: dict[str, str] = {
    "DI002 src/services/wallet/settlement.py::SettlementService.wallet_service": (
        "a liquidação e o crédito caem na mesma transação"
    ),
}
```

A chave é `"<CÓDIGO> <caminho>"` (todas as violações daquele código no
arquivo) ou `"<CÓDIGO> <caminho>::<alvo>"` (só a daquele nome). Entrada que
não casa com mais nada sai como `EXC002`: a exceção não sobrevive ao código
que ela desculpava.

## Migrando um serviço existente

Comece **medindo**. A contagem por código diz quanto trabalho há e por
onde começar:

```bash
tempest lint-layers | awk '{print $2}' | grep -E '^[A-Z]+[0-9]+$' | sort | uniq -c
```

O próprio scaffold da 0.328.0, sem extras, dava esta contagem (medida):

```text
     11 IMP001
      8 IMP002
      1 LAY002
      1 RTR004
      3 TST001
```

Depois ataque **uma família por PR**, cada uma com `--rule` até zerar:

1. **`IMP001`/`IMP002`** primeiro: é mecânico, não muda comportamento e
   deixa as próximas mudanças mais baratas — mover arquivo não quebra mais
   import.
2. **`LAY001`/`LAY002`**: mover cada classe para o pacote de contexto,
   reexportando relativo. Com o passo 1 feito, os call sites não mudam.
3. **`DI001`–`DI003`**: criar o `scripts/composition.py` e fazer os
   providers chamarem os `build_*`; apagar os `from_session`.
4. **`RTR003`/`RTR004`**: criar o `routers/api.py` e tirar o `/api` dos
   domínios.
5. **`STR001`/`STR002`**: callbacks com estado viram classe; argumento fixo
   vira `partial`.
6. **`TST001`–`TST004`**: a suíte — de longe a maior conta no transport
   (1380 de 1394 testes sem a forma da docstring).
7. **Tasks e schemas**, quando o serviço tem: separar registro de trabalho,
   e documentar os campos.

Três armadilhas que o transport encontrou no caminho:

!!! warning "Ciclo de import dos recursos"
    Importar `api.dependencies` (o pacote) a partir das tasks e dos scripts
    fecharia ciclo com `src.tasks`. Lá, task e script ficaram importando
    `api.dependencies.resources` direto — com a
    [exceção declarada](#excecao-declarada) do `IMP002`.

!!! warning "Nome de task preservado"
    Ao separar o `tasks.py` grande em módulos de domínio, a task muda de
    arquivo mas **não de nome** — senão as mensagens já na fila ficam sem
    dono. Fixe o nome antigo na constante `*_TASK_NAME`.

!!! warning "Ids do pytest-xdist"
    Um `parametrize` com `uuid4()` virando texto no id impedia o
    `pytest -n auto`. Troque por literais fixos antes de ligar o paralelo.

Quando uma família zerar, ligue o `layers_strict = true` — a partir daí, o
aviso que voltar derruba o `tempest check`.

## Recap

- **Uma classe por arquivo**, em `<camada>/<contexto>/`; o pacote reexporta
  relativo e o resto importa pelo pacote.
- **Service recebe repository; controller recebe service.** O grafo é
  montado no `scripts/composition.py`, e ninguém tem `from_session`.
- **Repository não importa repository.**
- **Um router por arquivo**, e o `/api` só no `build_api_router`.
- **Sem função aninhada nem `_helper` de módulo**, no código e na suíte.
- **Task é só registro**, com nome fixo; a API enfileira pelo nome.
- **Schema documentado**, conferido por `assert_schema_metadata`.
- **Teste com `Cenário:`/`Esperado:`**, que confere algo, com exceção
  específica e `parametrize` determinístico.
- Tudo isso é **aviso** até você ligar o `layers_strict`; o que você assume
  de propósito vai em `tests/architecture_exceptions.py`, com motivo.
