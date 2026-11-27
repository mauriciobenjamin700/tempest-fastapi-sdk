# Dashboard: cards, indicadores e mapa

Use para pôr métricas de negócio no dashboard: cards, áreas de indicadores com thresholds e o mapa de distribuição geográfica.

Parte da receita [Painel admin](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin.md).

## Dashboard: cards de métricas de negócio (`dashboard_cards=`)

O dashboard já mostra CPU/RAM/contadores. Para métricas do **seu
negócio** — pedidos hoje, receita vs semana passada, usuários por plano
— passe `dashboard_cards`. Cada card é um `MetricCard(label, compute)`
onde `compute` é uma função async que recebe a sessão e devolve um de
três tipos:

```python
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    AdminSite,
    MetricCard,
    MetricPartition,
    MetricTrend,
    MetricValue,
)

from src.db.models import OrderModel
from src.db.repositories import OrderRepository


async def orders_today(session: AsyncSession) -> MetricValue:
    total = await OrderRepository(session).count(filters={"start_in": date.today()})
    return MetricValue(total, unit="pedidos")


async def revenue_between(session: AsyncSession, start: date, end: date) -> float:
    """Soma a receita dos pedidos criados em [start, end)."""
    result = await session.execute(
        select(func.coalesce(func.sum(OrderModel.total), 0)).where(
            func.date(OrderModel.created_at) >= start,
            func.date(OrderModel.created_at) < end,
        )
    )
    return float(result.scalar_one())


async def revenue_trend(session: AsyncSession) -> MetricTrend:
    tomorrow = date.today() + timedelta(days=1)
    this_week = tomorrow - timedelta(days=7)
    last_week = this_week - timedelta(days=7)
    return MetricTrend(
        value=await revenue_between(session, this_week, tomorrow),
        previous=await revenue_between(session, last_week, this_week),
        unit="BRL",
    )


async def users_by_plan(session: AsyncSession) -> MetricPartition:
    return MetricPartition(segments=[("free", 120), ("pro", 30), ("enterprise", 4)])


site = AdminSite(
    title="Shop",
    dashboard_cards=[
        MetricCard("Pedidos hoje", orders_today, help_text="desde 00h"),
        MetricCard("Receita", revenue_trend),
        MetricCard("Usuários por plano", users_by_plan),
    ],
)
```

- **`MetricValue(value, unit=None)`** — um número grande.
- **`MetricTrend(value, previous, unit=None)`** — número + seta ▲/▼ e a
  variação percentual vs o período anterior (`delta` / `pct` /
  `direction` calculados pra você).
- **`MetricPartition(segments=[(label, value), ...])`** — breakdown com
  barras proporcionais; `total` somado automaticamente.

Os cards renderizam no topo do dashboard, computados a cada load. Um
card cujo `compute` levanta é **pulado** — uma métrica quebrada nunca
zera a página — e a falha vai para o log com traceback
(`logger.exception` no logger `tempest_fastapi_sdk.admin.router`, nomeando
o `label` do card), então o card que sumiu da tela aparece no log.

## Áreas de indicadores: schema + service (`metrics_areas=`)

O `MetricCard` recebe a sessão e consulta o banco direto, card por card.
Quando o seu service **já sabe** coletar os indicadores, repetir a
consulta no admin é trabalho dobrado — e pula a camada de service. A
`MetricsArea` é a outra forma: você entrega **um schema com um campo por
indicador** e **um service cujo método não recebe argumento**, e o admin
gera uma página com um card por campo, listada em **Metrics** na sidebar.

