# Geolocalização (distância + tempo)

Precisa saber **quantos km** separam dois pontos e **quanto tempo** leva a
viagem de carro, moto ou ônibus — sem pagar por uma API de mapas? O módulo
`tempest_fastapi_sdk.geo` resolve isso em duas camadas que compartilham os
mesmos schemas:

- **Heurística offline** — matemática pura, zero dependências, zero rede.
  Distância em linha reta (Haversine) ajustada por um fator de sinuosidade,
  e tempo pela velocidade média do modo. Instantânea e aproximada.
- **Roteamento real** — `OSRMBackend` conversa com um servidor
  [OSRM](https://project-osrm.org/) (open-source, grátis, self-hostável ou
  o servidor de demonstração público). Dá a geometria real da estrada.

Tudo importa **sem** o extra. Só o `OSRMBackend` precisa de `httpx`:

```bash
uv add "tempest-fastapi-sdk[geo]"
```

!!! info "Nenhuma API paga"
    A camada offline não faz rede nenhuma. O OSRM é software livre — use o
    servidor demo público ou rode o seu (`docker run osrm/osrm-backend`).
    Nada de chave paga em nenhum dos caminhos.

## Distância em linha reta

`haversine_km` recebe dois `Coordinate` (latitude/longitude em graus
decimais, já validados por `LatitudeField`/`LongitudeField`) e devolve a
distância great-circle em km — a "distância do pássaro", sem estradas:

```python
from tempest_fastapi_sdk.geo import Coordinate, haversine_km

sao_paulo = Coordinate(latitude=-23.5505, longitude=-46.6333)
rio = Coordinate(latitude=-22.9068, longitude=-43.1729)

km: float = haversine_km(sao_paulo, rio)
print(round(km, 1))  # 360.7
```

## Estimativa offline (distância + tempo por modo)

`estimate_travel` transforma a linha reta numa estimativa rodoviária:
multiplica a distância pelo **fator de sinuosidade** (quanto a estrada real
é mais longa que a reta, ~1.3 por padrão) e calcula o tempo pela velocidade
média do carro, escalada pelo fator do modo.

```python
from tempest_fastapi_sdk.geo import (
    Coordinate,
    TravelEstimate,
    TravelModeEnum,
    estimate_travel,
)

origem = Coordinate(latitude=-23.5505, longitude=-46.6333)
destino = Coordinate(latitude=-23.5015, longitude=-46.6553)

de_carro: TravelEstimate = estimate_travel(origem, destino, TravelModeEnum.CAR)
de_onibus: TravelEstimate = estimate_travel(origem, destino, TravelModeEnum.BUS)

print(de_carro.distance_km, de_carro.duration_minutes)   # 7.659817427032203 9.191780912438643
print(de_onibus.duration_minutes)                        # maior (ônibus para)
print(de_carro.source)                                   # "heuristic"
```

Os padrões são ajustáveis por chamada:

```python
from tempest_fastapi_sdk.geo import Coordinate, TravelModeEnum, estimate_travel

destino = Coordinate(latitude=-7.9899, longitude=-34.8386)

origem = Coordinate(latitude=-8.0476, longitude=-34.8770)


estimate_travel(
    origem,
    destino,
    TravelModeEnum.MOTORCYCLE,
    circuity_factor=1.4,       # estrada mais sinuosa
    car_speed_kmh=70.0,        # trecho de rodovia
)
```

!!! note "Moto e ônibus derivam do carro"
    Um único mapa, `DEFAULT_MODE_DURATION_FACTORS`, define quanto cada modo
    é mais lento/rápido que o carro (ônibus ~1.6x por paradas, moto ~0.95x).
    Ele escala **os dois** caminhos — a heurística (via velocidade) e o OSRM
    (via duração) — então tudo funciona mesmo com um perfil só de carro.

## Roteamento real com OSRM

`OSRMBackend` segue o padrão do SDK: você **injeta** o `httpx.AsyncClient`
(o SDK não abre nem fecha conexão por você) e ele devolve o mesmo
`TravelEstimate`, agora com `source="osrm"` e a distância real da estrada.

```python
import httpx

from tempest_fastapi_sdk.geo import Coordinate, OSRMBackend, TravelModeEnum

origem = Coordinate(latitude=-23.5505, longitude=-46.6333)
destino = Coordinate(latitude=-22.9068, longitude=-43.1729)


async def rota() -> None:
    """Consulta a rota real via servidor OSRM."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        backend = OSRMBackend(http_client=client)  # demo público por padrão
        estimativa = await backend.route(origem, destino, mode=TravelModeEnum.CAR)
        print(estimativa.distance_km, estimativa.duration_minutes)
```

`OSRMBackend` satisfaz o Protocol `RoutingBackend`, então você pode trocá-lo
por um mock nos testes ou por outra implementação sem mudar o call site.

!!! warning "Servidor demo = só carro"
    O demo público (`router.project-osrm.org`) expõe apenas o perfil de
    carro e é rate-limited. Moto e ônibus reusam a distância do carro e
    escalam a duração pelo fator do modo. Para perfis reais de moto/ônibus,
    rode um OSRM self-hostado com dados próprios e aponte `base_url` pra ele.

## Escolhendo a camada

| Precisa de... | Use |
| --- | --- |
| Rapidez, offline, "mais ou menos" | `estimate_travel` (heurística) |
| Distância/tempo real da estrada | `OSRMBackend.route` |
| Só a linha reta (raio, proximidade) | `haversine_km` |

Um padrão comum: tente o OSRM e caia na heurística se a rede falhar.

```python
from tempest_fastapi_sdk.geo import OSRMBackend, TravelEstimate, estimate_travel


async def estimar(origem, destino, mode, client) -> TravelEstimate:
    """Rota real quando dá; senão, estimativa offline."""
    try:
        return await OSRMBackend(http_client=client).route(
            origem, destino, mode=mode
        )
    except RuntimeError:
        return estimate_travel(origem, destino, mode)
```

