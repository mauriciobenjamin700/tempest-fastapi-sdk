# WhatsApp pela zap-api

O `zap-api` é o gateway de WhatsApp da casa. O SDK ships o cliente inteiro
dele — 28 schemas e 27 operações — gerado da especificação OpenAPI e
commitado, então você importa e usa, sem rodar codegen no seu serviço.

```python
import asyncio

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.messaging.zap import (
    SendTextRequest,
    ZapClient,
)


async def main() -> None:
    """Enviar uma mensagem de texto."""
    http: HTTPClient = HTTPClient(
        base_url="http://127.0.0.1:3000",
        default_headers={"x-api-key": "<sua chave>"},
    )
    async with http:
        client: ZapClient = ZapClient(http)
        accepted = await client.send_text(
            body=SendTextRequest(
                to="5511999999999", text="seu pedido saiu para entrega"
            ),
            idempotency_key="4f1c9a2e-...",
        )
        print(accepted.id, accepted.status, accepted.deduped)


asyncio.run(main())
```

A autenticação é o header `x-api-key`, e só ele. Coloque-o uma vez como
default do `HTTPClient` — não há fallback por query string, e o
`idempotency_key` **não** autentica nada.

## Enviar não é entregar

Esta é a parte que muda como você escreve o serviço. Um envio responde
`202` com a **linha que foi enfileirada**, não com a confirmação de que o
WhatsApp recebeu:

```python
import asyncio

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.messaging.zap import (
    AcceptedResponseStatus,
    SendTextRequest,
    ZapClient,
)


async def main() -> None:
    """Ler o status que o 202 devolve."""
    http: HTTPClient = HTTPClient(
        base_url="http://127.0.0.1:3000",
        default_headers={"x-api-key": "<sua chave>"},
    )
    async with http:
        client: ZapClient = ZapClient(http)
        accepted = await client.send_text(
            body=SendTextRequest(to="5511999999999", text="oi"),
        )
        assert accepted.status == AcceptedResponseStatus.QUEUED


asyncio.run(main())
```

O `status` caminha `queued → sending → sent → delivered → read`, ou
`failed`. Essas transições chegam no **webhook de status** do gateway.

!!! info "`id` e `status` são nuláveis, e só num caso"
    Um gateway rodando **sem persistência** (`DATABASE_URL` não setada)
    envia inline em vez de enfileirar, e responde o mesmo `202` com `id` e
    `status` nulos — não existe linha de outbox para nomear nem para
    reportar estado. É a única situação em que eles vêm nulos.

!!! warning "O webhook ainda não tem contrato"
    A especificação descreve o webhook em prosa e **não** declara um bloco
    `webhooks` nem `callbacks`, então não há o que gerar e este pacote não
    o modela. Um serviço que precisa de estado de entrega lê o webhook por
    conta própria, por enquanto.

    Medido em 2026-09-06 contra o documento que este pacote gerou: 27
    operações, zero `callbacks`, `webhooks` ausente.

## Retry precisa da chave de idempotência

Como o envio é assíncrono, um `202` perdido deixa você sem saber se a
mensagem foi enfileirada. Reenviar às cegas manda duas.

Passe um `idempotency_key` novo por mensagem, e **reutilize o mesmo** ao
repetir aquela mensagem:

```python
import asyncio
from uuid import uuid4

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.messaging.zap import (
    SendTextRequest,
    ZapClient,
)


async def main() -> None:
    """Repetir um envio sem mandar a mensagem duas vezes."""
    http: HTTPClient = HTTPClient(
        base_url="http://127.0.0.1:3000",
        default_headers={"x-api-key": "<sua chave>"},
    )
    async with http:
        client: ZapClient = ZapClient(http)
        key: str = str(uuid4())

        accepted = await client.send_text(
            body=SendTextRequest(to="5511999999999", text="oi"),
            idempotency_key=key,
        )

        again = await client.send_text(
            body=SendTextRequest(to="5511999999999", text="oi"),
            idempotency_key=key,
        )
        assert again.deduped is True
        assert again.id == accepted.id


asyncio.run(main())
```

A segunda chamada é o retry: a rede caiu e você não viu a resposta.
Repetindo com a **mesma** chave, o gateway devolve a linha original
(`deduped: true`) e não envia nada de novo.

!!! danger "A chave fica presa à mensagem, não à tentativa"
    Ela é escopada ao seu consumidor e **continua reivindicada enquanto a
    linha existir**. Reutilizar uma chave antiga para uma mensagem
    **nova** responde `deduped: true` e **não envia nada** — o silêncio
    parece sucesso. Gere uma chave por mensagem.

## O que mais dá para fazer

