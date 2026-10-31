"""The storage browser's data source: one bucket of an object store.

The admin already wrote to object storage (``upload_fields=``) and the CLI
already read it (``tempest storage ls``), but an operator who needed to see
what a bucket holds, look at one file or remove a stray upload had no screen
for it. :class:`StorageBrowserService` is that screen's service: it lists a
"folder", describes one object, decides how the object can be previewed, and
carries out the writes — upload, replace, rename, in-place text edit and
delete — with the limits checked here rather than in the template.

It talks to any store with the five calls of :class:`AdminStorageClient`,
which :class:`~tempest_fastapi_sdk.storage.AsyncMinIOClient` and
:class:`~tempest_fastapi_sdk.storage.LocalObjectStorageClient` both
implement. Folders are not objects on either: they are the ``/``-separated
prefixes of the keys, so an empty folder does not exist and a folder appears
as soon as one key is written under it.

What the browser deliberately does not do is serve a stored file as a page
of the admin's origin. Previews go through
:func:`~tempest_fastapi_sdk.storage.object_download_response`, which grants
``inline`` only to :data:`~tempest_fastapi_sdk.utils.INLINE_SAFE_MEDIA_TYPES`
and stamps ``nosniff`` plus a sandboxing CSP on every response. Text-like
types outside that list — HTML, SVG, JSON — are previewed as escaped source
in the page, never rendered.
"""

from __future__ import annotations

import asyncio
import mimetypes
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, BinaryIO, Final, Protocol, cast

from tempest_fastapi_sdk.core.enums import BaseStrEnum
from tempest_fastapi_sdk.utils.download import INLINE_SAFE_MEDIA_TYPES

if TYPE_CHECKING:
    from tempest_fastapi_sdk.storage.minio_client import ObjectStat


DEFAULT_STORAGE_PAGE_SIZE: Final[int] = 100
"""Entries (folders and files) shown per page of a folder listing."""

DEFAULT_STORAGE_MAX_UPLOAD_BYTES: Final[int] = 25 * 1024 * 1024
"""Largest file the browser accepts in one upload or replace: 25 MiB.

The same figure as
:data:`~tempest_fastapi_sdk.storage.DEFAULT_LOCAL_MAX_UPLOAD_BYTES`, so a
store and its admin screen refuse at the same size by default.
"""

DEFAULT_STORAGE_MAX_TEXT_BYTES: Final[int] = 256 * 1024
"""Largest text object previewed in the page and editable in place: 256 KiB.

A bigger file is still downloadable and replaceable; it is only kept out of
a ``<textarea>``, where a multi-megabyte log would make the page unusable.
"""

STORAGE_KEY_MAX_BYTES: Final[int] = 1024
"""Longest key accepted, in UTF-8 bytes — the S3 object-key limit."""

STORAGE_SPOOL_BYTES: Final[int] = 8 * 1024 * 1024
"""Bytes an upload or copy keeps in memory before spilling to a temp file."""

STORAGE_STAT_CONCURRENCY: Final[int] = 8
"""Concurrent ``stat_object`` calls made while describing one listing page."""

_STORAGE_READ_CHUNK: Final[int] = 64 * 1024
"""Bytes read per step from an upload."""

TEXT_MEDIA_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/csv",
        "application/javascript",
        "application/json",
        "application/ld+json",
        "application/sql",
        "application/toml",
        "application/x-ndjson",
        "application/x-sh",
        "application/x-yaml",
        "application/xml",
        "application/yaml",
        "image/svg+xml",
    }
)
"""Types outside ``text/*`` that are text and preview as source.

``image/svg+xml`` is here on purpose: it is never served ``inline`` (it can
carry ``<script>``), so the only safe preview is its escaped source.
"""

_MISSING_CODES: Final[frozenset[str]] = frozenset(
    {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}
)
"""``S3Error.code`` values meaning the object is not there."""


