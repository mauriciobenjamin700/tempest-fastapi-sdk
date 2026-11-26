"""``GoogleDriveUploadStorage`` and its client, checked on the wire."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from datetime import timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from tempest_fastapi_sdk import UploadStorage, UploadUtils
from tempest_fastapi_sdk.exceptions import (
    FileTooLargeException,
    GoogleDriveException,
    GoogleDriveFolderNotFoundException,
    InvalidFileTypeException,
    PresignedUrlUnsupportedException,
)
from tempest_fastapi_sdk.integrations.storage.google_drive import (
    APP_PROPERTY_MAX_BYTES,
    GoogleDriveClient,
    GoogleDriveUploadStorage,
    ServiceAccountCredentials,
    ServiceAccountTokenProvider,
    escape_query_value,
)
from tests.integrations.storage.google_drive.conftest import (
    CLIENT_EMAIL,
    FOLDER_ID,
    FakeDrive,
    Sleeps,
    stream,
)

KIB: int = 1024
PIECE: int = 256 * KIB

MakeStorage = Callable[..., GoogleDriveUploadStorage]


def _params(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}


class TestEscaping:
    @pytest.mark.parametrize(
        ("raw", "escaped"),
        [
            ("plain.txt", "plain.txt"),
            ("it's.txt", "it\\'s.txt"),
            ("a\\b", "a\\\\b"),
            ("\\'", "\\\\\\'"),
        ],
    )
    def test_escape_query_value(self, raw: str, escaped: str) -> None:
        assert escape_query_value(raw) == escaped

    async def test_lookup_query_escapes_quote_and_backslash(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage()
        key = "docs/it's a\\b.txt"
        await storage.write_stream(key, stream(b"hello"))
        fake.requests.clear()

        assert await storage.exists(key) is True
        assert await storage.exists("docs/it's a\\b.tx") is False

        query = _params(fake.api_requests()[0])["q"]
        assert query == (
            f"'{FOLDER_ID}' in parents and appProperties has "
            "{ key='tempest_key' and value='docs/it\\'s a\\\\b.txt' } "
            "and trashed = false"
        )


class TestWrite:
    async def test_small_file_is_one_session_and_one_piece(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage()
        result = await storage.write_stream(
            "avatars/u1.png", stream(b"\x89PNG", b"rest"), content_type="image/png"
        )

        assert result.key == "avatars/u1.png"
        assert result.size == 8
        assert result.path is None
        assert result.url == "https://drive.google.com/file/d/file-1/view"
        assert fake.contents["file-1"] == b"\x89PNGrest"

        listing, start = fake.api_requests()
        assert listing.method == "GET"
        assert start.method == "POST"
        assert start.url.path == "/upload/drive/v3/files"
        assert _params(start)["uploadType"] == "resumable"
        assert start.headers["X-Upload-Content-Type"] == "image/png"
        assert start.headers["Authorization"] == "Bearer ya29.token-1"
        assert json.loads(start.content) == {
            "name": "u1.png",
            "mimeType": "image/png",
            "appProperties": {"tempest_key": "avatars/u1.png"},
            "parents": [FOLDER_ID],
        }
        (piece,) = fake.piece_requests()
        assert piece.method == "PUT"
        assert piece.headers["Content-Range"] == "bytes 0-7/8"
        assert piece.content == b"\x89PNGrest"

    async def test_every_api_call_supports_all_drives(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage()
        await storage.write_stream("k", stream(b"x"))
        await storage.write_stream("k", stream(b"y"))
        await storage.exists("k")
        await storage.delete("k")
        calls = fake.api_requests()
        assert len(calls) >= 6
        for request in calls:
            assert _params(request)["supportsAllDrives"] == "true", request.url
        for request in calls:
            if request.url.path == "/drive/v3/files":
                assert _params(request)["includeItemsFromAllDrives"] == "true"
                assert _params(request)["corpora"] == "allDrives"

    async def test_drive_id_narrows_the_search(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage()
        storage.drive_id = "0ASharedDrive"
        await storage.exists("k")
        params = _params(fake.api_requests()[0])
        assert params["corpora"] == "drive"
        assert params["driveId"] == "0ASharedDrive"

    async def test_large_stream_goes_in_256kib_multiples(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage(chunk_size=PIECE)
        payload = bytes(range(256)) * (600 * KIB // 256)
        parts = [payload[i : i + 100 * KIB] for i in range(0, len(payload), 100 * KIB)]

        result = await storage.write_stream("big.bin", stream(*parts))

        ranges = [r.headers["Content-Range"] for r in fake.piece_requests()]
        assert ranges == [
            "bytes 0-262143/*",
            "bytes 262144-524287/*",
            f"bytes 524288-{len(payload) - 1}/{len(payload)}",
        ]
        for request in fake.piece_requests()[:-1]:
            assert len(request.content) % PIECE == 0
        assert fake.contents["file-1"] == payload
        assert result.size == len(payload)

    async def test_308_with_short_range_resends_only_the_missing_bytes(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage(chunk_size=PIECE)
        payload = b"a" * PIECE + b"b" * PIECE + b"c" * 1000
        fake.keep_only = [100_000]

        await storage.write_stream("partial.bin", stream(payload))

        ranges = [r.headers["Content-Range"] for r in fake.piece_requests()]
        assert ranges[0] == "bytes 0-262143/*"
        assert ranges[1] == f"bytes 100000-{100_000 + PIECE - 1}/*"
        assert fake.contents["file-1"] == payload
        assert len([r for r in fake.requests if r.method == "POST"]) == 2

    async def test_5xx_on_a_piece_is_retried_after_a_status_query(
        self, fake: FakeDrive, make_storage: MakeStorage, sleeps: Sleeps
    ) -> None:
        storage = make_storage(chunk_size=PIECE)
        payload = b"z" * (PIECE + 10)
        fake.piece_failures = [503]

        await storage.write_stream("retry.bin", stream(payload))

        ranges = [r.headers["Content-Range"] for r in fake.piece_requests()]
        assert ranges == [
            "bytes 0-262143/*",
            "bytes */*",
            "bytes 0-262143/*",
            f"bytes 262144-{PIECE + 9}/{PIECE + 10}",
        ]
        assert sleeps.delays == [0.5]
        assert fake.contents["file-1"] == payload
        starts = [r for r in fake.api_requests() if r.method == "POST"]
        assert len(starts) == 1

    async def test_429_backoff_doubles_and_stops_at_the_limit(
        self, fake: FakeDrive, make_storage: MakeStorage, sleeps: Sleeps
    ) -> None:
        storage = make_storage(max_retries=2)
        fake.piece_failures = [429, 429, 429, 429]

        with pytest.raises(GoogleDriveException) as raised:
            await storage.write_stream("k", stream(b"data"))

        assert raised.value.upstream_status == 429
        assert raised.value.reason == "backendError"
        assert sleeps.delays == [0.5, 1.0]
        assert fake.files == {}

    async def test_non_retryable_error_is_raised_at_once(
        self, fake: FakeDrive, make_storage: MakeStorage, sleeps: Sleeps
    ) -> None:
        storage = make_storage()
        fake.piece_failures = [403]
        with pytest.raises(GoogleDriveException) as raised:
            await storage.write_stream("k", stream(b"data"))
        assert raised.value.upstream_status == 403
        assert raised.value.status_code == 502
        assert raised.value.details == {"status": 403, "reason": "backendError"}
        assert sleeps.delays == []

    async def test_empty_file_declares_zero_length(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage()
        result = await storage.write_stream("empty.txt", stream())
        (piece,) = fake.piece_requests()
        assert piece.headers["Content-Range"] == "bytes */0"
        assert result.size == 0
        assert fake.contents["file-1"] == b""

    async def test_missing_link_is_read_back(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        fake.omit_link_on_upload = True
        storage = make_storage()
        result = await storage.write_stream("k", stream(b"x"))
        assert result.url == "https://drive.google.com/file/d/file-1/view"
        last = fake.api_requests()[-1]
        assert last.method == "GET"
        assert last.url.path == "/drive/v3/files/file-1"

    async def test_metadata_goes_into_app_properties(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage()
        await storage.write_stream("k", stream(b"x"), metadata={"owner": "u1"})
        assert fake.files["file-1"]["appProperties"] == {
            "owner": "u1",
            "tempest_key": "k",
        }
        with pytest.raises(ValueError, match="reserved"):
            await storage.write_stream(
                "k", stream(b"x"), metadata={"tempest_key": "other"}
            )

    async def test_key_over_the_property_limit_is_refused_before_any_request(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage()
        key = "k" * (APP_PROPERTY_MAX_BYTES - len("tempest_key") + 1)
        with pytest.raises(InvalidFileTypeException):
            await storage.write_stream(key, stream(b"x"))
        assert fake.requests == []
        fitting = "k" * (APP_PROPERTY_MAX_BYTES - len("tempest_key"))
        await storage.write_stream(fitting, stream(b"x"))


class TestRepeatedKey:
    async def test_second_write_replaces_the_content_of_the_same_file(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage()
        first = await storage.write_stream("doc.txt", stream(b"version one"))
        fake.requests.clear()

        second = await storage.write_stream(
            "doc.txt", stream(b"v2"), content_type="text/plain"
        )

        assert list(fake.files) == ["file-1"]
        assert fake.contents["file-1"] == b"v2"
        assert second.url == first.url
        assert second.size == 2
        start = [r for r in fake.api_requests() if r.url.path.startswith("/upload")]
        assert [r.method for r in start] == ["PATCH"]
        assert start[0].url.path == "/upload/drive/v3/files/file-1"
        assert "parents" not in json.loads(start[0].content)
        assert len(await storage.find("doc.txt")) == 1
        assert await storage.exists("doc.txt") is True


class TestDeleteAndExists:
    async def test_delete_then_exists(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage()
        await storage.write_stream("gone.txt", stream(b"x"))
        assert await storage.exists("gone.txt") is True

        assert await storage.delete("gone.txt") is True

        deletes = [r for r in fake.requests if r.method == "DELETE"]
        assert [r.url.path for r in deletes] == ["/drive/v3/files/file-1"]
        assert await storage.exists("gone.txt") is False
        assert await storage.delete("gone.txt") is False

    async def test_delete_removes_every_duplicate(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage()
        await storage.write_stream("dup", stream(b"1"))
        fake.files["file-2"] = {**fake.files["file-1"], "id": "file-2"}
        assert len(await storage.find("dup")) == 2
        assert await storage.delete("dup") is True
        assert fake.files == {}

    async def test_find_on_empty_folder_is_an_empty_list(
        self, make_storage: MakeStorage
    ) -> None:
        assert await make_storage().find("nothing") == []

    async def test_web_view_link(self, make_storage: MakeStorage) -> None:
        storage = make_storage()
        assert await storage.web_view_link("k") is None
        await storage.write_stream("k", stream(b"x"))
        assert (
            await storage.web_view_link("k")
            == "https://drive.google.com/file/d/file-1/view"
        )


class TestFolderNotFound:
    async def test_create_into_unshared_folder_names_the_account(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        fake.folders = {"other"}
        storage = make_storage()
        with pytest.raises(GoogleDriveFolderNotFoundException) as raised:
            await storage.write_stream("k", stream(b"x"))
        error = raised.value
        assert error.code == "GOOGLE_DRIVE_FOLDER_NOT_FOUND"
        assert error.status_code == 502
        assert CLIENT_EMAIL in error.message
        assert FOLDER_ID in error.message
        assert error.details == {
            "status": 404,
            "reason": "notFound",
            "folder_id": FOLDER_ID,
        }

    async def test_create_session_404_maps_to_folder_not_found(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        fake.folders = set()
        fake.list_unknown_folder_as_empty = True
        storage = make_storage()
        with pytest.raises(GoogleDriveFolderNotFoundException) as raised:
            await storage.write_stream("k", stream(b"x"))
        assert [r.method for r in fake.api_requests()] == ["GET", "POST"]
        assert CLIENT_EMAIL in raised.value.message


class TestValidation:
    async def test_validator_rejection_opens_no_session(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage()
        with pytest.raises(InvalidFileTypeException):
            await storage.write_stream(
                "k", stream(b"MZ-not-a-png"), validator=lambda b: b.startswith(b"PNG")
            )
        assert not [r for r in fake.requests if r.url.path.startswith("/upload")]
        assert fake.files == {}

    async def test_too_large_stream_creates_no_file(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage(chunk_size=PIECE)
        parts = [b"x" * (100 * KIB)] * 6
        with pytest.raises(FileTooLargeException):
            await storage.write_stream("k", stream(*parts), max_size_bytes=500 * KIB)
        assert fake.files == {}
        assert all(
            r.headers["Content-Range"].endswith("/*") for r in fake.piece_requests()
        )


class TestPresignedUrl:
    async def test_presigned_url_is_refused(self, make_storage: MakeStorage) -> None:
        storage = make_storage()
        with pytest.raises(PresignedUrlUnsupportedException) as raised:
            await storage.presigned_url("k", expires=timedelta(minutes=5))
        assert raised.value.status_code == 501
        assert raised.value.code == "PRESIGNED_URL_UNSUPPORTED"
        assert raised.value.details == {"backend": "google_drive"}
        assert "web_view_link" in raised.value.message


class TestUploadUtils:
    async def test_backend_satisfies_the_protocol_and_saves(
        self, fake: FakeDrive, make_storage: MakeStorage
    ) -> None:
        storage = make_storage()
        assert isinstance(storage, UploadStorage)
        uploads = UploadUtils(backend=storage, allowed_extensions={"txt"})
        upload = UploadFile(
            file=io.BytesIO(b"hello drive"),
            filename="hello.txt",
            headers=Headers({"content-type": "text/plain"}),
        )

        key = await uploads.save(upload, subdir="notes", filename="hello.txt")

        assert str(key) == "notes/hello.txt"
        assert await storage.exists("notes/hello.txt") is True
        assert fake.contents["file-1"] == b"hello drive"
        assert fake.files["file-1"]["name"] == "hello.txt"
        assert fake.files["file-1"]["mimeType"] == "text/plain"
        assert await uploads.delete(key) is True
        assert fake.token_calls == 1


class TestConstruction:
    def test_chunk_size_must_be_a_multiple_of_256_kib(
        self, http: httpx.AsyncClient, credentials_json: str
    ) -> None:
        provider = ServiceAccountTokenProvider(
            ServiceAccountCredentials.from_json(credentials_json), http_client=http
        )
        with pytest.raises(ValueError, match="256 KiB"):
            GoogleDriveClient(
                token_provider=provider, http_client=http, chunk_size=300 * KIB
            )

    def test_folder_is_required(self, credentials_json: str) -> None:
        with pytest.raises(ValueError, match="GOOGLE_DRIVE_FOLDER_ID"):
            GoogleDriveUploadStorage.from_service_account(
                folder_id="", credentials_json=credentials_json
            )

    async def test_from_service_account_owns_and_closes_its_client(
        self, credentials_json: str
    ) -> None:
        storage = GoogleDriveUploadStorage.from_service_account(
            folder_id=FOLDER_ID, credentials_json=credentials_json
        )
        assert storage.client.principal_email == CLIENT_EMAIL
        owned = storage._owned_http
        assert owned is not None
        await storage.aclose()
        assert owned.is_closed

    async def test_injected_client_is_left_open(
        self, http: httpx.AsyncClient, credentials_json: str
    ) -> None:
        storage = GoogleDriveUploadStorage.from_service_account(
            folder_id=FOLDER_ID, credentials_json=credentials_json, http_client=http
        )
        await storage.aclose()
        assert not http.is_closed
