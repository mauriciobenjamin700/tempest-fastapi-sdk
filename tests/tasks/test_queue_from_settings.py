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

from tempest_fastapi_sdk.tasks import (
    DEFAULT_RESULT_PREFIX,
    DEFAULT_RESULT_TTL_SECONDS,
    TaskIQSettingsLike,
    TaskQueue,
)

REDIS_URL = "redis://localhost:6379/0"
AMQP_URL = "amqp://guest:guest@localhost:5672/"


class Settings:
    """The fields the factory reads, without pydantic in the way.

    Attributes:
        TASKIQ_BROKER_URL (str): Broker URL.
        TASKIQ_RESULT_BACKEND_URL (str | None): Result backend URL.
        TASKIQ_STORE_RESULTS (bool): Whether results are stored at all.
        TASKIQ_RESULT_TTL_SECONDS (int): Seconds a result survives.
    """

    def __init__(
        self,
        broker_url: str = "",
        result_url: str | None = None,
        *,
        store_results: bool = True,
        result_ttl_seconds: int = DEFAULT_RESULT_TTL_SECONDS,
    ) -> None:
        """Set the fields.

        Args:
            broker_url (str): Value for ``TASKIQ_BROKER_URL``.
            result_url (str | None): Value for
                ``TASKIQ_RESULT_BACKEND_URL``.
            store_results (bool): Value for ``TASKIQ_STORE_RESULTS``.
            result_ttl_seconds (int): Value for
                ``TASKIQ_RESULT_TTL_SECONDS``.
        """
        self.TASKIQ_BROKER_URL: str = broker_url
        self.TASKIQ_RESULT_BACKEND_URL: str | None = result_url
        self.TASKIQ_STORE_RESULTS: bool = store_results
        self.TASKIQ_RESULT_TTL_SECONDS: int = result_ttl_seconds


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


class TestTheResultFootprintIsBounded:
    """A stored result expires and lives under a prefix.

    ``taskiq_redis`` defaults to neither: measured against Redis 8.2.9,
    a result key for a task returning ``None`` is 144 bytes with
    ``TTL -1``, written under the bare task id. A once-a-minute cron
    leaves 1440 such keys a day, and on a shared Redis running
    ``allkeys-lru`` that growth evicts the keys of whoever is using
    Redis to work.
    """

    def test_the_default_ttl_and_prefix_reach_the_backend(self) -> None:
        queue = TaskQueue.from_settings(Settings(REDIS_URL))
        backend = queue.broker.result_backend

        assert backend.result_ex_time == DEFAULT_RESULT_TTL_SECONDS
        assert backend.prefix_str == DEFAULT_RESULT_PREFIX

    def test_the_amqp_branch_gets_the_same_treatment(self) -> None:
        """Fixing only the branch that hurt is not fixing the rule."""
        queue = TaskQueue.from_settings(Settings(AMQP_URL, REDIS_URL))
        backend = queue.broker.result_backend

        assert backend.result_ex_time == DEFAULT_RESULT_TTL_SECONDS
        assert backend.prefix_str == DEFAULT_RESULT_PREFIX

    def test_zero_restores_the_unbounded_behaviour_on_purpose(self) -> None:
        queue = TaskQueue.from_settings(Settings(REDIS_URL), result_ttl_seconds=0)

        assert queue.broker.result_backend.result_ex_time is None

    def test_an_empty_prefix_restores_the_bare_task_id(self) -> None:
        queue = TaskQueue.from_settings(Settings(REDIS_URL), result_prefix="")

        assert queue.broker.result_backend.prefix_str is None

    def test_the_settings_ttl_is_honoured(self) -> None:
        queue = TaskQueue.from_settings(Settings(REDIS_URL, result_ttl_seconds=3600))

        assert queue.broker.result_backend.result_ex_time == 3600


class TestResultsCanBeTurnedOff:
    """``results=False`` used to be unreachable through the factory.

    The Redis branch hardcoded ``results=settings.TASKIQ_RESULT_BACKEND_URL
    or True``, which is always truthy, and passing the keyword through
    ``**options`` collided:
    ``TypeError: TaskQueue.redis() got multiple values for keyword
    argument 'results'``.
    """

    def test_an_explicit_false_no_longer_collides(self) -> None:
        queue = TaskQueue.from_settings(Settings(REDIS_URL), results=False)

        assert type(queue.broker.result_backend).__name__ == "DummyResultBackend"

    def test_the_settings_flag_turns_them_off(self) -> None:
        queue = TaskQueue.from_settings(Settings(REDIS_URL, store_results=False))

        assert type(queue.broker.result_backend).__name__ == "DummyResultBackend"

    def test_the_flag_is_symmetric_on_amqp(self) -> None:
        """Redis used to read an empty result URL as "use the broker".

        On AMQP the same configuration meant "no results", so the two
        transports disagreed about what one environment said.
        """
        queue = TaskQueue.from_settings(
            Settings(AMQP_URL, REDIS_URL, store_results=False),
        )

        assert type(queue.broker.result_backend).__name__ == "DummyResultBackend"

    def test_an_explicit_url_wins_over_the_flag(self) -> None:
        queue = TaskQueue.from_settings(
            Settings(REDIS_URL, store_results=False),
            results="redis://elsewhere:6379/9",
        )

        assert type(queue.broker.result_backend).__name__ == "RedisAsyncResultBackend"


class TestTheUnknownSchemeStillNamesItself:
    """A typo in a deployed variable must not become an AttributeError.

    The result knobs are read inside the transport branches for exactly
    this reason: reading a settings field before dispatching on the
    scheme answered an unsupported URL with a complaint about a field
    the caller never mentioned.
    """

    def test_a_bad_scheme_raises_value_error_even_without_the_new_fields(
        self,
    ) -> None:
        class Minimal:
            TASKIQ_BROKER_URL = "kafka://localhost:9092"
            TASKIQ_RESULT_BACKEND_URL = None

        with pytest.raises(ValueError, match="unsupported scheme 'kafka'"):
            TaskQueue.from_settings(Minimal())  # type: ignore[arg-type]
