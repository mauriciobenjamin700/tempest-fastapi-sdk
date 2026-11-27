# Salas de jogo online (TCP)

Alguns clientes não falam WebSocket, ou já têm todo o multiplayer escrito
sobre um socket TCP — é o caso do PokeBudokai, cuja `NetSession` lê e escreve
num par de `InputStream`/`OutputStream`. Para eles, o mesmo servidor de salas
de [Salas de jogo online (WebSocket)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/game-rooms-websocket.md) responde
também em TCP cru, com o **mesmo protocolo** e as **mesmas salas**: um
jogador em TCP e outro no navegador podem jogar juntos.

Leia a receita do WebSocket primeiro: as mensagens (`create`, `join`,
`resume`, `send`, `welcome`, `player_left`…), os slots, o anfitrião, a
reconexão e os limites são os mesmos. Aqui só muda o transporte.

## Por que frames, e não bytes soltos

TCP é um fluxo de bytes sem fronteira de mensagem, e o `NetProto` do
PokeBudokai não leva o tamanho de cada mensagem: quem lê precisa conhecer o
formato de cada tipo. O servidor de salas não conhece o protocolo do jogo, e
mesmo assim precisa saber onde uma mensagem acaba, para repassá-la inteira
ao slot certo. Por isso cada mensagem viaja num frame:

```text
[u32 tamanho, big-endian][u8 tipo][payload: tamanho bytes]
```

| Tipo | Constante | Payload |
| --- | --- | --- |
| `0` | `FRAME_JSON` | mensagem de controle, JSON em UTF-8 |
| `1` | `FRAME_BINARY` | relay binário: `[u8 slot][bytes do jogo]` |

O `tamanho` conta só o payload. Um frame acima de `max_frame_bytes` (64 KiB
por padrão) fecha a conexão antes de o servidor ler o corpo.

## O servidor, ao lado do WebSocket

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from tempest_fastapi_sdk.rooms import (
    RoomPolicy,
    RoomRegistry,
    make_game_room_router,
    serve_game_rooms_tcp,
)
from tempest_fastapi_sdk.settings import WebSocketSettings

