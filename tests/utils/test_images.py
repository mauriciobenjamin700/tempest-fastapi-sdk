"""Tests for :func:`tempest_fastapi_sdk.utils.images.strip_image_metadata`.

Every fixture image is generated here with Pillow, carrying the metadata the
function must remove, so no binary file is committed and each test names
exactly what went in.
"""

from __future__ import annotations

import io
import struct
import sys
import warnings
import zlib

import pytest
from PIL import Image, ImageCms, PngImagePlugin
from PIL.ExifTags import GPS, IFD, Base

from tempest_fastapi_sdk import (
    DEFAULT_JPEG_QUALITY,
    DEFAULT_MAX_IMAGE_PIXELS,
    DEFAULT_WEBP_QUALITY,
    AppException,
    FileTooLargeException,
    ImageDecodeException,
    ImageFormatUnsupportedException,
    ImageMediaTypeEnum,
    ImageTooLargeException,
    ImageTypeMismatchException,
    InvalidFileTypeException,
    strip_image_metadata,
)
from tempest_fastapi_sdk.exceptions import default_message_catalog

SECRET_MAKE: str = "AcmePhone"
SECRET_MODEL: str = "Model-SN-123456"
SECRET_XMP: bytes = b'<x:xmpmeta xmlns:x="adobe:ns:meta/">secret-xmp</x:xmpmeta>'
SECRET_TEXT: str = "secret-author"
SECRET_COMMENT: bytes = b"secret-comment"
SECRETS: tuple[bytes, ...] = (
    SECRET_MAKE.encode(),
    SECRET_MODEL.encode(),
    b"secret-xmp",
    SECRET_TEXT.encode(),
    SECRET_COMMENT,
)


def gps_exif(orientation: int | None = None) -> Image.Exif:
    """Build an EXIF block with device, GPS and, optionally, orientation.

    Args:
        orientation (int | None): The EXIF orientation tag, when set.

    Returns:
        Image.Exif: The EXIF block.
    """
    exif = Image.Exif()
    exif[Base.Make] = SECRET_MAKE
    exif[Base.Model] = SECRET_MODEL
    if orientation is not None:
        exif[Base.Orientation] = orientation
    exif[IFD.GPSInfo] = {
        GPS.GPSLatitudeRef: "S",
        GPS.GPSLatitude: (23.0, 33.0, 1.0),
        GPS.GPSLongitudeRef: "W",
        GPS.GPSLongitude: (46.0, 38.0, 1.0),
    }
    return exif


def srgb_icc() -> bytes:
    """Return the bytes of an sRGB ICC profile.

    Returns:
        bytes: The serialized profile.
    """
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    return profile.tobytes()


def encode(image: Image.Image, fmt: str, **options: object) -> bytes:
    """Save ``image`` in ``fmt`` and return the bytes.

    Args:
        image (Image.Image): The image to save.
        fmt (str): The Pillow format name.
        **options (object): Forwarded to ``Image.save``.

    Returns:
        bytes: The encoded file.
    """
    buffer = io.BytesIO()
    image.save(buffer, fmt, **options)
    return buffer.getvalue()


def open_bytes(data: bytes) -> Image.Image:
    """Open encoded bytes with Pillow.

    Args:
        data (bytes): The encoded file.

    Returns:
        Image.Image: The opened image.
    """
    return Image.open(io.BytesIO(data))


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    """Build one PNG chunk with its CRC.

    Args:
        kind (bytes): The four-byte chunk type.
        payload (bytes): The chunk data.

    Returns:
        bytes: Length, type, data and CRC.
    """
    crc = zlib.crc32(kind + payload)
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def png_header_only(width: int, height: int) -> bytes:
    """Build a PNG whose header claims a size its data never delivers.

    Pillow reads the size from ``IHDR`` without decoding ``IDAT``, which
    is how a decompression bomb looks before it is expanded.

    Args:
        width (int): Claimed width.
        height (int): Claimed height.

    Returns:
        bytes: The PNG file.
    """
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", b"\x00")
        + png_chunk(b"IEND", b"")
    )


