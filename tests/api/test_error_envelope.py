"""Tests for the 500 envelope passing through the app's middleware stack."""

from collections.abc import AsyncIterator, Callable

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket

from tempest_fastapi_sdk import (
    ErrorEnvelopeMiddleware,
    RequestIDMiddleware,
    apply_cors,
    register_exception_handlers,
)

ALLOWED: str = "https://front.example"
FOREIGN: str = "https://evil.example"


def _routes(app: FastAPI) -> FastAPI:
    """Attach a route that fails, one that succeeds and one that streams.

    Args:
        app (FastAPI): The app to decorate.

    Returns:
        FastAPI: The same app.
    """

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    @app.get("/ok")
    async def ok() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        async def body() -> AsyncIterator[bytes]:
            yield b"first chunk"
            raise RuntimeError("mid-stream")

        return StreamingResponse(body())

    return app


def _cors_then_handlers() -> FastAPI:
    """Build the app in the order the CLI template used before the fix.

    Returns:
        FastAPI: RequestID, CORS, then the exception handlers.
    """
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    apply_cors(app, origins=[ALLOWED])
    register_exception_handlers(app)
    return _routes(app)


def _handlers_then_cors() -> FastAPI:
    """Build the app registering the handlers before any middleware.

    Returns:
        FastAPI: The exception handlers, then RequestID and CORS.
    """
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(RequestIDMiddleware)
    apply_cors(app, origins=[ALLOWED])
    return _routes(app)


BUILDERS: list[Callable[[], FastAPI]] = [_cors_then_handlers, _handlers_then_cors]


@pytest.mark.parametrize("build", BUILDERS)
class TestUnhandledErrorInsideCors:
    """The 500 envelope is decorated by every middleware, in any order."""

    def test_allowed_origin_gets_the_cors_header(
        self,
        build: Callable[[], FastAPI],
    ) -> None:
        client = TestClient(build(), raise_server_exceptions=False)
        response = client.get("/boom", headers={"Origin": ALLOWED})
        assert response.status_code == 500
        assert response.headers["access-control-allow-origin"] == ALLOWED
        assert response.json()["code"] == "INTERNAL_SERVER_ERROR"

    def test_foreign_origin_still_gets_no_cors_header(
        self,
        build: Callable[[], FastAPI],
    ) -> None:
        client = TestClient(build(), raise_server_exceptions=False)
        response = client.get("/boom", headers={"Origin": FOREIGN})
        assert response.status_code == 500
        assert "access-control-allow-origin" not in response.headers

    def test_success_path_keeps_the_cors_header(
        self,
        build: Callable[[], FastAPI],
    ) -> None:
        client = TestClient(build())
        response = client.get("/ok", headers={"Origin": ALLOWED})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == ALLOWED

    def test_request_id_header_matches_the_envelope(
        self,
        build: Callable[[], FastAPI],
    ) -> None:
        client = TestClient(build(), raise_server_exceptions=False)
        response = client.get("/boom", headers={"Origin": ALLOWED})
        request_id = response.headers["x-request-id"]
        assert response.json()["details"]["request_id"] == request_id

    def test_default_test_client_still_raises(
        self,
        build: Callable[[], FastAPI],
    ) -> None:
        client = TestClient(build())
        with pytest.raises(RuntimeError, match="boom"):
            client.get("/boom", headers={"Origin": ALLOWED})

    def test_failure_after_response_started_is_reraised(
        self,
        build: Callable[[], FastAPI],
    ) -> None:
        client = TestClient(build())
        with pytest.raises(RuntimeError, match="mid-stream"):
            client.get("/stream", headers={"Origin": ALLOWED})


