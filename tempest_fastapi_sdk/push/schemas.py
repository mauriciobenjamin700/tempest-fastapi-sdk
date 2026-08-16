"""Wire and value types shared by every push transport.

The point of this module is that an application talks about *devices*,
not about browsers and phones. A :class:`PushDevice` describes any
target — a browser subscription or a mobile registration token — and a
:class:`PushPayloadSchema` describes a notification in terms both
transports can carry.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import ConfigDict, Field

from tempest_fastapi_sdk.schemas.base import BaseSchema


class PushPlatform(StrEnum):
    """The kind of device a notification is going to.

    Attributes:
        WEB: A browser subscription delivered over VAPID Web Push.
        IOS: An iOS app instance delivered over FCM.
        ANDROID: An Android app instance delivered over FCM.
    """

    WEB = "web"
    IOS = "ios"
    ANDROID = "android"


def mask_push_token(token: str) -> str:
    """Return a stable, non-sensitive identifier for a device token.

    A registration token (or a Web Push endpoint) is a credential: whoever
    holds it can send notifications to that device. It must never reach a
    log line or an API response, but failures still have to be traceable to
    a specific device — so this keeps a short SHA-256 prefix, which is
    stable across calls and useless to an attacker.

    Args:
        token (str): The raw device token or push endpoint.

    Returns:
        str: A twelve-character SHA-256 prefix.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class PushDevice:
    """One delivery target, whatever the platform.

    Web devices carry the browser's encryption material in ``p256dh`` /
    ``auth`` and put the push-service endpoint in ``token``; mobile
    devices put the FCM registration token there and leave the key fields
    ``None``. Keeping them in one type is what lets
    :class:`~tempest_fastapi_sdk.push.DeviceService` fan out to a user's
    whole fleet without the caller sorting devices by kind first.

    Attributes:
        platform (PushPlatform): Which transport delivers to this device.
        token (str): The per-device identity — a Web Push endpoint URL for
            ``WEB``, an FCM registration token for ``IOS`` / ``ANDROID``.
        p256dh (str | None): Browser ECDH public key. ``WEB`` only.
        auth (str | None): Browser auth secret. ``WEB`` only.
        expiration_time (int | None): Browser ``expirationTime`` in
            milliseconds since epoch, when the subscription reported one.
    """

    platform: PushPlatform
    token: str
    p256dh: str | None = None
    auth: str | None = None
    expiration_time: int | None = None

    @property
    def masked_token(self) -> str:
        """Return the token as it may appear in a log line.

        Returns:
            str: ``<platform>/<sha256-prefix>``.
        """
        return f"{self.platform.value}/{mask_push_token(self.token)}"


