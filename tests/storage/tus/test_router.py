"""The tus routes, driven the way ``tempest-react-sdk``'s client drives them.

Every test runs against both stores: :class:`LocalTusStore` on a temporary
directory and :class:`S3TusStore` over the in-memory
:class:`FakeObjectStorageClient`. The real-MinIO run lives in
``test_s3_live.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from starlette.middleware.cors import CORSMiddleware

from tempest_fastapi_sdk.storage.tus import (
    TUS_CORS_ALLOW_HEADERS,
    TUS_CORS_EXPOSE_HEADERS,
    LocalTusStore,
    S3TusStore,
    TusStore,
)
from tempest_fastapi_sdk.testing.fakes import FakeObjectStorageClient
from tests.storage.tus._support import (
    CHUNK,
    TUS,
    CompletionRecorder,
    build_app,
    metadata_header,
)

PAYLOAD: bytes = bytes(range(256)) * 40
"""10 240 bytes whose values encode their offset modulo 256."""


@pytest.fixture(params=["local", "s3"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> TusStore:
    """Yield each store kind in turn.

    Args:
        request (pytest.FixtureRequest): Carries the param.
        tmp_path (Path): Root for the local store.

    Returns:
        TusStore: The store.
    """
    if request.param == "local":
        return LocalTusStore(tmp_path / "tus")
    return S3TusStore(FakeObjectStorageClient(), prefix="uploads/tus", min_chunk_size=0)


@pytest.fixture
def recorder() -> CompletionRecorder:
    """Return a fresh completion recorder.

    Returns:
        CompletionRecorder: The recorder.
    """
    return CompletionRecorder()


@pytest_asyncio.fixture
async def client(
    store: TusStore,
    recorder: CompletionRecorder,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an HTTP client on an app with the tus router.

    Args:
        store (TusStore): The store.
        recorder (CompletionRecorder): The completion callback.

    Yields:
        httpx.AsyncClient: The client, acting as user ``ana`` by default.
    """
    app = build_app(store, on_complete=recorder)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"X-User": "ana"},
    ) as http:
        yield http


async def _create(
    http: httpx.AsyncClient,
    length: int,
    **headers: str,
) -> httpx.Response:
    """Send a creation ``POST``.

    Args:
        http (httpx.AsyncClient): The client.
        length (int): ``Upload-Length``.
        **headers (str): Extra headers, ``_`` read as ``-``.

    Returns:
        httpx.Response: The answer.
    """
    extra = {key.replace("_", "-"): value for key, value in headers.items()}
    return await http.post(
        "/api/uploads",
        headers={**TUS, "Upload-Length": str(length), **extra},
    )


async def _patch(
    http: httpx.AsyncClient,
    url: str,
    offset: int,
    body: bytes,
    **headers: str,
) -> httpx.Response:
    """Send one chunk.

    Args:
        http (httpx.AsyncClient): The client.
        url (str): Upload URL.
        offset (int): ``Upload-Offset``.
        body (bytes): The chunk.
        **headers (str): Header overrides.

    Returns:
        httpx.Response: The answer.
    """
    return await http.patch(
        url,
        content=body,
        headers={**CHUNK, "Upload-Offset": str(offset), **headers},
    )


async def test_full_upload_in_chunks_reaches_on_complete(
    client: httpx.AsyncClient,
    recorder: CompletionRecorder,
) -> None:
    """Create, probe and write in chunks; the callback sees the exact bytes."""
    created = await _create(
        client,
        len(PAYLOAD),
        Upload_Metadata=metadata_header({"filename": "../../nota-ação.webm"}),
        Idempotency_Key="k-1",
    )
    assert created.status_code == 201
    assert created.headers["tus-resumable"] == "1.0.0"
    location = created.headers["location"]
    assert location.startswith("/api/uploads/")
    assert "upload-expires" in created.headers

    head = await client.head(location, headers=TUS)
    assert head.status_code == 200
    assert head.headers["upload-offset"] == "0"
    assert head.headers["upload-length"] == str(len(PAYLOAD))
    assert head.headers["cache-control"] == "no-store"

    offset = 0
    while offset < len(PAYLOAD):
        chunk = PAYLOAD[offset : offset + 4000]
        answer = await _patch(client, location, offset, chunk)
        assert answer.status_code == 204, answer.text
        assert int(answer.headers["upload-offset"]) == offset + len(chunk)
        offset += len(chunk)

    assert answer.headers.get("upload-expires") is None
    assert recorder.contents == [PAYLOAD]
    assert recorder.filenames == ["nota-ação.webm"]
    final = await client.head(location, headers=TUS)
    assert final.headers["upload-offset"] == str(len(PAYLOAD))


