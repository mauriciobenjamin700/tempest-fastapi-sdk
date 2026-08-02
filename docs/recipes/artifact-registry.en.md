# Versioned artifact registry

Some services serve **binaries that swap at runtime**: ONNX models, rule bundles, compiled config files. The shape is always the same — one logical artifact (`name`) has **many versions**, and exactly **one is "current"** at a time. An operator uploads a new version to object storage and activates it from the admin panel; the endpoints resolve the current version per request, with no redeploy.

The `tempest_fastapi_sdk.artifacts` module ships the **generic core** of that pattern:

- `ArtifactVersionMixin` — the `(name, version, file_key, is_current)` columns.
- `ArtifactRegistry` — resolve the current version, list the current ones, activate one (one current per `name`).
- `file_digest` / `object_digest` — sha256 + size, **streamed** and **memoized** by the immutable identity.
- `build_manifest_entries` + `ArtifactManifestEntry` — a serialization-agnostic manifest.
- `make_activate_artifact_action` — the admin action that activates the selected version.

!!! info "What the SDK does NOT decide for you"
    The SDK stays generic. The domain (`.onnx` names, per-model `input_size`, the exact manifest shape your PWA consumes, the download URL scheme) lives in **your service** — the registry just hands you the pieces.

## Installation

Object digesting and serving bytes use the MinIO client, shipped in the `[minio]` extra:

```bash
uv add "tempest-fastapi-sdk[minio]"
```

Everything else (mixin, registry, file digest, admin action) runs on the core only.

## 1. The table

