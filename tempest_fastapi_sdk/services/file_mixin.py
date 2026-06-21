"""Reusable service mixin for single-key stored-file fields.

A great many entities carry exactly one object-storage key — an avatar,
a banner, a cover, a document — and every service that owns one rewrites
the same orchestration by hand:

1. resolve the entity (detach-safe),
2. upload the new file and delete the one it replaces,
3. write the new key back onto the model,
4. commit, and
5. hand out a presigned download URL.

:class:`StoredFileServiceMixin` encodes that flow once, parameterized by
the **field name**, so a single service can manage several file fields
without duplication. It deliberately covers only the common
"one key column → presigned URL" case; resize/thumbnail pipelines,
multi-variant assets and gallery (one-to-many) uploads are out of scope —
compose :class:`UploadUtils` directly for those.

The mixin is storage-agnostic by structural typing: it reads two
collaborators off ``self`` — ``upload_utils`` and ``storage`` — described
by the :class:`SupportsUpload` and :class:`SupportsPresign` protocols, so
importing it never pulls the optional ``[upload]`` / ``[minio]`` extras.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar
from uuid import UUID

from tempest_fastapi_sdk.db.model import BaseModel

if TYPE_CHECKING:
    from fastapi import UploadFile

    from tempest_fastapi_sdk.db.repository import BaseRepository

ModelType = TypeVar("ModelType", bound=BaseModel)

# One hour matches AsyncMinIOClient.presigned_get_url's own default.
_DEFAULT_URL_TTL = timedelta(hours=1)


class SupportsUpload(Protocol):
    """Structural type for the upload helper the mixin needs.

    Satisfied by :class:`tempest_fastapi_sdk.UploadUtils`.
    """

    async def replace(
        self,
        old_key: Path | str | None,
        file: UploadFile,
        *,
        subdir: str = ...,
        filename: str | None = ...,
        keep_original_name: bool = ...,
    ) -> Path:
        """Persist ``file`` and delete the object at ``old_key``."""
        ...

    async def delete(self, key: Path | str) -> bool:
        """Delete the object at ``key``; return whether it existed."""
        ...


class SupportsPresign(Protocol):
    """Structural type for the storage client the mixin needs.

    Satisfied by :class:`tempest_fastapi_sdk.AsyncMinIOClient`.
    """

    async def presigned_get_url(
        self,
        key: str,
        *,
        expires: timedelta = ...,
    ) -> str:
        """Return a temporary download URL for ``key``."""
        ...


class StoredFileServiceMixin(Generic[ModelType]):
    """Manage a single object-storage key field on a service's model.

    Mix into a service that already owns a ``repository`` (e.g. a
    :class:`tempest_fastapi_sdk.BaseService` subclass) and exposes an
    ``upload_utils`` and a ``storage`` collaborator::

        class UserService(
            BaseService[UserRepository, UserResponseSchema],
            StoredFileServiceMixin[UserModel],
        ):
            def __init__(self, repository, storage, upload_utils):
                super().__init__(repository)
                self.storage = storage
                self.upload_utils = upload_utils

        user = await service.set_file(
            user, image, field="profile_picture", subdir="profiles"
        )
        url = await service.file_url(user.profile_picture)

    The mixin does not construct its collaborators — it reads them off
    ``self`` so the owning service stays in charge of configuration
    (size limits, allowed types, bucket).

    Generic parameters:
        ModelType: The ORM model whose field is managed.

    Attributes:
        repository (BaseRepository[ModelType]): Supplied by the service.
        upload_utils (SupportsUpload): Supplied by the service.
        storage (SupportsPresign): Supplied by the service.
    """

    repository: BaseRepository[ModelType]
    upload_utils: SupportsUpload
    storage: SupportsPresign

    async def set_file(
        self,
        ref: UUID | ModelType,
        file: UploadFile,
        *,
        field: str,
        subdir: str = "",
        filename: str | None = None,
        keep_original_name: bool = False,
    ) -> ModelType:
        """Upload ``file`` and store its key on ``field``, replacing any old one.

        The new object is written **before** the old one is deleted (via
        :meth:`SupportsUpload.replace`), so a failed upload leaves the
        existing file untouched. The entity is re-resolved on the
        repository's session first, so a detached instance (e.g. the
        authenticated user) is safely re-attached before the write.

        Args:
            ref (UUID | ModelType): The entity to update, by id or instance.
            file (UploadFile): The uploaded file.
            field (str): Name of the model attribute holding the storage key.
            subdir (str): Optional key prefix / sub-directory.
            filename (str | None): Explicit final filename. When omitted the
                upload helper derives one.
            keep_original_name (bool): Preserve the upload's original
                filename when ``filename`` is not given.

        Returns:
            ModelType: The persisted entity with the new key on ``field``.

        Raises:
            AppException: ``repository.not_found_exception`` when ``ref`` is
                an id with no matching row.
        """
        entity = await self.repository.resolve(ref)
        old_key: Any = getattr(entity, field)
        new_key = await self.upload_utils.replace(
            old_key,
            file,
            subdir=subdir,
            filename=filename,
            keep_original_name=keep_original_name,
        )
        setattr(entity, field, str(new_key))
        return await self.repository.update(entity)

    async def clear_file(
        self,
        ref: UUID | ModelType,
        *,
        field: str,
    ) -> ModelType:
        """Delete the stored object (if any) and null ``field``.

        A no-op write is avoided when the field is already empty: the
        resolved entity is returned without a commit.

        Args:
            ref (UUID | ModelType): The entity to update, by id or instance.
            field (str): Name of the model attribute holding the storage key.

        Returns:
            ModelType: The entity with ``field`` set to ``None`` (persisted
            only when there was a key to clear).

        Raises:
            AppException: ``repository.not_found_exception`` when ``ref`` is
                an id with no matching row.
        """
        entity = await self.repository.resolve(ref)
        old_key: Any = getattr(entity, field)
        if not old_key:
            return entity
        await self.upload_utils.delete(old_key)
        setattr(entity, field, None)
        return await self.repository.update(entity)

    async def file_url(
        self,
        key: str | None,
        *,
        expires: timedelta = _DEFAULT_URL_TTL,
    ) -> str | None:
        """Return a presigned download URL for ``key``, or ``None`` when empty.

        Args:
            key (str | None): The stored object key (typically the field
                value). ``None`` / empty returns ``None`` so callers can
                forward the result straight into a response schema.
            expires (timedelta): URL lifetime. Defaults to one hour.

        Returns:
            str | None: A temporary download URL, or ``None`` when there is
            no key.
        """
        if not key:
            return None
        return await self.storage.presigned_get_url(key, expires=expires)
