"""Generic async service skeleton wrapping a repository."""

from __future__ import annotations

import inspect
from typing import Any, Generic
from uuid import UUID

from typing_extensions import TypeVar

from tempest_fastapi_sdk.db.repository import BaseRepository
from tempest_fastapi_sdk.schemas.base import BaseSchema

RepositoryT = TypeVar("RepositoryT", bound=BaseRepository[Any])
ResponseT = TypeVar("ResponseT")
UpdateT = TypeVar("UpdateT", bound=BaseSchema, default=BaseSchema)
"""Update-payload schema. Defaults to :class:`BaseSchema`, so a service
declared as ``BaseService[Repo, Resp]`` keeps working; pass a third
argument (``BaseService[Repo, Resp, MyUpdateSchema]``) to type ``update``."""


class BaseService(Generic[RepositoryT, ResponseT, UpdateT]):
    """Thin business-logic layer wrapping a :class:`BaseRepository`.

    The default implementation exposes CRUD pass-through methods that
    delegate to the repository and apply ``map_to_response`` so the
    surface matches what routers/controllers consume. Concrete
    services should override methods that involve orchestration
    (multi-repository writes, external side effects, domain rules);
    pure pass-through methods can be left untouched.

    Generic parameters:
        RepositoryT: The concrete repository class.
        ResponseT: The response schema returned by the service.
        UpdateT: The update-payload schema accepted by :meth:`update`.
            Optional — defaults to :class:`BaseSchema`, so a two-argument
            ``BaseService[Repo, Resp]`` still works; supply it
            (``BaseService[Repo, Resp, MyUpdateSchema]``) to type the
            ``update`` payload precisely.

    Attributes:
        repository (RepositoryT): The repository the service delegates to.
    """

    def __init__(self, repository: RepositoryT) -> None:
        """Initialize the service.

        Args:
            repository (RepositoryT): The repository to delegate to.
        """
        self.repository: RepositoryT = repository

    async def _map_to_response(self, instance: Any) -> ResponseT:
        """Map an ORM instance to a response, awaiting if needed.

        Supports repositories whose ``map_to_response`` is either
        synchronous (the SDK default) or asynchronous (projects that
        need to await nested lookups while mapping). When the call
        returns an awaitable it is awaited; otherwise the value is
        returned as-is.

        Args:
            instance (Any): The ORM instance to map.

        Returns:
            ResponseT: The mapped response schema.
        """
        result = self.repository.map_to_response(instance)
        if inspect.isawaitable(result):
            return await result  # type: ignore[no-any-return]
        return result  # type: ignore[no-any-return]

    async def get_by_id(self, id: UUID) -> ResponseT:
        """Fetch a single record by primary key and map it to a response.

        Args:
            id (UUID): The primary key.

        Returns:
            ResponseT: The mapped response.

        Raises:
            AppException: ``repository.not_found_exception`` when no
                record matches.
        """
        instance = await self.repository.get_by_id(id)
        return await self._map_to_response(instance)

    async def get_or_none(self, filters: dict[str, Any]) -> ResponseT | None:
        """Return the matching record (mapped) or ``None``.

        Args:
            filters (dict[str, Any]): Filter conditions.

        Returns:
            ResponseT | None: The mapped response, or ``None``.
        """
        instance = await self.repository.get_or_none(filters)
        if instance is None:
            return None
        return await self._map_to_response(instance)

    async def list(
        self,
        filters: dict[str, Any] | None = None,
        order_by: Any | None = None,
        ascending: bool = True,
    ) -> list[ResponseT]:
        """Return every matching record mapped to a response.

        Returns ``[]`` when nothing matches, in line with the SDK
        collection convention (never raises ``*NotFoundError`` for
        empty result sets).

        Args:
            filters (dict[str, Any] | None): Filter conditions.
            order_by: A SQLAlchemy column expression to order by.
            ascending (bool): Whether to order ascending.

        Returns:
            list[ResponseT]: The mapped responses.
        """
        instances = await self.repository.list(
            filters=filters,
            order_by=order_by,
            ascending=ascending,
        )
        return [await self._map_to_response(i) for i in instances]

    async def paginate(
        self,
        filters: dict[str, Any] | None = None,
        order_by: str | None = None,
        page: int = 1,
        page_size: int = 20,
        ascending: bool = True,
    ) -> dict[str, Any]:
        """Return an offset page with items mapped to responses.

        Args:
            filters (dict[str, Any] | None): Filter conditions.
            order_by (str | None): Column name to order by.
            page (int): 1-indexed page number.
            page_size (int): Items per page.
            ascending (bool): Whether to order ascending.

        Returns:
            dict[str, Any]: Mapping with ``items`` (mapped),
            ``total``, ``page``, ``size`` and ``pages``.
        """
        result = await self.repository.paginate(
            filters=filters,
            order_by=order_by,
            page=page,
            page_size=page_size,
            ascending=ascending,
        )
        items = [await self._map_to_response(i) for i in result["items"]]
        return {**result, "items": items}

    async def count(self, filters: dict[str, Any] | None = None) -> int:
        """Count the rows matching ``filters``.

        Args:
            filters (dict[str, Any] | None): The filter conditions.

        Returns:
            int: The matching row count.
        """
        return await self.repository.count(filters)

    async def exists(self, filters: dict[str, Any]) -> bool:
        """Whether at least one row matches ``filters``.

        Args:
            filters (dict[str, Any]): Filter conditions.

        Returns:
            bool: ``True`` if at least one row matches.
        """
        return await self.repository.exists(filters)

    async def update(self, id: UUID, data: UpdateT) -> ResponseT:
        """Apply a partial update to a record and map it to a response.

        Fetches the row by primary key, copies the fields present in
        ``data`` onto the instance, persists, and returns the mapped
        response. Because :meth:`BaseSchema.to_dict` drops unset and
        ``None`` fields, only the values the caller actually supplied are
        applied — so the same method serves both full (PUT) and partial
        (PATCH) updates.

        Override this in a concrete service when an update needs
        orchestration (cross-repository writes, domain rules, side
        effects); leave it untouched for plain field copies.

        Args:
            id (UUID): The primary key of the record to update.
            data (UpdateT): The update payload (a :class:`BaseSchema`).
                Fields left unset (or ``None``) are not applied.

        Returns:
            ResponseT: The mapped, updated response.

        Raises:
            AppException: ``repository.not_found_exception`` when no
                record with ``id`` exists.
            ConflictException: On integrity violations while persisting.
        """
        instance = await self.repository.get_by_id(id)
        for field, value in data.to_dict().items():
            setattr(instance, field, value)
        updated = await self.repository.update(instance)
        return await self._map_to_response(updated)

    async def delete(self, id: UUID) -> None:
        """Delete a row by primary key.

        Args:
            id (UUID): The primary key.

        Raises:
            AppException: ``repository.not_found_exception`` when no
                record with ``id`` exists.
        """
        await self.repository.delete(id)


__all__: list[str] = [
    "BaseService",
]
