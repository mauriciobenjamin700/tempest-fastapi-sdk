"""Kafka semantics of the facade against a real single-node Kafka.

``test_kafka.py`` pins what the facade hands FastStream. What only a
cluster answers is whether that means what the docs say: a key keeps its
partition and its order, consumers sharing a group split the partitions
while another group reads its own copy, ``ACK`` commits (also after a
raising handler) so a restarted group resumes past it, ``NACK_ON_ERROR``
reads the failed record again, and the ``message_id`` header is what
deduplicates. The broker runs in a container on ``make test-docker``.

``test_kafka_matrix.py`` reruns this module under the FastStream floor
``pyproject.toml`` declares and under a fixed newer release.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from faststream import AckPolicy
from faststream.kafka.annotations import KafkaMessage
from pydantic import BaseModel

from tempest_fastapi_sdk.queue import (
    Consumer,
    MemoryDedupStore,
    MessageBroker,
    OffsetResetEnum,
)
from tempest_fastapi_sdk.queue.dedup import MESSAGE_ID_HEADER

pytestmark = pytest.mark.docker

IMAGE: str = "apache/kafka:3.9.1"
"""The broker under test: Apache's own image, KRaft, no ZooKeeper."""

PORT: int = int(os.environ.get("TEMPEST_KAFKA_PORT", "59093"))
"""Host port; the matrix gives each subprocess its own."""

CONTAINER: str = os.environ.get("TEMPEST_KAFKA_CONTAINER", f"tempest-kafka-{PORT}")
"""Container name; the matrix gives each subprocess its own."""

READY_LINE: str = "Kafka Server started"
"""What the broker logs once it accepts clients."""

DEADLINE_SECONDS: float = 30.0
"""Upper bound on waiting for deliveries; bounds the failure, not the pass.

A consumer group's first join and partition assignment take seconds on
a cold broker, which is why this is larger than the RabbitMQ suite's.
"""


class Event(BaseModel):
    key: str
    seq: int


def _environment(port: int) -> list[str]:
    """Return the ``-e`` flags of a single-node KRaft broker on ``port``.

    The advertised listener has to be the **host** port: the client
    bootstraps on it and then reconnects to whatever the broker
    advertises, so the image's default (``localhost:9092``) would send it
    to a port nothing listens on.

    Args:
        port (int): The host port.

    Returns:
        list[str]: Alternating ``-e`` / ``NAME=value`` items.
    """
    settings = {
        "KAFKA_NODE_ID": "1",
        "KAFKA_PROCESS_ROLES": "broker,controller",
        "KAFKA_LISTENERS": "PLAINTEXT://:9092,CONTROLLER://:9093",
        "KAFKA_ADVERTISED_LISTENERS": f"PLAINTEXT://127.0.0.1:{port}",
        "KAFKA_CONTROLLER_LISTENER_NAMES": "CONTROLLER",
        "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP": (
            "CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT"
        ),
        "KAFKA_CONTROLLER_QUORUM_VOTERS": "1@localhost:9093",
        "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR": "1",
        "KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR": "1",
        "KAFKA_TRANSACTION_STATE_LOG_MIN_ISR": "1",
        "KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS": "0",
    }
    flags: list[str] = []
    for name, value in settings.items():
        flags += ["-e", f"{name}={value}"]
    return flags


@pytest.fixture(scope="module")
def servers() -> Iterator[str]:
    """Start a Kafka container and yield its bootstrap address.

    Yields:
        str: ``127.0.0.1:<port>``.
    """
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not reachable")
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    started = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            CONTAINER,
            "-p",
            f"{PORT}:9092",
            *_environment(PORT),
            IMAGE,
        ],
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start {IMAGE}: {started.stderr.strip()}")
    try:
        for _ in range(120):
            logs = subprocess.run(
                ["docker", "logs", CONTAINER],
                capture_output=True,
                text=True,
            )
            if READY_LINE in logs.stdout + logs.stderr:
                break
            time.sleep(1)
        else:
            pytest.skip(f"{IMAGE} never became ready")
        yield f"127.0.0.1:{PORT}"
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


