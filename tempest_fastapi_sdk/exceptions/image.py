"""Exceptions raised while re-encoding a user image.

:func:`~tempest_fastapi_sdk.utils.images.strip_image_metadata` raises one of
these instead of handing the original bytes back, so a caller cannot store
or forward an image that kept its metadata by accident.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tempest_fastapi_sdk.exceptions.base import AppException
from tempest_fastapi_sdk.exceptions.upload import (
    FileTooLargeException,
    InvalidFileTypeException,
)


class ImageDecodeException(AppException):
    """Raised when the bytes are not an image the declared format can decode.

    Covers content no accepted decoder recognizes and a recognized image
    that is truncated or corrupt once its pixels are read.
    """

    status_code: int = 422
    message: str = "The image could not be decoded"
    code: str = "IMAGE_DECODE_FAILED"
    details_example: ClassVar[dict[str, Any]] = {"declared": "image/jpeg"}


class ImageTypeMismatchException(InvalidFileTypeException):
    """Raised when the image's real format differs from the declared one.

    The declared type is client-controlled; the decoder reads the real
    format from the bytes, and the two must agree.
    """

    status_code: int = 415
    message: str = "The image content does not match the declared type"
    code: str = "IMAGE_TYPE_MISMATCH"
    details_example: ClassVar[dict[str, Any]] = {
        "declared": "image/jpeg",
        "detected": "image/png",
    }


class ImageFormatUnsupportedException(InvalidFileTypeException):
    """Raised when the declared type is not one the function can re-encode.

    Pillow ships no HEIC codec, so ``image/heic`` lands here, as does any
    value that is not an image media type at all.
    """

    status_code: int = 415
    message: str = "This image type cannot be processed"
    code: str = "IMAGE_FORMAT_UNSUPPORTED"
    details_example: ClassVar[dict[str, Any]] = {"declared": "image/heic"}


class ImageTooLargeException(FileTooLargeException):
    """Raised when the image has more pixels than the configured ceiling.

    The check reads the dimensions from the header, before any pixel is
    decoded, so a small file that would expand to gigabytes of pixels
    (a decompression bomb) is refused without being expanded.
    """

    status_code: int = 413
    message: str = "The image has more pixels than allowed"
    code: str = "IMAGE_TOO_LARGE"
    details_example: ClassVar[dict[str, Any]] = {
        "width": 20000,
        "height": 20000,
        "max_pixels": 50000000,
    }


__all__: list[str] = [
    "ImageDecodeException",
    "ImageFormatUnsupportedException",
    "ImageTooLargeException",
    "ImageTypeMismatchException",
]
