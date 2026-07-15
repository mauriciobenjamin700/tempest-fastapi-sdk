"""Versioned binary artifact registry — DB-backed, activatable, digested.

A generic core for a "one current version per name" registry: a
:class:`ArtifactVersionMixin` table, an :class:`ArtifactRegistry` over a
``BaseRepository``, streamed + memoized content digests
(:func:`file_digest` / :func:`object_digest`), a serialization-agnostic
manifest builder (:func:`build_manifest_entries` →
:class:`ArtifactManifestEntry`) and an admin action factory
(:func:`make_activate_artifact_action`).

The MinIO helpers need the ``[minio]`` extra
(``pip install tempest-fastapi-sdk[minio]``); everything else runs on the
core dependencies.
"""

from tempest_fastapi_sdk.artifacts.actions import (
    make_activate_artifact_action as make_activate_artifact_action,
)
from tempest_fastapi_sdk.artifacts.digest import file_digest as file_digest
from tempest_fastapi_sdk.artifacts.digest import object_digest as object_digest
from tempest_fastapi_sdk.artifacts.model import (
    ArtifactVersionMixin as ArtifactVersionMixin,
)
from tempest_fastapi_sdk.artifacts.registry import (
    ArtifactManifestEntry as ArtifactManifestEntry,
)
from tempest_fastapi_sdk.artifacts.registry import ArtifactRegistry as ArtifactRegistry
from tempest_fastapi_sdk.artifacts.registry import (
    build_manifest_entries as build_manifest_entries,
)

__all__: list[str] = [
    "ArtifactManifestEntry",
    "ArtifactRegistry",
    "ArtifactVersionMixin",
    "build_manifest_entries",
    "file_digest",
    "make_activate_artifact_action",
    "object_digest",
]
