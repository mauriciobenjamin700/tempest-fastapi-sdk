# Receber mensagens do WhatsApp (zap-api)

A receita [WhatsApp (zap-api)](zap.md) cobre o lado de **saída**: enviar e
receber um `202`. Esta cobre o outro lado — o gateway fazendo `POST` no seu
serviço quando uma mensagem **chega** e quando uma que você enviou muda de
status.

Você vai construir o receiver em quatro passos, cada um um programa completo
que roda:

1. **A rota pronta** — uma rota que recebe a mensagem e recusa com `401` o que
   não veio do gateway, montada por uma linha.
2. **Os enums** — despachar por tipo de evento, de mídia e de endereço sem
   comparar com string solta.
3. **A camada de serviço** — `model` → `repository` → `service` →
   `controller` → `router`, guardando cada mensagem **uma vez**, respondendo a
   quem escreveu e nunca regredindo um status.
4. **O teste** — entregas assinadas como o gateway assina, sem gateway de pé.

## Registrar o webhook no gateway

O registro é operação de admin do gateway, não do seu serviço. Um URL só
recebe os dois tipos de evento:

```bash
npm run webhook:set -- meu-bot https://meu-bot.test/webhooks/zap \
  --secret meu-segredo --events status,inbound
```

`--events` aceita `status` (o ciclo de entrega do que **este** consumidor
enviou), `inbound` (toda mensagem que chega) ou os dois. Sem a flag, o
default é só `status`.

!!! danger "Sem `--secret`, o gateway entrega sem assinatura"
    O gateway só assina quando o webhook tem `secret` — sem ele, faz o `POST`
    sem o header `x-zap-signature`. Por isso
    `make_zap_webhook_dependency` **se recusa a ser construída** sem um
    secret não vazio: com ela, entrega sem assinatura é sempre `401`.

## Passo 1 — A rota pronta

A rota pronta: um `POST`, a assinatura conferida, o corpo validado no modelo
certo, o despacho por evento e a resposta `200`.

```python title="zap_minimo.py" hl_lines="5 15"
from fastapi import FastAPI

from tempest_fastapi_sdk.integrations.messaging.zap import (
    ZapInboundMessage,
    make_zap_webhook_router,
)

app: FastAPI = FastAPI()


async def on_message(message: ZapInboundMessage) -> None:
    """Mostra as mensagens que chegam.

    Args:
        message (ZapInboundMessage): A entrega com a assinatura já conferida e
            o corpo já validado.
    """
    print(f"{message.push_name}: {message.text}")


app.include_router(
    make_zap_webhook_router(secret="meu-segredo", on_inbound=on_message)
)
```

Três peças:

- **`make_zap_webhook_router(secret=..., on_inbound=...)`** monta a rota
  inteira e devolve um `APIRouter` como qualquer outro.
- **`on_message`** recebe a entrega já validada: `ZapInboundMessage` para
  `message.received`, `ZapStatusCallback` para os quatro eventos de status.
  O segundo vai em `on_status`.
