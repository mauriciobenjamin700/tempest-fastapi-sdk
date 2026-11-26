"""Google Drive as an :class:`~tempest_fastapi_sdk.utils.UploadStorage` backend.

Drive addresses files by id, not by path, and two files in one folder may
share a name. So the ``key`` an
:class:`~tempest_fastapi_sdk.utils.UploadUtils` hands the backend is stored
in the file's ``appProperties`` (private to this app) under
:data:`TEMPEST_KEY_PROPERTY`, and every lookup searches that property inside
the configured folder::

    '<folder>' in parents
    and appProperties has { key='tempest_key' and value='<key>' }
    and trashed = false

The file's ``name`` is only for people browsing the folder: the last
segment of the key.

**Writing a key that already exists replaces the content of that file** —
same id, same link, new media (``files.update``) — instead of creating a
second file, matching what the local and MinIO backends do with an existing
key. Two writes racing on a brand-new key can still both create a file:
Drive has no conditional create. :meth:`GoogleDriveUploadStorage.exists`
then answers ``True``, :meth:`GoogleDriveUploadStorage.delete` removes every
match, and the next write replaces the first match.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence
from datetime import timedelta
from pathlib import Path

import httpx

from tempest_fastapi_sdk.exceptions.integration import (
    GoogleDriveException,
    GoogleDriveFolderNotFoundException,
)
from tempest_fastapi_sdk.exceptions.storage import PresignedUrlUnsupportedException
from tempest_fastapi_sdk.exceptions.upload import (
    FileTooLargeException,
    InvalidFileTypeException,
)
from tempest_fastapi_sdk.exceptions.validation import ValidationException
from tempest_fastapi_sdk.integrations.storage.google_drive.auth import (
    DRIVE_SCOPE,
    ServiceAccountCredentials,
    ServiceAccountTokenProvider,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.client import (
    DEFAULT_UPLOAD_CHUNK_SIZE,
    UPLOAD_SESSION_NOT_FOUND_REASON,
    GoogleDriveClient,
    GoogleDriveFile,
    escape_query_value,
)
from tempest_fastapi_sdk.utils.storage_backends import ContentValidator, UploadResult

TEMPEST_KEY_PROPERTY: str = "tempest_key"
"""The ``appProperties`` key that holds the ``UploadStorage`` key."""

APP_PROPERTY_MAX_BYTES: int = 124
"""Drive's documented ceiling for one property, key plus value, in UTF-8.