async def test_upload_metadata_is_echoed_on_head(client: httpx.AsyncClient) -> None:
    """``HEAD`` answers the metadata re-encoded the way the client encoded it."""
    header = metadata_header({"filename": "foto.jpg", "visit_id": "v-1"})
    created = await _create(client, 10, Upload_Metadata=header)
    head = await client.head(created.headers["location"], headers=TUS)
    assert head.headers["upload-metadata"] == header


async def test_empty_upload_completes_at_creation(
    client: httpx.AsyncClient,
    recorder: CompletionRecorder,
) -> None:
    """``Upload-Length: 0`` — the client sends no ``PATCH`` — completes on ``POST``."""
    created = await _create(client, 0)
    assert created.status_code == 201
    assert "upload-expires" not in created.headers
    assert recorder.contents == [b""]


async def test_missing_tus_resumable_answers_412_with_version(
    client: httpx.AsyncClient,
) -> None:
    """Without ``Tus-Resumable`` the server names the version it speaks."""
    answer = await client.post("/api/uploads", headers={"Upload-Length": "10"})
    assert answer.status_code == 412
    assert answer.headers["tus-version"] == "1.0.0"
    assert answer.headers["tus-resumable"] == "1.0.0"
    assert answer.json()["code"] == "TUS_VERSION_UNSUPPORTED"


@pytest.mark.parametrize("value", [None, "-1", "ten", "1e3", " 10"])
async def test_bad_upload_length_answers_400(
    client: httpx.AsyncClient,
    value: str | None,
) -> None:
    """``Upload-Length`` must be digits only; deferred length is not offered."""
    headers = dict(TUS)
    if value is not None:
        headers["Upload-Length"] = value
    answer = await client.post("/api/uploads", headers=headers)
    assert answer.status_code == 400
    assert answer.json()["code"] == "TUS_HEADER_INVALID"
    assert answer.json()["details"] == {"header": "upload-length"}
    assert answer.headers["tus-resumable"] == "1.0.0"


async def test_upload_length_over_max_size_answers_413(
    client: httpx.AsyncClient,
) -> None:
    """Over ``max_size`` the creation is refused with ``Tus-Max-Size``."""
    answer = await _create(client, 10 * 1024 * 1024 + 1)
    assert answer.status_code == 413
    assert answer.headers["tus-max-size"] == str(10 * 1024 * 1024)
    assert answer.json()["code"] == "TUS_UPLOAD_TOO_LARGE"


async def test_invalid_metadata_answers_400(client: httpx.AsyncClient) -> None:
    """A value that is not base64 of UTF-8 is refused."""
    answer = await _create(client, 10, Upload_Metadata="filename !!!")
    assert answer.status_code == 400
    assert answer.json()["details"] == {"header": "Upload-Metadata"}


async def test_patch_with_wrong_content_type_answers_415(
    client: httpx.AsyncClient,
) -> None:
    """A chunk must be ``application/offset+octet-stream``."""
    location = (await _create(client, 10)).headers["location"]
    answer = await _patch(
        client,
        location,
        0,
        b"x" * 10,
        **{"Content-Type": "application/octet-stream"},
    )
    assert answer.status_code == 415
    assert answer.json()["code"] == "TUS_CONTENT_TYPE_INVALID"


async def test_patch_at_wrong_offset_answers_409_with_stored_offset(
    client: httpx.AsyncClient,
) -> None:
    """A retry of a chunk the server already holds gets ``409`` and the real offset."""
    location = (await _create(client, 10)).headers["location"]
    assert (await _patch(client, location, 0, b"abcde")).status_code == 204
    replay = await _patch(client, location, 0, b"abcde")
    assert replay.status_code == 409
    assert replay.json()["code"] == "TUS_OFFSET_MISMATCH"
    assert replay.json()["details"] == {"offset": 5}
    assert replay.headers["tus-resumable"] == "1.0.0"
    ahead = await _patch(client, location, 7, b"abc")
    assert ahead.status_code == 409


async def test_patch_past_upload_length_answers_413_and_stores_nothing(
    client: httpx.AsyncClient,
) -> None:
    """A body that would overrun ``Upload-Length`` is refused whole."""
    location = (await _create(client, 10)).headers["location"]
    answer = await _patch(client, location, 0, b"x" * 11)
    assert answer.status_code == 413
    assert answer.json()["details"] == {"max_bytes": 10}
    head = await client.head(location, headers=TUS)
    assert head.headers["upload-offset"] == "0"


async def test_patch_over_max_chunk_size_answers_413(
    client: httpx.AsyncClient,
) -> None:
    """A chunk bigger than ``max_chunk_size`` is refused."""
    location = (await _create(client, 3 * 1024 * 1024)).headers["location"]
    answer = await _patch(client, location, 0, b"x" * (1024 * 1024 + 1))
    assert answer.status_code == 413
    assert answer.json()["details"] == {"max_bytes": 1024 * 1024}


