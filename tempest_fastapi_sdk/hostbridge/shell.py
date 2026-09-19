"""Run a process on the host and capture what it said.

Every command in this package funnels through :func:`run_subprocess`, which
means three properties hold everywhere and not per call site: the argument
list is passed to ``execve`` without a shell in between, the process is
killed when it outlives its timeout instead of leaking, and a missing
executable is reported as :class:`HostUnavailableError` rather than as a
command that failed.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from tempest_fastapi_sdk.hostbridge.config import HostBridgeConfig
from tempest_fastapi_sdk.hostbridge.exceptions import (
    HostCommandTimeoutError,
    HostUnavailableError,
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    """What a finished subprocess produced.

    Attributes:
        return_code (int): The process's exit status, or ``-1`` when it ended
            without one.
        stdout (str): Standard output, decoded with ``errors="replace"``.
        stderr (str): Standard error, decoded the same way.
        duration_seconds (float): Wall-clock time the process ran.
    """

    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float


async def run_subprocess(
    args: list[str],
    *,
    timeout: int | None = None,
    config: HostBridgeConfig | None = None,
) -> CommandResult:
    """Run a process and capture its output.

    Args:
        args (list[str]): Executable followed by its arguments. Passed to
            ``execve`` as a list — no shell parses it, so a value carrying a
            space or a quote is one argument and not an injection.
        timeout (int | None): Seconds the process may run. ``None`` uses the
            config's default.
        config (HostBridgeConfig | None): Source of the default timeout.
            ``None`` uses a default-constructed config.

    Returns:
        CommandResult: The finished process's exit status, output and duration.

    Raises:
        HostUnavailableError: When the executable is not on ``PATH``.
        HostCommandTimeoutError: When the process outlives the timeout. It is
            killed and awaited before this raises, so nothing is left running.
    """
    effective_config: HostBridgeConfig = config or HostBridgeConfig()
    effective_timeout: int = (
        timeout if timeout is not None else effective_config.default_command_timeout
    )
    start: float = time.perf_counter()
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        raise HostUnavailableError(
            message_params={"binary": args[0]},
            details={"reason": str(exc)},
        ) from exc
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=effective_timeout
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise HostCommandTimeoutError(
            message_params={"seconds": effective_timeout}
        ) from exc
    return CommandResult(
        return_code=process.returncode if process.returncode is not None else -1,
        stdout=stdout_bytes.decode(errors="replace"),
        stderr=stderr_bytes.decode(errors="replace"),
        duration_seconds=time.perf_counter() - start,
    )


async def run_powershell(
    command: str,
    *,
    timeout: int | None = None,
    config: HostBridgeConfig | None = None,
) -> CommandResult:
    """Run a PowerShell statement on the host.

    The interpreter is started with ``-NoProfile -NonInteractive``: a profile
    would let whatever the host user configured change the output this
    package parses, and an interactive prompt would hang the call until the
    timeout rather than failing.

    Args:
        command (str): The ``-Command`` payload.
        timeout (int | None): Seconds the command may run.
        config (HostBridgeConfig | None): Source of the binary and the
            default timeout.

    Returns:
        CommandResult: The finished process's output.

    Raises:
        HostUnavailableError: When PowerShell is not on ``PATH``.
        HostCommandTimeoutError: When the command outlives the timeout.
    """
    effective_config: HostBridgeConfig = config or HostBridgeConfig()
    return await run_subprocess(
        [
            effective_config.powershell_binary,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        timeout=timeout,
        config=effective_config,
    )


async def run_cmd(
    command: str,
    *,
    timeout: int | None = None,
    config: HostBridgeConfig | None = None,
) -> CommandResult:
    """Run a ``cmd.exe`` command on the host.

    Args:
        command (str): The command to run after ``cmd.exe /c``.
        timeout (int | None): Seconds the command may run.
        config (HostBridgeConfig | None): Source of the binary and the
            default timeout.

    Returns:
        CommandResult: The finished process's output.

    Raises:
        HostUnavailableError: When ``cmd.exe`` is not on ``PATH``.
        HostCommandTimeoutError: When the command outlives the timeout.
    """
    effective_config: HostBridgeConfig = config or HostBridgeConfig()
    return await run_subprocess(
        [effective_config.cmd_binary, "/c", command],
        timeout=timeout,
        config=effective_config,
    )


def ps_single_quote(value: str) -> str:
    """Quote a value as a PowerShell single-quoted literal.

    PowerShell's single-quoted string has exactly one escape: a doubled
    quote. Nothing else inside is interpreted — no ``$`` expansion, no
    backtick escapes — which is what makes this a safe way to put a
    caller-supplied title or path into a generated script.

    Args:
        value (str): The raw value.

    Returns:
        str: The value wrapped in single quotes, with inner quotes doubled.
    """
    return "'" + value.replace("'", "''") + "'"


__all__: list[str] = [
    "CommandResult",
    "ps_single_quote",
    "run_cmd",
    "run_powershell",
    "run_subprocess",
]
