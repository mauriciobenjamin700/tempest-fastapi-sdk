"""Pydantic schemas mirroring the Web Push browser API surface."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

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

    p256dh: str = Field(min_length=1, description="ECDH public key.")
    auth: str = Field(min_length=1, description="Client auth secret.")


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

    endpoint: str = Field(min_length=1, description="Push service URL.")
    keys: WebPushKeysSchema
    expiration_time: int | None = Field(
        default=None,
        alias="expirationTime",
        description="Optional expiration time (ms since epoch).",
    )


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

    title: str | None = None
    body: str | None = None
    icon: str | None = None
    badge: str | None = None
    tag: str | None = None
    data: dict[str, Any] | None = None
    actions: list[dict[str, Any]] | None = None


__all__: list[str] = [
    "WebPushKeysSchema",
    "WebPushPayloadSchema",
    "WebPushSubscriptionSchema",
]