From "Add custom file properties"
(https://developers.google.com/workspace/drive/api/guides/properties).
Enforced here so a long key fails before any byte is sent instead of at
the API; not measured against the live service.
"""

GOOGLE_DRIVE_BACKEND: str = "google_drive"
"""Backend name carried by :class:`PresignedUrlUnsupportedException`."""

DEFAULT_HTTP_TIMEOUT_SECONDS: float = 60.0
"""Timeout of the ``httpx.AsyncClient`` built by ``from_service_account``."""


class GoogleDriveUploadStorage:
    """:class:`~tempest_fastapi_sdk.utils.UploadStorage` on a Google Drive folder.

    Plug it into :class:`~tempest_fastapi_sdk.utils.UploadUtils` unchanged::

        storage = GoogleDriveUploadStorage.from_service_account(
            **settings.google_drive_kwargs()
        )
        uploads = UploadUtils(backend=storage, max_size_bytes=10 * 1024 * 1024)

    Validation (extension, MIME, size, magic bytes) stays in ``UploadUtils``;
    this backend runs ``validator`` on the first chunk **before** opening
    the upload session, and stops at ``max_size_bytes`` mid-stream — the
    resumable session is then abandoned before its last piece, which, as
    Google's upload guide describes the session, should leave no file
    (not measured against the live service).
    """

    def __init__(
        self,
        client: GoogleDriveClient,
        *,
        folder_id: str,
        drive_id: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialize the backend.

        Args:
            client (GoogleDriveClient): A configured Drive client.
            folder_id (str): Folder every file is written into and searched
                in. It must be shared with the client's account.
            drive_id (str | None): Shared Drive holding the folder; narrows
                searches to it. ``None`` searches every drive the account
                sees.
            http_client (httpx.AsyncClient | None): An HTTP client this
                backend owns and closes in :meth:`aclose`. Leave ``None``
                when the caller owns the client.

        Raises:
            ValueError: When ``folder_id`` is empty.
        """
        if not folder_id:
            raise ValueError(
                "GoogleDriveUploadStorage needs a folder_id (GOOGLE_DRIVE_FOLDER_ID)."
            )
        self.client: GoogleDriveClient = client
        self.folder_id: str = folder_id
        self.drive_id: str | None = drive_id
        self._owned_http: httpx.AsyncClient | None = http_client

    @classmethod
    def from_service_account(
        cls,
        *,
        folder_id: str,
        credentials_path: str | Path | None = None,
        credentials_json: str | None = None,
        drive_id: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        scopes: Sequence[str] = (DRIVE_SCOPE,),
        chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE,
    ) -> GoogleDriveUploadStorage:
        """Build the backend from a service-account key.

        The keywords ``folder_id``, ``credentials_path``,
        ``credentials_json`` and ``drive_id`` are what
        :meth:`~tempest_fastapi_sdk.settings.GoogleDriveSettings.google_drive_kwargs`
        returns.

        Args:
            folder_id (str): Target folder id.
            credentials_path (str | Path | None): Service-account key file.
            credentials_json (str | None): The key inline; wins over the
                file.
            drive_id (str | None): Shared Drive holding the folder.
            http_client (httpx.AsyncClient | None): Client for the token
                endpoint and the API. ``None`` builds one the backend owns
                and closes in :meth:`aclose`.
            scopes (Sequence[str]): OAuth scopes. Defaults to full Drive.
            chunk_size (int): Resumable piece size, a multiple of 256 KiB.

        Returns:
            GoogleDriveUploadStorage: The backend.

        Raises:
            ValueError: When no credentials are given, they are not a
                service-account key, or ``folder_id`` is empty.
            OSError: When the key file cannot be read.
        """
        credentials = ServiceAccountCredentials.load(
            credentials_path=credentials_path,
            credentials_json=credentials_json,
        )
        if not folder_id:
            raise ValueError(
                "GoogleDriveUploadStorage needs a folder_id (GOOGLE_DRIVE_FOLDER_ID)."
            )
        owned: httpx.AsyncClient | None = None
        if http_client is None:
            owned = http = httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT_SECONDS)
        else:
            http = http_client
        provider = ServiceAccountTokenProvider(
            credentials, http_client=http, scopes=scopes
        )
        client = GoogleDriveClient(
            token_provider=provider,
            http_client=http,
            principal_email=credentials.client_email,
            chunk_size=chunk_size,
        )
        return cls(client, folder_id=folder_id, drive_id=drive_id, http_client=owned)

    async def aclose(self) -> None:
        """Close the HTTP client this backend built, if it built one."""
        if self._owned_http is not None:
            await self._owned_http.aclose()

    def _query(self, key: str) -> str:
        """Build the ``q`` expression that finds ``key`` in the folder."""
        folder = escape_query_value(self.folder_id)
        name = escape_query_value(TEMPEST_KEY_PROPERTY)
        value = escape_query_value(key)
        return (
            f"'{folder}' in parents"
            f" and appProperties has {{ key='{name}' and value='{value}' }}"
            " and trashed = false"
        )

    def _folder_missing(self, exc: GoogleDriveException) -> GoogleDriveException:
        """Turn a folder ``404`` into the actionable folder-not-found error.

        A ``404`` from a resumable session that expired (reason
        :data:`UPLOAD_SESSION_NOT_FOUND_REASON`) is about the session, not
        the folder, and is returned unchanged.

        Args:
            exc (GoogleDriveException): The error from the client.

        Returns:
            GoogleDriveException: The error to raise.
        """
        if exc.upstream_status == 404 and exc.reason != UPLOAD_SESSION_NOT_FOUND_REASON:
            return GoogleDriveFolderNotFoundException(
                self.folder_id, principal_email=self.client.principal_email
            )
        return exc

    async def find(self, key: str) -> list[GoogleDriveFile]:
        """Return every non-trashed file in the folder stored under ``key``.

        Args:
            key (str): Storage key.

        Returns:
            list[GoogleDriveFile]: The matches — normally zero or one;
            ``[]`` when none.

        Raises:
            GoogleDriveFolderNotFoundException: When Drive answers ``404``.
            GoogleDriveException: On any other error answer.
        """
        try:
            return await self.client.list_files(
                self._query(key), drive_id=self.drive_id
            )
        except GoogleDriveException as exc:
            raise self._folder_missing(exc) from exc

    @staticmethod
    def _app_properties(key: str, metadata: Mapping[str, str] | None) -> dict[str, str]:
        """Merge the key and the caller's metadata into ``appProperties``.

        Args:
            key (str): Storage key.
            metadata (Mapping[str, str] | None): Caller's extra properties.

        Returns:
            dict[str, str]: The ``appProperties`` to send.

        Raises:
            ValueError: When ``metadata`` tries to set the reserved
                :data:`TEMPEST_KEY_PROPERTY`.
            ValidationException: When one property (name plus value, in
                UTF-8) exceeds :data:`APP_PROPERTY_MAX_BYTES` — ``422``.
        """
        properties: dict[str, str] = {}
        for name, value in (metadata or {}).items():
            if name == TEMPEST_KEY_PROPERTY:
                raise ValueError(
                    f"metadata must not set the reserved {TEMPEST_KEY_PROPERTY!r}."
                )
            properties[name] = value
        properties[TEMPEST_KEY_PROPERTY] = key
        for name, value in properties.items():
            used = len(name.encode()) + len(value.encode())
            if used > APP_PROPERTY_MAX_BYTES:
                raise ValidationException(
                    message=(
                        f"Google Drive property {name!r} takes {used} bytes "
                        "(name plus value, UTF-8); Drive allows at most "
                        f"{APP_PROPERTY_MAX_BYTES}. Use a shorter key or "
                        "metadata value."
                    ),
                    details={
                        "property": name,
                        "bytes": used,
                        "limit_bytes": APP_PROPERTY_MAX_BYTES,
                    },
                )
        return properties

    async def write_stream(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
        max_size_bytes: int | None = None,
        validator: ContentValidator | None = None,
    ) -> UploadResult:
        """Upload ``chunks`` to the folder under ``key``.

        When a file with ``key`` already exists its content is replaced
        (same id and link); otherwise a file named after the key's last
        segment is created.

        Args:
            key (str): Storage key, stored in ``appProperties``.
            chunks (AsyncIterator[bytes]): Source stream.
            content_type (str): MIME type stored on the file.
            metadata (dict[str, str] | None): Extra ``appProperties``.
            max_size_bytes (int | None): Abort once the stream exceeds it.
            validator (ContentValidator | None): Run on the first chunk,
                before any request to Drive.

        Returns:
            UploadResult: ``key``, the size Drive reports (the bytes sent
            when it reports none), and ``url`` = the file's
            ``webViewLink`` — a permanent link that opens only for someone
            with access to the file, not a download URL.

        When the write fails for any reason, ``chunks`` is closed
        (``aclose``) if it is an async generator, so a caller's ``finally``
        runs right away instead of when the generator is collected.

        Raises:
            ValidationException: When the key or a metadata property is too
                long for a Drive property (``422``, the message names
                :data:`APP_PROPERTY_MAX_BYTES`).
            InvalidFileTypeException: When ``validator`` rejects the first
                chunk.
            FileTooLargeException: When ``max_size_bytes`` is exceeded.
            GoogleDriveFolderNotFoundException: When the folder is missing
                or not shared with the account (a ``404`` on the search or
                on opening the upload session).
            GoogleDriveException: On any other Drive error — including a
                ``404`` from an expired resumable session, with reason
                :data:`~tempest_fastapi_sdk.integrations.storage.google_drive.UPLOAD_SESSION_NOT_FOUND_REASON`.
        """
        try:
            return await self._write_stream(
                key,
                chunks,
                content_type=content_type,
                metadata=metadata,
                max_size_bytes=max_size_bytes,
                validator=validator,
            )
        except BaseException:
            if isinstance(chunks, AsyncGenerator):
                await chunks.aclose()
            raise

    async def _write_stream(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        content_type: str,
        metadata: dict[str, str] | None,
        max_size_bytes: int | None,
        validator: ContentValidator | None,
    ) -> UploadResult:
        """Do the work of :meth:`write_stream`.

        Args:
            key (str): Storage key.
            chunks (AsyncIterator[bytes]): Source stream.
            content_type (str): MIME type stored on the file.
            metadata (dict[str, str] | None): Extra ``appProperties``.
            max_size_bytes (int | None): Abort once the stream exceeds it.
            validator (ContentValidator | None): Run on the first chunk.

        Returns:
            UploadResult: See :meth:`write_stream`.

        Raises:
            AppException: See :meth:`write_stream`.
        """
        properties = self._app_properties(key, metadata)
        sent = 0

        async def guarded() -> AsyncIterator[bytes]:
            """Yield ``chunks`` through the validator and the size ceiling."""
            nonlocal sent
            first = True
            async for chunk in chunks:
                if first:
                    first = False
                    if validator is not None and not validator(chunk):
                        raise InvalidFileTypeException(
                            details={"reason": "validator rejected first chunk"},
                        )
                sent += len(chunk)
                if max_size_bytes is not None and sent > max_size_bytes:
                    raise FileTooLargeException(
                        details={"max_size_bytes": max_size_bytes},
                    )
                yield chunk

        existing = await self.find(key)
        name = key.rstrip("/").rsplit("/", 1)[-1] or key
        body: dict[str, object] = {
            "name": name,
            "mimeType": content_type,
            "appProperties": properties,
        }
        if existing:
            target: str | None = existing[0].id
        else:
            target = None
            body["parents"] = [self.folder_id]
        try:
            uploaded = await self.client.upload(
                guarded(), metadata=body, content_type=content_type, file_id=target
            )
        except GoogleDriveException as exc:
            if target is None and exc.upstream_status == 404:
                raise self._folder_missing(exc) from exc
            raise
        if uploaded.web_view_link is None:
            uploaded = await self.client.get_file(uploaded.id)
        return UploadResult(
            key=key,
            size=uploaded.size if uploaded.size is not None else sent,
            path=None,
            url=uploaded.web_view_link,
        )

    async def delete(self, key: str) -> bool:
        """Permanently delete every file stored under ``key``.

        Args:
            key (str): Storage key.

        Returns:
            bool: ``True`` when at least one file was removed, ``False``
            when none matched.

        Raises:
            GoogleDriveException: On a Drive error.
        """
        removed = False
        for found in await self.find(key):
            removed = await self.client.delete_file(found.id) or removed
        return removed

    async def exists(self, key: str) -> bool:
        """Return whether a file is stored under ``key`` in the folder.

        Args:
            key (str): Storage key.

        Returns:
            bool: ``True`` when at least one non-trashed file matches.

        Raises:
            GoogleDriveException: On a Drive error.
        """
        return bool(await self.find(key))

    async def web_view_link(self, key: str) -> str | None:
        """Return the Drive link of the file under ``key``.

        Permanent, and it opens only for someone with Drive access to the
        file — useful in an admin screen, not as a public download URL.

        Args:
            key (str): Storage key.

        Returns:
            str | None: The ``webViewLink``, or ``None`` when no file
            matches.

        Raises:
            GoogleDriveException: On a Drive error.
        """
        matches = await self.find(key)
        return matches[0].web_view_link if matches else None

    async def presigned_url(
        self,
        key: str,
        *,
        expires: timedelta = timedelta(hours=1),
    ) -> str | None:
        """Refuse: Google Drive has no presigned URLs.

        Drive serves a file to callers holding a Drive permission; there is
        no temporary signed link to mint, and returning the permanent
        ``webViewLink`` here would let a caller treat it as one that
        expires. Stream the file through an authenticated route of your
        own, or read :meth:`web_view_link` when a Drive link is what you
        want.

        Args:
            key (str): Storage key (unused).
            expires (timedelta): Requested lifetime (unused).

        Returns:
            str | None: Never returns.

        Raises:
            PresignedUrlUnsupportedException: Always (``501``).
        """
        del key, expires
        raise PresignedUrlUnsupportedException(
            GOOGLE_DRIVE_BACKEND,
            message=(
                "The Google Drive backend cannot mint presigned URLs: Drive has "
                "none. Serve the file through your own authenticated route, or "
                "use GoogleDriveUploadStorage.web_view_link for the Drive link."
            ),
        )


__all__: list[str] = [
    "APP_PROPERTY_MAX_BYTES",
    "DEFAULT_HTTP_TIMEOUT_SECONDS",
    "GOOGLE_DRIVE_BACKEND",
    "TEMPEST_KEY_PROPERTY",
    "GoogleDriveUploadStorage",
]