class StoragePreview(BaseStrEnum):
    """How the object page previews a stored object.

    ``IMAGE``, ``VIDEO``, ``AUDIO`` and ``PDF`` are rendered by the browser
    from the raw route, and only for types in
    :data:`~tempest_fastapi_sdk.utils.INLINE_SAFE_MEDIA_TYPES`. ``TEXT`` is
    the escaped source in the page. ``NONE`` offers the download only.
    """

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    PDF = "pdf"
    TEXT = "text"
    NONE = "none"


class StorageBrowserError(ValueError):
    """A browser operation refused for a reason the operator can fix.

    The message is shown on the page as is, so it names the key and the
    limit rather than the exception type.
    """


class StorageObjectMissingError(StorageBrowserError):
    """The object the operation names is not stored (any more)."""


class AdminStorageClient(Protocol):
    """The object-store calls :class:`StorageBrowserService` makes.

    :class:`~tempest_fastapi_sdk.storage.AsyncMinIOClient` and
    :class:`~tempest_fastapi_sdk.storage.LocalObjectStorageClient` satisfy
    it; it is also a
    :class:`~tempest_fastapi_sdk.storage.DownloadableStore`, which is what
    the raw route streams from. Parameter names are the SDK clients' own.
    """

    async def list_objects(
        self,
        prefix: str = "",
        *,
        bucket: str | None = None,
        recursive: bool = True,
    ) -> list[str]:
        """List object keys under ``prefix``.

        Args:
            prefix (str): Prefix filter.
            bucket (str | None): Source bucket.
            recursive (bool): ``False`` lists one level, folders ending in
                ``/``.

        Returns:
            list[str]: The matching keys.
        """
        ...

    async def stat_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
    ) -> ObjectStat:
        """Describe an object without reading it.

        Args:
            key (str): Object key.
            bucket (str | None): Source bucket.

        Returns:
            ObjectStat: Size, content type, ETag, metadata.
        """
        ...

    async def stream_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
        chunk_size: int = 64 * 1024,
        offset: int = 0,
        length: int | None = None,
    ) -> AsyncIterator[bytes]:
        """Open an object, or one byte range of it, as a chunk stream.

        Args:
            key (str): Object key.
            bucket (str | None): Source bucket.
            chunk_size (int): Bytes per chunk.
            offset (int): First byte to read.
            length (int | None): Bytes to read; ``None`` reads to the end.

        Returns:
            AsyncIterator[bytes]: The chunks, in order.
        """
        ...

    async def put_object(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        bucket: str | None = None,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
        length: int | None = None,
    ) -> str:
        """Store one object, replacing any previous one.

        Args:
            key (str): Object key.
            data (bytes | BinaryIO): The payload.
            bucket (str | None): Target bucket.
            content_type (str): MIME type stored with the object.
            metadata (dict[str, str] | None): User metadata.
            length (int | None): Payload size when ``data`` is a stream.

        Returns:
            str: The object's ETag.
        """
        ...

    async def remove_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
    ) -> None:
        """Delete one object; a missing key is not an error.

        Args:
            key (str): Object key.
            bucket (str | None): Target bucket.
        """
        ...


@dataclass(frozen=True, slots=True)
class StorageEntry:
    """One row of a folder listing.

    Attributes:
        key (str): Full key; a folder's ends in ``/``.
        name (str): Last segment, as the row shows it.
        is_folder (bool): ``True`` for a prefix with keys under it.
        size (int | None): Bytes; ``None`` for a folder or a file whose
            stat failed.
        content_type (str | None): Stored MIME type.
        last_modified (datetime | None): Last write, UTC.
    """

    key: str
    name: str
    is_folder: bool
    size: int | None = None
    content_type: str | None = None
    last_modified: datetime | None = None


@dataclass(frozen=True, slots=True)
class StorageCrumb:
    """One link of the breadcrumb above a listing.

    Attributes:
        name (str): Segment shown; the bucket name for the root.
        prefix (str): Prefix the link opens; ``""`` for the root.
    """

    name: str
    prefix: str


