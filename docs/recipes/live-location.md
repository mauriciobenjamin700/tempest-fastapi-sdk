# Localização ao vivo

Motorista dirigindo, passageiros acompanhando no mapa. Entregador a caminho,
cliente vendo "onde está o meu pedido". Frota na estrada, painel mostrando
cada veículo. O formato é sempre o mesmo:

1. quem se move **envia** a posição de tempos em tempos;
2. o serviço **limita** a frequência e guarda **só a última**;
3. a posição chega **apenas a quem pode ver** — e para de chegar quando a
   viagem acaba.

Escrever essa cola à mão sobre `cache` + `sse` + `geo` em cada serviço é
como as regras de segurança (quem publica, quem recebe, quando para) acabam
diferentes em cada um. `tempest_fastapi_sdk.geo` traz a peça pronta:
`LivePositionSchema`, `LivePositionStore` e `make_live_position_router`.

```bash
uv add "tempest-fastapi-sdk[cache,geo]"
```

`[cache]` traz o `redis`; `[geo]` traz o `httpx` do `OSRMBackend` (só
necessário para a ETA pela rota real).

## O exemplo completo: carona

Uma carona com motorista, dois passageiros que pagaram (cada um com o seu
ponto de embarque) e uma terceira pessoa que não pagou. Copie, rode com um
Redis local, e o `main()` percorre a viagem inteira num processo só:

```python
import asyncio
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI, Header
from redis.asyncio import Redis

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.exceptions import ForbiddenException
from tempest_fastapi_sdk.geo import (
    Coordinate,
    LivePositionStore,
    OSRMBackend,
    make_live_position_router,
)
from tempest_fastapi_sdk.sse import SSEBroker


@dataclass
class Ride:
    """A shared ride: who drives, whether it started, who paid and where they board."""

    driver_id: str
    started: bool = False
    paid_pickups: dict[str, Coordinate] = field(default_factory=dict)


RIDES: dict[str, Ride] = {
    "ride-42": Ride(
        driver_id="driver-1",
        paid_pickups={
            "ana": Coordinate(latitude=-5.0381, longitude=-42.4608),
            "bruno": Coordinate(latitude=-4.8278, longitude=-42.1686),
        },
    ),
}


async def paid_passengers(channel_id: str) -> list[str]:
    """Audience: only passengers who paid for this ride see the driver."""
    ride = RIDES.get(channel_id)
    return [] if ride is None else list(ride.paid_pickups)


async def pickup_points(channel_id: str) -> dict[str, Coordinate]:
    """Each paid passenger's boarding point, for their own ETA."""
    ride = RIDES.get(channel_id)
    return {} if ride is None else dict(ride.paid_pickups)


async def only_the_driver(channel_id: str, x_user_id: str = Header()) -> None:
    """Only the ride's driver may publish its position."""
    ride = RIDES.get(channel_id)
    if ride is None or ride.driver_id != x_user_id:
        raise ForbiddenException()


async def ride_started(channel_id: str) -> bool:
    """Positions are accepted only between start and finish."""
    ride = RIDES.get(channel_id)
    return ride is not None and ride.started


redis = Redis.from_url(
    os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    decode_responses=True,
)
broker = SSEBroker(heartbeat_seconds=None)
osrm_client = httpx.AsyncClient(timeout=10.0)
store = LivePositionStore(
    redis,
    key_prefix="rides:live",
    broker=broker,
    audience=paid_passengers,
    eta_targets=pickup_points,
    routing=OSRMBackend(http_client=osrm_client),
)

app = FastAPI()
register_exception_handlers(app)
app.include_router(
    make_live_position_router(
        store,
        authorize=only_the_driver,
        channel_active=ride_started,
        prefix="/api/rides",
    )
)


async def finish_ride(ride_id: str) -> None:
    """End the ride: stop accepting positions and forget the last one."""
    RIDES[ride_id].started = False
    await store.clear(ride_id)


async def main() -> None:
    """Drive the ride end to end in one process."""
    ana = broker.register("ana")
    carla = broker.register("carla")
    position = {
        "lat": -5.0892,
        "lng": -42.8019,
        "speed_kmh": 38.0,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    url = "/api/rides/ride-42/position"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as api:
        before = await api.post(url, json=position, headers={"X-User-Id": "driver-1"})
        print("before start:", before.status_code)

        RIDES["ride-42"].started = True
        intruder = await api.post(url, json=position, headers={"X-User-Id": "ana"})
        print("passenger publishing:", intruder.status_code)
        sent = await api.post(url, json=position, headers={"X-User-Id": "driver-1"})
        print("driver publishing:", sent.status_code, sent.json())
        again = await api.post(url, json=position, headers={"X-User-Id": "driver-1"})
        print("same spot again:", again.json())

    frame = await asyncio.wait_for(anext(ana.stream()), timeout=5.0)
    print(frame.decode().strip())
    try:
        await asyncio.wait_for(anext(carla.stream()), timeout=0.5)
    except TimeoutError:
        print("carla (did not pay): nothing")

    await finish_ride("ride-42")
    print("latest after finish:", await store.latest("ride-42"))
    print((await asyncio.wait_for(anext(ana.stream()), timeout=5.0)).decode().strip())

    await osrm_client.aclose()
    await redis.aclose()


asyncio.run(main())
```

