# OpenPix (Pix via Woovi)

OpenPix publishes a complete OpenAPI specification, and the SDK already
knows how to turn that into code:

```bash
tempest openapi-client \
    https://developers.openpix.com.br/en/redocusaurus/plugin-redoc-1.yaml \
    --name openpix
```

```text
  + src/integrations/openpix/__init__.py
  + src/integrations/openpix/client.py
  + src/integrations/openpix/schemas.py
358 schema(s), 105 operation(s).
```

That settles the schemas and the calls. **Four things the specification does
not say are left over**, and every OpenPix integration rediscovers them by
hand. That is what `tempest_fastapi_sdk.openpix` supplies. 🚀

!!! info "The module does not duplicate the generated package"
    The 358 schemas live in your service, generated. The SDK does not embed
    them — that would double the package size to freeze a third party's spec
    into a release. Only the thin layer lives here.

## What the spec does not say

### 1. The two environments are different domains

Production is `api.openpix.com.br`. Testing is `api.woovi-sandbox.com` — a
different domain, not a subdomain. Neither one spells the other.

```python
from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.openpix import OpenPixEnvironment

from src.core.settings import settings
from src.integrations.openpix import OpenpixClient

environment: OpenPixEnvironment = (
    OpenPixEnvironment.PRODUCTION
    if settings.ENVIRONMENT == "production"
    else OpenPixEnvironment.SANDBOX
)

http: HTTPClient = HTTPClient(
    base_url=environment.base_url,
    default_headers={"Authorization": settings.OPENPIX_APP_ID},
)
openpix: OpenpixClient = OpenpixClient(http)
```

### 2. `value` is cents, but arrives as a float

The specification says, in so many words, *"Value in cents of this charge"* —
and then types the field `number`. The generated model therefore validates
`1990` into the float `1990.0`.

Money that has been through a float is money that can be wrong: add a few of
them and you get `0.30000000000000004`. Cents exist to avoid exactly that,
and the JSON layer undoes it.

```python
from tempest_fastapi_sdk.openpix import cents_to_reais, reais_to_cents, to_cents

to_cents(1990.0)          # 1990  (int, exact)
reais_to_cents("19.90")   # 1990
cents_to_reais(1990)      # Decimal("19.90")
```

!!! warning "`to_cents` refuses a fraction on purpose"
    `to_cents(19.9)` raises `ValueError`. The field **already is** cents, so a
    fraction means the caller is treating a reais amount as one. Rounding
    silently would hide that mistake behind a plausible number.

!!! tip "`reais_to_cents` rounds half-up"
    That is what a person expects from money (`0.005` → `1` cent) and it is
    **not** what the built-in `round` does: it rounds half to even, so
    `round(0.005 * 100)` gives `0`.

### 3. The 28 webhook events

Ported verbatim from the specification's `WebhookEventEnum`:

```python
from tempest_fastapi_sdk.openpix import OpenPixEvent

OpenPixEvent.CHARGE_COMPLETED.value        # "OPENPIX:CHARGE_COMPLETED"
OpenPixEvent.PIX_AUTOMATIC_APPROVED.value  # "PIX_AUTOMATIC_APPROVED"
```

!!! note "The prefix is not uniform, and that is OpenPix's doing"
    Charge, transaction, movement and dispute events carry the `OPENPIX:`
    namespace. The Pix-automatic and account-register families do **not**. It
    reads like a transcription slip; it is not — which is why a test pins both
    cases.

### 4. How to validate the webhook

OpenPix signs every delivery with its private key and publishes the public
one. The SDK already had `RSAWebhookSignatureVerifier`; what was missing was
tying the three facts together — which header, which key, and what the
`event` string means.

!!! warning "Verifying a signature needs `cryptography`"
    The module **imports** on a minimal install, but `verify()` raises
    `ImportError` on the first real delivery — in production, not at boot.
    Install it up front:

    ```bash
    uv add cryptography
    # or via the SDK extra that already ships it:
    uv add "tempest-fastapi-sdk[webpush]"
    ```

    The `[webpush]` name has nothing to do with payments; it is simply the
    extra that packages `cryptography` today. If all you want is to validate
    OpenPix webhooks, installing `cryptography` directly is more honest.

