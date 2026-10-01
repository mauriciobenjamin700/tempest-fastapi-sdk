# Receber mensagens do WhatsApp (zap-api)

A receita [WhatsApp (zap-api)](zap.md) cobre o lado de **saída**: enviar e
receber um `202`. Esta cobre o outro lado — o gateway fazendo `POST` no seu
serviço quando uma mensagem **chega** e quando uma que você enviou muda de
status.

Ao final você terá uma rota que:

- recusa com `401` toda entrega que não veio do gateway;
- separa mensagem recebida de callback de status pelo `event`;
- guarda cada mensagem **uma vez**, mesmo com o gateway reentregando;
- agrupa a conversa pelo `chatKey`, não pelo `from`;
- baixa a mídia pelo gateway, o único lugar onde ela existe;
- nunca regride um status por causa de um callback atrasado.

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

## O receiver completo

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.messaging.zap import (
    ZapClient,
    ZapInboundMessage,
    ZapStatusCallback,
    ZapWebhookDelivery,
    ZapWebhookEvent,
    is_forward_transition,
    make_zap_webhook_dependency,
)

ZAP_BASE_URL: str = "http://127.0.0.1:3000"
ZAP_API_KEY: str = "<sua chave>"
ZAP_WEBHOOK_SECRET: str = "meu-segredo"

http: HTTPClient = HTTPClient(
    base_url=ZAP_BASE_URL,
    default_headers={"x-api-key": ZAP_API_KEY},
)
zap: ZapClient = ZapClient(http)
verify_zap = make_zap_webhook_dependency(secret=ZAP_WEBHOOK_SECRET)

