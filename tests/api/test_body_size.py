"""Tests for ``BodySizeLimitMiddleware``."""

from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

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
