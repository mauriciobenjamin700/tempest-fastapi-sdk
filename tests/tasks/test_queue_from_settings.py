"""``TaskQueue.from_settings`` — the branch every service wrote by hand.

The module this replaces decided the same three things in every
service: which transport the URL means, whether results are stored, and
what an empty URL does. The last one carries the weight — a test suite
and a dev box without Redis run with ``TASKIQ_BROKER_URL`` empty, and
the answer has to be a broker that still registers ``@tq.task`` rather
than a parse error on an empty string.

The result backend is measured here too, because it was simply missing:
until v0.282.0 ``TaskQueue.redis()`` left TaskIQ's
``DummyResultBackend`` in place and offered no way to ask for another,
so reading a task result through the facade was impossible and the
service rebuilt the broker itself.
"""

from __future__ import annotations

import pytest
from taskiq.brokers.inmemory_broker import InMemoryBroker

from tempest_fastapi_sdk.tasks import TaskIQSettingsLike, TaskQueue

REDIS_URL = "redis://localhost:6379/0"
AMQP_URL = "amqp://guest:guest@localhost:5672/"


class Settings:
    """The two fields the factory reads, without pydantic in the way.

    Attributes:
        TASKIQ_BROKER_URL (str): Broker URL.
        TASKIQ_RESULT_BACKEND_URL (str | None): Result backend URL.
    """

    def __init__(
        self,
        broker_url: str = "",
        result_url: str | None = None,
    ) -> None:
        """Set the two fields.

        Args:
            broker_url (str): Value for ``TASKIQ_BROKER_URL``.
            result_url (str | None): Value for
                ``TASKIQ_RESULT_BACKEND_URL``.
        """
        self.TASKIQ_BROKER_URL: str = broker_url
        self.TASKIQ_RESULT_BACKEND_URL: str | None = result_url


def test_the_stand_in_satisfies_the_protocol() -> None:
    """Guards the fixture, not the feature.

    If ``Settings`` drifted from ``TaskIQSettingsLike`` the cases below
    would keep passing while testing a shape no real settings object
    has.
    """
    settings: TaskIQSettingsLike = Settings()
    assert settings.TASKIQ_BROKER_URL == ""


class TestTransportFromTheScheme:
    """The URL says which broker, so the service does not have to."""

    def test_empty_url_falls_back_to_memory(self) -> None:
        """The dev box and the suite run in exactly this shape."""
        queue = TaskQueue.from_settings(Settings(""))

        assert isinstance(queue.broker, InMemoryBroker)

    def test_whitespace_only_url_counts_as_empty(self) -> None:
        """``TASKIQ_BROKER_URL=" "`` in an ``.env`` is not a URL."""
        queue = TaskQueue.from_settings(Settings("   "))

        assert isinstance(queue.broker, InMemoryBroker)

    def test_redis_scheme_builds_a_stream_broker(self) -> None:
        queue = TaskQueue.from_settings(Settings(REDIS_URL))

        assert type(queue.broker).__name__ == "RedisStreamBroker"

    def test_amqp_scheme_builds_the_rabbitmq_broker(self) -> None:
        queue = TaskQueue.from_settings(Settings(AMQP_URL))

        assert type(queue.broker).__name__ == "AioPikaBroker"

    def test_an_unknown_scheme_is_refused_not_downgraded(self) -> None:
        """A scheme no transport handles raises, naming the scheme.

        What this asserts is the refusal, and the refusal is the whole
        point: falling back to the in-memory broker would leave a typo
        in a deployed environment variable looking like a healthy queue.
        The error names the scheme, so the typo is readable from the
        message.
        """
        with pytest.raises(ValueError, match="unsupported scheme 'kafka'"):
            TaskQueue.from_settings(Settings("kafka://localhost:9092"))


class TestResultBackend:
    """Where a task result goes, which was nowhere until v0.282.0."""

    def test_redis_stores_results_in_the_broker_url_by_default(self) -> None:
        queue = TaskQueue.redis(REDIS_URL)

        assert type(queue.broker.result_backend).__name__ == ("RedisAsyncResultBackend")

    def test_results_can_be_pointed_somewhere_else(self) -> None:
        queue = TaskQueue.redis(REDIS_URL, results="redis://other:6379/1")

        assert type(queue.broker.result_backend).__name__ == ("RedisAsyncResultBackend")

    def test_results_can_be_declined(self) -> None:
        """``False`` keeps the dummy, which is what shipped before."""
        queue = TaskQueue.redis(REDIS_URL, results=False)

        assert type(queue.broker.result_backend).__name__ == ("DummyResultBackend")

    def test_settings_route_results_where_the_field_points(self) -> None:
        """``TASKIQ_RESULT_BACKEND_URL`` had no consumer in the package."""
        queue = TaskQueue.from_settings(
            Settings(REDIS_URL, "redis://results:6379/2"),
        )

        assert type(queue.broker.result_backend).__name__ == ("RedisAsyncResultBackend")

    def test_rabbitmq_gets_results_from_redis_when_asked(self) -> None:
        """``taskiq-aio-pika`` ships no result backend of its own."""
        queue = TaskQueue.from_settings(Settings(AMQP_URL, REDIS_URL))

        assert type(queue.broker).__name__ == "AioPikaBroker"
        assert type(queue.broker.result_backend).__name__ == ("RedisAsyncResultBackend")


class TestTheLeaseUrlComesAlong:
    """A queue built from settings can elect a leader without help."""

    def test_redis_carries_the_url_its_lease_needs(self) -> None:
        """Otherwise ``scheduler=True`` would refuse on a Redis service."""
        queue = TaskQueue.from_settings(Settings(REDIS_URL))

        assert queue._lock_url == REDIS_URL

    def test_rabbitmq_borrows_the_redis_result_url(self) -> None:
        """A service with Redis results already has what election needs."""
        queue = TaskQueue.from_settings(Settings(AMQP_URL, REDIS_URL))

        assert queue._lock_url == REDIS_URL

    def test_rabbitmq_alone_carries_none(self) -> None:
        """And is therefore told to pass a lease explicitly."""
        queue = TaskQueue.from_settings(Settings(AMQP_URL))

        assert queue._lock_url is None
