"""Translate and confine a path before anything touches the filesystem.

Two jobs, in this order, and the order is the security property:

1. **Translate.** A caller may name a file in Windows form (``C:\\Users\\...``)
   or WSL form (``/mnt/c/Users/...``). ``wslpath`` converts the first into the
   second; the second is already usable.
2. **Confine.** The translated path is resolved — symlinks followed, ``..``
   collapsed — and only then checked against the allowed base paths.

Checking before resolving is the classic hole: ``/mnt/c/../etc/passwd``
starts with an allowed prefix as a string and does not as a path.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from tempest_fastapi_sdk.hostbridge.exceptions import (
    HostUnavailableError,
    InvalidHostPathError,
)

_WIN_DRIVE_RE: re.Pattern[str] = re.compile(r"^[A-Za-z]:[\\/]")
"""Matches a path that starts with a Windows drive letter."""


async def _run_wslpath(flag: str, value: str) -> str:
    """Invoke ``wslpath`` to convert between WSL and Windows path forms.

    Args:
        flag (str): Either ``-u`` (to WSL) or ``-w`` (to Windows).
        value (str): Path to convert.

    Returns:
        str: The converted path, trailing whitespace stripped.

    Raises:
        HostUnavailableError: When ``wslpath`` is not on ``PATH`` — this
            process is not running under WSL.
        InvalidHostPathError: When ``wslpath`` exits non-zero.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "wslpath",
            flag,
            value,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise HostUnavailableError(
            message_key="HOST_WSLPATH_MISSING",
            details={"reason": str(exc)},
        ) from exc
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise InvalidHostPathError(
            message_key="HOST_PATH_TRANSLATION_FAILED",
            details={"reason": stderr.decode().strip()},
        )
    return stdout.decode().strip()


async def to_wsl_path(path: str) -> Path:
    """Normalize an input path, in either form, to a local :class:`Path`.

    Args:
        path (str): A WSL path (``/mnt/c/...``) or a Windows path (``C:\\...``).

    Returns:
        Path: The path in the form this process can open.

    Raises:
        HostUnavailableError: When translation is needed and ``wslpath`` is
            absent.
        InvalidHostPathError: When the path cannot be converted.
    """
    if _WIN_DRIVE_RE.match(path):
        return Path(await _run_wslpath("-u", path))
    return Path(path)


async def to_windows_path(path: str) -> str:
    """Convert a WSL path to its Windows form.

    Args:
        path (str): A WSL path (``/mnt/c/foo``). A path that is already in
            Windows form is returned unchanged.

    Returns:
        str: The Windows-style path (``C:\\foo``).

    Raises:
        HostUnavailableError: When ``wslpath`` is absent.
        InvalidHostPathError: When the path cannot be converted.
    """
    if _WIN_DRIVE_RE.match(path):
        return path
    return await _run_wslpath("-w", path)


def ensure_path_allowed(target: Path, allowed_base_paths: tuple[str, ...]) -> Path:
    """Resolve a path and confine it to one of the allowed base directories.

    Args:
        target (Path): The path to validate.
        allowed_base_paths (tuple[str, ...]): Directories the caller may
            reach. An empty tuple denies every path.

    Returns:
        Path: The resolved absolute path, guaranteed to sit under one of the
        bases.

    Raises:
        InvalidHostPathError: When the resolved path is outside every base.
            The message carries the **resolved** path, not the one the caller
            sent, so a rejected ``/mnt/c/../etc/passwd`` reads as the
            ``/etc/passwd`` it actually was.
    """
    resolved: Path = target.expanduser().resolve(strict=False)
    for raw_base in allowed_base_paths:
        base: Path = Path(raw_base).expanduser().resolve(strict=False)
        if resolved == base or base in resolved.parents:
            return resolved
    raise InvalidHostPathError(message_params={"path": str(resolved)})


__all__: list[str] = [
    "ensure_path_allowed",
    "to_windows_path",
    "to_wsl_path",
]
