"""A truncated image is refused whatever ``LOAD_TRUNCATED_IMAGES`` says (#868).

``import weasyprint`` (the ``[pdf]`` extra) runs
``ImageFile.LOAD_TRUNCATED_IMAGES = True``, a process-wide Pillow switch.
From then on Pillow fills the missing tail of a cut file with gray instead
of raising, and every SDK path that decoded user images accepted half a
PNG: ``strip_image_metadata`` returned a 109-byte image for 77 of 155
bytes, after the first PDF a service rendered.

Each test here turns the switch on the way the ``[pdf]`` extra does and
runs one SDK decoding path on a truncated image.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageFile

from tempest_fastapi_sdk.exceptions import ImageDecodeException
from tempest_fastapi_sdk.utils.images import strip_image_metadata

FORMATS: list[tuple[str, str]] = [
    ("PNG", "image/png"),
    ("JPEG", "image/jpeg"),
    ("GIF", "image/gif"),
    ("BMP", "image/bmp"),
    ("WEBP", "image/webp"),
]


def encode(fmt: str, size: tuple[int, int] = (64, 48)) -> bytes:
    """Encode a noisy RGB image, so every cut lands inside pixel data.

    Args:
        fmt (str): Pillow format name.
        size (tuple[int, int]): Width and height.

    Returns:
        bytes: The complete encoded image.
    """
    image = Image.effect_noise(size, 60).convert("RGB")
    if fmt == "GIF":
        image = image.convert("P")
    buffer = io.BytesIO()
    image.save(buffer, fmt)
    return buffer.getvalue()


def half(fmt: str) -> bytes:
    """Return the first half of an encoded image.

    Args:
        fmt (str): Pillow format name.

    Returns:
        bytes: The truncated image.
    """
    data = encode(fmt)
    return data[: len(data) // 2]


@pytest.fixture
def lenient_pillow(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Put Pillow in the state ``import weasyprint`` leaves it in.

    The switch is set by hand rather than by importing WeasyPrint, because a
    module import runs once per process and the test must not depend on
    which test imported it first; :class:`TestAfterARealPdf` covers the
    real import. The ``monkeypatch`` restores the value the test started
    with, and the teardown asserts the SDK did not flip it back.

    Args:
        monkeypatch (pytest.MonkeyPatch): Restores the switch afterwards.

    Yields:
        None: Control, with ``LOAD_TRUNCATED_IMAGES`` set to ``True``.
    """
    monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", True)
    yield
    assert ImageFile.LOAD_TRUNCATED_IMAGES is True


def _strip(data: bytes, fmt: str) -> object:
    """Run :func:`strip_image_metadata` with the matching media type.

    Args:
        data (bytes): Encoded image.
        fmt (str): Pillow format name.

    Returns:
        object: The re-encoded bytes.
    """
    return strip_image_metadata(data, dict(FORMATS)[fmt])


def _qr_logo(data: bytes, fmt: str) -> object:
    """Decode ``data`` as a QR logo.

    Args:
        data (bytes): Encoded image.
        fmt (str): Pillow format name (unused).

    Returns:
        object: The decoded logo.
    """
    from tempest_fastapi_sdk.share.qr import decode_logo

    return decode_logo(data)


def _card_logo(data: bytes, fmt: str) -> object:
    """Build a share card renderer with ``data`` as the logo.

    Args:
        data (bytes): Encoded image.
        fmt (str): Pillow format name (unused).

    Returns:
        object: The renderer.
    """
    from tempest_fastapi_sdk.share import ShareCardRenderer

    return ShareCardRenderer(logo=data)


def _frame_photo(data: bytes, fmt: str) -> object:
    """Render a share frame with ``data`` as the photo.

    Args:
        data (bytes): Encoded image.
        fmt (str): Pillow format name (unused).

    Returns:
        object: The PNG.
    """
    from tempest_fastapi_sdk.share import ShareFrameRenderer, ShareFrameSchema

    return ShareFrameRenderer().render(
        ShareFrameSchema(url="https://example.com/s/1", title="Ana"),
        photo=data,
    )


def _face_source(data: bytes, fmt: str) -> object:
    """Open ``data`` the way ``FaceRecognizer`` opens an upload.

    Args:
        data (bytes): Encoded image.
        fmt (str): Pillow format name (unused).

    Returns:
        object: The image handed to the detector, converted as it does.
    """
    from tempest_fastapi_sdk.faces import FaceRecognizer

    return FaceRecognizer()._open(data).convert("RGB")


def _genai_image_source(data: bytes, fmt: str) -> object:
    """Normalize ``data`` the way ``ImageGenerator`` reads an edit source.

    Args:
        data (bytes): Encoded image.
        fmt (str): Pillow format name (unused).

    Returns:
        object: The RGB image fed to the pipeline.
    """
    from tempest_fastapi_sdk.genai.image import _load_image

    return _load_image(data).convert("RGB")


