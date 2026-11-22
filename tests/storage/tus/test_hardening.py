"""Defects a security review found in the first tus router, one test each.

Each test fails on the router as first committed (``1199b656``) and passes
after the fix: a ``PATCH`` to a complete upload re-ran ``on_complete``, an
empty ``PATCH`` renewed the expiry, a record deleted by the callback came
back, an S3 assembly that failed half-way left the upload stuck, a long
integer header answered ``500``, and there was no quota, no chunk minimum,
no type check on the owner, a shared anonymous idempotency scope, a sweep
without the lock and a ``401`` without ``Tus-Resumable``.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.storage.tus import (
    LocalTusStore,
    S3TusStore,
    TusStore,
    TusUpload,
    make_tus_router,
)
from tempest_fastapi_sdk.testing.fakes import FakeObjectStorageClient
from tests.storage.tus._support import CHUNK, TUS, CompletionRecorder, build_app

RECIPE: Path = Path(__file__).resolve().parents[3] / "docs/recipes/resumable-upload.md"


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
    return S3TusStore(FakeObjectStorageClient(), min_chunk_size=0)


def _client(app: FastAPI, *, raise_errors: bool = True) -> httpx.AsyncClient:
    """Build an HTTP client acting as user ``ana``.

    Args:
        app (FastAPI): The app.
        raise_errors (bool): Let app exceptions propagate.

    Returns:
        httpx.AsyncClient: The client.
    """
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=raise_errors),
        base_url="http://testserver",
        headers={"X-User": "ana"},
    )


async def _create(http: httpx.AsyncClient, length: int, **headers: str) -> str:
    """Create an upload and return its URL.

    Args:
        http (httpx.AsyncClient): The client.
        length (int): ``Upload-Length``.
        **headers (str): Extra headers.

    Returns:
        str: The ``Location``.
    """
    answer = await http.post(
        "/api/uploads",
        headers={**TUS, "Upload-Length": str(length), **headers},
    )
    assert answer.status_code == 201, answer.text
    return answer.headers["location"]


async def _patch(
    http: httpx.AsyncClient,
    url: str,
    offset: int,
    body: bytes,
) -> httpx.Response:
    """Send one chunk.

    Args:
        http (httpx.AsyncClient): The client.
        url (str): Upload URL.
        offset (int): ``Upload-Offset``.
        body (bytes): The chunk.

    Returns:
        httpx.Response: The answer.
    """
    return await http.patch(
        url,
        content=body,
        headers={**CHUNK, "Upload-Offset": str(offset)},
    )


async def test_patch_on_complete_upload_is_refused_and_on_complete_runs_once(
    store: TusStore,
) -> None:
    """Empty ``PATCH`` at the length answers ``409``; the callback ran once."""
    recorder = CompletionRecorder()
    async with _client(build_app(store, on_complete=recorder)) as http:
        url = await _create(http, 3)
        assert (await _patch(http, url, 0, b"abc")).status_code == 204
        answers = [await _patch(http, url, 3, b"") for _ in range(3)]
    assert [a.status_code for a in answers] == [409, 409, 409]
    assert {a.json()["code"] for a in answers} == {"TUS_UPLOAD_COMPLETED"}
    assert answers[0].json()["details"] == {"offset": 3}
    assert len(recorder.ids) == 1
    upload = await store.load(url.rsplit("/", 1)[-1])
    assert upload is not None and upload.notified is True


class _SlowFinalize(LocalTusStore):
    """A local store whose ``finalize`` waits for a release signal."""

    def __init__(self, root: Path) -> None:
        """Initialize the gates.

        Args:
            root (Path): Store directory.
        """
        super().__init__(root)
        self.inside: asyncio.Event = asyncio.Event()
        self.release: asyncio.Event = asyncio.Event()

    async def finalize(self, upload: TusUpload) -> None:
        """Hold the finalization until released.

        Args:
            upload (TusUpload): The upload.
        """
        self.inside.set()
        await self.release.wait()


async def test_two_final_patches_run_on_complete_once(tmp_path: Path) -> None:
    """A second final ``PATCH`` during the first's completion never re-completes."""
    store = _SlowFinalize(tmp_path)
    recorder = CompletionRecorder()
    async with _client(build_app(store, on_complete=recorder)) as http:
        url = await _create(http, 3)
        first = asyncio.create_task(_patch(http, url, 0, b"abc"))
        await asyncio.wait_for(store.inside.wait(), timeout=5)
        during = await _patch(http, url, 0, b"abc")
        store.release.set()
        assert (await asyncio.wait_for(first, timeout=5)).status_code == 204
        after = await _patch(http, url, 3, b"")
    assert during.status_code == 409
    assert during.json()["code"] == "TUS_UPLOAD_LOCKED"
    assert after.json()["code"] == "TUS_UPLOAD_COMPLETED"
    assert len(recorder.ids) == 1


