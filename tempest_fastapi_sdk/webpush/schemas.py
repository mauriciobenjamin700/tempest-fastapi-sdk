"""Pydantic schemas mirroring the Web Push browser API surface."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, field_validator, model_validator

from tempest_fastapi_sdk.schemas.base import BaseSchema

logger = logging.getLogger(__name__)


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
        validation_alias="expirationTime",
        serialization_alias="expirationTime",
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

        The Web Push spec requires HTTPS. This is only the shape check:
        it does not decide whether the host is safe to contact, because
        that needs a DNS lookup at send time. The destination policy
        (public addresses only, no redirects, a timeout) is enforced by
        :class:`~tempest_fastapi_sdk.webpush.WebPushDispatcher` on every
        dispatch.

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


class WebPushActionSchema(BaseSchema):
    """One notification button, as the service worker reads it.

    ``action``, ``title`` and ``icon`` are the browser's
    ``NotificationAction``. ``url`` is not part of that type: the
    tempest-react-sdk push handler moves it into ``data.actionUrls`` and
    opens it when this button is clicked, falling back to the payload's
    top-level ``url``.

    Attributes:
        action (str): Identifier delivered as ``event.action`` on click.
        title (str): Button label.
        icon (str | None): Button icon URL.
        url (str | None): Page opened when this button is clicked.
    """

    action: str = Field(
        min_length=1,
        title="Action identifier",
        description="Identifier delivered as ``event.action`` on click.",
        examples=["open", "dismiss"],
    )
    title: str = Field(
        min_length=1,
        title="Button label",
        description="Text shown on the notification button.",
        examples=["Open", "Dismiss"],
    )
    icon: str | None = Field(
        default=None,
        title="Button icon URL",
        description="URL of the icon shown on the button.",
        examples=[None, "https://example.com/icons/open.png"],
    )
    url: str | None = Field(
        default=None,
        title="Button destination",
        description=(
            "Page opened when this button is clicked. Falls back to the "
            "payload's top-level ``url``."
        ),
        examples=[None, "/inbox/42"],
    )


class WebPushPayloadSchema(BaseSchema):
    """JSON payload delivered with each push, in the shape the service worker reads.

    Mirrors tempest-react-sdk's ``PushPayload`` (v0.86.0), pinned by
    ``tests/webpush/test_payload_contract.py``: the field names on
    the wire are the ones ``installPushHandler`` reads, so
    :meth:`to_json` serializes by alias (``requireInteraction``) and drops
    ``None`` fields, leaving the browser defaults in place.

    The click destination is the top-level ``url``. The React push handler
    writes ``data.url`` **from** it after spreading ``data``, so a free-form
    ``data`` bag cannot redirect the click, and a ``url`` placed only inside
    ``data`` is ignored: every click opens ``/``. Earlier releases of this
    schema documented ``data={"url": ...}``, so construction lifts a string
    ``data["url"]`` into ``url`` when ``url`` is absent. When both are given
    and differ, the top-level ``url`` wins and a warning is logged.

    Attributes:
        title (str | None): Notification title shown to the user.
        body (str | None): Notification body.
        icon (str | None): URL of the icon to display.
        badge (str | None): URL of the badge icon (Android).
        image (str | None): URL of a large image shown in the notification.
        tag (str | None): Tag used to coalesce notifications.
        url (str | None): Page opened when the notification body is clicked.
        actions (list[WebPushActionSchema] | None): Notification buttons.
        require_interaction (bool | None): Keep the notification on screen
            until the user acts. ``requireInteraction`` on the wire.
        vibrate (int | list[int] | None): Vibration pattern in milliseconds.
        silent (bool | None): Show without sound or vibration.
        renotify (bool | None): Alert again when the same ``tag`` replaces
            an older notification.
        timestamp (int | None): Event time in epoch milliseconds.
        dir (Literal["auto", "ltr", "rtl"] | None): Text direction.
        lang (str | None): BCP 47 language tag of the title and body.
        data (dict[str, Any] | None): Arbitrary application payload.
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
    image: str | None = Field(
        default=None,
        title="Image URL",
        description="URL of a large image shown inside the notification.",
        examples=[None, "https://example.com/images/order-42.png"],
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
    url: str | None = Field(
        default=None,
        title="Click destination",
        description=(
            "Page opened when the notification body is clicked. The "
            "service worker opens ``/`` when it is absent."
        ),
        examples=[None, "/inbox", "/orders/42"],
    )
    actions: list[WebPushActionSchema] | None = Field(
        default=None,
        title="Action buttons",
        description=(
            "Notification buttons. Each may carry its own ``url``, opened "
            "when that button is clicked."
        ),
        examples=[
            None,
            [
                {"action": "open", "title": "Open", "url": "/inbox/42"},
                {"action": "dismiss", "title": "Dismiss"},
            ],
        ],
    )
    require_interaction: bool | None = Field(
        default=None,
        validation_alias="requireInteraction",
        serialization_alias="requireInteraction",
        title="Require interaction",
        description="Keep the notification on screen until the user acts.",
        examples=[None, True],
    )
    vibrate: int | list[int] | None = Field(
        default=None,
        title="Vibration pattern",
        description="Vibration pattern in milliseconds (mobile).",
        examples=[None, 200, [100, 50, 100]],
    )
    silent: bool | None = Field(
        default=None,
        title="Silent",
        description="Show the notification without sound or vibration.",
        examples=[None, True],
    )
    renotify: bool | None = Field(
        default=None,
        title="Renotify",
        description=(
            "Alert again when a notification with the same ``tag`` "
            "replaces an older one."
        ),
        examples=[None, True],
    )
    timestamp: int | None = Field(
        default=None,
        title="Event timestamp",
        description="Event time in epoch milliseconds, shown by some platforms.",
        examples=[None, 1_800_000_000_000],
    )
    dir: Literal["auto", "ltr", "rtl"] | None = Field(
        default=None,
        title="Text direction",
        description="Text direction of the title and body.",
        examples=[None, "ltr"],
    )
    lang: str | None = Field(
        default=None,
        title="Language",
        description="BCP 47 language tag of the title and body.",
        examples=[None, "pt-BR"],
    )
    data: dict[str, Any] | None = Field(
        default=None,
        title="Application payload",
        description=(
            "Arbitrary structured payload forwarded to "
            "``event.notification.data``. Its ``url`` and ``actionUrls`` "
            "keys are overwritten by the service worker; use the top-level "
            "``url`` for the click destination."
        ),
        examples=[None, {"notification_id": "abc", "chat_id": "42"}],
    )

    @model_validator(mode="before")
    @classmethod
    def _lift_data_url(cls, value: Any) -> Any:
        """Lift a legacy ``data["url"]`` into the top-level ``url``.

        Only a mapping input is inspected; anything else is returned
        untouched for the regular validation to judge. ``data`` itself is
        left as given.

        Args:
            value (Any): The raw input handed to the schema.

        Returns:
            Any: The input, with ``url`` filled from ``data["url"]`` when
            ``url`` was absent and ``data["url"]`` is a string.
        """
        if not isinstance(value, Mapping):
            return value
        data = value.get("data")
        if not isinstance(data, Mapping):
            return value
        legacy = data.get("url")
        if not isinstance(legacy, str):
            return value
        current = value.get("url")
        if current is None:
            return {**value, "url": legacy}
        if current != legacy:
            logger.warning(
                "WebPushPayloadSchema got url=%r and data['url']=%r; the "
                "top-level url decides the click destination.",
                current,
                legacy,
            )
        return value

    def to_json(self) -> str:
        """Serialize the payload as the service worker expects it.

        Uses the wire names (``requireInteraction``) and drops ``None``
        fields, so an option the payload does not set stays absent and the
        browser applies its own default instead of receiving ``null``.

        Returns:
            str: The JSON encoded payload.
        """
        return self.model_dump_json(by_alias=True, exclude_none=True)


__all__: list[str] = [
    "WebPushActionSchema",
    "WebPushKeysSchema",
    "WebPushPayloadSchema",
    "WebPushSubscriptionSchema",
]