Saída medida em 2026-11-18, com Redis 6.2 local e o demo público do OSRM
respondendo a ETA:

```text
before start: 409
passenger publishing: 403
driver publishing: 200 {'accepted': True}
same spot again: {'accepted': False}
event: live_position
data: {"channel_id": "ride-42", "position": {"lat": -5.0892, "lng": -42.8019, "accuracy_m": null, "speed_kmh": 38.0, "heading": null, "recorded_at": "2026-11-18T21:03:48.130416Z"}, "eta": {"target": {"latitude": -5.0381, "longitude": -42.4608}, "distance_km": 40.735099999999996, "duration_minutes": 37.755, "source": "osrm"}}
carla (did not pay): nothing
latest after finish: None
event: live_position_cleared
data: {"channel_id": "ride-42"}
```

Vamos por partes.

## A posição: `LivePositionSchema`

É o corpo que o aparelho envia: `lat`, `lng`, `accuracy_m`, `speed_kmh`,
`heading` e `recorded_at`. A validação recusa:

- coordenada fora de [-90, 90] / [-180, 180], `heading` fora de [0, 360),
  velocidade ou precisão negativas;
- `recorded_at` sem fuso horário;
- `recorded_at` mais de 30 segundos no futuro
  (`LIVE_POSITION_MAX_CLOCK_SKEW`) — alguns segundos de diferença entre o
  relógio do celular e o do servidor passam.

`position.coordinate` devolve um `Coordinate`, pronto para `haversine_km` ou
para o `OSRMBackend`.

## O que é guardado: `LivePositionStore`

O store guarda **só a última posição** de cada canal no Redis, na chave
`"<key_prefix>:<channel_id>"`, com TTL. `publish(channel_id, position)`
devolve `True` quando guardou e `False` quando recusou, nesta ordem:

| Recusa | Por quê | Default |
| --- | --- | --- |
| `recorded_at` não é posterior ao guardado | posição atrasada ou repetida | — |
| menos de `min_interval_seconds` desde a última aceita | throttle (tempo do servidor) | `5.0` |
| andou menos de `min_distance_m` | ruído do GPS parado | `10.0` |

No exemplo, `same spot again` volta `False`: mesma posição, mesmo
`recorded_at`, logo depois da primeira.

!!! tip "Parado no ponto não some do mapa"
    A posição recusada por distância **renova o TTL**. Um motorista
    esperando no ponto continua enviando, nada novo é publicado, e a última
    posição continua viva.

