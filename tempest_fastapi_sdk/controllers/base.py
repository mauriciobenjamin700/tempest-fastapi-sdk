"""Generic controller skeleton bridging routers and services."""

from __future__ import annotations

from typing import Any, Generic, TypeVar, cast
from uuid import UUID

from tempest_fastapi_sdk.services.base import BaseService

ServiceT = TypeVar("ServiceT", bound=BaseService[Any, Any])
ResponseT = TypeVar("ResponseT")


class BaseController(Generic[ServiceT, ResponseT]):
    """Thin orchestration layer between routers and services.

    Following the SDK layering rules (router → controller → service →
    repository), controllers are kept present even when no
    orchestration is required so the import graph stays uniform.
    Override methods here when a single endpoint needs to call
    multiple services or apply cross-cutting policy; leave the
    pass-throughs untouched otherwise.

    Generic parameters:
        ServiceT: The concrete service class.
        ResponseT: The response schema returned to the router.

    Attributes:
        service (ServiceT): The service the controller delegates to.
    """

    def __init__(self, service: ServiceT) -> None:
        """Initialize the controller.

        Args:
            service (ServiceT): The service to delegate to.
        """
        self.service: ServiceT = service

    async def get_by_id(self, id: UUID) -> ResponseT:
        """Pass-through to :meth:`BaseService.get_by_id`.

        Args:
            id (UUID): The primary key.

        Returns:
            ResponseT: The mapped response.
        """
        return cast("ResponseT", await self.service.get_by_id(id))

    async def list(
        self,
        filters: dict[str, Any] | None = None,
        order_by: Any | None = None,
        ascending: bool = True,
    ) -> list[ResponseT]:
        """Pass-through to :meth:`BaseService.list`.

        Args:
            filters (dict[str, Any] | None): Filter conditions.
            order_by: A SQLAlchemy column expression.
            ascending (bool): Whether to order ascending.

        Returns:
            list[ResponseT]: The mapped responses.
        """
        return cast(
            "list[ResponseT]",
            await self.service.list(
                filters=filters,
                order_by=order_by,
                ascending=ascending,
            ),
        )

    async def paginate(
        self,
        filters: dict[str, Any] | None = None,
        order_by: str | None = None,
        page: int = 1,
        page_size: int = 20,
        ascending: bool = True,
    ) -> dict[str, Any]:
        """Pass-through to :meth:`BaseService.paginate`.

        Args:
            filters (dict[str, Any] | None): Filter conditions.
            order_by (str | None): Column name to order by.
            page (int): 1-indexed page number.
            page_size (int): Items per page.
            ascending (bool): Whether to order ascending.

        Returns:
            dict[str, Any]: The paginated payload.
        """
        return await self.service.paginate(
            filters=filters,
            order_by=order_by,
            page=page,
            page_size=page_size,
            ascending=ascending,
        )

    async def count(self, filters: dict[str, Any] | None = None) -> int:
        """Pass-through to :meth:`BaseService.count`.

        Args:
            filters (dict[str, Any] | None): The filter conditions.

        Returns:
            int: The matching row count.
        """
        return await self.service.count(filters)

    async def delete(self, id: UUID) -> None:
        """Pass-through to :meth:`BaseService.delete`.

        Args:
            id (UUID): The primary key.
        """
        await self.service.delete(id)


__all__: list[str] = [
    "BaseController",
]
