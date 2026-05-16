"""Tests for tempest_fastapi_sdk.webpush.schemas."""

import pytest

from tempest_fastapi_sdk import (
    WebPushKeysSchema,
    WebPushPayloadSchema,
    WebPushSubscriptionSchema,
)


class TestWebPushSubscription:
    def test_round_trip_browser_payload(self) -> None:
        browser_payload = {
            "endpoint": "https://push.example.com/abc",
            "expirationTime": 1234567890,
            "keys": {"p256dh": "pk", "auth": "auth"},
        }
        sub = WebPushSubscriptionSchema.model_validate(browser_payload)
        assert sub.endpoint == "https://push.example.com/abc"
        assert sub.expiration_time == 1234567890
        assert sub.keys.p256dh == "pk"

    def test_expiration_time_optional(self) -> None:
        sub = WebPushSubscriptionSchema(
            endpoint="https://push.example.com/abc",
            keys=WebPushKeysSchema(p256dh="pk", auth="a"),
        )
        assert sub.expiration_time is None

    def test_dump_uses_alias_for_expiration_time(self) -> None:
        sub = WebPushSubscriptionSchema(
            endpoint="https://push.example.com/abc",
            keys=WebPushKeysSchema(p256dh="pk", auth="a"),
            expiration_time=10,
        )
        payload = sub.model_dump(by_alias=True)
        assert "expirationTime" in payload
        assert payload["expirationTime"] == 10

    def test_empty_endpoint_rejected(self) -> None:
        with pytest.raises(ValueError):
            WebPushSubscriptionSchema(
                endpoint="",
                keys=WebPushKeysSchema(p256dh="pk", auth="a"),
            )


class TestWebPushPayload:
    def test_all_fields_optional(self) -> None:
        payload = WebPushPayloadSchema()
        assert payload.title is None
        assert payload.actions is None

    def test_full_payload_serializes(self) -> None:
        payload = WebPushPayloadSchema(
            title="New message",
            body="Hello",
            icon="/icon.png",
            data={"chat_id": "abc"},
            actions=[{"action": "open", "title": "Open"}],
        )
        rendered = payload.model_dump(exclude_none=True)
        assert rendered["title"] == "New message"
        assert rendered["data"] == {"chat_id": "abc"}
