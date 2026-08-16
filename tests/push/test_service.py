"""Fan-out, pruning and registration for the unified device service."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    BaseRepository,
    BaseUserModel,
    DeviceRegistrationSchema,
    DeviceService,
    PushDevice,
    PushDeviceGoneError,
    PushError,
    PushPayloadSchema,
    PushPlatform,
    make_device_token_model,
)


class _DeviceUser(BaseUserModel):
    __tablename__ = "device_users"


_Device = make_device_token_model(
    user_table="device_users",
    tablename="device_tokens_test",
    class_name="_Device",
)


class _RecordingTransport:
    """Transport double that records deliveries and fails on demand.

    Attributes:
        platforms (frozenset[str]): Platforms this double claims.
        sent (list[str]): Masked tokens it accepted, in call order.
        gone (set[str]): Raw tokens it reports as disowned.
        broken (set[str]): Raw tokens it fails on without disowning.
    """

    def __init__(
        self,
        platforms: set[str],
        *,
        gone: set[str] | None = None,
        broken: set[str] | None = None,
    ) -> None:
        """Initialize the double.

        Args:
            platforms (set[str]): Platform values to claim.
            gone (set[str] | None): Tokens to disown.
            broken (set[str] | None): Tokens to fail on.
        """
        self.platforms: frozenset[str] = frozenset(platforms)
        self.sent: list[str] = []
        self.gone: set[str] = gone or set()
        self.broken: set[str] = broken or set()

    async def send(self, device: PushDevice, payload: PushPayloadSchema) -> None:
        """Record the delivery, or fail as configured.

        Args:
            device (PushDevice): The target device.
            payload (PushPayloadSchema): The notification.

        Raises:
            PushDeviceGoneError: When the token is in ``gone``.
            PushError: When the token is in ``broken``.
        """
        if device.token in self.gone:
            raise PushDeviceGoneError("gone", masked_token=device.masked_token)
        if device.token in self.broken:
            raise PushError("boom", masked_token=device.masked_token)
        self.sent.append(device.token)


def _service(session: AsyncSession, *transports: Any) -> DeviceService[Any]:
    """Build a service over the test device table.

    Args:
        session (AsyncSession): The test session.
        *transports (Any): Transports to wire.

    Returns:
        DeviceService[Any]: The service under test.
    """
    repository: BaseRepository[Any] = BaseRepository(session, model=_Device)
    return DeviceService(repository, transports)


def _web(token: str) -> DeviceRegistrationSchema:
    """Build a browser registration body.

    Args:
        token (str): The push endpoint.

    Returns:
        DeviceRegistrationSchema: The registration payload.
    """
    return DeviceRegistrationSchema(
        token=token,
        platform=PushPlatform.WEB,
        p256dh="p256dh-key",
        auth="auth-secret",
    )


def _mobile(token: str, platform: PushPlatform) -> DeviceRegistrationSchema:
    """Build a mobile registration body.

    Args:
        token (str): The FCM registration token.
        platform (PushPlatform): ``IOS`` or ``ANDROID``.

    Returns:
        DeviceRegistrationSchema: The registration payload.
    """
    return DeviceRegistrationSchema(token=token, platform=platform)


async def _register_fleet(service: DeviceService[Any], user_id: UUID) -> None:
    """Register one web, one iOS and one Android device for a user.

    Args:
        service (DeviceService[Any]): The service under test.
        user_id (UUID): The owner.
    """
    await service.register(user_id, _web("https://push.example/web-1"))
    await service.register(user_id, _mobile("ios-token-1", PushPlatform.IOS))
    await service.register(user_id, _mobile("android-token-1", PushPlatform.ANDROID))


class TestRegister:
    async def test_stores_a_web_device_with_its_keys(
        self, session: AsyncSession
    ) -> None:
        """A browser registration keeps the encryption material."""
        service = _service(session)
        user_id = uuid4()

        row = await service.register(user_id, _web("https://push.example/aaa"))

        assert row.platform == "web"
        assert row.p256dh == "p256dh-key"
        assert row.last_seen_at is not None

    async def test_stores_a_mobile_device_without_keys(
        self, session: AsyncSession
    ) -> None:
        """A mobile registration leaves the web-only columns NULL."""
        service = _service(session)
        user_id = uuid4()

        row = await service.register(user_id, _mobile("tok", PushPlatform.ANDROID))

        assert row.platform == "android"
        assert row.p256dh is None
        assert row.auth is None

    async def test_is_idempotent_by_token(self, session: AsyncSession) -> None:
        """Re-registering the same token updates the row instead of duplicating."""
        service = _service(session)
        user_id = uuid4()

        first = await service.register(user_id, _mobile("same", PushPlatform.IOS))
        second = await service.register(user_id, _mobile("same", PushPlatform.IOS))

        assert first.id == second.id
        assert len(await service.list_for_user(user_id)) == 1

    async def test_reassigns_a_handset_to_the_new_user(
        self, session: AsyncSession
    ) -> None:
        """Signing in as someone else moves the device, so notifications follow."""
        service = _service(session)
        user_a, user_b = uuid4(), uuid4()

        await service.register(user_a, _mobile("handset", PushPlatform.IOS))
        await service.register(user_b, _mobile("handset", PushPlatform.IOS))

        assert await service.list_for_user(user_a) == []
        assert len(await service.list_for_user(user_b)) == 1


class TestUnregister:
    async def test_removes_the_device(self, session: AsyncSession) -> None:
        """Unregistering deletes the row."""
        service = _service(session)
        user_id = uuid4()
        await service.register(user_id, _mobile("bye", PushPlatform.ANDROID))

        assert await service.unregister("bye") is True
        assert await service.list_for_user(user_id) == []

    async def test_unknown_token_is_a_noop(self, session: AsyncSession) -> None:
        """Unregistering an unknown token answers False without raising."""
        service = _service(session)

        assert await service.unregister("never-seen") is False


class TestFanout:
    async def test_each_device_goes_through_its_own_transport(
        self, session: AsyncSession
    ) -> None:
        """Web, iOS and Android each get one delivery, by the right transport."""
        web = _RecordingTransport({"web"})
        fcm = _RecordingTransport({"ios", "android"})
        service = _service(session, web, fcm)
        user_id = uuid4()
        await _register_fleet(service, user_id)

        result = await service.notify_user(user_id, PushPayloadSchema(title="hi"))

        assert result.delivered == 3
        assert web.sent == ["https://push.example/web-1"]
        assert set(fcm.sent) == {"ios-token-1", "android-token-1"}

    async def test_disowned_devices_are_pruned_on_both_transports(
        self, session: AsyncSession
    ) -> None:
        """404/410 on the web and UNREGISTERED on FCM delete the same way."""
        web = _RecordingTransport({"web"}, gone={"https://push.example/web-1"})
        fcm = _RecordingTransport({"ios", "android"}, gone={"ios-token-1"})
        service = _service(session, web, fcm)
        user_id = uuid4()
        await _register_fleet(service, user_id)

        result = await service.notify_user(user_id, PushPayloadSchema(title="hi"))

        assert result.delivered == 1
        assert len(result.pruned) == 2
        remaining = {row.token for row in await service.list_for_user(user_id)}
        assert remaining == {"android-token-1"}

    async def test_one_failure_does_not_abort_the_others(
        self, session: AsyncSession
    ) -> None:
        """A failing device is reported, keeps its row, and the rest still go out."""
        web = _RecordingTransport({"web"}, broken={"https://push.example/web-1"})
        fcm = _RecordingTransport({"ios", "android"})
        service = _service(session, web, fcm)
        user_id = uuid4()
        await _register_fleet(service, user_id)

        result = await service.notify_user(user_id, PushPayloadSchema(title="hi"))

        assert result.delivered == 2
        assert len(result.failed) == 1
        assert len(await service.list_for_user(user_id)) == 3

    async def test_devices_without_a_transport_are_skipped_not_pruned(
        self, session: AsyncSession
    ) -> None:
        """A web-only service keeps mobile rows instead of deleting them."""
        web = _RecordingTransport({"web"})
        service = _service(session, web)
        user_id = uuid4()
        await _register_fleet(service, user_id)

        result = await service.notify_user(user_id, PushPayloadSchema(title="hi"))

        assert result.delivered == 1
        assert len(result.skipped) == 2
        assert len(await service.list_for_user(user_id)) == 3

    async def test_excluded_device_is_never_contacted(
        self, session: AsyncSession
    ) -> None:
        """The device that caused the event does not notify itself."""
        web = _RecordingTransport({"web"})
        fcm = _RecordingTransport({"ios", "android"})
        service = _service(session, web, fcm)
        user_id = uuid4()
        await _register_fleet(service, user_id)

        result = await service.notify_user(
            user_id,
            PushPayloadSchema(title="hi"),
            exclude_tokens=["ios-token-1"],
        )

        assert result.delivered == 2
        assert "ios-token-1" not in fcm.sent

    async def test_platform_filter_restricts_the_fanout(
        self, session: AsyncSession
    ) -> None:
        """A web-only announcement never wakes the phones."""
        web = _RecordingTransport({"web"})
        fcm = _RecordingTransport({"ios", "android"})
        service = _service(session, web, fcm)
        user_id = uuid4()
        await _register_fleet(service, user_id)

        result = await service.notify_user(
            user_id,
            PushPayloadSchema(title="hi"),
            platforms=[PushPlatform.WEB],
        )

        assert result.delivered == 1
        assert fcm.sent == []

    async def test_user_without_devices_returns_an_empty_result(
        self, session: AsyncSession
    ) -> None:
        """No devices is a successful no-op, not an error."""
        service = _service(session, _RecordingTransport({"web"}))

        result = await service.notify_user(uuid4(), PushPayloadSchema(title="hi"))

        assert result.delivered == 0
        assert result.results == ()

    async def test_result_summary_never_leaks_a_raw_token(
        self, session: AsyncSession
    ) -> None:
        """Everything the result exposes is masked."""
        web = _RecordingTransport({"web"}, gone={"https://push.example/web-1"})
        service = _service(session, web)
        user_id = uuid4()
        await service.register(user_id, _web("https://push.example/web-1"))

        summary = (
            await service.notify_user(user_id, PushPayloadSchema(title="hi"))
        ).as_dict()

        assert "https://push.example/web-1" not in str(summary)
        assert len(summary["pruned"]) == 1
