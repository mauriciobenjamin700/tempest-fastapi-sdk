"""Trace and request-id propagation across the queue.

A request opens a trace, publishes an event, and answers 201. The
consumer that charges the card, writes the ledger and sends the mail then
shows up as **three orphan traces** — no parent, no relation to each
other, and no way to tell which request caused them. That is exactly the
shape distributed tracing exists to fix, and the queue was the one hop
the SDK did not instrument.

AMQP carries per-message headers, which is the transport the W3C
``traceparent`` was designed for, so nothing needs inventing: inject on
publish, extract on consume.

Two things travel, and the second matters more day to day:

* the **trace context**, so the consumer's span joins the request's
  trace; and
* the **request id** the
  :class:`~tempest_fastapi_sdk.api.middlewares.RequestIDMiddleware`
  already puts on every HTTP log line, so the worker's log lines carry it
  too and ``grep`` alone correlates them.

Like :class:`~tempest_fastapi_sdk.genai.genai_span`, everything here is a
no-op without the ``[otel]`` extra, and non-recording when the extra is
installed but no provider was configured. Request-id propagation needs no
extra at all.
"""

from __future__ import annotations

from typing import Any, Final

from tempest_fastapi_sdk.core.context import (
    clear_request_id,
    get_request_id,
    set_request_id,
)

TRACEPARENT_HEADER: Final[str] = "traceparent"
"""W3C header carrying the trace and span the message was published from."""

REQUEST_ID_HEADER: Final[str] = "x-request-id"
"""Header carrying the originating request id, for log correlation."""

MESSAGING_SYSTEM: Final[str] = "rabbitmq"
"""``messaging.system`` attribute value for the AMQP transport."""

_TRACER_NAME: Final[str] = "tempest.queue"


def _otel_trace() -> Any | None:
    """Return ``opentelemetry.trace``, or ``None`` when absent.

    Returns:
        Any | None: The module when the ``[otel]`` extra is installed,
        otherwise ``None`` so callers degrade to a no-op.
    """
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    return trace


def _propagator() -> Any | None:
    """Return the configured W3C propagator, or ``None`` when absent.

    Returns:
        Any | None: The global text-map propagator.
    """
    try:
        from opentelemetry.propagate import get_global_textmap
    except ImportError:
        return None
    return get_global_textmap()


def inject_context(headers: dict[str, Any]) -> dict[str, Any]:
    """Add the current trace context and request id to ``headers``.

    Args:
        headers (dict[str, Any]): Headers being assembled for a publish.
            Mutated in place and returned, for the caller's convenience.

    Returns:
        dict[str, Any]: The same mapping. Unchanged when no trace is
        active and no request id is set, so a publish outside a request
        carries no empty headers.
    """
    request_id = get_request_id()
    if request_id and REQUEST_ID_HEADER not in headers:
        headers[REQUEST_ID_HEADER] = request_id

    propagator = _propagator()
    if propagator is not None:
        propagator.inject(headers)
    return headers


def extract_context(headers: dict[str, Any]) -> Any | None:
    """Recover the publishing trace context from ``headers``.

    Args:
        headers (dict[str, Any]): The consumed message's headers.

    Returns:
        Any | None: An OpenTelemetry ``Context``, or ``None`` when the
        extra is missing or the message carries no ``traceparent``.
    """
    propagator = _propagator()
    if propagator is None or TRACEPARENT_HEADER not in headers:
        return None
    return propagator.extract(headers)


