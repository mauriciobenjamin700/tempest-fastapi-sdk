"""Generic async service skeleton wrapping a repository."""

from __future__ import annotations

import inspect
from typing import Any, Generic, TypeVar
from uuid import UUID

from tempest_fastapi_sdk.db.repository import BaseRepository

RepositoryT = TypeVar("RepositoryT", bound=BaseRepository[Any])
ResponseT = TypeVar("ResponseT")


class BaseService(Generic[RepositoryT, ResponseT]):
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
