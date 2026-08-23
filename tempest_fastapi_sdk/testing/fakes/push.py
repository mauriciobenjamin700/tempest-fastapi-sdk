"""Push delivery that reaches nobody's phone.

The failure mode this replaces is not a slow test: it is a notification
arriving on a real device during development. And the branch worth testing —
a token that FCM has retired — is unreachable on purpose against the real
service, so here it is one call.
"""

from __future__ import annotations

from dataclasses import dataclass

from tempest_fastapi_sdk.push.schemas import (
    PushDevice,
    PushPayloadSchema,
    PushPlatform,
)
from tempest_fastapi_sdk.testing.fakes._control import _Steerable


@dataclass(frozen=True, slots=True)
class SentPush:
    """One delivery this dispatcher accepted.

    Attributes:
        device (PushDevice): Where it would have gone.
        payload (PushPayloadSchema): What it would have carried.
    """

    device: PushDevice
    payload: PushPayloadSchema


class FakePushDispatcher(_Steerable):
    """A ``PushDispatcher`` that appends to a list.

    Example:

        >>> from tempest_fastapi_sdk.push import PushPlatform
        >>> dispatcher = FakePushDispatcher(platforms={PushPlatform.WEB.value})
        >>> dispatcher.platforms
        frozenset({'web'})

    Attributes:
        platforms (frozenset[str]): Which platform values this dispatcher
            claims, matching the real dispatchers' attribute.
        sent (list[SentPush]): Deliveries accepted, in order.
        calls (list[str]): Methods that ran, in order.
    """

    def __init__(self, *, platforms: set[str] | None = None) -> None:
        """Start with nothing sent.

        Args:
            platforms (set[str] | None): Platform values to claim. Defaults
                to every platform the SDK models, so a fan-out over a mixed
                fleet is exercised without registering three fakes.
        """
        super().__init__()
        claimed = platforms or {platform.value for platform in PushPlatform}
        self.platforms: frozenset[str] = frozenset(claimed)
        self.sent: list[SentPush] = []

    async def send(self, device: PushDevice, payload: PushPayloadSchema) -> None:
        """Record a delivery instead of performing one.

        Args:
            device (PushDevice): The target device.
            payload (PushPayloadSchema): The notification.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued — queue
                :class:`~tempest_fastapi_sdk.push.PushDeviceGoneError` to
                exercise the path that prunes a dead token.
        """
        self._record("send")
        self.sent.append(SentPush(device=device, payload=payload))

    def sent_to(self, token: str) -> list[SentPush]:
        """Every delivery aimed at one device token.

        Args:
            token (str): The device token to filter by.

        Returns:
            list[SentPush]: Matching deliveries, in order. Empty when the
            token was never targeted — which is a passing assertion about
            nothing being sent, not an error.
        """
        return [item for item in self.sent if item.device.token == token]
