"""Tests for ``tempest email`` and ``tempest storage``.

No SMTP server and no object store are started: what these commands own
is the settings they build the client from, the message they print, and
the exit code they pick. The clients themselves are the SDK's, and have
their own tests.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()

_EMAIL_SETTINGS = """
from tempest_fastapi_sdk.settings import EmailSettings, ServerSettings


class Settings(ServerSettings, EmailSettings):
    pass


settings = Settings()
"""

_MINIO_SETTINGS = """
from tempest_fastapi_sdk.settings import MinIOSettings, ServerSettings


class Settings(ServerSettings, MinIOSettings):
    pass


settings = Settings()
"""

_BARE_SETTINGS = """
from tempest_fastapi_sdk.settings import ServerSettings


class Settings(ServerSettings):
    pass


settings = Settings()
"""


def _write_project(root: Path, settings_source: str) -> None:
    """Write a project whose settings module holds ``settings_source``.

    Args:
        root (Path): Project root.
        settings_source (str): Body of ``src/core/settings.py``.
    """
    (root / "src" / "core").mkdir(parents=True, exist_ok=True)
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "core" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "core" / "settings.py").write_text(
        settings_source,
        encoding="utf-8",
    )


@pytest.fixture
def forget_project() -> None:
    """Drop the tmp project's package between tests."""
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]
    yield
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]


class _FakeMailer:
    """Stand-in for ``EmailUtils`` recording what it was asked to send."""

    sent: ClassVar[list[dict[str, Any]]] = []
    failure: Exception | None = None

    def __init__(self, **kwargs: Any) -> None:
        """Record the construction kwargs."""
        self.kwargs = kwargs

    async def send(self, to: str, subject: str, body: str, **kwargs: Any) -> None:
        """Record the message, or raise the configured failure."""
        if _FakeMailer.failure is not None:
            raise _FakeMailer.failure
        _FakeMailer.sent.append({"to": to, "subject": subject, "body": body, **kwargs})


@pytest.fixture
def mailer(monkeypatch: pytest.MonkeyPatch) -> type[_FakeMailer]:
    """Swap ``EmailUtils`` for the recorder above."""
    _FakeMailer.sent = []
    _FakeMailer.failure = None
    monkeypatch.setattr("tempest_fastapi_sdk.EmailUtils", _FakeMailer)
    return _FakeMailer


class TestEmailTest:
    def test_sends_through_the_project_settings(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        forget_project: None,
        mailer: type[_FakeMailer],
    ) -> None:
        _write_project(tmp_path, _EMAIL_SETTINGS)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_PORT", "587")

        result = runner.invoke(app, ["email", "test", "--to", "ana@example.com"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert mailer.sent[0]["to"] == "ana@example.com"
        assert "smtp.example.com:587" in result.stdout

    def test_html_adds_the_alternative_part(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        forget_project: None,
        mailer: type[_FakeMailer],
    ) -> None:
        _write_project(tmp_path, _EMAIL_SETTINGS)
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["email", "test", "--to", "ana@example.com", "--html"])
        assert mailer.sent[0]["html"] is not None

    def test_refusal_names_the_tls_pair(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        forget_project: None,
        mailer: type[_FakeMailer],
    ) -> None:
        """STARTTLS vs implicit TLS is the mistake this command exists for."""
        _write_project(tmp_path, _EMAIL_SETTINGS)
        monkeypatch.chdir(tmp_path)
        mailer.failure = ConnectionRefusedError("connection refused")

        result = runner.invoke(app, ["email", "test", "--to", "ana@example.com"])
        assert result.exit_code == 1
        assert "SMTP_USE_TLS" in result.stderr
        assert "SMTP_USE_SSL" in result.stderr

    def test_project_without_email_settings_exits_two(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        forget_project: None,
        mailer: type[_FakeMailer],
    ) -> None:
        """Composing only the mixins you use is ordinary, not broken."""
        _write_project(tmp_path, _BARE_SETTINGS)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["email", "test", "--to", "ana@example.com"])
        assert result.exit_code == 2
        assert "EmailSettings" in result.stderr
        assert mailer.sent == []