def _genai_vlm_source(data: bytes, fmt: str) -> object:
    """Normalize ``data`` the way the vision-language generator reads it.

    Args:
        data (bytes): Encoded image.
        fmt (str): Pillow format name (unused).

    Returns:
        object: The RGB image fed to the processor.
    """
    from tempest_fastapi_sdk.genai.vision_text import _load_image

    return _load_image(data).convert("RGB")


PATHS: dict[str, Callable[[bytes, str], object]] = {
    "strip_image_metadata": _strip,
    "share.decode_logo": _qr_logo,
    "ShareCardRenderer.logo": _card_logo,
    "ShareFrameRenderer.photo": _frame_photo,
    "FaceRecognizer": _face_source,
    "genai.image": _genai_image_source,
    "genai.vision_text": _genai_vlm_source,
}

REFUSALS: tuple[type[BaseException], ...] = (
    ImageDecodeException,
    ValueError,
    OSError,
)


def _needs_fonts(name: str) -> None:
    """Skip the share renderers on a machine without a TrueType font.

    Args:
        name (str): Path id.
    """
    if not name.startswith("Share"):
        return
    from pathlib import Path

    from tempest_fastapi_sdk.share import card

    if not any(Path(regular).is_file() for regular, _ in card.DEFAULT_FONT_CANDIDATES):
        pytest.skip("no system TrueType font (install fonts-dejavu-core)")


@pytest.mark.usefixtures("lenient_pillow")
class TestTruncatedIsRefused:
    """Every SDK path refuses a cut image with the switch on."""

    @pytest.mark.parametrize("fmt", [fmt for fmt, _ in FORMATS])
    @pytest.mark.parametrize("name", list(PATHS))
    def test_refused(self, name: str, fmt: str) -> None:
        _needs_fonts(name)
        with pytest.raises(REFUSALS):
            PATHS[name](half(fmt), fmt)

    @pytest.mark.parametrize("fmt", [fmt for fmt, _ in FORMATS])
    @pytest.mark.parametrize("name", list(PATHS))
    def test_complete_still_accepted(self, name: str, fmt: str) -> None:
        _needs_fonts(name)
        PATHS[name](encode(fmt), fmt)

    @pytest.mark.parametrize("fmt", [fmt for fmt, _ in FORMATS])
    def test_vision_router_answers_422(self, fmt: str) -> None:
        from tempest_fastapi_sdk.vision import make_vision_router

        calls: list[bytes] = []

        class _Detector:
            async def async_predict(self, data: bytes) -> list[object]:
                calls.append(data)
                raise RuntimeError("the model ran on a truncated image")

        app = FastAPI()
        app.include_router(make_vision_router(detector=_Detector()))
        response = TestClient(app, raise_server_exceptions=False).post(
            "/api/vision/detect",
            files={"file": ("x", half(fmt), dict(FORMATS)[fmt])},
        )

        assert response.status_code == 422
        assert calls == []


class TestAfterARealPdf:
    """The scenario from #868, end to end: render a PDF, then validate."""

    def test_strip_refuses_after_rendering(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        pytest.importorskip("weasyprint")
        from tempest_fastapi_sdk.pdf import PdfRenderer

        monkeypatch.setattr(
            ImageFile, "LOAD_TRUNCATED_IMAGES", ImageFile.LOAD_TRUNCATED_IMAGES
        )
        cut = half("PNG")
        with pytest.raises(ImageDecodeException):
            strip_image_metadata(cut, "image/png")

        asyncio.run(PdfRenderer().render_html("<h1>ok</h1>"))

        with pytest.raises(ImageDecodeException):
            strip_image_metadata(cut, "image/png")


SMALL: dict[str, bytes] = {
    "png": encode("PNG", (12, 10)),
    "jpeg": encode("JPEG", (12, 10)),
    "gif": encode("GIF", (12, 10)),
    "bmp": encode("BMP", (12, 10)),
    "webp": encode("WEBP", (12, 10)),
}


class TestEveryCut:
    """No cut point Pillow refuses by default slips through with the switch on."""

    @pytest.mark.parametrize("fmt", list(SMALL))
    def test_parity_with_the_strict_default(
        self, fmt: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data = SMALL[fmt]
        media_type = f"image/{fmt}"
        leaked: list[int] = []
        for cut in range(1, len(data)):
            piece = data[:cut]
            monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", False)
            try:
                with Image.open(io.BytesIO(piece)) as image:
                    image.load()
            except Exception:
                strict_refuses = True
            else:
                strict_refuses = False
            monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", True)
            if not strict_refuses:
                continue
            try:
                strip_image_metadata(piece, media_type)
            except ImageDecodeException:
                continue
            leaked.append(cut)

        assert leaked == []