@pytest_asyncio.fixture
async def topic(servers: str) -> AsyncIterator[Callable[[int], Awaitable[str]]]:
    """Create uniquely named topics with a given partition count.

    Created up front rather than left to auto-creation, which makes a
    single-partition topic and would make every key land together.

    Args:
        servers (str): The bootstrap address.

    Yields:
        Callable[[int], Awaitable[str]]: ``await make(partitions)`` → name.
    """
    from aiokafka.admin import AIOKafkaAdminClient, NewTopic

    admin = AIOKafkaAdminClient(bootstrap_servers=servers)
    await admin.start()

    async def make(partitions: int) -> str:
        name = f"t-{uuid4().hex[:10]}"
        await admin.create_topics(
            [NewTopic(name, num_partitions=partitions, replication_factor=1)],
        )
        return name

    try:
        yield make
    finally:
        await admin.close()


async def _until(condition: Callable[[], bool], what: str) -> None:
    """Poll ``condition`` until it holds or the deadline passes.

    Args:
        condition (Callable[[], bool]): The state to wait for.
        what (str): What is being waited for, for the failure message.
    """
    deadline = time.monotonic() + DEADLINE_SECONDS
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.1)


def _assigned(mq: MessageBroker) -> int:
    """Return how many partitions ``mq``'s first subscriber holds.

    Args:
        mq (MessageBroker): A connected facade.

    Returns:
        int: The size of the aiokafka consumer's assignment.
    """
    consumer = mq.broker.subscribers[0].consumer
    return 0 if consumer is None else len(consumer.assignment())


async def test_publish_reaches_the_consumer(servers: str, topic: Any) -> None:
    name = await topic(1)
    mq = MessageBroker.kafka(servers)
    got: list[Event] = []

    @mq.on(name, group_id="g", auto_offset_reset=OffsetResetEnum.EARLIEST)
    async def handle(event: Event) -> None:
        got.append(event)

    async with mq.lifespan():
        await mq.publish(name, Event(key="a", seq=1))
        await _until(lambda: len(got) == 1, "the delivery")

    assert got == [Event(key="a", seq=1)]


async def test_a_key_keeps_its_partition_and_its_order(
    servers: str,
    topic: Any,
) -> None:
    name = await topic(3)
    keys = [f"order-{n}" for n in range(6)]
    per_key = 8
    mq = MessageBroker.kafka(servers)
    seen: list[tuple[str, int, int]] = []

    @mq.on(name, group_id="g", auto_offset_reset=OffsetResetEnum.EARLIEST)
    async def handle(event: Event, message: KafkaMessage) -> None:
        seen.append((event.key, event.seq, message.raw_message.partition))

    published: dict[str, set[int]] = {key: set() for key in keys}
    async with mq.lifespan():
        for seq in range(per_key):
            for key in keys:
                metadata = await mq.publish(name, Event(key=key, seq=seq), key=key)
                published[key].add(metadata.partition)
        await _until(lambda: len(seen) == len(keys) * per_key, "every record")

    for key in keys:
        consumed = [(seq, part) for k, seq, part in seen if k == key]
        assert [seq for seq, _ in consumed] == list(range(per_key))
        assert {part for _, part in consumed} == published[key]
        assert len(published[key]) == 1
    assert len({part for _, _, part in seen}) > 1


async def test_a_group_splits_and_another_group_copies(
    servers: str,
    topic: Any,
) -> None:
    """Two replicas of one group share the records; a second group gets all.

    The replicas go through the class path and the second group through
    the decorator, so both carry ``group_id`` to the same place.
    """
    name = await topic(2)
    workers: list[list[int]] = [[], []]
    audit: list[int] = []

    def replica(index: int) -> MessageBroker:
        mq = MessageBroker.kafka(servers)

        class Worker(Consumer):
            async def handle(self, event: Event) -> None:
                workers[index].append(event.seq)

        mq.register(
            Worker(
                channel=name,
                schema=Event,
                group_id="workers",
                auto_offset_reset=OffsetResetEnum.EARLIEST,
            ),
        )
        return mq

    auditor = MessageBroker.kafka(servers)

    @auditor.on(name, group_id="audit", auto_offset_reset=OffsetResetEnum.EARLIEST)
    async def record(event: Event) -> None:
        audit.append(event.seq)

    first, second = replica(0), replica(1)
    total = 40
    async with first.lifespan(), second.lifespan(), auditor.lifespan():
        await _until(
            lambda: _assigned(first) == 1 and _assigned(second) == 1,
            "one partition per replica",
        )
        for seq in range(total):
            await first.publish(name, Event(key=f"k{seq}", seq=seq), key=f"k{seq}")
        await _until(
            lambda: len(workers[0]) + len(workers[1]) == total and len(audit) == total,
            "both groups",
        )

    assert sorted(workers[0] + workers[1]) == list(range(total))
    assert not set(workers[0]) & set(workers[1])
    assert workers[0] and workers[1]
    assert sorted(audit) == list(range(total))


