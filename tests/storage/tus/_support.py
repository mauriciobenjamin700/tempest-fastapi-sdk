"""Shared builders for the tus router tests."""

from __future__ import annotations

import base64

from fastapi import FastAPI, Header

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.storage.tus import (
    TUS_VERSION,
    TusCompleteCallback,
    TusStore,
    TusUpload,
    make_tus_router,
)

TUS: dict[str, str] = {"Tus-Resumable": TUS_VERSION}
"""The header every tus request carries."""

CHUNK: dict[str, str] = {**TUS, "Content-Type": "application/offset+octet-stream"}
"""Headers of a ``PATCH``, without the offset."""


async def caller(x_user: str | None = Header(default=None)) -> str | None:
    """Resolve the caller from ``X-User``, standing in for a real auth dependency.

    Args:
        x_user (str | None): The header.

    Returns:
        str | None: The user id, or ``None``.
    """
    return x_user


def build_app(
    store: TusStore,
    *,
    on_complete: TusCompleteCallback | None = None,
    authenticated: bool = True,
    max_size: int = 10 * 1024 * 1024,
    max_chunk_size: int = 1024 * 1024,
) -> FastAPI:
    """Build an app with the tus router mounted at ``/api/uploads``.

    Args:
        store (TusStore): The store.
        on_complete (TusCompleteCallback | None): Completion callback.
        authenticated (bool): Wire ``caller`` as ``current_user_id``.
        max_size (int): The router's ``max_size``.
        max_chunk_size (int): The router's ``max_chunk_size``.

    Returns:
        FastAPI: The app.
    """
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_tus_router(
            store,
            current_user_id=caller if authenticated else None,
            on_complete=on_complete,
            max_size=max_size,
            max_chunk_size=max_chunk_size,
        ),
    )
    return app


def metadata_header(pairs: dict[str, str]) -> str:
    """Encode metadata the way ``tempest-react-sdk`` does: UTF-8, then base64.

    Args:
        pairs (dict[str, str]): Plain pairs.

    Returns:
        str: The ``Upload-Metadata`` value.
    """
    return ",".join(
        f"{key} {base64.b64encode(value.encode('utf-8')).decode('ascii')}"
        for key, value in pairs.items()
    )


class CompletionRecorder:
    """An ``on_complete`` that records each upload and reads its bytes."""

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.ids: list[str] = []
        self.contents: list[bytes] = []
        self.filenames: list[str | None] = []

    async def __call__(self, upload: TusUpload, store: TusStore, /) -> None:
        """Record the upload and the bytes the store streams back.

        Args:
            upload (TusUpload): The finished upload.
            store (TusStore): The store.
        """
        self.ids.append(upload.id)
        self.filenames.append(upload.filename)
        self.contents.append(
            b"".join([block async for block in store.stream(upload.id)])
        )
