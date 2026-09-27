"""Controller bases bridging routers and services.

:class:`Controller` is the root of the layer: a controller that
orchestrates several services and has no single-resource CRUD.
:class:`BaseController` specializes it for the common case of one
service with CRUD pass-throughs.
"""

from __future__ import annotations

from typing import Any, Generic, cast
from uuid import UUID

from typing_extensions import TypeVar

from tempest_fastapi_sdk.schemas.base import BaseSchema
from tempest_fastapi_sdk.services.base import BaseService

ServiceT = TypeVar("ServiceT", bound=BaseService[Any, Any, Any])
"""Concrete service class the controller delegates to. The bound spells all
three of :class:`BaseService`'s parameters as ``Any``: ``BaseService[Any, Any]``
is not a partial application — PEP 696 fills the omitted ``UpdateT`` with its
default (:class:`BaseSchema`), and because ``UpdateT`` is invariant that bound
only admits services whose update schema is exactly ``BaseSchema``. Any service
declared as ``BaseService[Repo, Resp, MyUpdateSchema]`` would be rejected."""
ResponseT = TypeVar("ResponseT")
UpdateT = TypeVar("UpdateT", bound=BaseSchema, default=BaseSchema)
"""Update-payload schema for :meth:`BaseController.update`. Defaults to
:class:`BaseSchema`, so ``BaseController[Service, Resp]`` still works;
pass a third argument to type the payload precisely."""


class Controller:
    """Root of the controller layer, for controllers that orchestrate.

    Subclass it when a controller coordinates several services — under
    one lock, one commit, one audit entry — and has no CRUD of a single
    resource to pass through. It ships no methods on purpose: inheriting
    :class:`BaseController` there would hand the router ``get_by_id``,
    ``update`` and ``delete`` methods that mean nothing for that
    controller.

    What it fixes is the convention of the layer, the same one
    :class:`BaseController` follows:

    - services are **injected** through ``__init__`` (built by a FastAPI
      dependency provider, never inside the controller);
    - the controller never touches the database — no session, no
      repository, no engine. Persistence goes through the services it
      receives.

    The convention is not checked at runtime: the constructor belongs to
    the subclass, and telling a service from a repository by annotation
    would need the subclass's forward references resolved.

    Examples:
        >>> from tempest_fastapi_sdk import Controller
        >>> class BillingController(Controller):
        ...     def __init__(self, invoices: object, ledger: object) -> None:
        ...         self.invoices: object = invoices
        ...         self.ledger: object = ledger
        >>> isinstance(BillingController(object(), object()), Controller)
        True
    """


class BaseController(Controller, Generic[ServiceT, ResponseT, UpdateT]):
    """Thin orchestration layer between routers and one CRUD service.

    The CRUD specialization of :class:`Controller`: it takes a single
    service and passes its CRUD methods through. For a controller that
    orchestrates several services without single-resource CRUD,
    subclass :class:`Controller` instead.

    Following the SDK layering rules (router → controller → service →
    repository), controllers are kept present even when no
    orchestration is required so the import graph stays uniform.
    Override methods here when a single endpoint needs to call
    multiple services or apply cross-cutting policy; leave the
    pass-throughs untouched otherwise.

    Generic parameters:
        ServiceT: The concrete service class.
        ResponseT: The response schema returned to the router.
        UpdateT: The update-payload schema accepted by :meth:`update`.
            Optional — defaults to :class:`BaseSchema`, so a two-argument
            ``BaseController[Service, Resp]`` still works; supply it to
            type the ``update`` payload precisely.

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

    async def update(self, id: UUID, data: UpdateT) -> ResponseT:
        """Pass-through to :meth:`BaseService.update`.

        Args:
            id (UUID): The primary key of the record to update.
            data (UpdateT): The update payload (unset fields skipped).

        Returns:
            ResponseT: The mapped, updated response.
        """
        return cast("ResponseT", await self.service.update(id, data))

    async def delete(self, id: UUID) -> None:
        """Pass-through to :meth:`BaseService.delete`.

        Args:
            id (UUID): The primary key.
        """
        await self.service.delete(id)


__all__: list[str] = [
    "BaseController",
    "Controller",
]
