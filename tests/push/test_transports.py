"""Transport-level behaviour: payload mapping and error translation.

The FCM half runs against the real ``firebase_admin.messaging`` classes —
the message is built for real and only ``messaging.send`` is patched —
because the value of this transport is precisely *which* provider error
means "delete this device", and a hand-written stub would happily agree
with a wrong answer.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tempest_fastapi_sdk import (
    FCMTransport,
    PushDevice,
    PushDeviceGoneError,
    PushError,
    PushPayloadSchema,
    PushPlatform,
    WebPushDispatcher,
    WebPushGoneError,
    WebPushPayloadSchema,
    WebPushSubscriptionSchema,
    WebPushTransport,
)
from tempest_fastapi_sdk.webpush.dispatcher import WebPushError

firebase_admin = pytest.importorskip(
    "firebase_admin", reason="needs the optional [firebase] extra"
)
messaging = pytest.importorskip("firebase_admin.messaging")

WEB_DEVICE = PushDevice(
    platform=PushPlatform.WEB,
    token="https://push.example/device-1",
    p256dh="p256dh-key",
    auth="auth-secret",
)
MOBILE_DEVICE = PushDevice(platform=PushPlatform.ANDROID, token="fcm-token-1")


class _StubWebPushDispatcher(WebPushDispatcher):
    """Web Push dispatcher that records the call or raises a given error."""

    def __init__(self, error: Exception | None = None) -> None:
        """Initialize the stub.

        Args:
            error (Exception | None): Raised on send, when given.
        """
        super().__init__("dummy-key", vapid_subject="mailto:ops@example.com")
        self._error: Exception | None = error
        self.calls: list[tuple[WebPushSubscriptionSchema, Any]] = []

    async def send(
        self,
        subscription: WebPushSubscriptionSchema,
        payload: Any,
        *,
        ttl_seconds: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Record the call, or raise the configured error.

        Args:
            subscription (WebPushSubscriptionSchema): The recipient.
            payload (Any): The browser payload.
            ttl_seconds (int | None): Unused here.
            headers (dict[str, str] | None): Unused here.

        Raises:
            Exception: The configured error, when one was given.
        """
        if self._error is not None:
            raise self._error
        self.calls.append((subscription, payload))


class TestWebPushTransport:
    async def test_rebuilds_the_subscription_and_maps_the_payload(self) -> None:
        """The stored device turns back into a browser subscription."""
        dispatcher = _StubWebPushDispatcher()
        transport = WebPushTransport(dispatcher)

        await transport.send(
            WEB_DEVICE,
            PushPayloadSchema(
                title="Hi", body="There", image="https://x/i.png", data={"url": "/a"}
            ),
        )

        subscription, payload = dispatcher.calls[0]
        assert subscription.endpoint == WEB_DEVICE.token
        assert subscription.keys.p256dh == "p256dh-key"
        assert isinstance(payload, WebPushPayloadSchema)
        assert payload.icon == "https://x/i.png"
        assert payload.data == {"url": "/a"}

    async def test_gone_becomes_the_unified_prune_signal(self) -> None:
        """404/410 is what tells the service to delete the subscription."""
        transport = WebPushTransport(
            _StubWebPushDispatcher(WebPushGoneError("gone", status_code=410))
        )

        with pytest.raises(PushDeviceGoneError) as error:
            await transport.send(WEB_DEVICE, PushPayloadSchema(title="Hi"))

        assert error.value.status_code == 410

    async def test_other_failures_stay_retryable(self) -> None:
        """A 500 from the push service must not delete the device."""
        transport = WebPushTransport(
            _StubWebPushDispatcher(WebPushError("boom", status_code=500))
        )

        with pytest.raises(PushError) as error:
            await transport.send(WEB_DEVICE, PushPayloadSchema(title="Hi"))

        assert not isinstance(error.value, PushDeviceGoneError)

    async def test_web_row_without_keys_fails_loudly(self) -> None:
        """A web device stored without encryption material cannot be encrypted for."""
        transport = WebPushTransport(_StubWebPushDispatcher())
        broken = PushDevice(platform=PushPlatform.WEB, token="https://push.example/x")

        with pytest.raises(PushError, match="encryption keys"):
            await transport.send(broken, PushPayloadSchema(title="Hi"))

    async def test_the_raw_endpoint_never_reaches_the_error_message(self) -> None:
        """Errors identify the device by mask, never by credential."""
        transport = WebPushTransport(
            _StubWebPushDispatcher(WebPushError("boom", status_code=500))
        )

        with pytest.raises(PushError) as error:
            await transport.send(WEB_DEVICE, PushPayloadSchema(title="Hi"))

        assert WEB_DEVICE.token not in str(error.value)


