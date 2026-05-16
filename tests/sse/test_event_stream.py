"""Tests for tempest_fastapi_sdk.sse."""

import asyncio

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