## Exemplo integrado: ETA de entrega (FastAPI em camadas)

Um serviço real quer expor um endpoint que recebe origem, destino e modo e
devolve distância + tempo, tentando a rota real do OSRM e caindo na
heurística offline se a rede falhar. Segue a arquitetura em camadas do SDK
(schema → service → controller → router → dependency).

### Schemas de entrada/saída

```python
# src/schemas/geo.py
from tempest_fastapi_sdk.geo import Coordinate, TravelEstimate, TravelModeEnum
from tempest_fastapi_sdk.schemas.base import BaseSchema


class RouteRequestSchema(BaseSchema):
    """Pedido de estimativa de rota entre dois pontos.

    Attributes:
        origin: Coordenada de partida.
        destination: Coordenada de chegada.
        mode: Modo de viagem desejado.
    """

    origin: Coordinate
    destination: Coordinate
    mode: TravelModeEnum = TravelModeEnum.CAR


# A resposta é o próprio TravelEstimate do SDK — nada a redefinir.
RouteResponseSchema = TravelEstimate
```

### Service — regra de negócio + fallback

```python
# src/services/geo.py
from tempest_fastapi_sdk.geo import (
    Coordinate,
    RoutingBackend,
    TravelEstimate,
    TravelModeEnum,
    estimate_travel,
)


class GeoService:
    """Estima distância e tempo de viagem entre dois pontos.

    Usa um `RoutingBackend` (OSRM) para a rota real e cai na heurística
    offline quando o backend falha, para o endpoint nunca ficar 5xx só
    porque o servidor de rotas oscilou.
    """

    def __init__(self, routing: RoutingBackend) -> None:
        """Inicializa o serviço.

        Args:
            routing: Backend de roteamento (ex.: `OSRMBackend`).
        """
        self.routing: RoutingBackend = routing

    async def estimate(
        self,
        origin: Coordinate,
        destination: Coordinate,
        mode: TravelModeEnum = TravelModeEnum.CAR,
    ) -> TravelEstimate:
        """Estima a viagem, com rota real e fallback offline.

        Args:
            origin: Coordenada de partida.
            destination: Coordenada de chegada.
            mode: Modo de viagem.

        Returns:
            O `TravelEstimate` — `source="osrm"` quando a rota real
            respondeu, `source="heuristic"` no fallback.
        """
        try:
            return await self.routing.route(origin, destination, mode=mode)
        except RuntimeError:
            return estimate_travel(origin, destination, mode)
```

### Controller — passagem fina (orquestração futura)

```python
# src/controllers/geo.py
from src.schemas.geo import RouteRequestSchema
from src.services.geo import GeoService
from tempest_fastapi_sdk.geo import TravelEstimate


class GeoController:
    """Orquestra o `GeoService` para os routers."""

    def __init__(self, service: GeoService) -> None:
        """Inicializa o controller.

        Args:
            service: O serviço de geolocalização.
        """
        self.service: GeoService = service

    async def estimate_route(self, payload: RouteRequestSchema) -> TravelEstimate:
        """Estima uma rota a partir do payload validado.

        Args:
            payload: Origem, destino e modo.

        Returns:
            A estimativa de viagem.
        """
        return await self.service.estimate(
            payload.origin, payload.destination, payload.mode
        )
```

### Dependency — injeta o cliente httpx compartilhado

```python
# src/api/dependencies/services.py
from collections.abc import AsyncIterator

import httpx
from fastapi import Depends

from src.controllers.geo import GeoController
from src.services.geo import GeoService
from tempest_fastapi_sdk.geo import OSRMBackend


async def get_geo_controller() -> AsyncIterator[GeoController]:
    """Provê um `GeoController` com cliente httpx de vida curta.

    Yields:
        Um controller pronto pra uso, com o cliente fechado ao fim.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        backend = OSRMBackend(http_client=client)
        yield GeoController(GeoService(backend))
```

!!! tip "Reuse o cliente entre requests"
    Abrir um `httpx.AsyncClient` por request é simples mas custa handshakes.
    Em produção, crie um cliente único no `lifespan` da app, guarde em
    `app.state` e injete-o no `OSRMBackend` — o SDK nunca fecha o cliente
    que você passa, então o controle do ciclo de vida é seu.

### Router — só HTTP

```python
# src/api/routers/geo.py
from fastapi import APIRouter, Depends

from src.api.dependencies.services import get_geo_controller
from src.controllers.geo import GeoController
from src.schemas.geo import RouteRequestSchema
from tempest_fastapi_sdk.geo import TravelEstimate

router = APIRouter(prefix="/api/geo", tags=["geo"])


@router.post("/estimate")
async def estimate_route(
    payload: RouteRequestSchema,
    controller: GeoController = Depends(get_geo_controller),
) -> TravelEstimate:
    """Estima distância e tempo entre dois pontos por modo."""
    return await controller.estimate_route(payload)
```

Um `POST /api/geo/estimate` com origem/destino/modo devolve
`{"mode": "...", "distance_km": ..., "duration_minutes": ..., "source": ...}`.

## Filtro por raio e vizinhos (em memória)

Sem servidor de rotas, os helpers de geometria filtram e ordenam por
proximidade. `within_radius` devolve o que está dentro do raio;
`nearest` devolve os `k` mais próximos. Ambos aceitam `key=` pra extrair
a `Coordinate` de objetos seus:

