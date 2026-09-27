"""Flash messages carried in a signed, read-once cookie (#349)."""

from __future__ import annotations

import base64
import json
import time

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.testclient import TestClient
from starlette.types import Receive, Scope, Send

from tempest_fastapi_sdk.ssr import (
    FLASH_COOKIE_NAME,
    MAX_FLASH_COOKIE_BYTES,
    MAX_FLASH_MESSAGE_LENGTH,
    FlashMiddleware,
    flash,
    flash_enabled,
    get_flashes,
    html_response,
)
from tempest_fastapi_sdk.ui.components import FlashMessage, FlashMessages

SECRET = "0123456789abcdef-test-secret"


async def _noop_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app used only to construct the middleware directly."""


def _middleware(**kwargs: object) -> FlashMiddleware:
    return FlashMiddleware(_noop_app, secret=SECRET, **kwargs)  # type: ignore[arg-type]


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(FlashMiddleware, secret=SECRET, secure=False)

    @app.post("/buckets")
    async def create(request: Request) -> RedirectResponse:
        flash(request, "Bucket criado.", "success")
        return RedirectResponse("/buckets", status_code=303)

    @app.post("/twice")
    async def twice(request: Request) -> RedirectResponse:
        flash(request, "primeira")
        flash(request, "segunda", "warning")
        return RedirectResponse("/buckets", status_code=303)

    @app.get("/buckets")
    async def listing(request: Request) -> HTMLResponse:
        messages = get_flashes(request)
        return html_response(FlashMessages(messages=messages), title="Buckets")

    @app.get("/other")
    async def other(request: Request) -> HTMLResponse:
        return HTMLResponse("other")

    @app.get("/inline")
    async def inline(request: Request) -> HTMLResponse:
        flash(request, "agora", "info")
        return html_response(FlashMessages(messages=get_flashes(request)), title="I")

    return app


def test_message_survives_the_redirect_and_is_read_once() -> None:
    with TestClient(_app()) as client:
        page = client.post("/buckets")
        assert page.status_code == 200
        assert "Bucket criado." in page.text
        assert "tui-alert--success" in page.text
        assert client.cookies.get(FLASH_COOKIE_NAME) is None
        again = client.get("/buckets")
    assert "Bucket criado." not in again.text


def test_cookie_is_http_only_and_same_site_lax() -> None:
    with TestClient(_app(), follow_redirects=False) as client:
        response = client.post("/buckets")
    header = response.headers["set-cookie"]
    assert header.startswith(f"{FLASH_COOKIE_NAME}=")
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Max-Age=300" in header


def test_unread_messages_survive_a_page_that_does_not_read_them() -> None:
    with TestClient(_app(), follow_redirects=False) as client:
        client.post("/buckets")
        response = client.get("/other")
        assert "set-cookie" not in response.headers
        page = client.get("/buckets")
    assert "Bucket criado." in page.text


def test_messages_keep_their_order() -> None:
    with TestClient(_app()) as client:
        page = client.post("/twice")
    assert page.text.index("primeira") < page.text.index("segunda")
    assert "tui-alert--warning" in page.text


def test_message_queued_and_read_in_the_same_request_does_not_persist() -> None:
    with TestClient(_app()) as client:
        page = client.get("/inline")
        assert "agora" in page.text
        assert client.cookies.get(FLASH_COOKIE_NAME) is None


def test_forged_cookie_is_ignored_and_deleted() -> None:
    forger = FlashMiddleware(_noop_app, secret="another-secret-of-16+")
    forged = forger.encode([FlashMessage(message="Pague aqui: evil.example")])
    with TestClient(_app(), follow_redirects=False) as client:
        client.cookies.set(FLASH_COOKIE_NAME, forged)
        page = client.get("/buckets")
    assert "evil.example" not in page.text
    assert page.headers["set-cookie"].startswith(f'{FLASH_COOKIE_NAME}="";')
    assert "Max-Age=0" in page.headers["set-cookie"]


def test_tampered_payload_fails_the_signature() -> None:
    middleware = _middleware()
    value = middleware.encode([FlashMessage(message="ok")])
    payload, _, signature = value.partition(".")
    tampered = payload[:-2] + ("AA" if payload[-2:] != "AA" else "BB")
    assert middleware.decode(f"{tampered}.{signature}") == []
    assert middleware.decode(value) == [FlashMessage(message="ok")]


@pytest.mark.parametrize("value", ["", ".", "abc", "abc.", ".abc", "a.b.c"])
def test_malformed_cookie_decodes_to_nothing(value: str) -> None:
    assert _middleware().decode(value) == []


def test_expired_cookie_decodes_to_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = _middleware(max_age=60)
    value = middleware.encode([FlashMessage(message="velha")])
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 61)
    assert middleware.decode(value) == []


def test_signed_entry_with_unknown_variant_is_dropped() -> None:
    middleware = _middleware()
    body = json.dumps({"t": int(time.time()), "m": [["ok", "error"], ["x", "evil"]]})
    payload = base64.urlsafe_b64encode(body.encode()).rstrip(b"=").decode()
    value = f"{payload}.{middleware._sign(payload)}"
    assert middleware.decode(value) == [FlashMessage(message="ok", variant="error")]


def test_long_message_is_truncated() -> None:
    app = FastAPI()
    app.add_middleware(FlashMiddleware, secret=SECRET, secure=False)

    @app.get("/")
    async def root(request: Request) -> HTMLResponse:
        flash(request, "x" * (MAX_FLASH_MESSAGE_LENGTH + 50))
        message = get_flashes(request)[0].message
        return HTMLResponse(str(len(message)) + message[-1])

    with TestClient(app) as client:
        assert client.get("/").text == f"{MAX_FLASH_MESSAGE_LENGTH}…"


def test_oversized_queue_drops_the_oldest_first() -> None:
    middleware = _middleware()
    messages = [FlashMessage(message=f"{index:03d}" + "é" * 400) for index in range(20)]
    value = middleware.encode(messages)
    assert len(value) <= MAX_FLASH_COOKIE_BYTES
    decoded = middleware.decode(value)
    assert decoded
    assert decoded[-1] == messages[-1]
    assert decoded[0] != messages[0]


def test_secret_must_be_long_enough() -> None:
    with pytest.raises(ValueError, match="at least 16"):
        FlashMiddleware(_noop_app, secret="short")


def test_max_age_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        _middleware(max_age=0)


def test_flash_without_middleware_raises() -> None:
    app = FastAPI()

    @app.get("/")
    async def root(request: Request) -> HTMLResponse:
        assert flash_enabled(request) is False
        with pytest.raises(RuntimeError, match="FlashMiddleware"):
            flash(request, "perdida")
        with pytest.raises(RuntimeError, match="FlashMiddleware"):
            get_flashes(request)
        return HTMLResponse("ok")

    with TestClient(app) as client:
        assert client.get("/").text == "ok"


def test_flash_messages_component_escapes_text() -> None:
    body = bytes(
        html_response(
            FlashMessages(messages=[FlashMessage(message="<script>x</script>")]),
            document=False,
        ).body,
    ).decode()
    assert "&lt;script&gt;" in body
    assert "<script>" not in body


def test_empty_flash_messages_renders_an_empty_wrapper() -> None:
    body = bytes(html_response(FlashMessages(), document=False).body).decode()
    assert body == '<div class="tui-flash"></div>'
