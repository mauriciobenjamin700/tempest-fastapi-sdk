"""Kafka semantics through the typed facade, against FastStream's test broker.

What only a real cluster answers — that a key keeps its partition, that a
group splits the partitions and another group gets its own copy — is in
``test_kafka_live.py``. Here: what the facade hands FastStream, what it
refuses on each transport, and that the decorator and the class path
accept and refuse the same things.
"""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from faststream import AckPolicy
from faststream.kafka import KafkaBroker, TestKafkaBroker
from faststream.kafka.annotations import KafkaMessage
from faststream.rabbit import RabbitBroker
from faststream.redis import RedisBroker
from pydantic import BaseModel

from tempest_fastapi_sdk.core.context import clear_request_id, set_request_id
from tempest_fastapi_sdk.queue import (
    Consumer,
    MemoryDedupStore,
    MessageBroker,
    OffsetResetEnum,
    Publisher,
    QueueSpec,
    UnsupportedTopologyError,
    subscribe,
)
from tempest_fastapi_sdk.queue import broker as broker_module
from tempest_fastapi_sdk.queue.dedup import MESSAGE_ID_HEADER, message_key

ROOT: Path = Path(__file__).resolve().parents[2]


class OrderPaid(BaseModel):
    order_id: str


def _kafka() -> KafkaBroker:
    return KafkaBroker("localhost:9092")


def _subscriber_kwargs(mq: MessageBroker) -> Any:
    """Patch ``mq.broker.subscriber`` to record the keywords it gets.

    Args:
        mq (MessageBroker): The facade under test.

    Returns:
        Any: The patcher; its mock's ``call_args`` holds the keywords.
    """
    return patch.object(mq.broker, "subscriber", wraps=mq.broker.subscriber)


class TestTransportExtras:
    """Every constructor names an extra that installs its client.

    The defect: ``kafka()`` / ``redis()`` / ``nats()`` told the user to
    install ``[queue]``, which pulls ``faststream[rabbit]`` only, so
    following the error message left the same error in place.
    """

    def _named_extras(self) -> dict[str, str]:
        recorded: dict[str, str] = {}
        real = broker_module._require

        def spy(module: str, extra: str) -> Any:
            recorded[module] = extra
            return real(module, extra)

        with patch.object(broker_module, "_require", spy):
            MessageBroker.rabbitmq("amqp://guest:guest@localhost:5672/")
            MessageBroker.redis("redis://localhost:6379/0")
            MessageBroker.kafka("localhost:9092")
            MessageBroker.nats("nats://localhost:4222")
        return recorded

    def test_each_named_extra_installs_the_transport(self) -> None:
        extras = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
            "optional-dependencies"
        ]
        named = self._named_extras()
        assert set(named) == {
            "faststream.rabbit",
            "faststream.redis",
            "faststream.kafka",
            "faststream.nats",
        }
        for module, extra in named.items():
            transport = module.split(".")[1]
            requirements = extras[extra]
            assert any(
                req.startswith("faststream[") and transport in req.split("]")[0]
                for req in requirements
            ), f"[{extra}] does not install faststream[{transport}]"

    def test_the_old_mapping_would_fail(self) -> None:
        """``[queue]`` really lacks the Kafka client — the check has teeth."""
        extras = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
            "optional-dependencies"
        ]
        assert not any("kafka" in req for req in extras["queue"])

    def test_the_all_extra_installs_every_transport(self) -> None:
        extras = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
            "optional-dependencies"
        ]
        faststream = [req for req in extras["all"] if req.startswith("faststream[")]
        assert len(faststream) == 1
        for transport in ("rabbit", "kafka", "nats", "redis"):
            assert transport in faststream[0]

    def test_a_missing_backend_names_its_extra(self) -> None:
        with (
            patch("importlib.import_module", side_effect=ImportError("aiokafka")),
            pytest.raises(ImportError, match=r"tempest-fastapi-sdk\[queue-kafka\]"),
        ):
            MessageBroker.kafka("localhost:9092")