def split_red_blue(width: int, height: int) -> Image.Image:
    """Build an RGB image red on the left half and blue on the right half.

    Args:
        width (int): Image width.
        height (int): Image height.

    Returns:
        Image.Image: The image.
    """
    image = Image.new("RGB", (width, height), "red")
    image.paste((0, 0, 255), (width // 2, 0, width, height))
    return image


def assert_no_secret(data: bytes) -> None:
    """Fail when any planted metadata value survives in ``data``.

    Args:
        data (bytes): The re-encoded file.
    """
    for secret in SECRETS:
        assert secret not in data


class TestJpeg:
    def test_gps_exif_is_removed_and_orientation_applied(self) -> None:
        source = encode(
            split_red_blue(40, 20),
            "JPEG",
            exif=gps_exif(orientation=6),
            icc_profile=srgb_icc(),
            comment=SECRET_COMMENT,
        )
        assert SECRET_MAKE.encode() in source

        clean = strip_image_metadata(source, ImageMediaTypeEnum.JPEG)

        result = open_bytes(clean)
        assert result.format == "JPEG"
        assert "exif" not in result.info
        assert "icc_profile" not in result.info
        assert "comment" not in result.info
        assert len(result.getexif()) == 0
        assert b"Exif\x00\x00" not in clean
        assert_no_secret(clean)
        assert result.size == (20, 40)
        top_red, _, top_blue = result.getpixel((10, 2))
        bottom_red, _, bottom_blue = result.getpixel((10, 37))
        assert top_red > 200 and top_blue < 60
        assert bottom_blue > 200 and bottom_red < 60

    def test_xmp_and_iptc_segments_are_removed(self) -> None:
        source = encode(Image.new("RGB", (8, 8)), "JPEG", xmp=SECRET_XMP)
        iptc = b"\x1c\x02\x50\x00\x0bsecret-iptc"
        payload = (
            b"Photoshop 3.0\x00"
            + b"8BIM\x04\x04\x00\x00"
            + struct.pack(">I", len(iptc))
            + iptc
        )
        app13 = b"\xff\xed" + struct.pack(">H", len(payload) + 2) + payload
        source = source[:2] + app13 + source[2:]
        assert {"xmp", "photoshop"} <= set(open_bytes(source).info)

        clean = strip_image_metadata(source, "image/jpeg")

        assert_no_secret(clean)
        assert b"secret-iptc" not in clean
        assert b"Photoshop 3.0" not in clean
        assert not {"xmp", "photoshop"} & set(open_bytes(clean).info)

    def test_size_is_kept_without_orientation(self) -> None:
        source = encode(split_red_blue(40, 20), "JPEG", exif=gps_exif())

        result = open_bytes(strip_image_metadata(source, "image/jpeg"))

        assert result.size == (40, 20)
        assert result.mode == "RGB"

    def test_grayscale_mode_is_kept(self) -> None:
        source = encode(Image.new("L", (8, 8), 128), "JPEG", exif=gps_exif())

        assert open_bytes(strip_image_metadata(source, "image/jpeg")).mode == "L"

    def test_alias_of_the_declared_type_is_accepted(self) -> None:
        source = encode(Image.new("RGB", (8, 8)), "JPEG")

        assert open_bytes(strip_image_metadata(source, " Image/JPG ")).format == "JPEG"

    def test_quality_changes_the_output(self) -> None:
        source = encode(split_red_blue(64, 64), "JPEG", quality=100)

        low = strip_image_metadata(source, "image/jpeg", jpeg_quality=10)
        high = strip_image_metadata(source, "image/jpeg")

        assert len(low) < len(high)


class TestPng:
    def test_text_exif_xmp_and_icc_chunks_are_removed(self) -> None:
        info = PngImagePlugin.PngInfo()
        info.add_text("Author", SECRET_TEXT)
        info.add_text("Comment", SECRET_COMMENT.decode(), zip=True)
        info.add_itxt("XML:com.adobe.xmp", SECRET_XMP.decode())
        image = Image.new("RGBA", (12, 10), (10, 20, 30, 40))
        source = encode(
            image, "PNG", pnginfo=info, exif=gps_exif(), icc_profile=srgb_icc()
        )
        for chunk in (b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"iCCP"):
            assert chunk in source

        clean = strip_image_metadata(source, ImageMediaTypeEnum.PNG)

        for chunk in (b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"iCCP"):
            assert chunk not in clean
        assert_no_secret(clean)
        result = open_bytes(clean)
        assert result.mode == "RGBA"
        assert result.tobytes() == image.tobytes()

    def test_palette_transparency_is_kept(self) -> None:
        image = Image.new("P", (6, 6), 3)
        image.putpixel((0, 0), 0)
        source = encode(image, "PNG", transparency=0)

        result = open_bytes(strip_image_metadata(source, "image/png"))

        assert result.mode == "P"
        assert result.info["transparency"] == 0
        assert result.convert("RGBA").getpixel((0, 0))[3] == 0
        assert result.convert("RGBA").getpixel((1, 1))[3] == 255


class TestWebp:
    def test_exif_xmp_and_icc_are_removed(self) -> None:
        source = encode(
            Image.new("RGB", (16, 16), "blue"),
            "WEBP",
            exif=gps_exif(),
            xmp=SECRET_XMP,
            icc_profile=srgb_icc(),
        )
        for chunk in (b"EXIF", b"XMP ", b"ICCP"):
            assert chunk in source

        clean = strip_image_metadata(source, ImageMediaTypeEnum.WEBP)

        for chunk in (b"EXIF", b"XMP ", b"ICCP"):
            assert chunk not in clean
        assert_no_secret(clean)
        assert open_bytes(clean).format == "WEBP"

    def test_lossless_source_stays_lossless(self) -> None:
        image = Image.new("RGBA", (8, 8), (10, 20, 30, 40))
        source = encode(image, "WEBP", lossless=True, exif=gps_exif())

        clean = strip_image_metadata(source, "image/webp")

        assert b"VP8L" in clean
        assert open_bytes(clean).tobytes() == image.tobytes()

    def test_lossy_source_stays_lossy(self) -> None:
        source = encode(Image.new("RGB", (8, 8), "red"), "WEBP", quality=80)

        clean = strip_image_metadata(source, "image/webp")

        assert b"VP8 " in clean
        assert b"VP8L" not in clean

    def test_animation_comes_back_as_its_first_frame(self) -> None:
        frames = [Image.new("RGB", (8, 8), c) for c in ("red", "green", "blue")]
        source = encode(
            frames[0], "WEBP", save_all=True, append_images=frames[1:], lossless=True
        )
        assert getattr(open_bytes(source), "n_frames", 1) == 3

        result = open_bytes(strip_image_metadata(source, "image/webp"))

        assert getattr(result, "n_frames", 1) == 1
        assert result.convert("RGB").getpixel((0, 0)) == (255, 0, 0)


class TestGifAndBmp:
    def test_gif_comment_is_removed_and_transparency_kept(self) -> None:
        frames = [Image.new("P", (8, 8), index) for index in (1, 2, 3)]
        for frame in frames:
            frame.putpalette([0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255])
            frame.putpixel((0, 0), 0)
        source = encode(
            frames[0],
            "GIF",
            save_all=True,
            append_images=frames[1:],
            comment=SECRET_COMMENT,
            transparency=0,
            duration=100,
            loop=0,
        )
        assert SECRET_COMMENT in source
        assert getattr(open_bytes(source), "n_frames", 1) == 3

        clean = strip_image_metadata(source, ImageMediaTypeEnum.GIF)

        assert_no_secret(clean)
        result = open_bytes(clean)
        assert result.format == "GIF"
        assert getattr(result, "n_frames", 1) == 1
        assert "comment" not in result.info
        assert result.info["transparency"] == 0
        assert result.mode == "P"

    def test_bmp_passes(self) -> None:
        image = split_red_blue(10, 6)
        source = encode(image, "BMP")

        result = open_bytes(strip_image_metadata(source, ImageMediaTypeEnum.BMP))

        assert result.format == "BMP"
        assert result.tobytes() == image.tobytes()


class TestRefusals:
    def test_bytes_that_are_not_an_image(self) -> None:
        with pytest.raises(ImageDecodeException) as caught:
            strip_image_metadata(b"not an image at all", "image/jpeg")

        assert caught.value.status_code == 422
        assert caught.value.details == {"declared": "image/jpeg"}

    def test_truncated_image(self) -> None:
        source = encode(split_red_blue(64, 64), "PNG")

        with pytest.raises(ImageDecodeException):
            strip_image_metadata(source[: len(source) // 2], "image/png")

    def test_format_outside_the_accepted_set(self) -> None:
        source = encode(Image.new("RGB", (8, 8)), "TIFF")

        with pytest.raises(ImageDecodeException):
            strip_image_metadata(source, "image/jpeg")

    def test_declared_type_differs_from_content(self) -> None:
        source = encode(Image.new("RGB", (8, 8)), "PNG")

        with pytest.raises(ImageTypeMismatchException) as caught:
            strip_image_metadata(source, ImageMediaTypeEnum.JPEG)

        assert caught.value.status_code == 415
        assert caught.value.details == {
            "declared": "image/jpeg",
            "detected": "image/png",
        }
        assert isinstance(caught.value, InvalidFileTypeException)

    @pytest.mark.parametrize("declared", ["image/heic", "application/pdf", "jpeg"])
    def test_type_that_cannot_be_reencoded(self, declared: str) -> None:
        source = encode(Image.new("RGB", (8, 8)), "JPEG")

        with pytest.raises(ImageFormatUnsupportedException) as caught:
            strip_image_metadata(source, declared)

        assert caught.value.status_code == 415

    def test_image_above_the_pixel_ceiling(self) -> None:
        source = encode(Image.new("RGB", (20, 20)), "PNG")

        with pytest.raises(ImageTooLargeException) as caught:
            strip_image_metadata(source, "image/png", max_pixels=399)

        assert caught.value.status_code == 413
        assert caught.value.details == {
            "width": 20,
            "height": 20,
            "max_pixels": 399,
        }
        assert isinstance(caught.value, FileTooLargeException)

    def test_image_at_the_pixel_ceiling_passes(self) -> None:
        source = encode(Image.new("RGB", (20, 20)), "PNG")

        result = open_bytes(strip_image_metadata(source, "image/png", max_pixels=400))

        assert result.size == (20, 20)

    def test_ceiling_is_checked_before_decoding(self) -> None:
        source = png_header_only(10_000, 6_000)

        with pytest.raises(ImageTooLargeException) as caught:
            strip_image_metadata(source, "image/png")

        assert caught.value.details["width"] == 10_000

    def test_pillow_bomb_error_becomes_too_large(self) -> None:
        source = png_header_only(20_000, 20_000)

        with pytest.raises(ImageTooLargeException):
            strip_image_metadata(source, "image/png", max_pixels=10**9)

    def test_pillow_bomb_warning_is_followed_by_the_refusal(self) -> None:
        source = png_header_only(10_000, 10_000)

        with (
            pytest.warns(Image.DecompressionBombWarning),
            pytest.raises(ImageTooLargeException),
        ):
            strip_image_metadata(source, "image/png")

    def test_escalated_pillow_bomb_warning_becomes_too_large(self) -> None:
        source = png_header_only(10_000, 10_000)

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with pytest.raises(ImageTooLargeException):
                strip_image_metadata(source, "image/png", max_pixels=10**9)

    def test_pillow_global_ceiling_is_untouched(self) -> None:
        before = Image.MAX_IMAGE_PIXELS
        source = encode(Image.new("RGB", (8, 8)), "PNG")

        strip_image_metadata(source, "image/png", max_pixels=10**12)

        assert before == Image.MAX_IMAGE_PIXELS

    @pytest.mark.parametrize(
        ("options", "message"),
        [
            ({"max_pixels": 0}, "max_pixels"),
            ({"jpeg_quality": 0}, "jpeg_quality"),
            ({"jpeg_quality": 101}, "jpeg_quality"),
            ({"webp_quality": 0}, "webp_quality"),
        ],
    )
    def test_invalid_arguments(self, options: dict[str, int], message: str) -> None:
        source = encode(Image.new("RGB", (8, 8)), "JPEG")

        with pytest.raises(ValueError, match=message):
            strip_image_metadata(source, "image/jpeg", **options)


class TestSurface:
    def test_missing_pillow_names_the_extra(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = encode(Image.new("RGB", (8, 8)), "JPEG")
        monkeypatch.setitem(sys.modules, "PIL", None)

        with pytest.raises(ImportError, match=r"\[image\]"):
            strip_image_metadata(source, "image/jpeg")

    def test_defaults(self) -> None:
        assert DEFAULT_MAX_IMAGE_PIXELS == 50_000_000
        assert DEFAULT_JPEG_QUALITY == 90
        assert DEFAULT_WEBP_QUALITY == 90

    @pytest.mark.parametrize(
        ("exception", "code"),
        [
            (ImageDecodeException, "IMAGE_DECODE_FAILED"),
            (ImageTypeMismatchException, "IMAGE_TYPE_MISMATCH"),
            (ImageFormatUnsupportedException, "IMAGE_FORMAT_UNSUPPORTED"),
            (ImageTooLargeException, "IMAGE_TOO_LARGE"),
        ],
    )
    def test_codes_are_localized(
        self, exception: type[AppException], code: str
    ) -> None:
        catalog = default_message_catalog()

        assert exception.code == code
        assert catalog.resolve(code, "pt-BR") is not None
        assert catalog.resolve(code, "en-US") is not None
