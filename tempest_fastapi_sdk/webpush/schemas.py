"""Pydantic schemas mirroring the Web Push browser API surface."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, field_validator

from tempest_fastapi_sdk.schemas.base import BaseSchema


class WebPushKeysSchema(BaseSchema):
    """The ``keys`` object returned by ``PushSubscription.toJSON()``.

    Browsers expose the encryption material as two URL-safe base64
    strings; the SDK keeps the wire names so subscriptions can be
    stored verbatim and replayed on dispatch.

    Attributes:
        p256dh (str): Client public ECDH key (URL-safe base64).
        auth (str): Client auth secret (URL-safe base64).
    """

    p256dh: str = Field(
        min_length=1,
        title="Client ECDH public key",
        description="URL-safe base64 ECDH P-256 public key from the browser.",
        examples=["BNc8R7r2EXAMPLE_p256dh_url_safe_base64"],
    )
    auth: str = Field(
        min_length=1,
        title="Client auth secret",
        description=(
            "URL-safe base64 auth secret. Used as the IKM for the "
            "RFC 8291 content-encryption key derivation."
        ),
        examples=["kQ9p3FEXAMPLE_auth_secret"],
    )


class WebPushSubscriptionSchema(BaseSchema):
    """Server-side representation of ``PushSubscription.toJSON()``.

    Two browser-flavored field names (``expirationTime``) are exposed
    via aliases so the schema round-trips JSON produced by
    ``JSON.stringify(subscription)`` without manual key mangling.

    Attributes:
        endpoint (str): Push service endpoint URL.
        keys (WebPushKeysSchema): Encryption keys.
        expiration_time (int | None): Optional expiration timestamp
            in milliseconds since epoch. Aliased to ``expirationTime``
            on the wire.
    """

    model_config = ConfigDict(
        extra="ignore",
        from_attributes=True,
        use_enum_values=True,
        str_strip_whitespace=True,
        validate_assignment=True,
        arbitrary_types_allowed=True,
        populate_by_name=True,
    )

    endpoint: str = Field(
        min_length=1,
        title="Push service endpoint URL",
        description=(
            "HTTPS URL the push service exposes for this subscription. "
            "Must be ``https://`` (browser spec)."
        ),
        examples=[
            "https://fcm.googleapis.com/fcm/send/abcDEF123",
            "https://updates.push.services.mozilla.com/wpush/v2/gAAAA",
        ],
    )
    keys: WebPushKeysSchema = Field(
        title="Encryption keys",
        description="ECDH public key + auth secret from the browser.",
    )
    expiration_time: int | None = Field(
        default=None,
        alias="expirationTime",
        title="Subscription expiration time",
        description=(
            "Optional expiration timestamp in milliseconds since epoch. "
            "``None`` means the subscription does not auto-expire."
        ),
        examples=[None, 1_800_000_000_000],
    )

    @field_validator("endpoint")
    @classmethod
    def _endpoint_must_be_https(cls, value: str) -> str:
        """Reject endpoints that aren't ``https://`` URLs.

        The Web Push spec requires HTTPS, and accepting arbitrary
        schemes (``file://``, ``http://localhost``, etc.) would turn
        the server into an SSRF proxy when subscriptions come from
        untrusted clients.

        Args:
            value (str): The candidate endpoint URL.

        Returns:
            str: The same URL when valid.

        Raises:
            ValueError: When the URL is malformed or not HTTPS.
        """
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise ValueError("Invalid endpoint URL") from exc
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Push endpoints must be HTTPS URLs.")
        return value


class WebPushPayloadSchema(BaseSchema):
    """Optional helper for the JSON payload delivered with each push.

    Mirrors the Notification API options exposed in service workers;
    callers that want stricter typing can subclass this for their
    application-specific event types.

    Attributes:
        title (str | None): Notification title shown to the user.
        body (str | None): Notification body.
        icon (str | None): URL of the icon to display.
        badge (str | None): URL of the badge icon (Android).
        tag (str | None): Tag used to coalesce notifications.
        data (dict[str, Any] | None): Arbitrary application payload.
        actions (list[dict[str, Any]] | None): Action button specs.
    """

    title: str | None = Field(
        default=None,
        title="Notification title",
        description="Heading shown by the OS notification UI.",
        examples=[None, "New message", "Build failed"],
    )
    body: str | None = Field(
        default=None,
        title="Notification body",
        description="Short text shown below the title.",
        examples=[None, "You have 3 unread items."],
    )
    icon: str | None = Field(
        default=None,
        title="Icon URL",
        description="HTTPS URL of the notification icon.",
        examples=[None, "https://example.com/icons/notify.png"],
    )
    badge: str | None = Field(
        default=None,
        title="Badge icon URL",
        description="URL of the badge icon (Android status-bar mark).",
        examples=[None, "https://example.com/icons/badge.png"],
    )
    tag: str | None = Field(
        default=None,
        title="Notification tag",
        description=(
            "Tag used by the browser to coalesce notifications — newer "
            "messages with the same tag replace older ones."
        ),
        examples=[None, "chat:123", "build:status"],
    )
    data: dict[str, Any] | None = Field(
        default=None,
        title="Application payload",
        description=(
            "Arbitrary structured payload forwarded to the service "
            "worker's ``notificationclick`` handler."
        ),
        examples=[None, {"url": "/inbox", "notification_id": "abc"}],
    )
    actions: list[dict[str, Any]] | None = Field(
        default=None,
        title="Action buttons",
        description=(
            "Notification action button specs, each ``{action, title, "
            "icon?}`` per the Notifications API."
        ),
        examples=[
            None,
            [
                {"action": "open", "title": "Open"},
                {"action": "dismiss", "title": "Dismiss"},
            ],
        ],
    )


__all__: list[str] = [
    "WebPushKeysSchema",
    "WebPushPayloadSchema",
    "WebPushSubscriptionSchema",
]
