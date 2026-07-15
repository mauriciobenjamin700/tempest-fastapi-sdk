"""Framework-agnostic helpers over a versioned-artifact table.

:class:`ArtifactRegistry` wraps a :class:`BaseRepository` bound to a
concrete :class:`~tempest_fastapi_sdk.artifacts.ArtifactVersionMixin`
table and exposes the three operations the pattern needs: resolve the
current version for a name, list every current version, and activate one
version (clearing the siblings of the same name in a single
transaction).

:func:`build_manifest_entries` turns the current rows into a list of
small, serialization-agnostic :class:`ArtifactManifestEntry` records; the
application decides the final wire shape (download URLs, extra metadata,
the top-level manifest envelope).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Generic, TypeVar
from uuid import UUID

from pydantic import Field
from sqlalchemy import update

from tempest_fastapi_sdk.artifacts.model import ArtifactVersionMixin
from tempest_fastapi_sdk.db.repository import BaseRepository
from tempest_fastapi_sdk.schemas.base import BaseSchema

if TYPE_CHECKING:
    from tempest_fastapi_sdk.storage import AsyncMinIOClient

TModel = TypeVar("TModel", bound=ArtifactVersionMixin)


class ArtifactManifestEntry(BaseSchema):
    """One current-artifact row reduced to the manifest essentials.

    Serialization-agnostic on purpose: it carries the identity and
    integrity fields every manifest needs and leaves URL scheme,
    caching hints and the enclosing envelope to the application.

    Attributes:
        name (str): Logical artifact key.
        version (str): Opaque version label of the current row.
        file_key (str): Object-storage key of the current bytes.
        sha256 (str): Hex SHA-256 of the current bytes.
        size (int): Size of the current bytes, in bytes.
    """

    name: str = Field(
        title="Artifact name",
        description="Logical artifact key the entry resolves.",
        examples=["detect", "classify"],
    )
    version: str = Field(
        title="Version label",
        description="Opaque version label of the current row.",
        examples=["1.2.0", "2024-06-01-a1b2c3"],
    )
    file_key: str = Field(
        title="Object key",
        description="Object-storage key of the current bytes.",
        examples=["models/detect/1.2.0.onnx"],
    )
    sha256: str = Field(
        title="Content SHA-256",
        description="Hex SHA-256 digest of the current bytes.",
        examples=["e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"],
    )
    size: int = Field(
        title="Content size",
        description="Size of the current bytes, in bytes.",
        examples=[10485760],
    )


class ArtifactRegistry(Generic[TModel]):
    """Resolve, list and activate versions of a binary artifact.

    Generic over the concrete
    :class:`~tempest_fastapi_sdk.artifacts.ArtifactVersionMixin` table so
    every method returns the project's own rows. The optional ``minio`` /
    ``bucket`` pair is not used by the registry itself — it is held so
    callers (and the manifest ``digest_source``) can reach object storage
    without threading a second dependency through.

    Generic parameters:
        TModel: The concrete artifact-version model.

    Attributes:
        repository (BaseRepository[TModel]): Data access for the
            artifact-version table.
        minio (AsyncMinIOClient | None): Optional object-storage client
            for digesting / serving the stored bytes.
        bucket (str | None): Optional bucket the artifact objects live in.
    """

    def __init__(
        self,
        repository: BaseRepository[TModel],
        *,
        minio: AsyncMinIOClient | None = None,
        bucket: str | None = None,
    ) -> None:
        """Initialize the registry.

        Args:
            repository (BaseRepository[TModel]): Repository bound to the
                concrete artifact-version model.
            minio (AsyncMinIOClient | None): Optional object-storage
                client used to digest / serve the stored bytes.
            bucket (str | None): Optional bucket the objects live in.
        """
        self.repository: BaseRepository[TModel] = repository
        self.minio: AsyncMinIOClient | None = minio
        self.bucket: str | None = bucket

    async def current(self, name: str) -> TModel | None:
        """Return the current version for ``name``, or ``None``.

        Args:
            name (str): The logical artifact key.

        Returns:
            TModel | None: The row flagged ``is_current`` for ``name``,
            or ``None`` when no version has been activated yet.
        """
        return await self.repository.first(
            filters={"name": name, "is_current": True},
        )

    async def list_current(self) -> list[TModel]:
        """Return the current version of every artifact.

        Returns:
            list[TModel]: One ``is_current`` row per ``name`` (``[]``
            when nothing has been activated yet).
        """
        return await self.repository.list(filters={"is_current": True})

    async def activate(self, version_id: UUID) -> TModel:
        """Make one version the current one for its name.

        Flips ``is_current`` on the selected row and clears it on every
        sibling of the same ``name`` in a single transaction, so exactly
        one version per name is ever current.

        Args:
            version_id (UUID): Primary key of the version to activate.

        Returns:
            TModel: The activated row (refreshed).

        Raises:
            AppException: The repository's ``not_found_exception`` when
                no version with ``version_id`` exists.
        """
        target = await self.repository.get_by_id(version_id)
        model = self.repository.model
        session = self.repository.session
        await session.execute(
            update(model).where(model.name == target.name).values(is_current=False),
        )
        target.is_current = True
        await session.commit()
        await session.refresh(target)
        return target


async def build_manifest_entries(
    registry: ArtifactRegistry[TModel],
    *,
    digest_source: Callable[[TModel], Awaitable[tuple[str, int]]],
) -> list[ArtifactManifestEntry]:
    """Build manifest entries from a registry's current rows.

    Stays serialization-agnostic: it resolves the current version of
    every artifact and asks ``digest_source`` for each row's ``(sha256,
    size)``. The caller's ``digest_source`` decides where the bytes come
    from — typically
    :func:`~tempest_fastapi_sdk.artifacts.object_digest` against MinIO,
    with a :func:`~tempest_fastapi_sdk.artifacts.file_digest` fallback to
    a bundled on-disk file.

    Args:
        registry (ArtifactRegistry[TModel]): The registry to read current
            rows from.
        digest_source (Callable[[TModel], Awaitable[tuple[str, int]]]):
            Async function mapping a current row to its ``(sha256,
            size)``.

    Returns:
        list[ArtifactManifestEntry]: One entry per current artifact
        (``[]`` when nothing has been activated).
    """
    entries: list[ArtifactManifestEntry] = []
    for row in await registry.list_current():
        sha256, size = await digest_source(row)
        entries.append(
            ArtifactManifestEntry(
                name=row.name,
                version=row.version,
                file_key=row.file_key,
                sha256=sha256,
                size=size,
            ),
        )
    return entries


__all__: list[str] = [
    "ArtifactManifestEntry",
    "ArtifactRegistry",
    "build_manifest_entries",
]
