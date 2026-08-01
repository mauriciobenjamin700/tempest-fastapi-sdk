"""Tests for ``BodySizeLimitMiddleware``."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from starlette.requests import ClientDisconnect

from tempest_fastapi_sdk import BodySizeLimitMiddleware


def _build_app(*, max_bytes: int, exclude: tuple[str, ...] = ()) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=max_bytes,
        exclude_paths=exclude,
    )

    @app.post("/echo")
    async def echo(payload: dict[str, str]) -> dict[str, str]:
        return payload

    @app.post("/upload/raw")
    async def upload_raw() -> dict[str, str]:
        return {"ok": "ok"}

    return app


class TestBodySizeLimit:
    async def test_small_payload_passes(self) -> None:
        app = _build_app(max_bytes=1024)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post("/echo", json={"hello": "world"})
        assert r.status_code == 200

    async def test_content_length_over_cap_rejected(self) -> None:
        app = _build_app(max_bytes=64)
        big_body = "x" * 4096
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post("/echo", json={"big": big_body})
        assert r.status_code == 413
        assert r.json()["code"] == "REQUEST_BODY_TOO_LARGE"

    async def test_zero_max_disables_the_check(self) -> None:
        app = _build_app(max_bytes=0)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post("/echo", json={"big": "x" * 10_000})
        assert r.status_code == 200

    async def test_exclude_path_bypasses_check(self) -> None:
        app = _build_app(max_bytes=64, exclude=("/upload/",))
        big = "x" * 4096
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/upload/raw",
                content=big.encode("utf-8"),
                headers={"content-type": "application/octet-stream"},
            )
        assert r.status_code == 200


class TestStreamingRejection:
    """The oversize streaming body gets exactly one answer: the 413.

    The guard sends it the moment the count is exceeded, while the app is
    still reading the body. Whatever the app produces afterwards is dropped —
    it must be, because a second ``http.response.start`` makes uvicorn raise
    ``RuntimeError: Response already started``.
    """

    async def test_body_parsing_hits_the_limit(self) -> None:
        app = _build_app(max_bytes=10)

        async def _oversized() -> AsyncIterator[bytes]:
            for _ in range(4):
                yield b'{"k":"0123456789"}'

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post(
                "/echo",
                content=_oversized(),
                headers={"content-type": "application/json"},
            )

        assert response.status_code == 413
        assert response.json()["code"] == "REQUEST_BODY_TOO_LARGE"

    async def test_handler_answer_after_the_413_is_dropped(self) -> None:
        """A handler that catches the disconnect does not get to answer.

        Its response would be the second one on the wire. Before this was
        guarded, that pair — the handler's status and then the 413 — is what
        raised ``Response already started``.
        """
        app = FastAPI()
        app.add_middleware(BodySizeLimitMiddleware, max_bytes=10)

        @app.post("/graceful")
        async def graceful(request: Request) -> JSONResponse:
            try:
                async for _chunk in request.stream():
                    pass
            except ClientDisconnect:
                return JSONResponse({"handled": "yes"}, status_code=400)
            return JSONResponse({"handled": "no"})

        async def _oversized() -> AsyncIterator[bytes]:
            for _ in range(4):
                yield b"0123456789"

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post("/graceful", content=_oversized())

        assert response.status_code == 413
        assert response.json()["code"] == "REQUEST_BODY_TOO_LARGE"

    async def test_handler_that_ignores_the_body_keeps_its_response(self) -> None:
        """A handler answering before the count trips cannot be overridden.

        Nothing oversized was processed there either way: the bytes are
        counted and discarded, never handed to the handler.
        """
        app = FastAPI()
        app.add_middleware(BodySizeLimitMiddleware, max_bytes=10)

        @app.post("/ignores")
        async def ignores() -> dict[str, str]:
            return {"read": "nothing"}

        async def _oversized() -> AsyncIterator[bytes]:
            for _ in range(4):
                yield b"0123456789"

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post("/ignores", content=_oversized())

        assert response.status_code == 200
        assert response.json() == {"read": "nothing"}
