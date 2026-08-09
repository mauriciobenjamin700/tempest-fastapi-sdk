"""Tests for tempest_fastapi_sdk.queue.tracing.

Two properties carry the feature and both are asserted directly: the
request id survives the hop (worth more than the span to anyone debugging
with grep), and the consumer's span **links** to the publishing trace
instead of parenting it — a child span spanning minutes would stretch the
request's trace and make its latency unreadable.

The OTel paths run against a real in-memory exporter rather than a mock,
so a change in how the SDK builds spans shows up as a missing attribute
rather than as a passing assertion about a call that no longer happens.
"""

from collections.abc import Iterator
from typing import Any

import pytest

from tempest_fastapi_sdk.core.context import get_request_id, set_request_id
from tempest_fastapi_sdk.queue.tracing import (
    MESSAGING_SYSTEM,
    REQUEST_ID_HEADER,
    TRACEPARENT_HEADER,
    consume_span,
    extract_context,
    inject_context,
)

pytest.importorskip("opentelemetry", reason="tracing needs the [otel] extra")


@pytest.fixture
def exporter() -> Iterator[Any]:
    """Attach an in-memory exporter to the active OTel provider.

    Reuses an already-configured SDK ``TracerProvider`` when one is
    present, because OpenTelemetry refuses to replace a provider once set
    and another test in the suite may have installed one — forcing a
    fresh provider makes the spans vanish rather than fail loudly.

    Yields:
        Any: The exporter holding every finished span.
    """
    sdk_trace = pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry import trace
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    provider = trace.get_tracer_provider()
    if not isinstance(provider, sdk_trace.TracerProvider):
        provider = sdk_trace.TracerProvider()
        trace.set_tracer_provider(provider)
    memory = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    yield memory
    memory.clear()


class TestRequestIdPropagation:
    def test_the_current_request_id_is_injected(self) -> None:
        token = set_request_id("req-42")
        try:
            assert inject_context({})[REQUEST_ID_HEADER] == "req-42"
        finally:
            from tempest_fastapi_sdk.core.context import clear_request_id

            clear_request_id(token)

    def test_no_request_id_means_no_header(self) -> None:
        """A publish outside a request must not carry an empty header."""
        assert REQUEST_ID_HEADER not in inject_context({})

    def test_an_explicit_header_is_not_overwritten(self) -> None:
        token = set_request_id("req-42")
        try:
            headers = inject_context({REQUEST_ID_HEADER: "mine"})
            assert headers[REQUEST_ID_HEADER] == "mine"
        finally:
            from tempest_fastapi_sdk.core.context import clear_request_id

            clear_request_id(token)

    def test_the_consumer_adopts_the_publishers_request_id(self) -> None:
        """This is what makes the worker's log lines greppable."""
        with consume_span("orders.paid", {REQUEST_ID_HEADER: "req-7"}):
            assert get_request_id() == "req-7"

    def test_bytes_headers_are_decoded(self) -> None:
        """Some AMQP clients hand headers back as bytes."""
        with consume_span("orders.paid", {REQUEST_ID_HEADER: b"req-7"}):
            assert get_request_id() == "req-7"

    def test_the_request_id_is_dropped_on_exit(self) -> None:
        with consume_span("orders.paid", {REQUEST_ID_HEADER: "req-7"}):
            pass
        assert get_request_id() is None

    def test_it_is_dropped_even_when_the_handler_raises(self) -> None:
        with (
            pytest.raises(ValueError),
            consume_span("orders.paid", {REQUEST_ID_HEADER: "req-7"}),
        ):
            raise ValueError("boom")
        assert get_request_id() is None

    def test_it_is_dropped_even_when_closing_the_span_fails(self) -> None:
        """A tracer failing on exit must not leak the id into the next message.

        The worker task is reused across consumes, so a contextvar left
        set labels an unrelated message with the previous request's id —
        worse than no correlation at all, because it reads as real.
        """

        class _Exploding:
            def __exit__(self, *_: Any) -> None:
                raise RuntimeError("exporter died")

        span = consume_span("orders.paid", {REQUEST_ID_HEADER: "req-7"})
        span.__enter__()
        span._cm = _Exploding()
        with pytest.raises(RuntimeError, match="exporter died"):
            span.__exit__(None, None, None)
        assert get_request_id() is None


