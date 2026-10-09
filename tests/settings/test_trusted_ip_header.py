"""Tests for ``ServerSettings.TRUSTED_IP_HEADER``."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tempest_fastapi_sdk import (
    AccessLogMiddleware,
    BaseAppSettings,
    RateLimitMiddleware,
    ServerSettings,
)

LOGGER_NAME: str = "tempest.access.trusted-ip"
"""Logger the access log writes to in these tests."""


class Settings(ServerSettings, BaseAppSettings):
    """A service composing the server mixin."""


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run away from any ``.env`` with the variable unset.

    Args:
        tmp_path (Path): Empty working directory.
        monkeypatch (pytest.MonkeyPatch): Environment patcher.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRUSTED_IP_HEADER", raising=False)


class TestField:
    def test_default_is_none(self) -> None:
        assert Settings().TRUSTED_IP_HEADER is None

    def test_reads_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRUSTED_IP_HEADER", "x-real-ip")
        assert Settings().TRUSTED_IP_HEADER == "x-real-ip"

    def test_is_lowercased(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRUSTED_IP_HEADER", "CF-Connecting-IP")
        assert Settings().TRUSTED_IP_HEADER == "cf-connecting-ip"

    def test_empty_means_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRUSTED_IP_HEADER", "")
        assert Settings().TRUSTED_IP_HEADER is None

    @pytest.mark.parametrize(
        "name", ["x-forwarded-for", "X-Forwarded-For", "Forwarded"]
    )
    def test_spoofable_headers_are_refused_with_the_reason(
        self, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        monkeypatch.setenv("TRUSTED_IP_HEADER", name)
        with pytest.raises(ValidationError) as caught:
            Settings()
        message = str(caught.value)
        assert "TRUSTED_IP_HEADER" in message
        assert "proxies append to this header" in message


class TestReachesTheMiddlewares:
    def test_rate_limit_and_access_log_see_the_header_address(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """One setting, passed to both, keys both on the edge's address.

        Two clients behind the same proxy (same TCP peer) get separate
        rate-limit buckets, and the access log records each one's own
        address instead of the proxy's.
        """
        monkeypatch.setenv("TRUSTED_IP_HEADER", "X-Real-IP")
        settings = Settings()

        app = FastAPI()

        @app.get("/ping")
        def ping() -> dict[str, bool]:
            return {"ok": True}

        app.add_middleware(
            RateLimitMiddleware,
            max_requests=1,
            window_seconds=60.0,
            trusted_ip_header=settings.TRUSTED_IP_HEADER,
        )
        app.add_middleware(
            AccessLogMiddleware,
            logger_name=LOGGER_NAME,
            trusted_ip_header=settings.TRUSTED_IP_HEADER,
        )

        with (
            caplog.at_level(logging.INFO, logger=LOGGER_NAME),
            TestClient(app) as client,
        ):
            first = client.get("/ping", headers={"x-real-ip": "1.1.1.1"})
            other = client.get("/ping", headers={"x-real-ip": "2.2.2.2"})
            again = client.get("/ping", headers={"x-real-ip": "1.1.1.1"})

        assert [first.status_code, other.status_code, again.status_code] == [
            200,
            200,
            429,
        ]
        logged = [
            getattr(record, "client_ip", None)
            for record in caplog.records
            if record.name == LOGGER_NAME
        ]
        assert logged == ["1.1.1.1", "2.2.2.2", "1.1.1.1"]
