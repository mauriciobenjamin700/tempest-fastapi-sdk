"""The knobs the host bridge reads, as a plain frozen value object.

Kept separate from :class:`~tempest_fastapi_sdk.settings.HostBridgeSettings`
so the bridge is constructible in a test, a script or a CLI without a
``BaseAppSettings`` instance — and so the settings mixin stays what it is,
a mapping from environment variables onto these fields.

``allowed_base_paths`` has **no permissive default**. An empty tuple denies
every path, which is the safe direction to fail: a bridge that can run
PowerShell should not also be able to read anything on the disk because
nobody remembered to configure it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_MAX_FILE_READ_BYTES: int = 10 * 1024 * 1024
"""Ceiling for a single text or PDF read, in bytes.

Ported from the `windows-bridge-api` service this package replaced. It is a
text reader: 10 MiB of text is already far past what a caller can use, and
the cap is what keeps one request from pinning the process's memory.
"""

DEFAULT_COMMAND_TIMEOUT: int = 30
"""Seconds a host command may run before it is killed."""


@dataclass(frozen=True, slots=True)
class HostBridgeConfig:
    """Configuration for a :class:`~tempest_fastapi_sdk.hostbridge.HostBridge`.

    Attributes:
        allowed_base_paths (tuple[str, ...]): Directories the file surface may
            touch. A resolved path outside every one of them is refused. Empty
            (the default) denies everything.
        powershell_binary (str): Executable used for PowerShell commands.
            ``"powershell.exe"`` from WSL; ``"pwsh"`` on a non-Windows host
            that has PowerShell Core.
        cmd_binary (str): Executable used for ``cmd`` commands.
        default_command_timeout (int): Seconds a command may run when the
            caller does not pass its own timeout.
        max_file_read_bytes (int): Ceiling for a single read.
    """

    allowed_base_paths: tuple[str, ...] = field(default_factory=tuple)
    powershell_binary: str = "powershell.exe"
    cmd_binary: str = "cmd.exe"
    default_command_timeout: int = DEFAULT_COMMAND_TIMEOUT
    max_file_read_bytes: int = DEFAULT_MAX_FILE_READ_BYTES


__all__: list[str] = [
    "DEFAULT_COMMAND_TIMEOUT",
    "DEFAULT_MAX_FILE_READ_BYTES",
    "HostBridgeConfig",
]
