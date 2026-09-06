# WhatsApp pela zap-api

O `zap-api` é o gateway de WhatsApp da casa. O SDK ships o cliente inteiro
dele — 20 schemas e 18 operações — gerado da especificação OpenAPI e
commitado, então você importa e usa, sem rodar codegen no seu serviço.

```python
from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.messaging.zap import (
    SendTextRequest,
    ZapClient,
)

http: HTTPClient = HTTPClient(
    base_url="http://127.0.0.1:3000",
    default_headers={"x-api-key": "<sua chave>"},
)
client: ZapClient = ZapClient(http)

accepted = await client.send_text(
    body=SendTextRequest(to="5511999999999", text="seu pedido saiu para entrega"),
    idempotency_key="4f1c9a2e-...",
)
print(accepted.id, accepted.status, accepted.deduped)
```

A autenticação é o header `x-api-key`, e só ele. Coloque-o uma vez como
default do `HTTPClient` — não há fallback por query string, e o
`idempotency_key` **não** autentica nada.

## Enviar não é entregar

Esta é a parte que muda como você escreve o serviço. Um envio responde
`202` com a **linha que foi enfileirada**, não com a confirmação de que o
WhatsApp recebeu:

```python
accepted = await client.send_text(body=SendTextRequest(to=..., text=...))
accepted.status  # AcceptedResponseStatus.QUEUED
```

O `status` caminha `queued → sending → sent → delivered → read`, ou
`failed`. Essas transições chegam no **webhook de status** do gateway.

!!! warning "O webhook ainda não tem contrato"
    A especificação descreve o webhook em prosa e **não** declara um bloco
    `webhooks` nem `callbacks`, então não há o que gerar e este pacote não
    o modela. Um serviço que precisa de estado de entrega lê o webhook por
    conta própria, por enquanto.

    Medido em 2026-09-06 contra o documento que este pacote gerou: 18
    operações, zero `callbacks`, `webhooks` ausente.

## Retry precisa da chave de idempotência

Como o envio é assíncrono, um `202` perdido deixa você sem saber se a
mensagem foi enfileirada. Reenviar às cegas manda duas.

Passe um `idempotency_key` novo por mensagem, e **reutilize o mesmo** ao
repetir aquela mensagem:

```python
from uuid import uuid4

key = str(uuid4())

accepted = await client.send_text(
    body=SendTextRequest(to="5511999999999", text="oi"),
    idempotency_key=key,
)

# A rede caiu e você não viu a resposta. Repetir com a MESMA chave:
again = await client.send_text(
    body=SendTextRequest(to="5511999999999", text="oi"),
    idempotency_key=key,
)
assert again.deduped is True      # devolveu a linha original
assert again.id == accepted.id    # e nada foi enviado de novo
```

!!! danger "A chave fica presa à mensagem, não à tentativa"
    Ela é escopada ao seu consumidor e **continua reivindicada enquanto a
    linha existir**. Reutilizar uma chave antiga para uma mensagem
    **nova** responde `deduped: true` e **não envia nada** — o silêncio
    parece sucesso. Gere uma chave por mensagem.

## O que mais dá para fazer

| Método | O que faz |
| --- | --- |
| `send_text`, `send_image`, `send_video`, `send_audio`, `send_document` | Enfileiram uma mensagem. Todos aceitam `reply_to` |
| `react` | Enfileira uma reação de emoji a uma mensagem |
| `set_typing`, `mark_read` | Sinais de presença. Respondem `204` |
| `check_number` | Se o número existe no WhatsApp, e o JID dele |
| `get_history` | Últimas mensagens de um chat (`limit` de 1 a 200) |
| `start_session`, `get_session_status`, `get_session_qr`, `disconnect_session` | Ciclo de vida da conexão |
| `health` | Liveness, sem autenticação |

O `to` aceita duas grafias, e a diferença é real: os `send_*` exigem
dígitos (`^\d{10,15}$`), enquanto `react`, `mark_read` e `set_typing`
aceitam também o JID completo que o webhook de entrada entrega
(`^\d{10,15}(@s\.whatsapp\.net)?$`).

## Duas rotas que o cliente tipa como `None`

`metrics` responde `text/plain` (exposição Prometheus) e
`get_session_qr_image` responde `image/png`. O gerador modela apenas
`application/json`, reporta as duas, e as tipa `-> None`.

Para o QR, use a versão JSON, que carrega o mesmo valor:

```python
qr = await client.get_session_qr()
print(qr.qr)   # o código de pareamento como string
```

!!! note "`health` e `ready` também devolvem `None`"
    A especificação declara as duas **sem content**, então o corpo não
    chega ao cliente tipado. Só que elas respondem JSON de verdade —
    medido em 2026-09-06 contra um gateway rodando:

    ```console
    $ curl -s http://127.0.0.1:3000/health
    {"status":"ok","session":"disconnected","ready":false,
     "reconnectAttempts":0,"queue":{"queued":0,"oldestQueuedSeconds":null}}
    $ curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/ready
    503
    ```

    O `/ready` devolve o mesmo objeto sem o `status`. **Nada no repositório
    reproduz isto**: o gateway é externo, e não há fixture nem cassette
    aqui — o comando acima é a evidência, e ele precisa de um gateway no
    ar. Declarar o schema no gateway faz `make zap-regen` passar a expô-lo,
    e aí passa a ser verificável offline como o resto.

## Regenerar

O cliente é gerado de `vendor/zap-openapi.yaml` e commitado. Editar
`schemas.py` ou `client.py` à mão é revertido na próxima regeneração — e
`tests/integrations/messaging/zap/test_generated_drift.py` falha antes
disso.

```bash
make zap-fetch    # relê o documento do gateway (precisa dele no ar)
make zap-regen    # regenera a partir do vendorizado, offline
```

O `zap-fetch` lê de `ZAP_OPENAPI_URL`, ou de `http://127.0.0.1:3000` por
default. Diferente do OpenPix e do Mercado Pago, **não existe URL pública
canônica** para esta especificação — ela é nossa, e é servida de onde o
gateway estiver. Por isso o arquivo vendorizado é a autoridade, e o
`SPEC_SHA256` registra de quais bytes este checkout gerou.

## Recapitulando

- `ZapClient` sobre `HTTPClient`, com `x-api-key` como header default.
- Envio responde `202` e uma linha enfileirada — **não** uma entrega.
- Uma `idempotency_key` por mensagem, reutilizada só em retry da mesma.
- O webhook de status ainda não tem schema; o pacote não o modela.
- `make zap-regen` é quem edita o código gerado, nunca você.
