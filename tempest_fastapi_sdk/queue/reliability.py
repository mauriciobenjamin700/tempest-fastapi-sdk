"""Failure handling for the event path: dead letters, retry, metrics.

The consumer's ack policy is ``REJECT_ON_ERROR``: a handler that raises
issues ``basic.reject`` with ``requeue=False``. That is the right choice
— it avoids a poison message looping forever — but on its own it means a
failed message is **discarded**, with no error surface, no dead queue and
no metric. A bug in one handler silently drops the event that triggered
it.

Three pieces close that, mirroring what
:class:`~tempest_fastapi_sdk.tasks.TaskQueue` already has for background
tasks:

* :func:`make_dead_letter_middleware` hands every terminal failure to a
  :class:`~tempest_fastapi_sdk.tasks.DeadLetterSink` — the **same**
  protocol, sink implementations and admin panel the task path uses, so
  a dead task and a dead event land on one screen.
* :func:`retry_queues` builds the RabbitMQ delayed-retry topology out of
  :class:`~tempest_fastapi_sdk.queue.QueueSpec`, so a transient failure
  is retried by the broker rather than by a loop that dies with the pod.
* :class:`QueueMetrics` publishes consume counts and durations to the
  shared Prometheus registry, because a failure rate nobody can graph is
  a failure rate nobody alerts on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from tempest_fastapi_sdk.queue.topology import (
    DeadLetterSpec,
    QueueSpec,
    QueueType,
)
from tempest_fastapi_sdk.tasks.observability import DeadLetter, DeadLetterSink

if TYPE_CHECKING:
    from prometheus_client import CollectorRegistry

RETRY_SUFFIX: Final[str] = ".retry"
"""Suffix of the waiting queue a message sits in between attempts."""

DEAD_SUFFIX: Final[str] = ".dead"
"""Suffix of the terminal queue a message lands in once attempts run out."""

DEFAULT_RETRY_DELAY_MS: Final[int] = 30_000
"""Delay before a failed message is offered to the consumer again.

