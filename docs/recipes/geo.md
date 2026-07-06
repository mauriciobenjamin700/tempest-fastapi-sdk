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

## Recap

- `haversine_km(a, b)` — distância great-circle, pura, sempre disponível.
- `estimate_travel(a, b, mode)` — distância + tempo offline (`source="heuristic"`).
- `OSRMBackend(http_client=...).route(a, b, mode=...)` — estrada real, grátis (`source="osrm"`), extra `[geo]`.
- Moto/ônibus derivam do carro via `DEFAULT_MODE_DURATION_FACTORS` — funciona nas duas camadas.
- `Coordinate` valida lat/long; `TravelEstimate` serializa direto na resposta FastAPI.
