"""Tests for the run_server helper."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from tempest_fastapi_sdk import ServerSettings, run_server


class TestRunServer:
    """Verify run_server resolves defaults / overrides correctly."""

    def test_uses_default_host_port_when_no_settings(self) -> None:
        with patch("uvicorn.run") as mocked:
            run_server("module:app")
        mocked.assert_called_once_with(
            "module:app",
            host="127.0.0.1",
            port=8000,
            reload=False,
        )

    def test_pulls_values_from_settings(self) -> None:
        settings = ServerSettings(
            SERVER_HOST="0.0.0.0",
            SERVER_PORT=9000,
            SERVER_RELOAD=True,
        )
        with patch("uvicorn.run") as mocked:
            run_server("module:app", settings=settings)
        mocked.assert_called_once_with(
            "module:app",
            host="0.0.0.0",
            port=9000,
            reload=True,
        )

    def test_explicit_kwargs_override_settings(self) -> None:
        settings = ServerSettings(
            SERVER_HOST="0.0.0.0",
            SERVER_PORT=9000,
            SERVER_RELOAD=True,
        )
        with patch("uvicorn.run") as mocked:
            run_server(
                "module:app",
                settings=settings,
                host="127.0.0.1",
                port=4000,
                reload=False,
            )
        mocked.assert_called_once_with(
            "module:app",
            host="127.0.0.1",
            port=4000,
            reload=False,
        )

    def test_forwards_extra_uvicorn_kwargs(self) -> None:
        with patch("uvicorn.run") as mocked:
            run_server("module:app", workers=4, log_level="debug")
        args, kwargs = mocked.call_args
        assert args == ("module:app",)
        assert kwargs["workers"] == 4
        assert kwargs["log_level"] == "debug"

    def test_accepts_fastapi_instance(self) -> None:
        sentinel: Any = object()
        with patch("uvicorn.run") as mocked:
            run_server(sentinel)
        mocked.assert_called_once_with(
            sentinel,
            host="127.0.0.1",
            port=8000,
            reload=False,
        )
