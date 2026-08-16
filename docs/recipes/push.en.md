# Push (web + mobile in one flow)

A product with a site **and** an app ends up with two notification APIs:
one for the browser, one for the phone, and a caller that has to know
which kind of device it is talking to before sending anything. The
`tempest_fastapi_sdk.push` module exists to erase that difference.

You say "notify this user". The SDK reads their devices, routes each one
through the right transport, and **deletes exactly the ones the provider
disowned** — 404/410 on Web Push, `UNREGISTERED` on FCM. One rule, two
vocabularies.

!!! info "Installation"
    - Web: the `[webpush]` extra — `uv add "tempest-fastapi-sdk[webpush]"`.
    - Mobile: the `[firebase]` extra —
      `uv add "tempest-fastapi-sdk[firebase]"` (the same extra and the
      same service account as [Firebase auth](firebase-auth.en.md); one
      credential serves both features).
    - Web only, mobile only, or both: you install what you use.
      `import tempest_fastapi_sdk.push` works with neither.

!!! info "I already use `webpush` — does this break it?"
    No. `tempest_fastapi_sdk.webpush` still exports `WebPushDispatcher`,
    `WebPushSubscriptionService`, `make_web_push_router` and the schemas —
    same code, same names. The `push` module is an **addition**, not a
    replacement: reach for it when you want one path for browsers and
    phones. The [Web Push](webpush.en.md) recipe still applies to a
    browser-only service.

## The minimal path

### 1. The device table

```python
# src/db/models/device.py
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from uuid import UUID

from tempest_fastapi_sdk import BaseDeviceTokenModel


class DeviceModel(BaseDeviceTokenModel):
    """One device — browser or phone — that receives notifications."""

    __tablename__ = "device_tokens"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
```

One table for both worlds: a browser row keeps `p256dh` / `auth`; a
mobile row leaves those `NULL` and puts the FCM registration token in
`token`.

### 2. The transports

```python
# src/api/dependencies/push.py
from tempest_fastapi_sdk import (
    FCMTransport,
    FirebaseAuth,
    WebPushDispatcher,
    WebPushTransport,
)

from src.core.settings import settings

firebase = FirebaseAuth(credentials_path=settings.FIREBASE_CREDENTIALS_PATH)

transports = [
    WebPushTransport(WebPushDispatcher(**settings.webpush_kwargs())),
    FCMTransport(auth=firebase),
]
```

`FCMTransport(auth=...)` reuses the Firebase app that ID token
verification already initialized — one service account loaded, two
features.

### 3. The service and the route

```python
# src/api/routers/push.py
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository, DeviceService, make_push_router

from src.api.dependencies.auth import current_user_id
from src.api.dependencies.push import transports
from src.core.settings import settings
from src.db.models import DeviceModel
from src.db.session import get_session


def device_service(session: AsyncSession) -> DeviceService[Any]:
    """Build the request-scoped device service."""
    repository: BaseRepository[Any] = BaseRepository(session, model=DeviceModel)
    return DeviceService(repository, transports)


router = make_push_router(
    service_factory=device_service,
    session_factory=get_session,
    current_user_id=current_user_id,
    vapid_public_key=settings.VAPID_PUBLIC_KEY,
)
```

That gives you `POST /api/push/register`, `POST /api/push/unregister` and
`GET /api/push/vapid-public-key`.

### 4. Notify

```python
import asyncio
from typing import Any
from uuid import UUID

from tempest_fastapi_sdk import BaseRepository, DeviceService, PushPayloadSchema

from src.api.dependencies.push import transports
from src.db.models import DeviceModel
from src.db.session import get_session


async def main() -> None:
    """Run this example."""
    async for session in get_session():
        repository: BaseRepository[Any] = BaseRepository(session, model=DeviceModel)
        service: DeviceService[Any] = DeviceService(repository, transports)
        result = await service.notify_user(
            UUID("2f1b0f1e-0f4a-4e35-9a5f-2c8a2f9a1234"),
            PushPayloadSchema(
                title="Order confirmed",
                body="Order #1042 is out for delivery.",
                tag="order:1042",
                data={"url": "/orders/1042"},
            ),
        )
        print(result.as_dict())


asyncio.run(main())
```