async def test_empty_patch_does_not_renew_the_expiry(store: TusStore) -> None:
    """A ``PATCH`` that stores nothing is not a keep-alive."""
    async with _client(build_app(store)) as http:
        url = await _create(http, 10)
        upload_id = url.rsplit("/", 1)[-1]
        upload = await store.load(upload_id)
        assert upload is not None
        soon = datetime.now(UTC) + timedelta(seconds=30)
        upload.expires_at = soon
        await store.save(upload)
        answer = await _patch(http, url, 0, b"")
    assert answer.status_code == 204
    assert answer.headers["upload-offset"] == "0"
    reloaded = await store.load(upload_id)
    assert reloaded is not None and reloaded.expires_at == soon


def _recipe_block(marker: str) -> str:
    """Return the first ``python`` block of the recipe containing ``marker``.

    Args:
        marker (str): Text the block must contain.

    Returns:
        str: The block's source.
    """
    blocks = re.findall(r"```python\n(.*?)```", RECIPE.read_text("utf-8"), re.S)
    return str(next(block for block in blocks if marker in block))


async def test_recipe_archive_photo_leaves_no_zombie_record(tmp_path: Path) -> None:
    """The recipe's ``on_complete`` moves and deletes; the record stays deleted."""
    namespace: dict[str, Any] = {}
    exec(_recipe_block("async def archive_photo"), namespace)
    archive = tmp_path / "photos"
    archive.mkdir()
    namespace["ARCHIVE"] = archive
    store = LocalTusStore(tmp_path / "tus")
    async with _client(
        build_app(store, on_complete=namespace["archive_photo"])
    ) as http:
        url = await _create(
            http,
            3,
            **{"Upload-Metadata": "filename Zm90by5qcGc="},
        )
        final = await _patch(http, url, 0, b"abc")
        head = await http.head(url, headers=TUS)
    upload_id = url.rsplit("/", 1)[-1]
    assert final.status_code == 204
    assert head.status_code == 404
    assert await store.list_ids() == []
    assert (archive / f"{upload_id}-foto.jpg").read_bytes() == b"abc"


class _FlakyClient(FakeObjectStorageClient):
    """A fake bucket that fails chosen calls once."""

    def __init__(self) -> None:
        """Start with no failure armed."""
        super().__init__()
        self.fail_remove: bool = False
        self.fail_put_suffix: str | None = None

    async def remove_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
        version_id: str | None = None,
    ) -> None:
        """Fail every delete while armed.

        Args:
            key (str): Object key.
            bucket (str | None): Bucket.
            version_id (str | None): Version.

        Raises:
            ConnectionError: While ``fail_remove`` is set.
        """
        if self.fail_remove:
            raise ConnectionError("bucket unreachable")
        await super().remove_object(key, bucket=bucket, version_id=version_id)

    async def put_object(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        bucket: str | None = None,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
        length: int | None = None,
    ) -> str:
        """Fail once on a key ending with ``fail_put_suffix``.

        Args:
            key (str): Object key.
            data (bytes | BinaryIO): Payload.
            bucket (str | None): Bucket.
            content_type (str): MIME type.
            metadata (dict[str, str] | None): Metadata.
            length (int | None): Size.

        Returns:
            str: The ETag.

        Raises:
            ConnectionError: On the armed key, once.
        """
        if self.fail_put_suffix is not None and key.endswith(self.fail_put_suffix):
            self.fail_put_suffix = None
            raise ConnectionError("bucket unreachable")
        return await super().put_object(
            key,
            data,
            bucket=bucket,
            content_type=content_type,
            metadata=metadata,
            length=length,
        )


async def test_s3_failed_chunk_cleanup_does_not_strand_the_upload() -> None:
    """Deleting the chunks fails; the upload is still complete and readable."""
    client = _FlakyClient()
    store = S3TusStore(client, min_chunk_size=0)
    recorder = CompletionRecorder()
    async with _client(build_app(store, on_complete=recorder)) as http:
        url = await _create(http, 6)
        assert (await _patch(http, url, 0, b"abc")).status_code == 204
        client.fail_remove = True
        final = await _patch(http, url, 3, b"def")
        head = await http.head(url, headers=TUS)
    client.fail_remove = False
    assert final.status_code == 204
    assert head.headers["upload-offset"] == "6"
    assert recorder.contents == [b"abcdef"]
    upload_id = url.rsplit("/", 1)[-1]
    await store.delete(upload_id)
    assert await client.list_objects(f"tus/{upload_id}/") == []