```python
from dataclasses import dataclass

from tempest_fastapi_sdk.geo import Coordinate, nearest, within_radius


@dataclass
class Loja:
    """A store of yours, holding its coordinate in a field of its own."""

    nome: str
    location: Coordinate


store_a = Loja("Boa Viagem", Coordinate(latitude=-8.0476, longitude=-34.8770))
store_b = Loja("Olinda", Coordinate(latitude=-7.9899, longitude=-34.8386))
store_c = Loja("Jaboatão", Coordinate(latitude=-8.1130, longitude=-34.9060))

center = Coordinate(latitude=-23.55, longitude=-46.63)
stores = [store_a, store_b, store_c]

perto = within_radius(center, stores, 5.0, key=lambda s: s.location)
top3 = nearest(center, stores, k=3, key=lambda s: s.location)
```

!!! note "Raio é uma pré-filtragem barata"
    A linha reta subestima a distância rodoviária: use um raio um pouco
    maior que o alvo e refine com `estimate_travel`/OSRM só nos finalistas.

## Busca por raio no banco (`GeoRepositoryMixin`)

Pra buscar num raio direto do banco, misture `GeoPointMixin` no modelo e
`GeoRepositoryMixin` no repositório. O `nearby` faz **pré-filtro por
bounding-box em SQL** (indexado) e refina com Haversine em Python:

```python
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String

from tempest_fastapi_sdk import BaseModel, BaseRepository
from tempest_fastapi_sdk.geo import Coordinate, GeoPointMixin, GeoRepositoryMixin


class StoreModel(GeoPointMixin, BaseModel):
    __tablename__ = "stores"
    name: Mapped[str] = mapped_column(String(120))


class StoreRepository(GeoRepositoryMixin, BaseRepository[StoreModel]):
    ...


async def nearby_stores(repo: StoreRepository, center: Coordinate) -> list[StoreModel]:
    # Lojas ativas num raio de 5 km, mais próxima primeiro, no máximo 20.
    return await repo.nearby(
        center,
        radius_km=5.0,
        extra_filters={"is_active": True},
        limit=20,
    )
```

!!! tip "PostGIS quando o volume cresce"
    Com Postgres + extensão PostGIS, troque por `PostGISRepositoryMixin`:
    o `nearby` empurra o filtro e a ordenação por distância pro banco via
    `ST_DWithin` / `ST_Distance` — sem dependência Python extra. A
    assinatura **não** é a mesma: o `nearby` do PostGIS aceita `center`,
    `radius_km`, `limit`, `latitude_field` e `longitude_field`, sem
    `extra_filters` (o exemplo acima, com `is_active`, não roda nele), e o
    mixin não tem `paginate_nearby`.

## Paginação por raio no banco (`paginate_nearby`)

O `nearby` carrega a bounding box inteira e ordena em Python — ótimo para
"as 20 lojas mais perto", ruim para uma **listagem paginada**: página 3 de
uma busca por raio significaria trazer tudo e fatiar em memória. O
`paginate_nearby` deixa tudo no banco: a distância Haversine vira uma
**expressão SQL** (`haversine_distance_sql`), o raio vira `WHERE` sobre ela
(atrás do pré-filtro de bounding box, que o índice cobre), e ordenação,
`COUNT` e `OFFSET`/`LIMIT` rodam lá. Sem PostGIS — funciona em PostgreSQL
puro e em SQLite.

```python
import asyncio

from sqlalchemy import Float, String
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column
from tempest_fastapi_sdk import BaseModel, BaseRepository
from tempest_fastapi_sdk.geo import Coordinate, GeoRepositoryMixin


class EventModel(BaseModel):
    __tablename__ = "events_nearby_demo"

    name: Mapped[str] = mapped_column(String(80))
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)


class EventRepository(GeoRepositoryMixin, BaseRepository[EventModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, model=EventModel)


async def main() -> None:
    """Run this example."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(EventModel.metadata.create_all)
    async with AsyncSession(engine) as session:
        repo = EventRepository(session)
        await repo.add_all(
            [
                EventModel(name="Sé", latitude=-23.5505, longitude=-46.6333),
                EventModel(name="Paulista", latitude=-23.5614, longitude=-46.6559),
                EventModel(name="Santos", latitude=-23.9608, longitude=-46.3336),
                EventModel(name="Rio", latitude=-22.9068, longitude=-43.1729),
                EventModel(name="Sem pino", latitude=None, longitude=None),
            ],
        )
        center = Coordinate(latitude=-23.5505, longitude=-46.6333)
        page = await repo.paginate_nearby(center, 100.0, page=1, page_size=2)
        for event, distance_km in page["items"]:
            print(f"{event.name}: {distance_km:.1f} km")
        print(page["total"], page["pages"])
    await engine.dispose()


asyncio.run(main())
```

Saída:

```text
Sé: 0.0 km
Paulista: 2.6 km
3 2
```

Pedaço por pedaço:

- O retorno é o **envelope de paginação do SDK** (`items`, `total`, `page`,
  `page_size`, `pages`) — a mesma forma do `paginate`. Cada item é um
  `NearbyMatch`, uma tupla nomeada `(row, distance_km)`: desempacota no `for`
  ou lê por nome (`match.row`, `match.distance_km`). A distância é a que o
  banco calculou e usou para ordenar, então nunca discorda da ordem.
- Linha com latitude ou longitude `NULL` não entra ("Sem pino" ficou de
  fora). Empate de distância desempata por `id`, então as páginas são
  estáveis.
- `extra_filters=` (o vocabulário do `paginate`), `where=` (um `Q`) e
  `query=` (um `select` próprio cuja primeira entidade é o model) se somam ao
  raio. `latitude_field=`/`longitude_field=` apontam para colunas com outro
  nome; nome que não é coluna mapeada levanta `ValueError`.