@dataclass(frozen=True, slots=True)
class StorageListing:
    """One page of one folder.

    Attributes:
        prefix (str): The folder, ``""`` or ending in ``/``.
        entries (list[StorageEntry]): Folders first, then files, each sorted
            by name.
        crumbs (list[StorageCrumb]): Root to the current folder.
        page (int): 1-based page shown.
        pages (int): Pages in the folder; at least 1.
        total (int): Entries in the folder, across pages.
    """

    prefix: str
    entries: list[StorageEntry] = field(default_factory=list)
    crumbs: list[StorageCrumb] = field(default_factory=list)
    page: int = 1
    pages: int = 1
    total: int = 0


@dataclass(frozen=True, slots=True)
class StorageObjectView:
    """What the object page shows about one stored object.

    Attributes:
        key (str): Object key.
        name (str): Last segment of the key.
        folder (str): Prefix holding the object, ``""`` or ending in ``/``.
        size (int): Bytes.
        content_type (str): Stored MIME type, ``application/octet-stream``
            when none was recorded.
        etag (str | None): ETag; sent back by the text editor to detect a
            concurrent write.
        last_modified (datetime | None): Last write, UTC.
        metadata (dict[str, str]): User metadata.
        preview (StoragePreview): How the page previews it.
        text (str | None): The decoded content for a ``TEXT`` preview that
            fits :attr:`StorageBrowserService.max_text_bytes`.
        editable (bool): Whether the in-place text editor is offered.
        crumbs (list[StorageCrumb]): Root to the holding folder.
    """

    key: str
    name: str
    folder: str
    size: int
    content_type: str
    etag: str | None
    last_modified: datetime | None
    metadata: dict[str, str]
    preview: StoragePreview
    text: str | None
    editable: bool
    crumbs: list[StorageCrumb] = field(default_factory=list)


def check_storage_key(key: str) -> str:
    """Validate a key typed or derived in the browser.

    S3 accepts almost any byte string as a key; the browser does not,
    because a key with an empty, ``.`` or ``..`` segment cannot be shown as
    a folder path, means something else on
    :class:`~tempest_fastapi_sdk.storage.LocalObjectStorageClient`, and is
    never what an operator meant to type.

    Args:
        key (str): The candidate key.

    Returns:
        str: ``key`` unchanged.

    Raises:
        StorageBrowserError: When the key is empty, longer than
            :data:`STORAGE_KEY_MAX_BYTES`, starts or ends with ``/``, holds
            an empty, ``.`` or ``..`` segment, a backslash or a control
            character.
    """
    if not key:
        raise StorageBrowserError("The name cannot be empty.")
    if len(key.encode("utf-8")) > STORAGE_KEY_MAX_BYTES:
        raise StorageBrowserError(
            f"The name is longer than {STORAGE_KEY_MAX_BYTES} bytes."
        )
    if key.startswith("/") or key.endswith("/"):
        raise StorageBrowserError(f"{key!r} cannot start or end with '/'.")
    if "\\" in key or any(ord(char) < 32 or ord(char) == 127 for char in key):
        raise StorageBrowserError(f"{key!r} holds a backslash or a control character.")
    if any(part in {"", ".", ".."} for part in key.split("/")):
        raise StorageBrowserError(f"{key!r} holds an empty, '.' or '..' path segment.")
    return key


def normalize_storage_prefix(prefix: str) -> str:
    """Return ``prefix`` as a folder: ``""`` or a valid path ending in ``/``.

    Args:
        prefix (str): The folder from the query string, with or without the
            trailing ``/``.

    Returns:
        str: ``""`` for the root, otherwise the folder with one trailing
        ``/``.

    Raises:
        StorageBrowserError: When the folder is not a valid key path.
    """
    cleaned = prefix.strip("/")
    if not cleaned:
        return ""
    return check_storage_key(cleaned) + "/"


def _crumbs(bucket_label: str, prefix: str) -> list[StorageCrumb]:
    """Build the breadcrumb from the root to ``prefix``.

    Args:
        bucket_label (str): Name shown for the root.
        prefix (str): Folder, ``""`` or ending in ``/``.

    Returns:
        list[StorageCrumb]: Root first.
    """
    crumbs: list[StorageCrumb] = [StorageCrumb(name=bucket_label, prefix="")]
    walked = ""
    for part in [part for part in prefix.split("/") if part]:
        walked += part + "/"
        crumbs.append(StorageCrumb(name=part, prefix=walked))
    return crumbs


