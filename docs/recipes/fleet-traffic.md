# Trânsito da frota (GPS anônimo)

A frota já manda posição ao vivo (receita de [Localização ao vivo](live-location.md)).
Com essas mesmas posições dá para responder duas perguntas que um mapa comercial
cobra caro:

1. **Como está o trânsito agora?** Velocidade mediana por trecho de rua, nos
   últimos minutos.
2. **Que rua a frota usa e o mapa não tem?** Trechos com muitos rastros longe de
   qualquer rua do OpenStreetMap.

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
mesmo grafo e nas mesmas posições (seis cenários, quebra de cadeia incluída),
gerada por `node scripts/map_matching_parity_fixture.mjs <tempest-react-sdk>/dist`.

Você raramente chama `match_to_roads` direto: o agregador chama por você.

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

## Recap

- `RoadGraph` / `RoadEdge` — o grafo, no mesmo contrato do `createRoadGraph` do app; `road_graph_from_overpass`, `road_graph_from_geojson`, `load_road_graph` (com cache).
- `match_to_roads` — HMM de Newson & Krumm, porte do tempest-react-sdk com teste de paridade.
- `TrafficAggregator` — `add_position`, `end_trip`, `flush`; publica só trecho com k aparelhos.
- `TrafficPrivacy` — corte de início e fim, k, janela, sal rotativo; cada regra com teste.
- `make_traffic_router` — `GET` por caixa e tile do grafo, `authorize` obrigatório; `traffic_updated` por SSE.
- `OffRoadDensity` + `infer_road_candidates` — ruas que o OSM não tem, como candidatas para revisão.
