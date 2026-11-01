"""FCM over a switched-off Firebase: push is discarded, never raised.

``FIREBASE_ENABLED=false`` makes ``firebase_kwargs()`` return
``{"enabled": False}``. A transport built on that authenticator claims no
platform, so the device service skips every mobile device instead of
failing the fan-out, and the transport says so once, when it is built.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import pytest

from tempest_fastapi_sdk import (
    DeviceService,
    FCMTransport,
    FirebaseSettings,
    IntegrationDisabledException,
    PushDevice,
    PushPayloadSchema,
    PushPlatformEnum,
)
from tempest_fastapi_sdk.auth import FirebaseAuth


def disabled_auth() -> FirebaseAuth:
    """Build the authenticator ``firebase_kwargs()`` yields with the flag off.

    Returns:
        FirebaseAuth: An authenticator built with ``enabled=False``.
    """
    return FirebaseAuth(**FirebaseSettings(FIREBASE_ENABLED=False).firebase_kwargs())


def test_transport_claims_no_platform() -> None:
    transport = FCMTransport(auth=disabled_auth())
    assert transport.platforms == frozenset()
    assert transport.enabled is False


def test_device_service_routes_no_mobile_device_through_it() -> None:
    service: DeviceService[Any] = DeviceService(
        repository=None,  # type: ignore[arg-type]
        transports=[FCMTransport(auth=disabled_auth())],
    )
    assert service.transport_for("ios") is None
    assert service.transport_for("android") is None


def test_building_it_logs_one_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="tempest_fastapi_sdk.push"):
        FCMTransport(auth=disabled_auth())
    records = [r for r in caplog.records if "FCM transport" in r.getMessage()]
    assert len(records) == 1


async def test_direct_send_raises() -> None:
    transport = FCMTransport(auth=disabled_auth())
    device = PushDevice(token="t" * 40, platform=PushPlatformEnum.ANDROID)
    with pytest.raises(IntegrationDisabledException):
        await transport.send(device, PushPayloadSchema(title="Hi"))


def test_needs_no_firebase_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment with Firebase off need not install ``[firebase]``."""
    for name in ("firebase_admin", "firebase_admin.messaging"):
        monkeypatch.setitem(sys.modules, name, None)
    assert FCMTransport(auth=disabled_auth()).platforms == frozenset()


class TestDisabledFirebaseAuth:
    """Verification answers 503, with or without a token."""

    def test_app_refuses(self) -> None:
        with pytest.raises(IntegrationDisabledException):
            _ = disabled_auth().app

    async def test_verify_refuses(self) -> None:
        with pytest.raises(IntegrationDisabledException) as error:
            await disabled_auth().verify("token")
        assert error.value.details == {"integration": "firebase"}

    async def test_dependencies_refuse_before_the_missing_token(self) -> None:
        auth = disabled_auth()
        with pytest.raises(IntegrationDisabledException):
            await auth.get_identity(None)
        with pytest.raises(IntegrationDisabledException):
            await auth.get_optional_identity(None)
        with pytest.raises(IntegrationDisabledException):
            await auth.get_uid(None)