A typical result with three devices, one of them dead:

```json
{"delivered": 2, "pruned": ["ios/9f2c1a0b3d4e"], "failed": [], "skipped": []}
```

## How it works, piece by piece

### The contract: one method

```python
from typing import Protocol

from tempest_fastapi_sdk import PushDevice, PushPayloadSchema


class PushDispatcher(Protocol):
    """Deliver one notification to one device."""

    platforms: frozenset[str]

    async def send(self, device: PushDevice, payload: PushPayloadSchema) -> None:
        """Deliver the payload to the device."""
        ...
```

It is a `Protocol`, in the same shape `UploadStorage` uses for storage:
the service depends on the contract, never on a concrete backend. A test
passes a fake transport without inheriting anything; a new provider
(direct APNs, Huawei Push) plugs in without touching the service.

Everything interesting — fan-out, pruning, partial failure — is the
**service**'s job, not the transport's. That is what keeps the two halves
from drifting apart.

### The payload that survives both

`PushPayloadSchema` is the intersection that carries on both providers:

| Field | On the browser | On FCM |
| --- | --- | --- |
| `title` / `body` | `Notification` | `messaging.Notification` |
| `image` | notification `icon` | `notification.image` |
| `tag` | `tag` (coalescing) | `android.collapse_key` + `apns-collapse-id` |
| `data` | `notificationclick` payload | `data` |

!!! warning "`data` is `dict[str, str]`, and that is not fussiness"
    FCM **rejects** non-string values. The schema narrows the type here so
    the error appears at the edge, with a field name, instead of becoming
    a provider rejection halfway through a fan-out.

### Pruning: one rule, two codes

This is the point of the module. When the provider says the device is
gone, the row leaves the database — but each provider says it its own
way:

| Provider | Signal | Becomes |
| --- | --- | --- |
| Web Push | HTTP 404 / 410 | `PushDeviceGoneError` → row deleted |
| FCM | `UnregisteredError` | `PushDeviceGoneError` → row deleted |
| FCM | `SenderIdMismatchError` (token from another project) | `PushDeviceGoneError` → row deleted |
| Either | any other failure | `PushError` → row **kept**, retried next time |

!!! danger "FCM's `InvalidArgumentError` does **not** prune — deliberately"
    FCM raises `InvalidArgumentError` both for a bad token and for a
    **malformed payload**, and the exception type does not separate the
    two. Treating it as "dead device" would delete the user's entire
    fleet the first time a notification body is wrong. The opposite choice
    costs one failed attempt per fan-out until the client re-registers —
    the cheaper mistake of the two. This deliberately diverges from the
    original proposal in issue #157.

### One device failing never aborts the others

Deliveries run concurrently and independently. The result says what
happened to each one:

```python
import asyncio
from typing import Any
from uuid import UUID

from tempest_fastapi_sdk import (
    BaseRepository,
    DeviceService,
    PushFanoutResult,
    PushPayloadSchema,
)

from src.api.dependencies.push import transports
from src.db.models import DeviceModel
from src.db.session import get_session


async def main() -> None:
    """Run this example."""
    async for session in get_session():
        repository: BaseRepository[Any] = BaseRepository(session, model=DeviceModel)
        service: DeviceService[Any] = DeviceService(repository, transports)
        result: PushFanoutResult = await service.notify_user(
            UUID("2f1b0f1e-0f4a-4e35-9a5f-2c8a2f9a1234"),
            PushPayloadSchema(title="Hi"),
        )
        print(result.delivered)   # how many accepted
        print(result.pruned)      # deleted (masked)
        print(result.failed)      # failed, still in the table
        print(result.skipped)     # no transport configured


asyncio.run(main())
```

!!! tip "A device token never reaches a log line"
    A registration token is a credential: whoever holds it can notify that
    device. Everything the result exposes — and everything the SDK logs —
    goes through `mask_push_token`, which keeps a 12-character SHA-256
    prefix. Same treatment `_mask_endpoint` already gave the Web Push
    endpoint.

### `skipped` is not `pruned`