async def test_ack_commits_so_a_restarted_group_resumes_past_it(
    servers: str,
    topic: Any,
) -> None:
    """``ACK`` commits after the handler — after one that raised, too."""
    name = await topic(1)
    runs: list[list[int]] = [[], []]

    def member(run: int) -> MessageBroker:
        mq = MessageBroker.kafka(servers)

        @mq.on(
            name,
            group_id="committing",
            auto_offset_reset=OffsetResetEnum.EARLIEST,
            ack_policy=AckPolicy.ACK,
        )
        async def handle(event: Event) -> None:
            runs[run].append(event.seq)
            if event.seq == 1:
                raise RuntimeError("handler bug")

        return mq

    first = member(0)
    async with first.lifespan():
        for seq in range(3):
            await first.publish(name, Event(key="k", seq=seq))
        await _until(lambda: len(runs[0]) == 3, "the first three")
    second = member(1)
    async with second.lifespan():
        for seq in range(3, 5):
            await second.publish(name, Event(key="k", seq=seq))
        await _until(lambda: len(runs[1]) >= 2, "the next two")
        await asyncio.sleep(1.0)

    assert runs[0] == [0, 1, 2]
    assert runs[1] == [3, 4]


async def test_nack_on_error_reads_the_record_again(
    servers: str,
    topic: Any,
) -> None:
    name = await topic(1)
    mq = MessageBroker.kafka(servers)
    attempts: list[int] = []

    @mq.on(
        name,
        group_id="retrying",
        auto_offset_reset=OffsetResetEnum.EARLIEST,
        ack_policy=AckPolicy.NACK_ON_ERROR,
    )
    async def handle(event: Event) -> None:
        attempts.append(event.seq)
        if len(attempts) == 1:
            raise RuntimeError("transient")

    async with mq.lifespan():
        await mq.publish(name, Event(key="k", seq=7))
        await _until(lambda: len(attempts) == 2, "the second attempt")

    assert attempts == [7, 7]


async def test_a_republished_message_id_runs_once(
    servers: str,
    topic: Any,
) -> None:
    """Two records, two offsets, one id: the header is the dedup key."""
    name = await topic(1)
    mq = MessageBroker.kafka(servers)
    mq.deduplicate(MemoryDedupStore())
    handled: list[int] = []
    headers: list[dict[str, Any]] = []

    @mq.on(name, group_id="g", auto_offset_reset=OffsetResetEnum.EARLIEST)
    async def handle(event: Event, message: KafkaMessage) -> None:
        handled.append(event.seq)
        headers.append(dict(message.headers))

    async with mq.lifespan():
        await mq.publish(name, Event(key="k", seq=1), message_id="evt-1")
        await mq.publish(name, Event(key="k", seq=1), message_id="evt-1")
        await mq.publish(name, Event(key="k", seq=2), message_id="evt-2")
        await _until(lambda: len(handled) == 2, "the two distinct ids")
        await asyncio.sleep(1.0)

    assert handled == [1, 2]
    assert [h[MESSAGE_ID_HEADER] for h in headers] == ["evt-1", "evt-2"]


async def test_foreign_records_at_one_offset_are_both_handled(
    servers: str,
    topic: Any,
) -> None:
    """Without the header, the key is the record's coordinates.

    Published past the facade, so no ``message_id`` header: offset 0 of
    partition 0 and offset 0 of partition 1, same timestamp. FastStream
    names both ``0-<timestamp>``, and keyed by that the second was
    skipped as already processed.
    """
    name = await topic(2)
    mq = MessageBroker.kafka(servers)
    mq.deduplicate(MemoryDedupStore())
    handled: list[int] = []
    ids: list[str] = []

    @mq.on(name, group_id="g", auto_offset_reset=OffsetResetEnum.EARLIEST)
    async def handle(event: Event, message: KafkaMessage) -> None:
        handled.append(event.seq)
        ids.append(message.message_id)

    stamp = int(time.time() * 1000)
    async with mq.lifespan():
        for partition in (0, 1):
            await mq.broker.publish(
                Event(key="k", seq=partition).model_dump(),
                name,
                partition=partition,
                timestamp_ms=stamp,
            )
        await _until(lambda: len(handled) == 2, "both records")

    assert sorted(handled) == [0, 1]
    assert ids == [f"0-{stamp}", f"0-{stamp}"]