async def test_patch_body_without_content_length_is_still_capped(
    client: httpx.AsyncClient,
) -> None:
    """A streamed body with no ``Content-Length`` is cut at the limit."""
    location = (await _create(client, 10)).headers["location"]

    async def body() -> AsyncIterator[bytes]:
        """Yield 12 bytes in two pieces.

        Yields:
            bytes: The next piece.
        """
        yield b"x" * 6
        yield b"y" * 6

    answer = await client.patch(
        location,
        content=body(),
        headers={**CHUNK, "Upload-Offset": "0"},
    )
    assert "content-length" not in answer.request.headers
    assert answer.status_code == 413
    head = await client.head(location, headers=TUS)
    assert head.headers["upload-offset"] == "0"


async def test_same_idempotency_key_and_owner_returns_the_same_upload(
    client: httpx.AsyncClient,
) -> None:
    """A creation retried after a lost ``201`` gets the first upload back."""
    first = await _create(client, 10, Idempotency_Key="retry-me")
    second = await _create(client, 10, Idempotency_Key="retry-me")
    assert first.headers["location"] == second.headers["location"]


async def test_same_idempotency_key_from_another_owner_creates_another_upload(
    client: httpx.AsyncClient,
) -> None:
    """The key is scoped to the caller: another user never gets the URL back."""
    first = await _create(client, 10, Idempotency_Key="shared")
    second = await _create(client, 10, Idempotency_Key="shared", X_User="bruno")
    assert first.headers["location"] != second.headers["location"]


async def test_idempotency_key_reused_with_another_length_answers_422(
    client: httpx.AsyncClient,
) -> None:
    """Replaying a key for a different file is a client bug, not a replay."""
    await _create(client, 10, Idempotency_Key="k")
    answer = await _create(client, 11, Idempotency_Key="k")
    assert answer.status_code == 422
    assert answer.json()["code"] == "TUS_IDEMPOTENCY_KEY_REUSED"


async def test_idempotency_key_is_freed_when_its_upload_is_deleted(
    client: httpx.AsyncClient,
) -> None:
    """After a discard the same key creates a fresh upload."""
    first = (await _create(client, 10, Idempotency_Key="again")).headers["location"]
    assert (await client.delete(first, headers=TUS)).status_code == 204
    second = (await _create(client, 10, Idempotency_Key="again")).headers["location"]
    assert first != second


async def test_other_owner_gets_404_on_head_patch_and_delete(
    client: httpx.AsyncClient,
) -> None:
    """An upload is invisible to everyone but its creator."""
    location = (await _create(client, 10)).headers["location"]
    other = {"X-User": "bruno"}
    head = await client.head(location, headers={**TUS, **other})
    patch = await _patch(client, location, 0, b"x" * 10, **other)
    delete = await client.delete(location, headers={**TUS, **other})
    anonymous = await client.head(location, headers={**TUS, "X-User": ""})
    assert [head.status_code, patch.status_code, delete.status_code] == [404] * 3
    assert patch.json()["code"] == "TUS_UPLOAD_NOT_FOUND"
    assert anonymous.status_code == 404
    mine = await client.head(location, headers=TUS)
    assert mine.status_code == 200
    assert mine.headers["upload-offset"] == "0"


async def test_delete_discards_the_upload(
    client: httpx.AsyncClient,
    store: TusStore,
) -> None:
    """tus *termination*: ``DELETE`` then ``HEAD`` answers ``404``."""
    location = (await _create(client, 10)).headers["location"]
    await _patch(client, location, 0, b"abc")
    answer = await client.delete(location, headers=TUS)
    assert answer.status_code == 204
    assert answer.headers["tus-resumable"] == "1.0.0"
    assert (await client.head(location, headers=TUS)).status_code == 404
    assert await store.list_ids() == []


@pytest.mark.parametrize(
    "upload_id",
    [
        "..",
        "short",
        "a" * 15,
        "x" * 129,
        "abc.defghijklmnopq",
        "..%2F..%2Fetc%2Fpasswd",
    ],
)
async def test_malformed_ids_answer_404(
    client: httpx.AsyncClient,
    upload_id: str,
) -> None:
    """An id the router could not have generated never reaches the disk."""
    answer = await client.head(f"/api/uploads/{upload_id}", headers=TUS)
    assert answer.status_code in {404, 405}


