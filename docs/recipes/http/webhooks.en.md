# Webhooks: verify and send

Use to validate incoming signed webhooks (HMAC or RSA) and deliver outgoing webhooks with `WebhookSender`.

Part of the [HTTP layer](../http.md) recipe.

`WebhookSignatureVerifier` validates HMAC-signed inbound webhooks (GitHub style) and exposes a FastAPI dependency that reads the raw body, checks the signature with `hmac.compare_digest`, and yields the body bytes so the route handler can re-parse it without re-reading the stream.

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

!!! warning "Stripe does not fit this verifier"
    `Stripe-Signature` is not a hex HMAC of the body: it is
    `t=<timestamp>,v1=<hex>`, and what Stripe signs is `"<t>." + body`. A
    `WebhookSignatureVerifier(header_name="Stripe-Signature",
    encoding="hex")` rejects every legitimate delivery — measured with a
    header produced by `sign_payload`, `verify` returns `False`. For Stripe
    use `make_stripe_webhook_dependency` from the
    [Stripe integration](../stripe.md), which parses the header and checks the
    time window.

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

Supports `hex` (default) and `base64` encodings, any hashlib algorithm guaranteed across platforms, and an optional `prefix` (e.g. `"sha256="`) stripped before comparison. An empty or blank secret raises `ValueError` at construction: an HMAC under an empty key is computable by anyone, so a missing setting fails the boot instead of accepting every delivery. Use the imperative `verifier.verify(body, signature)` from queue handlers when validation happens outside the FastAPI pipeline.

The signature covers **the body only**, so a captured delivery stays valid forever — anyone who observes one can resend it indefinitely. Pass `timestamp_header=` to also require a recent unix timestamp and reject anything outside the window:

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

It is opt-in because a provider that sends no such header would have its legitimate traffic rejected. The SDK's own `WebhookSender` sends `X-Webhook-Timestamp`.

!!! warning "The timestamp is not covered by the signature"
    Since only the body is signed, whoever replays a capture can also rewrite the timestamp to a current one. The check bounds **accidental and opportunistic** replay, not an active attacker who reads and rewrites the request. Closing that depends on the provider signing `timestamp.body` — the shape Stripe uses (and that the [Stripe integration](../stripe.md) already verifies); when your provider does, verify it with `verifier.verify(f"{ts}.".encode() + body, signature)`.

For providers that sign with an RSA private key (Apple App Store, Google Play, custom enterprise services), swap `WebhookSignatureVerifier` for `RSAWebhookSignatureVerifier` — same `verify(body, signature)` surface, but it validates the signature against a PEM-encoded public key. Uses `RSASSA-PKCS1-v1_5` over SHA-256/384/512 (configurable via `algorithm=`). Requires the `cryptography` package (installed by the `[webpush]` extra).

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

# From queue handlers / outside FastAPI:
ok: bool = apple.verify(raw_body_bytes, base64_signature_header_value)
```

## Outbound webhook delivery — `WebhookSender`

The counterpart: **sending** signed events to your subscribers.
`WebhookSender` POSTs the JSON event, signs the body with the **same**
`WebhookSignatureVerifier` (so the receiver validates with that
verifier) and retries transient failures (connection error, 5xx, 429)
with exponential backoff. Other 4xx are **not** retried. The httpx
client is injected (you own its lifecycle).

!!! info "Installation"
    `WebhookSender` uses `httpx`, which already ships with the base
    `tempest-fastapi-sdk` package — no extra is needed. The `[http]` extra
    still exists and is optional: it declares `httpx` explicitly.

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
            "https://subscriber.example.com/hooks",
            event="order.paid",
            payload={"id": order["id"], "total": 4200},
        )
        if not result.delivered:
            # result.status_code / result.attempts / result.error
            ...  # enqueue for retry, alert, etc.

    # Same event to many subscribers, concurrently:
    results = await sender.send_many(
        [(url, {"id": order["id"]}) for url in subscribers],
        event="order.paid",
    )


asyncio.run(main())
```

Each delivery sends `X-Webhook-Event`, `X-Webhook-Id` (a unique uuid) and
`X-Webhook-Timestamp` headers, plus the HMAC signature in the `signer`'s
header. Returns a `WebhookDelivery` (`delivered`, `status_code`,
`attempts`, `error`, `delivery_id`).

!!! tip "Pairs with the outbox"
    Combine with `BaseOutboxModel` + `OutboxRelay`: write the event in
    the same transaction as the business change and let the relay call
    `WebhookSender` — at-least-once delivery with the signature the
    subscriber verifies.
