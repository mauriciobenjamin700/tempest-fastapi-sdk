"""Web Push (VAPID) dispatch and subscription schemas."""

from tempest_fastapi_sdk.webpush.dispatcher import (
    WebPushDispatcher,
    WebPushError,
    WebPushGoneError,
)
from tempest_fastapi_sdk.webpush.router import make_web_push_router
from tempest_fastapi_sdk.webpush.schemas import (
    WebPushKeysSchema,
    WebPushPayloadSchema,
    WebPushSubscriptionSchema,
)
from tempest_fastapi_sdk.webpush.service import WebPushSubscriptionService

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
