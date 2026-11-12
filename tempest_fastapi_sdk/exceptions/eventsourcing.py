"""Event store exceptions raised by :mod:`tempest_fastapi_sdk.eventsourcing`."""

from __future__ import annotations

from typing import Any, ClassVar

from tempest_fastapi_sdk.exceptions.conflict import ConflictException


class WrongExpectedVersionException(ConflictException):
    """Raised when an append's ``expected_version`` does not match the stream.

    This is the optimistic-concurrency failure of an event store: someone
    else appended to the stream between the read that produced
    ``expected_version`` and this append. Both backends raise it — the SQL
    store from its conditional ``UPDATE`` of the stream row, the KurrentDB
    store by translating the client's ``WrongCurrentVersionError`` — so a
    service handles one exception whatever the backend.

    The usual reaction is to reload the aggregate and decide again; retrying
    the same append blindly would discard the other writer's decision.

    Attributes:
        stream_id (str): The stream the append targeted.
        expected_version (int | str): What the caller expected — a version
            number, or a :class:`~tempest_fastapi_sdk.eventsourcing.StreamStateEnum`
            value (``"no_stream"``, ``"exists"``).
        actual_version (int | None): The stream's last version when the
            append was refused, or ``None`` when the stream does not exist.
    """

    status_code: int = 409
    message: str = "The stream was changed by another writer"
    code: str = "EVENTSOURCING_WRONG_EXPECTED_VERSION"
    details_example: ClassVar[dict[str, Any]] = {
        "stream_id": "order-42",
        "expected_version": 3,
        "actual_version": 4,
    }

    def __init__(
        self,
        stream_id: str,
        *,
        expected_version: int | str,
        actual_version: int | None,
    ) -> None:
        """Initialize the exception.

        Args:
            stream_id (str): The stream the append targeted.
            expected_version (int | str): The expected version or state.
            actual_version (int | None): The stream's last version, or
                ``None`` when the stream does not exist.
        """
        self.stream_id: str = stream_id
        self.expected_version: int | str = expected_version
        self.actual_version: int | None = actual_version
        super().__init__(
            message=(
                f"Stream {stream_id!r} expected version {expected_version!r}, "
                f"actual {actual_version!r}"
            ),
            details={
                "stream_id": stream_id,
                "expected_version": expected_version,
                "actual_version": actual_version,
            },
        )


__all__: list[str] = [
    "WrongExpectedVersionException",
]