O TTL (`ttl_seconds`, default 300) é o que limpa o marcador sozinho quando o
aparelho para de enviar sem que ninguém encerre a viagem — app fechado,
bateria acabou.

`latest(channel_id)` lê a última posição (ou `None`), e `clear(channel_id)`
encerra: apaga na hora e avisa o público com o evento
`live_position_cleared`, para o cliente tirar o marcador da tela.

## Quem recebe: `audience`

Com um `SSEBroker`, cada posição aceita vira um evento `live_position` para
cada destinatário que o callback `audience(channel_id)` devolver — no
exemplo, só os passageiros que pagaram. A Carla, que não pagou, não recebe
nada.

O SDK **nunca** decide quem pode ver. Por isso `broker` e `audience` andam
juntos: passar um sem o outro levanta `ValueError` na construção. Não existe
"mandar para todo mundo" por esquecimento.

!!! info "O destinatário é um canal do broker"
    O store publica no canal com o nome que o `audience` devolve. O
    `make_sse_router` assina cada usuário no canal `str(user.id)` por padrão,
    então devolva ids de usuário e o stream autenticado de cada um já recebe
    — veja a receita de [SSE](sse.md).

## ETA até o ponto de embarque

Passe `eta_targets(channel_id) -> {destinatário: Coordinate}` e cada evento
leva a ETA **daquele** destinatário até o ponto dele. Com `routing=` (um
`OSRMBackend`), todos os pontos vão numa requisição só ao `matrix`; sem ele,
ou se o OSRM falhar, cada ETA sai do `estimate_travel`, com
`source="heuristic"` em vez de `"osrm"`.

Destinatário que o `eta_targets` cita e o `audience` não é ignorado: quem
manda em quem vê é o `audience`, sempre.

## O endpoint: `make_live_position_router`

Monta `POST <prefix>/{channel_id}/position`. As duas regras são
parâmetros **sem default**:

- `authorize` — dependency que levanta (`ForbiddenException` → `403`) quando
  quem chama não pode publicar naquele canal. Omitir o argumento é o
  `TypeError` do próprio Python, e passar `None` levanta `ValueError`: o
  router não é montado sem ela.
- `channel_active` — dependency que responde se o canal aceita posições
  agora. `False` responde `409` e nada é guardado.

As duas são dependencies comuns do FastAPI: declaram `channel_id: str` para
receber o parâmetro do path e podem depender do seu `current_user`.

A resposta de sucesso é só `{"accepted": true | false}`: a posição chega a
quem acompanha pelo `audience`, e por nenhum outro caminho. A única resposta
que repete parte do body é o `422` de validação do FastAPI (um `recorded_at`
no futuro, por exemplo), e ela vai só para quem enviou — depois do
`authorize`, então só ao publicador autorizado.

!!! warning "Vários workers"
    Com mais de um processo, crie o `SSEBroker(redis=..., channel_prefix=...)`
    e rode o `broker.run()` no lifespan, como na receita de [SSE](sse.md). O
    store não muda: ele só chama `broker.publish`. A entrega entre workers é
    a do `SSEBroker`, coberta na receita de SSE; não foi medida de novo
    através do store.

## Recap

- `LivePositionSchema` — `lat`/`lng`/`accuracy_m`/`speed_kmh`/`heading`/`recorded_at`, validados (futuro e sem fuso recusados).
- `LivePositionStore.publish` — guarda só a última, com TTL; throttle (`min_interval_seconds`), distância mínima (`min_distance_m`) e posição antiga recusados.
- `audience` obrigatório com `broker`: o serviço decide quem vê, o SDK entrega.
- `eta_targets` + `routing` — ETA por destinatário pelo `matrix`, com fallback para `estimate_travel`.
- `clear` — fim da viagem: apaga e avisa com `live_position_cleared`.
- `make_live_position_router` — `authorize` e `channel_active` obrigatórios; `403`/`409`; a resposta não carrega a posição.