```python
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AdminSite, BaseUserModel, MetricsArea, Threshold


class UserModel(BaseUserModel):
    __tablename__ = "users"


class RideMetricsSchema(BaseModel):
    messages_to_route: float = Field(
        title="Mensagens até achar a rota",
        description="Média de mensagens antes do usuário chegar à rota que queria.",
    )
    accesses: int = Field(title="Acessos")
    accounts_created: int = Field(title="Contas criadas")
    rides_created: int = Field(title="Caronas criadas")


class RideMetricsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def collect(self) -> RideMetricsSchema:
        accounts = await self.session.scalar(select(func.count()).select_from(UserModel))
        return RideMetricsSchema(
            messages_to_route=4.2,
            accesses=1200,
            accounts_created=accounts or 0,
            rides_created=87,
        )


site = AdminSite(
    title="Transporte",
    metrics_areas=[
        MetricsArea(
            "Caronas",
            RideMetricsSchema,
            provider=RideMetricsService,
            method="collect",
            thresholds={
                "messages_to_route": Threshold(green=3, yellow=6, lower_is_better=True),
                "accounts_created": Threshold(green=100, yellow=40),
                "rides_created": Threshold(green=100, yellow=40),
            },
            units={"rides_created": "caronas"},
        )
    ],
)
```

A página fica em `/admin/metrics/caronas` (o slug sai do título). Peça por
peça:

- **`provider`** constrói o service a partir da sessão do admin. A própria
  classe serve quando o `__init__` recebe a sessão; uma função de
  composição (`build_ride_metrics_service`) também. Pode ser sync ou
  `async`.
- **`method`** é chamado **sem argumento** e devolve uma instância do
  `schema`. Outra coisa vira erro na página.
- **Cada campo vira um card**: `title` é o rótulo (o nome do campo quando
  falta), `description` a linha embaixo do valor. Número vira um card de
  valor; um campo tipado `MetricValue`, `MetricTrend` ou `MetricPartition`
  renderiza como esse card.
- **`units`** põe a unidade depois do número.

### Thresholds: verde, amarelo, vermelho

`Threshold(green=, yellow=)` pinta o card de um campo numérico (ou o valor
atual de um `MetricTrend`) com uma borda e um selo — **OK**, **Attention**
ou **Critical**:

| `Threshold(green=100, yellow=40)` (maior é melhor) | Faixa |
| --- | --- |
| `>= 100` | verde |
| `>= 40` e `< 100` | amarelo |
| `< 40` | vermelho |

Com `lower_is_better=True` a comparação inverte — `Threshold(green=3,
yellow=6, lower_is_better=True)` é verde até 3, amarelo até 6 e vermelho
acima. O limite exato cai sempre na faixa melhor. A cor vem dos tokens do
tema (`--tempest-success`, `--tempest-warning`, `--tempest-danger`), então
light e dark seguem consistentes.

!!! tip "Erro cedo, não card sem cor"
    `thresholds` ou `units` com um nome que o schema não tem levanta
    `ValueError` na construção da `MetricsArea`, e limites na ordem errada
    para a direção (`green < yellow` com maior-é-melhor) levantam na
    construção do `Threshold`. Um typo nunca vira um card que simplesmente
    não pinta.

### Quando o service falha

Se o `provider` ou o método levantam, a página responde `200` com um
aviso no lugar dos cards, e o traceback vai para o log
(`logger.exception` em `tempest_fastapi_sdk.admin.router`, nomeando a
área). Um service quebrado nunca vira `500` no painel.

### Quem vê

A área é a superfície `AdminSurfaceEnum.METRICS`: com `surface_policy`,
quem a política nega recebe `403` na página e não vê os links na sidebar.

## Mapa de distribuição geográfica (`location_maps=`) *(v0.367.0+)*

