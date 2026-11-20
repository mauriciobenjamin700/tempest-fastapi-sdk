# Salas de jogo online (WebSocket)

Um jogo multiplayer online precisa de um ponto de encontro na internet: os
jogadores entram numa **sala** por um código curto, sabem quem é o anfitrião e
trocam mensagens (o input de cada tick, o estado do mundo, um convite) com
pouca latência. Esta receita monta esse servidor com
`tempest_fastapi_sdk.rooms`, num endpoint WebSocket do FastAPI.

O servidor **não simula o jogo**. Ele junta os jogadores, repassa as
mensagens e cuida do que todo jogo online escreveria igual — e erraria de um
jeito diferente cada vez:

- o **`from`** de cada mensagem é o jogador que o servidor conhece, nunca um
  campo que o cliente manda;
- a sala tem **limite de jogadores**, recusa entrada **durante a partida** e
  checa a **versão do protocolo** no join;
- o **anfitrião** é eleito e, quando ele sai, o próximo assume e todos são
  avisados;
- quem cai (Wi-Fi para 4G, túnel) **mantém o lugar** por uma janela e volta
  com `resume`, no mesmo slot;
- **limite de taxa e de tamanho** por jogador, e uma conexão silenciosa é
  derrubada de verdade.

A autoridade continua nos clientes: uma simulação em lockstep que todos rodam
(PokeBudokai), ou um anfitrião que roda o mundo e manda snapshots (o co-op do
Jomasio). Para quem não fala WebSocket, a mesma sala está em
[Salas de jogo online (TCP)](game-rooms-tcp.md).

## O servidor

```python
import uvicorn
from fastapi import FastAPI

from tempest_fastapi_sdk.rooms import RoomPolicy, RoomRegistry, make_game_room_router
from tempest_fastapi_sdk.settings import WebSocketSettings

registry = RoomRegistry(
    RoomPolicy(max_players=4, protocol_version="6", reconnect_grace_seconds=10)
)
app = FastAPI()
app.include_router(make_game_room_router(registry, settings=WebSocketSettings()))

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

São três peças:

- **`RoomRegistry`** guarda as salas, na memória deste processo. Uma por jogo.
- **`RoomPolicy`** são as regras: até 4 jogadores, protocolo `"6"` (cliente
  com outra versão é recusado), lugar guardado por 10 s depois de uma queda.
- **`make_game_room_router`** monta o endpoint em `/rooms/ws`. O
  `WebSocketSettings` dá o heartbeat (`WS_HEARTBEAT_SECONDS`,
  `WS_HEARTBEAT_TIMEOUT_SECONDS`) e o tamanho máximo de frame
  (`WS_MAX_MESSAGE_BYTES`; acima disso a conexão fecha com `1009`).

!!! tip "Celulares na rede"
    `127.0.0.1` só aceita conexões da própria máquina. Para os celulares
    alcançarem o servidor, ponha um proxy na frente (nginx ou Caddy, com
    `wss://`) ou rode com `host="0.0.0.0"` numa rede que você controla.

## Dois jogadores numa sala

Um cliente Python que faz o papel dos dois celulares: o anfitrião cria a
sala, o convidado entra pelo código, e os dois trocam um tick de input em
binário e uma mensagem em JSON.

```python
import asyncio
import json
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

URL = "ws://127.0.0.1:8000/rooms/ws"


async def until(ws: ClientConnection, kind: str) -> dict[str, Any]:
    """Read control messages until one of ``kind`` arrives."""
    while True:
        frame = await ws.recv()
        if isinstance(frame, str):
            message: dict[str, Any] = json.loads(frame)
            if message["type"] == kind:
                return message


async def main() -> None:
    """A host and a guest meet in a room and swap one tick of input."""
    async with connect(URL) as host, connect(URL) as guest:
        print("hello:", await until(host, "hello"))
        await host.send(json.dumps({"type": "create", "name": "Ana", "version": "6"}))
        welcome = await until(host, "welcome")
        code = welcome["room"]["code"]
        print("sala criada:", code, "slot", welcome["slot"])

        await guest.send(
            json.dumps({"type": "join", "code": code, "name": "Bia", "version": "6"})
        )
        print("convidado no slot", (await until(guest, "welcome"))["slot"])
        print("anfitrião viu:", (await until(host, "player_joined"))["player"])

        await guest.send(bytes([0xFF]) + (7).to_bytes(4, "big") + (16).to_bytes(2, "big"))
        frame = await host.recv()
        assert isinstance(frame, bytes)
        print("anfitrião recebeu:", frame.hex(" "))

        await host.send(json.dumps({"type": "send", "data": {"type": "start", "seed": 42}}))
        print("convidado recebeu:", await until(guest, "message"))


asyncio.run(main())
```

