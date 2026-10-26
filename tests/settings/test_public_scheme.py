"""``ServerSettings.SERVER_PUBLIC_SCHEME``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tempest_fastapi_sdk import BaseAppSettings, ServerSettings


class _Settings(ServerSettings, BaseAppSettings):
    """Settings with only the server mixin."""


def test_default_keeps_the_connection_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SERVER_PUBLIC_SCHEME", raising=False)
    assert _Settings(_env_file=None).SERVER_PUBLIC_SCHEME == ""


def test_https_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVER_PUBLIC_SCHEME", "https")
    assert _Settings(_env_file=None).SERVER_PUBLIC_SCHEME == "https"


def test_an_unknown_scheme_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVER_PUBLIC_SCHEME", "ftp")
    with pytest.raises(ValidationError, match="SERVER_PUBLIC_SCHEME"):
        _Settings(_env_file=None)
