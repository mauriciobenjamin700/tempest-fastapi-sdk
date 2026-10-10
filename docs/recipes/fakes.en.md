# Fakes: run the whole flow with no provider at all

Your service charges over Pix, emails an activation link, fires a push,
geocodes an address and calls a model. To run that on your machine you would
need five credentials — and to **test** it, five hand-written mocks.

Or none of that.

```python
from tempest_fastapi_sdk.testing.fakes import FakePixProvider
```

Each fake implements one of the SDK's seams and talks to nobody: no
credential, no sandbox account, no network.

## Why this is not a mock

A mock answers the call you set up. A fake **holds state** — and these let you
move that state:

```python
import asyncio

from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatus,
    PixChargeRequest,
    PixProvider,
)
from tempest_fastapi_sdk.testing.fakes import FakePixProvider


async def main() -> None:
    """Run a checkout from open charge to paid, with nobody scanning a QR."""
    fake = FakePixProvider()
    provider: PixProvider = fake

    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=1990, reference="order-1042"),
    )
    print(charge.status.value)

    event = fake.advance(charge.provider_charge_id, PaymentStatus.PAID)
    print(event.type.value)


if __name__ == "__main__":
    asyncio.run(main())
```

Output:

```text
pending
charge_paid
```

`advance` is the part the real provider does not give you. Reaching `PAID` in
a sandbox takes someone scanning a QR code; reaching `CHARGED_BACK` takes
someone opening a dispute. Here it is one call.

!!! tip "The branch worth testing is the failing one"
    Every fake takes `fail_next(error)`: the next call raises, and only it.

    ```python
    from tempest_fastapi_sdk.push.dispatcher import PushDeviceGoneError
    from tempest_fastapi_sdk.testing.fakes import FakePushDispatcher

    dispatcher = FakePushDispatcher()
    dispatcher.fail_next(PushDeviceGoneError("token retired"))
    ```

    Pass the exception the real client raises — that way the branch you
    exercise is the branch production takes. Queue several to fail several
    calls, in order.

## What exists

| Fake | Stands in for | Its own steering |
| --- | --- | --- |
| `FakePixProvider` | `PixProvider` (OpenPix, Mercado Pago) | `advance(id, status)`, `charges` |
| `FakeCardProvider` | `CardProvider` (cards) | `decline_next(reason)`, `advance(id, status)`, `charges` |
| `FakePayoutProvider` | `PayoutProvider` (Pix payouts) | `transfers`, `status=` in the constructor |
| `FakeTextBackend` | `TextBackend` (local model) | `queue(...)`, `prompts` |
| `FakeModerationBackend` | `ModerationBackend` | `flag(substring)`, `checked` |
| `FakePushDispatcher` | `PushDispatcher` (FCM/APNs/WebPush) | `sent`, `sent_to(token)` |
| `FakeEmailUtils` | `EmailUtils` (SMTP) | `outbox`, `sent_to(address)` |
| `FakeGeocodingBackend` | `GeocodingBackend` (Nominatim) | `add_place(...)`, `queries` |
| `FakeRoutingBackend` | `RoutingBackend` (OSRM) | `add_route(...)`, `routes` |
| `FakeWebSearchBackend` | `WebSearchBackend` (Searxng) | `add_results(...)`, `queries` |

All of them expose `fail_next(error)`. All but `FakeEmailUtils` also expose
`calls` — the list of methods that ran, in order; on `FakeEmailUtils` the
record is the `outbox` itself, because `send` is the only method it
replaces.

## Card: a decline is a result, not an exception

`FakeCardProvider` follows the `CardProvider` contract. A charge is
approved, unless `decline_next(reason)` queued a decline — which comes back
as a `FAILED` charge, the way the real adapter returns the provider's `402`:

```python
import asyncio

from tempest_fastapi_sdk.integrations.payment import (
    CardChargeRequest,
    PaymentStatus,
)
from tempest_fastapi_sdk.testing.fakes import FakeCardProvider


async def main() -> None:
    """Decline one card, approve the next, refund it in two steps."""
    card = FakeCardProvider()
    request = CardChargeRequest(
        amount_cents=10000,
        reference="order-1",
        card_token="tok",
        payment_method_id="visa",
    )

    card.decline_next("rejected_by_issuer")
    declined = await card.create_card_charge(request)
    paid = await card.create_card_charge(request)
    partial = await card.refund_card_charge(paid.provider_charge_id, amount_cents=3000)
    full = await card.refund_card_charge(paid.provider_charge_id)

    assert declined.status is PaymentStatus.FAILED
    print(declined.status_detail, partial.status.value, full.status.value)


asyncio.run(main())
```

```text
rejected_by_issuer paid refunded
```

`capture=False` returns `AUTHORIZED`, waiting for `capture_card_charge` or
`cancel_card_charge`. The transitions the provider refuses — capturing what
is not authorized, refunding what was not paid, refunding more than is left
— raise `ValueError`: the refusal branch is reachable, but the exception type
is the fake's (the real adapter raises `httpx.HTTPStatusError`).
`advance(id, PaymentStatus.CHARGED_BACK)` reaches a chargeback, which no
contract method does.

## Email: an outbox instead of SMTP

`FakeEmailUtils` subclasses `EmailUtils` rather than implementing a protocol —
because `UserAuthService` is typed against the concrete class, and inheritance
is what lets the fake pass through there with the type-checker satisfied:

```python
import asyncio

from tempest_fastapi_sdk.testing.fakes import FakeEmailUtils


async def main() -> None:
    """Send nothing, and assert on what would have been sent."""
    mailer = FakeEmailUtils()

    await mailer.send("ana@example.test", "Activation", "Your link: ...")

    print(len(mailer.outbox))
    print(mailer.outbox[0].subject)
    print(mailer.sent_to("ana@example.test")[0].body)


if __name__ == "__main__":
    asyncio.run(main())
```