async def test_s3_failed_assembly_is_retried_by_the_last_patch() -> None:
    """The ``data`` write fails once; the client's retry completes the upload."""
    client = _FlakyClient()
    store = S3TusStore(client, min_chunk_size=0)
    recorder = CompletionRecorder()
    async with _client(
        build_app(store, on_complete=recorder), raise_errors=False
    ) as http:
        url = await _create(http, 6)
        assert (await _patch(http, url, 0, b"abc")).status_code == 204
        client.fail_put_suffix = "/data"
        failed = await _patch(http, url, 3, b"def")
        offset = (await http.head(url, headers=TUS)).headers["upload-offset"]
        retried = await _patch(http, url, int(offset), b"def")
    assert failed.status_code == 500
    assert offset == "3"
    assert retried.status_code == 204
    assert recorder.contents == [b"abcdef"]


@pytest.mark.parametrize("header", ["Upload-Length", "Upload-Offset"])
async def test_huge_integer_headers_answer_400_not_500(
    store: TusStore,
    header: str,
) -> None:
    """Past twenty digits the header is malformed, before ``int()`` can fail."""
    async with _client(build_app(store), raise_errors=False) as http:
        if header == "Upload-Length":
            answer = await http.post(
                "/api/uploads",
                headers={**TUS, "Upload-Length": "9" * 5000},
            )
        else:
            url = await _create(http, 3)
            answer = await http.patch(
                url,
                content=b"a",
                headers={**CHUNK, "Upload-Offset": "9" * 5000},
            )
    assert answer.status_code == 400
    assert answer.json()["code"] == "TUS_HEADER_INVALID"
    assert answer.headers["tus-resumable"] == "1.0.0"


def _quota_app(
    store: TusStore,
    *,
    max_uploads_per_owner: int | None = None,
    max_bytes_per_owner: int | None = None,
) -> FastAPI:
    """Build an app with quota limits.

    Args:
        store (TusStore): The store.
        max_uploads_per_owner (int | None): Upload-count limit.
        max_bytes_per_owner (int | None): Declared-bytes limit.

    Returns:
        FastAPI: The app.
    """

    async def caller(x_user: str | None = Header(default=None)) -> str | None:
        """Resolve the caller from ``X-User``.

        Args:
            x_user (str | None): The header.

        Returns:
            str | None: The user id.
        """
        return x_user

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_tus_router(
            store,
            current_user_id=caller,
            max_uploads_per_owner=max_uploads_per_owner,
            max_bytes_per_owner=max_bytes_per_owner,
        ),
    )
    return app


async def test_max_uploads_per_owner(store: TusStore) -> None:
    """A third unfinished upload is refused; others and finished ones do not count."""
    async with _client(_quota_app(store, max_uploads_per_owner=2)) as http:
        first = await _create(http, 3)
        await _create(http, 3)
        refused = await http.post(
            "/api/uploads",
            headers={**TUS, "Upload-Length": "3"},
        )
        other = await http.post(
            "/api/uploads",
            headers={**TUS, "Upload-Length": "3", "X-User": "bruno"},
        )
        await _patch(http, first, 0, b"abc")
        after_finishing = await http.post(
            "/api/uploads",
            headers={**TUS, "Upload-Length": "3"},
        )
    assert refused.status_code == 403
    assert refused.json()["code"] == "TUS_QUOTA_EXCEEDED"
    assert refused.json()["details"] == {"limit": "uploads", "max": 2}
    assert other.status_code == 201
    assert after_finishing.status_code == 201


async def test_max_bytes_per_owner(store: TusStore) -> None:
    """Declared bytes of unfinished uploads add up against the limit."""
    async with _client(_quota_app(store, max_bytes_per_owner=10)) as http:
        await _create(http, 6)
        refused = await http.post(
            "/api/uploads",
            headers={**TUS, "Upload-Length": "5"},
        )
        fits = await http.post("/api/uploads", headers={**TUS, "Upload-Length": "4"})
    assert refused.status_code == 403
    assert refused.json()["details"] == {"limit": "bytes", "max": 10}
    assert fits.status_code == 201


async def test_s3_min_chunk_size_refuses_tiny_non_final_chunks() -> None:
    """Below the minimum only the chunk that ends the upload is accepted."""
    store = S3TusStore(FakeObjectStorageClient(), min_chunk_size=4)
    async with _client(build_app(store)) as http:
        url = await _create(http, 6)
        tiny = await _patch(http, url, 0, b"ab")
        ok = await _patch(http, url, 0, b"abcd")
        last = await _patch(http, url, 4, b"ef")
    assert tiny.status_code == 400
    assert tiny.json()["code"] == "TUS_CHUNK_TOO_SMALL"
    assert tiny.json()["details"] == {"min_bytes": 4}
    assert ok.status_code == 204
    assert last.status_code == 204


