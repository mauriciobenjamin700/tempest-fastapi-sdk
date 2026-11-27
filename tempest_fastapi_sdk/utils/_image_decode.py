"""Decode an untrusted image so a truncated file fails, whatever Pillow's global says.

``PIL.ImageFile.LOAD_TRUNCATED_IMAGES`` is a process-wide switch, and
``import weasyprint`` (the ``[pdf]`` extra, imported on the first PDF render)
turns it on — measured with WeasyPrint 69.0 and 70.0 on Pillow 13.0.0. With
it on, Pillow fills the missing tail of a cut PNG, JPEG, GIF or BMP with gray
and returns, so validation that relied on ``load()`` raising accepted half a
file (#868).

The SDK does not read or write the switch. Flipping it back during a decode
would be a race against every other thread — a WeasyPrint render running at
the same moment raises ``OSError`` on a truncated PNG with the switch off
instead of drawing it. Instead the encoded bytes are served through
:class:`ImageStream`, which records whether the decoder asked for bytes past
the end. A complete image never does: its decoder stops at its own end
marker. A truncated one always does, because running out of input is what
truncation is.

Measured over every cut point of 18 encodings (PNG in six modes plus APNG,
baseline/progressive/CMYK/gray JPEG, static and animated GIF, BMP and RLE
BMP, lossy/lossless/animated WebP; 150 619 cuts), with the switch on: no cut
that Pillow refuses with the switch off was accepted, and every complete
image was accepted. The check is stricter in one place — a PNG missing only
the bytes after its pixel data (the last IDAT checksum and ``IEND``, up to
about 22 bytes), which Pillow's default accepts and this refuses.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage


class TruncatedImageError(OSError):
    """The decoder needed bytes past the end of the encoded image.

    Subclasses :class:`OSError`, the type Pillow raises for the same file
    with ``LOAD_TRUNCATED_IMAGES`` off, so a caller that already maps
    Pillow's decode errors maps this one too.
    """


class ImageStream(io.BytesIO):
    """In-memory image bytes that remember whether a read ran dry.

    Pass it to ``PIL.Image.open`` in place of ``io.BytesIO`` and decode
    with :meth:`load` instead of ``image.load()``.

    Attributes:
        starved (bool): Whether a read since the last :meth:`load` started
            asked for bytes and got none.
    """

    def __init__(self, data: bytes) -> None:
        """Wrap the encoded bytes.

        Args:
            data (bytes): The encoded image.
        """
        super().__init__(data)
        self.starved: bool = False

    def read(self, size: int | None = -1, /) -> bytes:
        """Read like ``io.BytesIO.read``, recording an empty answer.

        Args:
            size (int | None): Bytes wanted; negative or ``None`` reads to
                the end.

        Returns:
            bytes: Up to ``size`` bytes.
        """
        chunk = super().read(size)
        if not chunk and size != 0:
            self.starved = True
        return chunk

    def load(self, image: PILImage) -> None:
        """Decode ``image``'s pixels and refuse a truncated file.

        Only reads made by the decode count: the header parse done by
        ``Image.open`` is left out, so the caller can still reject on the
        declared size before paying for the pixels.

        Args:
            image (PILImage): An image opened from this stream.

        Raises:
            TruncatedImageError: When the decoder ran out of bytes, whatever
                ``LOAD_TRUNCATED_IMAGES`` is set to.
            OSError: Whatever Pillow raises for a corrupt file.
        """
        self.starved = False
        image.load()
        if self.starved:
            raise TruncatedImageError("image file is truncated")


def decode_image(source: bytes | str | Path) -> PILImage:
    """Open and fully decode an image from bytes or a file path.

    The file is read into memory first, so the returned image holds no open
    file handle.

    Args:
        source (bytes | str | Path): Encoded bytes, or a path to read them
            from.

    Returns:
        PILImage: The decoded image.

    Raises:
        TruncatedImageError: When the file is truncated.
        OSError: When Pillow cannot identify or decode the bytes.
    """
    from PIL import Image

    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    stream = ImageStream(data)
    image = Image.open(stream)
    try:
        stream.load(image)
    except BaseException:
        image.close()
        raise
    return image
