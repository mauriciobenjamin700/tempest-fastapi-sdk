"""Register devices, fan a notification out to them, prune the dead ones.

This is where web and mobile stop being two APIs. The caller says "notify
this user"; the service reads the user's devices, routes each one to the
transport that claims its platform, and deletes exactly the devices the
provider disowned — HTTP 404/410 on Web Push, ``UnregisteredError`` on
FCM. One rule, two vocabularies.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Generic, TypeVar
from uuid import UUID

from tempest_fastapi_sdk.db.device_token_model import BaseDeviceTokenModel
from tempest_fastapi_sdk.db.repository import BaseRepository
from tempest_fastapi_sdk.push.dispatcher import (
    PushDeviceGoneError,
    PushDispatcher,
    PushError,
)
from tempest_fastapi_sdk.push.schemas import (
    DeviceRegistrationSchema,
    PushDevice,
    PushFanoutResult,
    PushPayloadSchema,
    PushPlatform,
    PushResult,
    mask_push_token,
)
from tempest_fastapi_sdk.utils.datetime import utcnow

logger = logging.getLogger(__name__)

DeviceModelT = TypeVar("DeviceModelT", bound=BaseDeviceTokenModel)


class DeviceService(Generic[DeviceModelT]):
    """Store devices and deliver notifications to a user's whole fleet.

    Generic over the concrete device model so it returns the project's own
    rows. Pair a :class:`BaseRepository` bound to that model with the
    transports the service actually wired: web only, mobile only, or both.

    Generic parameters:
        DeviceModelT: The concrete :class:`BaseDeviceTokenModel` subclass.

    Attributes:
        repository (BaseRepository[DeviceModelT]): Data access for the
            device table.
        transports (tuple[PushDispatcher, ...]): The configured transports,
            in the order they were given.
    """

    def __init__(
        self,
        repository: BaseRepository[DeviceModelT],
        transports: Iterable[PushDispatcher],
    ) -> None:
        """Initialize the service.

        Args:
            repository (BaseRepository[DeviceModelT]): Repository bound to
                the concrete device model.
            transports (Iterable[PushDispatcher]): Transports to route by
                platform. When two transports claim the same platform, the
                first one wins.
        """
        self.repository: BaseRepository[DeviceModelT] = repository
        self.transports: tuple[PushDispatcher, ...] = tuple(transports)
        self._by_platform: dict[str, PushDispatcher] = {}
        for transport in self.transports:
            for platform in transport.platforms:
                self._by_platform.setdefault(platform, transport)

    def transport_for(self, platform: str) -> PushDispatcher | None:
        """Return the transport that delivers to ``platform``.

        Args:
            platform (str): A :class:`PushPlatform` value.

        Returns:
            PushDispatcher | None: The transport, or ``None`` when the
            service wired none for that platform.
        """
        return self._by_platform.get(platform)

    async def register(
        self,
        user_id: UUID,
        registration: DeviceRegistrationSchema,
    ) -> DeviceModelT:
        """Persist a device, idempotently keyed by ``token``.

        A device that re-registers — or one that moves to another user
        after a sign-out / sign-in on the same handset — updates the
        existing row instead of creating a duplicate, and refreshes
        ``last_seen_at``. That reassignment is the reason the update path
        writes ``user_id`` too: leaving it would deliver the next
        notification to the previous account.

        Args:
            user_id (UUID): The user that owns the device.
            registration (DeviceRegistrationSchema): What the client sent.

        Returns:
            DeviceModelT: The persisted (created or updated) row.
        """
        platform = (
            registration.platform.value
            if isinstance(registration.platform, PushPlatform)
            else str(registration.platform)
        )
        existing = await self.repository.get_or_none({"token": registration.token})
        if existing is not None:
            existing.user_id = user_id
            existing.platform = platform
            existing.p256dh = registration.p256dh
            existing.auth = registration.auth
            existing.expiration_time = registration.expiration_time
            existing.app_version = registration.app_version
            existing.last_seen_at = utcnow()
            return await self.repository.update(existing)

        row = self.repository.model(
            user_id=user_id,
            token=registration.token,
            platform=platform,
            p256dh=registration.p256dh,
            auth=registration.auth,
            expiration_time=registration.expiration_time,
            app_version=registration.app_version,
            last_seen_at=utcnow(),
        )
        return await self.repository.add(row)

    async def unregister(self, token: str) -> bool:
        """Remove the device with ``token``, if present.

        Idempotent: unregistering an unknown token is a no-op.

        Args:
            token (str): The device token / endpoint to drop.

        Returns:
            bool: ``True`` when a row was deleted.
        """
        existing = await self.repository.get_or_none({"token": token})
        if existing is None:
            return False
        await self.repository.delete(existing.id)
        return True

    async def list_for_user(self, user_id: UUID) -> list[DeviceModelT]:
        """Return every stored device for a user.

        Args:
            user_id (UUID): The user whose devices to list.

        Returns:
            list[DeviceModelT]: The user's devices (``[]`` when none).
        """
        return await self.repository.list(filters={"user_id": user_id})

    async def notify_user(
        self,
        user_id: UUID,
        payload: PushPayloadSchema,
        *,
        exclude_tokens: Iterable[str] = (),
        platforms: Iterable[PushPlatform | str] = (),
    ) -> PushFanoutResult:
        """Deliver ``payload`` to every device the user registered.

        Deliveries run concurrently and **independently**: one device
        failing never stops the others, and the returned result says which
        devices took the notification, which were deleted, and which merely
        failed. Devices the provider disowned are removed from the store
        before returning, so dead rows never accumulate.

        Args:
            user_id (UUID): The recipient.
            payload (PushPayloadSchema): The notification to deliver.
            exclude_tokens (Iterable[str]): Devices to skip entirely —
                typically the device that caused the event, which must not
                notify itself. Excluded devices are never contacted and
                never pruned.
            platforms (Iterable[PushPlatform | str]): When non-empty,
                restrict the fan-out to these platforms (a web-only
                announcement, for instance). Empty means every platform.

        Returns:
            PushFanoutResult: One entry per targeted device, plus the
            devices no configured transport could reach.
        """
        rows = await self.list_for_user(user_id)
        excluded = set(exclude_tokens)
        wanted = {
            platform.value if isinstance(platform, PushPlatform) else str(platform)
            for platform in platforms
        }
        targets = [
            row
            for row in rows
            if row.token not in excluded and (not wanted or row.platform in wanted)
        ]
        if not targets:
            return PushFanoutResult()

        deliverable: list[DeviceModelT] = []
        skipped: list[str] = []
        for row in targets:
            if self.transport_for(row.platform) is None:
                skipped.append(f"{row.platform}/{mask_push_token(row.token)}")
                continue
            deliverable.append(row)

        results = await asyncio.gather(
            *(self._deliver(row, payload) for row in deliverable)
        )
        gone = [
            row.token
            for row, result in zip(deliverable, results, strict=True)
            if result.pruned
        ]
        for token in gone:
            await self.unregister(token)
        return PushFanoutResult(results=tuple(results), skipped=tuple(skipped))

    async def _deliver(
        self, row: DeviceModelT, payload: PushPayloadSchema
    ) -> PushResult:
        """Deliver to one device and classify the outcome.

        Args:
            row (DeviceModelT): The stored device.
            payload (PushPayloadSchema): The notification to deliver.

        Returns:
            PushResult: Delivered, pruned, or failed — never an exception,
            because one device must not abort the fan-out.
        """
        device = self.to_device(row)
        transport = self._by_platform[row.platform]
        try:
            await transport.send(device, payload)
        except PushDeviceGoneError:
            logger.info("Pruning push device %s", device.masked_token)
            return PushResult(
                platform=device.platform,
                masked_token=device.masked_token,
                delivered=False,
                pruned=True,
            )
        except PushError as error:
            logger.warning(
                "Push delivery failed for %s: %s", device.masked_token, error
            )
            return PushResult(
                platform=device.platform,
                masked_token=device.masked_token,
                delivered=False,
                error=str(error),
            )
        return PushResult(
            platform=device.platform,
            masked_token=device.masked_token,
            delivered=True,
        )

    @staticmethod
    def to_device(row: BaseDeviceTokenModel) -> PushDevice:
        """Map a stored row onto the transport-facing device value.

        Args:
            row (BaseDeviceTokenModel): The persisted device.

        Returns:
            PushDevice: The same device as the transports see it.
        """
        return PushDevice(
            platform=PushPlatform(row.platform),
            token=row.token,
            p256dh=row.p256dh,
            auth=row.auth,
            expiration_time=row.expiration_time,
        )


__all__: list[str] = [
    "DeviceService",
]
