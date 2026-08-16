"""The transport contract every push backend implements.

:class:`PushDispatcher` is a :class:`~typing.Protocol`, in the same shape
:class:`~tempest_fastapi_sdk.utils.storage_backends.UploadStorage` uses
for storage: the service depends on the contract, never on a concrete
backend, so a project can wire Web Push only, FCM only, both, or a fake
in tests without any of them knowing about the others.

The contract is deliberately tiny — one method, one device, one payload —
because the interesting behaviour (fan-out, pruning, partial failure) is
the *service*'s job, and duplicating it per transport is how the two
halves drift apart.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tempest_fastapi_sdk.push.schemas import PushDevice, PushPayloadSchema


class PushError(RuntimeError):
    """Raised when a delivery attempt fails.

    Attributes:
        masked_token (str | None): The device the attempt targeted,
            identified safely for logs. Never the raw token.
        status_code (int | None): Provider status, when the transport
            exposes one.
    """

    def __init__(
        self,
        message: str,
        *,
        masked_token: str | None = None,
        status_code: int | None = None,
    ) -> None:
        """Initialize the error.

        Args:
            message (str): Human-readable description. Must not contain
                the raw device token.
            masked_token (str | None): Masked device identifier.
            status_code (int | None): Provider status code, when known.
        """
        super().__init__(message)
        self.masked_token: str | None = masked_token
        self.status_code: int | None = status_code


class PushDeviceGoneError(PushError):
    """Raised when the provider says the device no longer exists.

    This is the one failure that changes the store: the caller deletes the
    device instead of retrying it. Both providers signal it, in their own
    vocabulary — HTTP 404/410 on Web Push, ``UnregisteredError`` (and
    ``SenderIdMismatchError``) on FCM — which is exactly the translation
    this package exists to own.
    """


@runtime_checkable
class PushDispatcher(Protocol):
    """Deliver one notification to one device.

    Implementations are expected to be safe to share across requests and
    to do their network work off the event loop.

    Attributes:
        platforms (frozenset[str]): Which
            :class:`~tempest_fastapi_sdk.push.PushPlatform` values this
            transport can deliver to, as their string values.
    """

    platforms: frozenset[str]

    async def send(self, device: PushDevice, payload: PushPayloadSchema) -> None:
        """Deliver ``payload`` to ``device``.

        Args:
            device (PushDevice): The delivery target.
            payload (PushPayloadSchema): The notification to deliver.

        Raises:
            PushDeviceGoneError: When the provider reports the device as
                unknown or expired, so the caller should delete it.
            PushError: For any other delivery failure. The device keeps
                its row and will be retried on the next fan-out.
        """
        ...


__all__: list[str] = [
    "PushDeviceGoneError",
    "PushDispatcher",
    "PushError",
]
