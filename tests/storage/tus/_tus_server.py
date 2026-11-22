"""A tus server process for the restart tests: ``python _tus_server.py <port> <store>``.

``<store>`` is ``local:<directory>`` or ``minio:<host:port>:<bucket>``. Each
start builds a new store and a new router from nothing but that argument, so
whatever the next process finds came from disk or from the bucket.
"""

from __future__ import annotations

import sys

import uvicorn
from fastapi import FastAPI, Header

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.storage.tus import (
    LocalTusStore,
    S3TusStore,
    TusStore,
    make_tus_router,
)


async def caller(x_user: str | None = Header(default=None)) -> str | None:
    """Resolve the caller from ``X-User``.

    Args:
        x_user (str | None): The header.

    Returns:
        str | None: The user id.
    """
    return x_user


def build_store(spec: str) -> TusStore:
    """Build the store an argument names.

    Args:
        spec (str): ``local:<dir>`` or ``minio:<host:port>:<bucket>``.

    Returns:
        TusStore: The store.
    """
    kind, _, rest = spec.partition(":")
    if kind == "local":
        return LocalTusStore(rest)
    from tempest_fastapi_sdk import AsyncMinIOClient

    endpoint, _, bucket = rest.rpartition(":")
    client = AsyncMinIOClient(
        endpoint=endpoint,
        access_key="minioadmin",
        secret_key="minioadmin",
        default_bucket=bucket,
    )
    return S3TusStore(client)


def main() -> None:
    """Serve the tus router until killed."""
    port = int(sys.argv[1])
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_tus_router(build_store(sys.argv[2]), current_user_id=caller)
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
