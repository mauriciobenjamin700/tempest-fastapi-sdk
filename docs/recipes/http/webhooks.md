# Webhooks: verificar e enviar

Use para validar webhooks assinados (HMAC ou RSA) que chegam e entregar webhooks de saída com `WebhookSender`.

Parte da receita [Camada HTTP](../http.md).

`WebhookSignatureVerifier` valida webhooks de entrada assinados com HMAC (estilo GitHub) e expõe uma dependência FastAPI que lê o corpo cru, checa a assinatura com `hmac.compare_digest` e entrega os bytes do corpo para que o handler da rota possa reparsear sem reler o stream.

```python
# src/api/dependencies/webhooks.py
from tempest_fastapi_sdk import WebhookSignatureVerifier

from src.core.settings import settings


github = WebhookSignatureVerifier(
    secret=settings.GITHUB_WEBHOOK_SECRET,
    algorithm="sha256",
    header_name="X-Hub-Signature-256",
    prefix="sha256=",
)
```

!!! warning "Stripe não cabe neste verificador"
    O `Stripe-Signature` não é um hex do HMAC do corpo: é
    `t=<timestamp>,v1=<hex>`, e o que a Stripe assina é `"<t>." + corpo`.
    Um `WebhookSignatureVerifier(header_name="Stripe-Signature",
    encoding="hex")` recusa toda entrega legítima — medido com um header
    gerado por `sign_payload`, `verify` devolve `False`. Para a Stripe use
    `make_stripe_webhook_dependency` da [integração Stripe](../stripe.md), que
    parseia o header e checa a janela de tempo.

```python
# src/api/routers/webhooks.py

import json

from fastapi import APIRouter, Depends

from src.api.dependencies.webhooks import github


router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/github")
async def github_event(body: bytes = Depends(github.dependency())) -> None:
    payload = json.loads(body)
    ...
```

Suporta encodings `hex` (default) e `base64`, qualquer algoritmo hashlib garantido entre plataformas, e um `prefix` opcional (ex.: `"sha256="`) removido antes da comparação. Segredo vazio ou só com espaços levanta `ValueError` na construção: o HMAC com chave vazia é calculável por qualquer um, então um setting esquecido derruba o boot em vez de aceitar toda entrega. Use o imperativo `verifier.verify(body, signature)` de handlers de fila quando a validação acontece fora do pipeline FastAPI.

A assinatura cobre **só o corpo**, então uma entrega capturada continua válida pra sempre — quem observar uma pode reenviar indefinidamente. Passe `timestamp_header=` pra também exigir um timestamp unix recente e recusar o que estiver fora da janela:

```python
from tempest_fastapi_sdk import WebhookSignatureVerifier

from src.core.settings import settings


github = WebhookSignatureVerifier(
    settings.GITHUB_WEBHOOK_SECRET,
    header_name="X-Hub-Signature-256",
    prefix="sha256=",
)

check_github = github.dependency(
    timestamp_header="X-Webhook-Timestamp",
    max_age_seconds=300,
)
```

É opt-in porque provedor que não manda esse header teria o tráfego legítimo recusado. O `WebhookSender` do próprio SDK envia `X-Webhook-Timestamp`.

!!! warning "O timestamp não entra na assinatura"
    Como só o corpo é assinado, quem replica uma captura também pode reescrever o timestamp pra um atual. A checagem limita replay **acidental e oportunista**, não um atacante ativo que lê e reescreve a request. Fechar isso depende do provedor assinar `timestamp.body` — o formato que a Stripe usa (e que a [integração Stripe](../stripe.md) já verifica); quando o seu provedor fizer isso, valide com `verifier.verify(f"{ts}.".encode() + body, signature)`.

Para provedores que assinam com uma chave privada RSA (Apple App Store, Google Play, serviços enterprise custom), troque `WebhookSignatureVerifier` por `RSAWebhookSignatureVerifier` — mesma superfície `verify(body, signature)`, mas valida a assinatura contra uma chave pública codificada em PEM. Usa `RSASSA-PKCS1-v1_5` sobre SHA-256/384/512 (configurável via `algorithm=`). Requer o pacote `cryptography` (instalado com o extra `[webpush]`).

```python
from tempest_fastapi_sdk import RSAWebhookSignatureVerifier

from src.core.settings import settings

base64_signature_header_value = "c2lnbmF0dXJl"
raw_body_bytes = b'{"event": "order.paid"}'


apple = RSAWebhookSignatureVerifier(
    public_key_pem=settings.APPLE_PUBLIC_KEY_PEM,
    header_name="X-Apple-Signature",
    algorithm="sha256",
)

# Em handlers de fila / fora do FastAPI:
ok: bool = apple.verify(raw_body_bytes, base64_signature_header_value)
```

## Entrega de webhooks de saída — `WebhookSender`

A contraparte: **enviar** eventos assinados pros seus assinantes.
`WebhookSender` faz POST do evento em JSON, assina o corpo com o
**mesmo** `WebhookSignatureVerifier` (então o receptor valida com aquele
verifier) e re-tenta falhas transitórias (erro de conexão, 5xx, 429) com
backoff exponencial. Outros 4xx **não** são re-tentados. O cliente httpx
é injetado (você é dono do ciclo de vida).

!!! info "Instalação"
    O `WebhookSender` usa `httpx`, que já vem no pacote base
    `tempest-fastapi-sdk` — nenhum extra é necessário. O extra `[http]`
    continua existindo e é opcional: declara o `httpx` de forma explícita.

```python
import asyncio

import httpx

from tempest_fastapi_sdk import WebhookSender, WebhookSignatureVerifier

from src.core.settings import settings

order = {"id": "abc", "status": "paid"}
subscribers = ["https://partner.example.com/hooks"]


verifier = WebhookSignatureVerifier(settings.WEBHOOK_SECRET, prefix="sha256=")


async def main() -> None:
    """Run this example."""
    async with httpx.AsyncClient() as client:
        sender = WebhookSender(client, signer=verifier, max_attempts=4)
        result = await sender.send(
            "https://assinante.example.com/hooks",
            event="order.paid",
            payload={"id": order["id"], "total": 4200},
        )
        if not result.delivered:
            # result.status_code / result.attempts / result.error
            ...  # enfileira pra reprocessar, alerta, etc.

    # Mesmo evento pra vários assinantes, concorrente:
    results = await sender.send_many(
        [(url, {"id": order["id"]}) for url in subscribers],
        event="order.paid",
    )


asyncio.run(main())
```

Cada entrega envia os headers `X-Webhook-Event`, `X-Webhook-Id` (uuid
único) e `X-Webhook-Timestamp`, mais a assinatura HMAC no header do
`signer`. Devolve um `WebhookDelivery` (`delivered`, `status_code`,
`attempts`, `error`, `delivery_id`).

!!! tip "Casa com o outbox"
    Pareie com `BaseOutboxModel` + `OutboxRelay`: grave o evento na mesma
    transação do negócio e deixe o relay chamar o `WebhookSender` —
    entrega ao menos uma vez, com a assinatura que o assinante verifica.