def test_s3_store_has_a_chunk_minimum_by_default() -> None:
    """Out of the box an S3 upload cannot become one object per byte."""
    assert S3TusStore(FakeObjectStorageClient()).min_chunk_size == 64 * 1024


def _owner_app(store: TusStore, value: object) -> FastAPI:
    """Build an app whose ``current_user_id`` resolves ``value``.

    Args:
        store (TusStore): The store.
        value (object): What the dependency returns.

    Returns:
        FastAPI: The app.
    """

    async def caller() -> object:
        """Return the configured value.

        Returns:
            object: ``value``.
        """
        return value

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(make_tus_router(store, current_user_id=caller))
    return app


@pytest.mark.parametrize("value", [{"sub": "ana"}, object(), True, 1.5])
async def test_current_user_id_must_be_a_stable_id(
    store: TusStore,
    value: object,
) -> None:
    """A user model or claims dict is refused instead of ``str()``-ed."""
    async with _client(_owner_app(store, value)) as http:
        with pytest.raises(TypeError, match="current_user_id"):
            await http.post("/api/uploads", headers={**TUS, "Upload-Length": "3"})


@pytest.mark.parametrize("value", ["ana", 42])
async def test_current_user_id_accepts_str_and_int(
    store: TusStore,
    value: object,
) -> None:
    """``str`` and ``int`` ids are stored as their ``str()``."""
    async with _client(_owner_app(store, value)) as http:
        url = await _create(http, 3)
    upload = await store.load(url.rsplit("/", 1)[-1])
    assert upload is not None and upload.owner == str(value)


async def test_anonymous_router_ignores_idempotency_key(store: TusStore) -> None:
    """Without an owner the key would be one scope shared by every caller."""
    async with _client(build_app(store, authenticated=False)) as http:
        first = await _create(http, 3, **{"Idempotency-Key": "guessable"})
        second = await _create(http, 3, **{"Idempotency-Key": "guessable"})
    assert first != second


async def test_purge_skips_an_upload_whose_lock_is_held(store: TusStore) -> None:
    """The sweep never deletes under a request that holds the upload."""
    upload = TusUpload(
        id="p" * 32,
        length=10,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    await store.create(upload)
    async with store._upload_locks().hold(upload.id):
        assert await store.purge_expired() == []
    assert await store.purge_expired() == [upload.id]


async def test_dependency_refusal_carries_tus_resumable(store: TusStore) -> None:
    """A ``401`` from ``current_user_id`` still names the protocol version."""

    async def refuse() -> str:
        """Refuse every caller.

        Raises:
            HTTPException: Always ``401``.
        """
        raise HTTPException(status_code=401, detail="no token")

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(make_tus_router(store, current_user_id=refuse))
    async with _client(app) as http:
        answer = await http.post(
            "/api/uploads",
            headers={**TUS, "Upload-Length": "3"},
        )
    assert answer.status_code == 401
    assert answer.headers["tus-resumable"] == "1.0.0"


async def test_second_patch_is_refused_before_its_body_is_read(
    tmp_path: Path,
) -> None:
    """A concurrent ``PATCH`` answers ``409`` without spooling its body."""
    store = _SlowWrite(tmp_path)
    async with _client(build_app(store)) as http:
        url = await _create(http, 10)
        first = asyncio.create_task(_patch(http, url, 0, b"abcde"))
        await asyncio.wait_for(store.inside.wait(), timeout=5)
        read: list[bool] = []

        async def body() -> AsyncIterator[bytes]:
            """Record that the body was pulled.

            Yields:
                bytes: The chunk.
            """
            read.append(True)
            yield b"abcde"

        second = await http.patch(
            url,
            content=body(),
            headers={**CHUNK, "Upload-Offset": "0"},
        )
        store.release.set()
        await asyncio.wait_for(first, timeout=5)
    assert second.status_code == 409
    assert second.json()["code"] == "TUS_UPLOAD_LOCKED"
    assert read == []


class _SlowWrite(LocalTusStore):
    """A local store whose ``write`` waits for a release signal."""

    def __init__(self, root: Path) -> None:
        """Initialize the gates.

        Args:
            root (Path): Store directory.
        """
        super().__init__(root)
        self.inside: asyncio.Event = asyncio.Event()
        self.release: asyncio.Event = asyncio.Event()

    async def write(self, upload: TusUpload, data: BinaryIO, size: int) -> None:
        """Hold the write until released.

        Args:
            upload (TusUpload): The upload.
            data (BinaryIO): The chunk.
            size (int): Its size.
        """
        self.inside.set()
        await self.release.wait()
        await super().write(upload, data, size)
