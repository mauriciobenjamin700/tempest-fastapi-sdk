"""Tests for tempest_fastapi_sdk.sse."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import EventStream, ServerSentEvent, sse_response


class TestServerSentEvent:
    def test_simple_string_payload(self) -> None:
        encoded = ServerSentEvent(data="hello").encode()
        assert encoded == "data: hello\n\n"

    def test_multiline_payload_splits_data(self) -> None:
        encoded = ServerSentEvent(data="line1\nline2").encode()
        assert "data: line1\n" in encoded
        assert "data: line2\n" in encoded

    def test_full_fields(self) -> None:
        encoded = ServerSentEvent(
            data="payload",
            event="ping",
            id="42",
            retry=5000,
        ).encode()
        assert "event: ping" in encoded
        assert "id: 42" in encoded
        assert "retry: 5000" in encoded
        assert "data: payload" in encoded
        assert encoded.endswith("\n\n")

    def test_dict_payload_json_encoded(self) -> None:
        encoded = ServerSentEvent(data={"x": 1}).encode()
        assert 'data: {"x": 1}' in encoded

    def test_bytes_payload_decoded(self) -> None:
        encoded = ServerSentEvent(data=b"hello").encode()
        assert "data: hello" in encoded

    def test_comment_line(self) -> None:
        encoded = ServerSentEvent(comment="keepalive", data="").encode()
        assert encoded.startswith(": keepalive\n")


class TestEventStream:
    async def test_publish_and_close(self) -> None:
        stream = EventStream(heartbeat_seconds=None)
        await stream.publish("hello", event="ping")
        await stream.close()
        chunks = [chunk async for chunk in stream.stream()]
        assert b"event: ping" in chunks[0]
        assert b"data: hello" in chunks[0]

    async def test_heartbeat_emitted_when_idle(self) -> None:
        stream = EventStream(heartbeat_seconds=0.01)

        async def collect_one() -> bytes:
            async for chunk in stream.stream():
                return chunk
            return b""

        task = asyncio.create_task(collect_one())
        chunk = await asyncio.wait_for(task, timeout=1.0)
        assert b": keepalive" in chunk
        await stream.close()

    async def test_close_terminates_iteration(self) -> None:
        stream = EventStream(heartbeat_seconds=None)
        await stream.publish("x")
        await stream.close()
        count = 0
        async for _ in stream.stream():
            count += 1
        assert count == 1


class TestBackpressure:
    async def test_drop_oldest_evicts_and_counts(self) -> None:
        stream = EventStream(
            heartbeat_seconds=None, max_queue=2, overflow="drop_oldest"
        )
        await stream.publish("a")
        await stream.publish("b")
        await stream.publish("c")  # evicts "a"
        await stream.close()
        frames = [chunk async for chunk in stream.stream()]
        joined = b"".join(frames)
        assert b"data: a" not in joined
        assert b"data: b" in joined
        assert b"data: c" in joined
        assert stream.dropped_events == 1

    async def test_drop_newest_keeps_backlog(self) -> None:
        stream = EventStream(
            heartbeat_seconds=None, max_queue=2, overflow="drop_newest"
        )
        await stream.publish("a")
        await stream.publish("b")
        await stream.publish("c")  # dropped
        await stream.close()
        frames = [chunk async for chunk in stream.stream()]
        joined = b"".join(frames)
        assert b"data: a" in joined
        assert b"data: b" in joined
        assert b"data: c" not in joined
        assert stream.dropped_events == 1

    async def test_close_sentinel_always_gets_through_when_full(self) -> None:
        stream = EventStream(
            heartbeat_seconds=None, max_queue=1, overflow="drop_newest"
        )
        await stream.publish("a")
        await stream.publish("b")  # dropped, queue full of "a"
        await stream.close()  # must still terminate despite full queue
        count = 0
        async for _ in stream.stream():
            count += 1
        assert count == 1  # only "a" survived, and iteration ended

    async def test_unbounded_never_drops(self) -> None:
        stream = EventStream(heartbeat_seconds=None, max_queue=0)
        for n in range(50):
            await stream.publish(str(n))
        await stream.close()
        frames = [chunk async for chunk in stream.stream()]
        assert len(frames) == 50
        assert stream.dropped_events == 0


@pytest.mark.asyncio
async def test_sse_response_end_to_end() -> None:
    app = FastAPI()
    stream = EventStream(heartbeat_seconds=None)

    @app.get("/events")
    async def events() -> object:
        await stream.publish("hello", event="ping")
        await stream.publish("world")
        await stream.close()
        return sse_response(stream.stream())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert b"event: ping" in response.content
    assert b"data: hello" in response.content
    assert b"data: world" in response.content


async def test_sse_response_caller_headers_cannot_override_defaults() -> None:
    """Caller-supplied headers must not override SSE-critical headers."""
    from fastapi import FastAPI

    app = FastAPI()

    async def empty() -> AsyncIterator[bytes]:
        if False:  # pragma: no cover - generator never yields
            yield b""

    @app.get("/events")
    async def events() -> object:
        return sse_response(
            empty(),
            headers={
                "Cache-Control": "public, max-age=3600",
                "X-Accel-Buffering": "yes",
                "X-Custom": "ok",
            },
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/events")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["x-custom"] == "ok"


@pytest.mark.asyncio
async def test_sse_response_runs_on_disconnect_when_stream_ends() -> None:
    """on_disconnect fires once the response generator finishes."""
    cleaned: list[str] = []
    app = FastAPI()
    stream = EventStream(heartbeat_seconds=None)

    async def cleanup() -> None:
        cleaned.append("done")

    @app.get("/events")
    async def events() -> object:
        await stream.publish("hello")
        await stream.close()
        return sse_response(stream.stream(), on_disconnect=cleanup)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/events")
    assert response.status_code == 200
    assert b"data: hello" in response.content
    assert cleaned == ["done"]


@pytest.mark.asyncio
async def test_event_stream_response_helper() -> None:
    """EventStream.response wraps stream() with SSE headers + on_disconnect."""
    cleaned: list[str] = []
    app = FastAPI()
    stream = EventStream(heartbeat_seconds=None)

    @app.get("/events")
    async def events() -> object:
        await stream.publish("x", event="tick")
        await stream.close()
        return stream.response(on_disconnect=lambda: cleaned.append("bye"))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/events")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert b"event: tick" in response.content
    assert cleaned == ["bye"]


class TestHeartbeatEvent:
    async def test_default_beat_is_a_comment(self) -> None:
        """The default frame carries no dispatchable data, by construction.

        It encodes as ``: keepalive\\ndata: \\n\\n``: the trailing
        ``data:`` is empty, and the SSE spec dispatches nothing when the
        data buffer is empty, so the browser never fires ``onmessage``.
        That invisibility is the whole reason ``heartbeat_event`` exists.
        """
        stream = EventStream(heartbeat_seconds=0.05)
        frame = await asyncio.wait_for(stream.stream().__anext__(), 1.0)
        assert frame == b": keepalive\ndata: \n\n"

    async def test_static_event_replaces_the_comment(self) -> None:
        """A comment never fires onmessage; a real event does."""
        stream = EventStream(
            heartbeat_seconds=0.05,
            heartbeat_event=ServerSentEvent(
                data={"id": None, "type": "PING", "message": "ping"},
                event="ping",
            ),
        )
        frame = await asyncio.wait_for(stream.stream().__anext__(), 1.0)
        assert b"event: ping" in frame
        assert b'"type": "PING"' in frame
        assert b": keepalive" not in frame

    async def test_callable_is_resolved_per_beat(self) -> None:
        beats = iter(range(1, 10))

        def build() -> ServerSentEvent:
            return ServerSentEvent(data={"beat": next(beats)}, event="ping")

        stream = EventStream(heartbeat_seconds=0.05, heartbeat_event=build)
        iterator = stream.stream()
        first = await asyncio.wait_for(iterator.__anext__(), 1.0)
        second = await asyncio.wait_for(iterator.__anext__(), 1.0)
        assert b'"beat": 1' in first
        assert b'"beat": 2' in second

    async def test_a_real_event_does_not_displace_published_data(self) -> None:
        stream = EventStream(
            heartbeat_seconds=0.05,
            heartbeat_event=ServerSentEvent(data="ping", event="ping"),
        )
        await stream.publish({"msg": "hi"}, event="chat")
        frame = await asyncio.wait_for(stream.stream().__anext__(), 1.0)
        assert b"event: chat" in frame


@pytest.mark.asyncio
async def test_on_disconnect_accepts_a_sync_callback_that_returns_a_value() -> None:
    """The shape the SSE recipe teaches: `on_disconnect=task.cancel`.

    `Task.cancel` is synchronous and returns `bool`, and `_guard_stream`
    discards whatever is not awaitable. The annotation said
    `Callable[[], Awaitable[None] | None]` until v0.257.0, so the recipe's own
    line failed a consumer's mypy while working perfectly at runtime. The
    other on_disconnect tests here all pass `async def cleanup() -> None`,
    which is the half that never broke; this one pins the half that did.
    """
    ran: list[str] = []
    app = FastAPI()

    @app.get("/events")
    async def events() -> object:
        stream = EventStream(heartbeat_seconds=None)

        async def producer() -> None:
            await stream.publish({"n": 1}, event="counter")
            await stream.close()
            await asyncio.sleep(30)

        task = asyncio.create_task(producer())

        def cancel() -> bool:
            """Exactly what `Task.cancel` is: sync, and returns a bool."""
            ran.append("cancelled")
            return task.cancel()

        return stream.response(on_disconnect=cancel)

    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://test") as client,
        client.stream("GET", "/events") as response,
    ):
        body = "".join([chunk async for chunk in response.aiter_text()])

    assert response.status_code == 200
    assert "event: counter" in body
    assert ran == ["cancelled"]