Com o [`BRLocationMixin`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/geo.md#cidade-e-estado-do-usuario-brlocationmixin-v03670)
no model, o painel desenha **onde** estão as linhas: um mapa do Brasil com
cada estado num tom conforme a contagem e uma bolha por cidade, a legenda,
as tabelas de estados e cidades e quantas linhas ainda não têm localização.
Registre um `BRLocationMap` por mapa em `AdminSite(location_maps=[...])`:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import AdminModel, BRLocationMap
from tempest_fastapi_sdk.geo import BRLocationMixin


class UserModel(BRLocationMixin, BaseUserModel):
    """Users with the city and state they live in."""

    __tablename__ = "users"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./painel.db")
site = AdminSite(
    title="Painel",
    location_maps=[
        BRLocationMap("Usuários por região", UserModel, value_label="usuários"),
        BRLocationMap(
            "Usuários ativos",
            UserModel,
            where=lambda: UserModel.is_active.is_(True),
            value_label="usuários",
        ),
    ],
)
site.register(AdminModel(UserModel))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the tables and a few users spread over three cities."""
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        admin = UserModel(email="admin@example.com", hashed_password="", is_admin=True)
        admin.set_password("troque-esta-senha")
        session.add(admin)
        places = [("São Paulo", "SP"), ("São Paulo", "SP"), ("Campinas", "SP")]
        places.append(("Recife", "PE"))
        for index, (city, state) in enumerate(places):
            user = UserModel(email=f"user{index}@example.com", hashed_password="")
            user.set_br_location(city, state)
            session.add(user)
        await session.commit()
    yield
    await db.disconnect()


app = FastAPI(lifespan=lifespan)
app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key="troque-por-um-segredo-de-32-bytes-ou-mais",
        cookie_secure=False,
    )
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

!!! note "`create_tables()` aqui é atalho de demonstração"
    O exemplo cabe num arquivo só, então cria as tabelas direto no
    lifespan. Num serviço, o lifespan chama `sync_schema_async()` sobre as
    migrations do Alembic: `create_tables()` não acrescenta coluna em
    tabela que já existe. Veja
    [Migrations »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/migrations.md#3-sincronize-o-schema-no-lifespan).

Cada mapa vira uma entrada na seção **Maps** da barra lateral, em
`/admin/maps/<slug>` (o slug sai do título, sem acento:
`usuarios-por-regiao`). Medido com o exemplo, por requisição ao painel:

- a barra lateral lista `/admin/maps/usuarios-por-regiao` e
  `/admin/maps/usuarios-ativos`;
- a página responde `200` com Total `5`, With location `4` e No location `1`
  (o administrador não tem cidade);
- as bolhas, maior primeiro: `São Paulo (SP): 2 usuários`,
  `Campinas (SP): 1 usuários`, `Recife (PE): 1 usuários`;
- a dica de cada estado (`SP: 3 usuários`, `PE: 1 usuários`,
  `BA: 0 usuários`) e a legenda com as faixas de cada tom.

O que cada argumento faz:

- `where=` recebe uma função que monta o filtro a cada requisição
  (`lambda: UserModel.is_active.is_(True)`), aplicado a todas as contagens.
- `city_limit=` (padrão 50) limita as bolhas e a tabela às maiores cidades.
- `value_label=` é a palavra depois de cada número nas dicas e nas tabelas.

O mapa é um SVG gerado no servidor, com os contornos dos estados embutidos no
pacote: a página **não faz nenhuma requisição externa** (nem script de mapa,
nem servidor de tiles). As cores saem das variáveis do tema, então o modo
escuro (`AdminTheme(dark_mode=True)`) já vale. Validado em browser real a
1366 px e a 390 px, nos temas claro e escuro: no celular, o mapa ocupa a
largura toda e as tabelas vão para baixo dele.

!!! info "Quem vê"
    A página pede `VIEW` no model à `access_policy`, perguntado ao
    `AdminModel` registrado do model (ou a um `AdminModel(model)` descartável,
    quando ele não está registrado). Negado, a página responde `403` e some
    da barra lateral.

!!! note "Quando a contagem falha"
    Se o `where=` ou o banco levantam, a página renderiza assim mesmo, com o
    aviso `This map could not be counted` no lugar do mapa e o traceback no
    log — como nas áreas de indicadores.
