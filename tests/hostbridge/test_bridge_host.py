"""The system and command surface: power actions, host info, execution."""

import json
from unittest.mock import AsyncMock

import pytest

from tempest_fastapi_sdk.hostbridge import (
    CommandSchema,
    FilePickSchema,
    HostBridge,
    HostCommandError,
    HostCommandTimeoutError,
    PowerActionSchema,
)
from tests.hostbridge.conftest import fake_process

SPAWN = "tempest_fastapi_sdk.hostbridge.shell.asyncio.create_subprocess_exec"
HOST_INFO_JSON = json.dumps(
    {
        "computer_name": "DESKTOP-1",
        "user_name": "me",
        "os_version": "Microsoft Windows 11 10.0.26100",
        "uptime_seconds": 8123.5,
    }
).encode()


class TestPowerActions:
    """Each power verb assembles its own arguments and insists on success."""

    async def test_shutdown_passes_delay_force_and_message(
        self, bridge: HostBridge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every field of the payload reaches ``shutdown.exe``."""
        spawn = AsyncMock(return_value=fake_process(0))
        monkeypatch.setattr(SPAWN, spawn)
        await bridge.shutdown(
            PowerActionSchema(seconds=60, force=True, message="saving up")
        )
        assert spawn.await_args.args == (
            "shutdown.exe",
            "/s",
            "/t",
            "60",
            "/f",
            "/c",
            "saving up",
        )

    async def test_shutdown_defaults_to_immediate_and_gentle(
        self, bridge: HostBridge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No payload means now, without discarding anyone's unsaved work."""
        spawn = AsyncMock(return_value=fake_process(0))
        monkeypatch.setattr(SPAWN, spawn)
        await bridge.shutdown()
        assert spawn.await_args.args == ("shutdown.exe", "/s", "/t", "0")

    async def test_restart_uses_the_restart_verb(
        self, bridge: HostBridge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``/r`` is what separates a restart from a shutdown."""
        spawn = AsyncMock(return_value=fake_process(0))
        monkeypatch.setattr(SPAWN, spawn)
        await bridge.restart(PowerActionSchema())
        assert spawn.await_args.args[:2] == ("shutdown.exe", "/r")

    @pytest.mark.parametrize(
        ("method", "expected"),
        [
            ("abort_shutdown", ("shutdown.exe", "/a")),
            ("lock", ("rundll32.exe", "user32.dll,LockWorkStation")),
            ("logoff", ("shutdown.exe", "/l")),
        ],
    )
    async def test_the_remaining_verbs(
        self,
        bridge: HostBridge,
        monkeypatch: pytest.MonkeyPatch,
        method: str,
        expected: tuple[str, ...],
    ) -> None:
        """Abort, lock and logoff each invoke their own tool."""
        spawn = AsyncMock(return_value=fake_process(0))
        monkeypatch.setattr(SPAWN, spawn)
        await getattr(bridge, method)()
        assert spawn.await_args.args == expected

    async def test_a_non_zero_exit_raises_with_its_stderr(
        self, bridge: HostBridge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A power action either happened or it is an error — no middle state."""
        monkeypatch.setattr(
            SPAWN,
            AsyncMock(return_value=fake_process(1, stderr=b"Access is denied.\n")),
        )
        with pytest.raises(HostCommandError) as excinfo:
            await bridge.shutdown(PowerActionSchema())
        assert excinfo.value.details["stderr"] == "Access is denied."


class TestHostInfo:
    """Host info is parsed from compact JSON, and refuses anything else."""

    async def test_parses_the_reported_json(
        self, bridge: HostBridge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The script's JSON maps straight onto the schema."""
        monkeypatch.setattr(
            SPAWN, AsyncMock(return_value=fake_process(0, HOST_INFO_JSON))
        )
        info = await bridge.host_info()
        assert info.computer_name == "DESKTOP-1"
        assert info.uptime_seconds == 8123.5

    async def test_malformed_output_is_a_command_error(
        self, bridge: HostBridge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Output that is not JSON means the host answered something unusable."""
        monkeypatch.setattr(
            SPAWN, AsyncMock(return_value=fake_process(0, b"not json at all"))
        )
        with pytest.raises(HostCommandError):
            await bridge.host_info()

    async def test_a_failed_script_is_a_command_error(
        self, bridge: HostBridge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-zero PowerShell exit is reported with its stderr."""
        monkeypatch.setattr(
            SPAWN, AsyncMock(return_value=fake_process(1, stderr=b"CIM failed"))
        )
        with pytest.raises(HostCommandError) as excinfo:
            await bridge.host_info()
        assert excinfo.value.details["stderr"] == "CIM failed"


class TestRunCommand:
    """Running a command reports what it said, including when it said failure."""

    async def test_a_non_zero_exit_is_returned_not_raised(
        self, bridge: HostBridge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The command ran; which exit codes matter is the caller's domain.

        Raising here would make a grep that matched nothing indistinguishable
        from a host that refused the call.
        """
        monkeypatch.setattr(
            SPAWN, AsyncMock(return_value=fake_process(1, b"", b"not found\n"))
        )
        result = await bridge.run_command(CommandSchema(command="findstr x"))
        assert result.return_code == 1
        assert result.stderr == "not found\n"

    async def test_the_shell_field_picks_the_interpreter(
        self, bridge: HostBridge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``cmd`` routes to ``cmd.exe``, not to PowerShell."""
        spawn = AsyncMock(return_value=fake_process(0))
        monkeypatch.setattr(SPAWN, spawn)
        await bridge.run_command(CommandSchema(command="dir", shell="cmd"))
        assert spawn.await_args.args == ("cmd.exe", "/c", "dir")

    async def test_the_payload_timeout_wins(
        self, bridge: HostBridge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A per-call timeout overrides the configured default."""
        monkeypatch.setattr(SPAWN, AsyncMock(return_value=fake_process(times_out=True)))
        with pytest.raises(HostCommandTimeoutError) as excinfo:
            await bridge.run_command(CommandSchema(command="sleep 99", timeout=5))
        assert excinfo.value.message_params["seconds"] == 5


class TestPickFile:
    """The dialog blocks on a person, so declining is not a failure."""

    async def test_returns_the_chosen_paths(
        self, bridge: HostBridge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Paths the dialog reported come back, with ``cancelled`` false."""
        monkeypatch.setattr(
            SPAWN,
            AsyncMock(
                return_value=fake_process(
                    0, json.dumps({"paths": ["C:\\Users\\me\\a.txt"]}).encode()
                )
            ),
        )
        result = await bridge.pick_file(FilePickSchema())
        assert result.paths == ["C:\\Users\\me\\a.txt"]
        assert result.cancelled is False

    async def test_an_empty_selection_is_cancelled_not_an_error(
        self, bridge: HostBridge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Someone closing the dialog is an outcome, not a fault."""
        monkeypatch.setattr(
            SPAWN,
            AsyncMock(return_value=fake_process(0, json.dumps({"paths": []}).encode())),
        )
        result = await bridge.pick_file(FilePickSchema())
        assert result.cancelled is True
        assert result.paths == []

    async def test_the_script_is_passed_encoded_and_single_threaded(
        self, bridge: HostBridge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``-Sta`` is required by WinForms; ``-EncodedCommand`` ends quoting.

        Base64-encoding the whole script means nothing the caller supplied
        can be reinterpreted by a layer between here and PowerShell.
        """
        spawn = AsyncMock(
            return_value=fake_process(0, json.dumps({"paths": []}).encode())
        )
        monkeypatch.setattr(SPAWN, spawn)
        await bridge.pick_file(FilePickSchema(title="Pick it's file"))
        args = spawn.await_args.args
        assert "-Sta" in args
        assert "-EncodedCommand" in args
        decoded = __import__("base64").b64decode(args[-1]).decode("utf-16le")
        assert "'Pick it''s file'" in decoded

    async def test_silence_is_a_command_error(
        self, bridge: HostBridge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No output at all means the dialog never reported anything."""
        monkeypatch.setattr(SPAWN, AsyncMock(return_value=fake_process(0, b"   ")))
        with pytest.raises(HostCommandError):
            await bridge.pick_file(FilePickSchema())

    async def test_the_wait_is_bounded_by_the_payload(
        self, bridge: HostBridge, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A dialog nobody answers times out instead of hanging the caller."""
        monkeypatch.setattr(SPAWN, AsyncMock(return_value=fake_process(times_out=True)))
        with pytest.raises(HostCommandTimeoutError) as excinfo:
            await bridge.pick_file(FilePickSchema(timeout_seconds=15))
        assert excinfo.value.message_params["seconds"] == 15
