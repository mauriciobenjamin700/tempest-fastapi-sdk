"""Control the machine a service runs on — power, files, PDFs, commands.

Built for the shape a local assistant actually has: a Python process inside
WSL that needs to reach the Windows host it runs under. It shells out to
``powershell.exe`` and reads the disk through the ``/mnt`` drive mounts, and
works unchanged on Windows itself when those binaries resolve.

    from tempest_fastapi_sdk.hostbridge import HostBridge, HostBridgeConfig

    bridge = HostBridge(
        HostBridgeConfig(allowed_base_paths=("/mnt/c/Users/me/Documents",)),
    )
    info = await bridge.host_info()
    text = await bridge.read_text("C:\\\\Users\\\\me\\\\Documents\\\\notes.txt")

!!! danger "This is a shell on that machine"

    Whoever reaches these methods can run arbitrary commands as the host
    user and power the machine off. Two things follow, and neither is
    optional: ``allowed_base_paths`` starts **empty**, denying every path
    until it is configured, and
    :func:`~tempest_fastapi_sdk.hostbridge.make_hostbridge_router` takes its
    auth dependencies as a required argument and refuses an empty one. The
    write side of that router — commands, writes, deletes, power — is behind
    ``destructive=True`` and off by default.

Nothing here is imported by ``create_app`` or mounted anywhere on its own:
a host surface exists because a service asked for it.

**On a host with neither PowerShell nor ``wslpath``** — a plain Linux box, a
container — calls raise
:class:`~tempest_fastapi_sdk.hostbridge.HostUnavailableError` (503) rather
than a generic failure, so a caller can degrade instead of reporting that
the action failed.

Needs no extra of its own for the system, file and command surface — it is
standard library and FastAPI. Reading PDFs needs ``[pdf-read]``, and reading
them with layout and tables needs ``[pdf-layout]``.
"""