class TestConsumerOptions:
    def test_group_and_offset_reset_reach_faststream(self) -> None:
        mq = MessageBroker(_kafka())
        with _subscriber_kwargs(mq) as spy:

            @mq.on(
                "orders.paid",
                group_id="billing",
                auto_offset_reset=OffsetResetEnum.EARLIEST,
            )
            async def handle(event: OrderPaid) -> None: ...

        assert spy.call_args.args == ("orders.paid",)
        assert spy.call_args.kwargs == {
            "group_id": "billing",
            "auto_offset_reset": "earliest",
        }

    def test_the_plain_string_still_works(self) -> None:
        """Before the named keyword it rode ``**options`` as a ``str``."""
        mq = MessageBroker(_kafka())
        with _subscriber_kwargs(mq) as spy:

            @mq.on("orders.paid", group_id="g", auto_offset_reset="earliest")
            async def handle(event: OrderPaid) -> None: ...

        assert spy.call_args.kwargs["auto_offset_reset"] == "earliest"

    def test_an_unknown_offset_reset_raises(self) -> None:
        mq = MessageBroker(_kafka())
        with pytest.raises(ValueError, match="oldest"):
            mq.on("orders.paid", group_id="g", auto_offset_reset="oldest")

    def test_nothing_is_added_when_unset(self) -> None:
        mq = MessageBroker(_kafka())
        with _subscriber_kwargs(mq) as spy:

            @mq.on("orders.paid")
            async def handle(event: OrderPaid) -> None: ...

        assert spy.call_args.kwargs == {}

    def test_a_committing_policy_needs_a_group(self) -> None:
        """aiokafka refuses the commit with ``Requires group_id`` per message."""
        mq = MessageBroker(_kafka())
        with pytest.raises(ValueError, match="group_id"):

            @mq.on("orders.paid", ack_policy=AckPolicy.ACK)
            async def handle(event: OrderPaid) -> None: ...

    def test_a_broker_wide_committing_policy_needs_a_group(self) -> None:
        mq = MessageBroker(KafkaBroker("localhost:9092", ack_policy=AckPolicy.ACK))
        with pytest.raises(ValueError, match="ack_policy=ACK"):

            @mq.on("orders.paid")
            async def handle(event: OrderPaid) -> None: ...

    @pytest.mark.parametrize(
        "policy",
        [AckPolicy.ACK, AckPolicy.NACK_ON_ERROR, AckPolicy.MANUAL],
    )
    def test_a_committing_policy_with_a_group_is_accepted(
        self,
        policy: AckPolicy,
    ) -> None:
        mq = MessageBroker(_kafka())
        with _subscriber_kwargs(mq) as spy:

            @mq.on("orders.paid", ack_policy=policy, group_id="billing")
            async def handle(event: OrderPaid) -> None: ...

        assert spy.call_args.kwargs["ack_policy"] is policy

    def test_ack_first_needs_no_group(self) -> None:
        mq = MessageBroker(_kafka())

        @mq.on("orders.paid", ack_policy=AckPolicy.ACK_FIRST)
        async def handle(event: OrderPaid) -> None: ...

    def test_prefetch_is_refused_on_kafka(self) -> None:
        """It used to reach FastStream as a RabbitMQ ``Channel``."""
        mq = MessageBroker(_kafka())
        with pytest.raises(UnsupportedTopologyError, match="prefetch"):

            @mq.on("orders.paid", prefetch=10)
            async def handle(event: OrderPaid) -> None: ...

    @pytest.mark.parametrize(
        "broker",
        [
            RabbitBroker("amqp://guest:guest@localhost:5672/"),
            RedisBroker("redis://localhost:6379/0"),
        ],
    )
    @pytest.mark.parametrize(
        ("knob", "value"),
        [("group_id", "billing"), ("auto_offset_reset", OffsetResetEnum.EARLIEST)],
    )
    def test_kafka_knobs_are_refused_elsewhere(
        self,
        broker: Any,
        knob: str,
        value: Any,
    ) -> None:
        mq = MessageBroker(broker)
        with pytest.raises(UnsupportedTopologyError, match=knob):
            mq.on("orders.paid", **{knob: value})

    def test_a_spec_with_amqp_fields_still_raises(self) -> None:
        mq = MessageBroker(_kafka())
        with pytest.raises(UnsupportedTopologyError, match="max_priority"):
            mq.on(QueueSpec(name="orders.paid", max_priority=5), group_id="g")


