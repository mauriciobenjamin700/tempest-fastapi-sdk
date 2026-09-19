"""The path surface translates first and confines second."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tempest_fastapi_sdk.hostbridge import (
    HostUnavailableError,
    InvalidHostPathError,
    ensure_path_allowed,
    to_windows_path,
    to_wsl_path,
)
from tests.hostbridge.conftest import fake_process


class TestEnsurePathAllowed:
    """Confinement is decided on the resolved path, never on the string."""

    def test_accepts_a_path_under_a_base(self, tmp_path: Path) -> None:
        """A file inside an allowed base resolves and is returned."""
        target = tmp_path / "nested" / "file.txt"
        assert ensure_path_allowed(target, (str(tmp_path),)) == target.resolve()

    def test_accepts_the_base_itself(self, tmp_path: Path) -> None:
        """The base directory is inside itself, which listing it depends on."""
        assert ensure_path_allowed(tmp_path, (str(tmp_path),)) == tmp_path.resolve()

    def test_rejects_a_path_outside_every_base(self, tmp_path: Path) -> None:
        """A path under no base is refused."""
        with pytest.raises(InvalidHostPathError):
            ensure_path_allowed(Path("/etc/passwd"), (str(tmp_path),))

    def test_collapses_traversal_before_deciding(self, tmp_path: Path) -> None:
        """``base/../etc/passwd`` is judged as ``/etc/passwd``, not by prefix.

        This is the whole reason the resolve happens first. Checked on the
        raw string, the path starts with an allowed base and passes.
        """
        escaped = Path(*[".."] * len(tmp_path.resolve().parts[1:]), "etc", "passwd")
        with pytest.raises(InvalidHostPathError) as excinfo:
            ensure_path_allowed(tmp_path / escaped, (str(tmp_path),))
        assert excinfo.value.message_params["path"] == "/etc/passwd"

    def test_rejects_a_sibling_sharing_the_base_prefix(self, tmp_path: Path) -> None:
        """``/allowed-evil`` is not under ``/allowed`` despite the prefix."""
        base = tmp_path / "allowed"
        base.mkdir()
        with pytest.raises(InvalidHostPathError):
            ensure_path_allowed(tmp_path / "allowed-evil" / "x", (str(base),))

    def test_empty_bases_deny_everything(self, tmp_path: Path) -> None:
        """An unconfigured bridge refuses every path rather than allowing all."""
        with pytest.raises(InvalidHostPathError):
            ensure_path_allowed(tmp_path / "file.txt", ())


class TestPathTranslation:
    """``wslpath`` is invoked only for a path that needs translating."""

    async def test_wsl_form_does_not_shell_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``/mnt/c`` path is already usable, so no process is spawned."""
        spawn = AsyncMock()
        monkeypatch.setattr(
            "tempest_fastapi_sdk.hostbridge.paths.asyncio.create_subprocess_exec", spawn
        )
        assert await to_wsl_path("/mnt/c/Users/me") == Path("/mnt/c/Users/me")
        spawn.assert_not_called()

    async def test_windows_form_is_translated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A drive-letter path is handed to ``wslpath -u``."""
        spawn = AsyncMock(return_value=fake_process(0, stdout=b"/mnt/c/Users/me\n"))
        monkeypatch.setattr(
            "tempest_fastapi_sdk.hostbridge.paths.asyncio.create_subprocess_exec", spawn
        )
        assert await to_wsl_path("C:\\Users\\me") == Path("/mnt/c/Users/me")
        assert spawn.await_args.args[:2] == ("wslpath", "-u")

    async def test_windows_form_is_returned_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``to_windows_path`` on an already-Windows path spawns nothing."""
        spawn = AsyncMock()
        monkeypatch.setattr(
            "tempest_fastapi_sdk.hostbridge.paths.asyncio.create_subprocess_exec", spawn
        )
        assert await to_windows_path("C:\\Users\\me") == "C:\\Users\\me"
        spawn.assert_not_called()

    async def test_failed_translation_raises_invalid_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-zero ``wslpath`` is a bad path, and its stderr is kept."""
        monkeypatch.setattr(
            "tempest_fastapi_sdk.hostbridge.paths.asyncio.create_subprocess_exec",
            AsyncMock(return_value=fake_process(1, stderr=b"no such mount")),
        )
        with pytest.raises(InvalidHostPathError) as excinfo:
            await to_wsl_path("Z:\\nope")
        assert excinfo.value.details["reason"] == "no such mount"

    async def test_missing_wslpath_is_unavailable_not_invalid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Off WSL the tool is absent — a 503, not a rejected path.

        The distinction is what lets a caller degrade: the path may have
        been perfectly fine, and this environment simply cannot resolve it.
        """
        monkeypatch.setattr(
            "tempest_fastapi_sdk.hostbridge.paths.asyncio.create_subprocess_exec",
            AsyncMock(side_effect=FileNotFoundError("wslpath")),
        )
        with pytest.raises(HostUnavailableError):
            await to_wsl_path("C:\\Users\\me")
