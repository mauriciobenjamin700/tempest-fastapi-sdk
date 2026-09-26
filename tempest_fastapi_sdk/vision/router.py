"""Opt-in FastAPI router for vision inference.

Mirrors :func:`~tempest_fastapi_sdk.genai.make_genai_router`: pass the loaded
task objects you have (`Classifier` / `Detector` / `Segmenter` from
``ort-vision-sdk``) and the router mounts **only** the matching endpoints. Each
accepts a multipart ``UploadFile`` and returns the response schemas via the
:mod:`~tempest_fastapi_sdk.vision.mapping` helpers.

The task objects are injected already-constructed, so this module carries no
import-time dependency on ``ort-vision-sdk`` — only ``fastapi`` and the
dependency-free mappers/schemas. Pillow (a dependency of ``ort-vision-sdk``)
is imported when the router is built, for the pixel-count check.
"""

from __future__ import annotations

import io
import warnings
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, UploadFile

from tempest_fastapi_sdk.exceptions.validation import ValidationException
from tempest_fastapi_sdk.utils.upload import read_upload_capped
from tempest_fastapi_sdk.vision.mapping import (
    to_classification_schema,
    to_detection_schemas,
    to_segmentation_schemas,
)
from tempest_fastapi_sdk.vision.schemas import (
    ClassificationSchema,
    DetectionSchema,
    SegmentationSchema,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_MAX_IMAGE_UPLOAD_BYTES: int = 20 * 1024 * 1024
"""Largest image upload :func:`make_vision_router` accepts, in bytes (20 MiB).

The whole upload is held in memory while it is decoded, so the ceiling is
enforced while reading, not after.
"""

DEFAULT_MAX_IMAGE_PIXELS: int = 50_000_000
"""Largest ``width * height`` :func:`make_vision_router` decodes (50 MP).

The byte ceiling does not bound memory for images: a PNG of flat colour
compresses by orders of magnitude. Measured with Pillow 12.3.0 and
``ort-vision-sdk`` 0.8.0, a blank 9400 x 9400 PNG of 10 804 bytes decodes
into a 265 080 000-byte RGB array, without tripping Pillow's own
decompression-bomb warning (88.36 MP sits under its 89.48 MP threshold).
The dimensions are read from the header, before any pixel is decoded. 50 MP
covers a 50-megapixel phone photo (8160 x 6120); the models resize to a few
hundred pixels anyway.
"""


def _check_pixels(data: bytes, max_pixels: int) -> None:
    """Refuse an image whose header declares more than ``max_pixels``.

    ``PIL.Image.open`` is lazy: it parses the header and stops, so the size
    is known without decoding. Pillow's own decompression-bomb warning is
    silenced for the duration, because this check is the one that decides.

    Args:
        data (bytes): The encoded image.
        max_pixels (int): Largest accepted ``width * height``.

    Raises:
        ValidationException: When the bytes are not an image Pillow can
            identify, or the declared canvas exceeds ``max_pixels``
            (``422``).
    """
    from PIL import Image

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
        except Image.DecompressionBombError as exc:
            raise ValidationException(
                message=f"image has more than {max_pixels} pixels",
                details={"max_pixels": max_pixels},
            ) from exc
        except (OSError, SyntaxError, ValueError) as exc:
            raise ValidationException(
                message="upload is not a decodable image",
            ) from exc
    if width * height > max_pixels:
        raise ValidationException(
            message=f"image has more than {max_pixels} pixels",
            details={"max_pixels": max_pixels, "width": width, "height": height},
        )


def _image_load_errors() -> tuple[type[BaseException], ...]:
    """Return the decode error ``ort-vision-sdk`` raises, when installed.

    Returns:
        tuple[type[BaseException], ...]: ``(ImageLoadError,)``, or ``()``
        without ``ort-vision-sdk`` (fake or custom task objects), in which
        case nothing is remapped.
    """
    try:
        from ort_vision_sdk.core.exceptions import ImageLoadError
    except ImportError:
        return ()
    return (ImageLoadError,)


def make_vision_router(
    *,
    classifier: Any = None,
    detector: Any = None,
    segmenter: Any = None,
    prefix: str = "/api/vision",
    tags: Sequence[str] | None = None,
    max_upload_bytes: int = DEFAULT_MAX_IMAGE_UPLOAD_BYTES,
    max_image_pixels: int | None = DEFAULT_MAX_IMAGE_PIXELS,
) -> APIRouter:
    """Build a router exposing only the injected vision tasks.

    Every route reads the upload with
    :func:`~tempest_fastapi_sdk.utils.upload.read_upload_capped`, checks the
    declared pixel count from the image header, and only then runs the
    model. An oversized upload, a canvas over ``max_image_pixels``, or bytes
    that are not an image answer ``422``; so does an ``ImageLoadError``
    raised by ``ort-vision-sdk`` while decoding, which used to escape as a
    ``500``.

    Args:
        classifier (Any): A loaded ``Classifier`` (mounts ``POST /classify``),
            or ``None`` to omit it.
        detector (Any): A loaded ``Detector`` (mounts ``POST /detect``), or
            ``None``.
        segmenter (Any): A loaded ``Segmenter`` (mounts ``POST /segment``), or
            ``None``.
        prefix (str): Route prefix.
        tags (Sequence[str] | None): OpenAPI tags (defaults to ``["vision"]``).
        max_upload_bytes (int): Largest upload, in bytes. Defaults to
            :data:`DEFAULT_MAX_IMAGE_UPLOAD_BYTES`.
        max_image_pixels (int | None): Largest ``width * height`` decoded.
            Defaults to :data:`DEFAULT_MAX_IMAGE_PIXELS`. ``None`` skips the
            header check (and the Pillow import), leaving only Pillow's own
            decompression-bomb guard inside the decoder.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.

    Raises:
        ValueError: When no task object is injected.
        ImportError: When ``max_image_pixels`` is set and Pillow is not
            installed (it comes with the ``[vision]`` extra).
    """
    if classifier is None and detector is None and segmenter is None:
        raise ValueError(
            "make_vision_router needs at least one of classifier / detector / "
            "segmenter",
        )
    if max_image_pixels is not None:
        try:
            import PIL  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "make_vision_router checks image dimensions with Pillow; "
                'install it with `pip install "tempest-fastapi-sdk[vision]"` '
                "or pass max_image_pixels=None.",
            ) from exc
    load_errors = _image_load_errors()
    router = APIRouter(prefix=prefix, tags=list(tags or ["vision"]))

    async def _predict(task: Any, file: UploadFile) -> Any:
        """Read, bound-check and run one uploaded image through ``task``.

        Args:
            task (Any): The loaded task object.
            file (UploadFile): The uploaded image.

        Returns:
            Any: The first ``Results`` of ``task.async_predict``.

        Raises:
            ValidationException: When the upload is too large, declares too
                many pixels, or fails to decode (``422``).
        """
        data = await read_upload_capped(
            file,
            max_bytes=max_upload_bytes,
            label="image",
        )
        if max_image_pixels is not None:
            _check_pixels(data, max_image_pixels)
        try:
            return (await task.async_predict(data))[0]
        except load_errors as exc:
            raise ValidationException(
                message="upload is not a decodable image",
            ) from exc

    if classifier is not None:

        @router.post("/classify", response_model=ClassificationSchema)
        async def classify(file: UploadFile) -> ClassificationSchema:
            """Classify an uploaded image."""
            return to_classification_schema(await _predict(classifier, file))

    if detector is not None:

        @router.post("/detect", response_model=list[DetectionSchema])
        async def detect(file: UploadFile) -> list[DetectionSchema]:
            """Detect objects in an uploaded image."""
            return to_detection_schemas(await _predict(detector, file))

    if segmenter is not None:

        @router.post("/segment", response_model=list[SegmentationSchema])
        async def segment(file: UploadFile) -> list[SegmentationSchema]:
            """Segment instances in an uploaded image."""
            return to_segmentation_schemas(await _predict(segmenter, file))

    return router


__all__: list[str] = [
    "DEFAULT_MAX_IMAGE_PIXELS",
    "DEFAULT_MAX_IMAGE_UPLOAD_BYTES",
    "make_vision_router",
]
