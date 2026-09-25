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
    TravelMode,
    estimate_travel,
)

origem = Coordinate(latitude=-23.5505, longitude=-46.6333)
destino = Coordinate(latitude=-23.5015, longitude=-46.6553)

de_carro: TravelEstimate = estimate_travel(origem, destino, TravelMode.CAR)
de_onibus: TravelEstimate = estimate_travel(origem, destino, TravelMode.BUS)

print(de_carro.distance_km, de_carro.duration_minutes)   # 7.659817427032203 9.191780912438643
print(de_onibus.duration_minutes)                        # maior (ônibus para)
print(de_carro.source)                                   # "heuristic"
```

Os padrões são ajustáveis por chamada:

```python
from tempest_fastapi_sdk.geo import Coordinate, TravelMode, estimate_travel

destino = Coordinate(latitude=-7.9899, longitude=-34.8386)

origem = Coordinate(latitude=-8.0476, longitude=-34.8770)


estimate_travel(
    origem,
    destino,
    TravelMode.MOTORCYCLE,
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

from tempest_fastapi_sdk.geo import Coordinate, OSRMBackend, TravelMode

origem = Coordinate(latitude=-23.5505, longitude=-46.6333)
destino = Coordinate(latitude=-22.9068, longitude=-43.1729)


async def rota() -> None:
    """Consulta a rota real via servidor OSRM."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        backend = OSRMBackend(http_client=client)  # demo público por padrão
        estimativa = await backend.route(origem, destino, mode=TravelMode.CAR)
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
from tempest_fastapi_sdk.geo import Coordinate, TravelEstimate, TravelMode
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
    mode: TravelMode = TravelMode.CAR


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
    TravelMode,
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
        mode: TravelMode = TravelMode.CAR,
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
    `ST_DWithin` / `ST_Distance` — sem dependência Python extra, mesma
    assinatura.

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
  [Paginação](database.md#quais-colunas-ordenam-e-quantas-linhas-cabem-numa-pagina).

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
    a ~1 req/s. Self-host pra escala.

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


destino = Coordinate(latitude=-7.9899, longitude=-34.8386)
destinos = [destino]

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
2. o **endereço completo** — `"endereço, cidade, UF, Brasil"`, sem as
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

## Recap

- `haversine_km(a, b)` — distância great-circle, pura, sempre disponível.
- `bounding_box` / `within_radius` / `nearest` — proximidade offline; `key=` pra objetos seus.
- `GeoPointMixin` + `GeoRepositoryMixin.nearby` — busca por raio no banco (PostGIS via `PostGISRepositoryMixin`).
- `GeoRepositoryMixin.paginate_nearby` — raio, ordenação, `COUNT` e página no SQL, sem PostGIS; cada item é `NearbyMatch(row, distance_km)`.
- `NominatimBackend` — geocoding endereço↔coordenada, grátis, `httpx` injetado.
- `OSRMBackend.matrix` / `route(with_geometry=True)` — matriz N×M e linha da rota; `encode_polyline`/`decode_polyline`.
- `destination_point` / `initial_bearing` / `point_in_polygon` / `polygon_area_km2` / `path_length_km` — geometria offline.
- `uf_centroid` / `cep_to_coordinate` — atalhos Brasil.
- `extract_cep` / `resolve_br_coordinate` — CEP de texto livre e a cadeia CEP → endereço → centroide da UF, sem levantar em falha do geocoder.
- `estimate_travel` / `OSRMBackend.route` — distância + tempo (`heuristic`/`osrm`); modos carro/moto/ônibus/bici/pedestre.