class PushPayloadSchema(BaseSchema):
    """A notification described in terms both transports can carry.

    Web Push accepts an arbitrary JSON body that the service worker
    interprets, while FCM has a fixed ``notification`` shape plus a
    string-only ``data`` map. This schema is the intersection that
    survives both, and each transport widens it on its own terms —
    :class:`~tempest_fastapi_sdk.push.WebPushTransport` serializes the
    whole thing as JSON, while
    :class:`~tempest_fastapi_sdk.push.FCMTransport` maps it onto
    ``messaging.Notification`` + ``data``.

    Attributes:
        title (str | None): Heading shown by the OS notification UI.
        body (str | None): Short text under the title.
        image (str | None): HTTPS URL of the image — the icon on Web Push,
            ``notification.image`` on FCM.
        tag (str | None): Coalescing key. Newer notifications with the same
            tag replace older ones (``tag`` on Web Push,
            ``android.collapse_key`` + ``apns-collapse-id`` on FCM).
        data (dict[str, str]): Structured payload handed to the client.
            **Values must be strings** — FCM rejects anything else, so the
            constraint is modelled here instead of failing at send time.
    """

    model_config = ConfigDict(
        extra="ignore",
        from_attributes=True,
        use_enum_values=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    title: str | None = Field(
        default=None,
        title="Notification title",
        description="Heading shown by the OS notification UI.",
        examples=[None, "New message", "Your ride is here"],
    )
    body: str | None = Field(
        default=None,
        title="Notification body",
        description="Short text shown below the title.",
        examples=[None, "You have 3 unread items."],
    )
    image: str | None = Field(
        default=None,
        title="Image URL",
        description=(
            "HTTPS URL of the notification image — the icon on Web Push, "
            "``notification.image`` on FCM."
        ),
        examples=[None, "https://example.com/icons/notify.png"],
    )
    tag: str | None = Field(
        default=None,
        title="Coalescing tag",
        description=(
            "Newer notifications carrying the same tag replace older ones "
            "on the device."
        ),
        examples=[None, "chat:123", "ride:status"],
    )
    data: dict[str, str] = Field(
        default_factory=dict,
        title="Application payload",
        description=(
            "String-to-string map handed to the client. FCM refuses "
            "non-string values, so the type is narrowed here."
        ),
        examples=[{}, {"url": "/inbox", "notification_id": "abc"}],
    )


class DeviceRegistrationSchema(BaseSchema):
    """What a client POSTs to register itself for notifications.

    One body serves both sides. A browser sends ``platform="web"`` with
    the endpoint in ``token`` plus the two key fields from
    ``PushSubscription.toJSON()``; a mobile app sends ``platform="ios"``
    or ``"android"`` with the FCM registration token and nothing else.

    Attributes:
        token (str): Push endpoint URL (web) or FCM registration token
            (mobile).
        platform (PushPlatform): Which kind of device this is.
        p256dh (str | None): Browser ECDH public key. Required for ``web``.
        auth (str | None): Browser auth secret. Required for ``web``.
        expiration_time (int | None): Browser ``expirationTime``, when
            present.
        app_version (str | None): Optional client build identifier, useful
            when a notification format depends on the app version.
    """

    model_config = ConfigDict(
        extra="ignore",
        from_attributes=True,
        use_enum_values=True,
        str_strip_whitespace=True,
        validate_assignment=True,
        populate_by_name=True,
    )

    token: str = Field(
        min_length=1,
        title="Device token",
        description=(
            "Push service endpoint URL for the web, FCM registration token "
            "for iOS / Android."
        ),
        examples=[
            "https://fcm.googleapis.com/fcm/send/abcDEF123",
            "fMEXAMPLE:APA91bH...",
        ],
    )
    platform: PushPlatform = Field(
        title="Device platform",
        description="Which transport delivers to this device.",
        examples=["web", "ios", "android"],
    )
    p256dh: str | None = Field(
        default=None,
        title="Client ECDH public key",
        description="URL-safe base64 ECDH P-256 public key. Web only.",
        examples=[None, "BNc8R7r2EXAMPLE_p256dh"],
    )
    auth: str | None = Field(
        default=None,
        title="Client auth secret",
        description="URL-safe base64 auth secret. Web only.",
        examples=[None, "kQ9p3FEXAMPLE_auth"],
    )
    expiration_time: int | None = Field(
        default=None,
        alias="expirationTime",
        title="Subscription expiration time",
        description="Browser ``expirationTime`` in milliseconds since epoch.",
        examples=[None, 1_800_000_000_000],
    )
    app_version: str | None = Field(
        default=None,
        title="Client build",
        description="Optional app build identifier stored with the device.",
        examples=[None, "1.4.2+310"],
    )


@dataclass(frozen=True)
class PushResult:
    """The outcome of one delivery attempt.

    Attributes:
        platform (PushPlatform): The device's platform.
        masked_token (str): The device, identified safely for logs.
        delivered (bool): Whether the provider accepted the message.
        pruned (bool): Whether the provider reported the device as gone, so
            it was deleted from the store.
        error (str | None): Short description of the failure, when the
            attempt neither delivered nor pruned.
    """

    platform: PushPlatform
    masked_token: str
    delivered: bool
    pruned: bool = False
    error: str | None = None


@dataclass(frozen=True)
class PushFanoutResult:
    """What happened across a user's whole fleet.

    A failing device never aborts the fan-out, so this is how the caller
    learns which devices took the notification, which were deleted, and
    which merely failed and will be retried next time.

    Attributes:
        results (tuple[PushResult, ...]): One entry per targeted device, in
            the order the devices were read from the store.
        skipped (tuple[str, ...]): Masked tokens of devices no configured
            transport can reach — an iOS row in a service that wired only
            Web Push, for instance. They are **not** pruned: the device is
            fine, the wiring is missing.
    """

    results: tuple[PushResult, ...] = ()
    skipped: tuple[str, ...] = field(default_factory=tuple)

    @property
    def delivered(self) -> int:
        """Return how many devices accepted the notification.

        Returns:
            int: The number of successful deliveries.
        """
        return sum(1 for result in self.results if result.delivered)

    @property
    def pruned(self) -> tuple[str, ...]:
        """Return the devices deleted because the provider disowned them.

        Returns:
            tuple[str, ...]: Masked tokens of the pruned devices.
        """
        return tuple(result.masked_token for result in self.results if result.pruned)

    @property
    def failed(self) -> tuple[str, ...]:
        """Return the devices that failed without being disowned.

        Returns:
            tuple[str, ...]: Masked tokens of the failed devices. These
            keep their row and will be tried again on the next fan-out.
        """
        return tuple(
            result.masked_token
            for result in self.results
            if not result.delivered and not result.pruned
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe summary, for a router response or a log line.

        Returns:
            dict[str, Any]: ``delivered`` count plus the masked ``pruned``,
            ``failed`` and ``skipped`` lists. No raw token appears.
        """
        return {
            "delivered": self.delivered,
            "pruned": list(self.pruned),
            "failed": list(self.failed),
            "skipped": list(self.skipped),
        }


__all__: list[str] = [
    "DeviceRegistrationSchema",
    "PushDevice",
    "PushFanoutResult",
    "PushPayloadSchema",
    "PushPlatform",
    "PushResult",
    "mask_push_token",
]