```python
from fastapi import APIRouter, Depends

from tempest_fastapi_sdk.openpix import (
    OpenPixEvent,
    OpenPixWebhookEvent,
    make_openpix_webhook_dependency,
    to_cents,
)

from src.integrations.openpix.schemas import Charge

router: APIRouter = APIRouter(prefix="/webhooks", tags=["webhooks"])
verify = make_openpix_webhook_dependency()


@router.post("/openpix")
async def receive_openpix(
    event: OpenPixWebhookEvent = Depends(verify),
) -> dict[str, str]:
    """Receive an already-verified OpenPix delivery.

    Args:
        event (OpenPixWebhookEvent): The verified, decoded delivery.

    Returns:
        An acknowledgement so OpenPix stops redelivering.
    """
    if event.event is OpenPixEvent.CHARGE_COMPLETED:
        charge: Charge = Charge.model_validate(event.payload["charge"])
        cents: int = to_cents(charge.value)
        print(charge.correlation_id, cents)
    return {"status": "ok"}
```

The dependency verifies the signature, decodes the body and hands you the
resolved event. `event.payload` stays the raw dict — the generated schemas
live in your service, so you validate only the branch you care about.

## The security part, unsoftened

!!! danger "OpenPix's key is RSA-1024"
    Checked by loading it into `cryptography`: 1024 bits, exponent 65537.
    That is **below the 2048-bit floor** NIST has recommended since 2013, and
    it caps what the signature can prove.

    **Treat a valid signature as evidence the delivery came from OpenPix, not
    as authorization to move money.** Before acting on a `CHARGE_COMPLETED`,
    re-read the charge from the API:

    ```python
    if event.event is OpenPixEvent.CHARGE_COMPLETED:
        confirmed = await openpix.get_api_v1_charge_by_id(
            id=event.payload["charge"]["correlationID"]
        )
        if confirmed.charge.status == "COMPLETED":
            await release_order(...)
    ```

    The signature is a filter against inbound noise. The API read is the
    fact. Nothing here raises the key's strength — the mitigation is not
    trusting it beyond what it is.

!!! warning "Replay"
    The signature covers the body and nothing else, so a captured delivery
    stays valid forever. Treat your handler as **idempotent** — key off the
    charge's `correlationID` and ignore what you already processed. See
    [Idempotency](idempotency.md).

### If OpenPix rotates the key

The key ships embedded, but is overridable — a hard constant would strand
every consumer waiting on an SDK release:

```python
from tempest_fastapi_sdk.openpix import (
    decode_public_key,
    make_openpix_webhook_dependency,
    webhook_verifier,
)

rotated = decode_public_key("LS0tLS1CRUdJTiBQVUJMSUMgS0VZ...")
verify = make_openpix_webhook_dependency(
    verifier=webhook_verifier(public_key_pem=rotated)
)
```

OpenPix publishes the key base64-encoded, not as PEM. `decode_public_key`
decodes it and **checks the result really is a PEM** — without that, a
truncated paste would fail much later, as an invalid signature on a real
delivery.

## Two decisions that keep your service up

!!! check "An unknown event does not fail the request"
    OpenPix adds events. A service that 500s on one it has never seen turns
    the provider's release into its own outage. The name stays in
    `event.event_name` and `event.event` is left `None`.

!!! check "A non-JSON body that verified is still delivered"
    If it verified, it came from OpenPix. Rejecting it would discard a
    delivery the provider considers sent. `payload` stays empty and `body`
    carries the bytes.

## Recap

1. **`tempest openapi-client <the OpenPix spec>`** generates the 358 schemas
   and 105 operations into your service.
2. **`OpenPixEnvironment`** resolves production vs sandbox — different
   domains.
3. **`to_cents` / `reais_to_cents` / `cents_to_reais`** undo the float the
   spec forces, and refuse a fraction rather than round it away.
4. **`OpenPixEvent`** carries the 28 events verbatim, irregular prefix
   included.
5. **`make_openpix_webhook_dependency()`** verifies, decodes and hands over
   the typed event; a new event and a non-JSON body do not take the route
   down.
6. **The key is RSA-1024** — a valid signature proves origin, it does not
   authorize moving money. Re-read the charge from the API and keep the
   handler idempotent.