Output:

```text
1
Activation
Your link: ...
```

Only `send` is replaced. `render_template` is still `EmailUtils`', so a test
can assert on the **same** HTML production renders — pass `template_dir=` the
way you pass it in production.

## Text model: microseconds instead of VRAM

```python
import asyncio

from tempest_fastapi_sdk.testing.fakes import FakeTextBackend


async def main() -> None:
    """Answer from a queue, then from the echo default."""
    backend = FakeTextBackend()
    backend.queue("Hi! How can I help?")

    print(await backend.generate("Greet the customer"))
    print(await backend.generate("Greet the customer"))

    chunks = [chunk async for chunk in backend.stream("count to three")]
    print(chunks)


if __name__ == "__main__":
    asyncio.run(main())
```

Output:

```text
Hi! How can I help?
[fake] Greet the customer
['[fake]', ' count', ' to', ' three']
```

Once the queue is empty, the default reply **echoes the prompt** — so a failed
assertion tells you which prompt produced it, instead of showing generic text.
`stream` hands the reply over piece by piece, so your incremental rendering is
actually exercised.

## Geo: no Nominatim, no OSRM

```python
import asyncio

from tempest_fastapi_sdk.geo import Coordinate
from tempest_fastapi_sdk.testing.fakes import (
    FakeGeocodingBackend,
    FakeRoutingBackend,
)


async def main() -> None:
    """Resolve a place from a table, and estimate a trip offline."""
    geocoder = FakeGeocodingBackend()
    geocoder.add_place("Recife", Coordinate(latitude=-8.05, longitude=-34.9))

    print(await geocoder.geocode("recife") is not None)
    print(await geocoder.geocode("a street that does not exist"))

    router = FakeRoutingBackend()
    estimate = await router.route(
        Coordinate(latitude=-8.05, longitude=-34.9),
        Coordinate(latitude=-7.99, longitude=-34.85),
    )
    print(estimate.source, round(estimate.distance_km, 1))


if __name__ == "__main__":
    asyncio.run(main())
```

Output:

```text
True
None
fake 11.2
```

Two decisions worth explaining:

- **An unregistered address resolves to `None`**, which is what the real
  backend answers for an address nobody can find — and it is the branch a
  service forgets to handle.
- **The route invents no arithmetic**: `FakeRoutingBackend` delegates to
  `estimate_travel`, the offline estimator the SDK already ships. The numbers
  your test asserts on are the SDK's own, not a parallel calculation that can
  drift from it.

`reverse` returns the **nearest** registered place, not an exact-coordinate
match: a service reverse-geocodes a GPS reading, and a GPS reading never lands
on the decimal you stored.

## Wiring it into the service

The fake goes where the real one went — into the `Depends`:

```python
from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.integrations.payment import PixChargeRequest, PixProvider
from tempest_fastapi_sdk.testing.fakes import FakePixProvider

app = FastAPI()
_FAKE_PROVIDER = FakePixProvider()


def get_pix_provider() -> PixProvider:
    """Provide the Pix provider this deployment charges with.

    Returns:
        PixProvider: The provider, seen through the contract.
    """
    return _FAKE_PROVIDER


@app.post("/charges")
async def create_charge(
    provider: PixProvider = Depends(get_pix_provider),
) -> dict[str, str]:
    """Open a charge.

    Args:
        provider (PixProvider): Injected provider.

    Returns:
        dict[str, str]: The copy-and-paste code, for the payer.
    """
    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=1990, reference="order-1042"),
    )
    return {"br_code": charge.br_code or ""}
```

In tests, use `app.dependency_overrides[get_pix_provider]` to inject a fresh
fake per test — a new instance is clean state.

!!! warning "Fakes are for development and tests, not production"
    Nothing here persists: the state lives in the process and dies with it.
    And nothing here really charges, delivers or notifies. Wiring a fake into
    production means a checkout that accepts a payment which never arrived.

    The defence is the usual one — the provider comes from configuration, and
    the environment decides. If you want an explicit alarm, make your
    service's `build_provider` refuse a fake when the environment is
    production — with a field **your** `Settings` declares (`ENVIRONMENT`,
    say): no SDK settings mixin carries one.

## The guarantee: a fake cannot drift from its seam

A fake whose signature differs from the seam is worse than no fake — the
service passes against it and fails against the real provider, which is
exactly the defect the fake exists to prevent.

So `tests/testing/test_fakes_contract.py` compares, for every fake:

- that every callable on the seam exists on the fake;
- that the **parameter names** and the return annotation match, via
  `inspect.signature`;
- that it is `async` exactly where the seam is `async` — a sync stand-in would
  pass a name-only check and block the event loop;
- and that every exported fake is covered: a new fake with no entry in the
  table fails the guard instead of shipping unchecked.

Measured: with one parameter renamed from `max_results` to `limit`, the guard
fails with `['self', 'query', 'limit'] == ['self', 'query', 'max_results']`.

## Recap

- `from tempest_fastapi_sdk.testing.fakes import Fake...` — ten seams, no
  credentials and no network.
- A fake is not a mock: it holds state, and you **move** that state
  (`advance`, `flag`, `add_place`, `queue`).
- `fail_next(error)` reaches the failing branch, with the exception the real
  client raises.
- `calls`, `outbox`, `sent`, `prompts`, `charges`, `queries`: what happened
  stays inspectable.
- Resolution is lazy: asking for a Pix fake imports neither genai nor geo —
  nor loads anything beyond what `import tempest_fastapi_sdk` already loaded
  (`push` comes in through there, not through the fake).
- A signature guard keeps the fake from drifting away from its seam.