class _FakeStore:
    """Stand-in for ``AsyncMinIOClient`` with an in-memory bucket."""

    objects: ClassVar[dict[str, bytes]] = {}
    buckets: ClassVar[list[str]] = ["uploads"]
    calls: ClassVar[list[tuple[str, Any]]] = []

    def __init__(self, **kwargs: Any) -> None:
        """Record the construction kwargs."""
        self.kwargs = kwargs

    async def __aenter__(self) -> _FakeStore:
        """Enter the async context, returning self."""
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        """Leave the async context."""
        return None

    async def bucket_exists(self, bucket: str | None = None) -> bool:
        """Report whether the named bucket is known."""
        return (bucket or "uploads") in _FakeStore.buckets

    async def list_buckets(self) -> list[str]:
        """List the known buckets."""
        return list(_FakeStore.buckets)

    async def list_objects(
        self,
        prefix: str = "",
        *,
        bucket: str | None = None,
        recursive: bool = True,
    ) -> list[str]:
        """List keys under ``prefix``."""
        return [key for key in _FakeStore.objects if key.startswith(prefix)]

    async def fput_object(
        self,
        key: str,
        file_path: str,
        *,
        bucket: str | None = None,
    ) -> str:
        """Store the file's bytes under ``key``."""
        _FakeStore.objects[key] = Path(file_path).read_bytes()
        return "etag-1"

    async def fget_object(
        self,
        key: str,
        file_path: str,
        *,
        bucket: str | None = None,
    ) -> Path:
        """Write the stored bytes to ``file_path``."""
        target = Path(file_path)
        target.write_bytes(_FakeStore.objects[key])
        return target

    async def presigned_get_url(self, key: str, **kwargs: Any) -> str:
        """Return a fake signed URL."""
        _FakeStore.calls.append(("presign", kwargs))
        return f"https://cdn.example.com/{key}?signature=abc"

    async def remove_object(self, key: str, *, bucket: str | None = None) -> None:
        """Delete the stored key."""
        _FakeStore.objects.pop(key, None)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> type[_FakeStore]:
    """Swap ``AsyncMinIOClient`` for the in-memory store above."""
    _FakeStore.objects = {}
    _FakeStore.buckets = ["uploads"]
    _FakeStore.calls = []
    monkeypatch.setattr(
        "tempest_fastapi_sdk.storage.AsyncMinIOClient",
        _FakeStore,
    )
    return _FakeStore


@pytest.fixture
def minio_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    forget_project: None,
) -> Path:
    """Stand inside a project composing ``MinIOSettings``."""
    _write_project(tmp_path, _MINIO_SETTINGS)
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestStorage:
    def test_check_reports_the_bucket(
        self,
        minio_project: Path,
        store: type[_FakeStore],
    ) -> None:
        result = runner.invoke(app, ["storage", "check"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "uploads exists" in result.stdout

    def test_check_exits_one_on_a_missing_bucket(
        self,
        minio_project: Path,
        store: type[_FakeStore],
    ) -> None:
        result = runner.invoke(app, ["storage", "check", "--bucket", "absent"])
        assert result.exit_code == 1
        assert "does not exist" in result.stderr

    def test_put_then_ls_then_get(
        self,
        minio_project: Path,
        store: type[_FakeStore],
        tmp_path: Path,
    ) -> None:
        source = tmp_path / "note.txt"
        source.write_text("hello", encoding="utf-8")

        put = runner.invoke(app, ["storage", "put", str(source)])
        assert put.exit_code == 0, put.stdout + put.stderr

        listing = runner.invoke(app, ["storage", "ls"])
        assert "note.txt" in listing.stdout

        target = tmp_path / "back.txt"
        got = runner.invoke(
            app,
            ["storage", "get", "note.txt", "--out", str(target)],
        )
        assert got.exit_code == 0, got.stdout + got.stderr
        assert target.read_text(encoding="utf-8") == "hello"

    def test_empty_bucket_lists_nothing_and_succeeds(
        self,
        minio_project: Path,
        store: type[_FakeStore],
    ) -> None:
        result = runner.invoke(app, ["storage", "ls"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert result.stdout.strip() == ""

    def test_put_of_a_missing_file_is_a_usage_error(
        self,
        minio_project: Path,
        store: type[_FakeStore],
        tmp_path: Path,
    ) -> None:
        result = runner.invoke(app, ["storage", "put", str(tmp_path / "absent.txt")])
        assert result.exit_code == 2

    def test_presign_says_which_host_it_signed_for(
        self,
        minio_project: Path,
        store: type[_FakeStore],
    ) -> None:
        """A URL signed for the internal endpoint is valid and unusable."""
        result = runner.invoke(app, ["storage", "presign", "note.txt"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "https://cdn.example.com/note.txt" in result.stdout
        assert "signed for" in result.stderr

    def test_rm_deletes(
        self,
        minio_project: Path,
        store: type[_FakeStore],
        tmp_path: Path,
    ) -> None:
        source = tmp_path / "note.txt"
        source.write_text("hello", encoding="utf-8")
        runner.invoke(app, ["storage", "put", str(source)])

        result = runner.invoke(app, ["storage", "rm", "note.txt"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert runner.invoke(app, ["storage", "ls"]).stdout.strip() == ""

    def test_project_without_minio_settings_exits_two(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        forget_project: None,
        store: type[_FakeStore],
    ) -> None:
        _write_project(tmp_path, _BARE_SETTINGS)
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["storage", "ls"])
        assert result.exit_code == 2
        assert "MinIOSettings" in result.stderr
