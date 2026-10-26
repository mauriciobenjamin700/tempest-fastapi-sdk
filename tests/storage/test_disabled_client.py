"""``AsyncMinIOClient(enabled=False)`` — the storage half of the kill-switch.

``STORAGE_ENABLED=false`` makes ``storage_kwargs()`` carry
``enabled=False``. The client built from it must still construct (the app
boots) and must refuse **every** operation with the same ``503``, because
the failure the switch replaces is a connection error against
``localhost:9000`` surfacing as a ``500`` somewhere deep in a request.
"""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    IntegrationDisabledException,
    PutObjectItem,
)
from tempest_fastapi_sdk.privacy import SubjectObjectStorage

Operation = Callable[[AsyncMinIOClient], Awaitable[Any]]
"""One public call against the client under test."""


async def call_stream(client: AsyncMinIOClient) -> Any:
    """Open a stream, which is where the object is first requested.

    Args:
        client (AsyncMinIOClient): The disabled client.

    Returns:
        Any: Never returns; the call raises.
    """
    return await client.stream_object("a.txt")


OPERATIONS: dict[str, Operation] = {
    "bucket_exists": lambda c: c.bucket_exists(),
    "ensure_bucket": lambda c: c.ensure_bucket(),
    "list_buckets": lambda c: c.list_buckets(),
    "remove_bucket": lambda c: c.remove_bucket(),
    "put_object": lambda c: c.put_object("a.txt", b"x"),
    "fput_object": lambda c: c.fput_object("a.txt", Path("missing.txt")),
    "get_object_bytes": lambda c: c.get_object_bytes("a.txt"),
    "fget_object": lambda c: c.fget_object("a.txt", Path("out.txt")),
    "stream_object": call_stream,
    "download_response": lambda c: c.download_response("a.txt"),
    "accel_redirect_response": lambda c: c.accel_redirect_response("a.txt"),
    "serve_object": lambda c: c.serve_object("a.txt"),
    "stat_object": lambda c: c.stat_object("a.txt"),
    "list_objects": lambda c: c.list_objects("p/"),
    "remove_object": lambda c: c.remove_object("a.txt"),
    "remove_objects": lambda c: c.remove_objects(["a.txt"]),
    "copy_object": lambda c: c.copy_object("a.txt", "b.txt"),
    "presigned_get_url": lambda c: c.presigned_get_url("a.txt"),
    "presigned_put_url": lambda c: c.presigned_put_url("a.txt"),
    "presigned_post_policy": lambda c: c.presigned_post_policy("a.txt", max_bytes=10),
    "presigned_get_urls": lambda c: c.presigned_get_urls(
        ["a.txt"], expires=timedelta(minutes=1)
    ),
    "put_objects": lambda c: c.put_objects([PutObjectItem(key="a.txt", data=b"x")]),
    "get_objects_bytes": lambda c: c.get_objects_bytes(["a.txt"]),
}
"""Every public operation of ``AsyncMinIOClient``."""


def disabled_client() -> AsyncMinIOClient:
    """Build the client the way ``storage_kwargs()`` does with the flag off.

    Returns:
        AsyncMinIOClient: A client built with ``enabled=False``.
    """
    return AsyncMinIOClient(
        endpoint="localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        enabled=False,
    )


def test_operations_cover_the_public_surface() -> None:
    """A new public coroutine must join ``OPERATIONS`` (or this fails)."""
    public = {
        name
        for name in vars(AsyncMinIOClient)
        if not name.startswith("_") and callable(getattr(AsyncMinIOClient, name))
    }
    assert public == set(OPERATIONS)


@pytest.mark.parametrize("name", sorted(OPERATIONS))
async def test_every_operation_refuses_with_503(name: str) -> None:
    client = disabled_client()
    with pytest.raises(IntegrationDisabledException) as error:
        await OPERATIONS[name](client)
    assert error.value.status_code == 503
    assert error.value.code == "INTEGRATION_DISABLED"
    assert error.value.details == {"integration": "storage"}


def test_the_raw_client_attribute_refuses_too() -> None:
    """Dropping down to ``client`` is not a way around the switch."""
    with pytest.raises(IntegrationDisabledException):
        _ = disabled_client().client


def test_builds_without_the_minio_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment with storage off need not install ``[minio]``."""
    monkeypatch.setitem(sys.modules, "minio", None)
    client = disabled_client()
    assert client.enabled is False
    with pytest.raises(ImportError):
        AsyncMinIOClient(endpoint="h", access_key="a", secret_key="s")


def test_enabled_client_keeps_its_minio() -> None:
    client = AsyncMinIOClient(endpoint="h:9000", access_key="a", secret_key="s")
    assert client.enabled is True
    assert client.client is client._presign_client


async def test_subject_storage_inherits_the_refusal() -> None:
    """``SubjectObjectStorage`` over a disabled client refuses the same way."""
    storage = SubjectObjectStorage(disabled_client(), prefix="teens")
    with pytest.raises(IntegrationDisabledException):
        await storage.put("42", "a.txt", b"x")
    with pytest.raises(IntegrationDisabledException):
        await storage.delete_all("42")
