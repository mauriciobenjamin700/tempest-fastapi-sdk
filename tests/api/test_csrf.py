"""Tests for ``CSRFMiddleware`` + ``make_csrf_token_dependency``."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    CSRFMiddleware,
    generate_csrf_token,
    make_csrf_token_dependency,
)


def _build_app(*, exclude: tuple[str, ...] = ()) -> FastAPI:
    app = FastAPI()
    app.add_middleware(CSRFMiddleware, exclude_paths=exclude)

    @app.get("/page")
    async def page(
        token: str = Depends(make_csrf_token_dependency()),
    ) -> dict[str, str]:
        return {"csrf": token}

    @app.post("/submit")
    async def submit(_: Request) -> dict[str, str]:
        return {"ok": "ok"}

    return app


class TestCSRFGuard:
    async def test_safe_methods_pass(self) -> None:
        app = _build_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get("/page")
        assert r.status_code == 200
        assert "csrf" in r.json()

    async def test_post_without_token_rejected(self) -> None:
        app = _build_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post("/submit")
        assert r.status_code == 403
        assert r.json()["code"] == "CSRF_VALIDATION_FAILED"

    async def test_post_with_matching_token_passes(self) -> None:
        app = _build_app()
        token = generate_csrf_token()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/submit",
                cookies={CSRF_COOKIE_NAME: token},
                headers={CSRF_HEADER_NAME: token},
            )
        assert r.status_code == 200

    async def test_post_with_mismatched_token_rejected(self) -> None:
        app = _build_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/submit",
                cookies={CSRF_COOKIE_NAME: "cookie-value"},
                headers={CSRF_HEADER_NAME: "header-value"},
            )
        assert r.status_code == 403

    async def test_exclude_paths_bypass(self) -> None:
        app = _build_app(exclude=("/submit",))
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post("/submit")
        assert r.status_code == 200


class TestCSRFToken:
    def test_generate_token_unique_per_call(self) -> None:
        assert generate_csrf_token() != generate_csrf_token()

    def test_generate_token_url_safe(self) -> None:
        token = generate_csrf_token()
        # base64url: only A-Z a-z 0-9 - _
        assert all(c.isalnum() or c in "-_" for c in token)
