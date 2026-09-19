"""Request and response shapes of the host control surface.

These are the same objects whether the bridge is called in-process or
mounted as a router: the facade takes and returns them, and
:func:`~tempest_fastapi_sdk.hostbridge.make_hostbridge_router` only adds
HTTP around them. That is what lets a service move from one to the other
without its callers noticing.
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from tempest_fastapi_sdk.pdf.pages import PdfExtractor, PdfPage
from tempest_fastapi_sdk.schemas.base import BaseSchema


class HostMessageSchema(BaseSchema):
    """A finished action, described in one sentence.

    Attributes:
        message (str): What happened, for a human to read.
    """

    message: str


class PowerActionSchema(BaseSchema):
    """Arguments for a shutdown or a restart.

    Attributes:
        seconds (int): Delay before the action runs. ``0`` is immediate.
        force (bool): Close applications without prompting the user. This
            discards unsaved work — it is off by default for that reason.
        message (str | None): Text shown to the logged-in user.
    """

    seconds: int = Field(
        default=0, ge=0, le=86400, description="Delay before the action, in seconds."
    )
    force: bool = Field(
        default=False,
        description=(
            "Force applications closed without prompting, discarding unsaved work."
        ),
    )
    message: str | None = Field(
        default=None, max_length=512, description="Message shown to the logged-in user."
    )


class HostInfoSchema(BaseSchema):
    """Identity and uptime of the host.

    Attributes:
        computer_name (str): The machine's name.
        user_name (str): The logged-in user.
        os_version (str): Operating system caption and version.
        uptime_seconds (float): Seconds since the last boot.
    """

    computer_name: str
    user_name: str
    os_version: str
    uptime_seconds: float


class CommandSchema(BaseSchema):
    """A command to run on the host.

    Attributes:
        command (str): The command string. It reaches the interpreter as a
            single argument, so the interpreter — not a shell in between —
            is what parses it.
        shell (Literal["powershell", "cmd"]): Which interpreter runs it.
        timeout (int | None): Seconds it may run. ``None`` uses the
            configured default.
    """

    command: str = Field(..., min_length=1, description="Command to run on the host.")
    shell: Literal["powershell", "cmd"] = Field(
        default="powershell", description="Interpreter used to run the command."
    )
    timeout: int | None = Field(
        default=None,
        ge=1,
        le=600,
        description="Timeout in seconds, overriding the configured default.",
    )


class CommandResultSchema(BaseSchema):
    """What a host command produced.

    A non-zero ``return_code`` is **not** an error here: the command ran and
    said something, and which exit codes matter is the caller's domain
    knowledge, not this package's.

    Attributes:
        return_code (int): The command's exit status.
        stdout (str): Standard output.
        stderr (str): Standard error.
        duration_seconds (float): Wall-clock time the command ran.
    """

    model_config = ConfigDict(str_strip_whitespace=False)
    """Keep the payload byte-for-byte.

    :class:`~tempest_fastapi_sdk.BaseSchema` strips whitespace from every
    string, which is right for a name or an email and wrong for content that
    was read off a disk or a pipe: a file whose last line ends in a newline
    would come back without it, and writing that back would silently rewrite
    the file.
    """

    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float


class FileWriteSchema(BaseSchema):
    """A text file to write on the host.

    Attributes:
        path (str): Absolute path, in WSL or Windows form.
        content (str): Text to write.
        encoding (str): Encoding used to write it.
        append (bool): Append instead of overwriting.
        create_parents (bool): Create missing parent directories.
    """

    model_config = ConfigDict(str_strip_whitespace=False)
    """Write exactly what the caller sent.

    See :class:`FileContentSchema` — a write that strips the content cannot
    round-trip a file it just read.
    """

    path: str = Field(
        ..., min_length=1, description="Absolute path (WSL or Windows form)."
    )
    content: str = Field(..., description="Text content to write.")
    encoding: str = Field(
        default="utf-8", description="Encoding used to write the file."
    )
    append: bool = Field(default=False, description="Append instead of overwriting.")
    create_parents: bool = Field(
        default=True, description="Create missing parent directories."
    )


class FileContentSchema(BaseSchema):
    """A text file's contents.

    Attributes:
        path (str): The resolved path that was read, which may differ from
            the one requested — a Windows path comes back in its local form.
        encoding (str): Encoding used to decode it.
        size_bytes (int): Size on disk.
        content (str): The decoded text.
    """

    model_config = ConfigDict(str_strip_whitespace=False)
    """Keep the payload byte-for-byte.

    :class:`~tempest_fastapi_sdk.BaseSchema` strips whitespace from every
    string, which is right for a name or an email and wrong for content that
    was read off a disk or a pipe: a file whose last line ends in a newline
    would come back without it, and writing that back would silently rewrite
    the file.
    """

    path: str
    encoding: str
    size_bytes: int
    content: str


class FileInfoSchema(BaseSchema):
    """One entry in a directory listing.

    Attributes:
        name (str): The entry's own name.
        path (str): Its full resolved path.
        is_dir (bool): Whether it is a directory.
        size_bytes (int): Size on disk.
        modified_at (float): Unix timestamp of the last modification.
    """

    name: str
    path: str
    is_dir: bool
    size_bytes: int
    modified_at: float


class DirectoryListingSchema(BaseSchema):
    """The contents of a directory.

    Attributes:
        path (str): The resolved directory that was listed.
        entries (list[FileInfoSchema]): Its entries, directories first and
            then by name, case-insensitively. An empty directory lists
            nothing and is not an error.
    """

    path: str
    entries: list[FileInfoSchema] = Field(default_factory=list)


class FileDeleteSchema(BaseSchema):
    """A file or empty directory to remove.

    Attributes:
        path (str): Absolute path, in WSL or Windows form.
        missing_ok (bool): Succeed instead of failing when it is already gone.
    """

    path: str = Field(..., min_length=1)
    missing_ok: bool = Field(
        default=False, description="Succeed when the path does not exist."
    )


class FilePickSchema(BaseSchema):
    """Configuration for the host's native file-picker dialog.

    Attributes:
        title (str): The dialog's window title.
        initial_dir (str | None): Directory it opens in. Confined to the
            allowed base paths like any other path.
        filter (str): The dialog's filter string, in the
            ``Label1|pattern1|Label2|pattern2`` form the host expects.
        multiselect (bool): Allow more than one file to be chosen.
        timeout_seconds (int): How long to wait for the person to choose. A
            dialog nobody answers is what this bounds — without it the call
            would hang for as long as the window stays open.
    """

    title: str = Field(
        default="Select a file", max_length=200, description="Dialog window title."
    )
    initial_dir: str | None = Field(
        default=None, description="Directory the dialog opens in (WSL or Windows form)."
    )
    filter: str = Field(
        default="All files (*.*)|*.*",
        description="Filter string, formatted as 'Label1|pattern1|Label2|pattern2'.",
    )
    multiselect: bool = Field(default=False, description="Allow picking several files.")
    timeout_seconds: int = Field(
        default=300,
        ge=5,
        le=1800,
        description="Seconds to wait for the person to choose.",
    )


class FilePickResultSchema(BaseSchema):
    """What the file-picker dialog came back with.

    Attributes:
        cancelled (bool): ``True`` when the dialog closed with nothing chosen.
            Distinguishing this from an error matters: a person declining is
            not a failure.
        paths (list[str]): Chosen paths, in the host's own form.
    """

    cancelled: bool = Field(
        default=False, description="True when the dialog closed without a selection."
    )
    paths: list[str] = Field(default_factory=list, description="Selected file paths.")


class HostPdfSchema(BaseSchema):
    """A PDF on the host, read page by page.

    Attributes:
        path (str): The resolved path that was read.
        extractor (PdfExtractor): Which backend produced the pages.
        page_count (int): Number of pages read.
        size_bytes (int): The file's size on disk.
        pages (list[PdfPage]): One entry per page, in document order.
    """

    path: str
    extractor: PdfExtractor
    page_count: int = Field(..., ge=0)
    size_bytes: int = Field(..., ge=0)
    pages: list[PdfPage] = Field(default_factory=list)


__all__: list[str] = [
    "CommandResultSchema",
    "CommandSchema",
    "DirectoryListingSchema",
    "FileContentSchema",
    "FileDeleteSchema",
    "FileInfoSchema",
    "FilePickResultSchema",
    "FilePickSchema",
    "FileWriteSchema",
    "HostInfoSchema",
    "HostMessageSchema",
    "HostPdfSchema",
    "PowerActionSchema",
]
