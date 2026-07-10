"""Multi-tenant repository that scopes every query to one tenant.

In a shared-schema multi-tenant database, every tenant's rows live in
the same table, told apart by a ``tenant_id`` column. The danger is
obvious: forget one ``WHERE tenant_id = ?`` and tenant A reads (or
deletes) tenant B's data. ``TenantScopedRepository`` removes that
footgun — it binds a ``tenant_id`` at construction and injects the
predicate into **every** read, and stamps it onto **every** write, so
individual query sites can't get it wrong.

How the scoping is applied:

* **Reads** — :meth:`_apply_filters` is overridden to always merge the
  tenant predicate, and the filter-taking methods (``list``, ``first``,
  ``count``, ``paginate``, ``cursor_paginate``, ``delete_many``,
  ``exists``, ``get``/``get_or_none``) pass a tenant-bearing filter dict
  so the predicate fires even when the caller passed no filters at all.
* **Writes** — :meth:`add` / :meth:`add_all` stamp ``tenant_id`` onto
  the instance before insert.
* **Id-based mutation** — :meth:`delete` / :meth:`delete_batch` add the
  tenant predicate to the ``DELETE`` so a guessed id from another
  tenant matches nothing. ``soft_delete`` / ``restore`` ride on the
  scoped :meth:`get_by_id`.

!!! warning "Custom raw queries are your responsibility"
    A subclass that builds its own ``select(...)`` without going
    through :meth:`_apply_filters` — or a pre-built ``query=`` passed to
    :meth:`paginate` — is **not** auto-scoped. Add
    ``.where(self.tenant_column == self.tenant_id)`` yourself there.
"""

from __future__ import annotations

import builtins
from typing import Any, TypeVar, cast
from uuid import UUID

from sqlalchemy import CursorResult, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from tempest_fastapi_sdk.db.expressions import Q
from tempest_fastapi_sdk.db.model import BaseModel
from tempest_fastapi_sdk.db.repository import BaseRepository
from tempest_fastapi_sdk.exceptions.base import AppException
from tempest_fastapi_sdk.exceptions.not_found import NotFoundException

TenantModelType = TypeVar("TenantModelType", bound=BaseModel)


