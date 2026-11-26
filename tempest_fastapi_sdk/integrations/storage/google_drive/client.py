"""An async client for the Drive API v3 calls an upload backend needs.

Five calls, each written from Google's reference
(https://developers.google.com/workspace/drive/api/reference/rest/v3/files):
``files.list``, ``files.get``, ``files.delete``, and ``files.create`` /
``files.update`` through the **resumable** upload protocol
(https://developers.google.com/workspace/drive/api/guides/manage-uploads#resumable).

Every call sends ``supportsAllDrives=true``, so a folder inside a Shared
Drive behaves like one in My Drive.

The resumable upload streams in pieces of a multiple of 256 KiB (the
protocol's granularity). Each piece that is not the last says
``Content-Range: bytes <first>-<last>/*``; the server answers
``308 Resume Incomplete`` with a ``Range: bytes=0-<n>`` header naming what
it kept, and the next piece starts right after it, so bytes the server did
not keep are re-sent and nothing else is. The client reads one piece
ahead, so the last ``PUT`` always carries bytes **and** the total — a
payload that is an exact multiple of the piece size never ends on an empty
``bytes */<total>``, which the protocol also uses as a status query. The
only empty closing ``PUT`` is the zero-byte file's ``bytes */0``.

A ``429``/``5xx``, a ``403`` whose reason is ``rateLimitExceeded`` or
``userRateLimitExceeded``, or a transport error is retried with
exponential backoff — or after the ``Retry-After`` seconds of a
``429``/``503``, capped at ``backoff_max_seconds`` — up to ``max_retries``
times. On a piece, the client first asks the session how much it holds
(``Content-Range: bytes */<total or *>``); the stream already sent is
never replayed. A ``404`` on a piece or a status query means the session
itself is gone (reason :data:`UPLOAD_SESSION_NOT_FOUND_REASON`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
)
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

from tempest_fastapi_sdk.core.redaction import RedactionPolicy
from tempest_fastapi_sdk.exceptions.integration import GoogleDriveException
from tempest_fastapi_sdk.integrations.storage.google_drive.auth import (
    AccessTokenProvider,
)

DRIVE_API_BASE_URL: str = "https://www.googleapis.com/drive/v3"
"""Base URL of the Drive API v3 metadata endpoints."""

DRIVE_UPLOAD_BASE_URL: str = "https://www.googleapis.com/upload/drive/v3"
"""Base URL of the Drive API v3 media upload endpoints."""

UPLOAD_CHUNK_GRANULARITY: int = 256 * 1024
"""Every piece but the last must be a multiple of this (256 KiB)."""

DEFAULT_UPLOAD_CHUNK_SIZE: int = 32 * UPLOAD_CHUNK_GRANULARITY
"""Default piece size of a resumable upload: 8 MiB."""

DEFAULT_MAX_RETRIES: int = 5
"""How many times one request or one piece is retried on a transient error."""

RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
"""Statuses retried with backoff; every other error but a rate-limit ``403``
is raised at once."""

RATE_LIMIT_REASONS: frozenset[str] = frozenset(
    {"rateLimitExceeded", "userRateLimitExceeded"}
)
"""``error.errors[].reason`` values that make a ``403`` retryable.

