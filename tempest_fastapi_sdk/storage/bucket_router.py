"""One S3 credential per bucket behind a single storage client.

Some S3-compatible stores bind every access key to one bucket: the key made
for the public images cannot read the private documents. A service with two
buckets then needs two credentials, and :class:`BucketRoutedMinIOClient`
keeps that out of its code — it is an :class:`AsyncMinIOClient` whose every
bucket-scoped call is signed with the credential of the bucket it names,
falling back on the primary credential for a bucket without one.

``StorageSettings.build_storage_client`` returns it when
``STORAGE_BUCKET_CREDENTIALS`` is set, so the service keeps calling
``put_object(key, data, bucket="documents")`` as before.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from tempest_fastapi_sdk.settings.storage_credential import StorageBucketCredential
from tempest_fastapi_sdk.storage.minio_client import AsyncMinIOClient

if TYPE_CHECKING:
    from minio import Minio


class BucketRoutedMinIOClient(AsyncMinIOClient):
    """An :class:`AsyncMinIOClient` that signs each bucket with its own key.

    Built like :class:`AsyncMinIOClient`, plus ``bucket_credentials``. The
    constructor arguments describe the **primary** credential, which answers
    every bucket missing from ``bucket_credentials``; each listed bucket
    gets a client of its own with the same endpoint, region, TLS and public
    endpoint, and only its keys swapped.

    Every operation that takes ``bucket`` resolves it first
    (``bucket or default_bucket``) and is sent through the client of that
    bucket — presigned URLs included, so a link is signed by the key that
    can read the object. Two calls name no bucket and stay on the primary
    credential: :meth:`list_buckets` and the :attr:`client` property.

    :meth:`copy_object` is the one call with two buckets. When both resolve
    to the same credential it is the store's server-side copy, unchanged;
    when they resolve to different credentials no single key can read the
    source and write the destination, so the object is read with the source
    key into a temporary file and uploaded with the destination key.

    Example:

        >>> from pydantic import SecretStr
        >>> from tempest_fastapi_sdk.storage import (
        ...     BucketRoutedMinIOClient,
        ...     StorageBucketCredential,
        ... )
        >>> storage = BucketRoutedMinIOClient(
        ...     "storage.example.com",
        ...     "images-key",
        ...     "images-secret",
        ...     default_bucket="images",
        ...     secure=True,
        ...     bucket_credentials={
        ...         "documents": StorageBucketCredential(
        ...             access_key="documents-key",
        ...             secret_key=SecretStr("documents-secret"),
        ...         ),
        ...     },
        ... )
        >>> sorted(storage.routed_buckets)
        ['documents']
    """

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        *,
        bucket_credentials: Mapping[str, StorageBucketCredential],
        default_bucket: str = "uploads",
        secure: bool = False,
        region: str = "us-east-1",
        session_token: str | None = None,
        public_endpoint: str | None = None,
        public_secure: bool | None = None,
        accel_redirect: bool = False,
        accel_prefix: str = "/_bucket/",
        enabled: bool = True,
    ) -> None:
        """Initialize the primary client and one client per listed bucket.

        Args:
            endpoint (str): ``host[:port]`` without scheme, shared by every
                credential.
            access_key (str): Primary access key, used for every bucket
                missing from ``bucket_credentials``.
            secret_key (str): Primary secret key.
            bucket_credentials (Mapping[str, StorageBucketCredential]):
                Bucket name to the credential that reaches it.
            default_bucket (str): Bucket of a call that names none. It is
                routed like any other: listed in ``bucket_credentials``, it
                uses that credential.
            secure (bool): Use HTTPS when ``True``.
            region (str): S3 region.
            session_token (str | None): STS session token of the
                **primary** credential only; the per-bucket clients sign
                without one.
            public_endpoint (str | None): Split-endpoint host presigned URLs
                are signed against, for every credential.
            public_secure (bool | None): HTTPS for ``public_endpoint``.
            accel_redirect (bool): Delivery mode of :meth:`serve_object`.
            accel_prefix (str): Internal nginx location of
                ``X-Accel-Redirect``.
            enabled (bool): ``False`` builds a client whose every operation
                raises ``IntegrationDisabledException``; no per-bucket
                client is built.

        Raises:
            ValueError: When a bucket name in ``bucket_credentials`` is
                empty, or ``accel_prefix`` does not start with ``/``.
            ImportError: When the ``minio`` package is not installed and
                ``enabled`` is ``True``.
        """
        super().__init__(
            endpoint,
            access_key,
            secret_key,
            default_bucket=default_bucket,
            secure=secure,
            region=region,
            session_token=session_token,
            public_endpoint=public_endpoint,
            public_secure=public_secure,
            accel_redirect=accel_redirect,
            accel_prefix=accel_prefix,
            enabled=enabled,
        )
        if any(not bucket for bucket in bucket_credentials):
            raise ValueError("bucket_credentials has an empty bucket name")
        self._routes: dict[str, AsyncMinIOClient] = {}
        if not enabled:
            return
        for bucket, credential in bucket_credentials.items():
            self._routes[bucket] = AsyncMinIOClient(
                endpoint,
                credential.access_key,
                credential.secret_key.get_secret_value(),
                default_bucket=bucket,
                secure=secure,
                region=region,
                public_endpoint=public_endpoint,
                public_secure=public_secure,
                accel_redirect=accel_redirect,
                accel_prefix=accel_prefix,
            )

    @property
    def routed_buckets(self) -> frozenset[str]:
        """Return the buckets that have a credential of their own.

        Empty when the client was built with ``enabled=False``.

        Returns:
            frozenset[str]: Bucket names; any other bucket uses the
            primary credential.
        """
        return frozenset(self._routes)

    def _client_for(self, bucket: str) -> Minio:
        """Return the client of ``bucket``'s credential, or the primary one.

        Args:
            bucket (str): The resolved bucket of the operation.

        Returns:
            Minio: The client signed with the credential of ``bucket``.

        Raises:
            IntegrationDisabledException: When the client was built with
                ``enabled=False``.
        """
        route = self._routes.get(bucket)
        if route is None:
            return super()._client_for(bucket)
        return route.client

    def _presign_client_for(self, bucket: str) -> Minio:
        """Return the signer of ``bucket``'s credential, or the primary one.

        Args:
            bucket (str): The resolved bucket of the operation.

        Returns:
            Minio: The client that signs presigned URLs for ``bucket``.

        Raises:
            IntegrationDisabledException: When the client was built with
                ``enabled=False``.
        """
        route = self._routes.get(bucket)
        if route is None:
            return super()._presign_client_for(bucket)
        return route._presign_client_for(bucket)

    async def copy_object(
        self,
        source_key: str,
        dest_key: str,
        *,
        source_bucket: str | None = None,
        dest_bucket: str | None = None,
    ) -> str:
        """Copy an object, across credentials when the buckets need it.

        Both buckets on the same credential: the store's server-side copy
        (:meth:`AsyncMinIOClient.copy_object`). On different credentials:
        the source is downloaded with its key into a temporary file
        (deleted afterwards) and uploaded with the destination key, carrying
        the source's ``Content-Type`` and user metadata. Other stored
        headers (``Cache-Control``, ``Content-Disposition``) are not carried
        over on that path, and the bytes cross the app.

        Args:
            source_key (str): Source object key.
            dest_key (str): Destination object key.
            source_bucket (str | None): Source bucket; defaults to
                ``default_bucket``.
            dest_bucket (str | None): Destination bucket; defaults to
                ``default_bucket``.

        Returns:
            str: ETag of the copied object (quotes stripped).

        Raises:
            S3Error: When the source is missing or either side refuses.
            IntegrationDisabledException: When the client was built with
                ``enabled=False``.
        """
        source = self._bucket(source_bucket)
        dest = self._bucket(dest_bucket)
        if self._client_for(source) is self._client_for(dest):
            return await super().copy_object(
                source_key,
                dest_key,
                source_bucket=source,
                dest_bucket=dest,
            )
        stat = await self.stat_object(source_key, bucket=source)
        with tempfile.TemporaryDirectory(prefix="tempest-copy-") as workdir:
            path = Path(workdir) / "object"
            await self.fget_object(source_key, path, bucket=source)
            return await self.fput_object(
                dest_key,
                path,
                bucket=dest,
                content_type=stat.content_type or "application/octet-stream",
                metadata=stat.metadata or None,
            )


__all__: list[str] = ["BucketRoutedMinIOClient"]