class TenantScopedRepository(BaseRepository[TenantModelType]):
    """A :class:`BaseRepository` locked to a single tenant.

    Every read is filtered by ``tenant_id`` and every write is stamped
    with it, so no query site can leak across tenants. Use it exactly
    like :class:`BaseRepository` — the tenant scoping is invisible to
    callers.

    Example:
        >>> repo = TenantScopedRepository(
        ...     session, model=OrderModel, tenant_id=current_tenant
        ... )
        >>> await repo.list()              # WHERE tenant_id = current_tenant
        >>> await repo.add(OrderModel(...)) # tenant_id stamped automatically

    Attributes:
        tenant_id (UUID): The tenant this repository is bound to.
        tenant_field (str): The column name carrying the tenant id.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        model: type[TenantModelType],
        tenant_id: UUID,
        tenant_field: str = "tenant_id",
        not_found_exception: type[AppException] = NotFoundException,
        not_found_message: str | None = None,
        create_conflict_message: str | None = None,
        update_conflict_message: str | None = None,
        bulk_create_conflict_message: str | None = None,
        bulk_update_conflict_message: str | None = None,
    ) -> None:
        """Initialize the tenant-scoped repository.

        Args:
            session (AsyncSession): The async database session.
            model (type[TenantModelType]): The model class. It MUST
                declare the ``tenant_field`` column.
            tenant_id (UUID): The tenant to scope every operation to.
            tenant_field (str): Name of the tenant column. Defaults to
                ``"tenant_id"``.
            not_found_exception (type[AppException]): As in
                :class:`BaseRepository`.
            not_found_message (str | None): As in :class:`BaseRepository`.
            create_conflict_message (str | None): As in
                :class:`BaseRepository`.
            update_conflict_message (str | None): As in
                :class:`BaseRepository`.
            bulk_create_conflict_message (str | None): As in
                :class:`BaseRepository`.
            bulk_update_conflict_message (str | None): As in
                :class:`BaseRepository`.

        Raises:
            TypeError: When ``model`` is not a :class:`BaseModel`
                subclass.
            AttributeError: When ``model`` has no ``tenant_field``
                column.
        """
        super().__init__(
            session,
            model=model,
            not_found_exception=not_found_exception,
            not_found_message=not_found_message,
            create_conflict_message=create_conflict_message,
            update_conflict_message=update_conflict_message,
            bulk_create_conflict_message=bulk_create_conflict_message,
            bulk_update_conflict_message=bulk_update_conflict_message,
        )
        if not isinstance(getattr(model, tenant_field, None), InstrumentedAttribute):
            raise AttributeError(
                f"{model.__name__} has no mapped column {tenant_field!r}; "
                "TenantScopedRepository needs a tenant column to scope by.",
            )
        self.tenant_id: UUID = tenant_id
        self.tenant_field: str = tenant_field

    @property
    def tenant_column(self) -> InstrumentedAttribute[Any]:
        """Return the mapped tenant column for raw query building.

        Returns:
            InstrumentedAttribute[Any]: The model's tenant column, e.g.
            for ``.where(repo.tenant_column == repo.tenant_id)`` in a
            custom query.
        """
        return cast(
            "InstrumentedAttribute[Any]", getattr(self.model, self.tenant_field)
        )

    def _with_tenant(self, filters: dict[str, Any] | None) -> dict[str, Any]:
        """Return ``filters`` with the tenant predicate guaranteed present.

        Args:
            filters (dict[str, Any] | None): The caller's filters.

        Returns:
            dict[str, Any]: A new dict that always carries the tenant id
            (never empty, so the base's ``if filters`` guards fire).
        """
        merged = dict(filters or {})
        merged.setdefault(self.tenant_field, self.tenant_id)
        return merged

    def _apply_filters(self, query: Any, filters: dict[str, Any]) -> Any:
        """Apply filters with the tenant predicate always included.

        Args:
            query: The SQLAlchemy statement to scope.
            filters (dict[str, Any]): The filter conditions.

        Returns:
            The statement with the tenant predicate (and the caller's
            filters) applied.
        """
        return super()._apply_filters(query, self._with_tenant(filters))

    # -- reads that guard on ``if filters`` -------------------------------

    async def get(
        self,
        filters: dict[str, Any],
        for_update: bool = False,
        with_: builtins.list[str] | None = None,
        where: Q | None = None,
    ) -> TenantModelType:
        """See :meth:`BaseRepository.get` — scoped to the tenant."""
        return await super().get(
            self._with_tenant(filters),
            for_update=for_update,
            with_=with_,
            where=where,
        )

    async def get_or_none(
        self,
        filters: dict[str, Any],
        for_update: bool = False,
        with_: builtins.list[str] | None = None,
        where: Q | None = None,
    ) -> TenantModelType | None:
        """See :meth:`BaseRepository.get_or_none` — scoped to the tenant."""
        return await super().get_or_none(
            self._with_tenant(filters),
            for_update=for_update,
            with_=with_,
            where=where,
        )

    async def exists(self, filters: dict[str, Any], where: Q | None = None) -> bool:
        """See :meth:`BaseRepository.exists` — scoped to the tenant."""
        return await super().exists(self._with_tenant(filters), where=where)

    async def first(
        self,
        filters: dict[str, Any] | None = None,
        order_by: Any | None = None,
        ascending: bool = True,
        with_: builtins.list[str] | None = None,
        where: Q | None = None,
    ) -> TenantModelType | None:
        """See :meth:`BaseRepository.first` — scoped to the tenant."""
        return await super().first(
            self._with_tenant(filters),
            order_by=order_by,
            ascending=ascending,
            with_=with_,
            where=where,
        )

    async def list(
        self,
        filters: dict[str, Any] | None = None,
        order_by: Any | None = None,
        ascending: bool = True,
        with_: builtins.list[str] | None = None,
        where: Q | None = None,
    ) -> builtins.list[TenantModelType]:
        """See :meth:`BaseRepository.list` — scoped to the tenant."""
        return await super().list(
            self._with_tenant(filters),
            order_by=order_by,
            ascending=ascending,
            with_=with_,
            where=where,
        )

    async def count(
        self, filters: dict[str, Any] | None = None, where: Q | None = None
    ) -> int:
        """See :meth:`BaseRepository.count` — scoped to the tenant."""
        return await super().count(self._with_tenant(filters), where=where)

    async def paginate(
        self,
        filters: dict[str, Any] | None = None,
        order_by: str | None = None,
        page: int = 1,
        page_size: int = 20,
        ascending: bool = True,
        query: Any | None = None,
        where: Q | None = None,
    ) -> dict[str, Any]:
        """See :meth:`BaseRepository.paginate` — scoped to the tenant.

        When a pre-built ``query`` is passed, scope it yourself with
        ``.where(self.tenant_column == self.tenant_id)`` — the override
        only auto-scopes the default ``select(self.model)`` path.
        """
        return await super().paginate(
            self._with_tenant(filters),
            order_by=order_by,
            page=page,
            page_size=page_size,
            ascending=ascending,
            query=query,
            where=where,
        )

    async def cursor_paginate(
        self,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 20,
        order_by: str = "created_at",
        ascending: bool = False,
        query: Any | None = None,
    ) -> dict[str, Any]:
        """See :meth:`BaseRepository.cursor_paginate` — scoped to the tenant.

        The tenant predicate is always appended via ``filters``, so it
        applies even when a pre-built ``query`` is passed. As with
        :meth:`paginate`, joins/predicates inside that ``query`` are
        the caller's responsibility.
        """
        return await super().cursor_paginate(
            self._with_tenant(filters),
            cursor=cursor,
            limit=limit,
            order_by=order_by,
            ascending=ascending,
            query=query,
        )

    async def delete_many(self, filters: dict[str, Any], where: Q | None = None) -> int:
        """See :meth:`BaseRepository.delete_many` — scoped to the tenant.

        Because the tenant predicate is always added, an empty
        ``filters`` deletes only **this tenant's** rows, never the whole
        table.
        """
        return await super().delete_many(self._with_tenant(filters), where=where)

    # -- writes / id-based mutation --------------------------------------

    async def add(self, model: TenantModelType) -> TenantModelType:
        """Insert ``model`` after stamping the tenant id onto it.

        Args:
            model (TenantModelType): The instance to insert.

        Returns:
            TenantModelType: The inserted, refreshed instance.
        """
        setattr(model, self.tenant_field, self.tenant_id)
        return await super().add(model)

    async def add_all(
        self, models: builtins.list[TenantModelType]
    ) -> builtins.list[TenantModelType]:
        """Insert several models after stamping the tenant id onto each.

        Args:
            models (list[TenantModelType]): The instances to insert.

        Returns:
            list[TenantModelType]: The inserted, refreshed instances.
        """
        for model in models:
            setattr(model, self.tenant_field, self.tenant_id)
        return await super().add_all(models)

    async def delete(self, id: UUID) -> None:
        """Delete a row by id **within this tenant**.

        An id belonging to another tenant matches nothing and raises
        the not-found exception, so it is indistinguishable from a row
        that never existed — no cross-tenant probing.

        Args:
            id (UUID): The primary key.

        Raises:
            AppException: ``self.not_found_exception`` when no row in
                this tenant has that id.
        """
        try:
            query = delete(self.model).where(
                self.model.id == id,
                self.tenant_column == self.tenant_id,
            )
            result = cast(CursorResult[Any], await self.session.execute(query))
            if result.rowcount == 0:
                self._raise_not_found()
            await self.session.commit()
        except AppException:
            raise
        except Exception:
            await self.session.rollback()
            raise

    async def delete_batch(self, ids: builtins.list[UUID]) -> int:
        """Delete several rows by id **within this tenant**.

        Args:
            ids (list[UUID]): The primary keys.

        Returns:
            int: The number of rows deleted (rows from other tenants are
            never touched).
        """
        try:
            query = delete(self.model).where(
                self.model.id.in_(ids),
                self.tenant_column == self.tenant_id,
            )
            result = cast(CursorResult[Any], await self.session.execute(query))
            await self.session.commit()
            return result.rowcount or 0
        except Exception:
            await self.session.rollback()
            raise


__all__: list[str] = [
    "TenantScopedRepository",
]
