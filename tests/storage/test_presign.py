"""Size-capped uploads and signed download overrides over S3 (#637).

Signing a POST policy and a presigned GET is local to the client — no
request reaches the store — so the conditions S3 will enforce are pinned
here by decoding what was signed. ``test_presign_live.py`` measures the
store refusing them.
"""

from __future__ import annotations

import base64
import json
import warnings
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from tempest_fastapi_sdk import AsyncMinIOClient
from tempest_fastapi_sdk.privacy import SubjectObjectStorage
from tempest_fastapi_sdk.storage.presign import (
    check_post_limits,
    download_overrides,
)
from tempest_fastapi_sdk.testing.fakes import FakeObjectStorageClient


def minio(**kwargs: Any) -> AsyncMinIOClient:
    """Build a client against an endpoint nothing listens on.

    Args:
        **kwargs (Any): Overrides for the constructor.

    Returns:
        AsyncMinIOClient: The client.
    """
    options: dict[str, Any] = {
        "endpoint": "127.0.0.1:9",
        "access_key": "access",
        "secret_key": "secret-key-1234",
        "secure": False,
        "default_bucket": "media",
    }
    options.update(kwargs)
    return AsyncMinIOClient(**options)


def conditions(policy: str) -> list[Any]:
    """Decode the conditions of a signed POST policy.

    Args:
        policy (str): The base64 ``policy`` form field.

    Returns:
        list[Any]: The policy's ``conditions``.
    """
    decoded: dict[str, Any] = json.loads(base64.b64decode(policy))
    return list(decoded["conditions"])


class TestDownloadOverrides:
    def test_nothing_asked_signs_nothing(self) -> None:
        assert (
            download_overrides(
                "a/b.jpg", filename=None, media_type=None, as_attachment=None
            )
            == {}
        )

    def test_media_type_alone_signs_only_the_type(self) -> None:
        assert download_overrides(
            "a/b.jpg", filename=None, media_type="image/jpeg", as_attachment=None
        ) == {"response-content-type": "image/jpeg"}

    def test_filename_defaults_to_attachment(self) -> None:
        overrides = download_overrides(
            "a/b.jpg", filename="c.jpg", media_type=None, as_attachment=None
        )
        assert overrides["response-content-disposition"].startswith("attachment;")

    def test_inline_only_for_an_inline_safe_type(self) -> None:
        safe = download_overrides(
            "a/b.jpg", filename=None, media_type="image/jpeg", as_attachment=False
        )
        unsafe = download_overrides(
            "a/b.html", filename=None, media_type="text/html", as_attachment=False
        )
        assert safe["response-content-disposition"].startswith(
            'inline; filename="b.jpg"'
        )
        assert unsafe["response-content-disposition"].startswith("attachment;")

    @pytest.mark.parametrize(
        "media_type", ["image/jpeg\r\nX-Evil: 1", "image/jpeg; charset=x", "jpeg", ""]
    )
    def test_anything_but_a_bare_type_is_refused(self, media_type: str) -> None:
        with pytest.raises(ValueError, match="media_type"):
            download_overrides(
                "a", filename=None, media_type=media_type, as_attachment=None
            )


class TestPostLimits:
    @pytest.mark.parametrize(("min_bytes", "max_bytes"), [(-1, 10), (0, 0), (11, 10)])
    def test_empty_or_inverted_ranges_are_refused(
        self, min_bytes: int, max_bytes: int
    ) -> None:
        with pytest.raises(ValueError):
            check_post_limits(min_bytes=min_bytes, max_bytes=max_bytes)


class TestMinIOPostPolicy:
    async def test_the_policy_signs_the_key_type_and_size_range(self) -> None:
        post = await minio().presigned_post_policy(
            "ev/photo.jpg", max_bytes=1000, min_bytes=1, content_type="image/jpeg"
        )

        signed = conditions(post.fields["policy"])
        assert ["eq", "$bucket", "media"] in signed
        assert ["eq", "$key", "ev/photo.jpg"] in signed
        assert ["eq", "$Content-Type", "image/jpeg"] in signed
        assert ["content-length-range", 1, 1000] in signed
        assert post.fields["key"] == "ev/photo.jpg"
        assert post.fields["Content-Type"] == "image/jpeg"
        assert post.url == "http://127.0.0.1:9/media"
        assert post.max_bytes == 1000

    async def test_signing_survives_warnings_as_errors(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            post = await minio().presigned_post_policy("k", max_bytes=10)

        assert post.fields["policy"]

    async def test_without_a_type_the_type_is_free(self) -> None:
        post = await minio().presigned_post_policy("k", max_bytes=10)

        assert "Content-Type" not in post.fields
        assert not any("$Content-Type" in c for c in conditions(post.fields["policy"]))

    async def test_the_url_is_on_the_public_endpoint(self) -> None:
        client = minio(public_endpoint="https://cdn.example.com")

        post = await client.presigned_post_policy("k", max_bytes=10)

        assert post.url == "https://cdn.example.com/media"

    async def test_a_parameterized_content_type_is_refused(self) -> None:
        with pytest.raises(ValueError, match="content_type"):
            await minio().presigned_post_policy(
                "k", max_bytes=10, content_type="image/jpeg; x=y"
            )


class TestMinIOGetOverrides:
    async def test_overrides_are_signed_query_parameters(self) -> None:
        url = await minio().presigned_get_url(
            "ev/photo.jpg", media_type="image/jpeg", as_attachment=False
        )

        query = parse_qs(urlsplit(url).query)
        assert query["response-content-type"] == ["image/jpeg"]
        assert query["response-content-disposition"][0].startswith("inline;")
        assert "X-Amz-SignedHeaders" in query

    async def test_a_plain_link_carries_no_override(self) -> None:
        url = await minio().presigned_get_url("ev/photo.jpg")

        assert "response-" not in urlsplit(url).query


class TestSubjectStorage:
    async def test_presign_post_is_scoped_to_the_subject(self) -> None:
        client = FakeObjectStorageClient()
        storage = SubjectObjectStorage(client, prefix="teens", bucket="ev")

        post = await storage.presign_post(
            42, "photo.jpg", max_bytes=5_000_000, content_type="image/jpeg"
        )

        assert post.fields["key"] == "teens/42/photo.jpg"
        assert post.fields["Content-Type"] == "image/jpeg"
        assert post.fields["x-fake-content-length-range"] == "0,5000000"
        assert post.url == "https://fake-storage/ev"

    async def test_presign_forwards_the_overrides(self) -> None:
        client = FakeObjectStorageClient()
        storage = SubjectObjectStorage(client, prefix="teens")

        url = await storage.presign(42, "photo.jpg", media_type="image/jpeg")

        assert parse_qs(urlsplit(url).query)["response-content-type"] == ["image/jpeg"]