- `page_size` respeita o `max_page_size` do repository, quando declarado
  (`PageSizeTooLargeException`). Ver
  [Paginação](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/database/pagination.md#quais-colunas-ordenam-e-quantas-linhas-cabem-numa-pagina).

Para devolver isso numa rota, mapeie cada par para o schema de resposta e
reaproveite os metadados do envelope:

```python
from typing import Any

from pydantic import Field
from tempest_fastapi_sdk import BasePaginationSchema, BaseSchema


class EventNearbyResponse(BaseSchema):
    """Evento com a distância até o ponto buscado."""

    name: str = Field(description="Nome do evento.")
    distance_km: float = Field(description="Distância em km.")


def to_response(page: dict[str, Any]) -> BasePaginationSchema[EventNearbyResponse]:
    """Map a paginate_nearby result to the API envelope."""
    return BasePaginationSchema[EventNearbyResponse](
        items=[
            EventNearbyResponse(name=row.name, distance_km=distance_km)
            for row, distance_km in page["items"]
        ],
        total=page["total"],
        page=page["page"],
        page_size=page["page_size"],
        pages=page["pages"],
    )
```

!!! info "Detalhes técnicos: a expressão e o clamp"
    `haversine_distance_sql(lat, lng, center)` usa só `sin`, `cos`, `asin`,
    `sqrt` e aritmética — graus viram radianos multiplicando por uma
    constante, então nem `radians()` é exigido. O SQLite precisa ter as
    funções matemáticas compiladas (`SQLITE_ENABLE_MATH_FUNCTIONS`, 3.35+).
    Confira o seu com `SELECT sin(1), asin(1), sqrt(4)` — o SQLite 3.47.1 do
    CPython 3.13.3 que o `uv` instala (python-build-standalone) responde.

    O termo do Haversine passa de 1 por erro de ponto flutuante: em pares
    antípodas aleatórios saiu `1.0000000000000002` em cerca de 4% de
    2 000 000 sorteios, no PostgreSQL 16 e no SQLite. `sqrt` arredonda esse
    valor de volta para `1.0`, mas `asin` acima de 1 é `NULL` no SQLite e
    `ERROR: input is out of range` no PostgreSQL — por isso o termo é
    limitado a `[0, 1]` com um `CASE` antes, e o resultado não depende
    desse arredondamento.

!!! warning "Antimeridiano"
    Como no `nearby`, a bounding box é limitada em ±180 de longitude: um
    círculo que cruza o antimeridiano perde o outro lado.

## Geocoding (endereço ↔ coordenada)

`NominatimBackend` resolve endereço → coordenada (e reverso) via
OpenStreetMap Nominatim, grátis. Cliente `httpx` injetado, igual OSRM:

```python
import asyncio

import httpx

from tempest_fastapi_sdk.geo import Coordinate, NominatimBackend


async def main() -> None:
    """Run this example."""
    async with httpx.AsyncClient() as client:
        geocoder = NominatimBackend(http_client=client, user_agent="meu-app/1.0")
        hit = await geocoder.geocode("Av. Paulista, 1578, São Paulo")
        if hit:
            print(hit.coordinate, hit.display_name)
        lugar = await geocoder.reverse(Coordinate(latitude=-23.561, longitude=-46.656))


asyncio.run(main())
```

!!! warning "Política do Nominatim público"
    O `nominatim.openstreetmap.org` exige `User-Agent` descritivo e limita
    a ~1 req/s. Em caminho de request, envolva o backend com
    [`CachedGeocodingBackend`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils/geo.md#tempest_fastapi_sdk.geo.CachedGeocodingBackend)
    — a seção abaixo monta esse cache — ou suba uma instância própria antes
    de escalar.

## Cache de geocoding (`CachedGeocodingBackend`)

A política acima manda cachear geocoding que passa pelo caminho de request —
e a regra vale para qualquer provedor de endereço, pago ou não: geocoding é o
tipo de chamada em que o mesmo endereço é perguntado de novo. O
`CachedGeocodingBackend` envolve qualquer [`GeocodingBackend`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils/geo.md#tempest_fastapi_sdk.geo.GeocodingBackend)
— o `NominatimBackend`, o `FakeGeocodingBackend` do teste, um cliente de
provedor pago — e serve hits do cache sem tocar no backend:

```python
import asyncio

import httpx

from tempest_fastapi_sdk.geo import (
    CachedGeocodingBackend,
    Coordinate,
    InMemoryGeocodeCacheStore,
    NominatimBackend,
)


async def main() -> None:
    """Run this example."""
    async with httpx.AsyncClient() as client:
        geocoder = NominatimBackend(http_client=client, user_agent="meu-app/1.0")
        cached = CachedGeocodingBackend(
            backend=geocoder,
            store=InMemoryGeocodeCacheStore(),
        )
        for _ in range(10):
            await cached.reverse(Coordinate(latitude=-5.089, longitude=-42.802))


asyncio.run(main())
```

Dez chamadas ao mesmo ponto; só a primeira chega ao `NominatimBackend`. As
chaves são estáveis e não guardam a leitura crua — coordenada redonda e texto
com hash:

```
geocoding:reverse:v1:<latitude com 3 casas>:<longitude com 3 casas>
geocoding:search:v1:<SHA-256 do texto normalizado>
```

### Redondeio: 3 casas é ~111 m de grade

`reverse` arredonda a coordenada para 3 casas **antes** de chavear **e antes**
de chamar o backend — a leitura exata fica só no processo que a recebeu. A
tabela abaixo é a distância medida entre um grau inteiro de latitude a −5° e
cada vizinho mais fino
([`haversine_km`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils/geo.md#tempest_fastapi_sdk.geo.haversine_km)):

| Casas | Lado da célula |
| --- | --- |
| 0 (grau inteiro) | 111 km |
| 1 | 11,1 km |
| 2 | 1,11 km |
| 3 | 111 m |
| 4 | 11 m |
| 5 | 1,1 m |

Três casas é ~111 m: o tamanho de um quarteirão, o grão em que o mesmo
endereço resolve para o mesmo prédio. Duas leituras de GPS a ~40 m uma da
outra caem na mesma chave (medido), então o hit usa 1/3 das casas — mais
casas fragmentariam o cache sem melhorar a resposta. E `-0.0001` e `0.0001`
caem ambos em `0.000`: o `-0.0` nunca divide a chave em duas entradas.

### Backend que responde nada

Backend que não acha endereço responde `None` — "não há lugar aqui". Por
padrão isso **não** é cacheado: o Nominatim pode vir a indexar o lugar, e a
pergunta custa 1 req/s. Com `cache_misses=True` o wrapper guarda o vazio na
chave e a segunda pergunta custa zero — ligue quando a ausência for estável:
um endereço que você tem certeza que não existe na base.

### Store: memória, Redis, o seu

`InMemoryGeocodeCacheStore` é o store dos testes e do dev: um `dict` com TTL
checado na leitura — expira no processo, não no Redis. Produção compartilha o
cache entre réplicas, no Redis:

```python
import asyncio

import httpx

from tempest_fastapi_sdk.cache import AsyncRedisManager
from tempest_fastapi_sdk.geo import (
    CachedGeocodingBackend,
    Coordinate,
    NominatimBackend,
    RedisGeocodeCacheStore,
)


async def main() -> None:
    """Run this example."""
    manager = AsyncRedisManager("redis://localhost:6379/0")
    await manager.connect()
    try:
        async with httpx.AsyncClient() as client:
            geocoder = NominatimBackend(http_client=client, user_agent="meu-app/1.0")
            cached = CachedGeocodingBackend(
                backend=geocoder,
                store=RedisGeocodeCacheStore(manager.client_proxy),
            )
            await cached.reverse(Coordinate(latitude=-5.089, longitude=-42.802))
    finally:
        await manager.disconnect()


asyncio.run(main())
```

O TTL default é 30 dias (`DEFAULT_GEOCODE_CACHE_TTL_SECONDS`) e o prefixo de
chave é `geocoding` (`DEFAULT_GEOCODE_CACHE_KEY_PREFIX`) — ajuste para a sua
instância com `ttl_seconds=` e `key_prefix=`. Store seu: implemente
[`GeocodeCacheStore`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils/geo.md#tempest_fastapi_sdk.geo.GeocodeCacheStore)
como uma classe com `get(key) -> str | None` e `set(key, value, ttl_seconds)`
— o contrato inteiro.

Falha de leitura ou escrita no cache, payload que não parseia: tudo é logado
no logger `tempest_fastapi_sdk.geo.cache` e degrada para "perguntar de novo",
jamais vira erro para o request. A única exceção que sobe é a do próprio
backend — o cache não esconde o que a fonte respondeu.

### Recap

- `CachedGeocodingBackend(backend=..., store=...)` envolve qualquer
  `GeocodingBackend`; só a primeira chamada por chave chega ao backend.
- `reverse` arredonda a coordenada a 3 casas (~111 m, medido) antes de
  chavear e de chamar o backend; o texto de `geocode` entra na chave via
  SHA-256, sem o texto cru.
- `InMemoryGeocodeCacheStore` para testes e dev; `RedisGeocodeCacheStore`
  para cache compartilhado; implemente `GeocodeCacheStore` para o seu.
- `cache_misses=True` passa a cachear "não encontrado"; falha do backend sobe
  sem virar hit.

## Endereço estruturado (`GeocodeAddress`)

`GeocodeResult.display_name` é um texto só para exibir na tela: gravar cidade
e UF em colunas separadas a partir dele é parser de string, e formato de
endereço é justamente o tipo de coisa que muda. Como `NominatimBackend` pede
`addressdetails=1` nas duas direções, o resultado traz
[`GeocodeAddress`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils/geo.md#tempest_fastapi_sdk.geo.GeocodeAddress)
em `hit.address`, cada parte no seu campo:

```python
import asyncio

import httpx

from tempest_fastapi_sdk.geo import Coordinate, NominatimBackend


async def main() -> None:
    """Run this example."""
    async with httpx.AsyncClient() as client:
        geocoder = NominatimBackend(http_client=client, user_agent="meu-app/1.0")
        hit = await geocoder.reverse(Coordinate(latitude=-5.0892, longitude=-42.8019))
        if hit and hit.address:
            print(hit.address.city)
            print(hit.address.state)
            print(hit.address.state_code)
            print(hit.address.country_code)
            print(hit.address.postcode)


asyncio.run(main())
```

Saída — do que o servidor respondeu para essas coordenadas (payload gravado
em 07/10/2026, Memorial Esperança Garcia, Teresina/PI):

```text
Teresina
Piauí
PI
BR
64001-490
```

Os seis campos são opcionais: o servidor devolve só o que sabe sobre o lugar
encontrado. `city` é o primeiro entre `city`, `town`, `village` e
`municipality` presente na resposta — a ordem foi medida, não escolhida: num
mesmo ponto de São José da Tenda/PI vêm `town` (o município), `village` (a
sede) e `hamlet` juntos, e pegar a primeira chave que aparecer gravaria a
vila no lugar do município. `state_code` é a parte depois do `-` de
`ISO3166-2-lvl4` (`"BR-PI"` → `"PI"`) e `country_code` vem maiúsculo
(`"br"` → `"BR"`) porque o servidor o escreve minúsculo.

`address` fica `None` quando a resposta não traz objeto de endereço — o caso
de um backend que não manda um, sem virar exceção nem campo obrigatório.

## Matriz de distância e geometria da rota

O OSRM faz mais que ponto-a-ponto: `matrix` calcula N×M numa chamada
(roteirização, "entregador mais próximo") e `route(..., with_geometry=True)`
devolve a linha da rota decodificada em `TravelEstimate.geometry`:

```python
import asyncio

import httpx

from tempest_fastapi_sdk.geo import Coordinate, OSRMBackend

store_a = Coordinate(latitude=-8.0476, longitude=-34.8770)
a = store_a

store_b = Coordinate(latitude=-7.9899, longitude=-34.8386)
b = store_b

client = httpx.AsyncClient()


def desenhar_no_mapa(linha: list[Coordinate]) -> None:
    """Render the route line on your map widget."""


destinos = [
    Coordinate(latitude=-7.9899, longitude=-34.8386),
    Coordinate(latitude=-8.0631, longitude=-34.8711),
    Coordinate(latitude=-8.1127, longitude=-34.9156),
]

origem = Coordinate(latitude=-8.0476, longitude=-34.8770)
origens = [origem]


backend = OSRMBackend(http_client=client)


async def main() -> None:
    """Run this example."""
    matriz = await backend.matrix(origens, destinos)  # DistanceMatrix
    print(matriz.durations_minutes[0][2])  # tempo origem 0 → destino 2

    rota = await backend.route(a, b, with_geometry=True)
    desenhar_no_mapa(rota.geometry)  # list[Coordinate]


asyncio.run(main())
```

`encode_polyline` / `decode_polyline` convertem a linha pro formato
compacto do Google/OSRM (precision 5 ou 6), sem dependência.

## Rota com paradas (`route_through`)

`route` traça origem → destino. Uma viagem com paradas (A → B → C) — a linha
de ônibus, a entrega com várias coletas — sai numa chamada só com
`route_through`: o OSRM recebe todos os pontos e devolve o total **e** um
trecho (`legs`) por par consecutivo, com distância e tempo de cada um.

```python
import asyncio

import httpx

from tempest_fastapi_sdk.geo import (
    Coordinate,
    OSRMBackend,
    RouteThroughResult,
    TravelModeEnum,
    estimate_route_through,
)

PARADAS: list[Coordinate] = [
    Coordinate(latitude=-5.0892, longitude=-42.8019),
    Coordinate(latitude=-5.0381, longitude=-42.4608),
    Coordinate(latitude=-4.8278, longitude=-42.1686),
]


async def rota_com_paradas(client: httpx.AsyncClient) -> RouteThroughResult:
    """Traça a rota pelas paradas no OSRM e cai na estimativa offline."""
    backend = OSRMBackend(http_client=client)
    try:
        return await backend.route_through(PARADAS, mode=TravelModeEnum.BUS)
    except RuntimeError:
        return estimate_route_through(PARADAS, TravelModeEnum.BUS)


def osrm_fake(request: httpx.Request) -> httpx.Response:
    """Responde como o OSRM: dois trechos e o total."""
    legs = [
        {"distance": 41_250.0, "duration": 2_100.0},
        {"distance": 39_800.0, "duration": 1_980.0},
    ]
    route = {"distance": 81_050.0, "duration": 4_080.0, "legs": legs}
    return httpx.Response(200, json={"code": "Ok", "routes": [route]})


def osrm_fora_do_ar(request: httpx.Request) -> httpx.Response:
    """Simula o servidor indisponível."""
    return httpx.Response(503)


async def main() -> None:
    """Roda a mesma função com o OSRM respondendo e fora do ar."""
    for handler in (osrm_fake, osrm_fora_do_ar):
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            rota = await rota_com_paradas(client)
        trechos = [f"{leg.distance_km:.1f} km" for leg in rota.legs]
        print(rota.source, f"{rota.distance_km:.1f} km", f"{rota.duration_minutes:.0f} min", trechos)


asyncio.run(main())
```

Saída medida (o primeiro caso é um servidor OSRM **fake**, montado com
`httpx.MockTransport`, para o exemplo rodar sem rede; o segundo é o mesmo
cliente recebendo `503`):

```text
osrm 81.0 km 109 min ['41.2 km', '39.8 km']
heuristic 101.6 km 195 min ['49.7 km', '51.9 km']
```

O que acontece aí:

- **Um `RouteThroughResult`**: `distance_km`, `duration_minutes`, `source`,
  `geometry` e `legs: list[TravelEstimate]`. `legs[i]` vai de `PARADAS[i]` a
  `PARADAS[i + 1]`, então N pontos dão N − 1 trechos, e a soma dos trechos
  fecha com o total.
- **O fator do modo vale em tudo.** Com `TravelModeEnum.BUS`, o total e cada
  trecho têm a duração do carro × 1,6, igual ao `route` (68 min de carro
  viraram 109 min no exemplo).
- **`with_geometry=True`** decodifica a linha inteira da rota em `geometry`.
  Os trechos não carregam geometria.
- **O fallback offline** é `estimate_route_through`: soma `estimate_travel`
  por trecho, com `source="heuristic"` e, com `with_geometry=True`, a
  geometria em linha reta (os próprios pontos).

!!! info "Limite de pontos"
    O demo público respondeu `"Ok"` com 500 coordenadas e
    `{"code": "TooBig", "message": "Number of entries 501 is higher than
    current maximum (500)"}` com 501 (medido em 2026-11-18). Por isso
    `DEFAULT_OSRM_MAX_POINTS` é `500`, e acima dele `route_through` levanta
    `ValueError` **antes** de sair qualquer requisição. Servidor próprio com
    outro `--max-viaroute-size`? Passe `OSRMBackend(..., max_points=N)`.

`route_through` e `matrix` fazem parte do Protocol `RoutingBackend` desde a
mesma versão, e o `FakeRoutingBackend` implementa os dois — um backend seu
precisa dos três métodos para continuar passando no `isinstance`.

## Geometria: projeção, geofence, comprimento

```python
from tempest_fastapi_sdk.geo import (
    Coordinate,
    bounding_box,
    destination_point,
    initial_bearing,
    path_length_km,
    point_in_polygon,
    polygon_area_km2,
)

ponto = Coordinate(latitude=-8.0476, longitude=-34.8770)
center = ponto


pontos_do_gps = [ponto, Coordinate(latitude=-7.9899, longitude=-34.8386)]

zona_de_entrega = bounding_box(ponto, radius_km=5)

poligono_da_zona = [
    Coordinate(
        latitude=zona_de_entrega.min_latitude,
        longitude=zona_de_entrega.min_longitude,
    ),
    Coordinate(
        latitude=zona_de_entrega.min_latitude,
        longitude=zona_de_entrega.max_longitude,
    ),
    Coordinate(
        latitude=zona_de_entrega.max_latitude,
        longitude=zona_de_entrega.max_longitude,
    ),
    Coordinate(
        latitude=zona_de_entrega.max_latitude,
        longitude=zona_de_entrega.min_longitude,
    ),
]


alvo = destination_point(center, bearing_degrees=90.0, distance_km=2.0)  # 2 km a leste
rumo = initial_bearing(center, alvo)  # ~90.0
dentro = zona_de_entrega.contains(ponto)  # geofence: caixa, teste barato
no_poligono = point_in_polygon(ponto, poligono_da_zona)  # geofence: ring
area = polygon_area_km2(poligono_da_zona)
percorrido = path_length_km(pontos_do_gps)
```

## Brasil: centroide por UF, CEP e endereço → coordenada

```python
import asyncio

import httpx

from tempest_fastapi_sdk.geo import NominatimBackend, cep_to_coordinate, uf_centroid

geocoder = NominatimBackend(http_client=httpx.AsyncClient())


pino = uf_centroid("SP")  # centro aproximado do estado, offline


async def main() -> None:
    """Run this example."""
    coord = await cep_to_coordinate("01310-100", geocoder=geocoder)  # via Nominatim


asyncio.run(main())
```

### Endereço em texto livre: `resolve_br_coordinate`

Cadastro brasileiro costuma guardar o endereço num campo só, com o CEP em
algum lugar do meio (`"Av. Paulista, 1578 - 01310-200"`). O
`resolve_br_coordinate` tenta três fontes, da mais precisa para a mais
grossa, e devolve a primeira que responde:

1. o **CEP** achado em `address` ou `complement` (`extract_cep`), via
   `cep_to_coordinate`;
2. o **endereço completo** — `"endereço, cidade, UFEnum, Brasil"`, sem as
   partes vazias — geocodificado;
3. o **centroide da UF** (`uf_centroid`), offline.

```python
import asyncio

from tempest_fastapi_sdk.geo import extract_cep, resolve_br_coordinate

print(extract_cep("Av. Frei Serafim, 2280", "CEP 64001020, sala 4"))


async def main() -> None:
    """Run this example."""
    point = await resolve_br_coordinate(
        geocoder=None,
        uf="pi",
        address="Av. Frei Serafim, 2280",
        city="Teresina",
    )
    print(point)
    print(await resolve_br_coordinate(geocoder=None, uf="ZZ"))


asyncio.run(main())
```

Saída:

```text
64001-020
latitude=-7.4 longitude=-42.5
None
```

- `geocoder=None` pula direto para o centroide — o caminho offline, para
  teste e para deploy sem geocoding. UF desconhecida devolve `None`.
- **Falha do geocoder nunca sobe.** Qualquer exceção de `geocode` é logada
  em `WARNING` (com traceback) e a cadeia segue para o próximo passo; o
  pior desfecho é o centroide do estado.
- **Retry é do geocoder que você injeta.** A função só vê a falha depois que
  as suas tentativas acabaram, então embrulhe o `geocode`:

```python
import httpx
from tempest_fastapi_sdk import RetryPolicy, async_retry
from tempest_fastapi_sdk.geo import (
    Coordinate,
    GeocodeResult,
    GeocodingBackend,
    NominatimBackend,
)


class RetryingGeocoder:
    """Geocoder que retenta erro de transporte antes de desistir."""

    def __init__(self, inner: GeocodingBackend) -> None:
        self._inner = inner

    @async_retry(RetryPolicy(max_attempts=3), (httpx.HTTPError,))
    async def geocode(self, query: str) -> GeocodeResult | None:
        """Forward to the wrapped backend, retrying transport errors."""
        return await self._inner.geocode(query)

    async def reverse(self, coordinate: Coordinate) -> GeocodeResult | None:
        """Forward reverse geocoding unchanged."""
        return await self._inner.reverse(coordinate)


geocoder = RetryingGeocoder(
    NominatimBackend(
        http_client=httpx.AsyncClient(timeout=10.0),
        user_agent="meu-servico/1.0 (ops@example.com)",
    ),
)
```

!!! warning "Nominatim público fora do caminho da request"
    A instância pública limita em ~1 req/s e exige `User-Agent` próprio.
    Resolva a coordenada em background (depois de gravar o registro), não
    no request do usuário.

## Cidade e estado do usuário (`BRLocationMixin`) *(v0.367.0+)*

Para saber **onde** estão os seus usuários (ou lojas, ou pedidos) no Brasil,
misture o `BRLocationMixin` no model. Ele adiciona três colunas, todas
anuláveis (uma tabela existente ganha as três numa migration simples):

| Coluna | Tipo | O que guarda |
| --- | --- | --- |
| `city` | `String(64)` | o nome oficial do IBGE (`"São Paulo"`) |
| `state` | `String(2)`, indexada | a sigla da UF (`"SP"`) |
| `city_ibge_code` | `Integer`, indexada | o código IBGE de 7 dígitos (`3550308`) |

Grave sempre por `set_br_location(cidade, uf)`: ele procura a cidade nos
5571 municípios que o SDK já traz, sem rede, aceita qualquer caixa e
acentuação, e guarda a grafia oficial e o código. Cidade que a UF não tem
levanta `UnknownMunicipalityError`, e a linha fica como estava.

```python
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tempest_fastapi_sdk import BaseUserModel
from tempest_fastapi_sdk.geo import (
    BRLocationMixin,
    BRLocationSchema,
    Coordinate,
    UnknownMunicipalityError,
    br_location_distribution,
    render_br_map_svg,
    uf_at,
)


class UserModel(BRLocationMixin, BaseUserModel):
    """Users with the city and state they live in."""

    __tablename__ = "users"


async def main() -> None:
    """Store three users, refuse a wrong city and count per state and city."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(UserModel.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await store(session)
        result = await br_location_distribution(session, UserModel)
    print(result.total, result.located, result.unlocated)
    print(result.state_counts())
    print([(city.city, city.state, city.count) for city in result.cities])
    print(BRLocationSchema(city="belo horizonte", state="MG"))
    print(uf_at(Coordinate(latitude=-15.79, longitude=-47.88)))
    print(render_br_map_svg(result.state_counts())[:60])
    await engine.dispose()


async def store(session: AsyncSession) -> None:
    """Add the users; the last one has no location yet."""
    for email, city, state in [
        ("ana@example.com", "sao paulo", "SP"),
        ("bia@example.com", "SÃO PAULO", "sp"),
        ("caio@example.com", "Recife", "PE"),
    ]:
        user = UserModel(email=email, hashed_password="")
        user.set_br_location(city, state)
        session.add(user)
    try:
        UserModel(email="x@example.com", hashed_password="").set_br_location(
            "Recife", "SP"
        )
    except UnknownMunicipalityError as error:
        print(error)
    session.add(UserModel(email="dani@example.com", hashed_password=""))
    await session.commit()


asyncio.run(main())
```

Saída medida:

```text
SP has no municipality named 'Recife'
4 3 1
{'SP': 2, 'PE': 1}
[('São Paulo', 'SP', 2), ('Recife', 'PE', 1)]
city='Belo Horizonte' state='MG' city_ibge_code=3106200
DF
<svg class="tempest-br-map" viewBox="0 0 1000 969" role="img
```

Pedaço por pedaço:

- `set_br_location("sao paulo", "SP")` e `set_br_location("SÃO PAULO", "sp")`
  gravam o mesmo `"São Paulo"`, então a contagem não se divide por grafia.
- `BRLocationSchema` faz a mesma checagem na borda da API: numa rota FastAPI,
  cidade inexistente na UF responde `422` antes do handler, e a válida volta
  com o nome oficial e o `city_ibge_code`.
- `br_location_distribution(session, Model)` conta em três consultas
  agregadas (total, `GROUP BY state`, `GROUP BY city_ibge_code`), sem carregar
  as linhas. `where=` filtra todas elas e `city_limit=` corta as menores
  cidades.
- `uf_at(coordenada)` responde em qual UF um ponto cai, pelos contornos dos
  estados que o SDK também traz (malha do IBGE na qualidade mínima, cerca de
  5 000 pontos para o país).
- `render_br_map_svg(contagens, pontos)` desenha o mapa do Brasil com cada UF
  num tom e uma bolha por cidade — é o que a página do admin usa (veja
  [Mapa de distribuição](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin/dashboard.md#mapa-de-distribuicao-geografica-location_maps-v03670)).

!!! note "Os contornos são simplificados"
    Na malha mínima do IBGE, 52 dos 5571 municípios (0,93%) caem fora do
    contorno da própria UF: são cidades no litoral ou na divisa. As 27
    capitais caem dentro. Para o mapa isso não importa, porque a bolha usa a
    coordenada do município; para decidir a UF de um endereço na divisa, não
    use `uf_at`.

!!! tip "A escala de tons é logarítmica"
    Com um estado dominando, a escala linear achata o mapa: na validação em
    browser (SP com 184 usuários, os demais entre 1 e 40), 22 dos 25 estados
    com usuários ficavam no tom mais claro, a faixa 1–36. O tom `n`
    vai até `pico ** (n / 5)`; a legenda (`br_map_legend`) mostra as faixas.

## Recap

- `haversine_km(a, b)` — distância great-circle, pura, sempre disponível.
- `bounding_box` / `within_radius` / `nearest` — proximidade offline; `key=` pra objetos seus.
- `GeoPointMixin` + `GeoRepositoryMixin.nearby` — busca por raio no banco (PostGIS via `PostGISRepositoryMixin`, sem `extra_filters` nem `paginate_nearby`).
- `GeoRepositoryMixin.paginate_nearby` — raio, ordenação, `COUNT` e página no SQL, sem PostGIS; cada item é `NearbyMatch(row, distance_km)`.
- `NominatimBackend` — geocoding endereço↔coordenada, grátis, `httpx` injetado.
- `OSRMBackend.matrix` / `route(with_geometry=True)` — matriz N×M e linha da rota; `encode_polyline`/`decode_polyline`.
- `OSRMBackend.route_through` / `estimate_route_through` — rota com paradas numa chamada, um `leg` por trecho, fallback offline e limite de pontos (`DEFAULT_OSRM_MAX_POINTS`).
- `destination_point` / `initial_bearing` / `point_in_polygon` / `polygon_area_km2` / `path_length_km` — geometria offline.
- `uf_centroid` / `cep_to_coordinate` — atalhos Brasil.
- `BRLocationMixin` / `BRLocationSchema` / `br_location_distribution` — cidade e UF validadas contra o IBGE e a contagem por estado e cidade; `render_br_map_svg` / `uf_at` — o mapa do Brasil offline.
- `extract_cep` / `resolve_br_coordinate` — CEP de texto livre e a cadeia CEP → endereço → centroide da UF, sem levantar em falha do geocoder.
- `estimate_travel` / `OSRMBackend.route` — distância + tempo (`heuristic`/`osrm`); modos carro/moto/ônibus/bici/pedestre.
