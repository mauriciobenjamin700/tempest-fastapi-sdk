"""Fan-out, per-consumer dead letters and replay against a real RabbitMQ.

The unit tests pin what the facade hands FastStream. What only a broker
answers is whether that is a working topology: that two queues bound to
one key each get a copy, that a rejection reaches the consumer's own
dead-letter queue and not a neighbour's, that the declaration happens
before the first delivery, and that a replay goes back to one consumer
only. Each image runs in a container on ``make test-docker``.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio

from tempest_fastapi_sdk.queue import (
    ExchangeSpec,
    MessageBroker,
    QueueSpec,
    RetryTopology,
    retry_queues,
)

pytestmark = pytest.mark.docker

IMAGE: str = "rabbitmq:4-alpine"
"""The broker under test."""

PORT: int = 56721
"""Host port the container's AMQP listener is published on."""

READY_LINE: str = "Server startup complete"
"""What the broker logs once it accepts connections.

Read from the logs rather than probed with ``docker exec
rabbitmq-diagnostics``: measured on ``rabbitmq:4-alpine``, an exec issued
while the node is still booting takes the container down, and with
``--rm`` it is gone before the next probe.
"""

DEADLINE_SECONDS: float = 10.0
"""Upper bound on waiting for a delivery; bounds the failure, not the pass."""


@pytest.fixture(scope="module")
def amqp_url() -> Iterator[str]:
    """Start a RabbitMQ container and yield its AMQP URL.

    Waits for :data:`READY_LINE`.

    Yields:
        str: The URL of the broker, on ``127.0.0.1``.
    """
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not reachable")
    name = f"tempest-queue-fanout-{PORT}"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    started = subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, "-p", f"{PORT}:5672", IMAGE],
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start {IMAGE}: {started.stderr.strip()}")
    try:
        for _ in range(90):
            logs = subprocess.run(
                ["docker", "logs", name],
                capture_output=True,
                text=True,
            )
            if READY_LINE in logs.stdout + logs.stderr:
                break
            time.sleep(1)
        else:
            pytest.skip(f"{IMAGE} never became ready")
        yield f"amqp://guest:guest@127.0.0.1:{PORT}/"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.fixture
def names() -> dict[str, str]:
    """Unique names per test, so tests share no topology.

    A queue re-declared with different arguments is refused with
    ``PRECONDITION_FAILED``, so consumer names carry the suffix too.

    Returns:
        dict[str, str]: The event exchange, the dead-letter exchange and
        the suffix for consumer names.
    """
    suffix = uuid4().hex[:8]
    return {"events": f"events.{suffix}", "dlx": f"dlx.{suffix}", "id": suffix}


@pytest_asyncio.fixture
async def inspector(amqp_url: str) -> AsyncIterator[Any]:
    """An ``aio_pika`` channel to read queue depths without consuming.

    Not FastStream's declarer: it caches the queue object per name, so the
    message count read from it is the one of its first declaration.

    Args:
        amqp_url (str): The broker URL.

    Yields:
        Any: An open ``aio_pika`` channel.
    """
    import aio_pika

    connection = await aio_pika.connect(amqp_url)
    try:
        yield await connection.channel()
    finally:
        await connection.close()


async def _depth(inspector: Any, queue: str) -> int:
    """Return how many messages sit in ``queue`` right now.

    Args:
        inspector (Any): An open ``aio_pika`` channel.
        queue (str): The queue name.

    Returns:
        int: The ready message count, from a fresh passive declaration.
    """
    declared = await inspector.declare_queue(queue, passive=True)
    return int(declared.declaration_result.message_count)


async def _until(predicate: Any, what: str) -> None:
    """Poll ``predicate`` until it holds or the deadline passes.

    Args:
        predicate (Any): An async callable returning ``bool``.
        what (str): What is awaited, for the failure message.

    Raises:
        AssertionError: When the deadline passes first.
    """
    deadline = time.monotonic() + DEADLINE_SECONDS
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