- **A resposta** é `200` depois que o handler retorna, com o nome do evento de volta —
  `{"ok": true, "event": "message.received"}`, um
  [`ZapWebhookAckSchema`](../reference.md#tempest_fastapi_sdk.integrations.messaging.zap.router.ZapWebhookAckSchema).

Os parâmetros que a rota aceita:

| Parâmetro | Padrão | O que faz |
| --- | --- | --- |
| `secret` | — | O segredo registrado no gateway. Obrigatório sem `verify`, e nunca vazio. |
| `verify` | — | Uma dependência sua, para quando o segredo não basta: rotação, dois segredos, segredo vindo do seu settings. |
| `on_inbound` | `None` | Chamado com o `ZapInboundMessage` de `message.received`. |
| `on_status` | `None` | Chamado com o `ZapStatusCallback` de `message.sent`, `.delivered`, `.read` e `.failed`. |
| `path` | `"/webhooks/zap"` | Onde a rota entra. |
| `tags` | `["zap"]` | As tags no documento OpenAPI. |
| `include_in_schema` | `True` | `False` some com a rota do OpenAPI sem desmontá-la. |

!!! danger "Sem `secret` e sem `verify`, a rota não é construída"
    O gateway só assina quando o webhook foi registrado com `--secret` — sem
    ele, faz o `POST` sem o header `x-zap-signature`. A fábrica **levanta
    `ValueError`** nesse caso, em vez de montar uma rota que aceitaria
    qualquer `POST`. E entrega sem assinatura vira `401`, sempre.

!!! warning "Handler que levanta vira `500`, e o gateway reentrega"
    A rota pronta **não** engole a exceção do seu handler: ela sobe, a resposta
    vira `500`, e o gateway re-POSTa os mesmos bytes com backoff (3 tentativas
    por default). É o que você quer para uma falha passageira — banco fora,
    lock — e o que você **não** quer para um corpo que nunca vai passar:
    nesse caso trate dentro do handler e responda `200`. Evento sem handler, ou
    um evento que o gateway inventar depois, já é `200` com log em `debug`.

### Por baixo dos panos: a rota que a fábrica monta

???+ "A mesma rota, escrita à mão"

    Quando você precisa do envelope — ou quando a rota mora dentro do seu
    serviço, com controller e dependências próprias (passo 3) —, a rota manual
    é exatamente esta:

    ```python title="zap_manual.py" hl_lines="9 14"
    from fastapi import Depends, FastAPI

    from tempest_fastapi_sdk.integrations.messaging.zap import (
        ZapWebhookDelivery,
        make_zap_webhook_dependency,
    )

    app: FastAPI = FastAPI()
    verify_zap = make_zap_webhook_dependency(secret="meu-segredo")


    @app.post("/webhooks/zap", include_in_schema=False)
    async def receive_zap(
        delivery: ZapWebhookDelivery = Depends(verify_zap),
    ) -> dict[str, str]:
        """Recebe toda entrega do gateway e mostra as mensagens que chegam.

        Args:
            delivery (ZapWebhookDelivery): A entrega com a assinatura já
                conferida e o corpo já validado.

        Returns:
            dict[str, str]: Sempre ``200``, para o gateway não reentregar.
        """
        if delivery.inbound is not None:
            print(f"{delivery.inbound.push_name}: {delivery.inbound.text}")
        return {"status": "ok"}
    ```

    A diferença está no corpo: a fábrica despacha por `event` e responde sozinha,
    com o nome do evento de volta. A sua faz o que quiser depois de receber o
    `ZapWebhookDelivery` — e continua podendo usar `Depends(verify_zap)`, que é
    a mesma dependência que a fábrica monta para você.

### O que a dependência resolve por você

A assinatura usa o header `x-zap-signature`, HMAC-SHA256 em hex, prefixo
`sha256=`, calculado sobre o corpo **cru** antes de qualquer parse e
comparado com `hmac.compare_digest`. Os dois erros clássicos ficam
impossíveis: verificar sobre o JSON re-serializado (que muda espaço e ordem
de chave) e comparar com `==`. Header sem o prefixo `sha256=` também é `401`,
mesmo com o hex certo.

!!! tip "Responda 2xx para tudo que passou pela assinatura"
    O gateway trata qualquer resposta fora de 2xx como falha e reentrega com
    backoff, até `CALLBACK_MAX_ATTEMPTS` tentativas (3 por default). Um `422`
    para um evento novo não protege nada: só gera tentativas, e o gateway
    desiste da entrega no fim. Por isso a rota **não** falha com corpo que
    ela não reconhece — responde `200` com o nome do evento e registra em
    `debug` (veja o passo 2).

Para ver funcionando sem o gateway, assine o corpo como ele assina
(HMAC-SHA256 do corpo cru, em hex, com prefixo `sha256=`) e mande pelo
`TestClient`. O corpo é o exemplo de entrada do README do gateway:

```python title="simular.py"
import hashlib
import hmac

from fastapi.testclient import TestClient

from zap_minimo import app

SECRET: str = "meu-segredo"
BODY: bytes = (
    '{"event":"message.received","messageId":"ABCD1234",'
    '"from":"5511999999999@s.whatsapp.net","chatKey":"5511999999999",'
    '"pushName":"Fulano","text":"Olá!","mediaType":null,"mediaUrl":null,'
    '"timestamp":"2026-04-21T18:30:00.000Z"}'
).encode()


def sign(body: bytes) -> str:
    """Assina o corpo como o gateway assina.

    Args:
        body (bytes): Os bytes exatos que vão no ``POST``.

    Returns:
        str: O valor do header ``x-zap-signature``.
    """
    digest: str = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


client: TestClient = TestClient(app)
signed = client.post(
    "/webhooks/zap",
    content=BODY,
    headers={"x-zap-signature": sign(BODY)},
)
print(signed.status_code, signed.json())
unsigned = client.post("/webhooks/zap", content=BODY)
print(unsigned.status_code, unsigned.json())
```

```console
$ python simular.py
Fulano: Olá!
200 {'ok': True, 'event': 'message.received'}
401 {'detail': 'Invalid zap-api webhook signature'}
```

## Passo 2 — Despachar por tipo com os enums

Cada valor que o gateway manda como string tem um enum no SDK. Todos são
`StrEnum`: o membro **é igual** à string do fio, então código que já compara
com `"audio"` continua funcionando — o enum só dá autocomplete, checagem de
tipo e um lugar para ler o que existe.

| Enum | Onde aparece | Valores |
| --- | --- | --- |
| `ZapWebhookEvent` | `delivery.event` | `MESSAGE_RECEIVED`, `MESSAGE_SENT`, `MESSAGE_DELIVERED`, `MESSAGE_READ`, `MESSAGE_FAILED` |
| `ZapInboundMediaType` | `message.media_type` | `IMAGE`, `VIDEO`, `AUDIO`, `DOCUMENT`, `STICKER` (`None` é texto puro) |
| `ZapJidServer` | `message.from_server` | `USER`, `LID`, `GROUP`, `BROADCAST`, `NEWSLETTER` e mais cinco do Baileys |
| `ZapOutboundKind` | `callback.kind` | `TEXT`, `IMAGE`, `VIDEO`, `AUDIO`, `DOCUMENT`, `REACTION` |
| `AcceptedResponseStatus` | `callback.status` | `SENT`, `DELIVERED`, `READ`, `FAILED` (mais `QUEUED` e `SENDING`, que nunca chegam no webhook) |

O passo 1 despachou por você; aqui a rota é sua de novo, porque o `event` é o
que decide o caminho e ele mora no `ZapWebhookDelivery` — a fábrica entrega ao
handler só o corpo (`ZapInboundMessage` ou `ZapStatusCallback`), já aberto no
modelo certo. Para usar a fábrica com estas funções, embrulhe cada uma num
handler `async def ... -> None` e passe-o em `on_inbound` / `on_status`: elas
são síncronas e devolvem `str`, e a fábrica faz `await` no handler.

```python title="zap_despacho.py" hl_lines="27 29 31 34 36 51 53"
from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.integrations.messaging.zap import (
    AcceptedResponseStatus,
    ZapInboundMediaType,
    ZapInboundMessage,
    ZapJidServer,
    ZapOutboundKind,
    ZapStatusCallback,
    ZapWebhookDelivery,
    make_zap_webhook_dependency,
)

app: FastAPI = FastAPI()
verify_zap = make_zap_webhook_dependency(secret="meu-segredo")


def describe_inbound(message: ZapInboundMessage) -> str:
    """Decide o que fazer com uma mensagem recebida, só olhando enums.

    Args:
        message (ZapInboundMessage): A mensagem já validada.

    Returns:
        str: Um rótulo do caminho escolhido.
    """
    if message.from_server is ZapJidServer.BROADCAST:
        return "ignorar: story de status"
    if message.from_server is ZapJidServer.GROUP:
        return f"grupo: {message.text}"
    match message.media_type:
        case None:
            return f"texto: {message.text}"
        case ZapInboundMediaType.AUDIO:
            return "áudio: transcrever"
        case ZapInboundMediaType.IMAGE | ZapInboundMediaType.VIDEO:
            return f"visual, legenda: {message.text}"
        case _:
            return f"arquivo: {message.media_type}"


def describe_status(callback: ZapStatusCallback) -> str:
    """Decide o que fazer com um status de envio, só olhando enums.

    Args:
        callback (ZapStatusCallback): O status já validado.

    Returns:
        str: Um rótulo do caminho escolhido.
    """
    if callback.status == AcceptedResponseStatus.FAILED:
        return f"falhou: {callback.error}"
    if callback.kind == ZapOutboundKind.REACTION:
        return "reação entregue: nada a mostrar"
    return f"{callback.kind} agora está {callback.status}"


@app.post("/webhooks/zap", include_in_schema=False)
async def receive_zap(
    delivery: ZapWebhookDelivery = Depends(verify_zap),
) -> dict[str, str]:
    """Despacha cada entrega para o tratador do tipo dela.

    Args:
        delivery (ZapWebhookDelivery): A entrega verificada.

    Returns:
        dict[str, str]: O caminho escolhido, ou ``ignored`` para evento que
        o SDK não modela.
    """
    if delivery.inbound is not None:
        return {"handled": describe_inbound(delivery.inbound)}
    if delivery.status is not None:
        return {"handled": describe_status(delivery.status)}
    return {"handled": "ignored", "event": delivery.event_name}
```

Mandando uma entrega de cada tipo, assinadas como no passo 1:

```python title="simular_despacho.py"
import hashlib
import hmac
import json
from typing import Any

from fastapi.testclient import TestClient

from zap_despacho import app

SECRET: str = "meu-segredo"
WHEN: str = "2026-04-21T18:30:00.000Z"
DELIVERIES: list[dict[str, Any]] = [
    {
        "event": "message.received",
        "messageId": "M1",
        "text": "Olá!",
        "from": "5511999999999@s.whatsapp.net",
        "timestamp": WHEN,
    },
    {
        "event": "message.received",
        "messageId": "M2",
        "mediaType": "audio",
        "from": "123456789012345@lid",
        "timestamp": WHEN,
    },
    {
        "event": "message.received",
        "messageId": "M3",
        "text": "bom dia",
        "from": "120363000000000000@g.us",
        "timestamp": WHEN,
    },
    {
        "event": "message.received",
        "messageId": "M4",
        "mediaType": "image",
        "from": "status@broadcast",
        "timestamp": WHEN,
    },
    {
        "event": "message.read",
        "id": "out-1",
        "consumer": "bot",
        "kind": "text",
        "to": "5511999999999",
        "status": "read",
        "timestamp": WHEN,
    },
    {
        "event": "message.failed",
        "id": "out-2",
        "consumer": "bot",
        "kind": "image",
        "to": "5511999999999",
        "status": "failed",
        "error": "number not on WhatsApp",
        "timestamp": WHEN,
    },
    {"event": "message.edited", "messageId": "M1", "timestamp": WHEN},
]


def sign(body: bytes) -> str:
    """Assina o corpo como o gateway assina.

    Args:
        body (bytes): Os bytes exatos que vão no ``POST``.

    Returns:
        str: O valor do header ``x-zap-signature``.
    """
    digest: str = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


client: TestClient = TestClient(app)
for delivery in DELIVERIES:
    body: bytes = json.dumps(delivery).encode()
    response = client.post(
        "/webhooks/zap",
        content=body,
        headers={"x-zap-signature": sign(body)},
    )
    print(f"{delivery['event']:<17} {response.status_code} {response.json()}")
```

```console
$ python simular_despacho.py
message.received  200 {'handled': 'texto: Olá!'}
message.received  200 {'handled': 'áudio: transcrever'}
message.received  200 {'handled': 'grupo: bom dia'}
message.received  200 {'handled': 'ignorar: story de status'}
message.read      200 {'handled': 'text agora está read'}
message.failed    200 {'handled': 'falhou: number not on WhatsApp'}
message.edited    200 {'handled': 'ignored', 'event': 'message.edited'}
```

Vamos por partes.

### Qual corpo veio: `inbound`, `status` ou nenhum

O mesmo URL recebe `message.received` e `message.{sent,delivered,read,failed}`,
com formatos diferentes. A dependência já devolve o corpo validado no campo
certo:

| `delivery.event` | Corpo em |
| --- | --- |
| `ZapWebhookEvent.MESSAGE_RECEIVED` | `delivery.inbound` (`ZapInboundMessage`) |
| `MESSAGE_SENT`, `MESSAGE_DELIVERED`, `MESSAGE_READ`, `MESSAGE_FAILED` | `delivery.status` (`ZapStatusCallback`) |
| `None` | nenhum — logue `event_name` e `payload` e responda `200` |

`event` só vem preenchido quando o corpo correspondente validou, então
perguntar `delivery.inbound is not None` e `delivery.status is not None` é o
despacho completo — e é a forma que o type-checker entende, porque estreita o
`Optional` para você. `None` cobre um evento que esta versão do SDK não
conhece (o `message.edited` acima) e um evento conhecido cujo corpo não bate
com o modelo.

### `from_server`: que tipo de endereço escreveu

`from_` é o JID cru, `<usuário>@<servidor>`, e o servidor diz se é uma
pessoa (`s.whatsapp.net` ou `lid`), um grupo (`g.us`), uma story de status
(`status@broadcast`) ou um canal (`newsletter`). `message.from_server` lê
esse pedaço e devolve o membro de `ZapJidServer` — `None` quando não há `@`
ou o servidor é desconhecido.

!!! warning "O gateway não filtra pelo servidor do JID"
    O `publishMessage` do gateway (`src/services/whatsapp.service.ts`, commit
    `d0477d8`) repassa toda mensagem `notify` com conteúdo, sem olhar o
    servidor do `remoteJid` — lido no código, não medido numa sessão real.
    Então uma story de status (`status@broadcast`) ou um post de canal pode
    chegar como `message.received`, e responder a ela, ou guardá-la como
    conversa, quase nunca é o que você quer. Filtre por `from_server`.

### `is` para o envelope, `==` para o corpo

`ZapWebhookDelivery` é um dataclass, então `delivery.event` guarda o próprio
membro e `delivery.event is ZapWebhookEvent.MESSAGE_RECEIVED` funciona.
`from_server` também devolve o membro. Já dentro de `ZapInboundMessage` e
`ZapStatusCallback`, como em todo `BaseSchema`, os campos de enum guardam o
**valor** — compare com `==` (ou use `match`, que compara com `==`).

`callback.kind` é `str` de propósito, não `ZapOutboundKind`: o gateway o
declara como `string`, e um tipo de envio que ele ganhar depois não pode
transformar o status de uma mensagem numa entrega `event=None`. Compare com
o enum mesmo assim — `callback.kind == ZapOutboundKind.REACTION`.

## Passo 3 — A camada de serviço

Até aqui a regra mora na rota. Num serviço de verdade ela vai para camadas,
cada uma com um trabalho só:

| Camada | Trabalho aqui |
| --- | --- |
| **model** | as tabelas: mensagem recebida (`UNIQUE` em `message_id`) e status de envio (`UNIQUE` em `outbound_id`) |
| **repository** | `BaseRepository` do SDK — `add`, `get_or_none`, `update` |
| **service** | a regra: filtrar quem escreveu, baixar a mídia, guardar uma vez, responder, aplicar status só para frente |
| **controller** | escolher o método do serviço pelo corpo que a entrega trouxe |
| **router** | receber o `POST`, verificar pela dependência e delegar — uma linha |

O arquivo abaixo tem todas, em ordem, para caber num exemplo que roda. O
`fake_gateway` faz o papel do gateway através de um `httpx.MockTransport`:
aceita o `send-text` (respondendo `deduped=True` quando a `idempotency-key`
repete, como o gateway real) e serve 2 KiB de mídia.

```python title="zap_servico.py" hl_lines="64 76 138 157 162 179 284"
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseModel,
    BaseRepository,
    BaseStrEnum,
    HTTPClient,
)
from tempest_fastapi_sdk.exceptions import ConflictException
from tempest_fastapi_sdk.integrations.messaging.zap import (
    SendTextRequest,
    ZapClient,
    ZapInboundMessage,
    ZapJidServer,
    ZapStatusCallback,
    ZapWebhookDelivery,
    is_forward_transition,
    make_zap_webhook_dependency,
)
from tempest_fastapi_sdk.schemas import BaseSchema


SENT_KEYS: set[str] = set()


def fake_gateway(request: httpx.Request) -> httpx.Response:
    """Faz o papel do gateway: aceita o envio e serve a mídia.

    Args:
        request (httpx.Request): A chamada que o ``ZapClient`` fez.

    Returns:
        httpx.Response: O que o gateway responderia — com ``deduped=True``
        quando a ``idempotency-key`` já foi usada.
    """
    if request.url.path == "/message/send-text":
        key: str = request.headers["idempotency-key"]
        deduped: bool = key in SENT_KEYS
        SENT_KEYS.add(key)
        print(f"  gateway: send-text key={key} deduped={deduped}")
        return httpx.Response(
            202, json={"id": "out-1", "status": "queued", "deduped": deduped}
        )
    return httpx.Response(200, content=b"\x00" * 2048)


db: AsyncDatabaseManager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
verify_zap = make_zap_webhook_dependency(secret="meu-segredo")


class ZapMessageModel(BaseModel):
    """Uma mensagem recebida, guardada uma vez só."""

    __tablename__ = "zap_messages"

    message_id: Mapped[str] = mapped_column(String(128), unique=True)
    chat_key: Mapped[str | None] = mapped_column(String(32), default=None)
    text: Mapped[str | None] = mapped_column(default=None)
    media_type: Mapped[str | None] = mapped_column(String(16), default=None)
    media_size: Mapped[int | None] = mapped_column(default=None)


class ZapDeliveryModel(BaseModel):
    """O último status conhecido de uma mensagem enviada."""

    __tablename__ = "zap_deliveries"

    outbound_id: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16))


class ZapOutcome(BaseStrEnum):
    """O que o serviço fez com uma entrega — a resposta da rota."""

    STORED = "stored"
    DUPLICATE = "duplicate"
    MEDIA_LOST = "media-lost"
    SKIPPED = "skipped"
    APPLIED = "applied"
    STALE = "stale"
    IGNORED = "ignored"


class ZapWebhookResponseSchema(BaseSchema):
    """Corpo da resposta ao gateway.

    Attributes:
        outcome (ZapOutcome): O que aconteceu com a entrega.
    """

    outcome: ZapOutcome


class ZapInboxService:
    """Regra de negócio do WhatsApp: guardar, responder e acompanhar envios."""

    def __init__(
        self,
        messages: BaseRepository[ZapMessageModel],
        deliveries: BaseRepository[ZapDeliveryModel],
        client: ZapClient,
    ) -> None:
        """Guarda as dependências.

        Args:
            messages (BaseRepository[ZapMessageModel]): Mensagens recebidas.
            deliveries (BaseRepository[ZapDeliveryModel]): Status dos envios.
            client (ZapClient): O cliente do gateway.
        """
        self.messages: BaseRepository[ZapMessageModel] = messages
        self.deliveries: BaseRepository[ZapDeliveryModel] = deliveries
        self.client: ZapClient = client

    async def receive(self, message: ZapInboundMessage) -> ZapOutcome:
        """Guarda uma mensagem recebida e responde a quem escreveu.

        O ``UNIQUE`` em ``message_id`` é o claim: a reentrega do gateway
        bate nele e vira ``duplicate``. A resposta sai nos dois casos, com
        ``idempotency_key`` derivada de ``message_id``: se o envio falhou
        na primeira entrega, a reentrega o completa; se não falhou, o
        gateway devolve a linha original (``deduped=True``) em vez de
        mandar duas vezes.

        Args:
            message (ZapInboundMessage): A mensagem já validada.

        Returns:
            ZapOutcome: O que aconteceu com ela.
        """
        if message.from_server not in (ZapJidServer.USER, ZapJidServer.LID):
            return ZapOutcome.SKIPPED
        if message.media_type is not None and message.media_url is None:
            return ZapOutcome.MEDIA_LOST
        media_size: int | None = None
        if message.media_type is not None:
            media: bytes = await self.client.get_message_media(message.message_id)
            media_size = len(media)
        stored: bool = True
        try:
            await self.messages.add(
                ZapMessageModel(
                    message_id=message.message_id,
                    chat_key=message.chat_key,
                    text=message.text,
                    media_type=message.media_type,
                    media_size=media_size,
                )
            )
        except ConflictException:
            stored = False
        if message.chat_key is not None:
            await self.client.send_text(
                body=SendTextRequest(to=message.chat_key, text="Recebemos, obrigado!"),
                idempotency_key=f"reply-{message.message_id}",
            )
        return ZapOutcome.STORED if stored else ZapOutcome.DUPLICATE

    async def track(self, callback: ZapStatusCallback) -> ZapOutcome:
        """Aplica um status de envio só quando ele avança o guardado.

        Args:
            callback (ZapStatusCallback): O status já validado.

        Returns:
            ZapOutcome: ``applied`` ou ``stale``.
        """
        row: ZapDeliveryModel | None = await self.deliveries.get_or_none(
            filters={"outbound_id": callback.id}
        )
        current: str | None = row.status if row is not None else None
        if not is_forward_transition(current, callback.status):
            return ZapOutcome.STALE
        if row is None:
            await self.deliveries.add(
                ZapDeliveryModel(outbound_id=callback.id, status=callback.status)
            )
        else:
            row.status = callback.status
            await self.deliveries.update(row)
        return ZapOutcome.APPLIED


class ZapWebhookController:
    """Liga a entrega do gateway ao método certo do serviço."""

    def __init__(self, service: ZapInboxService) -> None:
        """Guarda o serviço.

        Args:
            service (ZapInboxService): A regra de negócio.
        """
        self.service: ZapInboxService = service

    async def handle(self, delivery: ZapWebhookDelivery) -> ZapWebhookResponseSchema:
        """Despacha pelo corpo que a entrega trouxe.

        Args:
            delivery (ZapWebhookDelivery): A entrega verificada.

        Returns:
            ZapWebhookResponseSchema: O que aconteceu, sempre com ``200``.
        """
        if delivery.inbound is not None:
            outcome: ZapOutcome = await self.service.receive(delivery.inbound)
        elif delivery.status is not None:
            outcome = await self.service.track(delivery.status)
        else:
            outcome = ZapOutcome.IGNORED
        return ZapWebhookResponseSchema(outcome=outcome)


def get_zap_client(request: Request) -> ZapClient:
    """Devolve o cliente do gateway que o lifespan abriu.

    Args:
        request (Request): O request corrente.

    Returns:
        ZapClient: O cliente compartilhado pela aplicação.
    """
    client: ZapClient = request.app.state.zap
    return client


def get_zap_inbox_service(
    session: AsyncSession = Depends(db.session_dependency),
    client: ZapClient = Depends(get_zap_client),
) -> ZapInboxService:
    """Monta o serviço sobre a sessão do request.

    Args:
        session (AsyncSession): A sessão do request.
        client (ZapClient): O cliente do gateway.

    Returns:
        ZapInboxService: O serviço pronto.
    """
    return ZapInboxService(
        messages=BaseRepository(session, model=ZapMessageModel),
        deliveries=BaseRepository(session, model=ZapDeliveryModel),
        client=client,
    )


def get_zap_webhook_controller(
    service: ZapInboxService = Depends(get_zap_inbox_service),
) -> ZapWebhookController:
    """Monta o controller sobre o serviço.

    Args:
        service (ZapInboxService): O serviço do request.

    Returns:
        ZapWebhookController: O controller pronto.
    """
    return ZapWebhookController(service)


router: APIRouter = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/zap", include_in_schema=False)
async def receive_zap(
    delivery: ZapWebhookDelivery = Depends(verify_zap),
    controller: ZapWebhookController = Depends(get_zap_webhook_controller),
) -> ZapWebhookResponseSchema:
    """Recebe as entregas do gateway, de entrada e de status.

    Args:
        delivery (ZapWebhookDelivery): A entrega verificada.
        controller (ZapWebhookController): O controller do request.

    Returns:
        ZapWebhookResponseSchema: O que aconteceu com a entrega.
    """
    return await controller.handle(delivery)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Cria as tabelas e abre o cliente do gateway junto com o serviço.

    Args:
        app (FastAPI): A aplicação.

    Yields:
        None: Enquanto o serviço está de pé.
    """
    await db.create_tables()
    async with HTTPClient(
        base_url="http://127.0.0.1:3000",
        default_headers={"x-api-key": "<sua chave>"},
        transport=httpx.MockTransport(fake_gateway),
    ) as http:
        app.state.zap = ZapClient(http)
        yield
    await db.disconnect()


app: FastAPI = FastAPI(lifespan=lifespan)
app.include_router(router)
```

Rodando uma sequência de entregas contra ele:

```python title="rodar_servico.py"
from fastapi.testclient import TestClient

from test_zap_servico import inbound, post, status
from zap_servico import app

USER: str = "5511999999999@s.whatsapp.net"

with TestClient(app) as client:
    for label, payload in [
        ("texto", inbound("A1", USER, chatKey="5511999999999", text="Oi")),
        ("mesma entrega", inbound("A1", USER, chatKey="5511999999999", text="Oi")),
        (
            "áudio",
            inbound(
                "A2",
                USER,
                chatKey="5511999999999",
                mediaType="audio",
                mediaUrl="/message/A2/media",
            ),
        ),
        ("áudio perdido", inbound("A3", USER, mediaType="audio")),
        ("grupo", inbound("A4", "120363000000000000@g.us", text="bom dia")),
        ("delivered", status("out-1", "delivered")),
        ("sent atrasado", status("out-1", "sent")),
        ("read", status("out-1", "read")),
        ("evento novo", {"event": "message.edited", "messageId": "A1"}),
    ]:
        response = post(client, payload)
        print(f"{label:<14} {response.status_code} {response.json()}")
```

```console
$ python rodar_servico.py
  gateway: send-text key=reply-A1 deduped=False
texto          200 {'outcome': 'stored'}
  gateway: send-text key=reply-A1 deduped=True
mesma entrega  200 {'outcome': 'duplicate'}
  gateway: send-text key=reply-A2 deduped=False
áudio          200 {'outcome': 'stored'}
áudio perdido  200 {'outcome': 'media-lost'}
grupo          200 {'outcome': 'skipped'}
delivered      200 {'outcome': 'applied'}
sent atrasado  200 {'outcome': 'stale'}
read           200 {'outcome': 'applied'}
evento novo    200 {'outcome': 'ignored'}
```

`rodar_servico.py` reaproveita os ajudantes `inbound`, `status` e `post` do
teste do passo 4. A `ConflictException` da segunda entrega também sai no log,
como `WARNING` do `BaseRepository`
(`IntegrityError on ZapMessageModel.add: unique violation; table=zap_messages;
columns=message_id; ...`) — é o claim funcionando, não um erro.

Agora cada camada.

### O service: a regra inteira num lugar

**Idempotência por `messageId`.** As entregas passam pela fila
`callback_deliveries` do gateway, com retry: a mesma mensagem chega mais de
uma vez quando a sua resposta se perde, ou quando o gateway reinicia no meio
de um `POST`. O `UNIQUE` em `message_id` é o claim — o insert de quem chega
segundo bate nele, o `BaseRepository.add` levanta `ConflictException`, e o
serviço devolve `duplicate`. Sem `set` em memória, sem corrida entre réplicas.
Veja [Idempotência](idempotency.md) para o mesmo raciocínio em rotas suas.

**A resposta sai nos dois caminhos.** Se o serviço respondesse só no
`stored`, um envio que falhasse depois do insert nunca seria refeito: a
reentrega cairia em `duplicate`. Por isso a resposta sai sempre, com
`idempotency_key=f"reply-{message_id}"` — na reentrega o gateway devolve a
linha original com `deduped=True` em vez de mandar duas vezes (a segunda
linha `gateway:` da saída acima).

**`chatKey`, não `from`.** `from_` é o endereço cru, e a mesma pessoa pode
chegar por um número e depois por um LID — agrupar por `from_` parte uma
conversa em duas. `chat_key` são os dígitos do número, já resolvidos pelo
gateway a partir do LID; é também o `to` que o `send_text` aceita. Vem `None`
para grupo, broadcast, canal e LID que o gateway não conseguiu mapear — aí
não há para quem responder, e o serviço só guarda.

**Mídia só pelo gateway.** O gateway baixa a mídia quando a mensagem chega,
e o WhatsApp não a serve de novo depois. `media_url` aponta para
`GET /message/{messageId}/media` no gateway, e
`ZapClient.get_message_media(message_id)` lê esses bytes com a sua
`x-api-key`. Quando `media_type` vem preenchido e `media_url` vem `None`, o
download falhou ou passou de `MEDIA_MAX_BYTES`: a mídia está perdida, não
adianta tentar de novo, e o serviço devolve `media-lost`. O download vem
**antes** do insert: se falhar, a rota responde erro, o gateway reentrega, e
a mensagem ainda não foi reivindicada.

**Status fora de ordem.** Cada entrega tem retry e backoff próprios, então
um `sent` cujo primeiro `POST` falhou pode chegar depois do `delivered`. E o
gateway protege a **própria** linha contra regressão, mas manda o callback
mesmo assim: um `delivered` atrasado depois de um `read` chega até você.
`is_forward_transition(current, new)` responde se o status novo avança o que
você guardou:

- a escada é `queued < sending < sent < delivered < read`;
- repetir o status atual, ou descer a escada, não avança;
- `failed` é terminal — nada o sobrescreve;
- `failed` só sobrescreve `queued`, `sending` e `sent`, porque `delivered` e
  `read` já provam que a mensagem chegou.

`current` aceita `str` porque é o que a coluna guarda. Chaveie pelo `id` do
callback — a linha do outbox que o `202` do envio devolveu.

### O controller: escolher o caminho, nada mais

`ZapWebhookController.handle` olha qual corpo a entrega trouxe e chama
`receive` ou `track`. Não abre sessão, não decide regra, não conhece o
gateway. Se amanhã o mesmo evento precisar disparar duas coisas (guardar e
notificar outro serviço), é aqui que as duas chamadas se juntam.

### As dependências e o router

`get_zap_inbox_service` monta o serviço sobre a sessão do request
(`db.session_dependency`) e sobre o `ZapClient` que o `lifespan` abriu e
guardou em `app.state`; `get_zap_webhook_controller` monta o controller sobre
o serviço. O router recebe os dois por `Depends` e fica com uma linha só,
`return await controller.handle(delivery)`.

!!! note "Por que o `ZapClient` nasce no `lifespan`"
    Um `HTTPClient` fechado não reabre. Criado no nível do módulo, ele
    sobrevive a um `lifespan` só: o segundo `TestClient(app)` da suíte acha o
    cliente fechado e falha com `RuntimeError: Cannot send a request, as the
    client has been closed`. Criado dentro do `lifespan`, cada subida da
    aplicação ganha o seu.

### Onde cada pedaço mora no seu serviço

O arquivo único é para o exemplo rodar. No layout de serviço Tempest, cada
classe vai para a camada dela:

```text
src/
├── api/
│   ├── dependencies/
│   │   ├── controllers.py   # get_zap_webhook_controller
│   │   └── services.py      # get_zap_client, get_zap_inbox_service
│   └── routers/
│       └── webhooks.py      # router + receive_zap
├── controllers/
│   └── zap_webhook.py       # ZapWebhookController
├── services/
│   └── zap_inbox.py         # ZapInboxService
├── schemas/
│   └── zap.py               # ZapOutcome, ZapWebhookResponseSchema
└── db/
    └── models/
        └── zap.py           # ZapMessageModel, ZapDeliveryModel
```

`verify_zap` vai junto das outras dependências de autenticação
(`api/dependencies/auth.py`), lendo o secret do settings.

## Passo 4 — Testando sem o gateway

Assine o corpo como o gateway assina e mande pelo `TestClient`. A fixture
abre o `TestClient` como context manager para o `lifespan` rodar — sem isso
nem o `create_tables` nem o `ZapClient` rodam, e a primeira entrega falha com
`AttributeError: 'State' object has no attribute 'zap'`.

```python title="test_zap_servico.py"
import hashlib
import hmac
import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from zap_servico import app

SECRET: str = "meu-segredo"
WHEN: str = "2026-04-21T18:30:00.000Z"


def inbound(message_id: str, sender: str, **fields: Any) -> dict[str, Any]:
    """Monta o corpo de um ``message.received`` como o gateway manda.

    Args:
        message_id (str): O ``messageId``.
        sender (str): O JID cru em ``from``.
        **fields (Any): Campos a mais (``text``, ``chatKey``, ``mediaType``…).

    Returns:
        dict[str, Any]: O corpo pronto para assinar.
    """
    return {
        "event": "message.received",
        "messageId": message_id,
        "from": sender,
        "timestamp": WHEN,
        **fields,
    }


def status(outbound_id: str, value: str) -> dict[str, Any]:
    """Monta o corpo de um callback de status como o gateway manda.

    Args:
        outbound_id (str): O ``id`` que o ``202`` do envio devolveu.
        value (str): ``sent``, ``delivered``, ``read`` ou ``failed``.

    Returns:
        dict[str, Any]: O corpo pronto para assinar.
    """
    return {
        "event": f"message.{value}",
        "id": outbound_id,
        "consumer": "bot",
        "to": "5511999999999",
        "kind": "text",
        "status": value,
        "timestamp": WHEN,
    }


def post(client: TestClient, payload: dict[str, Any], secret: str = SECRET) -> Any:
    """Assina e entrega um corpo, como o worker de callbacks do gateway.

    Args:
        client (TestClient): O cliente da aplicação.
        payload (dict[str, Any]): O corpo.
        secret (str): O secret usado na assinatura.

    Returns:
        Any: A resposta da rota.
    """
    body: bytes = json.dumps(payload).encode()
    digest: str = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/zap",
        content=body,
        headers={"x-zap-signature": f"sha256={digest}"},
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Sobe a aplicação com o lifespan (tabelas + cliente do gateway).

    Yields:
        TestClient: O cliente da aplicação.
    """
    with TestClient(app) as test_client:
        yield test_client


def test_wrong_secret_is_401(client: TestClient) -> None:
    """Entrega assinada com outro secret não entra."""
    response = post(client, inbound("A1", "5511999999999@s.whatsapp.net"), "outro")
    assert response.status_code == 401


def test_message_is_stored_once(client: TestClient) -> None:
    """A reentrega do gateway vira ``duplicate``, não uma segunda linha."""
    payload = inbound(
        "A2", "5511999999999@s.whatsapp.net", chatKey="5511999999999", text="Oi"
    )
    assert post(client, payload).json() == {"outcome": "stored"}
    assert post(client, payload).json() == {"outcome": "duplicate"}


def test_group_is_skipped(client: TestClient) -> None:
    """Mensagem de grupo não é guardada nem respondida."""
    payload = inbound("A3", "120363000000000000@g.us", text="bom dia")
    assert post(client, payload).json() == {"outcome": "skipped"}


def test_lost_media(client: TestClient) -> None:
    """``mediaType`` sem ``mediaUrl`` é mídia perdida."""
    payload = inbound("A4", "5511999999999@s.whatsapp.net", mediaType="audio")
    assert post(client, payload).json() == {"outcome": "media-lost"}


def test_late_status_is_stale(client: TestClient) -> None:
    """Um ``delivered`` depois do ``read`` não regride o status."""
    assert post(client, status("out-9", "read")).json() == {"outcome": "applied"}
    assert post(client, status("out-9", "delivered")).json() == {"outcome": "stale"}


def test_unknown_event_is_200(client: TestClient) -> None:
    """Evento que o SDK não modela responde ``200``, para não gerar retry."""
    payload = {"event": "message.edited", "messageId": "A2"}
    assert post(client, payload).json() == {"outcome": "ignored"}
```

```console
$ pytest test_zap_servico.py -v
test_zap_servico.py::test_wrong_secret_is_401 PASSED
test_zap_servico.py::test_message_is_stored_once PASSED
test_zap_servico.py::test_group_is_skipped PASSED
test_zap_servico.py::test_lost_media PASSED
test_zap_servico.py::test_late_status_is_stale PASSED
test_zap_servico.py::test_unknown_event_is_200 PASSED
============================== 6 passed in 0.66s ===============================
```

!!! check "Assinatura cruzada com o gateway"
    O `hmac` da biblioteca padrão produz o mesmo header que o `signPayload`
    do gateway (`src/utils/signature.ts`): a suíte do SDK fixa duas
    assinaturas geradas pelo **próprio** gateway e confere que elas passam
    (`tests/integrations/messaging/zap/test_webhooks.py`).

## Recapitulando

- Registre o webhook **com** `--secret`; sem ele o gateway não assina.
- `make_zap_webhook_dependency(secret=...)` verifica o HMAC sobre o corpo cru
  e devolve um `ZapWebhookDelivery`.
- Despache por `delivery.inbound` / `delivery.status`; o que não tem nenhum
  dos dois responde `200` e vai para o log.
- Compare com os enums — `ZapWebhookEvent`, `ZapInboundMediaType`,
  `ZapJidServer`, `ZapOutboundKind`, `AcceptedResponseStatus` —, não com
  string solta; `is` no envelope e no `from_server`, `==` nos campos do corpo.
- Filtre por `from_server`: grupo, story de status e canal também chegam.
- Idempotência por `UNIQUE` em `message_id` (entrada) e `id` (status); a
  resposta a quem escreveu usa `idempotency_key` derivada de `message_id`.
- Agrupe e responda por `chat_key`; `None` é grupo ou LID sem número.
- Mídia só por `get_message_media`, e `media_url: None` com `media_type`
  significa perdida.
- `is_forward_transition` antes de gravar qualquer status.
- Router → controller → service → repository: a rota fica com uma linha, e a
  regra fica testável sem HTTP.