class consume_span:  # noqa: N801
    """Span around one consumed message, linked to the publishing trace.

    Follows the OpenTelemetry **messaging** semantic conventions:
    ``messaging.system``, ``messaging.destination.name`` and
    ``messaging.operation``, with the span named ``"<channel> process"``.

    The publishing trace is attached as a **link**, not as a parent. The
    convention recommends it for asynchronous consumption, and the reason
    is practical: a consumer can run minutes after the publish, and a
    child span of that duration would stretch the request's trace and
    make its latency unreadable. A link keeps both traces truthful and
    still lets you jump between them.

    Restores the publisher's request id for the duration of the block, so
    the worker's log lines carry the id of the request that caused them —
    which is worth more than the span to anyone debugging with ``grep``.
    """

    def __init__(self, channel: str, headers: dict[str, Any] | None = None) -> None:
        """Prepare a span for a message on ``channel``.

        Args:
            channel (str): The queue/topic the message arrived on.
            headers (dict[str, Any] | None): The message headers, read for
                the trace context and the request id.
        """
        self.channel: str = channel
        self.headers: dict[str, Any] = headers or {}
        self._span: Any | None = None
        self._cm: Any | None = None
        self._token: Any | None = None

    def __enter__(self) -> consume_span:
        """Start the span and adopt the publisher's request id.

        Returns:
            consume_span: This instance.
        """
        request_id = self.headers.get(REQUEST_ID_HEADER)
        if isinstance(request_id, bytes):
            request_id = request_id.decode()
        if request_id:
            self._token = set_request_id(str(request_id))

        trace = _otel_trace()
        if trace is None:
            return self

        parent = extract_context(self.headers)
        links: list[Any] = []
        if parent is not None:
            span_context = trace.get_current_span(parent).get_span_context()
            if span_context.is_valid:
                links.append(trace.Link(span_context))

        tracer = trace.get_tracer(_TRACER_NAME)
        self._cm = tracer.start_as_current_span(
            f"{self.channel} process",
            links=links,
            attributes={
                "messaging.system": MESSAGING_SYSTEM,
                "messaging.destination.name": self.channel,
                "messaging.operation": "process",
            },
        )
        self._span = self._cm.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Close the span, recording a handler failure, and drop the id.

        The request id is cleared in a ``finally``: a contextvar restored
        on the way in and not reset on the way out leaks into whatever
        the worker task runs next, which would label an unrelated message
        with the previous message's request id — the exact confusion this
        module exists to remove.

        Args:
            exc_type (Any): The exception class, when the block raised.
            exc (Any): The exception instance.
            tb (Any): The traceback.
        """
        try:
            if self._cm is not None:
                if exc is not None and self._span is not None:
                    self._span.record_exception(exc)
                    trace = _otel_trace()
                    if trace is not None:
                        self._span.set_status(
                            trace.Status(trace.StatusCode.ERROR, str(exc)),
                        )
                self._cm.__exit__(exc_type, exc, tb)
        finally:
            if self._token is not None:
                clear_request_id(self._token)


def make_tracing_middleware() -> Any:
    """Build the FastStream middleware that opens a span per consume.

    Returns:
        Any: A ``faststream.BaseMiddleware`` subclass.

    Raises:
        ImportError: When the ``[queue]`` extra is not installed.
    """
    from tempest_fastapi_sdk.queue.broker import _require
    from tempest_fastapi_sdk.queue.reliability import _message_channel

    faststream = _require("faststream", "queue")

    class _TracingMiddleware(faststream.BaseMiddleware):  # type: ignore[misc,name-defined]
        """Open a linked span and restore the request id around a consume."""

        async def consume_scope(self, call_next: Any, msg: Any) -> Any:
            """Run the handler inside a span.

            Args:
                call_next (Any): The next middleware or the handler.
                msg (Any): The message being consumed.

            Returns:
                Any: Whatever the handler returned.

            Raises:
                BaseException: Re-raised unchanged after the span records
                    it, so nothing about error handling changes.
            """
            headers = dict(getattr(msg, "headers", None) or {})
            with consume_span(_message_channel(msg), headers):
                return await call_next(msg)

    return _TracingMiddleware


__all__: list[str] = [
    "MESSAGING_SYSTEM",
    "REQUEST_ID_HEADER",
    "TRACEPARENT_HEADER",
    "consume_span",
    "extract_context",
    "inject_context",
    "make_tracing_middleware",
]