Thirty seconds is long enough to ride out the failure that dominates this
path — a dependency restarting or briefly rate-limiting — and short
enough that a transient blip does not read as an outage. It is a starting
point, not a measurement: the right value depends on what the handler
calls, which is why it is a parameter.
"""

DEFAULT_MAX_ATTEMPTS: Final[int] = 3
"""How many times a message is delivered before it is given up on."""


@dataclass(frozen=True)
class ConsumerRetryPolicy:
    """How many times to retry a failing consumer, and how long to wait.

    Attributes:
        max_attempts (int): Total deliveries, including the first. ``1``
            disables retrying and sends the first failure straight to the
            dead queue.
        delay_ms (int): Milliseconds a message waits before being offered
            again.
    """

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    delay_ms: int = DEFAULT_RETRY_DELAY_MS

    def __post_init__(self) -> None:
        """Reject values that would silently disable the policy.

        Raises:
            ValueError: When ``max_attempts`` is below 1 or ``delay_ms``
                is not positive.
        """
        if self.max_attempts < 1:
            raise ValueError(
                f"ConsumerRetryPolicy.max_attempts must be at least 1, "
                f"got {self.max_attempts!r}.",
            )
        if self.delay_ms <= 0:
            raise ValueError(
                f"ConsumerRetryPolicy.delay_ms must be positive, "
                f"got {self.delay_ms!r}.",
            )


@dataclass(frozen=True)
class RetryTopology:
    """The three queues a retried channel needs.

    Carries the exchange names as well as the queues, because the queues
    alone are not a working topology: each has to be **bound** to its
    exchange, and a chain declared without the bindings routes a rejected
    message into an exchange with nothing behind it — where RabbitMQ
    silently drops it. See
    :meth:`~tempest_fastapi_sdk.queue.MessageBroker.declare_retry_topology`.

    Attributes:
        main (QueueSpec): What the consumer subscribes to. Dead-letters
            to the retry exchange when the handler rejects.
        retry (QueueSpec): The waiting room. Holds nothing but a TTL;
            when a message expires it is dead-lettered **back** to the
            main exchange, which is what makes the delay work without a
            plugin.
        dead (QueueSpec): Terminal. Nothing routes out of it — it is read
            by a human, or by the admin panel.
        channel (str): The routing key every binding uses.
        main_exchange (str): Exchange the main queue is bound to.
        retry_exchange (str): Exchange the retry queue is bound to.
        dead_exchange (str): Exchange the dead queue is bound to.
    """

    main: QueueSpec
    retry: QueueSpec
    dead: QueueSpec
    channel: str
    main_exchange: str
    retry_exchange: str
    dead_exchange: str


def retry_queues(
    channel: str,
    policy: ConsumerRetryPolicy | None = None,
    *,
    retry_exchange: str,
    main_exchange: str,
    dead_exchange: str,
    queue_type: QueueType = QueueType.CLASSIC,
) -> RetryTopology:
    """Build the delayed-retry topology for ``channel``.

    RabbitMQ has no per-message delay of its own. The portable way to get
    one is a pair of queues: the main queue dead-letters a rejected
    message into a **retry queue whose only job is to hold it**, and that
    queue's TTL dead-letters it back to the main exchange when it
    expires. The message therefore reappears at the consumer ``delay_ms``
    later, with the broker doing the waiting — so a worker restart in the
    meantime changes nothing.

    The alternative is the ``rabbitmq_delayed_message_exchange`` plugin,
    which is simpler to declare and **requires the plugin installed** —
    not available on several managed offerings, including the free
    CloudAMQP tier. This builds on stock AMQP instead.

    Args:
        channel (str): The channel the consumer subscribes to.
        policy (ConsumerRetryPolicy | None): Attempts and delay. Defaults
            to :class:`ConsumerRetryPolicy`.
        retry_exchange (str): Exchange the main queue dead-letters to.
        main_exchange (str): Exchange the retry queue returns messages
            to. Must route back to ``channel``.
        dead_exchange (str): Exchange the terminal queue is fed from.
        queue_type (QueueType): Queue implementation for all three.

    Returns:
        RetryTopology: The three specs, ready to declare and subscribe.

    Notes:
        ``max_attempts`` is **not** enforced by this topology — AMQP
        counts redeliveries in the ``x-death`` header but will not stop
        on its own. The count is enforced by the consumer middleware
        (:func:`make_dead_letter_middleware`), which reads that header
        and routes to the dead queue once the budget is spent. Declaring
        the topology without installing the middleware yields infinite
        retries, which is why they are documented together.
    """
    effective = policy or ConsumerRetryPolicy()
    return RetryTopology(
        channel=channel,
        main_exchange=main_exchange,
        retry_exchange=retry_exchange,
        dead_exchange=dead_exchange,
        main=QueueSpec(
            name=channel,
            dead_letter=DeadLetterSpec(exchange=retry_exchange),
            queue_type=queue_type,
        ),
        retry=QueueSpec(
            name=f"{channel}{RETRY_SUFFIX}",
            dead_letter=DeadLetterSpec(
                exchange=main_exchange,
                routing_key=channel,
            ),
            message_ttl_ms=effective.delay_ms,
            queue_type=queue_type,
        ),
        dead=QueueSpec(
            name=f"{channel}{DEAD_SUFFIX}",
            dead_letter=None,
            queue_type=queue_type,
        ),
    )


def delivery_attempt(message: Any) -> int:
    """Return which delivery this is, counting from 1.

    RabbitMQ records every dead-lettering in the message's ``x-death``
    header, so the number of times a message has already been rejected is
    readable without any state of our own — which matters, because a
    consumer restart must not reset the budget.

    Args:
        message (Any): The FastStream message being consumed.

    Returns:
        int: ``1`` on first delivery, ``2`` on the first retry, and so
        on. Falls back to ``1`` when the header is absent or malformed,
        so an unreadable header costs an extra attempt rather than
        dropping the message early.
    """
    headers = getattr(message, "headers", None) or {}
    deaths = headers.get("x-death")
    if not isinstance(deaths, list):
        return 1
    total = 0
    for entry in deaths:
        if isinstance(entry, dict):
            count = entry.get("count", 0)
            if isinstance(count, int):
                total += count
    return total + 1


def _message_channel(message: Any) -> str:
    """Return the channel a message arrived on, for reporting.

    Args:
        message (Any): The FastStream message.

    Returns:
        str: The queue/topic/subject name, or ``"unknown"`` when the
        transport does not expose one.
    """
    for attribute in ("queue", "channel", "subject", "topic"):
        value = getattr(message, attribute, None)
        if isinstance(value, str) and value:
            return value
    raw = getattr(message, "raw_message", None)
    routing_key = getattr(raw, "routing_key", None)
    return routing_key if isinstance(routing_key, str) and routing_key else "unknown"


def _message_id(message: Any) -> str:
    """Return a stable identifier for the message, for reporting.

    Args:
        message (Any): The FastStream message.

    Returns:
        str: The broker's message id, or ``""`` when absent.
    """
    value = getattr(message, "message_id", None)
    return str(value) if value else ""


def make_dead_letter_middleware(
    sink: DeadLetterSink,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Any:
    """Build a FastStream middleware routing terminal failures to ``sink``.

    The handler's exception is re-raised after the sink runs, so the
    broker still rejects the message and the retry topology (when
    declared) still applies. The sink is called **once**, on the delivery
    that exhausts ``max_attempts`` — not on every failed attempt, which
    would turn one bad message into a stream of alerts.

    The record handed over is the same
    :class:`~tempest_fastapi_sdk.tasks.DeadLetter` the task path uses, so
    every existing sink works unchanged and both kinds of failure share
    one admin screen. The mapping is deliberate and worth knowing:
    ``task_name`` carries the **channel**, ``task_id`` the broker's
    message id, and ``kwargs`` the decoded body under ``"body"``.

    Args:
        sink (DeadLetterSink): Where dead events go.
        max_attempts (int): Deliveries to allow before giving up. Must
            match the :class:`ConsumerRetryPolicy` used to build the
            topology, or the message will keep bouncing.

    Returns:
        Any: A ``faststream.BaseMiddleware`` subclass to add to a broker.

    Raises:
        ImportError: When the ``[queue]`` extra is not installed.
    """
    from tempest_fastapi_sdk.queue.broker import _require

    faststream = _require("faststream", "queue")

    class _DeadLetterMiddleware(faststream.BaseMiddleware):  # type: ignore[misc,name-defined]
        """Report a terminally-failed consume, then let it reject."""

        async def consume_scope(self, call_next: Any, msg: Any) -> Any:
            """Run the handler, reporting the failure that ends its budget.

            Args:
                call_next (Any): The next middleware or the handler.
                msg (Any): The message being consumed.

            Returns:
                Any: Whatever the handler returned.

            Raises:
                BaseException: Always re-raised, so the ack policy still
                    rejects the message and the broker still routes it.
            """
            try:
                return await call_next(msg)
            except Exception as exc:
                if delivery_attempt(msg) >= max_attempts:
                    await sink(
                        DeadLetter(
                            task_name=_message_channel(msg),
                            task_id=_message_id(msg),
                            exception=exc,
                            retries=max_attempts - 1,
                            kwargs={"body": getattr(msg, "body", None)},
                        ),
                    )
                raise

    return _DeadLetterMiddleware


class QueueMetrics:
    """Prometheus counters and durations for consumed messages.

    Mirrors :class:`~tempest_fastapi_sdk.tasks.TaskMetrics` so the event
    path and the task path graph the same way, on the same registry and
    the same ``/metrics`` endpoint:

    * ``queue_messages_total{channel,status}`` — ``status`` is ``ok`` or
      ``error``.
    * ``queue_message_duration_seconds{channel}`` — handler wall time.

    Without this, the consumer failure rate is invisible: the message is
    rejected, the broker discards or dead-letters it, and nothing counts.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        """Register the two collectors.

        Args:
            registry (CollectorRegistry | None): Registry to register on.
                ``None`` uses the SDK's shared one, so the series appear
                on the same ``/metrics`` as everything else.

        Raises:
            ImportError: When the ``[prometheus]`` extra is missing.
        """
        from tempest_fastapi_sdk.queue.broker import _require

        prometheus = _require("prometheus_client", "prometheus")
        target = registry if registry is not None else prometheus.REGISTRY
        self.consumed: Any = prometheus.Counter(
            "queue_messages_total",
            "Messages consumed, by channel and outcome.",
            ("channel", "status"),
            registry=target,
        )
        self.duration: Any = prometheus.Histogram(
            "queue_message_duration_seconds",
            "Handler wall time per consumed message.",
            ("channel",),
            registry=target,
        )

    def middleware(self) -> Any:
        """Build the FastStream middleware that feeds these collectors.

        Returns:
            Any: A ``faststream.BaseMiddleware`` subclass.

        Raises:
            ImportError: When the ``[queue]`` extra is not installed.
        """
        from tempest_fastapi_sdk.queue.broker import _require

        faststream = _require("faststream", "queue")
        metrics = self

        class _MetricsMiddleware(faststream.BaseMiddleware):  # type: ignore[misc,name-defined]
            """Time each consume and count its outcome."""

            async def consume_scope(self, call_next: Any, msg: Any) -> Any:
                """Record duration and outcome around the handler.

                Args:
                    call_next (Any): The next middleware or the handler.
                    msg (Any): The message being consumed.

                Returns:
                    Any: Whatever the handler returned.

                Raises:
                    BaseException: Re-raised unchanged after counting.
                """
                import time

                channel = _message_channel(msg)
                started = time.perf_counter()
                try:
                    result = await call_next(msg)
                except Exception:
                    metrics.consumed.labels(channel=channel, status="error").inc()
                    raise
                else:
                    metrics.consumed.labels(channel=channel, status="ok").inc()
                    return result
                finally:
                    metrics.duration.labels(channel=channel).observe(
                        time.perf_counter() - started,
                    )

        return _MetricsMiddleware


__all__: list[str] = [
    "DEAD_SUFFIX",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_RETRY_DELAY_MS",
    "RETRY_SUFFIX",
    "ConsumerRetryPolicy",
    "QueueMetrics",
    "RetryTopology",
    "delivery_attempt",
    "make_dead_letter_middleware",
    "retry_queues",
]
