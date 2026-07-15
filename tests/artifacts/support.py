"""Shared concrete model + helpers for the artifacts test suite.

Defined once (module import is cached) so the test table is registered
on ``BaseModel.metadata`` a single time, then reused across the suite.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    ArtifactRegistry,
    ArtifactVersionMixin,
    BaseModel,
    BaseRepository,
)


class ArtifactVersionModel(BaseModel, ArtifactVersionMixin):
    """Concrete artifact-version table used across the artifacts tests."""

    __tablename__ = "artifact_versions_test"


def make_repository(session: AsyncSession) -> BaseRepository[ArtifactVersionModel]:
    """Build a repository bound to the test artifact-version table.

    Args:
        session (AsyncSession): The async database session.

    Returns:
        BaseRepository[ArtifactVersionModel]: The bound repository.
    """
    return BaseRepository(session, model=ArtifactVersionModel)


def make_registry(
    session: AsyncSession,
    **kwargs: Any,
) -> ArtifactRegistry[ArtifactVersionModel]:
    """Build a registry over the test repository.

    Args:
        session (AsyncSession): The async database session.
        **kwargs (Any): Forwarded to :class:`ArtifactRegistry` (e.g.
            ``minio`` / ``bucket``).

    Returns:
        ArtifactRegistry[ArtifactVersionModel]: The registry.
    """
    return ArtifactRegistry(make_repository(session), **kwargs)


async def add_version(
    session: AsyncSession,
    *,
    name: str,
    version: str,
    file_key: str,
    is_current: bool = False,
) -> ArtifactVersionModel:
    """Insert one artifact version row and return it refreshed.

    Args:
        session (AsyncSession): The async database session.
        name (str): Logical artifact key.
        version (str): Opaque version label.
        file_key (str): Object-storage key.
        is_current (bool): Whether the row is the current version.

    Returns:
        ArtifactVersionModel: The persisted row.
    """
    row = ArtifactVersionModel(
        name=name,
        version=version,
        file_key=file_key,
        is_current=is_current,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