Saída medida (com o servidor acima; os ids e o código mudam a cada
execução):

```text
hello: {'type': 'hello', 'protocol_version': '6', 'max_players': 4, 'reconnect_grace_seconds': 10, 'heartbeat_seconds': 30, 'requires_identity': False}
sala criada: 3PJN slot 0
convidado no slot 1
anfitrião viu: {'player_id': 'a288dacbb8b60115', 'name': 'Bia', 'slot': 1, 'connected': True}
anfitrião recebeu: 01 00 00 00 07 00 10
convidado recebeu: {'type': 'message', 'from': '5bf6a98642dea04a', 'data': {'type': 'start', 'seed': 42}}
```

Repare em duas coisas:

- O convidado mandou `ff 00 00 00 07 00 10` (destino `0xFF` = todos os
  outros, mais 6 bytes do jogo) e o anfitrião recebeu `01 …`: o servidor
  troca o primeiro byte pelo **slot de quem mandou**.
- O `from` da mensagem JSON foi o servidor que preencheu. Um cliente que
  mandasse `"from": "outro"` dentro de `data` chegaria com o `from` real ao
  lado.

## O protocolo

Frames de **texto** são mensagens de controle em JSON, sempre com `type`.
Frames **binários** são bytes do jogo, que o servidor repassa sem ler.

### Cliente → servidor

| `type` | Campos | O que faz |
| --- | --- | --- |
| `create` | `name?`, `version?` | Abre uma sala, com você como anfitrião no slot 0 |
| `join` | `code`, `name?`, `version?` | Entra numa sala (código em qualquer caixa) |
| `resume` | `code`, `player_id`, `resume_token` | Volta para o seu lugar depois de uma queda |
| `send` | `data`, `to?` | Repassa `data` para todos os outros, ou só para o jogador `to` |
| `start` / `end` | — | Anfitrião põe a sala em `playing` / volta para `lobby` |
| `leave` | — | Sai da sala na hora, liberando o slot |
| `echo` | `t?` | O servidor devolve `t` na hora: meça o RTT com ele |
| *(binário)* | `[u8 slot destino][bytes]` | `0xFF` = todos os outros |

### Servidor → cliente

| `type` | Quando |
| --- | --- |
| `hello` | Ao conectar: versão, tamanho da sala, janela de reconexão, cadência do heartbeat |
| `welcome` | Depois de `create`/`join`/`resume`: `player_id`, `resume_token`, `slot`, `resumed`, `room` |
| `left` | Resposta ao seu `leave`: `code` da sala que você deixou |
| `player_joined` / `player_left` | Alguém entrou / saiu (ou a janela de reconexão acabou) |
| `player_disconnected` / `player_reconnected` | Alguém caiu (com `grace_seconds`) / voltou |
| `host_changed` | O anfitrião saiu e outro assumiu (o conectado de menor slot): `host_id` |
| `room_state` | A sala foi para `playing` ou `lobby`: `state` |
| `message` | Uma mensagem repassada: `from` + `data` |
| `echo` | Resposta ao seu `echo`, com o mesmo `t` |
| `error` | Recusa: `code` estável, `message` legível e `request` (o `type` recusado); a conexão continua aberta |
| `ping` | Heartbeat do servidor: responda `{"type": "pong"}` se estiver ocioso |
| *(binário)* | `[u8 slot de quem mandou][bytes]` |

Os `code` de erro: `room_not_found`, `room_full`, `room_in_match`,
`version_mismatch`, `not_host`, `not_in_room`, `already_in_room`,
`player_not_found`, `resume_rejected`, `rate_limited`,
`message_not_allowed`, `unauthorized`, `server_full`, `bad_request`.

!!! info "Qualquer frame prova que o cliente está vivo"
    Um jogo em tempo real manda dezenas de frames por segundo, e cada um
    conta como sinal de vida. O `pong` só é necessário num cliente parado
    (no lobby, por exemplo). Quem fica em silêncio além de
    `WS_HEARTBEAT_TIMEOUT_SECONDS` é fechado com `4408` no primeiro ping
    depois do prazo — com ping a cada 1 s e prazo de 2 s, a conexão caiu
    2,0 s depois do último frame — e o lugar dele espera o `resume`.