async def test_expired_upload_answers_410_and_is_purged(
    client: httpx.AsyncClient,
    store: TusStore,
) -> None:
    """Past ``Upload-Expires`` the upload is gone, and the sweep deletes it."""
    location = (await _create(client, 10)).headers["location"]
    upload_id = location.rsplit("/", 1)[-1]
    upload = await store.load(upload_id)
    assert upload is not None
    upload.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await store.save(upload)

    head = await client.head(location, headers=TUS)
    patch = await _patch(client, location, 0, b"x")
    assert head.status_code == 410
    assert patch.status_code == 410
    assert head.headers["tus-resumable"] == "1.0.0"

    done = (await _create(client, 3)).headers["location"]
    await _patch(client, done, 0, b"abc")
    assert await store.purge_expired() == [upload_id]
    assert await store.list_ids() == [done.rsplit("/", 1)[-1]]


async def test_complete_upload_is_never_purged(
    client: httpx.AsyncClient,
    store: TusStore,
) -> None:
    """A finished upload belongs to the service, whatever its ``expires_at``."""
    location = (await _create(client, 3)).headers["location"]
    await _patch(client, location, 0, b"abc")
    far_future = datetime.now(UTC) + timedelta(days=365)
    assert await store.purge_expired(now=far_future) == []


async def test_patch_renews_upload_expires(
    client: httpx.AsyncClient,
    store: TusStore,
) -> None:
    """Every accepted chunk pushes the expiry forward."""
    location = (await _create(client, 10)).headers["location"]
    upload_id = location.rsplit("/", 1)[-1]
    upload = await store.load(upload_id)
    assert upload is not None
    upload.expires_at = datetime.now(UTC) + timedelta(seconds=5)
    await store.save(upload)
    answer = await _patch(client, location, 0, b"abc")
    renewed = await store.load(upload_id)
    assert renewed is not None and renewed.expires_at is not None
    assert renewed.expires_at > datetime.now(UTC) + timedelta(hours=23)
    assert "upload-expires" in answer.headers


async def test_failing_on_complete_leaves_the_upload_complete_and_unnotified(
    store: TusStore,
) -> None:
    """A callback that raises answers ``500``; the store keeps every byte."""

    async def explode(upload: object, store: TusStore, /) -> None:
        """Fail like a service whose database is down.

        Args:
            upload (object): The upload.
            store (TusStore): The store.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("database down")

    app = build_app(store, on_complete=explode)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"X-User": "ana"},
    ) as http:
        location = (await _create(http, 3)).headers["location"]
        answer = await _patch(http, location, 0, b"abc")
        assert answer.status_code == 500
        head = await http.head(location, headers=TUS)
        assert head.headers["upload-offset"] == "3"
    pending = [u for u in await store.list_uploads() if u.completed and not u.notified]
    assert [u.id for u in pending] == [location.rsplit("/", 1)[-1]]


async def test_anonymous_router_lets_the_url_holder_continue(
    store: TusStore,
) -> None:
    """Without ``current_user_id`` the unguessable URL is the capability."""
    app = build_app(store, authenticated=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as http:
        location = (await _create(http, 3)).headers["location"]
        assert len(location.rsplit("/", 1)[-1]) == 32
        assert (await _patch(http, location, 0, b"abc")).status_code == 204
        upload = await store.load(location.rsplit("/", 1)[-1])
        assert upload is not None and upload.owner is None


async def test_location_follows_root_path(store: TusStore) -> None:
    """Behind a proxy prefix the relative ``Location`` keeps the prefix."""
    app = build_app(store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, root_path="/svc"),
        base_url="http://testserver",
        headers={"X-User": "ana"},
    ) as http:
        created = await http.post(
            "/svc/api/uploads",
            headers={**TUS, "Upload-Length": "3"},
        )
        assert created.headers["location"].startswith("/svc/api/uploads/")
        head = await http.head(created.headers["location"], headers=TUS)
        assert head.status_code == 200


async def test_cors_constants_expose_location_and_offset(store: TusStore) -> None:
    """With the constants in ``CORSMiddleware`` a browser can read the tus headers."""
    app = build_app(store)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://app.example"],
        allow_methods=["POST", "HEAD", "PATCH", "DELETE"],
        allow_headers=list(TUS_CORS_ALLOW_HEADERS),
        expose_headers=list(TUS_CORS_EXPOSE_HEADERS),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as http:
        preflight = await http.options(
            "/api/uploads",
            headers={
                "Origin": "http://app.example",
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": (
                    "tus-resumable,upload-offset,content-type,idempotency-key,"
                    "upload-length,upload-metadata,authorization,x-csrf-token"
                ),
            },
        )
        assert preflight.status_code == 200
        created = await http.post(
            "/api/uploads",
            headers={**TUS, "Upload-Length": "3", "Origin": "http://app.example"},
        )
        exposed = created.headers["access-control-expose-headers"].lower()
        assert "location" in exposed
        assert "upload-offset" in exposed
