---
description: "Use para tirar trânsito por trecho e ruas ausentes do mapa das posições GPS da frota; cobre `match_to_roads`, `TrafficAggregator` e as regras de privacidade."
---

# Trânsito da frota (GPS anônimo)

A frota já manda posição ao vivo (receita de [Localização ao vivo](live-location.md)).
Com essas mesmas posições dá para responder duas perguntas que um mapa comercial
cobra caro:

1. **Como está o trânsito agora?** Velocidade mediana por trecho de rua, nos
   últimos minutos.
2. **Que rua a frota usa e o mapa não tem?** Trechos com muitos rastros longe de
   qualquer rua do OpenStreetMap.

E uma terceira, sobre a própria ferramenta: **o encaixe na rua funciona nesta
frota?** Medida nos rastros reais, publicada só agregada (veja
[Qualidade do encaixe na frota real](#qualidade-do-encaixe-na-frota-real)).

O mapa é **híbrido**: o OpenStreetMap é a base (o grafo de ruas, os nomes), e o
GPS da frota é a camada viva. A decisão e as medições estão na issue
[tempest-react-sdk#667](https://github.com/mauriciobenjamin700/tempest-react-sdk/issues/667).

Rastro de GPS é dado pessoal: revela casa, trabalho e rotina. Por isso as regras
de privacidade (LGPD) aqui **são código**, não recomendação — corte do começo e
do fim de cada viagem, k-anonimato por trecho, nenhum rastro guardado e nenhum
id de aparelho em claro. Cada uma tem um teste.

```bash
uv add "tempest-fastapi-sdk[geo]"
```

Nada além disso: o grafo, o map-matching, a agregação e a inferência são Python
puro. `[cache]` só entra se você guardar o retrato do trânsito no Redis
(`RedisTrafficSnapshotStore`).

## O exemplo completo

Duas avenidas paralelas, 400 m uma da outra, num recorte do OSM. Os ônibus
descem a Av. Sul, sobem por uma rua **que o OSM não tem** e seguem pela Av.
Norte. Copie e rode — não precisa de rede nem de Redis:

```python
import asyncio
import math
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import FastAPI, Header

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.exceptions import ForbiddenException
from tempest_fastapi_sdk.geo import (
    Coordinate,
    InMemoryTrafficSnapshotStore,
    LivePositionSchema,
    OffRoadDensity,
    TrafficAggregator,
    TrafficPrivacy,
    infer_road_candidates,
    make_traffic_router,
    road_graph_from_overpass,
)

CENTER = Coordinate(latitude=-5.0892, longitude=-42.8019)
METERS_PER_DEGREE = 6371008.8 * math.pi / 180


def point(x: float, y: float) -> Coordinate:
    """A point x metres east and y metres north of the centre."""
    cos_lat = math.cos(math.radians(CENTER.latitude))
    return Coordinate(
        latitude=CENTER.latitude + y / METERS_PER_DEGREE,
        longitude=CENTER.longitude + x / (METERS_PER_DEGREE * cos_lat),
    )


def overpass_excerpt() -> dict[str, Any]:
    """Two parallel avenues 400 m apart, joined at both ends (no middle street)."""
    xs = [-600, 0, 300, 600, 900]
    corners = [(x, 0) for x in xs] + [(x, 400) for x in xs]
    nodes = [
        {
            "type": "node",
            "id": n,
            "lat": point(x, y).latitude,
            "lon": point(x, y).longitude,
        }
        for n, (x, y) in enumerate(corners, start=1)
    ]
    ways = [
        {
            "type": "way",
            "id": 100,
            "nodes": [1, 2, 3, 4, 5],
            "tags": {"highway": "primary", "name": "Av. Sul", "maxspeed": "50"},
        },
        {
            "type": "way",
            "id": 200,
            "nodes": [6, 7, 8, 9, 10],
            "tags": {"highway": "primary", "name": "Av. Norte"},
        },
        {
            "type": "way",
            "id": 300,
            "nodes": [1, 6],
            "tags": {"highway": "residential"},
        },
        {
            "type": "way",
            "id": 400,
            "nodes": [5, 10],
            "tags": {"highway": "residential"},
        },
    ]
    return {"elements": [*nodes, *ways]}


def route(second: int) -> tuple[float, float]:
    """10 m/s: east on Av. Sul, north through an unmapped street, east on Av. Norte."""
    d = 10.0 * second - 600
    if d <= 300:
        return d, 0.0
    if d <= 700:
        return 300.0, d - 300
    return 300.0 + d - 700, 400.0


async def ops_only(x_user_id: str = Header()) -> None:
    """Only the operations team reads traffic."""
    if x_user_id != "ops":
        raise ForbiddenException()


graph = road_graph_from_overpass(overpass_excerpt())
store = InMemoryTrafficSnapshotStore()
density = OffRoadDensity(CENTER)
start = datetime(2026, 11, 18, 7, tzinfo=UTC)
clock = [start.timestamp()]
traffic = TrafficAggregator(
    graph,
    privacy=TrafficPrivacy(min_devices=5),
    store=store,
    density=density,
    clock=lambda: clock[0],
)

app = FastAPI()
register_exception_handlers(app)
app.include_router(
    make_traffic_router(store, authorize=ops_only, prefix="/api/traffic")
)


def drive(bus: str, departure: datetime) -> None:
    """One bus trip, one position per second, then the trip ends."""
    for second in range(191):
        when = departure + timedelta(seconds=second)
        clock[0] = when.timestamp()
        x, y = route(second)
        here = point(x, y)
        traffic.add_position(
            bus,
            LivePositionSchema(
                lat=here.latitude, lng=here.longitude, recorded_at=when
            ),
        )
        if second % 20 == 19:
            traffic.process()
    traffic.end_trip(bus)


async def main() -> None:
    """Five buses drive, the app reads the traffic, the backend infers a street."""
    for n in range(4):
        drive(f"bus-{n}", start + timedelta(seconds=20 * n))
    snapshot = await traffic.flush()
    print("4 buses -> published edges:", len(snapshot.edges))

    drive("bus-4", start + timedelta(seconds=80))
    snapshot = await traffic.flush()
    print("5 buses -> published edges:", len(snapshot.edges))
    for edge in snapshot.edges:
        print(" ", edge.edge_id, edge.speed_kmh, "km/h,", edge.devices, "devices")

    box = {
        "min_latitude": point(0, -50).latitude,
        "max_latitude": point(0, 50).latitude,
        "min_longitude": point(-650, 0).longitude,
        "max_longitude": point(250, 0).longitude,
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as api:
        stranger = await api.get(
            "/api/traffic", params=box, headers={"X-User-Id": "x"}
        )
        print("stranger:", stranger.status_code)
        ops = await api.get(
            "/api/traffic", params=box, headers={"X-User-Id": "ops"}
        )
        print("ops:", ops.status_code, [e["edge_id"] for e in ops.json()["edges"]])

    for n in range(5, 20):
        drive(f"bus-{n}", start + timedelta(minutes=n))
    density.roll((start + timedelta(days=1)).timestamp())
    for candidate in infer_road_candidates(density, graph):
        first, last = candidate.coordinates[0], candidate.coordinates[-1]
        print(
            "candidate:",
            candidate.status,
            round(candidate.length_m),
            "m,",
            candidate.devices,
            "devices, from",
            (round(first.latitude, 5), round(first.longitude, 5)),
            "to",
            (round(last.latitude, 5), round(last.longitude, 5)),
        )


asyncio.run(main())
```

Saída medida em 2026-11-19 (Python 3.13.3):

```text
4 buses -> published edges: 0
5 buses -> published edges: 4
  way/100:0 36.0 km/h, 5 devices
  way/100:1 36.0 km/h, 5 devices
  way/200:2 36.0 km/h, 5 devices
  way/200:3 36.0 km/h, 5 devices
stranger: 403
ops: 200 ['way/100:0', 'way/100:1']
candidate: candidate 400 m, 20 devices, from (-5.0892, -42.79921) to (-5.0856, -42.79921)
```

Com quatro ônibus nada sai: o mínimo é cinco aparelhos por trecho. Com o quinto,
saem os quatro trechos das avenidas a 36 km/h (os 10 m/s do exemplo). A rua do
meio volta como **candidata**, de 400 m, ligando uma avenida à outra. Vamos por
partes.

## O grafo de ruas

`RoadGraph` é a rede: nós em WGS84 e arestas que apontam para eles por índice.
É o mesmo contrato do `createRoadGraph` do
[tempest-react-sdk](https://github.com/mauriciobenjamin700/tempest-react-sdk),
no fio:

```json
{"nodes": [{"latitude": -5.09, "longitude": -42.8}, {"latitude": -5.09, "longitude": -42.799}],
 "edges": [{"from": 0, "to": 1, "twoWay": true, "id": "way/100:0", "name": "Av. Sul"}],
 "twoWay": false}
```

Em Python, a aresta é `RoadEdge(start, end, two_way, id, data)`: `from`/`to`
viram `start`/`end`, e qualquer campo extra (nome, classe, limite de velocidade)
fica em `data` e volta intacto. `RoadGraph.from_dict` e `to_dict` fazem a ida e
a volta.

O grafo vem de um recorte do OpenStreetMap, sem osmnx nem shapely:

| Função | Lê |
| --- | --- |
| `road_graph_from_overpass(data)` | resposta `[out:json]` do Overpass (`way["highway"](bbox); (._;>;); out;`) |
| `road_graph_from_geojson(data)` | `FeatureCollection` de `LineString`/`MultiLineString` (osmtogeojson, QGIS); vértices iguais viram o mesmo nó |
| `road_graph_from_osm_json(data)` | qualquer um dos dois, pelo formato |
| `load_road_graph(path)` | um arquivo, com cache em memória por caminho, tamanho e mtime |

Cada via vira uma aresta por par de vértices, com `id` `"way/<id>:<n>"`. A mão
de direção segue o OSM: `oneway=yes`, rotatória e `motorway` são de mão única;
`oneway=-1` é mão única contra o desenho; o resto é mão dupla. Por padrão ficam
só vias de carro (`OSM_DRIVABLE_HIGHWAYS`); `highways=None` mantém todas.

!!! tip "Tiles para o app"
    `graph.extract(bbox)` recorta o grafo numa caixa, mantendo os `id`. O
    `make_traffic_router(..., graph=graph)` serve isso em `GET <prefix>/graph`,
    no formato que o `createRoadGraph` do app lê — o app encaixa o veículo nas
    mesmas arestas em que o trânsito é publicado.

## Encaixe na rua: `match_to_roads`

GPS na cidade erra de 5 a 20 m, e às vezes centenas. Encaixar cada posição na
rua mais próxima erra a rua em 1 de cada 7 posições (medido na fase 0 do #667).
`match_to_roads(fixes, graph)` usa o modelo oculto de Markov de Newson & Krumm
(2009): candidatas por distância (gaussiana), transição pela diferença entre a
linha reta e o caminho na rede (exponencial), e Viterbi escolhe a sequência
mais provável.

É o porte do `matchToRoads` do tempest-react-sdk, com os mesmos padrões
(`ROAD_MATCH_DEFAULTS`: σ 7 m, β 3 m, raio 200 m, 16 ruas, desvio máximo
100 m), escolhidos lá por varredura em 100 trajetos reais. O teste
`tests/geo/test_map_matching_parity.py` compara a saída com a do TypeScript no
mesmo grafo e nas mesmas posições (31 cenários: quebra de cadeia, posições com
horário, relógio repetido ou voltando), gerada por
`node scripts/map_matching_parity_fixture.mjs <tempest-react-sdk>/dist`.

Você raramente chama `match_to_roads` direto: o agregador chama por você.

### Posições esparsas: passe o horário

O desvio máximo de 100 m foi ajustado para 1 posição por segundo. Uma fonte
esparsa (uma posição a cada 5–30 s) vira esquina e dá a volta no quarteirão
entre duas posições, e com o limite fixo a cadeia quebra onde o veículo só
dirigiu. Com o horário de cada posição em `timestamps=` (segundos epoch, o
`datetime.timestamp()`), o limite da transição passa a ser

`linha reta + max_detour_m + detour_per_second_m · min(Δt, max_interval_s)`

com `detour_per_second_m` 2 m/s e `max_interval_s` 60 s nos padrões — os
mesmos do tempest-react-sdk (PR #678), escolhidos lá nos trajetos de Kubicka
rarefeitos para uma posição a cada 5–300 s e em rastros públicos do OSM de
cinco centros de cidades brasileiras.

```python
import math
from datetime import UTC, datetime

from tempest_fastapi_sdk.geo import (
    Coordinate,
    RoadEdge,
    RoadGraph,
    RoadMatchOptions,
    match_to_roads,
)

BASE = Coordinate(latitude=-5.09, longitude=-42.8)
M_PER_DEG: float = 6371008.8 * math.pi / 180


def at(x: float, y: float) -> Coordinate:
    """A point ``x`` m east and ``y`` m north of BASE."""
    return Coordinate(
        latitude=BASE.latitude + y / M_PER_DEG,
        longitude=BASE.longitude
        + x / (M_PER_DEG * math.cos(math.radians(BASE.latitude))),
    )


graph = RoadGraph(
    [at(0, 0), at(0, 80), at(150, 80), at(150, 0)],
    [
        RoadEdge(start=0, end=1, id="west"),
        RoadEdge(start=1, end=2, id="top"),
        RoadEdge(start=2, end=3, id="east"),
    ],
)
fixes = [at(0, 10), at(150, 10)]
sent = [
    datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC),
    datetime(2026, 11, 20, 12, 0, 30, tzinfo=UTC),
]
options = RoadMatchOptions(radius_m=50)

untimed = match_to_roads(fixes, graph, options)
timed = match_to_roads(
    fixes, graph, options, timestamps=[t.timestamp() for t in sent]
)

print("untimed breaks:", untimed.breaks)
print("timed breaks:", timed.breaks)
print("route:", [t.edge.id for t in timed.routes[0].edges])
```

Saída:

```text
untimed breaks: 1
timed breaks: 0
route: ['west', 'top', 'east']
```

Sem horário, os 150 m em linha reta contra 290 m pela rede passam do desvio de
100 m e a cadeia quebra. Com 30 s entre as duas posições, o limite ganha 60 m e
a rota sai inteira: sobe a `west`, cruza a `top`, desce a `east`.

!!! note "Sem horário, nada muda"
    Horário ausente (`None`), `nan`, repetido (Δt 0) ou voltando conta como
    Δt 0: o limite é o fixo de antes e o resultado é idêntico ao de quem não
    passa `timestamps`. Só ganha folga a transição em que **as duas** posições
    têm horário. `detour_per_second_m=0` desliga a folga.

O `TrafficAggregator` já passa o horário do aparelho (`recorded_at`) de cada
posição: uma transição de até 10 s ganha até 20 m de folga sem configurar nada.

O `createRoadMatcher` do `tempest-react-sdk` (o encaixe incremental, uma posição por vez) não foi
portado: o backend encaixa trechos já soltos de uma vez, e o marcador ao vivo é
trabalho do app.

## A agregação: `TrafficAggregator`

```python
from tempest_fastapi_sdk.geo import TrafficAggregator, TrafficPrivacy, load_road_graph

graph = load_road_graph("teresina.osm.json")
traffic = TrafficAggregator(graph, privacy=TrafficPrivacy(min_devices=5))
```

Três chamadas:

- `traffic.add_position(device_id, position)` — toda posição de todo aparelho
  (`LivePositionSchema`). Síncrona e barata: não faz map-matching.
- `traffic.end_trip(device_id)` — quando você sabe que a viagem acabou.
- `await traffic.flush()` — de tempos em tempos (30 a 60 s): solta o que já está
  longe o bastante de cada aparelho, encaixa nas ruas, encerra viagens em
  silêncio, calcula o retrato e salva no store, avisando quem acompanha por SSE.

O que sai é um `TrafficSnapshotSchema`: `generated_at`, `window_seconds`,
`min_devices` e `edges`, cada uma um `TrafficEdgeSchema` com `edge_id`, as duas
pontas, `speed_kmh` (mediana), `devices` e `speed_limit_kmh` (do `maxspeed` do
OSM, quando numérico). Sentido conta: quem desce a rua contra o desenho aparece
em `"<id>~"`.

## As regras de privacidade

Todas ficam em `TrafficPrivacy`, com valores padrão de proteção:

| Regra | Parâmetro (padrão) | Teste |
| --- | --- | --- |
| Posição a menos do raio do **início** da viagem não entra | `trim_radius_m` (200) | `TestTrimsStartAndEnd` |
| Posição só entra quando o aparelho já está a mais do raio dela; o que espera no **fim** da viagem é descartado | `trim_radius_m` (200), `trip_gap_seconds` (300) | `TestTrimsStartAndEnd` |
| Trecho só é publicado com **k aparelhos distintos** na janela | `min_devices` (5, mínimo 2), `window_seconds` (300) | `TestKAnonymity` |
| O mesmo aparelho em duas viagens conta uma vez; nunca conta dobrado na troca do sal | — | `TestKAnonymity` |
| Id do aparelho nunca guardado: HMAC com segredo do processo; cada amostra leva HMAC por trecho com sal que gira a cada janela e é esquecido depois | — | `TestNoIdentifierKept` |
| O store recebe só o retrato k-anônimo | — | `TestNoIdentifierKept` |
| Precisão ruim, salto impossível e posição fora de ordem são descartados | `max_accuracy_m` (50), `max_speed_kmh` (200) | `TestCleaning` |

??? info "Por que a contagem é um limite inferior"
    O sal gira a cada janela, então o mesmo aparelho em duas janelas tem dois
    hashes diferentes. Somar as contagens contaria esse aparelho duas vezes —
    e publicaria um trecho com menos de k pessoas. O agregador pega a **maior**
    contagem entre as épocas que a janela cobre: pode contar a menos, nunca a
    mais. Também por isso o hash é por trecho: duas amostras do mesmo aparelho em
    ruas diferentes não se ligam uma à outra.

!!! warning "Trade-off do corte do fim"
    A posição é liberada quando o aparelho já está a `trim_radius_m` dela. Uma
    viagem que passa perto de um ponto, vai longe e **volta** a ele para
    terminar teve aquele trecho liberado na ida. É o preço de ter trânsito em
    tempo real em vez de só no fim da viagem; o trecho ainda precisa de k
    aparelhos para aparecer.

## Leitura pelo app: `make_traffic_router`

`make_traffic_router(store, authorize=..., graph=graph, prefix="/api/traffic")`
monta duas rotas (no exemplo, só a primeira, porque não passa `graph=`):

- `GET /api/traffic?min_latitude=&max_latitude=&min_longitude=&max_longitude=`
  — o retrato recortado na caixa. Sem retrato ainda, `edges` vem vazio e
  `generated_at` é `null`. Caixa invertida responde `422`.
- `GET /api/traffic/graph?...` — com `graph=`, o tile do grafo.

`authorize` é obrigatório, como em todo router de geo: quem pode ver o trânsito
é decisão do serviço.

Com `broker=` e `audience=` no agregador, cada `flush` manda o evento
`traffic_updated` (`{"generated_at", "edges"}`) a quem o `audience()` devolver.
O evento é só o aviso; o cliente busca de novo a caixa que está na tela. Assim
ninguém recebe a cidade inteira a cada minuto.

### Ligando ao router de posição ao vivo

Passe `traffic=traffic` ao `make_live_position_router` e toda posição
autorizada de um canal ativo vai também para o agregador, com o
`channel_id` como aparelho — mesmo a que o `LivePositionStore` recusou por
throttle, porque o map-matching quer todas.

!!! warning "Vários workers"
    O estado da viagem fica na memória do processo. Com vários workers, cada um
    vê parte das posições de cada aparelho e conta menos aparelhos por trecho:
    o resultado é publicar **menos**, nunca abaixo de k. Para o trânsito completo,
    rode o agregador num processo só (o consumidor de uma fila, por exemplo) e
    leia o retrato de qualquer worker pelo `RedisTrafficSnapshotStore`.

## Qualidade do encaixe na frota real

Os padrões do `match_to_roads` foram escolhidos em rastros de mapeadores do OSM
e no dataset de Kubicka — aparelho bom, log muitas vezes suavizado. A pergunta
que sobra é a da **sua** frota: aparelho de entrada, cânion urbano, envio em
lote. Responder exige medir no rastro real, e rastro real é exatamente o que
não se pode guardar.

`TrafficAggregator(..., quality=MatchQualityOptions(...))` mede no próprio
encaixe que o agregador já faz e guarda **só o agregado da janela**. Nenhum
rastro, nenhuma viagem, nenhum id.

Copie e rode — sem rede e sem Redis:

```python
import asyncio
import math
import random
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import FastAPI, Header

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.exceptions import ForbiddenException
from tempest_fastapi_sdk.geo import (
    Coordinate,
    InMemoryTrafficSnapshotStore,
    LivePositionSchema,
    MatchQualityOptions,
    RoadEdge,
    RoadGraph,
    TrafficAggregator,
    TrafficPrivacy,
    make_match_quality_router,
)

CENTER = Coordinate(latitude=-5.0892, longitude=-42.8019)
METERS_PER_DEGREE = 6371008.8 * math.pi / 180


def point(x: float, y: float) -> Coordinate:
    """A point x metres east and y metres north of the centre."""
    cos_lat = math.cos(math.radians(CENTER.latitude))
    return Coordinate(
        latitude=CENTER.latitude + y / METERS_PER_DEGREE,
        longitude=CENTER.longitude + x / (METERS_PER_DEGREE * cos_lat),
    )


def avenue() -> RoadGraph:
    """A 4 km two-way avenue, one edge per 100 m block of way 100."""
    nodes = [point(100.0 * i, 0.0) for i in range(41)]
    edges = [RoadEdge(i, i + 1, True, f"way/100:{i}") for i in range(40)]
    return RoadGraph(nodes, edges)


async def ops_only(x_user_id: str = Header()) -> None:
    """Only the operations team reads the quality."""
    if x_user_id != "ops":
        raise ForbiddenException()


store = InMemoryTrafficSnapshotStore()
window_start = datetime(2026, 11, 18, 7, tzinfo=UTC)
clock = [window_start.timestamp()]
traffic = TrafficAggregator(
    avenue(),
    privacy=TrafficPrivacy(min_devices=5),
    quality=MatchQualityOptions(window_seconds=3600, dimensions=("os",)),
    store=store,
    clock=lambda: clock[0],
)

app = FastAPI()
register_exception_handlers(app)
app.include_router(
    make_match_quality_router(store, authorize=ops_only, prefix="/api/traffic/quality")
)


def drive(bus: str, os_name: str, departure: datetime, noise: random.Random) -> None:
    """One trip east at 10 m/s, 1 Hz, GPS 5 m off the avenue on average."""
    for second in range(301):
        when = departure + timedelta(seconds=second)
        clock[0] = when.timestamp()
        here = point(10.0 * second, noise.gauss(0.0, 5.0))
        traffic.add_position(
            bus,
            LivePositionSchema(lat=here.latitude, lng=here.longitude, recorded_at=when),
            dimensions={"os": os_name},
        )
        if second % 20 == 19:
            traffic.process()
    traffic.end_trip(bus)


async def main() -> None:
    """Twelve trips in one hour, then the operations team reads the quality."""
    noise = random.Random(667)
    fleet = ["android"] * 6 + ["ios"] * 5 + ["harmony"]
    for n, os_name in enumerate(fleet):
        drive(f"bus-{n}", os_name, window_start + timedelta(minutes=4 * n), noise)
    clock[0] = (window_start + timedelta(hours=1, seconds=30)).timestamp()
    await traffic.flush()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as api:
        stranger = await api.get("/api/traffic/quality", headers={"X-User-Id": "x"})
        print("stranger:", stranger.status_code)
        report = (
            await api.get("/api/traffic/quality", headers={"X-User-Id": "ops"})
        ).json()

    snapshot = report["snapshot"]
    print("window:", snapshot["window_start"], "->", snapshot["window_end"])
    for label, cell in [
        ("overall", snapshot["overall"]),
        *((str(c["dimensions"]), c) for c in snapshot["cells"]),
        ("other", snapshot["other"]),
    ]:
        if cell is None:
            continue
        print(
            f"{label}: {cell['devices']} devices, {cell['positions']} positions,",
            f"breaks/1000 {cell['breaks_per_1000_positions']},",
            f"snap {cell['snap_distance_median_m']}/{cell['snap_distance_p90_m']} m,",
            f"inferred {cell['inferred_share']}",
        )
    for ref in report["references"]:
        print(
            f"reference {ref['name']}: breaks/1000 {ref['breaks_per_1000_positions']},",
            f"snap median {ref['snap_distance_median_m']} m",
        )


asyncio.run(main())
```

Saída medida em 2026-11-20 (Python 3.13.3):

```text
stranger: 403
window: 2026-11-18T07:00:00Z -> 2026-11-18T08:00:00Z
overall: 12 devices, 3132 positions, breaks/1000 0.0, snap 3.3/8.0 m, inferred 0.4639
{'os': 'android'}: 6 devices, 1566 positions, breaks/1000 0.0, snap 3.3/8.25 m, inferred 0.461
other: 6 devices, 1566 positions, breaks/1000 0.0, snap 3.3/8.0 m, inferred 0.4668
reference kubicka: breaks/1000 0.19, snap median 3.7 m
reference brazil-osm: breaks/1000 1.06, snap median 3.7 m
```

O ruído do exemplo é gaussiano de 5 m no eixo perpendicular à avenida. A
distância até o ponto encaixado é o módulo desse ruído, cuja mediana teórica é
0,674 × 5 = 3,37 m e o p90, 1,645 × 5 = 8,22 m. Saiu 3,3 / 8,0 m. Vamos por
partes.

### Os indicadores

São os mesmos que validaram o matcher na issue
[tempest-react-sdk#667](https://github.com/mauriciobenjamin700/tempest-react-sdk/issues/667),
com o mesmo denominador. Por isso a janela se compara direto com as referências:

| Indicador (campo) | O que diz | Kubicka | Brasil (OSM) |
| --- | --- | --- | --- |
| Quebras por 1000 posições (`breaks_per_1000_positions`) | Vezes em que a cadeia do HMM quebrou: nenhum caminho possível entre duas posições. Sobe com contramão (ou `oneway` errado no OSM), rua que o grafo não tem e amostragem esparsa | 0,19 | 1,06 |
| Posição → ponto encaixado, mediana / p90 (`snap_distance_median_m` / `snap_distance_p90_m`) | O erro do GPS visto pelo encaixe. Uma cauda longa (p90 alto) é cânion urbano, viaduto ou rua faltando | 3,7 m (até a rota verdadeira) | 3,7 / 11,6 m |
| Fração encaixada (`matched_share`) | Posições com alguma rua a até 200 m | — | 100% |
| Fração `inferred` (`inferred_share`) | Posições que o pré-filtro de 2σ (14 m) pulou e projetou na aresta da anterior. Alta em trânsito lento a 1 Hz, e esperada | — | 55,1% |
| Intervalo de amostragem, mediana / p90 (`sampling_interval_median_s` / `sampling_interval_p90_s`) | Tempo entre duas posições mantidas da viagem. A ≥ 5 s o desvio fixo de 100 m quebra 5,7 a 24,2 vezes por 1000 posições (medição esparsa do #667) | ~1 s | 1 / 4 s |
| Trocas para rua paralela por km (`parallel_switches_per_km`) | Em trecho reto do GPS (rumo dos 75 m antes e depois difere < 10°), a rua encaixada muda para outra a menos de 20° e a mais de 8 m da linha da anterior. No Brasil, foram pista dupla e elevado × túnel | — | 0,109 |

`straight_km` é o denominador da última linha, e `positions`, o das três
primeiras. As duas referências vêm prontas em `MATCH_QUALITY_REFERENCES`, e o
router as devolve ao lado da janela.

!!! note "Onde a medida difere da referência"
    A referência do Brasil mediu corridas inteiras. O agregador mede os trechos
    que solta, já sem os 200 m do início e do fim, e encaixa cada trecho com só
    a última posição do anterior como contexto. Por isso um trecho reto que
    cruza a borda de dois trechos conta um pouco menos. A mediana de Kubicka é
    até a rota verdadeira, não até o ponto encaixado.

??? info "Precisão das medianas"
    Distância e intervalo vão para histogramas de baldes fixos
    (`SNAP_DISTANCE_BUCKETS_M`, `SAMPLING_INTERVAL_BUCKETS_S`): 0,1 m até 5 m,
    0,25 m até 20 m, e segundos inteiros até 30 s. O quantil é o do posto mais
    próximo, a mesma regra dos scripts do #667. Ele devolve o limite de cima do
    balde, limitado ao maior valor visto. Erra para mais por menos de um balde
    e é exato em valor que cai num limite. A memória não cresce com o número de
    posições.

### Dimensões grossas, também k-anônimas

`MatchQualityOptions(dimensions=("os", "app_version"))` declara até quatro
dimensões. O app as informa por viagem:
`add_position(..., dimensions={"os": "android"})`. A última informada vale
para a viagem toda, e a que faltar vira `"unknown"`. Use valores grossos
(família de SO, faixa de versão do app), nunca modelo de aparelho nem versão
completa.

Com o `make_live_position_router(..., traffic=traffic)`, passe também
`traffic_dimensions=`: uma dependência FastAPI que lê a dimensão da request (um
header que o app manda, por exemplo) e devolve `{"os": "android"}` ou `None`.
Valor que o agregador recusa (nome não declarado, texto longo demais) é
descartado com um aviso no log, e a posição entra mesmo assim.

A proteção não depende de você acertar isso:

- uma célula só sai com **k aparelhos distintos** (o mesmo `min_devices` do
  `TrafficPrivacy`);
- as células abaixo de k são somadas em `other`. Quando essa soma ainda fica
  abaixo de k, a menor célula publicada desce para `other` também, até `other`
  chegar a k ou não sobrar célula. Assim, `overall` menos as células nunca
  isola um grupo menor que k;
- no exemplo, `ios` tinha exatamente 5 aparelhos. Mas `harmony`, com 1, só
  ficaria protegido se `ios` descesse junto, e por isso só `android` aparece;
- passada a `max_cells` (64), uma combinação nova conta só no total.

### O que fica e o que sai

| Regra | Teste |
| --- | --- |
| Cada trecho entra na hora em contadores e histogramas de tamanho fixo da célula; nada por viagem é guardado | `TestNoTripDataKept` |
| O aparelho só aparece como HMAC sob um sal sorteado para a janela | `TestNoTripDataKept` |
| Ao fechar a janela, sobra só o agregado publicado: sal, hashes e contadores são apagados | `TestNoTripDataKept` |
| O store recebe o retrato com `quality` agregado e nada mais; o `GET` do trânsito por caixa não leva `quality` | `TestNoTripDataKept` |
| Célula com menos de k aparelhos nunca sai, nem por diferença | `TestDimensionKAnonymity` |
| Histograma com memória fixa | `TestHistogram` |
| Ruído conhecido dá a mediana esperada; lacuna forçada conta uma quebra | `TestKnownNoise` |

Todos em `tests/geo/test_match_quality.py`.

### Leitura: `make_match_quality_router`

`make_match_quality_router(store, authorize=..., prefix="/api/traffic/quality")`
monta `GET <prefix>`, que lê o mesmo store do trânsito e devolve um
`MatchQualityReportSchema`: `snapshot` (a última janela fechada) e
`references`. Antes de a primeira janela fechar, `snapshot.window_start` vem
`null`. O router é separado do `make_traffic_router` para você poder dar a ele
um `authorize` mais estreito: o trânsito o app inteiro lê, e a qualidade é
assunto do time de operação.

!!! tip "Janela"
    O padrão é uma hora (`window_seconds=3600`), alinhada ao relógio. Com
    frota pequena, aumente a janela para as células chegarem a k. Janela que
    não junta k aparelhos publica `overall` `null`, e nada vaza.

## Ruas que o mapa não tem

`OffRoadDensity(centro)` conta, numa grade de 5 m, as posições **já cortadas**
que caíram a mais de 20 m de qualquer rua do grafo. Passe ao agregador com
`density=`. A grade tem a mesma proteção:

- uma célula só é gravada quando o **bloco de 25 m** dela teve k aparelhos
  distintos naquela época (um dia, por padrão);
- até lá ela vive como hashes por bloco com sal do dia, e no fim do dia os
  hashes e o sal são apagados;
- o que sobra — e o que `to_dict()` deixa você persistir — é `points` e
  `devices` por célula, nada por aparelho.

`density.roll(agora)` fecha os dias que já acabaram (o `flush` chama sozinho).

`infer_road_candidates(density, graph)` transforma a densidade em linhas:
fechamento morfológico, afinamento de Zhang–Suen, esqueleto em linhas entre
cruzamentos, Douglas–Peucker, e a **diferença com o OSM** — só sai a linha que,
em pelo menos 80% do comprimento, fica longe de toda rua. As pontas são ligadas
à rua mais próxima (até 40 m), mostrando onde a rua nova encosta na rede.

Cada `RoadCandidateSchema` tem `status="candidate"`: nada entra no grafo
sozinho. `road_candidates_geojson(candidatas)` gera um GeoJSON para revisar no
JOSM ou no QGIS.

## Medições

Comando: `uv run python scripts/bench_geo_traffic.py [--devices N] [--spacing S]`
(i9-13900F, Python 3.13.3, 2026-11-19). Cidade sintética de 9 × 9 cruzamentos
a cada 150 m; uma rua de 450 m é tirada do grafo, e a frota passa por ela com
ruído gaussiano de 5 m a 1 Hz.

| Medida | 40 viagens | 200 viagens |
| --- | --- | --- |
| Agregação, map-matching incluso (4 execuções de cada) | 48,6–63,9 ms por 1000 posições | 48,1–59,2 ms por 1000 posições |
| Viagens abertas ao mesmo tempo | até 16 | até 77 |
| Memória de pico (`tracemalloc`) | 0,77 MB | 3,00 MB |
| Rua tirada recuperada (amostra a cada 1 m, 15 m de tolerância) | 100% | 100% |
| Maior desvio de um vértice da candidata | 2,5 m | 2,5 m |
| Candidatas em outro lugar | 0 | 0 |

Com menos viagens por dia, a inferência depende do k: com 12 viagens, 77,2% da
rua; com 8 ou menos, nenhuma candidata (k = 5 por bloco de 25 m).

!!! note "O que estes números não dizem"
    O caso é sintético: rua reta, ruído gaussiano, nenhum estacionamento. Em
    cidade real, pátio e estacionamento também ficam longe das ruas e viram
    candidatas — é para isso que a revisão humana existe. O desempenho é de um
    núcleo de desktop; o map-matching do tempest-react-sdk, em TypeScript, faz o
    mesmo trabalho a 8,1 µs por posição.

### Custo da qualidade

Comando: `uv run python scripts/bench_geo_traffic.py --devices N [--spacing 3] [--quality] --repeat 6`
(Python 3.13.3, 2026-11-20). As 200 viagens usam `--spacing 3`, para dar as 77
abertas da tabela acima. A máquina estava compartilhada (load average de 15 a
24 em 12 núcleos), então o tempo total variou mais entre execuções do que entre
"com" e "sem". O número estável é o tempo **dentro** do
`MatchQualityTracker.record`, porque os indicadores reaproveitam o
`match_to_roads` que o agregador já roda:

| Medida | 40 viagens | 200 viagens |
| --- | --- | --- |
| Agregação sem `--quality` (mais rápida / mediana) | 66,6 / 77,7 ms por 1000 posições | 49,4–56,1 / 53,3–63,8 ms |
| Agregação com `--quality` (mais rápida / mediana) | 75,0 / 84,0 ms | 51,7–59,3 / 63,2–63,8 ms |
| Só os indicadores (`record`), mais rápida / mediana | 2,07 / 4,50 ms por 1000 posições | 1,72–1,93 / 2,01–2,11 ms |
| Pico de memória (`tracemalloc`), sem / com | 0,75 / 0,77 MB | 2,98 / 3,01 MB |
| Estado da janela aberta (2 células, histogramas + hashes) | 10,6 kB | 30,8 kB |

O estado cresce com o número de aparelhos da janela (um hash por aparelho e
célula, medido com `sys.getsizeof`), não com o de posições: os histogramas têm tamanho fixo.

## Recap

- `RoadGraph` / `RoadEdge` — o grafo, no mesmo contrato do `createRoadGraph` do app; `road_graph_from_overpass`, `road_graph_from_geojson`, `load_road_graph` (com cache).
- `match_to_roads` — HMM de Newson & Krumm, porte do tempest-react-sdk com teste de paridade.
- `TrafficAggregator` — `add_position`, `end_trip`, `flush`; publica só trecho com k aparelhos.
- `TrafficPrivacy` — corte de início e fim, k, janela, sal rotativo; cada regra com teste.
- `make_traffic_router` — `GET` por caixa e tile do grafo, `authorize` obrigatório; `traffic_updated` por SSE.
- `MatchQualityOptions` + `make_match_quality_router` — a qualidade do encaixe na frota real, por janela, só agregada e k-anônima também nas dimensões; ao lado das referências de Kubicka e do Brasil.
- `OffRoadDensity` + `infer_road_candidates` — ruas que o OSM não tem, como candidatas para revisão.
