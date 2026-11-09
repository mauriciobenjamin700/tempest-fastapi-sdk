# Image metadata — cleaning a user's photo

A photo taken on a phone carries more than pixels. Inside the file go the
**GPS coordinates** of where it was taken, the device make, model and
sometimes serial number, the date and time, and now and then a thumbnail of
the original photo, without the crop the user made.

Nobody sees any of this on screen. But when your service stores the photo, or
forwards it to a third party (an AI provider, a partner, a public bucket), the
metadata goes along. A profile picture becomes the home address of whoever
sent it.

`strip_image_metadata` hands back the same image, in the same format, without
any of that.

!!! info "Required extra"
    ```bash
    uv add "tempest-fastapi-sdk[image]"
    ```
    Brings only `pillow`. `[all]` already includes it.

## Your first clean upload route

A route that receives the photo, cleans it and writes it to disk:

```python
# main.py

import asyncio
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Form, UploadFile
from pydantic import BaseModel

from tempest_fastapi_sdk import (
    ImageMediaTypeEnum,
    read_upload_capped,
    register_exception_handlers,
    strip_image_metadata,
)

MAX_PHOTO_BYTES: int = 10 * 1024 * 1024
PHOTOS_DIR: Path = Path("photos")


class PhotoResponseSchema(BaseModel):
    """Where the photo was stored, and how big it ended up."""

    key: str
    content_type: ImageMediaTypeEnum
    size: int


app = FastAPI()
register_exception_handlers(app)


@app.post("/photos")
async def upload_photo(
    content_type: Annotated[ImageMediaTypeEnum, Form()],
    file: UploadFile,
) -> PhotoResponseSchema:
    """Store the photo only after its metadata is gone."""
    data: bytes = await read_upload_capped(file, max_bytes=MAX_PHOTO_BYTES)
    clean: bytes = await asyncio.to_thread(strip_image_metadata, data, content_type)
    key: str = f"{uuid4()}.{content_type.extension}"
    PHOTOS_DIR.mkdir(exist_ok=True)
    await asyncio.to_thread((PHOTOS_DIR / key).write_bytes, clean)
    return PhotoResponseSchema(key=key, content_type=content_type, size=len(clean))
```

### Try it

This script builds a JPEG the way a phone saves one (GPS, device and an
"upright" orientation), sends it to the route and checks the stored file:

```python
# try_it.py

import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from PIL.ExifTags import GPS, IFD, Base

from main import PHOTOS_DIR, app


def phone_photo() -> bytes:
    """Build a JPEG the way a phone saves one: GPS, device and orientation."""
    exif = Image.Exif()
    exif[Base.Make] = "AcmePhone"
    exif[Base.Orientation] = 6
    exif[IFD.GPSInfo] = {GPS.GPSLatitudeRef: "S", GPS.GPSLatitude: (23.0, 33.0, 1.0)}
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), "teal").save(buffer, "JPEG", exif=exif)
    return buffer.getvalue()


def main() -> None:
    """Upload the photo and inspect what was stored."""
    client = TestClient(app)
    photo: bytes = phone_photo()
    response = client.post(
        "/photos",
        data={"content_type": "image/jpeg"},
        files={"file": ("photo.jpg", photo, "image/jpeg")},
    )
    print(response.status_code, response.json()["content_type"])
    stored: Path = PHOTOS_DIR / response.json()["key"]
    with Image.open(stored) as image:
        print("size:", image.size)
        print("exif tags:", len(image.getexif()))
    print("AcmePhone in the original:", b"AcmePhone" in photo)
    print("AcmePhone in the stored file:", b"AcmePhone" in stored.read_bytes())
    wrong = client.post(
        "/photos",
        data={"content_type": "image/png"},
        files={"file": ("photo.png", photo, "image/png")},
    )
    print(wrong.status_code, wrong.json()["code"])


if __name__ == "__main__":
    main()
```

Run it with `python try_it.py`:

```text
200 image/jpeg
size: (300, 400)
exif tags: 0
AcmePhone in the original: True
AcmePhone in the stored file: False
415 IMAGE_TYPE_MISMATCH
```