Drive reports per-user and per-project quota as ``403`` with one of these
reasons (https://developers.google.com/workspace/drive/api/guides/handle-errors).
"""

RETRY_AFTER_STATUSES: frozenset[int] = frozenset({429, 503})
"""Statuses whose ``Retry-After`` (in seconds) replaces the backoff delay."""

UPLOAD_SESSION_NOT_FOUND_REASON: str = "upload_session_not_found"
"""``reason`` of the error raised when a resumable session answers ``404``."""

FILE_FIELDS: str = "id,name,mimeType,size,webViewLink,appProperties"
"""The ``fields`` mask requested for every file resource."""

RESUME_INCOMPLETE: int = 308
"""Status of a resumable session that has not received the whole file yet."""

_REDACTION: RedactionPolicy = RedactionPolicy()
"""Redacts tokens and e-mails out of upstream error text before it is raised."""

_logger: logging.Logger = logging.getLogger(__name__)


def escape_query_value(value: str) -> str:
    """Escape a value for a single-quoted string in a Drive ``q`` query.

    The Drive search syntax
    (https://developers.google.com/workspace/drive/api/guides/ref-search-terms)
    delimits strings with ``'`` and escapes ``'`` and ``\\`` with a
    backslash. The backslash is escaped first, so the backslash added in
    front of a quote is not doubled again.

    Args:
        value (str): The raw value.

    Returns:
        str: The value, safe between single quotes.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


@dataclass(frozen=True, slots=True)
class GoogleDriveFile:
    """The parts of a Drive ``File`` resource this client reads.

    Attributes:
        id (str): The file id.
        name (str): The display name.
        mime_type (str | None): The stored MIME type.
        size (int | None): Size in bytes; Drive sends it as a string and
            omits it for Google Docs-type files.
        web_view_link (str | None): The link that opens the file in Drive,
            for someone with access to it. Not a download URL and not
            temporary.
        app_properties (dict[str, str]): Private properties set by this
            app.
    """

    id: str
    name: str
    mime_type: str | None = None
    size: int | None = None
    web_view_link: str | None = None
    app_properties: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_resource(cls, resource: Mapping[str, object]) -> GoogleDriveFile:
        """Parse a ``File`` resource as returned by the API.

        Args:
            resource (Mapping[str, object]): The decoded JSON object.

        Returns:
            GoogleDriveFile: The parsed file.

        Raises:
            GoogleDriveException: When the resource has no ``id``.
        """
        file_id = resource.get("id")
        if not isinstance(file_id, str) or not file_id:
            raise GoogleDriveException(
                upstream_status=None,
                reason="malformed_response",
                message="Google Drive answered a file resource without an id.",
            )
        name = resource.get("name")
        mime_type = resource.get("mimeType")
        size = resource.get("size")
        link = resource.get("webViewLink")
        raw_properties = resource.get("appProperties")
        properties: dict[str, str] = {}
        if isinstance(raw_properties, dict):
            properties = {str(key): str(value) for key, value in raw_properties.items()}
        return cls(
            id=file_id,
            name=name if isinstance(name, str) else "",
            mime_type=mime_type if isinstance(mime_type, str) else None,
            size=int(size)
            if isinstance(size, int) or (isinstance(size, str) and size.isdigit())
            else None,
            web_view_link=link if isinstance(link, str) else None,
            app_properties=properties,
        )


def _error_from_response(response: httpx.Response) -> GoogleDriveException:
    """Map a Drive error answer onto :class:`GoogleDriveException`.

    Reads ``error.errors[0].reason`` and ``error.message`` from the
    standard Google error body; the message is redacted before it is
    quoted.

    Args:
        response (httpx.Response): The failed response.

    Returns:
        GoogleDriveException: The exception to raise.
    """
    reason: str | None = None
    upstream_message = ""
    body: object
    try:
        body = response.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        message = error.get("message")
        upstream_message = message if isinstance(message, str) else ""
        errors = error.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            first_reason = errors[0].get("reason")
            reason = first_reason if isinstance(first_reason, str) else None
        if reason is None:
            status = error.get("status")
            reason = status if isinstance(status, str) else None
    summary = f"Google Drive answered {response.status_code} {reason or 'error'}"
    return GoogleDriveException(
        upstream_status=response.status_code,
        reason=reason,
        message=_REDACTION.redact_text(
            f"{summary}: {upstream_message}" if upstream_message else summary
        ),
    )


def _error_reasons(response: httpx.Response) -> set[str]:
    """Return every ``error.errors[].reason`` of a Google error body.

    Args:
        response (httpx.Response): The failed response.

    Returns:
        set[str]: The reasons; empty when the body has none.
    """
    try:
        body: object = response.json()
    except ValueError:
        return set()
    error = body.get("error") if isinstance(body, dict) else None
    errors = error.get("errors") if isinstance(error, dict) else None
    if not isinstance(errors, list):
        return set()
    return {
        item["reason"]
        for item in errors
        if isinstance(item, dict) and isinstance(item.get("reason"), str)
    }


def _is_retryable(response: httpx.Response) -> bool:
    """Return whether an error answer is worth retrying.

    ``429`` and ``5xx`` in :data:`RETRYABLE_STATUSES` are; so is a ``403``
    whose reasons include one of :data:`RATE_LIMIT_REASONS`.

    Args:
        response (httpx.Response): The answer.

    Returns:
        bool: ``True`` when the call should be retried.
    """
    if response.status_code in RETRYABLE_STATUSES:
        return True
    return response.status_code == 403 and bool(
        _error_reasons(response) & RATE_LIMIT_REASONS
    )


def _retry_after(response: httpx.Response) -> float | None:
    """Read ``Retry-After`` as seconds from a ``429``/``503``.

    The HTTP-date form is not parsed: the caller falls back to its backoff.

    Args:
        response (httpx.Response): The answer.

    Returns:
        float | None: The non-negative delay, or ``None`` when the status
        does not carry one or the header is absent or not a number.
    """
    if response.status_code not in RETRY_AFTER_STATUSES:
        return None
    header = response.headers.get("Retry-After")
    if header is None:
        return None
    try:
        seconds = float(header.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _session_not_found(response: httpx.Response) -> GoogleDriveException:
    """Build the error for a resumable session that answered ``404``.

    Args:
        response (httpx.Response): The ``404`` answer.

    Returns:
        GoogleDriveException: Reason :data:`UPLOAD_SESSION_NOT_FOUND_REASON`.
    """
    return GoogleDriveException(
        upstream_status=response.status_code,
        reason=UPLOAD_SESSION_NOT_FOUND_REASON,
        message=(
            "The Google Drive resumable upload session was not found: it "
            "expired or was lost. Nothing was created; upload the file again."
        ),
    )


def _committed_bytes(response: httpx.Response) -> int:
    """Read how many bytes a ``308`` says the session holds.

    Args:
        response (httpx.Response): A ``308 Resume Incomplete`` answer.

    Returns:
        int: ``last + 1`` from ``Range: bytes=0-<last>``, or ``0`` when the
        header is absent (nothing received yet).

    Raises:
        GoogleDriveException: When the header is present but unparsable.
    """
    header = response.headers.get("Range")
    if not header:
        return 0
    _, _, span = header.partition("=")
    _, _, last = span.partition("-")
    try:
        return int(last) + 1
    except ValueError:
        raise GoogleDriveException(
            upstream_status=response.status_code,
            reason="malformed_range",
            message="Google Drive answered an unparsable Range header.",
        ) from None


class GoogleDriveClient:
    """Async Drive API v3 client over an injected ``httpx.AsyncClient``.

    Safe to share between concurrent requests: it keeps no per-call state,
    and the token provider serializes its own refresh.
    """

    def __init__(
        self,
        *,
        token_provider: AccessTokenProvider,
        http_client: httpx.AsyncClient,
        principal_email: str | None = None,
        chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base_seconds: float = 0.5,
        backoff_max_seconds: float = 16.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        api_base_url: str = DRIVE_API_BASE_URL,
        upload_base_url: str = DRIVE_UPLOAD_BASE_URL,
    ) -> None:
        """Initialize the client.

        Args:
            token_provider (AccessTokenProvider): Source of bearer tokens.
            http_client (httpx.AsyncClient): The caller owns and closes it.
            principal_email (str | None): The account the token belongs to,
                named in folder-not-found messages.
            chunk_size (int): Piece size of a resumable upload; a positive
                multiple of :data:`UPLOAD_CHUNK_GRANULARITY`.
            max_retries (int): Retries per request or piece on
                ``429``/``5xx``, rate-limit ``403`` and transport errors;
                also how many no-progress ``308`` answers in a row a piece
                survives.
            backoff_base_seconds (float): First backoff delay; doubles per
                attempt.
            backoff_max_seconds (float): Ceiling of one backoff delay.
            sleep (Callable[[float], Awaitable[None]]): Awaited between
                attempts; tests inject a no-op.
            api_base_url (str): Metadata endpoint root.
            upload_base_url (str): Upload endpoint root.

        Raises:
            ValueError: When ``chunk_size`` is not a positive multiple of
                256 KiB or ``max_retries`` is negative.
        """
        if chunk_size <= 0 or chunk_size % UPLOAD_CHUNK_GRANULARITY:
            raise ValueError(
                "chunk_size must be a positive multiple of 256 KiB "
                f"({UPLOAD_CHUNK_GRANULARITY} bytes)."
            )
        if max_retries < 0:
            raise ValueError("max_retries must not be negative.")
        self.token_provider: AccessTokenProvider = token_provider
        self.principal_email: str | None = principal_email
        self.chunk_size: int = chunk_size
        self.max_retries: int = max_retries
        self._http: httpx.AsyncClient = http_client
        self._backoff_base: float = backoff_base_seconds
        self._backoff_max: float = backoff_max_seconds
        self._sleep: Callable[[float], Awaitable[None]] = sleep
        self._api: str = api_base_url.rstrip("/")
        self._upload: str = upload_base_url.rstrip("/")

    def _delay(self, attempt: int, response: httpx.Response | None = None) -> float:
        """Return the wait before retry number ``attempt`` (1-based).

        Args:
            attempt (int): The retry about to happen, from ``1``.
            response (httpx.Response | None): The answer that failed; its
                ``Retry-After`` seconds (on ``429``/``503``) win over the
                exponential backoff, both capped at ``backoff_max_seconds``.

        Returns:
            float: Seconds to sleep.
        """
        hinted = None if response is None else _retry_after(response)
        if hinted is not None:
            return float(min(self._backoff_max, hinted))
        return float(min(self._backoff_max, self._backoff_base * 2 ** (attempt - 1)))

    async def _headers(self) -> dict[str, str]:
        """Return the ``Authorization`` header for the next request."""
        return {"Authorization": f"Bearer {await self.token_provider.access_token()}"}

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Send one API request, retrying transient errors.

        Retried: ``429``/``5xx``, a rate-limit ``403`` and transport errors.

        Args:
            method (str): HTTP method.
            url (str): Absolute URL.
            params (Mapping[str, str] | None): Query parameters.
            json_body (Mapping[str, object] | None): JSON body.
            headers (Mapping[str, str] | None): Extra headers.

        Returns:
            httpx.Response: The first non-retryable answer (success or not).

        Raises:
            GoogleDriveException: When every attempt failed with a
                retryable status or a transport error.
        """
        attempt = 0
        while True:
            request_headers = {**(headers or {}), **await self._headers()}
            try:
                response = await self._http.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=request_headers,
                )
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise GoogleDriveException(
                        upstream_status=None,
                        reason="transport_error",
                        message=(
                            f"Could not reach Google Drive ({type(exc).__name__})."
                        ),
                    ) from exc
                status_label = type(exc).__name__
                failed: httpx.Response | None = None
            else:
                if not _is_retryable(response):
                    return response
                if attempt >= self.max_retries:
                    raise _error_from_response(response)
                status_label = str(response.status_code)
                failed = response
            attempt += 1
            delay = self._delay(attempt, failed)
            _logger.warning(
                "Google Drive %s failed (%s); retry %d/%d in %.1fs",
                method,
                status_label,
                attempt,
                self.max_retries,
                delay,
            )
            await self._sleep(delay)

    async def list_files(
        self,
        query: str,
        *,
        drive_id: str | None = None,
        page_size: int = 100,
    ) -> list[GoogleDriveFile]:
        """Run ``files.list`` and follow every page.

        Args:
            query (str): A Drive ``q`` expression. Escape string values
                with :func:`escape_query_value`.
            drive_id (str | None): Restrict the search to this Shared Drive
                (``corpora=drive``). ``None`` searches every drive the
                account can see (``corpora=allDrives``).
            page_size (int): Page size requested.

        Returns:
            list[GoogleDriveFile]: Matching files; ``[]`` when none match.

        Raises:
            GoogleDriveException: On an error answer.
        """
        params: dict[str, str] = {
            "q": query,
            "fields": f"nextPageToken,files({FILE_FIELDS})",
            "pageSize": str(page_size),
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if drive_id:
            params["corpora"] = "drive"
            params["driveId"] = drive_id
        else:
            params["corpora"] = "allDrives"
        found: list[GoogleDriveFile] = []
        while True:
            response = await self._request("GET", f"{self._api}/files", params=params)
            if response.status_code != 200:
                raise _error_from_response(response)
            body: object = response.json()
            if not isinstance(body, dict):
                return found
            files = body.get("files")
            if isinstance(files, list):
                found.extend(
                    GoogleDriveFile.from_resource(item)
                    for item in files
                    if isinstance(item, dict)
                )
            token = body.get("nextPageToken")
            if not isinstance(token, str) or not token:
                return found
            params["pageToken"] = token

    async def get_file(self, file_id: str) -> GoogleDriveFile:
        """Run ``files.get`` for metadata.

        Args:
            file_id (str): The file id.

        Returns:
            GoogleDriveFile: The file.

        Raises:
            GoogleDriveException: On an error answer (``404`` included).
        """
        response = await self._request(
            "GET",
            f"{self._api}/files/{quote(file_id, safe='')}",
            params={"fields": FILE_FIELDS, "supportsAllDrives": "true"},
        )
        if response.status_code != 200:
            raise _error_from_response(response)
        return GoogleDriveFile.from_resource(response.json())

    async def delete_file(self, file_id: str) -> bool:
        """Run ``files.delete`` — permanent, the file skips the trash.

        Args:
            file_id (str): The file id.

        Returns:
            bool: ``True`` when deleted, ``False`` when Drive answered
            ``404`` (already gone).

        Raises:
            GoogleDriveException: On any other error answer.
        """
        response = await self._request(
            "DELETE",
            f"{self._api}/files/{quote(file_id, safe='')}",
            params={"supportsAllDrives": "true"},
        )
        if response.status_code in (200, 204):
            return True
        if response.status_code == 404:
            return False
        raise _error_from_response(response)

    async def upload(
        self,
        chunks: AsyncIterator[bytes],
        *,
        metadata: Mapping[str, object],
        content_type: str,
        file_id: str | None = None,
    ) -> GoogleDriveFile:
        """Stream ``chunks`` through a resumable session.

        The first piece is read from ``chunks`` **before** the session is
        opened, so an iterator that raises on its first chunk (a content
        validator) leaves nothing behind on Drive. Google's upload guide
        describes the file as created when the session completes, so an
        exception raised by ``chunks`` mid-stream, which abandons the
        session before its last piece, is expected to leave no file —
        not measured against the live service. When the upload fails,
        ``chunks`` is closed (``aclose``) if it is an async generator.

        The stream is read one piece ahead: a piece goes out with
        ``/*`` only while more bytes are known to follow, so the last
        ``PUT`` always carries bytes and the total. The zero-byte file is
        the exception — it is closed with an empty ``bytes */0``.

        A ``308`` naming fewer bytes than were sent re-sends the missing
        ones; one that names no progress at all waits the backoff before
        re-sending, and gives up as ``upload_stalled`` after
        ``max_retries`` such answers in a row. A ``308`` naming fewer
        bytes than the client already dropped from its buffer cannot be
        recovered and is raised at once as ``upload_range_regressed``.

        Args:
            chunks (AsyncIterator[bytes]): The payload.
            metadata (Mapping[str, object]): The ``File`` metadata
                (``name``, ``parents``, ``appProperties``…).
            content_type (str): MIME type of the payload.
            file_id (str | None): Replace the content of this existing file
                (``files.update``, same id) instead of creating one.

        Returns:
            GoogleDriveFile: The created or updated file.

        Raises:
            GoogleDriveException: On an error answer, when retries run
                out, when the session stalls or loses bytes, or when the
                session answers ``404`` (reason
                :data:`UPLOAD_SESSION_NOT_FOUND_REASON`).
        """
        try:
            return await self._run_upload(
                chunks, metadata=metadata, content_type=content_type, file_id=file_id
            )
        except BaseException:
            if isinstance(chunks, AsyncGenerator):
                await chunks.aclose()
            raise

    async def _run_upload(
        self,
        chunks: AsyncIterator[bytes],
        *,
        metadata: Mapping[str, object],
        content_type: str,
        file_id: str | None,
    ) -> GoogleDriveFile:
        """Run the resumable upload :meth:`upload` describes.

        Args:
            chunks (AsyncIterator[bytes]): The payload.
            metadata (Mapping[str, object]): The ``File`` metadata.
            content_type (str): MIME type of the payload.
            file_id (str | None): Existing file to update, or ``None``.

        Returns:
            GoogleDriveFile: The created or updated file.

        Raises:
            GoogleDriveException: See :meth:`upload`.
        """
        iterator = aiter(chunks)
        buffer = bytearray()
        exhausted = False

        async def fill() -> None:
            """Read until more than a whole piece is buffered or ``chunks`` ends."""
            nonlocal exhausted
            while not exhausted and len(buffer) <= self.chunk_size:
                try:
                    buffer.extend(await anext(iterator))
                except StopAsyncIteration:
                    exhausted = True

        await fill()
        session = await self._start_session(
            metadata=metadata, content_type=content_type, file_id=file_id
        )
        offset = 0
        stalled = 0
        while True:
            await fill()
            if exhausted and len(buffer) <= self.chunk_size:
                piece = bytes(buffer)
                total: int | None = offset + len(piece)
            else:
                piece = bytes(buffer[: self.chunk_size])
                total = None
            outcome = await self._send_piece(session, piece, offset, total)
            if isinstance(outcome, GoogleDriveFile):
                return outcome
            if outcome < offset:
                raise GoogleDriveException(
                    upstream_status=RESUME_INCOMPLETE,
                    reason="upload_range_regressed",
                    message=(
                        f"Google Drive reports holding {outcome} bytes of an "
                        f"upload already acknowledged up to {offset}; the "
                        "bytes in between are no longer buffered. Upload the "
                        "file again."
                    ),
                )
            if outcome == offset:
                stalled += 1
                if stalled > self.max_retries:
                    raise GoogleDriveException(
                        upstream_status=RESUME_INCOMPLETE,
                        reason="upload_stalled",
                        message="Google Drive stopped accepting upload bytes.",
                    )
                delay = self._delay(stalled)
                _logger.warning(
                    "Google Drive kept no bytes of the piece at byte %d; "
                    "retry %d/%d in %.1fs",
                    offset,
                    stalled,
                    self.max_retries,
                    delay,
                )
                await self._sleep(delay)
                continue
            stalled = 0
            del buffer[: outcome - offset]
            offset = outcome

    async def _start_session(
        self,
        *,
        metadata: Mapping[str, object],
        content_type: str,
        file_id: str | None,
    ) -> str:
        """Open a resumable session and return its URI.

        Args:
            metadata (Mapping[str, object]): The ``File`` metadata.
            content_type (str): Sent as ``X-Upload-Content-Type``.
            file_id (str | None): ``PATCH`` this file instead of creating.

        Returns:
            str: The session URI from the ``Location`` header. It is a
            capability, so it is never logged.

        Raises:
            GoogleDriveException: On an error answer or a missing
                ``Location``.
        """
        if file_id is None:
            method, url = "POST", f"{self._upload}/files"
        else:
            method, url = "PATCH", f"{self._upload}/files/{quote(file_id, safe='')}"
        response = await self._request(
            method,
            url,
            params={
                "uploadType": "resumable",
                "supportsAllDrives": "true",
                "fields": FILE_FIELDS,
            },
            json_body=metadata,
            headers={"X-Upload-Content-Type": content_type},
        )
        if response.status_code != 200:
            raise _error_from_response(response)
        location: str | None = response.headers.get("Location")
        if not location:
            raise GoogleDriveException(
                upstream_status=response.status_code,
                reason="no_upload_session",
                message="Google Drive opened no resumable session (no Location).",
            )
        return location

    async def _send_piece(
        self,
        session: str,
        piece: bytes,
        offset: int,
        total: int | None,
    ) -> GoogleDriveFile | int:
        """PUT one piece, retrying it on transient errors.

        Retried like :meth:`_request`; a ``404`` means the session is gone.

        Args:
            session (str): The session URI.
            piece (bytes): Bytes starting at ``offset``.
            offset (int): Position of ``piece[0]`` in the file.
            total (int | None): The file size when this is the last piece,
                else ``None`` (``/*``).

        Returns:
            GoogleDriveFile | int: The file when the upload completed, else
            how many bytes the session now holds.

        Raises:
            GoogleDriveException: On a non-retryable error, when retries
                run out, or when the session answers ``404``.
        """
        size = "*" if total is None else str(total)
        if piece:
            content_range = f"bytes {offset}-{offset + len(piece) - 1}/{size}"
        else:
            content_range = f"bytes */{size}"
        attempt = 0
        while True:
            try:
                response = await self._http.put(
                    session,
                    content=piece,
                    headers={**await self._headers(), "Content-Range": content_range},
                )
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise GoogleDriveException(
                        upstream_status=None,
                        reason="transport_error",
                        message=(
                            "Could not reach Google Drive while uploading "
                            f"({type(exc).__name__})."
                        ),
                    ) from exc
                status_label = type(exc).__name__
                failed: httpx.Response | None = None
            else:
                if response.status_code in (200, 201):
                    return GoogleDriveFile.from_resource(response.json())
                if response.status_code == RESUME_INCOMPLETE:
                    return _committed_bytes(response)
                if response.status_code == 404:
                    raise _session_not_found(response)
                if not _is_retryable(response) or attempt >= self.max_retries:
                    raise _error_from_response(response)
                status_label = str(response.status_code)
                failed = response
            attempt += 1
            delay = self._delay(attempt, failed)
            _logger.warning(
                "Google Drive upload piece at byte %d failed (%s); retry %d/%d "
                "in %.1fs",
                offset,
                status_label,
                attempt,
                self.max_retries,
                delay,
            )
            await self._sleep(delay)
            status = await self._query_session(session, size)
            if status is not None and (
                isinstance(status, GoogleDriveFile) or status != offset
            ):
                return status

    async def _query_session(
        self, session: str, size: str
    ) -> GoogleDriveFile | int | None:
        """Ask an interrupted session how much it holds.

        Args:
            session (str): The session URI.
            size (str): The total when known, else ``*``.

        Returns:
            GoogleDriveFile | int | None: The file when complete, the byte
            count on ``308``, or ``None`` when the status query itself
            failed transiently (the caller re-sends the piece).

        Raises:
            GoogleDriveException: When the session answers ``404`` (reason
                :data:`UPLOAD_SESSION_NOT_FOUND_REASON`) or any other
                non-retryable error.
        """
        try:
            response = await self._http.put(
                session,
                content=b"",
                headers={**await self._headers(), "Content-Range": f"bytes */{size}"},
            )
        except httpx.TransportError:
            return None
        if response.status_code in (200, 201):
            return GoogleDriveFile.from_resource(response.json())
        if response.status_code == RESUME_INCOMPLETE:
            return _committed_bytes(response)
        if response.status_code == 404:
            raise _session_not_found(response)
        if _is_retryable(response):
            return None
        raise _error_from_response(response)


__all__: list[str] = [
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_UPLOAD_CHUNK_SIZE",
    "DRIVE_API_BASE_URL",
    "DRIVE_UPLOAD_BASE_URL",
    "FILE_FIELDS",
    "RATE_LIMIT_REASONS",
    "RESUME_INCOMPLETE",
    "RETRYABLE_STATUSES",
    "RETRY_AFTER_STATUSES",
    "UPLOAD_CHUNK_GRANULARITY",
    "UPLOAD_SESSION_NOT_FOUND_REASON",
    "GoogleDriveClient",
    "GoogleDriveFile",
    "escape_query_value",
]