class TestFanOut:
    """Two consumers of one event, one exchange, one copy each."""

    async def test_each_consumer_gets_its_own_copy(
        self,
        amqp_url: str,
        names: dict[str, str],
    ) -> None:
        events = ExchangeSpec(names["events"])
        mq = MessageBroker.rabbitmq(amqp_url, logger=None)
        received: dict[str, list[str]] = {"gamification": [], "rules": []}

        @mq.on(
            QueueSpec.consumer(
                f"gamification-{names['id']}", "task.completed", exchange=events
            )
        )
        async def gamification(body: str) -> None:
            received["gamification"].append(body)

        @mq.on(
            QueueSpec.consumer(
                f"rules-{names['id']}", "task.completed", exchange=events
            )
        )
        async def rules(body: str) -> None:
            received["rules"].append(body)

        async with mq.lifespan():
            await mq.publish("task.completed", "t-1", exchange=events)

            async def both() -> bool:
                return bool(received["gamification"] and received["rules"])

            await _until(both, "a copy on each consumer")
            await asyncio.sleep(0.3)
        assert received == {"gamification": ["t-1"], "rules": ["t-1"]}

    async def test_a_relay_can_publish_before_any_consumer_declared_the_exchange(
        self,
        amqp_url: str,
        names: dict[str, str],
    ) -> None:
        """Publishing to a missing exchange is a channel error; the spec declares it."""
        mq = MessageBroker.rabbitmq(amqp_url, logger=None)
        async with mq.lifespan():
            await mq.publish(
                "task.completed",
                "t-1",
                exchange=ExchangeSpec(names["events"]),
            )


class TestDeadLetters:
    """A rejection lands in the rejecting consumer's queue, and only there."""

    async def test_failure_goes_to_the_consumer_dlq_only(
        self,
        amqp_url: str,
        names: dict[str, str],
        inspector: Any,
    ) -> None:
        events = ExchangeSpec(names["events"])
        failing = QueueSpec.consumer(
            f"gamification-{names['id']}",
            "task.completed",
            exchange=events,
            dead_letter_exchange=names["dlx"],
        )
        healthy = QueueSpec.consumer(
            f"rules-{names['id']}",
            "task.completed",
            exchange=events,
            dead_letter_exchange=names["dlx"],
        )
        mq = MessageBroker.rabbitmq(amqp_url, logger=None)
        received: list[str] = []

        @mq.on(failing)
        async def broken(body: str) -> None:
            raise RuntimeError("handler bug")

        @mq.on(healthy)
        async def fine(body: str) -> None:
            received.append(body)

        async with mq.lifespan():
            await mq.publish("task.completed", "t-1", exchange=events)

            async def dead_lettered() -> bool:
                return (
                    await _depth(
                        inspector, f"gamification-{names['id']}.task.completed.dlq"
                    )
                    == 1
                )

            await _until(dead_lettered, "the rejection in the dlq")
        assert received == ["t-1"]
        assert await _depth(inspector, f"rules-{names['id']}.task.completed.dlq") == 0

    async def test_dead_letter_topology_exists_before_the_first_delivery(
        self,
        amqp_url: str,
        names: dict[str, str],
        inspector: Any,
    ) -> None:
        """A message already waiting is rejected the moment the consumer starts.

        The queue is created by a broker that declares nothing, so its
        dead-letter exchange does not exist, and a message is left in it.
        The second broker's handler rejects that message as soon as it
        subscribes; it reaches the dead-letter queue only if the exchange
        and the queue were declared before the subscription started.
        """
        events = ExchangeSpec(names["events"])
        spec = QueueSpec.consumer(
            f"focus-{names['id']}",
            "task.completed",
            exchange=events,
            dead_letter_exchange=names["dlx"],
        )
        setup = MessageBroker.rabbitmq(amqp_url, declare_topology=False, logger=None)

        @setup.on(spec)
        async def idle(body: str) -> None:
            raise AssertionError("the setup broker must not consume")

        await setup.connect()
        await setup.disconnect()
        async with MessageBroker.rabbitmq(amqp_url, logger=None).lifespan() as relay:
            await relay.publish("task.completed", "waiting", exchange=events)
        assert await _depth(inspector, spec.name) == 1

        mq = MessageBroker.rabbitmq(amqp_url, logger=None)

        @mq.on(spec)
        async def broken(body: str) -> None:
            raise RuntimeError("handler bug")

        async with mq.lifespan():

            async def dead_lettered() -> bool:
                return (
                    await _depth(inspector, f"focus-{names['id']}.task.completed.dlq")
                    == 1
                )

            await _until(dead_lettered, "the waiting message in the dlq")