| Método | O que faz |
| --- | --- |
| `send_text`, `send_image`, `send_video`, `send_audio`, `send_document` | Enfileiram uma mensagem. Todos aceitam `reply_to` |
| `upload_image`, `upload_video`, `upload_audio`, `upload_document` | Enviam o arquivo no corpo, em `multipart/form-data` |
| `send_image_base64`, `send_video_base64`, `send_audio_base64`, `send_document_base64` | Enviam o arquivo embutido em base64, em JSON |
| `react` | Enfileira uma reação de emoji a uma mensagem |
| `set_typing`, `mark_read` | Sinais de presença. Respondem `204` |
| `check_number` | Se o número existe no WhatsApp, e o JID dele |
| `get_history` | Últimas mensagens de um chat (`limit` de 1 a 200) |
| `get_message_media` | Os bytes da mídia de uma mensagem armazenada |
| `start_session`, `get_session_status`, `get_session_qr`, `get_session_qr_image`, `disconnect_session` | Ciclo de vida da conexão |
| `health`, `ready`, `metrics` | Sondas e exposição Prometheus |

O `to` aceita duas grafias, e a diferença é real: os `send_*` exigem
dígitos (`^\d{10,15}$`), enquanto `react`, `mark_read` e `set_typing`
aceitam também o JID completo que o webhook de entrada entrega
(`^\d{10,15}(@s\.whatsapp\.net)?$`).

## Três formas de mandar mídia

A mesma imagem chega ao WhatsApp por três caminhos, e a escolha é sobre
onde o arquivo está:

| Rota | Quando usar |
| --- | --- |
| `send_image(body=...)` | O arquivo já tem URL pública; o gateway baixa |
| `upload_image(file=...)` | O arquivo só existe no disco de quem chama |
| `send_image_base64(body=...)` | Você já tem os bytes e quer um corpo JSON só |

As de `upload_*` são `multipart/form-data`, então o cliente as expõe com
os campos do formulário achatados — o arquivo em `files`, os escalares em
`data`:

```python
import asyncio
from pathlib import Path

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.messaging.zap import ZapClient


async def main() -> None:
    """Subir uma imagem do disco."""
    http: HTTPClient = HTTPClient(
        base_url="http://127.0.0.1:3000",
        default_headers={"x-api-key": "<sua chave>"},
    )
    async with http:
        client: ZapClient = ZapClient(http)
        accepted = await client.upload_image(
            file=Path("nota-fiscal.png").read_bytes(),
            to="5511999999999",
            caption="sua nota fiscal",
        )
        print(accepted.id, accepted.status)


asyncio.run(main())
```

!!! warning "O arquivo é `bytes`, e isso é por causa do retry"
    O `HTTPClient` repete a requisição em erro de rede e em `5xx`, e um
    retry reenvia os mesmos argumentos. Um objeto de arquivo já foi
    consumido na primeira tentativa, então a segunda subiria um corpo
    truncado — que o servidor não tem como distinguir de um arquivo curto.
    Por isso o part é `bytes`.

    Passando um stream direto ao `HTTPClient`, ele **não repete**: uma
    tentativa só, e a resposta dela é o que você recebe. É a troca certa
    entre não repetir e repetir errado.

!!! note "O limite é `MEDIA_MAX_BYTES`"
    16MB por default, aplicado enquanto o corpo trafega — um upload grande
    demais é cortado no meio do voo, não bufferizado inteiro para ser
    recusado depois. O cliente vê `413`.

## Corpo que não é JSON chega como `bytes`

Três operações respondem um corpo de sucesso que não é JSON:
`get_message_media` (`application/octet-stream`), `get_session_qr_image`
(`image/png`) e `metrics` (`text/plain`). O cliente as tipa `-> bytes` e
entrega o corpo **sem decodificar** — a especificação não carrega charset,
e adivinhar um corrompe em silêncio.

```python
import asyncio
from pathlib import Path

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.messaging.zap import ZapClient


async def main() -> None:
    """Salvar a mídia de uma mensagem recebida."""
    http: HTTPClient = HTTPClient(
        base_url="http://127.0.0.1:3000",
        default_headers={"x-api-key": "<sua chave>"},
    )
    async with http:
        client: ZapClient = ZapClient(http)

        media: bytes = await client.get_message_media("3EB0C767D26B8E3C1A2B")
        Path("recebido.bin").write_bytes(media)

        scrape: bytes = await client.metrics()
        print(scrape.decode("utf-8"))


asyncio.run(main())
```

!!! danger "A mídia não é rebaixável"
    O gateway baixa a mídia enquanto a mensagem ainda está em memória, e o
    WhatsApp **não a serve de novo**. Uma mensagem cujo download falhou ou
    estourou `MEDIA_MAX_BYTES` fica armazenada com tipo e sem arquivo, e
    responde `404` aqui. Este endpoint é o único caminho para esses bytes —
    busque-os quando o webhook de entrada avisar, não depois.

!!! note "`health` e `ready` continuam `-> None`"
    Estas duas não são o caso acima: a especificação as declara **sem
    content**, então não há corpo declarado para tipar. Só que elas
    respondem JSON de verdade — medido em 2026-09-06 contra um gateway
    rodando:

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
- Corpo não-JSON chega como `bytes`; a mídia só existe uma vez.
- O webhook de status ainda não tem schema; o pacote não o modela.
- `make zap-regen` é quem edita o código gerado, nunca você.
