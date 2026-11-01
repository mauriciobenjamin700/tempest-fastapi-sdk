"""Tests for :class:`tempest_fastapi_sdk.utils.media_types.ImageMediaTypeEnum`."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from tempest_fastapi_sdk import SNIFFABLE_MIMETYPES, ImageMediaTypeEnum, sniff_mime

_SAMPLES: dict[ImageMediaTypeEnum, bytes] = {
    ImageMediaTypeEnum.JPEG: b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 20,
    ImageMediaTypeEnum.PNG: b"\x89PNG\r\n\x1a\n" + b"\x00" * 24,
    ImageMediaTypeEnum.GIF: b"GIF89a" + b"\x00" * 26,
    ImageMediaTypeEnum.BMP: b"BM" + b"\x00" * 30,
    ImageMediaTypeEnum.WEBP: b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 16,
    ImageMediaTypeEnum.HEIC: (
        b"\x00\x00\x00\x1cftypheic\x00\x00\x00\x00mif1heicmiaf"
        b"\x00\x00\x01|meta\x00\x00\x00\x00"
    ),
}


class PhotoSchema(BaseModel):
    """Schema carrying a declared image ``Content-Type``."""

    content_type: ImageMediaTypeEnum


app = FastAPI()


@app.post("/photos")
async def create_photo(payload: PhotoSchema) -> dict[str, str]:
    """Echo the canonical media type and its extension."""
    return {
        "content_type": payload.content_type.value,
        "extension": payload.content_type.extension,
    }


def test_members_are_exactly_the_sniffable_image_types() -> None:
    assert {member.value for member in ImageMediaTypeEnum} == {
        mime for mime in SNIFFABLE_MIMETYPES if mime.startswith("image/")
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("image/jpeg", ImageMediaTypeEnum.JPEG),
        ("IMAGE/PNG", ImageMediaTypeEnum.PNG),
        ("  image/webp\n", ImageMediaTypeEnum.WEBP),
        ("Image/HEIC", ImageMediaTypeEnum.HEIC),
        ("image/jpg", ImageMediaTypeEnum.JPEG),
        (" Image/PJPEG ", ImageMediaTypeEnum.JPEG),
        ("image/x-png", ImageMediaTypeEnum.PNG),
    ],
)
def test_lenient_lookup(raw: str, expected: ImageMediaTypeEnum) -> None:
    assert ImageMediaTypeEnum(raw) is expected
    assert PhotoSchema(content_type=raw).content_type is expected  # type: ignore[arg-type]
    from_json = PhotoSchema.model_validate_json(json.dumps({"content_type": raw}))
    assert from_json.content_type is expected


@pytest.mark.parametrize(
    "raw",
    ["image/avif", "image/svg+xml", "text/html", "image/heif", "", "jpeg"],
)
def test_unknown_value_fails_validation(raw: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        PhotoSchema(content_type=raw)  # type: ignore[arg-type]
    assert excinfo.value.errors()[0]["type"] == "enum"
    with pytest.raises(ValueError, match="is not a valid ImageMediaTypeEnum"):
        ImageMediaTypeEnum(raw)


def test_non_string_fails_validation() -> None:
    with pytest.raises(ValidationError):
        PhotoSchema(content_type=1)  # type: ignore[arg-type]


def test_json_schema_lists_canonical_values() -> None:
    schema = PhotoSchema.model_json_schema()
    assert schema["$defs"]["ImageMediaTypeEnum"]["enum"] == [
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/bmp",
        "image/webp",
        "image/heic",
    ]


def test_route_accepts_alias_and_rejects_unknown_with_422() -> None:
    client = TestClient(app)
    ok = client.post("/photos", json={"content_type": " IMAGE/JPG "})
    assert ok.status_code == 200
    assert ok.json() == {"content_type": "image/jpeg", "extension": "jpg"}
    bad = client.post("/photos", json={"content_type": "image/avif"})
    assert bad.status_code == 422


@pytest.mark.parametrize(
    ("member", "extension"),
    [
        (ImageMediaTypeEnum.JPEG, "jpg"),
        (ImageMediaTypeEnum.PNG, "png"),
        (ImageMediaTypeEnum.GIF, "gif"),
        (ImageMediaTypeEnum.BMP, "bmp"),
        (ImageMediaTypeEnum.WEBP, "webp"),
        (ImageMediaTypeEnum.HEIC, "heic"),
    ],
)
def test_extension(member: ImageMediaTypeEnum, extension: str) -> None:
    assert member.extension == extension


@pytest.mark.parametrize("member", list(ImageMediaTypeEnum))
def test_matches_own_signature_only(member: ImageMediaTypeEnum) -> None:
    for candidate, data in _SAMPLES.items():
        assert member.matches(data) is (candidate is member)
        assert sniff_mime(data) == candidate.value


def test_matches_rejects_polyglot_and_avif() -> None:
    html = b"<!DOCTYPE html><script>alert(1)</script>"
    avif = b"\x00\x00\x00 ftypavif\x00\x00\x00\x00avifmif1miafMA1B\x00\x00\x00\xebmeta"
    assert not any(member.matches(html) for member in ImageMediaTypeEnum)
    assert not ImageMediaTypeEnum.HEIC.matches(avif)
