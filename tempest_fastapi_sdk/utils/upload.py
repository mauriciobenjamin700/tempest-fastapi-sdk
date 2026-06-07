"""File upload helpers with validation and local disk persistence.

Requires the ``[upload]`` extra. The dependency is imported lazily so
``import tempest_fastapi_sdk`` keeps working when the extra is not
installed — :class:`UploadUtils` raises :class:`ImportError` on first
instantiation instead.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from tempest_fastapi_sdk.storage.minio_client import AsyncMinIOClient
    from tempest_fastapi_sdk.utils.storage_backends import UploadStorage

try:
    import aiofiles as _aiofiles_mod

    _aiofiles: ModuleType | None = _aiofiles_mod
except ImportError:  # pragma: no cover - guarded by extras
    _aiofiles = None

from fastapi import UploadFile

from tempest_fastapi_sdk.exceptions.upload import InvalidFileTypeException

# Magic-byte signatures, compared against the first bytes of an upload.
# Used by :func:`sniff_mime` to detect what a file ACTUALLY is, so a
# polyglot declared as a benign MIME (e.g. an HTML+JS payload sent with
# ``Content-Type: image/jpeg``) can be rejected before it is trusted.
_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"%PDF-", "application/pdf"),
)

# MIME aliases treated as equivalent when comparing a sniffed type
# against the declared ``Content-Type`` (no ``allowed_mimetypes`` set).
_MIME_ALIASES: dict[str, str] = {
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/x-png": "image/png",
}


def _canonical_mime(mime: str) -> str:
    """Return the canonical form of a MIME type, resolving known aliases.

    Args:
        mime (str): A (possibly lowercased) MIME type.

    Returns:
        str: The canonical MIME type (e.g. ``image/jpg`` → ``image/jpeg``).
    """
    return _MIME_ALIASES.get(mime, mime)


def sniff_mime(prefix: bytes) -> str | None:
    """Detect a file's MIME type from its leading bytes.

    Performs magic-byte sniffing on the first bytes read from an upload
    stream and returns the MIME the bytes actually represent — regardless
    of the ``Content-Type`` the client declared. Compare the result with
    the declared type (or an allow-list) to reject content/declaration
    mismatches such as an HTML payload uploaded as ``image/jpeg``.

    Recognizes JPEG, PNG, GIF, BMP, WebP and PDF. Returns ``None`` for
    anything else (text, archives, office documents, audio/video
    containers, …) — absence of a match means "not one of the sniffable
    binary formats", not "invalid".

    Args:
        prefix (bytes): The first bytes of the file (≥12 bytes
            recommended so the WebP ``RIFF…WEBP`` marker can be checked;
            shorter inputs still match the fixed-prefix signatures).

    Returns:
        str | None: The detected MIME type, or ``None`` when no known
            signature matches.
    """
    for signature, mime in _MAGIC_SIGNATURES:
        if prefix.startswith(signature):
            return mime
    if prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP":
        return "image/webp"
    return None


class UploadUtils:
    """Persist uploaded files to local disk with opt-in validation.

    Validation is incremental: extension and MIME type are checked
    against the configured whitelists before reading any bytes; the
    file's real content is optionally sniffed from its first bytes
    (``verify_magic_bytes``); and size is enforced as the stream is
    consumed so oversized uploads don't fill the disk before being
    rejected.

    Saved files are streamed in chunks so memory usage stays
    bounded regardless of the upload size.

    Attributes:
        upload_dir (Path | None): Local base directory when constructed
            with a path; ``None`` in MinIO mode.
        max_size_bytes (int | None): Reject uploads larger than this.
            ``None`` disables the size check.
        allowed_extensions (set[str] | None): Whitelist of file
            extensions (lowercase, no dot). ``None`` disables the
            extension check.
        allowed_mimetypes (set[str] | None): Whitelist of MIME types
            (lowercase). ``None`` disables the MIME check.
        verify_magic_bytes (bool): When ``True``, the first bytes of
            every upload are sniffed (:func:`sniff_mime`) and the
            detected type must be consistent with the declared type /
            allow-list. Defends against polyglots and content/MIME
            mismatches. Only enable when every accepted format is one
            :func:`sniff_mime` recognizes (images, PDF) — otherwise a
            legitimate but unsniffable upload is rejected.
    """

    def __init__(
        self,
        source: str | Path | AsyncMinIOClient,
        *,
        max_size_bytes: int | None = None,
        allowed_extensions: set[str] | None = None,
        allowed_mimetypes: set[str] | None = None,
        verify_magic_bytes: bool = False,
        chunk_size: int = 1024 * 1024,
    ) -> None:
        """Initialize with a local directory or a MinIO client.

        Args:
            source (str | Path | AsyncMinIOClient): Where uploads are
                persisted. A directory path stores files on local disk
                (created if missing); an ``AsyncMinIOClient`` stores them
                in its bucket. The backend is fixed here, so callers never
                pass it per :meth:`save`.
            max_size_bytes (int | None): Reject uploads larger than
                this. ``None`` disables the size check.
            allowed_extensions (set[str] | None): Whitelist of file
                extensions. Leading dots and case are normalized
                internally so ``{"PNG", ".jpg"}`` works as expected.
            allowed_mimetypes (set[str] | None): Whitelist of MIME
                types (case-insensitive, e.g. ``{"image/png"}``).
            verify_magic_bytes (bool): Sniff the first bytes of each
                upload and reject content that does not match its
                declared type / the allow-list. See the class
                attribute docs for the caveat. Default ``False``.
            chunk_size (int): Stream read chunk in bytes. Defaults to
                1 MiB; raise to trade memory for fewer syscalls.

        Raises:
            ImportError: When the ``[upload]`` extra is not installed.
        """
        if _aiofiles is None:
            raise ImportError(
                "UploadUtils requires the [upload] extra. "
                "Install with `pip install tempest-fastapi-sdk[upload]`."
            )
        from tempest_fastapi_sdk.utils.storage_backends import (
            LocalUploadStorage,
            MinIOUploadStorage,
        )

        if isinstance(source, (str, Path)):
            self.upload_dir: Path | None = Path(source)
            self._storage: UploadStorage = LocalUploadStorage(source)
        else:
            self.upload_dir = None
            self._storage = MinIOUploadStorage(source)
        self.max_size_bytes: int | None = max_size_bytes
        self.allowed_extensions: set[str] | None = (
            {ext.lower().lstrip(".") for ext in allowed_extensions}
            if allowed_extensions is not None
            else None
        )
        self.allowed_mimetypes: set[str] | None = (
            {mime.lower() for mime in allowed_mimetypes}
            if allowed_mimetypes is not None
            else None
        )
        self.verify_magic_bytes: bool = verify_magic_bytes
        self._chunk_size: int = chunk_size

    def validate(self, file: UploadFile) -> None:
        """Validate extension and MIME type before reading the stream.

        Size and content (magic bytes) cannot be checked here because
        they require reading the stream; they are enforced incrementally
        in :meth:`save` as bytes are consumed.

        Args:
            file (UploadFile): The FastAPI upload to validate.

        Raises:
            InvalidFileTypeException: If the extension or MIME type
                is not in the configured whitelist.
        """
        if self.allowed_extensions is not None:
            ext = Path(file.filename or "").suffix.lower().lstrip(".")
            if ext not in self.allowed_extensions:
                raise InvalidFileTypeException(
                    details={
                        "extension": ext,
                        "allowed": sorted(self.allowed_extensions),
                    },
                )
        if self.allowed_mimetypes is not None:
            mime = (file.content_type or "").lower()
            if mime not in self.allowed_mimetypes:
                raise InvalidFileTypeException(
                    details={
                        "mimetype": mime,
                        "allowed": sorted(self.allowed_mimetypes),
                    },
                )

    def _verify_content(
        self,
        prefix: bytes,
        file: UploadFile,
        content_validator: Callable[[bytes], bool] | None,
    ) -> None:
        """Run content checks against the first chunk of an upload.

        Applies the caller-supplied ``content_validator`` first, then —
        when ``verify_magic_bytes`` is enabled — sniffs the bytes and
        confirms the detected type is consistent with the allow-list (or,
        absent one, the declared ``Content-Type``).

        Args:
            prefix (bytes): The first chunk read from the stream.
            file (UploadFile): The upload being saved (for its declared
                ``Content-Type``).
            content_validator (Callable[[bytes], bool] | None): Optional
                caller predicate; returning ``False`` rejects the upload.

        Raises:
            InvalidFileTypeException: If a validator rejects the bytes,
                the signature is unrecognized, or the detected type does
                not match the declared type / allow-list.
        """
        if content_validator is not None and not content_validator(prefix):
            raise InvalidFileTypeException(
                details={
                    "reason": "content_validator rejected the upload bytes",
                },
            )

        if not self.verify_magic_bytes:
            return

        detected = sniff_mime(prefix)
        declared = _canonical_mime((file.content_type or "").lower())
        if detected is None:
            raise InvalidFileTypeException(
                details={
                    "reason": "unrecognized file signature; declared content"
                    " type cannot be verified against the file's bytes",
                    "declared": declared,
                },
            )

        if self.allowed_mimetypes is not None:
            if detected not in self.allowed_mimetypes:
                raise InvalidFileTypeException(
                    details={
                        "reason": "file content does not match an allowed type",
                        "detected": detected,
                        "declared": declared,
                        "allowed": sorted(self.allowed_mimetypes),
                    },
                )
        elif declared and detected != declared:
            raise InvalidFileTypeException(
                details={
                    "reason": "file content does not match the declared content type",
                    "detected": detected,
                    "declared": declared,
                },
            )

    async def save(
        self,
        file: UploadFile,
        *,
        subdir: str = "",
        filename: str | None = None,
        keep_original_name: bool = False,
        content_validator: Callable[[bytes], bool] | None = None,
    ) -> Path:
        """Validate and persist ``file`` to the configured backend.

        The backend (local disk or MinIO) is the one chosen at
        construction — the validation pipeline (extension / MIME / size /
        magic bytes / ``content_validator``) is identical for either.

        Args:
            file (UploadFile): The FastAPI upload.
            subdir (str): Optional sub-directory / key prefix
                (e.g. ``"avatars"``). Created on demand for local; used as
                a key prefix for MinIO.
            filename (str | None): Explicit final filename (e.g.
                ``f"{user_id}.jpg"``) for deterministic, addressable
                names. Reduced to its basename and guarded against path
                traversal. Takes precedence over ``keep_original_name``.
            keep_original_name (bool): When ``True`` (and ``filename`` is
                not given), preserves the upload's original filename;
                otherwise generates a UUID-based name with the original
                extension. Default ``False`` so collisions are
                impossible.
            content_validator (Callable[[bytes], bool] | None): Optional
                predicate run on the first chunk read from the stream.
                Returning ``False`` aborts the save (and removes the
                partial object) before any further bytes are written —
                e.g. ``lambda b: sniff_mime(b) in {"image/png"}``.

        Returns:
            Path: ``Path(storage_key)`` — the key to read the file back
            (``downloads.download(str(result))``). Call ``str(result)``
            for the bare key.

        Raises:
            InvalidFileTypeException: If the extension/MIME violates the
                whitelist, the ``content_validator`` rejects the bytes,
                or ``verify_magic_bytes`` detects a content mismatch.
            FileTooLargeException: If the stream exceeds
                ``max_size_bytes`` mid-write; the partial object is
                removed before raising.
        """
        self.validate(file)

        resolved_name = self._resolve_filename(
            file,
            filename=filename,
            keep_original_name=keep_original_name,
        )
        return await self._save_via_storage(
            file,
            storage=self._storage,
            subdir=subdir,
            resolved_name=resolved_name,
            content_validator=content_validator,
        )

    async def _save_via_storage(
        self,
        file: UploadFile,
        *,
        storage: UploadStorage,
        subdir: str,
        resolved_name: str,
        content_validator: Callable[[bytes], bool] | None,
    ) -> Path:
        """Validate + persist through an :class:`UploadStorage` backend.

        The first chunk read from ``file`` runs through
        :meth:`_verify_content` and then through the backend, so
        magic-byte + caller validators stay aligned with the local path.

        Args:
            file (UploadFile): Inbound upload.
            storage (UploadStorage): Backend conforming to :class:`UploadStorage`.
            subdir (str): Optional key prefix.
            resolved_name (str): Sanitized basename from
                :meth:`_resolve_filename`.
            content_validator (Callable[[bytes], bool] | None): Caller
                predicate forwarded to :meth:`_verify_content`.

        Returns:
            Path: ``Path(storage_key)`` so the return type stays
            consistent with the local branch.
        """
        from collections.abc import AsyncIterator as _AsyncIterator

        key = f"{subdir.strip('/')}/{resolved_name}" if subdir else resolved_name

        first_chunk_verified = False

        async def _chunks() -> _AsyncIterator[bytes]:
            nonlocal first_chunk_verified
            while True:
                chunk = await file.read(self._chunk_size)
                if not chunk:
                    return
                if not first_chunk_verified:
                    first_chunk_verified = True
                    self._verify_content(chunk, file, content_validator)
                yield chunk

        await storage.write_stream(
            key,
            _chunks(),
            content_type=file.content_type or "application/octet-stream",
            max_size_bytes=self.max_size_bytes,
        )
        return Path(key)

    def _resolve_filename(
        self,
        file: UploadFile,
        *,
        filename: str | None,
        keep_original_name: bool,
    ) -> str:
        """Compute the on-disk filename for an upload.

        Args:
            file (UploadFile): The upload being saved.
            filename (str | None): Explicit name override.
            keep_original_name (bool): Preserve the original name when
                no override is given.

        Returns:
            str: The basename to write under ``upload_dir``.

        Raises:
            InvalidFileTypeException: If ``filename`` reduces to an empty
                or path-only value (``""``, ``"."``, ``".."``).
        """
        original = Path(file.filename or "upload")

        if filename is not None:
            safe = Path(filename).name
            if not safe or safe in {".", ".."}:
                raise InvalidFileTypeException(
                    details={"filename": filename, "reason": "invalid name"},
                )
            return safe

        if keep_original_name:
            safe = Path(original.name).name
            if not safe or safe in {".", ".."}:
                return f"{uuid4().hex}{original.suffix}"
            return safe

        return f"{uuid4().hex}{original.suffix}"

    async def delete(self, key: Path | str) -> bool:
        """Delete a previously saved object via the configured backend.

        For the local backend the key resolves under the base directory
        (path-traversal attempts raise ``InvalidFileTypeException``); for
        MinIO it is the object key.

        Args:
            key (Path | str): The storage key returned by :meth:`save`.

        Returns:
            bool: ``True`` if the object existed and was deleted,
            ``False`` when it was already missing.

        Raises:
            InvalidFileTypeException: Local backend, when ``key`` resolves
                outside the base directory (path traversal attempt).
        """
        return await self._storage.delete(str(key))


__all__: list[str] = [
    "UploadUtils",
    "sniff_mime",
]