class TestTraceContext:
    def test_a_traceparent_is_injected_inside_a_span(self, exporter: Any) -> None:
        from opentelemetry import trace

        with trace.get_tracer("test").start_as_current_span("publish"):
            headers = inject_context({})
        assert TRACEPARENT_HEADER in headers

    def test_extract_returns_none_without_a_traceparent(self) -> None:
        assert extract_context({}) is None

    def test_the_consumer_span_links_to_the_publisher(self, exporter: Any) -> None:
        """Link, not parent: an async consume must not stretch the trace."""
        from opentelemetry import trace

        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("publish") as publisher:
            headers = inject_context({})
            published = publisher.get_span_context()

        with consume_span("orders.paid", headers):
            pass

        consumed = next(
            span for span in exporter.get_finished_spans() if "process" in span.name
        )
        assert len(consumed.links) == 1
        assert consumed.links[0].context.trace_id == published.trace_id
        assert consumed.parent is None

    def test_the_span_carries_the_messaging_conventions(self, exporter: Any) -> None:
        with consume_span("orders.paid", {}):
            pass
        span = next(
            s for s in exporter.get_finished_spans() if s.name == "orders.paid process"
        )
        assert span.attributes["messaging.system"] == MESSAGING_SYSTEM
        assert span.attributes["messaging.destination.name"] == "orders.paid"
        assert span.attributes["messaging.operation"] == "process"

    def test_a_handler_failure_marks_the_span(self, exporter: Any) -> None:
        from opentelemetry.trace import StatusCode

        with pytest.raises(ValueError), consume_span("orders.paid", {}):
            raise ValueError("handler bug")

        span = next(s for s in exporter.get_finished_spans() if "process" in s.name)
        assert span.status.status_code is StatusCode.ERROR
        assert span.events


class TestBrokerWiring:
    def test_enable_tracing_installs_a_middleware(self) -> None:
        from tempest_fastapi_sdk.queue import MessageBroker

        mq = MessageBroker.rabbitmq("amqp://guest:guest@localhost:5672/")
        before = len(mq.broker.middlewares)
        mq.enable_tracing()
        assert len(mq.broker.middlewares) == before + 1

    async def test_publish_injects_headers(self) -> None:
        from tempest_fastapi_sdk.core.context import clear_request_id
        from tempest_fastapi_sdk.queue import MessageBroker

        seen: dict[str, Any] = {}

        class _Recorder:
            async def publish(self, message: Any, channel: Any, **options: Any) -> None:
                seen.update(options)

        mq = MessageBroker(_Recorder())  # type: ignore[arg-type]
        mq._started = True
        token = set_request_id("req-9")
        try:
            await mq.publish("orders.paid", {"a": 1})
        finally:
            clear_request_id(token)
        assert seen["headers"][REQUEST_ID_HEADER] == "req-9"

    async def test_caller_headers_are_preserved(self) -> None:
        from tempest_fastapi_sdk.queue import MessageBroker

        seen: dict[str, Any] = {}

        class _Recorder:
            async def publish(self, message: Any, channel: Any, **options: Any) -> None:
                seen.update(options)

        mq = MessageBroker(_Recorder())  # type: ignore[arg-type]
        mq._started = True
        await mq.publish("orders.paid", {"a": 1}, headers={"x-tenant": "acme"})
        assert seen["headers"]["x-tenant"] == "acme"


class TestTransportCompatibility:
    """``headers`` must not reach a transport that cannot take it.

    Redis happens to accept ``headers`` while rejecting ``message_id``, so
    the two keywords are guarded independently rather than behind one
    "is this RabbitMQ?" branch — a transport allow-list would have been
    wrong for exactly this case.
    """

    def test_redis_takes_headers_even_though_it_rejects_message_id(self) -> None:
        from faststream.redis import RedisBroker

        from tempest_fastapi_sdk.queue.broker import _publish_accepts

        publish = RedisBroker("redis://x").publish
        assert _publish_accepts(publish, "headers")
        assert not _publish_accepts(publish, "message_id")

    async def test_headers_are_omitted_on_a_transport_without_them(self) -> None:
        from tempest_fastapi_sdk.queue import MessageBroker

        seen: dict[str, Any] = {}

        class _NoHeaders:
            async def publish(self, message: Any, channel: Any) -> None:
                seen["called"] = True

        mq = MessageBroker(_NoHeaders())  # type: ignore[arg-type]
        mq._started = True
        await mq.publish("orders.paid", {"a": 1})
        assert seen["called"] is True
