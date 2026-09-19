"""The host control surface, as one object a service can hold.

    from tempest_fastapi_sdk.hostbridge import HostBridge, HostBridgeConfig

    bridge = HostBridge(HostBridgeConfig(allowed_base_paths=("/mnt/c/Users",)))
    info = await bridge.host_info()
    listing = await bridge.list_dir("C:\\\\Users\\\\me\\\\Documents")

Everything here reaches the host through :mod:`~tempest_fastapi_sdk.hostbridge.shell`
and everything that names a file goes through
:mod:`~tempest_fastapi_sdk.hostbridge.paths` first, so the timeout, the
confinement and the "missing binary is not a failed command" rule hold for
every method rather than per call site.

**What this can do is what a shell on that machine can do.** Whoever can call
these methods can power the machine off and run arbitrary commands as the
host user. Mount it behind real authentication, keep ``allowed_base_paths``
as narrow as the job needs, and treat the second tier — :meth:`run_command`,
:meth:`write_text`, :meth:`delete`, :meth:`shutdown`, :meth:`restart`,
:meth:`logoff` — as actions worth confirming with the person, not just
authorizing once.

Success sentences are returned in English. They are not error codes, so
they do not pass through the message catalog; a service showing them to
people translates them at its own edge, where it already knows the reader.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

from tempest_fastapi_sdk.hostbridge.config import HostBridgeConfig
from tempest_fastapi_sdk.hostbridge.exceptions import (
    HostCommandError,
    HostFileDecodeError,
    HostFileNotFoundError,
    HostFileTooLargeError,
)
from tempest_fastapi_sdk.hostbridge.paths import (
    ensure_path_allowed,
    to_windows_path,
    to_wsl_path,
)
from tempest_fastapi_sdk.hostbridge.schemas import (
    CommandResultSchema,
    CommandSchema,
    DirectoryListingSchema,
    FileContentSchema,
    FileDeleteSchema,
    FileInfoSchema,
    FilePickResultSchema,
    FilePickSchema,
    FileWriteSchema,
    HostInfoSchema,
    HostPdfSchema,
    PowerActionSchema,
)
from tempest_fastapi_sdk.hostbridge.shell import (
    CommandResult,
    ps_single_quote,
    run_cmd,
    run_powershell,
    run_subprocess,
)
from tempest_fastapi_sdk.pdf.pages import PdfExtractor, read_pdf_pages

MSG_SHUTDOWN_SCHEDULED: str = "Shutdown scheduled."
MSG_SHUTDOWN_ABORTED: str = "Shutdown aborted."
MSG_RESTART_SCHEDULED: str = "Restart scheduled."
MSG_LOCK_TRIGGERED: str = "Workstation locked."
MSG_LOGOFF_TRIGGERED: str = "User logoff triggered."
MSG_FILE_WRITTEN: str = "File written."
MSG_FILE_DELETED: str = "File deleted."

_HOST_INFO_SCRIPT: str = (
    "$os = Get-CimInstance Win32_OperatingSystem; "
    "$uptime = (Get-Date) - $os.LastBootUpTime; "
    "[ordered]@{"
    "computer_name = $env:COMPUTERNAME;"
    "user_name = $env:USERNAME;"
    "os_version = $os.Caption + ' ' + $os.Version;"
    "uptime_seconds = [math]::Round($uptime.TotalSeconds, 2)"
    "} | ConvertTo-Json -Compress"
)
"""PowerShell that reports the host's identity and uptime as compact JSON.

