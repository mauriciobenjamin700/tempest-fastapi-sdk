"""Abstract tables of the SQL event store.

The project subclasses each one and picks ``__tablename__``, exactly like
:class:`~tempest_fastapi_sdk.db.outbox.BaseOutboxModel`, so the tables live
in the project's own metadata and Alembic migrations.

* :class:`BaseEventModel` — the append-only event log.
* :class:`BaseEventStreamModel` — one row per stream holding its last
  version; the conditional ``UPDATE`` of this row is the concurrency check.
* :class:`BaseEventSnapshotModel` — optional, latest snapshot per stream.
* :class:`BaseProjectionCheckpointModel` — optional, last position each
  projection processed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from tempest_fastapi_sdk.db.datetime_type import UtcDateTime
from tempest_fastapi_sdk.db.model import BaseModel
from tempest_fastapi_sdk.utils.datetime import utcnow


class BaseEventModel(BaseModel):
    """Abstract event log — one immutable row per event.

    ``id`` (from :class:`~tempest_fastapi_sdk.db.model.BaseModel`) is the
    event id. ``(stream_id, version)`` and ``position`` are unique, so even
    a writer that bypassed the store could not record two events in the
    same slot.

    Attributes:
        position (int): Store-wide position, ``1, 2, 3, ...`` in commit
            order.
        stream_id (str): The stream.
        version (int): 0-based version within the stream.
        event_type (str): Registered type name.
        data (dict[str, Any]): The event payload.
        correlation_id (str | None): Correlation id from the metadata.
        causation_id (str | None): Causation id from the metadata.
        extra (dict[str, Any]): Remaining metadata.
        recorded_at (datetime): When the store recorded the event.
    """

    __abstract__ = True

    position: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        unique=True,
        doc="Store-wide position, assigned in commit order.",
    )
    stream_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="The stream the event belongs to.",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="0-based version within the stream.",
    )
    event_type: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        doc="Registered event type name.",
    )
    data: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        doc="The event payload.",
    )
    correlation_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        doc="Correlation id from the event metadata.",
    )
    causation_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Causation id from the event metadata.",
    )
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        doc="Remaining event metadata.",
    )
    recorded_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        nullable=False,
        default=utcnow,
        doc="When the store recorded the event.",
    )

    @declared_attr.directive
    def __table_args__(cls) -> tuple[UniqueConstraint]:  # noqa: N805
        """Make ``(stream_id, version)`` unique on the concrete table.

        Returns:
            tuple[UniqueConstraint]: The constraint.
        """
        return (UniqueConstraint("stream_id", "version"),)


class BaseEventStreamModel(BaseModel):
    """Abstract stream table — one row per stream with its last version.

    An append moves ``version`` with ``UPDATE ... WHERE version = :expected``
    before inserting events, so of two concurrent appends with the same
    expectation exactly one changes the row.

    Attributes:
        stream_id (str): The stream, unique.
        version (int): The stream's last version.
    """

    __abstract__ = True

    stream_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        doc="The stream id.",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="The stream's last 0-based version.",
    )


class BaseEventSnapshotModel(BaseModel):
    """Abstract snapshot table — the latest snapshot per stream.

    Attributes:
        stream_id (str): The stream, unique.
        version (int): Last version folded into ``state``.
        state (dict[str, Any]): The folded state.
    """

    __abstract__ = True

    stream_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        doc="The stream id.",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Last version folded into the state.",
    )
    state: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        doc="The folded aggregate state.",
    )


class BaseProjectionCheckpointModel(BaseModel):
    """Abstract checkpoint table — last position each projection processed.

    Attributes:
        name (str): The projection name, unique.
        position (int): Last ``$all`` position the projection processed.
    """

    __abstract__ = True

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        doc="The projection name.",
    )
    position: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        doc="Last $all position processed.",
    )


__all__: list[str] = [
    "BaseEventModel",
    "BaseEventSnapshotModel",
    "BaseEventStreamModel",
    "BaseProjectionCheckpointModel",
]
