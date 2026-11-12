"""Event types, the registry and the metadata model."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from typing import ClassVar
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tempest_fastapi_sdk.eventsourcing import (
    DomainEvent,
    EventMetadata,
    EventRegistry,
    RecordedEvent,
    UnknownEventTypeError,
)
from tests.eventsourcing.support import OrderPlaced, OrderShipped


class Unnamed(DomainEvent):
    """An event without ``event_type``."""

    value: int


class TestTypeName:
    """How an event class is named in storage."""

    def test_declared_name_wins(self) -> None:
        """``event_type`` is the stored name."""
        assert OrderPlaced.type_name() == "order.placed"

    def test_class_name_is_the_fallback(self) -> None:
        """Without ``event_type`` the class name is used."""
        assert Unnamed.type_name() == "Unnamed"


class TestRegistry:
    """Registration, encoding and decoding."""

    def test_round_trip(self) -> None:
        """An event encodes to JSON data and decodes to the same class."""
        registry = EventRegistry(OrderPlaced)
        name, data = registry.encode(OrderPlaced(order_id="1", total_cents=5))
        assert (name, data) == ("order.placed", {"order_id": "1", "total_cents": 5})
        decoded = registry.decode(name, data)
        assert type(decoded) is OrderPlaced
        assert decoded == OrderPlaced(order_id="1", total_cents=5)

    def test_decorator_and_introspection(self) -> None:
        """``register`` works as a decorator and the registry is iterable."""
        registry = EventRegistry()

        @registry.register
        class Pinged(DomainEvent):
            n: int

        assert "Pinged" in registry
        assert registry.types == frozenset({"Pinged"})
        assert list(registry) == [Pinged]
        assert len(registry) == 1
        registry.register(Pinged)
        assert len(registry) == 1

    def test_name_collision_is_refused(self) -> None:
        """Two classes under one name would decode to the wrong class."""

        class Other(DomainEvent):
            event_type: ClassVar[str] = "order.placed"

        registry = EventRegistry(OrderPlaced)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(Other)

    def test_non_event_is_refused(self) -> None:
        """Only ``DomainEvent`` subclasses register."""
        with pytest.raises(TypeError):
            EventRegistry().register(dict)  # type: ignore[type-var]

    def test_unregistered_encode_and_decode_raise(self) -> None:
        """Both directions refuse a type the registry does not know."""
        registry = EventRegistry(OrderPlaced)
        with pytest.raises(UnknownEventTypeError):
            registry.encode(OrderShipped(order_id="1"))
        with pytest.raises(UnknownEventTypeError) as caught:
            registry.decode("order.shipped", {"order_id": "1"})
        assert caught.value.event_type == "order.shipped"

    def test_decode_ignores_unknown_fields_and_validates(self) -> None:
        """Extra stored fields are dropped; a missing field fails validation."""
        registry = EventRegistry(OrderPlaced)
        event = registry.decode(
            "order.placed", {"order_id": "1", "total_cents": 2, "legacy": True}
        )
        assert event == OrderPlaced(order_id="1", total_cents=2)
        with pytest.raises(ValidationError):
            registry.decode("order.placed", {"order_id": "1"})


def test_events_are_frozen_and_keep_whitespace() -> None:
    """An event is immutable and stores strings exactly as given."""
    event = OrderPlaced(order_id="  padded ", total_cents=1)
    assert event.order_id == "  padded "
    with pytest.raises(ValidationError):
        event.total_cents = 2  # type: ignore[misc]


def test_recorded_event_payload() -> None:
    """``to_payload`` is JSON-compatible and carries every field."""
    event_id = uuid4()
    at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    record = RecordedEvent(
        event_id=event_id,
        stream_id="order-1",
        version=3,
        position=10,
        event_type="order.placed",
        event=OrderPlaced(order_id="1", total_cents=9),
        metadata=EventMetadata(correlation_id="c", extra={"k": 1}),
        recorded_at=at,
    )
    assert record.to_payload() == {
        "event_id": str(event_id),
        "stream_id": "order-1",
        "version": 3,
        "position": 10,
        "event_type": "order.placed",
        "data": {"order_id": "1", "total_cents": 9},
        "metadata": {"correlation_id": "c", "causation_id": None, "extra": {"k": 1}},
        "recorded_at": "2026-01-02T03:04:05+00:00",
    }


def test_package_imports_without_the_kurrentdb_extra() -> None:
    """``eventsourcing`` imports, and the SQL store works, without the client."""
    code = (
        "import sys; sys.modules['kurrentdbclient'] = None\n"
        "import tempest_fastapi_sdk.eventsourcing as es\n"
        "assert es.KurrentDBEventStore is not None\n"
        "try:\n"
        "    registry = es.EventRegistry()\n"
        "    es.KurrentDBEventStore.from_uri('kurrentdb://x', registry=registry)\n"
        "except ImportError:\n"
        "    print('needs-extra')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "needs-extra"
