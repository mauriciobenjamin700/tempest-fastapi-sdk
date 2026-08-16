"""Unified push notifications — one API for browsers and phones.

Before this module the SDK shipped Web Push and nothing for mobile, so a
product with both ended up with two notification APIs and a caller that
had to know which kind of device it was talking to. Here the application
says "notify this user" and the SDK picks the transport per device:

* :class:`PushDispatcher` — the one-method contract every transport
  implements (a :class:`~typing.Protocol`, like ``UploadStorage`` for
  storage);
* :class:`WebPushTransport` — VAPID Web Push, wrapping the dispatcher the
  ``webpush`` package already ships;
* :class:`FCMTransport` — iOS and Android through ``firebase_admin``,
  sharing the ``[firebase]`` extra and the service account that
  :class:`tempest_fastapi_sdk.auth.FirebaseAuth` loads;
* :class:`DeviceService` — register, fan out, and prune the devices the
  provider disowned (HTTP 404/410 on the web, ``UnregisteredError`` on
  FCM — one rule, two vocabularies);
* :func:`make_push_router` — ``POST /register`` / ``POST /unregister``
  plus the VAPID public key.

``tempest_fastapi_sdk.webpush`` keeps working exactly as before: it is
the same code, still exported under the same names.

Re-exports use the PEP 484 ``from x import Y as Y`` explicit re-export
form combined with ``__all__`` so every type-checker accepts
``from tempest_fastapi_sdk.push import DeviceService`` without a
"private import usage" diagnostic.
"""

from tempest_fastapi_sdk.push.dispatcher import (
    PushDeviceGoneError as PushDeviceGoneError,
)
from tempest_fastapi_sdk.push.dispatcher import PushDispatcher as PushDispatcher
from tempest_fastapi_sdk.push.dispatcher import PushError as PushError
from tempest_fastapi_sdk.push.router import make_push_router as make_push_router
from tempest_fastapi_sdk.push.schemas import (
    DeviceRegistrationSchema as DeviceRegistrationSchema,
)
from tempest_fastapi_sdk.push.schemas import PushDevice as PushDevice
from tempest_fastapi_sdk.push.schemas import PushFanoutResult as PushFanoutResult
from tempest_fastapi_sdk.push.schemas import PushPayloadSchema as PushPayloadSchema
from tempest_fastapi_sdk.push.schemas import PushPlatform as PushPlatform
from tempest_fastapi_sdk.push.schemas import PushResult as PushResult
from tempest_fastapi_sdk.push.schemas import mask_push_token as mask_push_token
from tempest_fastapi_sdk.push.service import DeviceService as DeviceService
from tempest_fastapi_sdk.push.transports import FCMTransport as FCMTransport
from tempest_fastapi_sdk.push.transports import WebPushTransport as WebPushTransport

__all__: list[str] = [
    "DeviceRegistrationSchema",
    "DeviceService",
    "FCMTransport",
    "PushDevice",
    "PushDeviceGoneError",
    "PushDispatcher",
    "PushError",
    "PushFanoutResult",
    "PushPayloadSchema",
    "PushPlatform",
    "PushResult",
    "WebPushTransport",
    "make_push_router",
    "mask_push_token",
]