def _is_missing(exc: BaseException) -> bool:
    """Tell whether a store exception means "no such object".

    Args:
        exc (BaseException): What the store raised.

    Returns:
        bool: ``True`` for ``FileNotFoundError`` (the local store and the
        test fake) or an ``S3Error`` whose ``code`` names a missing key or
        bucket.
    """
    if isinstance(exc, FileNotFoundError):
        return True
    return getattr(exc, "code", None) in _MISSING_CODES


def _base_type(content_type: str | None) -> str:
    """Strip parameters and case from a MIME type.

    Args:
        content_type (str | None): The stored type.

    Returns:
        str: ``"text/plain"`` for ``"Text/Plain; charset=utf-8"``; ``""``
        for ``None``.
    """
    return (content_type or "").split(";", 1)[0].strip().lower()


def guess_storage_content_type(name: str, declared: str | None = None) -> str:
    """Pick the content type an uploaded file is stored with.

    The browser's declared type wins when it is specific; a missing or
    generic ``application/octet-stream`` falls back to the extension.

    Args:
        name (str): The file name or key.
        declared (str | None): The type the browser sent with the upload.

    Returns:
        str: The type to store; ``application/octet-stream`` when nothing
        better is known.
    """
    base = _base_type(declared)
    if base and base != "application/octet-stream":
        return declared or base
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def storage_preview_for(content_type: str | None, key: str) -> StoragePreview:
    """Decide how an object is previewed.

    Args:
        content_type (str | None): Stored MIME type.
        key (str): Object key, whose extension is consulted when the stored
            type is missing or ``application/octet-stream``.

    Returns:
        StoragePreview: A rendered preview only for a type the raw route
        will serve ``inline``; ``TEXT`` for textual types; otherwise
        ``NONE``.
    """
    base = _base_type(content_type)
    if not base or base == "application/octet-stream":
        base = _base_type(mimetypes.guess_type(key)[0])
    if base in INLINE_SAFE_MEDIA_TYPES:
        major = base.split("/", 1)[0]
        if major == "image":
            return StoragePreview.IMAGE
        if major == "video":
            return StoragePreview.VIDEO
        if major == "audio":
            return StoragePreview.AUDIO
        if base == "application/pdf":
            return StoragePreview.PDF
    if base.startswith("text/") or base in TEXT_MEDIA_TYPES:
        return StoragePreview.TEXT
    return StoragePreview.NONE


async def _gather_bounded[T](
    calls: list[Callable[[], Awaitable[T]]],
    limit: int,
) -> list[T]:
    """Run ``calls`` with at most ``limit`` in flight, keeping order.

    Args:
        calls (list[Callable[[], Awaitable[T]]]): Zero-argument coroutine
            factories.
        limit (int): Maximum concurrent calls.

    Returns:
        list[T]: The results, in the order of ``calls``.
    """
    gate = asyncio.Semaphore(limit)

    async def _run(call: Callable[[], Awaitable[T]]) -> T:
        """Await ``call`` once a slot is free.

        Args:
            call (Callable[[], Awaitable[T]]): The call to make.

        Returns:
            T: Its result.
        """
        async with gate:
            return await call()

    return list(await asyncio.gather(*(_run(call) for call in calls)))


