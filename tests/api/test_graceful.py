"""Tests for the GracefulShutdownMiddleware."""

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from tempest_fastapi_sdk.api.middlewares.graceful import (
    GracefulShutdownMiddleware,
)


def _app(shutdown: GracefulShutdownMiddleware) -> FastAPI:
    app = FastAPI()
    app.add_middleware(BaseHTTPMiddleware, dispatch=shutdown.dispatch)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "yes"}

    return app


class TestServing:
    def test_serves_normally_before_drain(self) -> None:
        shutdown = GracefulShutdownMiddleware()
        with TestClient(_app(shutdown)) as client:
            response = client.get("/ping")
            assert response.status_code == 200
            assert shutdown.is_draining is False

    def test_rejects_with_503_while_draining(self) -> None:
        shutdown = GracefulShutdownMiddleware(retry_after=7)
        with TestClient(_app(shutdown)) as client:
            shutdown.begin_drain()
            response = client.get("/ping")
            assert response.status_code == 503
            assert response.headers["retry-after"] == "7"
            assert response.json()["detail"] == "Server is shutting down."

    def test_exempt_path_still_served_while_draining(self) -> None:
        shutdown = GracefulShutdownMiddleware(exempt_paths=("/ping",))
        with TestClient(_app(shutdown)) as client:
            shutdown.begin_drain()
            assert client.get("/ping").status_code == 200


class TestDrainState:
    def test_begin_drain_is_idempotent(self) -> None:
        shutdown = GracefulShutdownMiddleware()
        shutdown.begin_drain()
        shutdown.begin_drain()
        assert shutdown.is_draining is True

    async def test_wait_drained_returns_true_when_idle(self) -> None:
        shutdown = GracefulShutdownMiddleware()
        assert await shutdown.wait_drained() is True

    async def test_wait_drained_times_out_with_in_flight(self) -> None:
        shutdown = GracefulShutdownMiddleware(drain_timeout=0.05)
        # Simulate an in-flight request without going through dispatch.
        shutdown._in_flight = 1
        shutdown._idle.clear()
        assert await shutdown.wait_drained() is False


class TestInFlightCounting:
    def test_in_flight_returns_to_zero_after_request(self) -> None:
        shutdown = GracefulShutdownMiddleware()
        with TestClient(_app(shutdown)) as client:
            client.get("/ping")
            assert shutdown.in_flight == 0


class TestSignalHandlers:
    def test_install_is_best_effort(self) -> None:
        shutdown = GracefulShutdownMiddleware()
        # Should not raise even though pytest may run off the main thread.
        shutdown.install_signal_handlers()
