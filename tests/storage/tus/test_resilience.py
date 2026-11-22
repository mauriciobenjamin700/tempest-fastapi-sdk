"""Second review round: a stalled body, a dropped client, quotas without an owner.

Each test fails on ``aef81efe`` and passes after the fix. The stalled-body
and disconnect cases drive the app as raw ASGI, because an HTTP client
always finishes or aborts its body: here the body sends one piece and then
never sends the next, or sends ``http.disconnect`` — what a phone losing
signal mid-chunk looks like to the server.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI, Header, HTTPException

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.storage.tus import LocalTusStore, TusStore, make_tus_router
from tests.storage.tus._support import CHUNK, TUS

Message = MutableMapping[str, Any]


async def caller(x_user: str | None = Header(default=None)) -> str | None:
    """Resolve the caller from ``X-User``.

    Args:
        x_user (str | None): The header.

    Returns:
        str | None: The user id.
    """
    return x_user


def _app(store: TusStore, **options: Any) -> FastAPI:
    """Build an app with the tus router and ``options``.

    Args:
        store (TusStore): The store.
        **options (Any): Forwarded to :func:`make_tus_router`.

    Returns:
        FastAPI: The app.
    """
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(make_tus_router(store, **options))
    return app


def _client(app: FastAPI, user: str | None = "ana") -> httpx.AsyncClient:
    """Build an HTTP client.

    Args:
        app (FastAPI): The app.
        user (str | None): ``X-User`` to send, or none.

    Returns:
        httpx.AsyncClient: The client.
    """
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={} if user is None else {"X-User": user},
    )


async def _raw_patch(
    app: FastAPI,
    path: str,
    receive: Callable[[], Awaitable[Message]],
) -> list[Message]:
    """Send a ``PATCH`` as raw ASGI and collect what the app sends back.

    Args:
        app (FastAPI): The app.
        path (str): The upload path.
        receive (Callable[[], Awaitable[Message]]): The body source.

    Returns:
        list[Message]: The ASGI messages the app sent.
    """
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "PATCH",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"tus-resumable", b"1.0.0"),
            (b"content-type", b"application/offset+octet-stream"),
            (b"upload-offset", b"0"),
            (b"content-length", b"6"),
            (b"x-user", b"ana"),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    sent: list[Message] = []

    async def send(message: Message) -> None:
        """Record one message.

        Args:
            message (Message): What the app sent.
        """
        sent.append(message)

    await asyncio.wait_for(app(scope, receive, send), timeout=10)
    return sent


async def test_stalled_body_times_out_and_releases_the_lock(tmp_path: Path) -> None:
    """One piece then silence: ``408``, nothing stored, the next ``PATCH`` gets in."""
    store = LocalTusStore(tmp_path)
    app = _app(
        store,
        current_user_id=caller,
        body_read_timeout=timedelta(milliseconds=200),
    )
    never = asyncio.Event()
    calls = 0

    async def stalled() -> Message:
        """Send three bytes, then nothing, ever.

        Returns:
            Message: The first piece; later calls never return.
        """
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"type": "http.request", "body": b"abc", "more_body": True}
        await never.wait()
        return {"type": "http.disconnect"}

    async with _client(app) as http:
        url = (
            await http.post("/api/uploads", headers={**TUS, "Upload-Length": "6"})
        ).headers["location"]
        sent = await _raw_patch(app, url, stalled)
        start = next(m for m in sent if m["type"] == "http.response.start")
        offset = (await http.head(url, headers=TUS)).headers["upload-offset"]
        retry = await http.patch(
            url,
            content=b"abcdef",
            headers={**CHUNK, "Upload-Offset": "0"},
        )
    assert start["status"] == 408
    assert (b"tus-resumable", b"1.0.0") in start["headers"]
    assert offset == "0"
    assert retry.status_code == 204
    assert retry.headers["upload-offset"] == "6"


async def test_client_disconnect_mid_body_is_not_a_500(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A dropped client is logged as a disconnect and leaves the upload free."""
    store = LocalTusStore(tmp_path)
    app = _app(store, current_user_id=caller)
    calls = 0

    async def dropping() -> Message:
        """Send three bytes, then the disconnect.

        Returns:
            Message: The next ASGI message.
        """
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"type": "http.request", "body": b"abc", "more_body": True}
        return {"type": "http.disconnect"}

    async with _client(app) as http:
        url = (
            await http.post("/api/uploads", headers={**TUS, "Upload-Length": "6"})
        ).headers["location"]
        with caplog.at_level(logging.DEBUG):
            sent = await _raw_patch(app, url, dropping)
        retry = await http.patch(
            url,
            content=b"abcdef",
            headers={**CHUNK, "Upload-Offset": "0"},
        )
    statuses = [m["status"] for m in sent if m["type"] == "http.response.start"]
    assert all(status < 500 for status in statuses)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("disconnected" in r.getMessage() for r in caplog.records)
    assert retry.status_code == 204


async def test_anonymous_router_enforces_no_shared_quota(tmp_path: Path) -> None:
    """Without owners one anonymous caller cannot exhaust a quota for all."""
    app = _app(LocalTusStore(tmp_path), max_uploads_per_owner=1)
    async with _client(app, user=None) as http:
        answers = [
            await http.post("/api/uploads", headers={**TUS, "Upload-Length": "3"})
            for _ in range(3)
        ]
    assert [a.status_code for a in answers] == [201, 201, 201]


async def test_empty_upload_is_not_counted_against_the_quota(tmp_path: Path) -> None:
    """An upload that completes on creation is never refused by the count."""
    app = _app(LocalTusStore(tmp_path), current_user_id=caller, max_uploads_per_owner=1)
    async with _client(app) as http:
        first = await http.post("/api/uploads", headers={**TUS, "Upload-Length": "3"})
        empty = await http.post("/api/uploads", headers={**TUS, "Upload-Length": "0"})
    assert first.status_code == 201
    assert empty.status_code == 201


async def test_reused_http_exception_does_not_leak_the_tus_header(
    tmp_path: Path,
) -> None:
    """A module-level ``HTTPException`` raised by the tus routes stays untouched."""
    shared = HTTPException(status_code=401, detail="no token")

    async def refuse() -> str:
        """Refuse with the shared instance.

        Raises:
            HTTPException: Always ``shared``.
        """
        raise shared

    app = _app(LocalTusStore(tmp_path), current_user_id=refuse)

    @app.get("/me")
    async def me(user: str = Depends(refuse)) -> str:
        """A non-tus route behind the same dependency.

        Args:
            user (str): Never resolved.

        Returns:
            str: Never returned.
        """
        return user

    async with _client(app) as http:
        tus = await http.post("/api/uploads", headers={**TUS, "Upload-Length": "3"})
        other = await http.get("/me")
    assert tus.status_code == 401
    assert tus.headers["tus-resumable"] == "1.0.0"
    assert other.status_code == 401
    assert "tus-resumable" not in other.headers
    assert shared.headers is None