class TestClassPathParity:
    def test_constructor_form_passes_the_group(self) -> None:
        mq = MessageBroker(_kafka())

        class Billing(Consumer):
            async def handle(self, event: OrderPaid) -> None: ...

        with _subscriber_kwargs(mq) as spy:
            mq.register(
                Billing(
                    channel="orders.paid",
                    schema=OrderPaid,
                    group_id="billing",
                    auto_offset_reset=OffsetResetEnum.EARLIEST,
                    ack_policy=AckPolicy.ACK,
                ),
            )

        assert spy.call_args.kwargs == {
            "group_id": "billing",
            "auto_offset_reset": "earliest",
            "ack_policy": AckPolicy.ACK,
        }

    def test_grouped_form_inherits_and_overrides(self) -> None:
        mq = MessageBroker(_kafka())

        class Orders(Consumer):
            group_id = "orders"

            @subscribe("orders.paid")
            async def on_paid(self, event: OrderPaid) -> None: ...

            @subscribe(
                "orders.refunded",
                group_id="refunds",
                auto_offset_reset=OffsetResetEnum.EARLIEST,
            )
            async def on_refunded(self, event: OrderPaid) -> None: ...

        with _subscriber_kwargs(mq) as spy:
            mq.register(Orders())

        by_topic = {c.args[0]: c.kwargs for c in spy.call_args_list}
        assert by_topic == {
            "orders.paid": {"group_id": "orders"},
            "orders.refunded": {
                "group_id": "refunds",
                "auto_offset_reset": "earliest",
            },
        }

    def test_register_refuses_what_on_refuses(self) -> None:
        mq = MessageBroker(_kafka())

        class Billing(Consumer):
            async def handle(self, event: OrderPaid) -> None: ...

        with pytest.raises(ValueError, match="group_id"):
            mq.register(Billing(channel="orders.paid", ack_policy=AckPolicy.ACK))
        rabbit = MessageBroker(RabbitBroker("amqp://guest:guest@localhost:5672/"))
        with pytest.raises(UnsupportedTopologyError, match="group_id"):
            rabbit.register(Billing(channel="orders.paid", group_id="billing"))