registry = RoomRegistry(RoomPolicy(max_players=4, protocol_version="6"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the TCP port next to the WebSocket endpoint, same rooms."""
    tcp = await serve_game_rooms_tcp(registry, host="127.0.0.1", port=47200)
    yield
    tcp.close()
    await tcp.wait_closed()


app = FastAPI(lifespan=lifespan)
app.include_router(make_game_room_router(registry, settings=WebSocketSettings()))

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

O `serve_game_rooms_tcp` abre a porta 47200 no mesmo event loop do FastAPI,
com o **mesmo `registry`**: uma sala criada por um celular em TCP aparece
para quem entra pelo WebSocket. Sem FastAPI, ele roda sozinho num
`asyncio.run`.

O que ele faz em cada conexão:

- liga **`TCP_NODELAY`**: o lockstep manda um frame pequeno a cada tick, e o
  algoritmo de Nagle seguraria esse frame esperando mais dados;
- manda `{"type": "ping"}` a cada `heartbeat_seconds` (15 s por padrão);
  qualquer frame que o cliente mande conta como sinal de vida, e
  `{"type": "pong"}` é engolido;
- fecha quem fica em silêncio por `heartbeat_timeout_seconds` (45 s), sem
  aviso — o lugar dele espera o `resume`, como no WebSocket;
- TCP cru não tem código de fechamento, então quando o servidor encerra a
  conexão de propósito ele avisa antes com um frame de controle. Hoje isso
  acontece num caso só: o jogador retomou o lugar por outra conexão, e a
  antiga recebe `{"type": "close", "code": 4409, "reason": "replaced"}` e
  depois o fim do stream;
- recusa um tipo de frame desconhecido ou um JSON inválido com `error`
  (`bad_request`), sem fechar.

## Dois celulares numa sala

O cliente Python abaixo faz o papel dos dois aparelhos:

```python
import asyncio
import json
from typing import Any

from tempest_fastapi_sdk.rooms import FRAME_BINARY, FRAME_JSON, encode_frame, read_frame


class Player:
    """One TCP player speaking the framed room protocol."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer

    async def send(self, message: dict[str, Any]) -> None:
        """Send a control message."""
        self.writer.write(encode_frame(FRAME_JSON, json.dumps(message).encode()))
        await self.writer.drain()

    async def send_bytes(self, data: bytes) -> None:
        """Send a binary relay frame."""
        self.writer.write(encode_frame(FRAME_BINARY, data))
        await self.writer.drain()

    async def until(self, kind: str) -> dict[str, Any]:
        """Read frames until a control message of ``kind`` arrives."""
        while True:
            frame_kind, payload = await read_frame(self.reader)
            if frame_kind == FRAME_JSON:
                message: dict[str, Any] = json.loads(payload)
                if message["type"] == kind:
                    return message

    async def binary(self) -> bytes:
        """Read frames until a binary one arrives."""
        while True:
            frame_kind, payload = await read_frame(self.reader)
            if frame_kind == FRAME_BINARY:
                return payload


async def main() -> None:
    """Two phones meet over TCP and swap one tick of input."""
    host = Player(*await asyncio.open_connection("127.0.0.1", 47200))
    guest = Player(*await asyncio.open_connection("127.0.0.1", 47200))
    print("hello:", await host.until("hello"))
    await host.send({"type": "create", "name": "Ana", "version": "6"})
    code = (await host.until("welcome"))["room"]["code"]
    await guest.send({"type": "join", "code": code, "name": "Bia", "version": "6"})
    print("convidado no slot", (await guest.until("welcome"))["slot"], "da sala", code)
    await guest.send_bytes(bytes([0]) + (7).to_bytes(4, "big") + (16).to_bytes(2, "big"))
    print("anfitrião recebeu:", (await host.binary()).hex(" "))


asyncio.run(main())
```

Saída medida (com o servidor acima):

```text
hello: {'type': 'hello', 'protocol_version': '6', 'max_players': 4, 'reconnect_grace_seconds': 10.0, 'heartbeat_seconds': 15.0, 'requires_identity': False}
convidado no slot 1 da sala W5ED
anfitrião recebeu: 01 00 00 00 07 00 10
```

O convidado mandou o input do tick 7 para o slot `0` (o anfitrião), e o
anfitrião recebeu com o primeiro byte trocado pelo slot de quem mandou (`01`).

!!! tip "O mesmo cliente serve de teste"
    O backlog do PokeBudokai cita um "convidado falso em Python" para testar
    a sala sem um segundo aparelho. A classe `Player` acima é esse convidado:
    ela fala o protocolo inteiro com 30 linhas e as funções `encode_frame` /
    `read_frame` do SDK.

## No Android (a `NetSession` do PokeBudokai)

A `NetSession` já separa leitura e escrita em duas threads sobre um par de
streams. Online, ela continua escrevendo `NetProto`; o que muda é o canal,
que embrulha cada mensagem num frame e abre cada frame recebido:

```kotlin
class RoomChannel(host: String, port: Int) {
    private val socket = Socket().apply {
        tcpNoDelay = true
        connect(InetSocketAddress(host, port), 5_000)
    }
    private val input = DataInputStream(BufferedInputStream(socket.getInputStream()))
    private val output = DataOutputStream(BufferedOutputStream(socket.getOutputStream()))

    @Synchronized
    fun sendControl(json: String) = writeFrame(0, json.toByteArray(Charsets.UTF_8))

    // Uma mensagem do NetProto para um slot (0xFF = todos os outros).
    @Synchronized
    fun sendGame(toSlot: Int, netProtoMessage: ByteArray) =
        writeFrame(1, byteArrayOf(toSlot.toByte()) + netProtoMessage)

    private fun writeFrame(kind: Int, payload: ByteArray) {
        output.writeInt(payload.size)   // big-endian, como o servidor espera
        output.writeByte(kind)
        output.write(payload)
        output.flush()
    }

    // Bloqueia até o próximo frame: (tipo, payload).
    fun readFrame(): Pair<Int, ByteArray> {
        val size = input.readInt()
        val kind = input.readUnsignedByte()
        val payload = ByteArray(size).also { input.readFully(it) }
        return kind to payload
    }
}
```

Na thread de leitura, um frame de tipo `1` traz `[slot de quem mandou][NetProto]`
— o slot diz de qual jogador veio o `INPUT`, o que a sala TODOS x TODOS
precisa para montar o `FRAME`. Um frame de tipo `0` é controle: trate
`welcome` (guarde `player_id`, `resume_token` e o código para o `resume`),
`player_disconnected` (passe o slot para a CPU), `ping` (responda
`{"type":"pong"}` quando o jogo estiver parado no menu) e `close` (outra
conexão assumiu o lugar: não tente o `resume` de novo).

!!! warning "TCP cru não tem TLS"
    O servidor TCP fala em claro. Pela internet, ponha na frente um proxy TCP
    que termine TLS (nginx `stream` com `ssl`, ou HAProxy) e conecte o app
    com `SSLSocket`, ou prefira o [WebSocket](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/game-rooms-websocket.md) com
    `wss://`, que passa por qualquer proxy HTTP.

## Conta e reconexão

- **Conta:** com `identity_resolver` no `RoomRegistry`, o token vai no campo
  `token` do `create` / `join` (não há query string em TCP). Token faltando
  ou inválido responde `error` com `code: "unauthorized"`.
- **Reconexão:** abra outra conexão e mande `resume` com o `code`, o
  `player_id` e o `resume_token` do `welcome`. Dá para voltar por TCP ou
  por WebSocket — medido: um jogador TCP que caiu retomou o slot pelo
  WebSocket.

## Recap

- `serve_game_rooms_tcp(registry, ...)` serve o mesmo protocolo de salas em
  TCP cru, no lifespan do FastAPI ou sozinho.
- Cada mensagem vai num frame `[u32 tamanho][u8 tipo][payload]`: tipo `0` é
  controle JSON, tipo `1` é relay binário com o slot na frente.
- Mesmo `registry` = mesmas salas: TCP e WebSocket jogam juntos.
- `TCP_NODELAY`, ping do servidor e queda por silêncio vêm prontos; TLS fica
  com um proxy TCP na frente.
- `encode_frame` / `read_frame` montam um cliente de teste em poucas linhas.