Mix `ArtifactVersionMixin` into a concrete `BaseModel` table. `is_current` is **distinct** from `is_active` (`BaseModel`'s soft-delete flag): a version can be active (not deleted) and still not be the one served.

```python
from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.artifacts import ArtifactVersionMixin


class ModelVersion(BaseModel, ArtifactVersionMixin):
    """One version of an ONNX model served at runtime."""

    __tablename__ = "model_versions"
```

That's it — you inherit `id` / `is_active` / `created_at` / `updated_at` from `BaseModel` **and** `name` / `version` / `file_key` / `is_current` from the mixin.

!!! tip "Checksums are not columns"
    Don't store `sha256`/`size` on the table. They derive from the **immutable** object in storage and are computed on demand (and memoized by `file_key`). Storing them would duplicate the truth and invite drift.

## 2. The registry

`ArtifactRegistry` takes a `BaseRepository` bound to your table and, optionally, the MinIO client + bucket.

```python
from tempest_fastapi_sdk import AsyncMinIOClient, BaseRepository
from tempest_fastapi_sdk.artifacts import ArtifactRegistry

from src.db.models import ModelVersion


def build_registry(session, storage: AsyncMinIOClient) -> ArtifactRegistry[ModelVersion]:
    """Assemble the registry for the model-version table."""
    repository: BaseRepository[ModelVersion] = BaseRepository(session, model=ModelVersion)
    return ArtifactRegistry(repository, minio=storage, bucket="models")
```

The three operations:

```python
import asyncio
from uuid import UUID

from tempest_fastapi_sdk import UploadUtils

from src.db.repositories import build_registry

registry = build_registry(session, UploadUtils(source="./uploads"))

version_id = UUID("6f1c3d84-2a55-4d0b-9d7e-0c1a2b3c4d5e")

session = None  # provided by db.get_session_context() in your code


async def main() -> None:
    """Run this example."""
    # The served version of "detect" (or None when none has been activated yet).
    current = await registry.current("detect")

    # The current version of every artifact (one current per name).
    rows = await registry.list_current()

    # Activate a version: flip is_current on it and clear it on the same-name
    # siblings, all in one transaction.
    activated = await registry.activate(version_id)


asyncio.run(main())
```

!!! note "One current per `name`, enforced on write"
    `activate` runs an `UPDATE ... SET is_current=False WHERE name=<name>` then flips the flag on the target row, in the same commit. There's no DB constraint — the invariant is kept by the action, exactly like the reference service.

## 3. The digest (streamed + memoized)

Both helpers return `(sha256, size)` **without loading the whole file into memory** (1 MiB chunks) and memoize the result, because the identity is immutable:

```python
import asyncio

from tempest_fastapi_sdk import UploadUtils
from tempest_fastapi_sdk.artifacts import file_digest, object_digest

storage = UploadUtils(source="./uploads")


async def main() -> None:
    """Run this example."""
    # On-disk file (the bundled fallback), cached by path:
    sha256, size = await file_digest("/opt/models/detect.onnx")

    # Object in MinIO, cached by (bucket, key):
    sha256, size = await object_digest(storage, "models", "detect/1.2.0.onnx")


asyncio.run(main())
```

## 4. The manifest

`build_manifest_entries` walks the current versions and asks a **`digest_source` you provide** for each row's `(sha256, size)` — that's where you decide where the bytes come from (MinIO for active versions, disk for the bundled fallback).

```python
import asyncio

from tempest_fastapi_sdk import UploadUtils
from tempest_fastapi_sdk.artifacts import (
    ArtifactManifestEntry,
    build_manifest_entries,
    object_digest,
)

from src.db.models import ModelVersion
from src.db.repositories import build_registry

registry = build_registry(session, UploadUtils(source="./uploads"))

storage = UploadUtils(source="./uploads")

session = None  # provided by db.get_session_context() in your code


async def main() -> None:
    """Run this example."""
    async def model_digest(row: ModelVersion) -> tuple[str, int]:
        """Digest the current version from its MinIO object."""
        return await object_digest(storage, "models", row.file_key)


    entries: list[ArtifactManifestEntry] = await build_manifest_entries(
        registry, digest_source=model_digest
    )


asyncio.run(main())
```

Each `ArtifactManifestEntry` carries `name`, `version`, `file_key`, `sha256`, `size`. The **final envelope** (download URL, global manifest version, domain fields like `input_size`) is yours to assemble on top:

```python
from fastapi import APIRouter
from pydantic import BaseModel

from tempest_fastapi_sdk import UploadUtils
from tempest_fastapi_sdk.artifacts import build_manifest_entries, object_digest

from src.db.repositories import build_registry


async def model_digest() -> tuple[str, int]:
    """Return the (sha256, size) of the object behind the active version."""
    return await object_digest(storage, "models", "detect/1.2.0.onnx")


registry = build_registry(session, UploadUtils(source="./uploads"))

storage = UploadUtils(source="./uploads")

session = None  # provided by db.get_session_context() in your code


router = APIRouter(prefix="/models", tags=["models"])


class ModelManifest(BaseModel):
    """Manifest shape your PWA consumes."""

    models: list[dict]


@router.get("/manifest")
async def manifest() -> ModelManifest:
    """Manifest the client polls to detect newer versions."""
    entries = await build_manifest_entries(registry, digest_source=model_digest)
    return ModelManifest(
        models=[
            {
                "name": e.name,
                "url": f"/models/{e.name}.onnx",
                "sha256": e.sha256,
                "size": e.size,
            }
            for e in entries
        ]
    )
```

## 5. The "Activate version" admin action

`make_activate_artifact_action` returns an `AdminAction` whose handler activates the selected row (clearing same-`name` siblings). The handler carries the `@admin_action` marker, so register it by passing `action.handler`:

```python
from tempest_fastapi_sdk import AdminModel
from tempest_fastapi_sdk.artifacts import make_activate_artifact_action

from src.admin import site
from src.db.models import ModelVersion


activate = make_activate_artifact_action(label="Activate version")

site.register(AdminModel(model=ModelVersion, actions=[activate.handler]))
```

Select **one** version in the list, choose "Activate version" — the panel flips `is_current` on it and clears it on the others of the same model. Selecting zero or many returns a warning.

!!! check "Why pass `action.handler`"
    `AdminModel(actions=[...])` expects functions decorated with `@admin_action` (it reads the `__admin_action__` marker). The factory returns the `AdminAction`, and `.handler` is the decorated function — so `action.handler` is exactly what `AdminModel` knows how to register.

## 6. Serving the bytes (with fallback)

Resolve the active version; if it exists, stream it from MinIO; otherwise serve the bundled on-disk file.

```python
from fastapi import APIRouter
from fastapi.responses import FileResponse
from starlette.responses import StreamingResponse

from tempest_fastapi_sdk import UploadUtils

from src.db.repositories import build_registry

registry = build_registry(session, UploadUtils(source="./uploads"))

storage = UploadUtils(source="./uploads")

session = None  # provided by db.get_session_context() in your code

router = APIRouter()


@router.get("/{name}.onnx", response_model=None)
async def download(name: str) -> StreamingResponse | FileResponse:
    """Serve the active version from MinIO, or the bundled file."""
    active = await registry.current(name)
    if active is not None:
        return await storage.download_response(
            active.file_key,
            bucket="models",
            media_type="application/octet-stream",
            filename=f"{name}.onnx",
        )
    return FileResponse(f"/opt/models/{name}.onnx", filename=f"{name}.onnx")
```

## Recap

- **Mix** `ArtifactVersionMixin` into a `BaseModel` table → `name` / `version` / `file_key` / `is_current` columns.
- **`ArtifactRegistry`** gives `current` / `list_current` / `activate` (one current per `name`, in one transaction).
- **`file_digest` / `object_digest`** stream the sha256 + size and memoize by the immutable identity.
- **`build_manifest_entries`** builds agnostic entries; you supply the `digest_source` and the final envelope.
- **`make_activate_artifact_action`** is the activation admin action — register `action.handler`.

The generic pattern comes from the SDK; the domain details (file names, `input_size`, the PWA manifest shape) stay in your service. 🚀
