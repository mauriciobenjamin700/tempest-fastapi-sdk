"""Web Push transport — the browser half of the unified module.

The VAPID implementation itself stays in
:mod:`tempest_fastapi_sdk.webpush.dispatcher`, which every existing
project already imports; this module adapts it to the
:class:`~tempest_fastapi_sdk.push.PushDispatcher` contract.

Adapting rather than moving is deliberate. Physically relocating
:class:`~tempest_fastapi_sdk.webpush.WebPushDispatcher` under
``push.transports`` would make the ``webpush`` package import ``push``
while ``push`` imports ``webpush.schemas`` — an import cycle through two
package ``__init__`` files. The user-facing unification (one contract,
one service, one router) is unaffected by where the VAPID code lives.
"""

from __future__ import annotations

from tempest_fastapi_sdk.push.dispatcher import PushDeviceGoneError, PushError
from tempest_fastapi_sdk.push.schemas import (
    PushDevice,
    PushPayloadSchema,
    PushPlatform,
)
from tempest_fastapi_sdk.webpush.dispatcher import (
    WebPushDispatcher,
    WebPushError,
    WebPushGoneError,
)
from tempest_fastapi_sdk.webpush.schemas import (
    WebPushKeysSchema,
    WebPushPayloadSchema,
    WebPushSubscriptionSchema,
)


class WebPushTransport:
    """Deliver unified payloads to browsers over VAPID Web Push.

    Attributes:
        dispatcher (WebPushDispatcher): The configured VAPID sender.
        platforms (frozenset[str]): ``{"web"}`` — the only platform this
            transport claims.
    """

    platforms: frozenset[str] = frozenset({PushPlatform.WEB.value})

    def __init__(self, dispatcher: WebPushDispatcher) -> None:
        """Initialize the transport.

        Args:
            dispatcher (WebPushDispatcher): A configured VAPID dispatcher.
        """
        self.dispatcher: WebPushDispatcher = dispatcher

    @staticmethod
    def _to_subscription(device: PushDevice) -> WebPushSubscriptionSchema:
        """Rebuild the browser subscription from a stored device.

        Args:
            device (PushDevice): The web device to deliver to.

        Returns:
            WebPushSubscriptionSchema: The subscription the VAPID
            dispatcher accepts.

        Raises:
            PushError: When the device carries no encryption material —
                a row registered as ``web`` without ``p256dh`` / ``auth``
                cannot be encrypted for, and failing loudly beats sending
                a request the push service will reject.
        """
        if not device.p256dh or not device.auth:
            raise PushError(
                "Web device is missing its encryption keys",
                masked_token=device.masked_token,
            )
        return WebPushSubscriptionSchema(
            endpoint=device.token,
            keys=WebPushKeysSchema(p256dh=device.p256dh, auth=device.auth),
            expiration_time=device.expiration_time,
        )

    @staticmethod
    def _to_web_payload(payload: PushPayloadSchema) -> WebPushPayloadSchema:
        """Widen the unified payload into the browser notification shape.

        ``image`` becomes the notification ``icon``, which is what a
        service worker reads for the small graphic; the ``data`` map is
        forwarded untouched to the ``notificationclick`` handler.

        Args:
            payload (PushPayloadSchema): The unified notification.

        Returns:
            WebPushPayloadSchema: The browser-flavoured payload.
        """
        return WebPushPayloadSchema(
            title=payload.title,
            body=payload.body,
            icon=payload.image,
            tag=payload.tag,
            data=dict(payload.data) or None,
        )

    async def send(self, device: PushDevice, payload: PushPayloadSchema) -> None:
        """Deliver ``payload`` to a browser subscription.

        Args:
            device (PushDevice): The web device to deliver to.
            payload (PushPayloadSchema): The notification to deliver.

        Raises:
            PushDeviceGoneError: When the push service answers 404/410, so
                the subscription should be deleted.
            PushError: For any other delivery failure, including a web row
                stored without its encryption keys.
        """
        subscription = self._to_subscription(device)
        try:
            await self.dispatcher.send(subscription, self._to_web_payload(payload))
        except WebPushGoneError as error:
            raise PushDeviceGoneError(
                f"Subscription gone for {device.masked_token}",
                masked_token=device.masked_token,
                status_code=error.status_code,
            ) from error
        except WebPushError as error:
            raise PushError(
                f"Web Push delivery failed for {device.masked_token}",
                masked_token=device.masked_token,
                status_code=error.status_code,
            ) from error


__all__: list[str] = [
    "WebPushTransport",
]
