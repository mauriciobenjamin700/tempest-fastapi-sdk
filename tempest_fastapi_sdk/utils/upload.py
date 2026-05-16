"""File upload helpers with validation and local disk persistence.

Requires the ``[upload]`` extra. The dependency is imported lazily so
``import tempest_fastapi_sdk`` keeps working when the extra is not
installed — :class:`UploadUtils` raises :class:`ImportError` on first
instantiation instead.
"""

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


class UploadUtils:
    """Persist uploaded files to local disk with opt-in validation.

    Validation is incremental: extension and MIME type are checked
    against the configured whitelists before reading any bytes;
    size is enforced as the stream is consumed so oversized
    uploads don't fill the disk before being rejected.

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
    """

    def __init__(
        self,
        upload_dir: Path | str,
        *,
        max_size_bytes: int | None = None,
        allowed_extensions: set[str] | None = None,
        allowed_mimetypes: set[str] | None = None,
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
        self._chunk_size: int = chunk_size

    def validate(self, file: UploadFile) -> None:
        """Validate extension and MIME type before reading the stream.

        Size cannot be checked here because the stream length isn't
        always known up front; it's enforced incrementally in
        :meth:`save` as bytes are consumed.

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

    async def save(
        self,
        file: UploadFile,
        *,
        subdir: str = "",
        keep_original_name: bool = False,
    ) -> Path:
        """Persist ``file`` to disk and return the final path.

        Args:
            file (UploadFile): The FastAPI upload.
            subdir (str): Optional sub-directory relative to
                ``upload_dir`` (e.g. ``"avatars"``). Created on
                demand.
            keep_original_name (bool): When ``True``, preserves the
                upload's original filename; otherwise generates a
                UUID-based name with the original extension. Default
                ``False`` so collisions are impossible.

        Returns:
            Path: Absolute path of the saved file.

        Raises:
            InvalidFileTypeException: If the extension or MIME type
                violates the configured whitelist.
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

        original = Path(file.filename or "upload")
        if keep_original_name:
            filename = Path(original.name).name
            if not filename or filename in {".", ".."}:
                filename = f"{uuid4().hex}{original.suffix}"
        else:
            filename = f"{uuid4().hex}{original.suffix}"

        target_path = (target_dir / filename).resolve()
        if base_dir != target_path.parent and base_dir not in target_path.parents:
            raise InvalidFileTypeException(
                details={"filename": filename, "reason": "escapes upload_dir"},
            )
        total = 0
        try:
            async with _aiofiles.open(target_path, "wb") as out:
                while chunk := await file.read(self._chunk_size):
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

    def delete(self, path: Path | str) -> bool:
        """Delete a previously saved file.

        Args:
            path (Path | str): The file path to delete.

        Returns:
            bool: ``True`` if the file existed and was deleted,
            ``False`` when it was already missing.
        """
        target = Path(path)
        if not target.exists():
            return False
        target.unlink()
        return True


__all__: list[str] = [
    "UploadUtils",
]
