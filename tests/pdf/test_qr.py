"""The voucher encodes its own QR code, calibrated for its own box (#300).

The decoding tests read the symbol back with ``zxing-cpp``, a decoder that
shares no code with ``segno``, so a round trip proves the image is a QR
code a scanner reads — not that the encoder agrees with itself.
"""

from __future__ import annotations

import base64
import io
import re

import pytest
from pydantic import ValidationError

from tempest_fastapi_sdk.pdf import (
    QR_BORDER,
    QR_ERROR_CORRECTION,
    QR_SCALE,
    PdfRenderer,
    VoucherDocument,
    qr_data_uri,
)

URL = "https://alofans.example.com/v/8f3a2c1d-4b5e-4f6a-9c7d-1e2f3a4b5c6d"
PIX = (
    "00020101021226930014br.gov.bcb.pix2571qrcode.example.com.br/pix/v2/"
    "cobv/9d36b84f-c70b-478f-b95c-12729b90ca25520400005303986540510.005802BR"
    "5925ALOFANS EVENTOS LTDA ME6009SAO PAULO62070503***6304ABCD"
)


def _decode_png(uri: str) -> list[str]:
    """Decode every QR code in a PNG ``data:`` URI.

    Args:
        uri (str): The URI to decode.

    Returns:
        list[str]: The decoded texts.
    """
    zxingcpp = pytest.importorskip("zxingcpp")
    image_module = pytest.importorskip("PIL.Image")
    raw = base64.b64decode(uri.split(",", 1)[1])
    image = image_module.open(io.BytesIO(raw))
    return [barcode.text for barcode in zxingcpp.read_barcodes(image)]


class TestDefaults:
    def test_pinned_values(self) -> None:
        """Pinned so a change is a decision, made next to voucher.html."""
        assert (QR_ERROR_CORRECTION, QR_SCALE, QR_BORDER) == ("h", 8, 2)


class TestQrDataUri:
    def test_matches_the_voucher_field_pattern(self) -> None:
        pattern = VoucherDocument.model_fields["qr_data_uri"].metadata[0].pattern

        assert re.fullmatch(pattern, qr_data_uri("x"))

    @pytest.mark.parametrize("content", ["x", URL, PIX])
    def test_round_trips_through_an_independent_decoder(self, content: str) -> None:
        assert _decode_png(qr_data_uri(content)) == [content]

    def test_scale_and_border_set_the_pixel_size(self) -> None:
        """Version 1 is 21 modules; two of border a side make it 25."""
        image_module = pytest.importorskip("PIL.Image")
        raw = base64.b64decode(qr_data_uri("x").split(",", 1)[1])

        assert image_module.open(io.BytesIO(raw)).size == (25 * 8, 25 * 8)

    def test_empty_content_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            qr_data_uri("")

    def test_overflow_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            qr_data_uri("x" * 5000)


class TestVoucherQrContent:
    def test_content_is_encoded(self) -> None:
        document = VoucherDocument(heading="INGRESSO", qr_content=URL)

        assert document.qr_image is not None
        assert _decode_png(document.qr_image) == [URL]

    def test_data_uri_still_accepted(self) -> None:
        uri = qr_data_uri(URL)

        assert VoucherDocument(heading="X", qr_data_uri=uri).qr_image == uri

    def test_both_raise(self) -> None:
        with pytest.raises(ValidationError, match="not both"):
            VoucherDocument(heading="X", qr_content=URL, qr_data_uri=qr_data_uri(URL))

    def test_overflow_is_a_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            VoucherDocument(heading="X", qr_content="x" * 5000)

    def test_dump_validates_again(self) -> None:
        """The image is private, so the dump carries one of the two fields."""
        document = VoucherDocument(heading="X", qr_content=URL)

        again = VoucherDocument.model_validate(document.model_dump())

        assert again.qr_image == document.qr_image
        assert "qr_image" not in document.model_dump()

    def test_model_copy_re_encodes(self) -> None:
        """``model_copy`` runs no validator; the image must follow anyway."""
        template = VoucherDocument(heading="INGRESSO", qr_content=URL)

        copies = [
            template.model_copy(update={"qr_content": f"{URL}?seat={seat}"})
            for seat in (1, 2)
        ]

        assert [_decode_png(c.qr_image or "") for c in copies] == [
            [f"{URL}?seat=1"],
            [f"{URL}?seat=2"],
        ]
        assert _decode_png(template.qr_image or "") == [URL]

    def test_no_qr_by_default(self) -> None:
        assert VoucherDocument(heading="X").qr_image is None


class TestRenderedVoucher:
    """The calibration guard: the QR read back off the printed page."""

    @pytest.mark.parametrize("content", [URL, PIX])
    async def test_page_decodes_at_150_dpi(self, content: str) -> None:
        pymupdf = pytest.importorskip("pymupdf")
        zxingcpp = pytest.importorskip("zxingcpp")
        image_module = pytest.importorskip("PIL.Image")
        pytest.importorskip("weasyprint")
        pdf = await PdfRenderer().render_document(
            VoucherDocument(heading="INGRESSO", qr_content=content),
        )
        page = pymupdf.open(stream=pdf, filetype="pdf")[0]
        image = image_module.open(io.BytesIO(page.get_pixmap(dpi=150).tobytes("png")))

        assert [barcode.text for barcode in zxingcpp.read_barcodes(image)] == [content]

    async def test_image_is_not_interpolated(self) -> None:
        """``image-rendering: pixelated`` becomes ``/Interpolate false``."""
        pytest.importorskip("weasyprint")
        pdf = await PdfRenderer().render_document(
            VoucherDocument(heading="X", qr_content=URL),
        )

        assert re.findall(rb"/Interpolate \w+", pdf) == [b"/Interpolate false"]