class TestReplay:
    """Back to the consumer that failed, and to nobody else."""

    async def test_replay_returns_the_message_to_its_consumer_only(
        self,
        amqp_url: str,
        names: dict[str, str],
        inspector: Any,
    ) -> None:
        events = ExchangeSpec(names["events"])
        failing = QueueSpec.consumer(
            f"gamification-{names['id']}",
            "task.completed",
            exchange=events,
            dead_letter_exchange=names["dlx"],
        )
        healthy = QueueSpec.consumer(
            f"rules-{names['id']}", "task.completed", exchange=events
        )
        from faststream.rabbit import RabbitMessage

        mq = MessageBroker.rabbitmq(amqp_url, logger=None)
        broken = True
        seen: dict[str, list[tuple[str, Any, Any]]] = {"gamification": [], "rules": []}

        @mq.on(failing)
        async def gamification_handler(body: str, message: RabbitMessage) -> None:
            seen["gamification"].append(
                (body, message.message_id, message.headers.get("x-death")),
            )
            if broken:
                raise RuntimeError("handler bug")

        @mq.on(healthy)
        async def rules_handler(body: str, message: RabbitMessage) -> None:
            seen["rules"].append((body, message.message_id, None))

        async with mq.lifespan():
            await mq.publish(
                "task.completed",
                "t-1",
                exchange=events,
                message_id="evt-1",
            )

            async def dead_lettered() -> bool:
                return (
                    await _depth(
                        inspector, f"gamification-{names['id']}.task.completed.dlq"
                    )
                    == 1
                )

            await _until(dead_lettered, "the rejection in the dlq")
            broken = False
            assert await mq.replay_dead_letters(failing) == 1

            async def redelivered() -> bool:
                return len(seen["gamification"]) == 2

            await _until(redelivered, "the replayed delivery")
            await asyncio.sleep(0.3)
        assert seen["rules"] == [("t-1", "evt-1", None)]
        assert seen["gamification"] == [
            ("t-1", "evt-1", None),
            ("t-1", "evt-1", None),
        ]
        assert (
            await _depth(inspector, f"gamification-{names['id']}.task.completed.dlq")
            == 0
        )

    async def test_dead_lettering_writes_the_headers_replay_drops(
        self,
        amqp_url: str,
        names: dict[str, str],
        inspector: Any,
    ) -> None:
        """Measure what the broker adds, so the dropped set is not a guess."""
        from tempest_fastapi_sdk.queue.reliability import DEATH_HEADERS

        spec = QueueSpec.consumer(
            f"audit-{names['id']}",
            "task.completed",
            exchange=ExchangeSpec(names["events"]),
            dead_letter_exchange=names["dlx"],
        )
        mq = MessageBroker.rabbitmq(amqp_url, logger=None)

        @mq.on(spec)
        async def broken(body: str) -> None:
            raise RuntimeError("handler bug")

        async with mq.lifespan():
            await mq.publish(
                "task.completed",
                "t-1",
                exchange=spec.binding.exchange if spec.binding else None,
                headers={"tenant": "acme"},
            )

            async def dead_lettered() -> bool:
                return (
                    await _depth(inspector, f"audit-{names['id']}.task.completed.dlq")
                    == 1
                )

            await _until(dead_lettered, "the rejection in the dlq")
        dlq = await inspector.declare_queue(
            f"audit-{names['id']}.task.completed.dlq",
            passive=True,
        )
        raw = await dlq.get(no_ack=False, fail=True)
        added = {key for key in raw.headers if key.startswith("x-")}
        await raw.nack(requeue=True)
        assert added == DEATH_HEADERS
        assert raw.headers["tenant"] == "acme"

    async def test_retry_topology_dead_queue_is_replayed_into_main(
        self,
        amqp_url: str,
        names: dict[str, str],
        inspector: Any,
    ) -> None:
        topology: RetryTopology = retry_queues(
            f"orders.{names['events']}",
            retry_exchange=f"{names['events']}.retry",
            main_exchange=names["events"],
            dead_exchange=f"{names['events']}.dead",
        )
        mq = MessageBroker.rabbitmq(amqp_url, logger=None)
        async with mq.lifespan():
            await mq.declare_retry_topology(topology)
            await mq.publish(topology.dead.name, "dead-1")

            async def landed() -> bool:
                return await _depth(inspector, topology.dead.name) == 1

            await _until(landed, "the message in the dead queue")
            assert await mq.replay_dead_letters(topology) == 1
        assert await _depth(inspector, topology.dead.name) == 0
        assert await _depth(inspector, topology.main.name) == 1

    async def test_limit_caps_one_run(
        self,
        amqp_url: str,
        names: dict[str, str],
        inspector: Any,
    ) -> None:
        dead = f"manual.{names['events']}.dlq"
        origin = f"manual.{names['events']}"
        mq = MessageBroker.rabbitmq(amqp_url, logger=None)
        from faststream.rabbit import RabbitQueue

        async with mq.lifespan():
            await mq.broker.declare_queue(RabbitQueue(dead, durable=True))
            await mq.broker.declare_queue(RabbitQueue(origin, durable=True))
            for index in range(3):
                await mq.publish(dead, f"m-{index}")

            async def landed() -> bool:
                return await _depth(inspector, dead) == 3

            await _until(landed, "three dead messages")
            assert await mq.replay_dead_letters(dead, into=origin, limit=2) == 2
        assert await _depth(inspector, dead) == 1
        assert await _depth(inspector, origin) == 2

    async def test_a_publish_that_fails_leaves_the_message_dead(
        self,
        amqp_url: str,
        names: dict[str, str],
        inspector: Any,
    ) -> None:
        """Nothing is acknowledged that did not arrive somewhere.

        Measured: publishing to a queue that does not exist through the
        default exchange raises nothing — the broker returns the message
        as unroutable — so a replay that only published and acked would
        lose it. The origin queue is checked before the first read.
        """
        from aiormq.exceptions import ChannelNotFoundEntity

        dead = f"orphan.{names['events']}.dlq"
        mq = MessageBroker.rabbitmq(amqp_url, logger=None)
        from faststream.rabbit import RabbitQueue

        async with mq.lifespan():
            await mq.broker.declare_queue(RabbitQueue(dead, durable=True))
            await mq.publish(dead, "m-1")

            async def landed() -> bool:
                return await _depth(inspector, dead) == 1

            await _until(landed, "the dead message")
            with pytest.raises(ChannelNotFoundEntity):
                await mq.replay_dead_letters(dead, into=f"missing.{names['events']}")
        assert await _depth(inspector, dead) == 1


class TestRefusedDeclaration:
    """A DLQ that exists with other arguments stops connect() up front."""

    async def test_precondition_failed_surfaces_before_any_consumer(
        self,
        amqp_url: str,
        names: dict[str, str],
        inspector: Any,
    ) -> None:
        from aiormq.exceptions import ChannelPreconditionFailed

        spec = QueueSpec.consumer(
            f"legacy-{names['id']}",
            "task.completed",
            exchange=ExchangeSpec(names["events"]),
            dead_letter_exchange=names["dlx"],
        )
        assert spec.dead_letter is not None
        assert spec.dead_letter.queue is not None
        await inspector.declare_queue(
            spec.dead_letter.queue,
            durable=True,
            arguments={"x-message-ttl": 60_000},
        )
        mq = MessageBroker.rabbitmq(amqp_url, logger=None)
        consumed: list[str] = []

        @mq.on(spec)
        async def handle(body: str) -> None:
            consumed.append(body)

        with pytest.raises(ChannelPreconditionFailed, match="inequivalent arg"):
            await mq.connect()
        assert mq.is_connected is False
        assert consumed == []