class TestRegistration:
    """How ``register_exception_handlers`` installs the layer."""

    def test_layer_is_the_innermost_user_middleware(self) -> None:
        app = _handlers_then_cors()
        assert app.user_middleware[-1].cls is ErrorEnvelopeMiddleware

    def test_registering_twice_installs_one_layer(self) -> None:
        app = FastAPI()
        register_exception_handlers(app)
        register_exception_handlers(app, include_traceback=True)
        layers = [m for m in app.user_middleware if m.cls is ErrorEnvelopeMiddleware]
        assert len(layers) == 1
        client = TestClient(_routes(app), raise_server_exceptions=False)
        assert "traceback" in client.get("/boom").json()["details"]

    def test_registering_on_a_started_app_raises(self) -> None:
        app = _routes(FastAPI())
        TestClient(app).get("/ok")
        before = dict(app.exception_handlers)
        middleware_before = list(app.user_middleware)
        with pytest.raises(RuntimeError, match="before the application starts"):
            register_exception_handlers(app)
        assert app.exception_handlers == before
        assert app.user_middleware == middleware_before

    def test_a_middleware_failure_still_reaches_the_catch_all(self) -> None:
        class Failing:
            """A pure ASGI middleware that raises before calling the app."""

            def __init__(self, app: ASGIApp) -> None:
                """Wrap ``app``.

                Args:
                    app (ASGIApp): The downstream app.
                """
                self.app: ASGIApp = app

            async def __call__(
                self,
                scope: Scope,
                receive: Receive,
                send: Send,
            ) -> None:
                """Raise on every HTTP request.

                Args:
                    scope (Scope): The ASGI scope.
                    receive (Receive): The receive channel.
                    send (Send): The send channel.

                Raises:
                    RuntimeError: Always, for HTTP.
                """
                if scope["type"] == "http":
                    raise RuntimeError("middleware failed")
                await self.app(scope, receive, send)

        app = _routes(FastAPI())
        register_exception_handlers(app)
        app.add_middleware(Failing)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/ok")
        assert response.status_code == 500
        assert response.json()["code"] == "INTERNAL_SERVER_ERROR"


class TestMiddlewareStandalone:
    """The middleware used directly, with a caller-supplied handler."""

    @staticmethod
    async def _handler(request: Request, exc: Exception) -> Response:
        return JSONResponse({"caught": type(exc).__name__}, status_code=500)

    def test_non_http_scope_passes_through(self) -> None:
        app = FastAPI()
        app.add_middleware(ErrorEnvelopeMiddleware, handler=self._handler)

        @app.websocket("/ws")
        async def ws(websocket: WebSocket) -> None:
            await websocket.accept()
            await websocket.send_text("hi")
            await websocket.close()

        with TestClient(app).websocket_connect("/ws") as socket:
            assert socket.receive_text() == "hi"

    def test_renders_the_supplied_handler(self) -> None:
        app = _routes(FastAPI())
        app.add_middleware(ErrorEnvelopeMiddleware, handler=self._handler)
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/boom").json() == {"caught": "RuntimeError"}


class TestEachFailureIsHandledOnce:
    """``ServerErrorMiddleware`` calls its handler even after a response started.

    Without the flag on the exception, every 500 the envelope layer
    answered was logged and reported to ``on_server_error`` a second time
    by the handler left in ``ServerErrorMiddleware``.
    """

    def _app(self, calls: list[str]) -> FastAPI:
        """Build an app whose ``on_server_error`` records each call.

        Args:
            calls (list[str]): Receives the exception message per call.

        Returns:
            FastAPI: The app with the failing routes.
        """

        async def record(request: Request, exc: Exception) -> None:
            calls.append(str(exc))

        app = FastAPI()
        apply_cors(app, origins=[ALLOWED])
        register_exception_handlers(app, on_server_error=record)
        return _routes(app)

    def test_on_server_error_fires_once(self) -> None:
        calls: list[str] = []
        client = TestClient(self._app(calls), raise_server_exceptions=False)
        client.get("/boom", headers={"Origin": ALLOWED})
        assert calls == ["boom"]

    def test_on_server_error_fires_once_when_the_client_raises(self) -> None:
        calls: list[str] = []
        with pytest.raises(RuntimeError):
            TestClient(self._app(calls)).get("/boom")
        assert calls == ["boom"]

    def test_a_mid_stream_failure_is_still_logged_once(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with (
            caplog.at_level("ERROR", logger="tempest_fastapi_sdk.api.handlers"),
            pytest.raises(RuntimeError),
        ):
            TestClient(self._app([])).get("/stream")
        unhandled = [r for r in caplog.records if "Unhandled exception" in r.message]
        assert len(unhandled) == 1

    def test_a_mid_stream_failure_reaches_on_server_error_once(self) -> None:
        calls: list[str] = []
        with pytest.raises(RuntimeError):
            TestClient(self._app(calls)).get("/stream")
        assert calls == ["mid-stream"]

    def test_the_error_is_logged_once(self, caplog: pytest.LogCaptureFixture) -> None:
        client = TestClient(self._app([]), raise_server_exceptions=False)
        with caplog.at_level("ERROR", logger="tempest_fastapi_sdk.api.handlers"):
            client.get("/boom")
        unhandled = [r for r in caplog.records if "Unhandled exception" in r.message]
        assert len(unhandled) == 1
