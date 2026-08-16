"""Concrete push transports.

Each module here implements
:class:`tempest_fastapi_sdk.push.PushDispatcher` for one provider. They
are imported eagerly because neither import pulls its optional
dependency: the Web Push transport only imports the SDK's own
``webpush`` package, and the FCM transport imports ``firebase_admin``
inside its constructor.

Re-exports use the PEP 484 ``from x import Y as Y`` explicit re-export
form combined with ``__all__``.
"""

from tempest_fastapi_sdk.push.transports.fcm import FCMTransport as FCMTransport
from tempest_fastapi_sdk.push.transports.webpush import (
    WebPushTransport as WebPushTransport,
)

__all__: list[str] = [
    "FCMTransport",
    "WebPushTransport",
]