## Lockstep binário (o PokeBudokai)

O PokeBudokai já tem um protocolo binário próprio (`NetProto`) e uma
`NetSession` que lê e escreve num par de streams. Online, o app só troca o
canal: cada mensagem do `NetProto` vira um frame binário do WebSocket, com o
slot de destino na frente. Um esboço com OkHttp, no Android:

```kotlin
val client = OkHttpClient()
val socket = client.newWebSocket(
    Request.Builder().url("wss://jogo.example.com/rooms/ws").build(),
    object : WebSocketListener() {
        override fun onMessage(webSocket: WebSocket, text: String) {
            // hello, welcome, player_joined, host_changed, error...
            handleControl(JSONObject(text))
        }
        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            val fromSlot = bytes[0].toInt() and 0xFF
            netSession.deliver(fromSlot, bytes.substring(1).toByteArray())
        }
    },
)
socket.send("""{"type":"create","name":"${Build.MODEL}","version":"6"}""")

// INPUT do tick: [destino 0xFF][tipo 4][int tick][short bits]
fun sendInput(tick: Int, bits: Int) {
    val buffer = ByteBuffer.allocate(8).put(0xFF.toByte()).put(4)
        .putInt(tick).putShort(bits.toShort())
    socket.send(ByteString.of(*buffer.array()))
}
```

Mapeando o que o jogo já faz:

- **`HELLO` com versão e `rosterHash`**: ponha os dois na `version`
  (`"6:<rosterHash>"`) **e** no servidor
  (`RoomPolicy(protocol_version="6:<rosterHash>")`); o servidor recusa quem
  não bate com `version_mismatch` antes de qualquer frame do jogo. Os dois
  lados mudam juntos a cada roster novo.
- **Sala TODOS x TODOS**: quem cria a sala começa no slot 0, mas o
  anfitrião pode mudar (`host_changed`). Leia o slot dele cruzando
  `room.host_id` com `room.players`, e não fixe o 0; os convidados mandam o
  `INPUT` para esse slot, e o anfitrião monta o `FRAME` e manda para
  `0xFF`.
- **Queda**: o `player_disconnected` chega na hora em que o servidor percebe
  a queda; o anfitrião pode passar aquele slot para a CPU no mesmo tick,
  sem esperar os 8 s de travamento.
- **Atraso adaptativo**: meça o RTT com `echo` e escolha o atraso de input
  (6 a 10 ticks) a partir dele.

## JSON com anfitrião autoritativo (o Jomasio)

No co-op do Jomasio, o líder roda a IA do NPC e manda `battleSnapshot` a
20 Hz; o parceiro manda `battlePlayerState`. Com o servidor do SDK, cada
mensagem do jogo vira o `data` de um `send`:

```typescript
const ws = new WebSocket(`wss://${location.host}/rooms/ws?token=${jwt}`);

ws.onmessage = (event) => {
  if (typeof event.data !== "string") return;
  const message = JSON.parse(event.data);
  if (message.type === "message") {
    handleGameMessage(message.from, message.data); // from é confiável
  } else if (message.type === "error") {
    console.warn(message.code, message.message);
  }
};

function sendSnapshot(snapshot: BattleSnapshot): void {
  ws.send(JSON.stringify({ type: "send", data: { type: "battleSnapshot", ...snapshot } }));
}