class StorageBrowserService:
    """List, preview and edit the objects of one bucket from the admin.

    Example:

        >>> from tempest_fastapi_sdk.admin import StorageBrowserService
        >>> from tempest_fastapi_sdk.storage import LocalObjectStorageClient
        >>> store = LocalObjectStorageClient(
        ...     "/var/lib/app/storage",
        ...     public_url="https://api.example.com",
        ...     signing_secret=settings.JWT_SECRET,
        ... )
        >>> browser = StorageBrowserService(store)
        >>> make_admin_router(site, ..., storage=browser)

    Attributes:
        client (AdminStorageClient): The store.
        bucket (str | None): Bucket browsed; ``None`` is the client's
            default.
        label (str): Name of the root in the breadcrumb.
        read_only (bool): ``True`` hides and refuses every write.
        page_size (int): Entries per listing page.
        max_upload_bytes (int): Largest upload or replace accepted.
        max_text_bytes (int): Largest text previewed and edited in place.
    """

    def __init__(
        self,
        client: AdminStorageClient,
        *,
        bucket: str | None = None,
        label: str | None = None,
        read_only: bool = False,
        page_size: int = DEFAULT_STORAGE_PAGE_SIZE,
        max_upload_bytes: int = DEFAULT_STORAGE_MAX_UPLOAD_BYTES,
        max_text_bytes: int = DEFAULT_STORAGE_MAX_TEXT_BYTES,
    ) -> None:
        """Keep the store and the limits the screen enforces.

        Args:
            client (AdminStorageClient): The store to browse.
            bucket (str | None): Bucket to browse; ``None`` uses the client's
                default bucket.
            label (str | None): Name of the root in the breadcrumb; defaults
                to ``bucket``, or ``"storage"``.
            read_only (bool): ``True`` keeps listing, preview and download
                and refuses upload, replace, rename, edit and delete.
                Default ``False``.
            page_size (int): Entries per listing page. Default
                :data:`DEFAULT_STORAGE_PAGE_SIZE`.
            max_upload_bytes (int): Largest upload or replace. Default
                :data:`DEFAULT_STORAGE_MAX_UPLOAD_BYTES`.
            max_text_bytes (int): Largest text object previewed in the page
                and edited in place. Default
                :data:`DEFAULT_STORAGE_MAX_TEXT_BYTES`.

        Raises:
            ValueError: When a size or the page size is not positive.
        """
        if page_size < 1:
            raise ValueError("page_size must be at least 1")
        if max_upload_bytes < 1:
            raise ValueError("max_upload_bytes must be at least 1")
        if max_text_bytes < 1:
            raise ValueError("max_text_bytes must be at least 1")
        self.client: AdminStorageClient = client
        self.bucket: str | None = bucket
        self.label: str = label or bucket or "storage"
        self.read_only: bool = read_only
        self.page_size: int = page_size
        self.max_upload_bytes: int = max_upload_bytes
        self.max_text_bytes: int = max_text_bytes

    def _check_writable(self) -> None:
        """Refuse a write on a read-only browser.

        Raises:
            StorageBrowserError: When :attr:`read_only` is set.
        """
        if self.read_only:
            raise StorageBrowserError("This storage browser is read-only.")

    async def _stat(self, key: str) -> ObjectStat:
        """Stat ``key``, translating "missing" into the browser's error.

        Args:
            key (str): Object key.

        Returns:
            ObjectStat: The object's metadata.

        Raises:
            StorageObjectMissingError: When the object is not stored.
        """
        try:
            return await self.client.stat_object(key, bucket=self.bucket)
        except Exception as exc:
            if _is_missing(exc):
                raise StorageObjectMissingError(f"{key!r} is not stored.") from exc
            raise

    async def exists(self, key: str) -> bool:
        """Tell whether ``key`` is stored.

        Args:
            key (str): Object key.

        Returns:
            bool: ``True`` when ``stat_object`` finds it.
        """
        try:
            await self._stat(key)
        except StorageObjectMissingError:
            return False
        return True

    async def _describe_entry(self, key: str, name: str) -> StorageEntry:
        """Stat one file of a listing; a failed stat leaves the columns blank.

        A file that disappears between the listing and the stat, or one the
        store refuses to describe, still gets its row: hiding it would make
        the page disagree with the store's own listing.

        Args:
            key (str): Object key.
            name (str): Last segment.

        Returns:
            StorageEntry: The row.
        """
        try:
            stat = await self.client.stat_object(key, bucket=self.bucket)
        except Exception:
            return StorageEntry(key=key, name=name, is_folder=False)
        return StorageEntry(
            key=key,
            name=name,
            is_folder=False,
            size=stat.size,
            content_type=stat.content_type,
            last_modified=stat.last_modified,
        )

    async def browse(self, prefix: str = "", *, page: int = 1) -> StorageListing:
        """List one folder, folders first, one page at a time.

        Both a one-level listing (folders ending in ``/``) and a recursive
        one are folded into the folder's direct children, so a store that
        ignores ``recursive=False`` still lists correctly.

        Args:
            prefix (str): The folder; ``""`` for the root.
            page (int): 1-based page; clamped into range.

        Returns:
            StorageListing: The page, its files stat-ed for size, type and
            date. An empty or unknown folder is an empty listing.

        Raises:
            StorageBrowserError: When ``prefix`` is not a valid folder.
        """
        folder = normalize_storage_prefix(prefix)
        keys = await self.client.list_objects(
            folder, bucket=self.bucket, recursive=False
        )
        folders: set[str] = set()
        files: set[str] = set()
        for key in keys:
            if not key.startswith(folder):
                continue
            rest = key[len(folder) :]
            if not rest:
                continue
            head, slash, _ = rest.partition("/")
            if slash:
                folders.add(head)
            else:
                files.add(head)
        ordered: list[tuple[str, bool]] = [(name, True) for name in sorted(folders)]
        ordered.extend((name, False) for name in sorted(files))
        total = len(ordered)
        pages = max(1, -(-total // self.page_size))
        current = min(max(1, page), pages)
        start = (current - 1) * self.page_size
        window = ordered[start : start + self.page_size]
        entries: list[StorageEntry] = [
            StorageEntry(key=f"{folder}{name}/", name=name, is_folder=True)
            for name, is_folder in window
            if is_folder
        ]
        entries.extend(
            await _gather_bounded(
                [
                    partial(self._describe_entry, folder + name, name)
                    for name, is_folder in window
                    if not is_folder
                ],
                STORAGE_STAT_CONCURRENCY,
            )
        )
        return StorageListing(
            prefix=folder,
            entries=entries,
            crumbs=_crumbs(self.label, folder),
            page=current,
            pages=pages,
            total=total,
        )

    async def _read_head(self, key: str, limit: int) -> bytes:
        """Read at most ``limit`` bytes from the start of ``key``.

        Args:
            key (str): Object key.
            limit (int): Bytes to read.

        Returns:
            bytes: The first bytes of the object.
        """
        stream = await self.client.stream_object(key, bucket=self.bucket, length=limit)
        chunks: list[bytes] = [chunk async for chunk in stream]
        return b"".join(chunks)[:limit]

    async def describe(self, key: str) -> StorageObjectView:
        """Describe one object for its page, text content included.

        Args:
            key (str): Object key.

        Returns:
            StorageObjectView: Metadata, preview kind and, for a text object
            within :attr:`max_text_bytes` that decodes as UTF-8, its content.
            Text that does not decode is downgraded to ``NONE``.

        Raises:
            StorageBrowserError: When the key is invalid.
            StorageObjectMissingError: When the object is not stored.
        """
        check_storage_key(key)
        stat = await self._stat(key)
        folder, _, name = key.rpartition("/")
        folder = f"{folder}/" if folder else ""
        content_type = stat.content_type or "application/octet-stream"
        preview = storage_preview_for(stat.content_type, key)
        text: str | None = None
        if preview is StoragePreview.TEXT and stat.size <= self.max_text_bytes:
            raw = await self._read_head(key, self.max_text_bytes) if stat.size else b""
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                preview = StoragePreview.NONE
        return StorageObjectView(
            key=key,
            name=name,
            folder=folder,
            size=stat.size,
            content_type=content_type,
            etag=stat.etag,
            last_modified=stat.last_modified,
            metadata=dict(stat.metadata),
            preview=preview,
            text=text,
            editable=text is not None and not self.read_only,
            crumbs=_crumbs(self.label, folder),
        )

    async def _spool(
        self,
        read: Callable[[int], Awaitable[bytes]],
        *,
        capped: bool = True,
    ) -> tuple[BinaryIO, int]:
        """Copy a reader into a spooled temp file.

        Args:
            read (Callable[[int], Awaitable[bytes]]): Reads up to ``n`` bytes;
                ``b""`` at the end.
            capped (bool): Enforce :attr:`max_upload_bytes`. ``False`` for a
                rename, which moves bytes already in the store.

        Returns:
            tuple[BinaryIO, int]: The spool, rewound, and its size. The
            caller closes it.

        Raises:
            StorageBrowserError: When ``capped`` and the content exceeds
                :attr:`max_upload_bytes`.
        """
        spool = cast(
            "BinaryIO",
            tempfile.SpooledTemporaryFile(max_size=STORAGE_SPOOL_BYTES),  # noqa: SIM115
        )
        size = 0
        try:
            while chunk := await read(_STORAGE_READ_CHUNK):
                size += len(chunk)
                if capped and size > self.max_upload_bytes:
                    raise StorageBrowserError(
                        f"The file is larger than {self.max_upload_bytes} bytes."
                    )
                await asyncio.to_thread(spool.write, chunk)
        except BaseException:
            spool.close()
            raise
        spool.seek(0)
        return spool, size

    async def upload(
        self,
        folder: str,
        name: str,
        read: Callable[[int], Awaitable[bytes]],
        *,
        content_type: str | None = None,
        overwrite: bool = False,
    ) -> str:
        """Store a new file under ``folder``.

        Args:
            folder (str): Target folder; ``""`` for the root.
            name (str): File name, or a relative path (``"a/b.txt"``)
                that creates the folders it names.
            read (Callable[[int], Awaitable[bytes]]): Reads the upload in
                chunks — ``UploadFile.read``.
            content_type (str | None): Type the browser declared.
            overwrite (bool): Replace an object already stored under the key.
                Default ``False``.

        Returns:
            str: The key written.

        Raises:
            StorageBrowserError: When the browser is read-only, the key is
                invalid, the key is taken and ``overwrite`` is off, or the
                file is over :attr:`max_upload_bytes`.
        """
        self._check_writable()
        key = check_storage_key(normalize_storage_prefix(folder) + name.strip("/"))
        if not overwrite and await self.exists(key):
            raise StorageBrowserError(
                f"{key!r} already exists. Tick 'overwrite' to replace it."
            )
        spool, size = await self._spool(read)
        try:
            await self.client.put_object(
                key,
                spool,
                bucket=self.bucket,
                content_type=guess_storage_content_type(name, content_type),
                length=size,
            )
        finally:
            spool.close()
        return key

    async def replace(
        self,
        key: str,
        read: Callable[[int], Awaitable[bytes]],
        *,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> None:
        """Overwrite an existing object's content, keeping its key and metadata.

        Args:
            key (str): Object key.
            read (Callable[[int], Awaitable[bytes]]): Reads the new content in
                chunks.
            content_type (str | None): Type the browser declared; when
                generic, guessed from ``filename``, then the key.
            filename (str | None): The uploaded file's own name.

        Raises:
            StorageBrowserError: When the browser is read-only, the key is
                invalid or the file is over :attr:`max_upload_bytes`.
            StorageObjectMissingError: When the object is not stored.
        """
        self._check_writable()
        check_storage_key(key)
        stat = await self._stat(key)
        spool, size = await self._spool(read)
        try:
            await self.client.put_object(
                key,
                spool,
                bucket=self.bucket,
                content_type=guess_storage_content_type(filename or key, content_type),
                metadata=dict(stat.metadata),
                length=size,
            )
        finally:
            spool.close()

    async def save_text(
        self,
        key: str,
        text: str,
        *,
        expected_etag: str | None,
    ) -> None:
        """Write edited text back, refusing when someone wrote in between.

        Browsers submit a ``<textarea>`` with ``\\r\\n`` line ends; they are
        written back as ``\\n`` when the original held no ``\\r\\n``, so
        saving a Unix file unchanged does not rewrite every line.

        Args:
            key (str): Object key.
            text (str): The new content.
            expected_etag (str | None): ETag the editor was opened at;
                ``None`` skips the check.

        Raises:
            StorageBrowserError: When the browser is read-only, the key is
                invalid, the object is not editable text, the content is over
                :attr:`max_text_bytes`, or its ETag changed since the editor
                opened.
            StorageObjectMissingError: When the object is not stored.
        """
        self._check_writable()
        view = await self.describe(key)
        if not view.editable or view.text is None:
            raise StorageBrowserError(f"{key!r} cannot be edited as text.")
        if expected_etag is not None and view.etag != expected_etag:
            raise StorageBrowserError(
                f"{key!r} changed since you opened it. Reload and edit again."
            )
        if "\r\n" not in view.text:
            text = text.replace("\r\n", "\n")
        payload = text.encode("utf-8")
        if len(payload) > self.max_text_bytes:
            raise StorageBrowserError(
                f"The text is larger than {self.max_text_bytes} bytes."
            )
        await self.client.put_object(
            key,
            payload,
            bucket=self.bucket,
            content_type=view.content_type,
            metadata=view.metadata,
        )

    async def rename(self, key: str, new_key: str) -> str:
        """Move an object to another key, folder included.

        Object stores have no rename: this copies the bytes, type and
        metadata to ``new_key``, then deletes ``key``. The copy streams
        through this process, so it costs one download and one upload of
        the object.

        Args:
            key (str): Current key.
            new_key (str): Target key; a leading ``/`` is ignored.

        Returns:
            str: The new key.

        Raises:
            StorageBrowserError: When the browser is read-only, a key is
                invalid, the keys are equal, or ``new_key`` is taken.
            StorageObjectMissingError: When the object is not stored.
        """
        self._check_writable()
        check_storage_key(key)
        target = check_storage_key(new_key.strip().lstrip("/"))
        if target == key:
            raise StorageBrowserError("The new name is the current one.")
        if await self.exists(target):
            raise StorageBrowserError(f"{target!r} already exists.")
        stat = await self._stat(key)
        stream = await self.client.stream_object(key, bucket=self.bucket)
        iterator = stream.__aiter__()

        async def _next_chunk(_: int) -> bytes:
            """Return the next chunk of the source, ``b""`` at its end.

            Args:
                _ (int): Ignored; the store picks the chunk size.

            Returns:
                bytes: The chunk.
            """
            try:
                return await anext(iterator)
            except StopAsyncIteration:
                return b""

        spool, size = await self._spool(_next_chunk, capped=False)
        try:
            await self.client.put_object(
                target,
                spool,
                bucket=self.bucket,
                content_type=stat.content_type or "application/octet-stream",
                metadata=dict(stat.metadata),
                length=size,
            )
        finally:
            spool.close()
        await self.client.remove_object(key, bucket=self.bucket)
        return target

    async def delete(self, key: str) -> None:
        """Delete one object.

        Args:
            key (str): Object key.

        Raises:
            StorageBrowserError: When the browser is read-only or the key is
                invalid.
            StorageObjectMissingError: When the object is not stored —
                reported so a double submit does not claim a second delete.
        """
        self._check_writable()
        check_storage_key(key)
        await self._stat(key)
        await self.client.remove_object(key, bucket=self.bucket)


__all__: list[str] = [
    "DEFAULT_STORAGE_MAX_TEXT_BYTES",
    "DEFAULT_STORAGE_MAX_UPLOAD_BYTES",
    "DEFAULT_STORAGE_PAGE_SIZE",
    "STORAGE_KEY_MAX_BYTES",
    "STORAGE_SPOOL_BYTES",
    "STORAGE_STAT_CONCURRENCY",
    "TEXT_MEDIA_TYPES",
    "AdminStorageClient",
    "StorageBrowserError",
    "StorageBrowserService",
    "StorageCrumb",
    "StorageEntry",
    "StorageListing",
    "StorageObjectMissingError",
    "StorageObjectView",
    "StoragePreview",
    "check_storage_key",
    "guess_storage_content_type",
    "normalize_storage_prefix",
    "storage_preview_for",
]
