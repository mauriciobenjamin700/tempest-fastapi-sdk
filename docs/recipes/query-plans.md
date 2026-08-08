# Planos de query (EXPLAIN)

Ferramenta de desenvolvimento para a pergunta "por que esse endpoint está
lento?". Cronômetro na aplicação diz *quanto* demorou; o plano do banco diz
*por quê*.

## Capturando um bloco

```python
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import OrderModel
from tempest_fastapi_sdk import BaseRepository, explain_queries


async def profile_page(
    session: AsyncSession,
    orders: BaseRepository[OrderModel],
) -> str:
    """Captura os planos de uma paginação e devolve o relatório.

    Args:
        session (AsyncSession): A sessão a observar.
        orders (BaseRepository[OrderModel]): Repositório de pedidos.

    Returns:
        str: Uma linha por statement capturado.
    """
    async with explain_queries(session) as report:
        await orders.paginate(filters={"status": "open"}, page=3)
    return report.report()
```

Tudo que a sessão executar dentro do bloco é registrado. Ao sair, cada
statement é explicado e o relatório é preenchido:

```text
12.75ms cost=431.2 rows=1904 [measured] SELECT "order".id, "order".status ...
0.31ms cost=8.1 rows=1 [measured] SELECT count(*) AS count_1 FROM "order" ...
```

!!! info "O relatório só enche na saída"
    Os statements são registrados durante o bloco e explicados **depois**.
    Explicar durante perturbaria justamente o que está sendo medido.

O repositório expõe o mesmo bloco como açúcar:

```python
from src.db.models import OrderModel
from tempest_fastapi_sdk import BaseRepository, ExplainReport


async def profile_list(orders: BaseRepository[OrderModel]) -> ExplainReport:
    """Abre o bloco pelo repositório em vez de pela sessão.

    Args:
        orders (BaseRepository[OrderModel]): Repositório de pedidos.

    Returns:
        ExplainReport: Os planos capturados.
    """
    async with orders.explain() as report:
        await orders.list(filters={"status": "open"})
    return report
```

## O que cada banco entrega

| Backend | Comando | `detail` | Custo | Tempo medido |
| --- | --- | --- | --- | --- |
| PostgreSQL (`SELECT`) | `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` | `MEASURED` | sim | sim |
| PostgreSQL (escrita) | `EXPLAIN (FORMAT JSON)` | `ESTIMATED` | sim | não |
| SQLite | `EXPLAIN QUERY PLAN` | `PLAN_ONLY` | não | não |

O SQLite mostra qual índice cada passo usa — ou que não usa nenhum — e
nada mais. O SDK reporta `ExplainDetail.PLAN_ONLY` em vez de inventar
números; `total_cost` e `duration_ms` ficam `None`, não zero, porque zero
se leria como "de graça".

## Escritas nunca são reexecutadas

`EXPLAIN ANALYZE` **executa** o que explica. Aplicá-lo ao `INSERT` que o
seu bloco acabou de fazer inseriria uma segunda linha. Por isso só
`SELECT` é analisado; qualquer outra coisa é explicada sem `ANALYZE`, o
que consulta o planejador sem tocar nos dados.

```python
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import OrderModel
from tempest_fastapi_sdk import BaseRepository, explain_queries


async def insert_once(
    session: AsyncSession,
    orders: BaseRepository[OrderModel],
    order: OrderModel,
) -> int:
    """Grava um pedido dentro do bloco e conta as linhas.

    Args:
        session (AsyncSession): A sessão a observar.
        orders (BaseRepository[OrderModel]): Repositório de pedidos.
        order (OrderModel): O pedido a gravar.

    Returns:
        int: O total de linhas — 1, porque a escrita não é reexecutada.
    """
    async with explain_queries(session):
        await orders.add(order)   # explicado, não reexecutado
    return await orders.count()
```

Um `text()` cru é classificado pela primeira palavra, e o que não for
reconhecido é tratado como escrita — o lado seguro do erro.

Se até o plano estimado for caro demais, desligue a análise:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import OrderModel
from tempest_fastapi_sdk import BaseRepository, ExplainReport, explain_queries


async def estimate_only(
    session: AsyncSession,
    orders: BaseRepository[OrderModel],
) -> ExplainReport:
    """Coleta só estimativas do planejador, sem rodar nada duas vezes.

    Args:
        session (AsyncSession): A sessão a observar.
        orders (BaseRepository[OrderModel]): Repositório de pedidos.

    Returns:
        ExplainReport: Os planos, sem tempo medido.
    """
    async with explain_queries(session, analyze=False) as report:
        await orders.list()
    return report
```

## Lendo o relatório

```python
from tempest_fastapi_sdk import ExplainReport, QueryPlan


def worst(report: ExplainReport) -> QueryPlan | None:
    """Escolhe o statement que merece atenção primeiro.

    Args:
        report (ExplainReport): O relatório capturado.

    Returns:
        QueryPlan | None: O plano mais caro, ou ``None`` se nada foi
        capturado.
    """
    slowest = report.slowest
    if slowest is not None:
        print(slowest.summary())
        print(slowest.plan_text)

    print(len(report))                 # quantos statements
    print(report.total_duration_ms)    # None se nada foi medido
    return slowest
```

`slowest` usa o tempo medido; onde não há tempo, cai para o custo do
planejador — então a propriedade continua respondendo "qual eu olho
primeiro?" num backend que não cronometra nada.

Cada `QueryPlan` carrega ainda `raw`, com a saída intocada do banco, para
o que os campos tipados não cobrem (nós filhos, contagem de buffers).

!!! tip "Os planos sobrevivem a uma exceção"
    Se o bloco levantar, o que foi capturado antes da falha continua no
    relatório — e a query que está errando costuma ser exatamente a que
    você quer ver o plano.

## Escopo

Só a sessão passada é observada, então uma requisição concorrente em outra
sessão não polui o relatório.

!!! warning "Não é para o caminho quente"
    Cada `SELECT` analisado roda duas vezes. Isto é para desenvolvimento e
    para uma sessão de profiling deliberada, não para deixar ligado em
    produção.

## Recapitulando

- `explain_queries(session)` captura tudo do bloco e explica na saída.
- PostgreSQL dá custo, tempo medido e linhas reais versus estimadas;
  SQLite dá o plano e o SDK diz que é só isso.
- Escrita nunca é reexecutada — a regra que impede a ferramenta de ser
  destrutiva.
- `report.slowest` e `report.report()` para achar o culpado; `plan.raw`
  para o resto.