``ConvertTo-Json -Compress`` rather than parsing free-form output: the
shape is then the contract, and a locale that renders dates differently
cannot change what this reads.
"""


class HostBridge:
    """Control the machine this process runs on, or the Windows host behind it.

    Built for the WSL case — a Linux process reaching the Windows host it
    runs under, through ``powershell.exe`` and the ``/mnt`` drive mounts —
    and works unchanged on Windows itself when the binaries resolve. On a
    host with neither, every call raises
    :class:`~tempest_fastapi_sdk.hostbridge.HostUnavailableError` instead of
    failing in a way a caller cannot tell apart from a real error.
    """

    def __init__(self, config: HostBridgeConfig | None = None) -> None:
        """Hold the configuration every call reads.

        Args:
            config (HostBridgeConfig | None): Binaries, timeout and the
                allowed base paths. ``None`` builds a default config, which
                allows **no** paths — the file surface then refuses
                everything until it is configured.
        """
        self.config: HostBridgeConfig = config or HostBridgeConfig()

    async def _resolve(self, raw_path: str) -> Path:
        """Translate a caller's path and confine it to the allowed bases.

        Args:
            raw_path (str): A path in WSL or Windows form.

        Returns:
            Path: The resolved path, inside an allowed base directory.

        Raises:
            InvalidHostPathError: When it resolves outside every base, or
                cannot be translated.
            HostUnavailableError: When translation needs ``wslpath`` and it
                is absent.
        """
        return ensure_path_allowed(
            await to_wsl_path(raw_path), self.config.allowed_base_paths
        )

    async def _checked_file(self, raw_path: str) -> tuple[Path, int]:
        """Resolve a path, require it to be a file, and enforce the size cap.

        Args:
            raw_path (str): A path in WSL or Windows form.

        Returns:
            tuple[Path, int]: The resolved path and its size in bytes.

        Raises:
            InvalidHostPathError: When the path is not allowed.
            HostFileNotFoundError: When there is no file there.
            HostFileTooLargeError: When it is over ``max_file_read_bytes``.
        """
        path: Path = await self._resolve(raw_path)
        if not path.is_file():
            raise HostFileNotFoundError(message_params={"path": str(path)})
        size: int = path.stat().st_size
        if size > self.config.max_file_read_bytes:
            raise HostFileTooLargeError(
                message_params={
                    "path": str(path),
                    "size": size,
                    "limit": self.config.max_file_read_bytes,
                }
            )
        return path, size

    async def _power_command(self, args: list[str], message: str) -> str:
        """Run a power-management command and require it to succeed.

        Args:
            args (list[str]): Executable and arguments.
            message (str): Sentence returned when the command exits zero.

        Returns:
            str: ``message``.

        Raises:
            HostCommandError: When the command exits non-zero, carrying its
                stderr in ``details``.
            HostUnavailableError: When the executable is absent.
        """
        result: CommandResult = await run_subprocess(args, config=self.config)
        if result.return_code != 0:
            raise HostCommandError(details={"stderr": result.stderr.strip()})
        return message

    @staticmethod
    def _power_args(verb: str, payload: PowerActionSchema) -> list[str]:
        """Assemble a ``shutdown.exe`` invocation from a power-action payload.

        Args:
            verb (str): ``/s`` to shut down, ``/r`` to restart.
            payload (PowerActionSchema): Delay, force flag and message.

        Returns:
            list[str]: The argument list.
        """
        args: list[str] = ["shutdown.exe", verb, "/t", str(payload.seconds)]
        if payload.force:
            args.append("/f")
        if payload.message:
            args.extend(["/c", payload.message])
        return args

    async def shutdown(self, payload: PowerActionSchema | None = None) -> str:
        """Schedule the host to power off.

        Args:
            payload (PowerActionSchema | None): Delay, force flag and message.
                ``None`` powers off immediately without forcing.

        Returns:
            str: A confirmation sentence.

        Raises:
            HostCommandError: When ``shutdown.exe`` exits non-zero.
            HostUnavailableError: When it is absent.
        """
        return await self._power_command(
            self._power_args("/s", payload or PowerActionSchema()),
            MSG_SHUTDOWN_SCHEDULED,
        )

    async def restart(self, payload: PowerActionSchema | None = None) -> str:
        """Schedule the host to restart.

        Args:
            payload (PowerActionSchema | None): Delay, force flag and message.
                ``None`` restarts immediately without forcing.

        Returns:
            str: A confirmation sentence.

        Raises:
            HostCommandError: When ``shutdown.exe`` exits non-zero.
            HostUnavailableError: When it is absent.
        """
        return await self._power_command(
            self._power_args("/r", payload or PowerActionSchema()),
            MSG_RESTART_SCHEDULED,
        )

    async def abort_shutdown(self) -> str:
        """Cancel a shutdown or restart that has not fired yet.

        Returns:
            str: A confirmation sentence.

        Raises:
            HostCommandError: When ``shutdown.exe /a`` exits non-zero — which
                is also what "there was nothing scheduled" looks like.
            HostUnavailableError: When it is absent.
        """
        return await self._power_command(["shutdown.exe", "/a"], MSG_SHUTDOWN_ABORTED)

    async def lock(self) -> str:
        """Lock the host's active session.

        Returns:
            str: A confirmation sentence.

        Raises:
            HostCommandError: When ``rundll32`` exits non-zero.
            HostUnavailableError: When it is absent.
        """
        return await self._power_command(
            ["rundll32.exe", "user32.dll,LockWorkStation"], MSG_LOCK_TRIGGERED
        )

    async def logoff(self) -> str:
        """Log the host's active user off, closing their session.

        Returns:
            str: A confirmation sentence.

        Raises:
            HostCommandError: When ``shutdown.exe /l`` exits non-zero.
            HostUnavailableError: When it is absent.
        """
        return await self._power_command(["shutdown.exe", "/l"], MSG_LOGOFF_TRIGGERED)

    async def host_info(self) -> HostInfoSchema:
        """Report the host's name, user, OS version and uptime.

        Returns:
            HostInfoSchema: The collected host information.

        Raises:
            HostCommandError: When PowerShell fails, or answers with
                something that is not the expected JSON.
            HostUnavailableError: When PowerShell is absent.
        """
        result: CommandResult = await run_powershell(
            _HOST_INFO_SCRIPT, config=self.config
        )
        if result.return_code != 0:
            raise HostCommandError(details={"stderr": result.stderr.strip()})
        try:
            data: dict[str, str | float] = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise HostCommandError(details={"reason": str(exc)}) from exc
        return HostInfoSchema.model_validate(data)

    async def run_command(self, payload: CommandSchema) -> CommandResultSchema:
        """Run a command on the host and return everything it said.

        A non-zero exit is returned, not raised: the command ran, and which
        exit codes are failures is the caller's domain knowledge.

        Args:
            payload (CommandSchema): The command, its interpreter and an
                optional timeout.

        Returns:
            CommandResultSchema: Exit status, output and duration.

        Raises:
            HostCommandTimeoutError: When it outlives its timeout.
            HostUnavailableError: When the interpreter is absent.
        """
        runner = run_powershell if payload.shell == "powershell" else run_cmd
        result: CommandResult = await runner(
            payload.command, timeout=payload.timeout, config=self.config
        )
        return CommandResultSchema(
            return_code=result.return_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_seconds=result.duration_seconds,
        )

    async def read_text(
        self, raw_path: str, encoding: str = "utf-8"
    ) -> FileContentSchema:
        """Read a text file from the host.

        Args:
            raw_path (str): The file's path, in WSL or Windows form.
            encoding (str): Encoding used to decode it.

        Returns:
            FileContentSchema: The resolved path, size and decoded text.

        Raises:
            InvalidHostPathError: When the path is not allowed.
            HostFileNotFoundError: When there is no file there.
            HostFileTooLargeError: When it is over the configured cap.
            HostFileDecodeError: When the bytes do not decode — which is what
                pointing this at a PDF or an image looks like.
        """
        path, size = await self._checked_file(raw_path)
        try:
            content: str = await asyncio.to_thread(path.read_text, encoding=encoding)
        except (UnicodeDecodeError, LookupError) as exc:
            raise HostFileDecodeError(
                message_params={"path": str(path), "encoding": encoding},
                details={"reason": str(exc)},
            ) from exc
        return FileContentSchema(
            path=str(path), encoding=encoding, size_bytes=size, content=content
        )

    async def write_text(self, payload: FileWriteSchema) -> str:
        """Write text to a file on the host.

        Args:
            payload (FileWriteSchema): Path, content, encoding and whether to
                append and create parents.

        Returns:
            str: A confirmation sentence.

        Raises:
            InvalidHostPathError: When the path is not allowed.
            OSError: When the write itself fails — a full disk, a read-only
                mount, a missing parent with ``create_parents`` off.
        """
        path: Path = await self._resolve(payload.path)
        if payload.create_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        with path.open(
            mode="a" if payload.append else "w",
            encoding=payload.encoding,
            newline="",
        ) as handle:
            handle.write(payload.content)
        return MSG_FILE_WRITTEN

    async def list_dir(self, raw_path: str) -> DirectoryListingSchema:
        """List a directory on the host.

        An entry that cannot be stat'ed — a broken link, a file that
        disappeared mid-walk, one the host user may not see — is skipped
        rather than failing the listing.

        Args:
            raw_path (str): The directory's path, in WSL or Windows form.

        Returns:
            DirectoryListingSchema: Its entries, directories first and then
            by name. An empty directory lists nothing and is not an error.

        Raises:
            InvalidHostPathError: When the path is not allowed.
            HostFileNotFoundError: When there is no directory there.
        """
        path: Path = await self._resolve(raw_path)
        if not path.is_dir():
            raise HostFileNotFoundError(message_params={"path": str(path)})
        entries: list[FileInfoSchema] = []
        for child in path.iterdir():
            try:
                stat = child.stat()
                is_dir: bool = child.is_dir()
            except OSError:
                continue
            entries.append(
                FileInfoSchema(
                    name=child.name,
                    path=str(child),
                    is_dir=is_dir,
                    size_bytes=stat.st_size,
                    modified_at=stat.st_mtime,
                )
            )
        entries.sort(key=lambda entry: (not entry.is_dir, entry.name.lower()))
        return DirectoryListingSchema(path=str(path), entries=entries)

    async def delete(self, payload: FileDeleteSchema) -> str:
        """Delete a file, or an empty directory, on the host.

        A non-empty directory is **not** removed recursively: the call fails
        with the ``OSError`` the filesystem raises. Erasing a tree is not
        something to do as a side effect of a delete call.

        Args:
            payload (FileDeleteSchema): The path and whether a missing one is
                acceptable.

        Returns:
            str: A confirmation sentence.

        Raises:
            InvalidHostPathError: When the path is not allowed.
            HostFileNotFoundError: When it does not exist and ``missing_ok``
                is off.
            OSError: When the directory is not empty, or the delete fails.
        """
        path: Path = await self._resolve(payload.path)
        if not path.exists():
            if payload.missing_ok:
                return MSG_FILE_DELETED
            raise HostFileNotFoundError(message_params={"path": str(path)})
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
        return MSG_FILE_DELETED

    async def read_pdf(
        self,
        raw_path: str,
        extractor: PdfExtractor = PdfExtractor.TEXT,
        password: str | None = None,
    ) -> HostPdfSchema:
        """Read a PDF on the host, page by page.

        Args:
            raw_path (str): The file's path, in WSL or Windows form.
            extractor (PdfExtractor): ``TEXT`` for ``pypdf``, ``LAYOUT`` for
                ``pdfplumber`` — which recovers columns and tables and needs
                the ``[pdf-layout]`` extra.
            password (str | None): Password for an encrypted document.

        Returns:
            HostPdfSchema: The path, size and one entry per page. A scanned
            page comes back with empty text — there is no OCR here.

        Raises:
            InvalidHostPathError: When the path is not allowed.
            HostFileNotFoundError: When there is no file there.
            HostFileTooLargeError: When it is over the configured cap.
            PdfDecryptError: When it is encrypted and the password is wrong.
            PdfExtractError: When parsing fails for another reason.
        """
        path, size = await self._checked_file(raw_path)
        result = await read_pdf_pages(
            path.read_bytes(), extractor=extractor, password=password
        )
        return HostPdfSchema(
            path=str(path),
            extractor=result.extractor,
            page_count=result.page_count,
            size_bytes=size,
            pages=result.pages,
        )

    async def pick_file(self, payload: FilePickSchema) -> FilePickResultSchema:
        """Open the host's native file-picker and return what was chosen.

        This blocks on a person: the dialog stays open until they choose or
        close it, bounded by ``timeout_seconds``. Everything the caller
        supplies reaches the generated script through
        :func:`~tempest_fastapi_sdk.hostbridge.shell.ps_single_quote`, and
        the whole script is passed base64-encoded as ``-EncodedCommand`` so
        no quoting survives the trip to be reinterpreted.

        The dialog is given a hidden, always-on-top owner window. Without an
        owner it opens behind whatever the person is looking at, and a modal
        nobody can see reads as a hang.

        Args:
            payload (FilePickSchema): Title, initial directory, filter,
                multiselect and timeout.

        Returns:
            FilePickResultSchema: The chosen paths, or ``cancelled=True``.

        Raises:
            InvalidHostPathError: When ``initial_dir`` is not allowed.
            HostCommandTimeoutError: When nobody answers in time.
            HostCommandError: When PowerShell exits non-zero or answers with
                something that is not the expected JSON.
            HostUnavailableError: When PowerShell is absent.
        """
        initial_dir_windows: str = ""
        if payload.initial_dir and payload.initial_dir.strip():
            resolved_dir: Path = await self._resolve(payload.initial_dir.strip())
            initial_dir_windows = await to_windows_path(str(resolved_dir))

        script: str = (
            "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
            "$dlg = New-Object System.Windows.Forms.OpenFileDialog;"
            f"$dlg.Title = {ps_single_quote(payload.title)};"
            f"$dlg.Filter = {ps_single_quote(payload.filter)};"
            f"$dlg.Multiselect = {'$true' if payload.multiselect else '$false'};"
            "$dlg.CheckFileExists = $true;"
        )
        if initial_dir_windows:
            script += f"$dlg.InitialDirectory = {ps_single_quote(initial_dir_windows)};"
        script += (
            "$owner = New-Object System.Windows.Forms.Form;"
            "$owner.TopMost = $true;"
            "$owner.ShowInTaskbar = $false;"
            "$owner.Opacity = 0;"
            "$owner.WindowState = 'Minimized';"
            "try {"
            "    $result = $dlg.ShowDialog($owner);"
            "    if ($result -eq [System.Windows.Forms.DialogResult]::OK) {"
            "        $payload = @{ paths = @($dlg.FileNames) };"
            "    } else {"
            "        $payload = @{ paths = @() };"
            "    }"
            "    [Console]::Out.Write("
            "(ConvertTo-Json -Compress -InputObject $payload));"
            "} finally { $owner.Dispose() }"
        )
        result: CommandResult = await run_subprocess(
            [
                self.config.powershell_binary,
                "-NoProfile",
                "-Sta",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                base64.b64encode(script.encode("utf-16le")).decode("ascii"),
            ],
            timeout=payload.timeout_seconds,
            config=self.config,
        )
        if result.return_code != 0:
            raise HostCommandError(
                details={
                    "exit_code": result.return_code,
                    "stderr": result.stderr.strip() or result.stdout.strip(),
                }
            )
        stdout: str = result.stdout.strip()
        if not stdout:
            raise HostCommandError(details={"stderr": "no output"})
        try:
            data: dict[str, list[str]] = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise HostCommandError(details={"reason": str(exc)}) from exc
        paths: list[str] = list(data.get("paths") or [])
        return FilePickResultSchema(cancelled=not paths, paths=paths)


__all__: list[str] = [
    "MSG_FILE_DELETED",
    "MSG_FILE_WRITTEN",
    "MSG_LOCK_TRIGGERED",
    "MSG_LOGOFF_TRIGGERED",
    "MSG_RESTART_SCHEDULED",
    "MSG_SHUTDOWN_ABORTED",
    "MSG_SHUTDOWN_SCHEDULED",
    "HostBridge",
]
