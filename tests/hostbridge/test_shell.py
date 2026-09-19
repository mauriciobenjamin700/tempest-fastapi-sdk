"""Every host command funnels through one runner, so its rules hold everywhere."""

from unittest.mock import AsyncMock

import pytest

from tempest_fastapi_sdk.hostbridge import (
    HostBridgeConfig,
    HostCommandTimeoutError,
    HostUnavailableError,
    ps_single_quote,
    run_cmd,
    run_powershell,
    run_subprocess,
)
from tests.hostbridge.conftest import fake_process

SPAWN = "tempest_fastapi_sdk.hostbridge.shell.asyncio.create_subprocess_exec"


class TestRunSubprocess:
    """The runner reports what the process said, or why it could not run."""

    async def test_captures_output_and_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A finished process fills every field of the result."""
        monkeypatch.setattr(
            SPAWN, AsyncMock(return_value=fake_process(0, b"out\n", b"warn\n"))
        )
        result = await run_subprocess(["echo", "hi"])
        assert (result.return_code, result.stdout, result.stderr) == (
            0,
            "out\n",
            "warn\n",
        )
        assert result.duration_seconds >= 0

    async def test_decodes_invalid_bytes_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Output that is not valid UTF-8 is replaced, not fatal.

        A host tool writing in the console's own code page is normal, and
        losing the whole result over one byte is worse than a replacement
        character in the middle of it.
        """
        monkeypatch.setattr(SPAWN, AsyncMock(return_value=fake_process(0, b"caf\xe9")))
        assert (await run_subprocess(["x"])).stdout == "caf\ufffd"

    async def test_timeout_kills_the_process_and_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An expired command is killed and awaited, so nothing is left running."""
        process = fake_process(times_out=True)
        monkeypatch.setattr(SPAWN, AsyncMock(return_value=process))
        with pytest.raises(HostCommandTimeoutError) as excinfo:
            await run_subprocess(["sleep", "99"], timeout=3)
        process.kill.assert_called_once()
        process.wait.assert_awaited_once()
        assert excinfo.value.message_params["seconds"] == 3

    async def test_config_supplies_the_default_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no explicit timeout the config's value is the one enforced."""
        monkeypatch.setattr(SPAWN, AsyncMock(return_value=fake_process(times_out=True)))
        with pytest.raises(HostCommandTimeoutError) as excinfo:
            await run_subprocess(
                ["x"], config=HostBridgeConfig(default_command_timeout=7)
            )
        assert excinfo.value.message_params["seconds"] == 7

    async def test_missing_binary_is_unavailable_not_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An absent interpreter means the command never ran.

        Reporting it as a failed command would tell a caller the host
        refused the action, when the truth is this environment has no host
        control at all.
        """
        monkeypatch.setattr(SPAWN, AsyncMock(side_effect=FileNotFoundError("nope")))
        with pytest.raises(HostUnavailableError) as excinfo:
            await run_subprocess(["powershell.exe", "-Command", "x"])
        assert excinfo.value.message_params["binary"] == "powershell.exe"


class TestInterpreters:
    """The interpreter wrappers pass the flags the parsers here depend on."""

    async def test_powershell_is_non_interactive_and_profile_free(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A profile could rewrite the output this package parses."""
        spawn = AsyncMock(return_value=fake_process(0))
        monkeypatch.setattr(SPAWN, spawn)
        await run_powershell("Get-Date")
        args = spawn.await_args.args
        assert args[0] == "powershell.exe"
        assert "-NoProfile" in args
        assert "-NonInteractive" in args
        assert args[-1] == "Get-Date"

    async def test_cmd_uses_slash_c(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``cmd.exe /c`` runs the command and exits."""
        spawn = AsyncMock(return_value=fake_process(0))
        monkeypatch.setattr(SPAWN, spawn)
        await run_cmd("dir")
        assert spawn.await_args.args == ("cmd.exe", "/c", "dir")

    async def test_binaries_come_from_the_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PowerShell Core on a non-Windows host is a config change, not a fork."""
        spawn = AsyncMock(return_value=fake_process(0))
        monkeypatch.setattr(SPAWN, spawn)
        await run_powershell("x", config=HostBridgeConfig(powershell_binary="pwsh"))
        assert spawn.await_args.args[0] == "pwsh"


class TestPsSingleQuote:
    """PowerShell's single-quoted literal has exactly one escape."""

    def test_wraps_a_plain_value(self) -> None:
        """A value with nothing special is wrapped and left alone."""
        assert ps_single_quote("Documents") == "'Documents'"

    def test_doubles_inner_quotes(self) -> None:
        """A quote is escaped by doubling, which is what closes the hole."""
        assert ps_single_quote("it's") == "'it''s'"

    def test_leaves_expansion_syntax_inert(self) -> None:
        """``$`` and backticks are literal inside single quotes."""
        assert ps_single_quote("$env:PATH`n") == "'$env:PATH`n'"

    def test_a_closing_attempt_stays_inside_the_literal(self) -> None:
        """An injected ``'; rm -rf /` ends up doubled, not terminating."""
        assert ps_single_quote("'; Remove-Item C:\\ -Recurse") == (
            "'''; Remove-Item C:\\ -Recurse'"
        )
