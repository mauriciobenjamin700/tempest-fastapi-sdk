"""The thin typed layer over an OpenPix integration.

`tempest openapi-client` already turns the OpenPix specification into 358
schemas and a typed client. This module is **not** a second copy of that —
the generated package lives in the consuming service, and nothing here
duplicates it. It supplies the four things the specification does not, and
that every OpenPix integration re-derives by hand:

1. **The environments.** ``api.openpix.com.br`` and
   ``api.woovi-sandbox.com`` are different domains; neither spells the
   other.
2. **The events.** 28 webhook event names, ported verbatim from the
   specification's ``WebhookEventEnum``.
3. **The webhook.** Which header carries the signature, which public key
   verifies it, and how to get from a verified body to a parsed event.
4. **The money.** The specification says ``Value in cents`` and types the
   field ``number``, so a generated model hands you ``1990.0``.

Submodule import, like ``geo`` and ``vision`` — ``import
tempest_fastapi_sdk`` does not pull it in.

.. code-block:: python

    from fastapi import APIRouter, Depends

    from tempest_fastapi_sdk.openpix import (
        OpenPixEvent,
        OpenPixWebhookEvent,
        make_openpix_webhook_dependency,
        to_cents,
    )

    router: APIRouter = APIRouter(prefix="/webhooks")
    verify = make_openpix_webhook_dependency()


    @router.post("/openpix")
    async def receive(event: OpenPixWebhookEvent = Depends(verify)) -> dict[str, str]:
        \"\"\"Handle a verified OpenPix delivery.\"\"\"
        if event.event is OpenPixEvent.CHARGE_COMPLETED:
            charge = event.payload.get("charge", {})
            cents: int = to_cents(charge.get("value", 0))
            print(charge.get("correlationID"), cents)
        return {"status": "ok"}
"""

from tempest_fastapi_sdk.openpix.environment import (
    OpenPixEnvironment as OpenPixEnvironment,
)
from tempest_fastapi_sdk.openpix.events import OpenPixEvent as OpenPixEvent
from tempest_fastapi_sdk.openpix.money import cents_to_reais as cents_to_reais
from tempest_fastapi_sdk.openpix.money import reais_to_cents as reais_to_cents
from tempest_fastapi_sdk.openpix.money import to_cents as to_cents
from tempest_fastapi_sdk.openpix.webhooks import (
    OPENPIX_WEBHOOK_PUBLIC_KEY as OPENPIX_WEBHOOK_PUBLIC_KEY,
)
from tempest_fastapi_sdk.openpix.webhooks import (
    OPENPIX_WEBHOOK_SIGNATURE_HEADER as OPENPIX_WEBHOOK_SIGNATURE_HEADER,
)
from tempest_fastapi_sdk.openpix.webhooks import (
    OpenPixWebhookEvent as OpenPixWebhookEvent,
)
from tempest_fastapi_sdk.openpix.webhooks import (
    decode_public_key as decode_public_key,
)
from tempest_fastapi_sdk.openpix.webhooks import (
    make_openpix_webhook_dependency as make_openpix_webhook_dependency,
)
from tempest_fastapi_sdk.openpix.webhooks import (
    webhook_verifier as webhook_verifier,
)

__all__: list[str] = [
    "OPENPIX_WEBHOOK_PUBLIC_KEY",
    "OPENPIX_WEBHOOK_SIGNATURE_HEADER",
    "OpenPixEnvironment",
    "OpenPixEvent",
    "OpenPixWebhookEvent",
    "cents_to_reais",
    "decode_public_key",
    "make_openpix_webhook_dependency",
    "reais_to_cents",
    "to_cents",
    "webhook_verifier",
]