A service that wired **only** Web Push and has iOS rows in the database
does not delete them: the device is alive, the wiring is missing. They
come back under `skipped`, and start being delivered the day an
`FCMTransport` is added.

### Narrowing the fan-out

```python
import asyncio
from typing import Any
from uuid import UUID

from tempest_fastapi_sdk import (
    BaseRepository,
    DeviceService,
    PushPayloadSchema,
    PushPlatform,
)

from src.api.dependencies.push import transports
from src.db.models import DeviceModel
from src.db.session import get_session


async def main() -> None:
    """Run this example."""
    async for session in get_session():
        repository: BaseRepository[Any] = BaseRepository(session, model=DeviceModel)
        service: DeviceService[Any] = DeviceService(repository, transports)
        await service.notify_user(
            UUID("2f1b0f1e-0f4a-4e35-9a5f-2c8a2f9a1234"),
            PushPayloadSchema(title="Browsers only"),
            platforms=[PushPlatform.WEB],
            exclude_tokens=["https://push.example/the-device-that-caused-it"],
        )


asyncio.run(main())
```

`exclude_tokens` is the multi-device sync case: whoever made the change
must not notify themselves. An excluded device is never contacted **and
never pruned**.

### Registration is idempotent by token

Registering the same token twice updates the row and refreshes
`last_seen_at` instead of duplicating. And when the device changes hands
(sign out, sign in on the same handset), the row **moves to the new
user** — without that, the next notification would reach the previous
account.

### Configuration

```python
from tempest_fastapi_sdk import BaseAppSettings
from tempest_fastapi_sdk.settings import PushSettings


class Settings(PushSettings, BaseAppSettings):
    """Settings for a service notifying browsers and phones."""


settings = Settings()
print(settings.web_enabled, settings.mobile_enabled, settings.enabled)
```

`PushSettings` joins `WebPushSettings` and `FirebaseSettings` — and it
exists because of a real trap: both declare `enabled`, so composing them
by hand makes the MRO silently pick the Web Push one, and a mobile-only
service reads `enabled is False` with FCM perfectly configured. Here
`enabled` answers "can this service notify anyone?", and the two halves
stay readable through `web_enabled` / `mobile_enabled`.

## Testing

The contract is a `Protocol`, so a fake transport is a class with one
method:

```python
from tempest_fastapi_sdk import PushDevice, PushDeviceGoneError, PushPayloadSchema


class FakeTransport:
    """Accepts everything except the tokens you ask it to disown."""

    platforms: frozenset[str] = frozenset({"web", "ios", "android"})

    def __init__(self, gone: set[str]) -> None:
        """Store the tokens to disown."""
        self.gone: set[str] = gone
        self.sent: list[str] = []

    async def send(self, device: PushDevice, payload: PushPayloadSchema) -> None:
        """Record the delivery or disown the device."""
        if device.token in self.gone:
            raise PushDeviceGoneError("gone", masked_token=device.masked_token)
        self.sent.append(device.token)
```

The SDK's own suite goes further on the real transports: the FCM test
builds the message with the genuine `firebase_admin` classes and asserts
the serialized JSON carries `token`. That pins a measured detail — on
7.5.0 `Message.token` is **deprecated** in favour of `fid`, but the two
are **different wire fields** (`{"token": ...}` vs `{"fid": ...}`), and an
FCM registration token belongs in `token`. Following the deprecation
literally would send the wrong field.

## Recap

- One `PushDispatcher` (`Protocol`), two transports: `WebPushTransport`
  and `FCMTransport`.
- One table (`BaseDeviceTokenModel`) and one service (`DeviceService`)
  for browsers and phones.
- Unified pruning: 404/410 and `UNREGISTERED` / `SenderIdMismatch` delete
  the row; any other failure keeps it and retries. FCM's
  `InvalidArgument` does **not** prune, so a bad payload cannot wipe the
  fleet.
- One device failing never aborts the others; `PushFanoutResult` reports
  who delivered, who was pruned, who failed and who had no transport.
- Tokens never appear in logs or responses — only the masked hash.
- `tempest_fastapi_sdk.webpush` is unchanged; `push` is an addition.