processed_messages: set[str] = set()
conversations: dict[str, list[str]] = {}
delivery_status: dict[str, str] = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Abre o cliente do gateway junto com o serviço.

    Args:
        app (FastAPI): A aplicação.

    Yields:
        None: Enquanto o serviço está de pé.
    """
    async with http:
        yield


app: FastAPI = FastAPI(lifespan=lifespan)


async def handle_inbound(message: ZapInboundMessage) -> str:
    """Guarda uma mensagem recebida, uma vez só.

    Args:
        message (ZapInboundMessage): A mensagem já validada.

    Returns:
        str: O que aconteceu com ela.
    """
    if message.message_id in processed_messages:
        return "duplicate"
    processed_messages.add(message.message_id)

    if message.chat_key is None:
        return "no-chat-key"
    conversations.setdefault(message.chat_key, []).append(message.text or "")

    if message.media_type is None:
        return "stored"
    if message.media_url is None:
        return "media-lost"
    media: bytes = await zap.get_message_media(message.message_id)
    Path(f"{message.message_id}.bin").write_bytes(media)
    return "stored-with-media"


def handle_status(callback: ZapStatusCallback) -> str:
    """Aplica um status de entrega só se ele avança o estado.

    Args:
        callback (ZapStatusCallback): O callback já validado.

    Returns:
        str: ``applied`` ou ``stale``.
    """
    current: str | None = delivery_status.get(callback.id)
    if not is_forward_transition(current, callback.status):
        return "stale"
    delivery_status[callback.id] = callback.status
    return "applied"


@app.post("/webhooks/zap", include_in_schema=False)
async def receive_zap(
    delivery: ZapWebhookDelivery = Depends(verify_zap),
) -> dict[str, str]:
    """Recebe as entregas do gateway, de entrada e de status.

    Args:
        delivery (ZapWebhookDelivery): A entrega verificada e decodificada.

    Returns:
        dict[str, str]: Sempre 200, para o gateway parar de reentregar.
    """
    if delivery.event is ZapWebhookEvent.MESSAGE_RECEIVED and delivery.inbound:
        return {"status": await handle_inbound(delivery.inbound)}
    if delivery.status is not None:
        return {"status": handle_status(delivery.status)}
    return {"status": "ignored", "event": delivery.event_name}
```

Os `set` e `dict` em memória fazem o papel do seu banco, para o exemplo
caber numa página. Vamos por partes.

### A assinatura

`make_zap_webhook_dependency(secret=...)` monta um
`WebhookSignatureVerifier` com o que o gateway usa: header
`x-zap-signature`, HMAC-SHA256 em hex, prefixo `sha256=`. Ele lê o corpo
**cru** antes de qualquer parse e compara com `hmac.compare_digest`.

Os dois erros clássicos ficam impossíveis: verificar sobre o JSON
re-serializado (que muda espaço e ordem de chave) e comparar com `==`.
Header sem o prefixo `sha256=` também é `401`, mesmo com o hex certo.

### O despacho por `event`

O mesmo URL recebe `message.received` e `message.{sent,delivered,read,failed}`,
com formatos diferentes. A dependência já devolve o corpo validado no campo
certo:

| `delivery.event` | Corpo em |
| --- | --- |
| `ZapWebhookEvent.MESSAGE_RECEIVED` | `delivery.inbound` (`ZapInboundMessage`) |
| `MESSAGE_SENT`, `MESSAGE_DELIVERED`, `MESSAGE_READ`, `MESSAGE_FAILED` | `delivery.status` (`ZapStatusCallback`) |
| `None` | nenhum — veja abaixo |

`event` só vem preenchido quando o corpo correspondente validou. `None`
cobre dois casos: um evento que esta versão do SDK não conhece, e um evento
conhecido cujo corpo não bate com o modelo. Nos dois, `event_name` guarda a
string como chegou e `payload` guarda o dict — logue e responda `200`.

!!! tip "Responda 2xx para o que você ignora"
    O gateway trata qualquer resposta fora de 2xx como falha e reentrega com
    backoff, até `CALLBACK_MAX_ATTEMPTS` tentativas (3 por default). Um
    `422` para evento novo não protege nada: só gera tentativas, e o
    gateway desiste da entrega no fim.

!!! note "`delivery.event` é enum; o campo do schema é valor"
    `ZapWebhookDelivery` é um dataclass, então `delivery.event is
    ZapWebhookEvent.MESSAGE_RECEIVED` funciona. Dentro de `ZapInboundMessage`
    e `ZapStatusCallback`, como em todo `BaseSchema`, os campos de enum
    guardam o **valor** — compare com `==`.

### Idempotência por `messageId`

As entregas passam pela fila `callback_deliveries` do gateway, com retry.
A mesma mensagem chega mais de uma vez — quando a sua resposta se perde,
ou quando o gateway reinicia no meio de um `POST` e devolve a linha para a
fila. Chaveie a mensagem recebida por `message_id`, e o callback de status
por `id` (a linha do outbox que o `202` do envio devolveu).

Em produção, o `set` vira uma constraint `UNIQUE` em `message_id` e o
insert é o claim: quem perder a corrida recebe `IntegrityError` e responde
`200` sem fazer nada. Veja [Idempotência](idempotency.md).

### `chatKey`, não `from`

`from_` é o endereço cru: `<dígitos>@s.whatsapp.net`, um `@lid` ou um
grupo `@g.us`. A mesma pessoa pode chegar por um número e depois por um
LID — agrupar por `from_` parte uma conversa em duas.

`chat_key` são os dígitos do número, já resolvidos pelo gateway a partir do
LID. Vem `None` para grupo e para LID que o gateway não conseguiu mapear —
aí não há número para vincular, e o exemplo apenas registra o caso.

### Mídia só pelo gateway

O gateway baixa a mídia quando a mensagem chega, e o WhatsApp não a serve
de novo depois. `media_url` aponta para `GET /message/{messageId}/media`
no gateway, e `ZapClient.get_message_media(message_id)` é a chamada que
lê esses bytes com a sua `x-api-key`.

Quando `media_type` vem preenchido e `media_url` vem `None`, o download
falhou ou passou de `MEDIA_MAX_BYTES`. A mídia está perdida — não adianta
tentar de novo, e o exemplo devolve `media-lost`.

### Status fora de ordem

Cada entrega tem retry e backoff próprios, então um `sent` cujo primeiro
`POST` falhou pode chegar depois do `delivered`. E o gateway protege a
**própria** linha contra regressão, mas manda o callback mesmo assim: um
`delivered` atrasado depois de um `read` chega até você.

`is_forward_transition(current, new)` responde se o status novo avança o
que você guardou:

- a escada é `queued < sending < sent < delivered < read`;
- repetir o status atual, ou descer a escada, não avança;
- `failed` é terminal — nada o sobrescreve;
- `failed` só sobrescreve `queued`, `sending` e `sent`, porque `delivered`
  e `read` já provam que a mensagem chegou.

`current` aceita `str` porque é o que um campo de `BaseSchema` (ou uma
coluna) guarda.

## Medido

O receiver acima rodou sob `uvicorn`, com um gateway falso servindo a mídia
em `127.0.0.1:3000`. Cada entrega foi montada e assinada pelo
`signPayload` do **próprio** gateway (`src/utils/signature.ts`), com o
corpo serializado por `JSON.stringify`, como o worker de callbacks faz.
Em ordem:

| Entrega | Resposta |
| --- | --- |
| Sem o header de assinatura | `401 {"detail":"Invalid zap-api webhook signature"}` |
| Assinada com outro secret | `401` |
| Exemplo de entrada do README do gateway | `200 {"status":"stored"}` |
| A mesma entrega de novo | `200 {"status":"duplicate"}` |
| Áudio com `mediaUrl` | `200 {"status":"stored-with-media"}`, bytes gravados em disco |
| Áudio com `mediaUrl: null` | `200 {"status":"media-lost"}` |
| Grupo (`chatKey: null`) | `200 {"status":"no-chat-key"}` |
| `message.delivered` | `200 {"status":"applied"}` |
| `message.sent` depois do `delivered` | `200 {"status":"stale"}` |
| `message.read` | `200 {"status":"applied"}` |
| `message.delivered` depois do `read` | `200 {"status":"stale"}` |
| `message.edited` (evento que o SDK não conhece) | `200 {"status":"ignored","event":"message.edited"}` |

## Testando sem o gateway

Assine o corpo como o gateway assina e mande pela `ASGITransport`. O
`hmac` da biblioteca padrão basta:

```python
import hashlib
import hmac

import httpx
from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.integrations.messaging.zap import (
    ZAP_WEBHOOK_SIGNATURE_HEADER,
    ZapWebhookDelivery,
    make_zap_webhook_dependency,
)

SECRET: str = "meu-segredo"
app: FastAPI = FastAPI()
verify_zap = make_zap_webhook_dependency(secret=SECRET)


@app.post("/webhooks/zap")
async def receive_zap(
    delivery: ZapWebhookDelivery = Depends(verify_zap),
) -> dict[str, str]:
    """Ecoa o evento recebido.

    Args:
        delivery (ZapWebhookDelivery): A entrega verificada.

    Returns:
        dict[str, str]: O nome do evento.
    """
    return {"event": delivery.event_name}


async def test_signed_delivery_is_accepted() -> None:
    """Uma entrega assinada como o gateway assina passa."""
    body: bytes = b'{"event":"message.read","id":"x"}'
    digest: str = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.post(
            "/webhooks/zap",
            content=body,
            headers={ZAP_WEBHOOK_SIGNATURE_HEADER: f"sha256={digest}"},
        )
    assert response.status_code == 200
    assert response.json() == {"event": "message.read"}
```

O corpo deste teste não tem os campos de um status, então a entrega chega
com `event=None` — e mesmo assim responde `200`, que é o comportamento
descrito acima.

## Recapitulando

- Registre o webhook **com** `--secret`; sem ele o gateway não assina.
- `make_zap_webhook_dependency(secret=...)` verifica o HMAC sobre o corpo
  cru e devolve um `ZapWebhookDelivery`.
- Despache por `delivery.event`: `inbound` para mensagem recebida,
  `status` para callback, `None` para o que o SDK não modela — e responda
  `200` para todos.
- Idempotência por `message_id` (entrada) e por `id` (status).
- Agrupe por `chat_key`; `None` é grupo ou LID sem número.
- Mídia só por `get_message_media`, e `media_url: None` com `media_type`
  significa perdida.
- `is_forward_transition` antes de gravar qualquer status.
