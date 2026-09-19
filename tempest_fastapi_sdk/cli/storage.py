"""``tempest storage`` — inspect and move objects in the project's bucket.

Everything here goes through ``AsyncMinIOClient(**settings.minio_kwargs())``,
so the endpoint, credentials, region and — importantly — the *public*
endpoint used to sign URLs are the ones the service uses. A presigned URL
produced with the internal endpoint is valid and useless: the browser it
is handed to cannot resolve ``minio:9000``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from tempest_fastapi_sdk.cli.project import load_project_settings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

storage_app: typer.Typer = typer.Typer(
    name="storage",
    help="Inspect and move objects in the configured object store.",
    no_args_is_help=True,
)

_BUCKET_OPTION: Any = typer.Option(
    "",
    "--bucket",
    "-b",
    help="Bucket to act on. Defaults to MINIO_DEFAULT_BUCKET.",
)


def _client_kwargs() -> dict[str, Any]:
    """Read the object-store kwargs off the project's settings.

    Returns:
        dict[str, Any]: Arguments for ``AsyncMinIOClient``.

    Raises:
        typer.Exit: Exit code 2 when the project composes no
            ``MinIOSettings``.
    """
    settings = load_project_settings()
    if settings is None or not hasattr(settings, "MINIO_ENDPOINT"):
        typer.echo(
            "error: this project has no object-store settings. Compose "
            "MinIOSettings into your Settings class.",
            err=True,
        )
        raise typer.Exit(2)
    kwargs: dict[str, Any] = settings.minio_kwargs()
    return kwargs


def _run(operation: Callable[[Any], Awaitable[Any]]) -> Any:
    """Run ``operation`` against a client built from the settings.

    Args:
        operation (Callable[[Any], Awaitable[Any]]): Coroutine function
            taking the client.

    Returns:
        Any: Whatever ``operation`` returned.

    Raises:
        typer.Exit: Exit code 2 when the ``[minio]`` extra is missing;
            code 1 when the store refuses the call, carrying its own
            message (``NoSuchBucket`` names the fix, "failed" does not).
    """
    kwargs = _client_kwargs()

    async def _call() -> Any:
        from tempest_fastapi_sdk.storage import AsyncMinIOClient

        async with AsyncMinIOClient(**kwargs) as client:
            return await operation(client)

    try:
        return asyncio.run(_call())
    except ImportError as exc:
        typer.echo(
            "error: the object store needs the [minio] extra. Install it: "
            'uv add "tempest-fastapi-sdk[minio]".',
            err=True,
        )
        raise typer.Exit(2) from exc
    except Exception as exc:
        typer.echo(
            f"error: {kwargs['endpoint']} — {type(exc).__name__}: {exc}",
            err=True,
        )
        raise typer.Exit(1) from exc


@storage_app.command("check")
def storage_check(bucket: str = _BUCKET_OPTION) -> None:
    """Report whether the store answers and the bucket exists.

    Raises:
        typer.Exit: Exit code 1 when the store answers but the bucket is
            absent, so the command is usable as a startup gate.
    """
    kwargs = _client_kwargs()
    target = bucket or kwargs["default_bucket"]

    async def _probe(client: Any) -> tuple[bool, list[str]]:
        return (await client.bucket_exists(target), await client.list_buckets())

    exists, buckets = _run(_probe)
    typer.echo(f"endpoint  {kwargs['endpoint']} ({len(buckets)} bucket(s))")
    if not exists:
        typer.echo(f"error: bucket {target!r} does not exist.", err=True)
        raise typer.Exit(1)
    typer.echo(f"bucket    {target} exists")


@storage_app.command("ls")
def storage_ls(
    prefix: str = typer.Argument("", help="Prefix filter. Empty lists everything."),
    bucket: str = _BUCKET_OPTION,
    flat: bool = typer.Option(
        False,
        "--flat",
        help="List only the immediate level instead of walking prefixes.",
    ),
) -> None:
    """List object keys under a prefix.

    An empty bucket prints nothing and exits 0 — no object is a result,
    not a failure, the same convention the SDK's repositories follow.
    """
    keys: list[str] = _run(
        lambda client: client.list_objects(
            prefix,
            bucket=bucket or None,
            recursive=not flat,
        )
    )
    for key in keys:
        typer.echo(key)


@storage_app.command("put")
def storage_put(
    source: Path = typer.Argument(..., help="Local file to upload."),
    key: str = typer.Option(
        "",
        "--key",
        "-k",
        help="Object key. Defaults to the file's name.",
    ),
    bucket: str = _BUCKET_OPTION,
) -> None:
    """Upload a local file into the bucket.

    Raises:
        typer.Exit: Exit code 2 when the local file does not exist.
    """
    if not source.is_file():
        typer.echo(f"error: {source} is not a file.", err=True)
        raise typer.Exit(2)
    target_key = key or source.name
    etag: str = _run(
        lambda client: client.fput_object(
            target_key,
            str(source),
            bucket=bucket or None,
        )
    )
    typer.echo(f"Uploaded {source} -> {target_key} (etag {etag})")


@storage_app.command("get")
def storage_get(
    key: str = typer.Argument(..., help="Object key to download."),
    out: Path = typer.Option(
        ...,
        "--out",
        "-o",
        help="Local path to write.",
    ),
    bucket: str = _BUCKET_OPTION,
) -> None:
    """Download one object to a local file."""
    _run(lambda client: client.fget_object(key, str(out), bucket=bucket or None))
    typer.echo(f"Downloaded {key} -> {out}")


@storage_app.command("presign")
def storage_presign(
    key: str = typer.Argument(..., help="Object key to sign a GET URL for."),
    expires: int = typer.Option(
        3600,
        "--expires",
        "-e",
        min=1,
        help="Lifetime of the URL, in seconds.",
    ),
    bucket: str = _BUCKET_OPTION,
) -> None:
    """Print a presigned GET URL for one object.

    The URL is signed against ``MINIO_PUBLIC_ENDPOINT`` when the project
    sets one. Without it the signature names the internal endpoint,
    which is valid and unusable from a browser that cannot resolve
    ``minio:9000`` — so the command says which host it signed for.
    """
    from datetime import timedelta

    kwargs = _client_kwargs()
    url: str = _run(
        lambda client: client.presigned_get_url(
            key,
            expires=timedelta(seconds=expires),
            bucket=bucket or None,
        )
    )
    typer.echo(url)
    signed_for = kwargs.get("public_endpoint") or kwargs["endpoint"]
    typer.echo(f"(signed for {signed_for}, valid {expires}s)", err=True)


@storage_app.command("rm")
def storage_rm(
    key: str = typer.Argument(..., help="Object key to delete."),
    bucket: str = _BUCKET_OPTION,
) -> None:
    """Delete one object from the bucket."""
    _run(lambda client: client.remove_object(key, bucket=bucket or None))
    typer.echo(f"Removed {key}")


__all__: list[str] = [
    "storage_app",
]
