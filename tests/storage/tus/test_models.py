"""Header codecs and the upload record."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tempest_fastapi_sdk import TusInvalidHeaderException
from tempest_fastapi_sdk.storage.tus import (
    TusUpload,
    encode_upload_metadata,
    http_date,
    parse_upload_metadata,
    sanitize_upload_filename,
    valid_upload_id,
)
from tests.storage.tus._support import metadata_header


def test_metadata_round_trips_utf8_values() -> None:
    """Accented values survive the base64 header, as the client encodes them."""
    pairs = {"filename": "nota-ação.webm", "visit_id": "v-1", "filetype": "image/jpeg"}
    header = metadata_header(pairs)
    assert parse_upload_metadata(header) == pairs
    assert encode_upload_metadata(pairs) == header


def test_metadata_key_without_value_is_empty() -> None:
    """A bare key means an empty value, per tus 1.0.0."""
    assert parse_upload_metadata("flag,name Zm9v") == {"flag": "", "name": "foo"}


@pytest.mark.parametrize(
    "header",
    ["name !!!", "name Zm9v,name YmFy", "name Zm9v,,x", "name /w==", "a b c"],
)
def test_malformed_metadata_is_refused(header: str) -> None:
    """Bad base64, non-UTF-8 bytes, repeated or empty keys are refused."""
    with pytest.raises(TusInvalidHeaderException):
        parse_upload_metadata(header)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\ana\\foto.jpg", "foto.jpg"),
        ("nota-ação.webm", "nota-ação.webm"),
        ("a\r\nSet-Cookie: x.txt", "aSet-Cookie x.txt"),
        ("..", None),
        ("...hidden", "hidden"),
        ("", None),
        (None, None),
        ("x" * 300 + ".bin", "x" * 255),
    ],
)
def test_filename_is_sanitized(raw: str | None, expected: str | None) -> None:
    """Only a safe base name is ever returned."""
    assert sanitize_upload_filename(raw) == expected


def test_filename_property_falls_back_to_name() -> None:
    """``filename`` first, then ``name`` (what tus-js-client and Uppy send)."""
    upload = TusUpload(id="a" * 32, length=1, metadata={"name": "../x.png"})
    assert upload.filename == "x.png"


def test_record_round_trips_through_json() -> None:
    """Every field survives :meth:`to_json` / :meth:`from_json`."""
    upload = TusUpload(
        id="b" * 32,
        length=10,
        offset=4,
        metadata={"filename": "ç.txt"},
        owner="42",
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        idempotency_digest="c" * 64,
        notified=True,
    )
    assert TusUpload.from_json(upload.to_json()) == upload


def test_only_incomplete_uploads_expire() -> None:
    """A complete upload never expires, whatever its ``expires_at``."""
    past = datetime.now(UTC) - timedelta(seconds=1)
    assert TusUpload(id="d" * 32, length=2, offset=1, expires_at=past).is_expired()
    assert not TusUpload(id="d" * 32, length=2, offset=2, expires_at=past).is_expired()
    assert not TusUpload(id="d" * 32, length=2, offset=1).is_expired()


def test_http_date_is_rfc9110() -> None:
    """``Upload-Expires`` uses the IMF-fixdate form."""
    moment = datetime(2026, 6, 25, 16, 0, tzinfo=UTC)
    assert http_date(moment) == "Thu, 25 Jun 2026 16:00:00 GMT"


@pytest.mark.parametrize(
    ("candidate", "valid"),
    [("A" * 32, True), ("a-b_c" * 4, True), ("../" * 8, False), ("a.b" * 8, False)],
)
def test_upload_id_shape(candidate: str, valid: bool) -> None:
    """Ids never contain a path separator or a dot."""
    assert valid_upload_id(candidate) is valid
