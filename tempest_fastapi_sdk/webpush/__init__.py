"""Web Push (VAPID) dispatch and subscription schemas."""

from tempest_fastapi_sdk.webpush.dispatcher import (
    WebPushDispatcher as WebPushDispatcher,
)
from tempest_fastapi_sdk.webpush.dispatcher import (
    WebPushError as WebPushError,
)
from tempest_fastapi_sdk.webpush.dispatcher import (
    WebPushGoneError as WebPushGoneError,
)
from tempest_fastapi_sdk.webpush.router import (
    make_web_push_router as make_web_push_router,
)
from tempest_fastapi_sdk.webpush.schemas import (
    WebPushKeysSchema as WebPushKeysSchema,
)
from tempest_fastapi_sdk.webpush.schemas import (
    WebPushPayloadSchema as WebPushPayloadSchema,
)
from tempest_fastapi_sdk.webpush.schemas import (
    WebPushSubscriptionSchema as WebPushSubscriptionSchema,
)
from tempest_fastapi_sdk.webpush.service import (
    WebPushSubscriptionService as WebPushSubscriptionService,
)

__all__: list[str] = [
    "WebPushDispatcher",
    "WebPushError",
    "WebPushGoneError",
    "WebPushKeysSchema",
    "WebPushPayloadSchema",
    "WebPushSubscriptionSchema",
    "WebPushSubscriptionService",
    "make_web_push_router",
]