@pytest.fixture(scope="module")
def fcm_app() -> Any:
    """Return a Firebase app for the transport to bind to.

    Reuses the app across the module — ``initialize_app`` raises on a name
    that already exists, and the transport never needs a distinct one.
    ``messaging.send`` is patched in every test that delivers, so no
    credential is ever exercised.

    Returns:
        Any: A ``firebase_admin.App``.
    """
    name = "tempest-fcm-transport-test"
    try:
        return firebase_admin.get_app(name)
    except ValueError:
        return firebase_admin.initialize_app(
            options={"projectId": "tempest-test"}, name=name
        )


class TestFCMTransport:
    def test_registration_tokens_go_in_the_token_field(self, fcm_app: Any) -> None:
        """The message still serializes a ``token``, not a ``fid``.

        ``firebase-admin`` 7.5.0 marks ``Message.token`` deprecated in
        favour of ``fid``, but the two encode **different wire fields**
        (measured). An FCM registration token belongs in ``token``, so
        following the deprecation blindly would send the wrong field.
        """
        transport = FCMTransport(app=fcm_app)

        message = transport._build_message(
            MOBILE_DEVICE, PushPayloadSchema(title="Hi", body="There")
        )

        wire = json.loads(str(message))
        assert wire["token"] == "fcm-token-1"
        assert "fid" not in wire

    def test_tag_becomes_both_collapse_keys(self, fcm_app: Any) -> None:
        """One unified tag coalesces on Android and on iOS alike."""
        transport = FCMTransport(app=fcm_app)

        message = transport._build_message(
            MOBILE_DEVICE, PushPayloadSchema(title="Hi", tag="chat:1")
        )

        wire = json.loads(str(message))
        assert wire["android"]["collapse_key"] == "chat:1"
        assert wire["apns"]["headers"]["apns-collapse-id"] == "chat:1"

    async def test_unregistered_prunes_the_device(
        self, fcm_app: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``UnregisteredError`` is FCM's version of 410."""
        transport = FCMTransport(app=fcm_app)

        def fake_send(message: Any, dry_run: bool, app: Any) -> str:
            """Fail as an uninstalled app would.

            Args:
                message (Any): The built message.
                dry_run (bool): Validate-only flag.
                app (Any): The Firebase app.

            Returns:
                str: Never returns.

            Raises:
                UnregisteredError: Always.
            """
            raise messaging.UnregisteredError("token not registered")

        monkeypatch.setattr(messaging, "send", fake_send)

        with pytest.raises(PushDeviceGoneError):
            await transport.send(MOBILE_DEVICE, PushPayloadSchema(title="Hi"))

    async def test_invalid_argument_does_not_prune(
        self, fcm_app: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed payload must not delete the user's whole fleet.

        FCM raises ``InvalidArgumentError`` both for a bad token and for a
        bad message, and the two are indistinguishable from the exception
        type alone. Treating it as "device gone" would wipe every device
        the first time a notification body is wrong, so it stays a plain
        retryable failure.
        """
        transport = FCMTransport(app=fcm_app)

        def fake_send(message: Any, dry_run: bool, app: Any) -> str:
            """Fail the way a malformed message does.

            Args:
                message (Any): The built message.
                dry_run (bool): Validate-only flag.
                app (Any): The Firebase app.

            Returns:
                str: Never returns.

            Raises:
                InvalidArgumentError: Always.
            """
            raise firebase_admin.exceptions.InvalidArgumentError("bad payload")

        monkeypatch.setattr(messaging, "send", fake_send)

        with pytest.raises(PushError) as error:
            await transport.send(MOBILE_DEVICE, PushPayloadSchema(title="Hi"))

        assert not isinstance(error.value, PushDeviceGoneError)

    async def test_delivery_passes_the_dry_run_flag(
        self, fcm_app: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``dry_run=True`` reaches FCM, so staging never wakes real devices."""
        transport = FCMTransport(app=fcm_app, dry_run=True)
        seen: dict[str, Any] = {}

        def fake_send(message: Any, dry_run: bool, app: Any) -> str:
            """Record the call.

            Args:
                message (Any): The built message.
                dry_run (bool): Validate-only flag.
                app (Any): The Firebase app.

            Returns:
                str: A fake message id.
            """
            seen["dry_run"] = dry_run
            seen["app"] = app
            return "projects/x/messages/1"

        monkeypatch.setattr(messaging, "send", fake_send)

        await transport.send(MOBILE_DEVICE, PushPayloadSchema(title="Hi"))

        assert seen["dry_run"] is True
        assert seen["app"] is fcm_app

    def test_claimed_platforms_do_not_overlap(self) -> None:
        """Web and FCM claim disjoint platforms, so routing is unambiguous."""
        assert FCMTransport.platforms.isdisjoint(WebPushTransport.platforms)