function invite(partnerId: string): void {
  ws.send(JSON.stringify({ type: "send", to: partnerId, data: { type: "pvpInvite", bestOf: 3 } }));
}
```

Três coisas que o servidor passa a fazer pelo jogo:

- o `from` é do servidor em **toda** mensagem — no servidor Node, o relay
  de batalha carimba o `from` pelo socket, mas os convites
  (`guildInvite`, `pvpInvite` e as respostas) repassam o `from` que o
  cliente escreveu, e o `playerId` do socket vem do `joinRoom` que o
  próprio cliente manda, então um cliente pode convidar em nome de outro;
- `to` entrega **só** ao destinatário, em vez de mandar o convite para a
  sala inteira e deixar o cliente filtrar;
- com `RoomPolicy(allowed_message_types=frozenset({...}))`, um `data.type`
  fora da lista é recusado com `message_not_allowed` — a whitelist que o
  servidor Node mantém à mão. A lista vale para o JSON do `send`; frames
  binários passam sem leitura.

## Conta em vez de convidado

Sem configuração, todo jogador é convidado: o servidor sorteia o
`player_id` e usa o `name` que o cliente mandou. Para exigir conta, passe um
`identity_resolver`. O token vem em `?token=` e, faltando ou inválido, a
conexão fecha com `4401` antes do handshake. Para os tokens do
`UserAuthService`, use o pronto:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import JWTUtils
from tempest_fastapi_sdk.rooms import (
    RoomPolicy,
    RoomRegistry,
    make_game_room_router,
    make_jwt_room_identity_resolver,
)
from tempest_fastapi_sdk.settings import JWTSettings, WebSocketSettings

tokens = JWTUtils(**JWTSettings().jwt_kwargs())
registry = RoomRegistry(
    RoomPolicy(max_players=2),
    identity_resolver=make_jwt_room_identity_resolver(tokens),
)
app = FastAPI()
app.include_router(make_game_room_router(registry, settings=WebSocketSettings()))
```

O `player_id` passa a ser o `sub` do token (o id do usuário) e o nome vem da
claim `name`, quando o token tiver.

!!! danger "Não escreva o resolver com `decode` + `sub`"
    O `UserAuthService` assina com o mesmo segredo o access token, o refresh
    token e o token de MFA pendente — os três têm `sub`. Um resolver que só
    decodifica e devolve o `sub` deixa entrar quem ainda não passou do
    segundo fator. O `make_jwt_room_identity_resolver` é montado sobre o
    `make_jwt_bearer_resolver` e só aceita access token (medido em
    `tests/rooms/test_websocket.py`: refresh, MFA pendente e token sem tipo
    são recusados).

Com conta, o mesmo jogador que entra de novo na sala (outra aba, outro
aparelho) **assume o lugar** dele, e a conexão antiga fecha com `4409`.

## Reconexão

O `welcome` traz `player_id` e `resume_token`. Guarde os dois (e o código da
sala) até a partida acabar. Se a conexão cair, abra outra e mande:

```json
{"type": "resume", "code": "3PJN", "player_id": "a288dacbb8b60115", "resume_token": "…"}
```

Dentro de `reconnect_grace_seconds`, você volta no **mesmo slot**, com
`"resumed": true`, e os outros recebem `player_reconnected`. Depois disso o
lugar é liberado (`player_left`) e o `resume` responde `resume_rejected`.
Se a conexão antiga ainda estiver aberta (o servidor não percebeu a queda),
ela é fechada com `4409`.

## Latência

O servidor acrescenta muito pouco à latência da rede. Medido em loopback
(mesma máquina, 12 núcleos, Python 3.12.7, uvicorn 0.52.4), `echo` de ida e
volta, N=2000 por rodada, três rodadas:

| Transporte | Mediana | p99 |
| --- | --- | --- |
| WebSocket | 0,22–0,24 ms | 0,34–0,41 ms |
| TCP | 0,15–0,16 ms | 0,25–0,30 ms |

Pela internet, o RTT real vem da rede (dezenas de ms no 4G), não do servidor.

## Um processo só

!!! warning "As salas vivem na memória de um processo"
    Como o `WebSocketHub`, o `RoomRegistry` guarda tudo neste processo. Todos
    os jogadores de uma sala precisam chegar à **mesma réplica**: rode uma
    réplica para o servidor de salas, ou use sticky sessions pelo código da
    sala. Reiniciar o processo encerra as salas abertas.

## Recap

- `RoomRegistry` + `RoomPolicy` + `make_game_room_router` montam o servidor
  de salas num endpoint WebSocket.
- O servidor junta os jogadores por código, elege o anfitrião, repassa JSON
  (`send`) e bytes do jogo (frame binário com o slot na frente) e carimba o
  remetente — a autoridade do jogo continua nos clientes.
- Versão, limite de jogadores, sala em partida, limite de taxa e de tamanho
  e heartbeat são do servidor, com recusas de `code` estável.
- Quem cai volta no mesmo slot com `resume` dentro da janela de reconexão.
- Com `identity_resolver`, a sala exige conta; sem ele, todo mundo é
  convidado.
- Tudo vive num processo: uma réplica (ou sticky por sala) para o servidor
  de salas.
