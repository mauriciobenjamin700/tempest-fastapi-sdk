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
print(round(km, 1))  # ~360.0
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

print(de_carro.distance_km, de_carro.duration_minutes)   # ex.: 8.2 9.8
print(de_onibus.duration_minutes)                        # maior (ônibus para)
print(de_carro.source)                                   # "heuristic"
```

Os padrões são ajustáveis por chamada:

```python
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
from tempest_fastapi_sdk.geo import Coordinate, nearest, within_radius

center = Coordinate(latitude=-23.55, longitude=-46.63)
stores = [store_a, store_b, store_c]  # objetos com .location: Coordinate

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

## Geocoding (endereço ↔ coordenada)

`NominatimBackend` resolve endereço → coordenada (e reverso) via
OpenStreetMap Nominatim, grátis. Cliente `httpx` injetado, igual OSRM:

```python
import httpx
from tempest_fastapi_sdk.geo import NominatimBackend

async with httpx.AsyncClient() as client:
    geocoder = NominatimBackend(http_client=client, user_agent="meu-app/1.0")
    hit = await geocoder.geocode("Av. Paulista, 1578, São Paulo")
    if hit:
        print(hit.coordinate, hit.display_name)
    lugar = await geocoder.reverse(Coordinate(latitude=-23.561, longitude=-46.656))
```

!!! warning "Política do Nominatim público"
    O `nominatim.openstreetmap.org` exige `User-Agent` descritivo e limita
    a ~1 req/s. Self-host pra escala.

## Matriz de distância e geometria da rota

O OSRM faz mais que ponto-a-ponto: `matrix` calcula N×M numa chamada
(roteirização, "entregador mais próximo") e `route(..., with_geometry=True)`
devolve a linha da rota decodificada em `TravelEstimate.geometry`:

```python
from tempest_fastapi_sdk.geo import OSRMBackend

backend = OSRMBackend(http_client=client)

matriz = await backend.matrix(origens, destinos)  # DistanceMatrix
print(matriz.durations_minutes[0][2])  # tempo origem 0 → destino 2

rota = await backend.route(a, b, with_geometry=True)
desenhar_no_mapa(rota.geometry)  # list[Coordinate]
```

`encode_polyline` / `decode_polyline` convertem a linha pro formato
compacto do Google/OSRM (precision 5 ou 6), sem dependência.

## Geometria: projeção, geofence, comprimento

```python
from tempest_fastapi_sdk.geo import (
    destination_point, initial_bearing, point_in_polygon,
    polygon_area_km2, path_length_km,
)

alvo = destination_point(center, bearing_degrees=90.0, distance_km=2.0)  # 2 km a leste
rumo = initial_bearing(center, alvo)                                     # ~90.0
dentro = point_in_polygon(ponto, zona_de_entrega)                        # geofence
area = polygon_area_km2(zona_de_entrega)
percorrido = path_length_km(pontos_do_gps)
```

## Brasil: centroide por UF e CEP → coordenada

```python
from tempest_fastapi_sdk.geo import cep_to_coordinate, uf_centroid

pino = uf_centroid("SP")  # centro aproximado do estado, offline
coord = await cep_to_coordinate("01310-100", geocoder=geocoder)  # via Nominatim
```

## Recap

- `haversine_km(a, b)` — distância great-circle, pura, sempre disponível.
- `bounding_box` / `within_radius` / `nearest` — proximidade offline; `key=` pra objetos seus.
- `GeoPointMixin` + `GeoRepositoryMixin.nearby` — busca por raio no banco (PostGIS via `PostGISRepositoryMixin`).
- `NominatimBackend` — geocoding endereço↔coordenada, grátis, `httpx` injetado.
- `OSRMBackend.matrix` / `route(with_geometry=True)` — matriz N×M e linha da rota; `encode_polyline`/`decode_polyline`.
- `destination_point` / `initial_bearing` / `point_in_polygon` / `polygon_area_km2` / `path_length_km` — geometria offline.
- `uf_centroid` / `cep_to_coordinate` — atalhos Brasil.
- `estimate_travel` / `OSRMBackend.route` — distância + tempo (`heuristic`/`osrm`); modos carro/moto/ônibus/bici/pedestre.