from tempest_fastapi_sdk.hostbridge.bridge import (
    MSG_FILE_DELETED as MSG_FILE_DELETED,
)
from tempest_fastapi_sdk.hostbridge.bridge import (
    MSG_FILE_WRITTEN as MSG_FILE_WRITTEN,
)
from tempest_fastapi_sdk.hostbridge.bridge import (
    MSG_LOCK_TRIGGERED as MSG_LOCK_TRIGGERED,
)
from tempest_fastapi_sdk.hostbridge.bridge import (
    MSG_LOGOFF_TRIGGERED as MSG_LOGOFF_TRIGGERED,
)
from tempest_fastapi_sdk.hostbridge.bridge import (
    MSG_RESTART_SCHEDULED as MSG_RESTART_SCHEDULED,
)
from tempest_fastapi_sdk.hostbridge.bridge import (
    MSG_SHUTDOWN_ABORTED as MSG_SHUTDOWN_ABORTED,
)
from tempest_fastapi_sdk.hostbridge.bridge import (
    MSG_SHUTDOWN_SCHEDULED as MSG_SHUTDOWN_SCHEDULED,
)
from tempest_fastapi_sdk.hostbridge.bridge import HostBridge as HostBridge
from tempest_fastapi_sdk.hostbridge.config import (
    DEFAULT_COMMAND_TIMEOUT as DEFAULT_COMMAND_TIMEOUT,
)
from tempest_fastapi_sdk.hostbridge.config import (
    DEFAULT_MAX_FILE_READ_BYTES as DEFAULT_MAX_FILE_READ_BYTES,
)
from tempest_fastapi_sdk.hostbridge.config import HostBridgeConfig as HostBridgeConfig
from tempest_fastapi_sdk.hostbridge.exceptions import (
    HostCommandError as HostCommandError,
)
from tempest_fastapi_sdk.hostbridge.exceptions import (
    HostCommandTimeoutError as HostCommandTimeoutError,
)
from tempest_fastapi_sdk.hostbridge.exceptions import (
    HostFileDecodeError as HostFileDecodeError,
)
from tempest_fastapi_sdk.hostbridge.exceptions import (
    HostFileNotFoundError as HostFileNotFoundError,
)
from tempest_fastapi_sdk.hostbridge.exceptions import (
    HostFileTooLargeError as HostFileTooLargeError,
)
from tempest_fastapi_sdk.hostbridge.exceptions import (
    HostUnavailableError as HostUnavailableError,
)
from tempest_fastapi_sdk.hostbridge.exceptions import (
    InvalidHostPathError as InvalidHostPathError,
)
from tempest_fastapi_sdk.hostbridge.paths import (
    ensure_path_allowed as ensure_path_allowed,
)
from tempest_fastapi_sdk.hostbridge.paths import to_windows_path as to_windows_path
from tempest_fastapi_sdk.hostbridge.paths import to_wsl_path as to_wsl_path
from tempest_fastapi_sdk.hostbridge.router import (
    make_hostbridge_router as make_hostbridge_router,
)
from tempest_fastapi_sdk.hostbridge.schemas import (
    CommandResultSchema as CommandResultSchema,
)
from tempest_fastapi_sdk.hostbridge.schemas import CommandSchema as CommandSchema
from tempest_fastapi_sdk.hostbridge.schemas import (
    DirectoryListingSchema as DirectoryListingSchema,
)
from tempest_fastapi_sdk.hostbridge.schemas import (
    FileContentSchema as FileContentSchema,
)
from tempest_fastapi_sdk.hostbridge.schemas import FileDeleteSchema as FileDeleteSchema
from tempest_fastapi_sdk.hostbridge.schemas import FileInfoSchema as FileInfoSchema
from tempest_fastapi_sdk.hostbridge.schemas import (
    FilePickResultSchema as FilePickResultSchema,
)
from tempest_fastapi_sdk.hostbridge.schemas import FilePickSchema as FilePickSchema
from tempest_fastapi_sdk.hostbridge.schemas import FileWriteSchema as FileWriteSchema
from tempest_fastapi_sdk.hostbridge.schemas import HostInfoSchema as HostInfoSchema
from tempest_fastapi_sdk.hostbridge.schemas import (
    HostMessageSchema as HostMessageSchema,
)
from tempest_fastapi_sdk.hostbridge.schemas import HostPdfSchema as HostPdfSchema
from tempest_fastapi_sdk.hostbridge.schemas import (
    PowerActionSchema as PowerActionSchema,
)
from tempest_fastapi_sdk.hostbridge.shell import CommandResult as CommandResult
from tempest_fastapi_sdk.hostbridge.shell import ps_single_quote as ps_single_quote
from tempest_fastapi_sdk.hostbridge.shell import run_cmd as run_cmd
from tempest_fastapi_sdk.hostbridge.shell import run_powershell as run_powershell
from tempest_fastapi_sdk.hostbridge.shell import run_subprocess as run_subprocess

__all__: list[str] = [
    "DEFAULT_COMMAND_TIMEOUT",
    "DEFAULT_MAX_FILE_READ_BYTES",
    "MSG_FILE_DELETED",
    "MSG_FILE_WRITTEN",
    "MSG_LOCK_TRIGGERED",
    "MSG_LOGOFF_TRIGGERED",
    "MSG_RESTART_SCHEDULED",
    "MSG_SHUTDOWN_ABORTED",
    "MSG_SHUTDOWN_SCHEDULED",
    "CommandResult",
    "CommandResultSchema",
    "CommandSchema",
    "DirectoryListingSchema",
    "FileContentSchema",
    "FileDeleteSchema",
    "FileInfoSchema",
    "FilePickResultSchema",
    "FilePickSchema",
    "FileWriteSchema",
    "HostBridge",
    "HostBridgeConfig",
    "HostCommandError",
    "HostCommandTimeoutError",
    "HostFileDecodeError",
    "HostFileNotFoundError",
    "HostFileTooLargeError",
    "HostInfoSchema",
    "HostMessageSchema",
    "HostPdfSchema",
    "HostUnavailableError",
    "InvalidHostPathError",
    "PowerActionSchema",
    "ensure_path_allowed",
    "make_hostbridge_router",
    "ps_single_quote",
    "run_cmd",
    "run_powershell",
    "run_subprocess",
    "to_windows_path",
    "to_wsl_path",
]