class TestPublish:
    async def test_key_message_id_and_trace_headers_arrive(self) -> None:
        broker = _kafka()
        mq = MessageBroker(broker)
        seen: list[tuple[Any, dict[str, Any]]] = []
        done = asyncio.Event()

        @mq.on("orders.paid", group_id="billing")
        async def handle(event: OrderPaid, message: KafkaMessage) -> None:
            seen.append((message.raw_message.key, dict(message.headers)))
            done.set()

        token = set_request_id("req-1")
        async with TestKafkaBroker(broker):
            await mq.connect()
            await mq.publish(
                "orders.paid",
                OrderPaid(order_id="abc"),
                key="order-abc",
                message_id="evt-1",
                headers={"tenant": "t1"},
            )
            await asyncio.wait_for(done.wait(), timeout=1.0)
            await mq.disconnect()
        clear_request_id(token)

        [(key, headers)] = seen
        assert key == b"order-abc"
        assert headers[MESSAGE_ID_HEADER] == "evt-1"
        assert headers["tenant"] == "t1"
        assert headers["x-request-id"] == "req-1"

    async def test_a_message_id_is_generated_when_absent(self) -> None:
        broker = _kafka()
        mq = MessageBroker(broker)
        seen: list[str] = []
        done = asyncio.Event()

        @mq.on("orders.paid")
        async def handle(event: OrderPaid, message: KafkaMessage) -> None:
            seen.append(message.headers[MESSAGE_ID_HEADER])
            done.set()

        async with TestKafkaBroker(broker):
            await mq.connect()
            await mq.publish("orders.paid", OrderPaid(order_id="abc"))
            await asyncio.wait_for(done.wait(), timeout=1.0)
            await mq.disconnect()

        assert len(seen[0]) == 36

    @pytest.mark.parametrize(
        ("message_id", "expected"),
        [("evt-explicit", "evt-explicit"), (None, "evt-header")],
    )
    async def test_explicit_message_id_beats_the_header(
        self,
        message_id: str | None,
        expected: str,
    ) -> None:
        broker = _kafka()
        mq = MessageBroker(broker)
        seen: list[str] = []
        done = asyncio.Event()

        @mq.on("orders.paid")
        async def handle(event: OrderPaid, message: KafkaMessage) -> None:
            seen.append(message.headers[MESSAGE_ID_HEADER])
            done.set()

        async with TestKafkaBroker(broker):
            await mq.connect()
            await mq.publish(
                "orders.paid",
                OrderPaid(order_id="abc"),
                message_id=message_id,
                headers={MESSAGE_ID_HEADER: "evt-header"},
            )
            await asyncio.wait_for(done.wait(), timeout=1.0)
            await mq.disconnect()

        assert seen == [expected]

    async def test_bytes_key_is_kept(self) -> None:
        broker = _kafka()
        mq = MessageBroker(broker)
        keys: list[Any] = []
        done = asyncio.Event()

        @mq.on("orders.paid")
        async def handle(event: dict[str, int], message: KafkaMessage) -> None:
            keys.append(message.raw_message.key)
            done.set()

        async with TestKafkaBroker(broker):
            await mq.connect()
            await mq.publish("orders.paid", {"a": 1}, key=b"\x00raw")
            await asyncio.wait_for(done.wait(), timeout=1.0)
            await mq.disconnect()

        assert keys == [b"\x00raw"]

    async def test_publisher_class_passes_the_key(self) -> None:
        broker = _kafka()
        mq = MessageBroker(broker)
        keys: list[Any] = []
        done = asyncio.Event()

        class OrderPaidPublisher(Publisher[OrderPaid]):
            channel = "orders.paid"
            schema = OrderPaid

        @mq.on("orders.paid")
        async def handle(event: OrderPaid, message: KafkaMessage) -> None:
            keys.append(message.raw_message.key)
            done.set()

        orders = mq.publisher_for(OrderPaidPublisher)
        async with TestKafkaBroker(broker):
            await mq.connect()
            await orders.publish(OrderPaid(order_id="abc"), key="order-abc")
            await asyncio.wait_for(done.wait(), timeout=1.0)
            await mq.disconnect()

        assert keys == [b"order-abc"]

    async def test_key_is_refused_off_kafka(self) -> None:
        mq = MessageBroker(RabbitBroker("amqp://guest:guest@localhost:5672/"))
        mq._started = True
        with pytest.raises(UnsupportedTopologyError, match="key"):
            await mq.publish("orders.paid", {"a": 1}, key="k")

    async def test_a_republished_message_id_runs_once(self) -> None:
        """Dedup works on Kafka: the id rides in the header, not the offset."""
        broker = _kafka()
        mq = MessageBroker(broker)
        mq.deduplicate(MemoryDedupStore())
        calls: list[str] = []
        done = asyncio.Event()

        @mq.on("orders.paid")
        async def handle(event: OrderPaid) -> None:
            calls.append(event.order_id)
            if len(calls) == 2:
                done.set()

        async with TestKafkaBroker(broker):
            await mq.connect()
            await mq.publish("orders.paid", OrderPaid(order_id="a"), message_id="x")
            await mq.publish("orders.paid", OrderPaid(order_id="a"), message_id="x")
            await mq.publish("orders.paid", OrderPaid(order_id="b"), message_id="y")
            await asyncio.wait_for(done.wait(), timeout=1.0)
            await mq.disconnect()

        assert calls == ["a", "b"]


class _Record:
    """Stand-in for an ``aiokafka`` ``ConsumerRecord``."""

    def __init__(self, topic: str, partition: int, offset: int) -> None:
        self.topic = topic
        self.partition = partition
        self.offset = offset


class _KafkaMessage:
    """Stand-in for a FastStream Kafka message."""

    def __init__(
        self,
        record: _Record,
        *,
        headers: dict[str, str] | None = None,
        message_id: str = "0-1700000000000",
    ) -> None:
        self.raw_message = record
        self.headers = headers or {}
        self.message_id = message_id
        self.correlation_id = None


class TestMessageKey:
    def test_the_header_wins(self) -> None:
        message = _KafkaMessage(
            _Record("orders", 0, 5),
            headers={MESSAGE_ID_HEADER: "evt-1"},
        )
        assert message_key(message) == "evt-1"

    def test_foreign_records_are_keyed_by_coordinates(self) -> None:
        """FastStream's ``<offset>-<timestamp>`` collides across partitions."""
        first = _KafkaMessage(_Record("orders", 0, 0))
        second = _KafkaMessage(_Record("orders", 1, 0))
        assert first.message_id == second.message_id
        assert message_key(first) == "orders:0:0"
        assert message_key(second) == "orders:1:0"

    def test_a_non_kafka_raw_message_falls_through(self) -> None:
        message = _KafkaMessage(_Record("orders", 0, 0))
        message.raw_message = object()  # type: ignore[assignment]
        assert message_key(message) == "0-1700000000000"