The uploaded photo was 400 x 300 with orientation tag `6` ("rotate 90° to
display"). The stored one is 300 x 400 with **no** tag at all: the rotation was
applied to the pixels before the EXIF went away, so it stays upright in any
viewer. The same JPEG declared as PNG was refused.

## Piece by piece

### Read with a cap

`read_upload_capped` stops reading at the byte cap, so a huge upload never
lands in memory whole. That is the **file** limit; the **pixel** limit comes
next.

### Clean off the event loop

`strip_image_metadata` decodes and encodes again: CPU work, in a
synchronous function.
Called directly in an `async` route, it blocks the event loop while it runs;
`asyncio.to_thread` moves it to a thread and the server keeps serving.

Its second argument is the type the client **declared**. The function checks
the bytes against it and refuses when they disagree: you never store a PNG
with a `.jpg` extension.

### Store the result, never the original

After the cleaning, `data` should go nowhere. Store and forward only `clean`.

!!! tip "An error never hands the original bytes back"
    When the image does not decode, does not match the type or is over the
    ceiling, the function **raises**. There is no path where it returns
    `data` untouched "because it could not clean it", so a `try`/`except`
    that goes on storing the original would be an explicit choice by whoever
    wrote it, not an accident.

## What goes and what stays

| Goes | Stays |
| --- | --- |
| EXIF (GPS, make, model, date, embedded thumbnail) | the pixels, already in the right orientation |
| XMP | the size (width and height swapped when the orientation rotates by 90°) |
| IPTC (the JPEG `Photoshop 3.0` segment) | the mode (`RGB`, `RGBA`, `L`, `P`...) |
| ICC color profile | the transparency |
| comments (JPEG, GIF) and PNG text chunks (`tEXt`, `iTXt`, `zTXt`) | the format |

Accepted formats: JPEG, PNG, WEBP, GIF and BMP. The `image/heic` member of
`ImageMediaTypeEnum` is refused with `415`: Pillow ships no HEIC codec.

!!! warning "An animation becomes its first frame"
    Animated GIF and WEBP come back with **only their first frame**.
    Re-encoding every frame would multiply the cost by a frame count the
    client controls. If your service must keep animations, do not route that
    upload through here.

!!! note "Without the ICC profile, color is read as sRGB"
    The color profile goes too, and the pixels are then read as sRGB. On a
    photo that was already sRGB nothing changes; on a wide-gamut photo
    (Display P3, common on recent phones) the color can shift slightly in
    saturation.

## The errors

They all extend `AppException`, so `register_exception_handlers` already turns
them into a response with a `code`:

| Exception | Status | `code` | When |
| --- | --- | --- | --- |
| `ImageDecodeException` | `422` | `IMAGE_DECODE_FAILED` | the bytes are not an image of an accepted format, or are truncated |
| `ImageTypeMismatchException` | `415` | `IMAGE_TYPE_MISMATCH` | it is an image, but of a different format than declared |
| `ImageFormatUnsupportedException` | `415` | `IMAGE_FORMAT_UNSUPPORTED` | the declared type is not one the function re-encodes (`image/heic`, `application/pdf`) |
| `ImageTooLargeException` | `413` | `IMAGE_TOO_LARGE` | the image has more pixels than `max_pixels` |

`ImageTypeMismatchException` and `ImageFormatUnsupportedException` extend
`InvalidFileTypeException`, and `ImageTooLargeException` extends
`FileTooLargeException`: an `except` that already caught those two keeps
catching.

## The pixel ceiling

A PNG of a few KB can declare 20 000 x 20 000 pixels. Decoded, it turns into
gigabytes of memory: a *decompression bomb*. That is why the function reads
width and height from the header and checks them against `max_pixels`
**before** decoding any pixel:

```python
from tempest_fastapi_sdk import DEFAULT_MAX_IMAGE_PIXELS, strip_image_metadata


def clean_avatar(data: bytes) -> bytes:
    """Clean an avatar, accepting at most 4 000 x 4 000 pixels."""
    return strip_image_metadata(data, "image/jpeg", max_pixels=16_000_000)


print(DEFAULT_MAX_IMAGE_PIXELS)
```

The default is `DEFAULT_MAX_IMAGE_PIXELS` (50 million), which fits the full
photo of a 48 or 50 MP sensor. Memory grows with the pixels: measured on
Pillow 13.0.0 with an 8000 x 6000 JPEG, the call's peak rose by about 186 MB
for an upright photo and 368 MB for one its orientation rotates by 90°. Raise
the ceiling with that in mind.

??? info "Technical details: Pillow's global ceiling"
    Pillow has its own guard, `PIL.Image.MAX_IMAGE_PIXELS` (about 89.5
    million by default), which applies to the whole process. The function
    does **not** touch it. It still runs when the file is opened: above it
    Pillow emits a `DecompressionBombWarning`, and above twice that it
    refuses on its own. Both cases reach you as `ImageTooLargeException`
    (the warning when you escalate it to an error; otherwise the function's
    ceiling refuses right after).

## Re-encoding quality

| Format | How it comes back |
| --- | --- |
| JPEG | `jpeg_quality`, default `DEFAULT_JPEG_QUALITY` (90; Pillow's is 75) |
| Lossless WEBP | stays lossless |
| Lossy WEBP | `webp_quality`, default `DEFAULT_WEBP_QUALITY` (90) |
| PNG, GIF, BMP | lossless, as always |

JPEG is re-encoded lossy, so the bytes change even for a photo that had no
metadata at all. The function does not resize.

??? info "Technical details: why re-encode instead of cutting segments"
    You can drop a JPEG's EXIF by cutting its `APP1` segment. But each format
    hides metadata somewhere else: `APP1` (EXIF and XMP) and `APP13` (IPTC)
    in JPEG, `eXIf`, `iCCP`, `tEXt`, `iTXt` and `zTXt` in PNG, `EXIF`,
    `XMP ` and `ICCP` in WEBP, comment extensions in GIF. A hand-written
    cutter misses the case it does not know. The function opens the image
    through Pillow's decoder, keeps only the pixels (and the transparency
    they need) and encodes again without passing `exif=`, `icc_profile=` or
    `pnginfo=`, so whatever the source file carried has no way back.

## Recap

- `strip_image_metadata(data, content_type)` returns the image in the same
  format, without EXIF, XMP, IPTC, ICC, comments and text chunks.
- The EXIF orientation is applied to the pixels before it goes: the photo
  stays upright.
- A declared type that differs from the real one, bytes that do not decode
  and an image above `max_pixels` raise a typed error; the original never
  comes back.
- In an `async` route, call it through `asyncio.to_thread`.
- An animation comes back as its first frame; HEIC is refused.
- Extra `[image]` (only `pillow`).

See also: [Uploads](uploads.md), to receive and validate the file before
cleaning it.
