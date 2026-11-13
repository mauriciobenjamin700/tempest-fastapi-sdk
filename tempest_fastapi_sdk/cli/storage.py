"""``tempest storage`` — inspect and move objects in the project's bucket.

Everything here goes through ``AsyncMinIOClient(**settings.storage_kwargs())``,
so the endpoint, credentials, region and — importantly — the *public*
endpoint used to sign URLs are the ones the service uses. A presigned URL
produced with the internal endpoint is valid and useless: the browser it
is handed to cannot resolve ``minio:9000``. With
``STORAGE_BUCKET_CREDENTIALS`` set the client is the
``BucketRoutedMinIOClient`` the service builds, so ``--bucket documents``
is signed with the documents key.

``encrypt-local`` and ``reencrypt-local`` act on the local backend instead,
through ``LocalObjectStorageClient(**settings.local_storage_kwargs())``: they
migrate the files under ``STORAGE_LOCAL_DIR`` to the encryption at rest that
``STORAGE_LOCAL_ENCRYPTION_KEYS`` switches on.
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
    help="Bucket to act on. Defaults to STORAGE_DEFAULT_BUCKET.",
)


def _client_kwargs() -> dict[str, Any]:
    """Read the object-store kwargs off the project's settings.

    Returns:
        dict[str, Any]: Arguments for ``AsyncMinIOClient``, plus
        ``bucket_credentials`` when ``STORAGE_BUCKET_CREDENTIALS`` is set
        (the arguments of ``BucketRoutedMinIOClient`` then).

    Raises:
        typer.Exit: Exit code 2 when the project composes no
            ``StorageSettings`` (or its deprecated ``MinIOSettings``
            name), or when ``STORAGE_ENABLED`` is off.
    """
    settings = load_project_settings()
    if settings is None or not hasattr(settings, "storage_kwargs"):
        typer.echo(
            "error: this project has no object-store settings. Compose "
            "StorageSettings into your Settings class.",
            err=True,
        )
        raise typer.Exit(2)
    kwargs: dict[str, Any] = settings.storage_kwargs()
    if kwargs.get("enabled") is False:
        typer.echo(
            "error: STORAGE_ENABLED=false — the object-store integration is "
            "switched off in this environment.",
            err=True,
        )
        raise typer.Exit(2)
    bucket_credentials = getattr(settings, "STORAGE_BUCKET_CREDENTIALS", None)
    if bucket_credentials:
        kwargs["bucket_credentials"] = bucket_credentials
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
        from tempest_fastapi_sdk.storage import (
            AsyncMinIOClient,
            BucketRoutedMinIOClient,
        )

        client_class: type[AsyncMinIOClient] = (
            BucketRoutedMinIOClient
            if "bucket_credentials" in kwargs
            else AsyncMinIOClient
        )
        async with client_class(**kwargs) as client:
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


def _local_kwargs() -> dict[str, Any]:
    """Read the local-store kwargs off the project's settings.

    Returns:
        dict[str, Any]: Arguments for ``LocalObjectStorageClient``, with
        ``encryption_keys``.

    Raises:
        typer.Exit: Exit code 2 when the project composes no
            ``StorageSettings``, ``STORAGE_ENABLED`` is off, or
            ``STORAGE_LOCAL_ENCRYPTION_KEYS`` is empty.
    """
    settings = load_project_settings()
    if settings is None or not hasattr(settings, "local_storage_kwargs"):
        typer.echo(
            "error: this project has no object-store settings. Compose "
            "StorageSettings into your Settings class.",
            err=True,
        )
        raise typer.Exit(2)
    kwargs: dict[str, Any] = settings.local_storage_kwargs()
    if kwargs.get("enabled") is False:
        typer.echo(
            "error: STORAGE_ENABLED=false — the object-store integration is "
            "switched off in this environment.",
            err=True,
        )
        raise typer.Exit(2)
    if not kwargs.get("encryption_keys"):
        typer.echo(
            "error: STORAGE_LOCAL_ENCRYPTION_KEYS is empty — there is no key "
            "to encrypt with.",
            err=True,
        )
        raise typer.Exit(2)
    return kwargs


def _rewrite_local(bucket: str, prefix: str, *, rotate: bool) -> dict[str, int]:
    """Run the local store's migration over one bucket or all of them.

    Args:
        bucket (str): Bucket to act on; empty for every bucket on disk.
        prefix (str): Key prefix.
        rotate (bool): ``True`` runs ``reencrypt``; ``False`` runs
            ``encrypt_existing``.

    Returns:
        dict[str, int]: Objects rewritten, per bucket.

    Raises:
        typer.Exit: Exit code 2 when the ``[storage-encryption]`` extra is
            missing; code 1 when an object fails, carrying the error.
    """
    kwargs = _local_kwargs()

    async def _call() -> dict[str, int]:
        from tempest_fastapi_sdk.storage import LocalObjectStorageClient

        client = LocalObjectStorageClient(**kwargs)
        buckets = [bucket] if bucket else await client.list_buckets()
        done: dict[str, int] = {}
        for name in buckets:
            if rotate:
                done[name] = await client.reencrypt(prefix, bucket=name)
            else:
                done[name] = await client.encrypt_existing(prefix, bucket=name)
        return done

    try:
        return asyncio.run(_call())
    except ImportError as exc:
        typer.echo(
            "error: encrypting the local store needs the [storage-encryption] "
            'extra. Install it: uv add "tempest-fastapi-sdk[storage-encryption]".',
            err=True,
        )
        raise typer.Exit(2) from exc
    except Exception as exc:
        typer.echo(f"error: {type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc


_LOCAL_BUCKET_OPTION: Any = typer.Option(
    "",
    "--bucket",
    "-b",
    help="Bucket to act on. Defaults to every bucket under STORAGE_LOCAL_DIR.",
)


@storage_app.command("encrypt-local")
def storage_encrypt_local(
    prefix: str = typer.Argument("", help="Key prefix. Empty covers every key."),
    bucket: str = _LOCAL_BUCKET_OPTION,
) -> None:
    """Encrypt the plaintext files of the local store with the first key.

    The migration of a volume written before STORAGE_LOCAL_ENCRYPTION_KEYS
    was set. Idempotent: a file that is already encrypted is skipped, so it
    is safe to run again after an interruption. Run it while nothing
    writes to the store.
    """
    for name, count in _rewrite_local(bucket, prefix, rotate=False).items():
        typer.echo(f"{name}: {count} object(s) encrypted")


@storage_app.command("reencrypt-local")
def storage_reencrypt_local(
    prefix: str = typer.Argument("", help="Key prefix. Empty covers every key."),
    bucket: str = _LOCAL_BUCKET_OPTION,
) -> None:
    """Rewrite files sealed with an older key under the first key.

    The second half of a key rotation: put the new key first in
    STORAGE_LOCAL_ENCRYPTION_KEYS, keep the old one, run this, then drop
    the old key. Idempotent: a file already under the first key is skipped.
    """
    for name, count in _rewrite_local(bucket, prefix, rotate=True).items():
        typer.echo(f"{name}: {count} object(s) re-encrypted")


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

    The URL is signed against ``STORAGE_PUBLIC_ENDPOINT`` when the project
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
