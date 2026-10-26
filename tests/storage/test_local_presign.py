"""Size-capped form uploads and signed download overrides on the local store.

The local backend answers the same calls as S3 (#637): a
``presigned_post_policy`` form the route holds to ``[min_bytes, max_bytes]``,
and a ``presigned_get_url`` whose ``filename``/``media_type``/``as_attachment``
are bound into the signature. What is pinned: the limits are enforced before
anything is stored, every bound parameter is tamper-evident, and a plain link
signed without them still verifies.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.storage import (
    POST_FILE_FIELD,
    LocalObjectStorageClient,
    PresignedPost,
    make_local_storage_router,
)

SECRET: str = "test-local-presign-secret"
"""Signing secret of the store under test."""

KEY: str = "evidence/photo.jpg"
"""Object key every test signs."""


def build(tmp_path: Path) -> tuple[LocalObjectStorageClient, TestClient]:
    """Build a store with a 100-byte cap and mount its router.

    Args:
        tmp_path (Path): The store's root.

    Returns:
        tuple[LocalObjectStorageClient, TestClient]: The store and the HTTP
        client.
    """
    store = LocalObjectStorageClient(
        tmp_path,
        public_url="http://testserver",
        signing_secret=SECRET,
        default_bucket="media",
        route_prefix="/api/storage",
        max_upload_bytes=100,
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(make_local_storage_router(store))
    return store, TestClient(app)


def relative(url: str) -> str:
    """Strip scheme and host from a signed URL.

    Args:
        url (str): The absolute URL.

    Returns:
        str: Path and query string.
    """
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}"


def with_query(url: str, **changes: str) -> str:
    """Rewrite query parameters of a signed URL, keeping the rest.

    Args:
        url (str): The relative signed URL.
        **changes (str): Parameters to set.

    Returns:
        str: The URL with the parameters replaced.
    """
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.update(changes)
    return f"{parts.path}?{urlencode(query)}"


def post_form(
    http: TestClient,
    post: PresignedPost,
    body: bytes,
    **field_changes: str,
) -> int:
    """Send a form upload the way a browser would.

    Args:
        http (TestClient): The client.
        post (PresignedPost): The signed form.
        body (bytes): The file.
        **field_changes (str): Fields to override, to forge a form.

    Returns:
        int: The response status.
    """
    fields = {**post.fields, **field_changes}
    response = http.post(
        relative(post.url),
        data=fields,
        files={POST_FILE_FIELD: ("photo.jpg", body, "image/jpeg")},
    )
    return response.status_code


def sign_post(store: LocalObjectStorageClient, **kwargs: object) -> PresignedPost:
    """Sign a form upload for :data:`KEY`.

    Args:
        store (LocalObjectStorageClient): The store.
        **kwargs (object): Arguments for ``presigned_post_policy``.

    Returns:
        PresignedPost: The signed form.
    """
    return asyncio.run(store.presigned_post_policy(KEY, **kwargs))  # type: ignore[arg-type]


class TestPostPolicy:
    def test_a_file_within_the_cap_is_stored(self, tmp_path: Path) -> None:
        store, http = build(tmp_path)
        post = sign_post(store, max_bytes=50, content_type="image/jpeg")

        assert post_form(http, post, b"x" * 50) == 204
        stat = asyncio.run(store.stat_object(KEY))
        assert (stat.size, stat.content_type) == (50, "image/jpeg")

    def test_one_byte_over_the_cap_is_413_and_nothing_is_stored(
        self, tmp_path: Path
    ) -> None:
        store, http = build(tmp_path)
        post = sign_post(store, max_bytes=50)

        assert post_form(http, post, b"x" * 51) == 413
        assert asyncio.run(store.list_objects()) == []

    def test_under_the_minimum_is_400(self, tmp_path: Path) -> None:
        store, http = build(tmp_path)
        post = sign_post(store, max_bytes=50, min_bytes=10)

        assert post_form(http, post, b"x" * 9) == 400
        assert asyncio.run(store.list_objects()) == []

    def test_another_content_type_field_is_refused(self, tmp_path: Path) -> None:
        store, http = build(tmp_path)
        post = sign_post(store, max_bytes=50, content_type="image/jpeg")

        assert post_form(http, post, b"x", **{"Content-Type": "text/html"}) == 403
        assert asyncio.run(store.list_objects()) == []

    def test_another_key_field_is_refused(self, tmp_path: Path) -> None:
        store, http = build(tmp_path)
        post = sign_post(store, max_bytes=50)

        assert post_form(http, post, b"x", key="evidence/other.jpg") == 403

    def test_raising_max_bytes_in_the_url_breaks_the_signature(
        self, tmp_path: Path
    ) -> None:
        store, http = build(tmp_path)
        post = sign_post(store, max_bytes=50)
        forged = PresignedPost(
            url=with_query(relative(post.url), max_bytes="100"),
            max_bytes=100,
            fields=post.fields,
        )

        assert post_form(http, forged, b"x" * 80) == 403
        assert asyncio.run(store.list_objects()) == []

    def test_dropping_the_content_type_from_the_url_breaks_the_signature(
        self, tmp_path: Path
    ) -> None:
        store, http = build(tmp_path)
        post = sign_post(store, max_bytes=50, content_type="image/jpeg")
        parts = urlsplit(relative(post.url))
        query = [(k, v) for k, v in parse_qsl(parts.query) if k != "content_type"]
        forged = PresignedPost(
            url=f"{parts.path}?{urlencode(query)}",
            max_bytes=50,
            fields={**post.fields, "Content-Type": "text/html"},
        )

        assert post_form(http, forged, b"x") == 403

    def test_a_put_link_does_not_post(self, tmp_path: Path) -> None:
        store, http = build(tmp_path)
        put = asyncio.run(store.presigned_put_url(KEY))
        response = http.post(
            relative(put),
            files={POST_FILE_FIELD: ("a.jpg", b"x", "image/jpeg")},
        )

        assert response.status_code == 403

    def test_max_bytes_above_the_store_cap_is_refused_when_signing(
        self, tmp_path: Path
    ) -> None:
        store, _http = build(tmp_path)

        with pytest.raises(ValueError, match="max_upload_bytes"):
            sign_post(store, max_bytes=101)

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"max_bytes": 0}, "max_bytes"),
            ({"max_bytes": 10, "min_bytes": 11}, "min_bytes"),
            ({"max_bytes": 10, "content_type": "image/jpeg; x=y"}, "content_type"),
        ],
    )
    def test_invalid_limits_are_refused_when_signing(
        self, tmp_path: Path, kwargs: dict[str, object], message: str
    ) -> None:
        store, _http = build(tmp_path)

        with pytest.raises(ValueError, match=message):
            sign_post(store, **kwargs)


class TestDownloadOverrides:
    def test_the_link_answers_with_the_signed_type_and_disposition(
        self, tmp_path: Path
    ) -> None:
        store, http = build(tmp_path)
        asyncio.run(store.put_object(KEY, b"data", content_type="text/html"))
        url = asyncio.run(
            store.presigned_get_url(
                KEY,
                filename="evidência.jpg",
                media_type="image/jpeg",
                as_attachment=False,
            )
        )

        response = http.get(relative(url))

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["content-disposition"].startswith("inline;")
        assert "evid%C3%AAncia.jpg" in response.headers["content-disposition"]

    @pytest.mark.parametrize(
        "change",
        [
            {"media_type": "text/html"},
            {"disposition": "attachment"},
            {"filename": "other.exe"},
        ],
    )
    def test_editing_a_bound_override_breaks_the_signature(
        self, tmp_path: Path, change: dict[str, str]
    ) -> None:
        store, http = build(tmp_path)
        asyncio.run(store.put_object(KEY, b"data", content_type="image/jpeg"))
        url = asyncio.run(
            store.presigned_get_url(
                KEY, filename="a.jpg", media_type="image/jpeg", as_attachment=False
            )
        )

        assert http.get(with_query(relative(url), **change)).status_code == 403

    def test_adding_an_override_to_a_plain_link_breaks_it(self, tmp_path: Path) -> None:
        store, http = build(tmp_path)
        asyncio.run(store.put_object(KEY, b"data", content_type="image/jpeg"))
        url = asyncio.run(store.presigned_get_url(KEY))

        forged = with_query(relative(url), media_type="text/html")

        assert http.get(forged).status_code == 403

    def test_a_plain_link_keeps_the_stored_type_and_router_default(
        self, tmp_path: Path
    ) -> None:
        store, http = build(tmp_path)
        asyncio.run(store.put_object(KEY, b"data", content_type="image/jpeg"))
        url = asyncio.run(store.presigned_get_url(KEY))

        response = http.get(relative(url))

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["content-disposition"].startswith("inline;")

    def test_a_media_type_with_a_line_break_is_refused(self, tmp_path: Path) -> None:
        store, _http = build(tmp_path)

        with pytest.raises(ValueError, match="media_type"):
            asyncio.run(
                store.presigned_get_url(KEY, media_type="image/jpeg\r\nX-Evil: 1")
            )
