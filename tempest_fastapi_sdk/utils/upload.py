"""File upload helpers with validation and local disk persistence.

Requires the ``[upload]`` extra. The dependency is imported lazily so
``import tempest_fastapi_sdk`` keeps working when the extra is not
installed — :class:`UploadUtils` raises :class:`ImportError` on first
instantiation instead.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    import aiofiles as _aiofiles
except ImportError:  # pragma: no cover - guarded by extras
    _aiofiles: Any = None  # type: ignore[no-redef]

from fastapi import UploadFile

from tempest_fastapi_sdk.exceptions.upload import (
    FileTooLargeException,
    InvalidFileTypeException,
)

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
        upload_dir (Path): Base directory where files are persisted.
            Created on instantiation when missing.
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
        upload_dir: Path | str,
        *,
        max_size_bytes: int | None = None,
        allowed_extensions: set[str] | None = None,
        allowed_mimetypes: set[str] | None = None,
        verify_magic_bytes: bool = False,
        chunk_size: int = 1024 * 1024,
    ) -> None:
        """Initialize.

        Args:
            upload_dir (Path | str): Base directory. Created if
                missing (parents included).
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
        self.upload_dir: Path = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
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
        """Persist ``file`` to disk and return the final path.

        Args:
            file (UploadFile): The FastAPI upload.
            subdir (str): Optional sub-directory relative to
                ``upload_dir`` (e.g. ``"avatars"``). Created on
                demand.
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
                partial file) before any further bytes are written —
                e.g. ``lambda b: sniff_mime(b) in {"image/png"}``.

        Returns:
            Path: Absolute path of the saved file.

        Raises:
            InvalidFileTypeException: If the extension/MIME violates the
                whitelist, the ``content_validator`` rejects the bytes,
                or ``verify_magic_bytes`` detects a content mismatch.
            FileTooLargeException: If the stream exceeds
                ``max_size_bytes`` mid-write; the partial file is
                deleted before raising.
        """
        self.validate(file)

        base_dir = self.upload_dir.resolve()
        target_dir = (base_dir / subdir).resolve() if subdir else base_dir
        if base_dir != target_dir and base_dir not in target_dir.parents:
            raise InvalidFileTypeException(
                details={"subdir": subdir, "reason": "escapes upload_dir"},
            )
        target_dir.mkdir(parents=True, exist_ok=True)

        resolved_name = self._resolve_filename(
            file,
            filename=filename,
            keep_original_name=keep_original_name,
        )

        target_path = (target_dir / resolved_name).resolve()
        if base_dir != target_path.parent and base_dir not in target_path.parents:
            raise InvalidFileTypeException(
                details={
                    "filename": resolved_name,
                    "reason": "escapes upload_dir",
                },
            )

        total = 0
        first_chunk = True
        try:
            async with _aiofiles.open(target_path, "wb") as out:
                while chunk := await file.read(self._chunk_size):
                    if first_chunk:
                        first_chunk = False
                        self._verify_content(chunk, file, content_validator)
                    total += len(chunk)
                    if self.max_size_bytes is not None and total > self.max_size_bytes:
                        raise FileTooLargeException(
                            details={"max_size_bytes": self.max_size_bytes},
                        )
                    await out.write(chunk)
        except Exception:
            target_path.unlink(missing_ok=True)
            raise

        return target_path.resolve()

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

    def delete(self, path: Path | str) -> bool:
        """Delete a previously saved file, bounded to ``upload_dir``.

        Rejects any path that resolves outside ``upload_dir`` — that
        way callers can forward a user-supplied filename without
        risking ``rm -rf`` semantics on the rest of the filesystem.
        Absolute paths are accepted only when they land under
        ``upload_dir``; everything else is treated as relative to it.

        Args:
            path (Path | str): The file path to delete. Resolved
                against ``upload_dir`` when relative.

        Returns:
            bool: ``True`` if the file existed and was deleted,
            ``False`` when it was already missing.

        Raises:
            InvalidFileTypeException: When ``path`` resolves outside
                ``upload_dir`` (path traversal attempt).
        """
        base_dir = self.upload_dir.resolve()
        raw = Path(path)
        candidate = raw if raw.is_absolute() else (base_dir / raw)
        target = candidate.resolve()
        if target != base_dir and base_dir not in target.parents:
            raise InvalidFileTypeException(
                details={"path": str(path), "reason": "escapes upload_dir"},
            )
        if not target.exists():
            return False
        target.unlink()
        return True


__all__: list[str] = [
    "UploadUtils",
    "sniff_mime",
]
