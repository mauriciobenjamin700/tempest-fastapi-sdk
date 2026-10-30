"""Multi-tenant repository that scopes every query to one tenant.

In a shared-schema multi-tenant database, every tenant's rows live in
the same table, told apart by a ``tenant_id`` column. The danger is
obvious: forget one ``WHERE tenant_id = ?`` and tenant A reads (or
deletes) tenant B's data. ``TenantScopedRepository`` removes that
footgun — it binds a ``tenant_id`` at construction and injects the
predicate into **every** read, and stamps it onto **every** write, so
individual query sites can't get it wrong.

How the scoping is applied:

* **Reads** — :meth:`_apply_filters` is overridden to always AND
  ``tenant_column == tenant_id`` onto the statement, and every
  filter-taking method (``list``, ``first``, ``count``, ``paginate``,
  ``cursor_paginate``, ``delete_many``, ``exists``,
  ``get``/``get_or_none``, ``search``/``full_text_search``,
  ``bulk_update``/``update_returning``) reaches it even when the caller
  passed no filters at all. The predicate is ANDed, never merged into
  the caller's dict, so a caller filter on the tenant column can only
  narrow the result: ``{"tenant_id": other}`` matches nothing.
* **Inserts** — :meth:`add`, :meth:`add_all`,
  :meth:`save_with_outbox` and :meth:`add_audited` stamp ``tenant_id``
  onto the instance, and :meth:`bulk_create_values` /
  :meth:`bulk_upsert` stamp it onto every row mapping, overwriting any
  tenant value the caller supplied.
* **Upserts** — :meth:`bulk_upsert` with ``on_conflict="update"`` only
  rewrites a conflicting row that already belongs to this tenant; a
  conflict with another tenant's row leaves that row untouched.
* **Updates** — :meth:`bulk_update` / :meth:`update_returning` refuse
  ``values`` that set the tenant column to another tenant, and
  :meth:`update`, :meth:`update_many`, :meth:`update_audited` and
  :meth:`delete_audited` refuse an instance that belongs to, or was
  moved to, another tenant.
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
from collections.abc import Sequence
from typing import Any, TypeVar, cast
from uuid import UUID

from sqlalchemy import CursorResult, delete, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from tempest_fastapi_sdk.db.expressions import WhereClause
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
        """Return ``filters`` guaranteed non-empty, so scoping always runs.

        The base methods call :meth:`_apply_filters` only when
        ``filters`` is truthy, so an empty dict would skip the scope.
        This returns a copy that carries the tenant key when the caller
        did not pass one. It is **not** what enforces the scope: a
        caller key on the tenant column is kept as given and
        :meth:`_apply_filters` ANDs the bound tenant on top of it, so it
        can only narrow the result.

        Args:
            filters (dict[str, Any] | None): The caller's filters.

        Returns:
            dict[str, Any]: A new, never-empty dict.
        """
        merged = dict(filters or {})
        merged.setdefault(self.tenant_field, self.tenant_id)
        return merged

    def _apply_filters(self, query: Any, filters: dict[str, Any]) -> Any:
        """Apply filters and AND the bound tenant predicate onto the statement.

        Args:
            query: The SQLAlchemy statement to scope.
            filters (dict[str, Any]): The filter conditions.

        Returns:
            The statement with the caller's filters and
            ``tenant_column == tenant_id`` applied.
        """
        scoped = super()._apply_filters(query, self._with_tenant(filters))
        return scoped.where(self.tenant_column == self.tenant_id)

    async def _run_search(
        self,
        condition: ColumnElement[bool] | None,
        *,
        rank: ColumnElement[Any] | None,
        filters: dict[str, Any] | None,
        where: WhereClause | None,
        order_by: Any | None,
        ascending: bool,
        with_: builtins.list[str] | None,
        limit: int | None,
    ) -> builtins.list[TenantModelType]:
        """Run :meth:`search` / :meth:`full_text_search` scoped to the tenant.

        The base applies filters only when they are truthy, so a search
        with just a term would skip :meth:`_apply_filters`; this passes
        a never-empty filter dict instead.

        Args:
            condition (ColumnElement[bool] | None): The text condition.
            rank (ColumnElement[Any] | None): Relevance expression.
            filters (dict[str, Any] | None): The caller's extra filters.
            where (WhereClause | None): Extra condition ANDed in.
            order_by (Any | None): Explicit ordering.
            ascending (bool): Direction for ``order_by``.
            with_ (builtins.list[str] | None): Relationship paths to
                eager-load.
            limit (int | None): Cap on rows returned.

        Returns:
            builtins.list[TenantModelType]: The matching rows of this
            tenant.
        """
        return await super()._run_search(
            condition,
            rank=rank,
            filters=self._with_tenant(filters),
            where=where,
            order_by=order_by,
            ascending=ascending,
            with_=with_,
            limit=limit,
        )

    def _prepare_insert(self, model: TenantModelType) -> None:
        """Stamp the bound tenant id onto an instance before it is inserted.

        Args:
            model (TenantModelType): The instance about to be inserted.
        """
        setattr(model, self.tenant_field, self.tenant_id)

    def _prepare_insert_rows(
        self, rows: builtins.list[dict[str, Any]]
    ) -> builtins.list[dict[str, Any]]:
        """Return copies of ``rows`` stamped with the bound tenant id.

        A tenant value already present in a row is overwritten, the same
        way :meth:`add` overwrites the attribute.

        Args:
            rows (builtins.list[dict[str, Any]]): The caller's mappings.

        Returns:
            builtins.list[dict[str, Any]]: New mappings carrying
            ``tenant_field = tenant_id``.
        """
        return [{**row, self.tenant_field: self.tenant_id} for row in rows]

    def _upsert_conflict_where(self) -> ColumnElement[bool]:
        """Limit an upsert's ``DO UPDATE`` to rows of the bound tenant.

        A conflict with a row of another tenant (a shared primary key,
        say) then updates nothing instead of rewriting, and re-tenanting,
        that row.

        Returns:
            ColumnElement[bool]: ``tenant_column == tenant_id``.
        """
        return cast("ColumnElement[bool]", self.tenant_column == self.tenant_id)

    async def _check_instance_write(self, model: TenantModelType) -> None:
        """Refuse to update or delete an instance outside the bound tenant.

        Reads the tenant value the instance was loaded with and any
        value assigned since, from the attribute history, without
        touching the database. When the attribute was never loaded, the
        stored value is read with one ``SELECT``.

        Args:
            model (TenantModelType): The instance about to be written.

        Raises:
            AppException: ``self.not_found_exception`` when the stored
                row belongs to another tenant.
            ValueError: When the instance's tenant column was changed to
                another tenant.
        """
        history = inspect(model).attrs[self.tenant_field].history
        stored = [*history.unchanged, *history.deleted]
        if not stored and model.id is not None:
            persisted = await self.session.scalar(
                select(self.tenant_column).where(self.model.id == model.id)
            )
            stored = [] if persisted is None else [persisted]
        if any(value != self.tenant_id for value in stored):
            self._raise_not_found()
        if any(value != self.tenant_id for value in history.added):
            raise ValueError(
                f"{self.model.__name__}.{self.tenant_field} cannot be moved to "
                "another tenant through a TenantScopedRepository.",
            )

    async def _execute_update(
        self,
        filters: dict[str, Any],
        values: dict[str, Any],
        where: WhereClause | None,
        returning: Sequence[Any],
        *,
        operation: str,
    ) -> tuple[int, builtins.list[Any]]:
        """Run a scoped ``UPDATE``, refusing to move rows to another tenant.

        Args:
            filters (dict[str, Any]): The caller's filters; the tenant
                predicate is ANDed on by :meth:`_apply_filters`.
            values (dict[str, Any]): Column-value pairs to set.
            where (WhereClause | None): Extra condition, or ``None``.
            returning (Sequence[Any]): Mapped columns to read back.
            operation (str): Method name, for the log line.

        Returns:
            tuple[int, builtins.list[Any]]: The affected row count and the
            returned rows.

        Raises:
            ValueError: When ``values`` sets the tenant column to anything
                other than the bound tenant id.
        """
        if self.tenant_field in values:
            value = values[self.tenant_field]
            if not isinstance(value, UUID) or value != self.tenant_id:
                raise ValueError(
                    f"{self.model.__name__}.{self.tenant_field} cannot be set "
                    "to another tenant through a TenantScopedRepository.",
                )
        return await super()._execute_update(
            filters,
            values,
            where,
            returning,
            operation=operation,
        )

    # -- reads that guard on ``if filters`` -------------------------------

    async def get(
        self,
        filters: dict[str, Any],
        for_update: bool = False,
        with_: builtins.list[str] | None = None,
        where: WhereClause | None = None,
    ) -> TenantModelType:
        """See :meth:`BaseRepository.get` — scoped to the tenant.

        Args:
            filters (dict[str, Any]): Column name to the value it must match, ANDed
                with the tenant scope, e.g.
                ``{"user_id": 1234, "is_active": True}``. A list is ``IN``, ``None``
                is ``IS NULL``, and a ``<column>__<op>`` key applies an operator
                (``{"price__gte": 10}``); every convention is listed on
                :class:`~tempest_fastapi_sdk.BaseRepository`. An unknown column or
                operator raises ``UnknownFilterKeyException``.
            for_update (bool): Whether to lock the selected rows.
            with_ (builtins.list[str] | None): Relationship names to eager-
                load.
            where (WhereClause | None): Extra filter narrowing the tenant-scoped query.

        Returns:
            TenantModelType: The row.
        """
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
        where: WhereClause | None = None,
    ) -> TenantModelType | None:
        """See :meth:`BaseRepository.get_or_none` — scoped to the tenant.

        Args:
            filters (dict[str, Any]): Column name to the value it must match, ANDed
                with the tenant scope, e.g.
                ``{"user_id": 1234, "is_active": True}``. A list is ``IN``, ``None``
                is ``IS NULL``, and a ``<column>__<op>`` key applies an operator
                (``{"price__gte": 10}``); every convention is listed on
                :class:`~tempest_fastapi_sdk.BaseRepository`. An unknown column or
                operator raises ``UnknownFilterKeyException``.
            for_update (bool): Whether to lock the selected rows.
            with_ (builtins.list[str] | None): Relationship names to eager-
                load.
            where (WhereClause | None): Extra filter narrowing the tenant-scoped query.

        Returns:
            TenantModelType | None: The row, or ``None`` when absent or owned
                by another tenant.
        """
        return await super().get_or_none(
            self._with_tenant(filters),
            for_update=for_update,
            with_=with_,
            where=where,
        )

    async def exists(
        self, filters: dict[str, Any], where: WhereClause | None = None
    ) -> bool:
        """See :meth:`BaseRepository.exists` — scoped to the tenant.

        Args:
            filters (dict[str, Any]): Column name to the value it must match, ANDed
                with the tenant scope, e.g.
                ``{"user_id": 1234, "is_active": True}``. A list is ``IN``, ``None``
                is ``IS NULL``, and a ``<column>__<op>`` key applies an operator
                (``{"price__gte": 10}``); every convention is listed on
                :class:`~tempest_fastapi_sdk.BaseRepository`. An unknown column or
                operator raises ``UnknownFilterKeyException``.
            where (WhereClause | None): Extra filter narrowing the tenant-scoped query.

        Returns:
            bool: Whether any row matched.
        """
        return await super().exists(self._with_tenant(filters), where=where)

    async def first(
        self,
        filters: dict[str, Any] | None = None,
        order_by: Any | None = None,
        ascending: bool = True,
        with_: builtins.list[str] | None = None,
        where: WhereClause | None = None,
    ) -> TenantModelType | None:
        """See :meth:`BaseRepository.first` — scoped to the tenant.

        Args:
            filters (dict[str, Any] | None): Column name to the value it must match,
                ANDed with the tenant scope, e.g.
                ``{"user_id": 1234, "is_active": True}``. A list is ``IN``, ``None``
                is ``IS NULL``, and a ``<column>__<op>`` key applies an operator
                (``{"price__gte": 10}``); every convention is listed on
                :class:`~tempest_fastapi_sdk.BaseRepository`. An unknown column or
                operator raises ``UnknownFilterKeyException``.
            order_by (Any | None): Column to sort by; ``None`` keeps the
                repository default.
            ascending (bool): Whether to sort ascending.
            with_ (builtins.list[str] | None): Relationship names to eager-
                load.
            where (WhereClause | None): Extra filter narrowing the tenant-scoped query.

        Returns:
            TenantModelType | None: The row, or ``None`` when absent or owned
                by another tenant.
        """
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
        where: WhereClause | None = None,
    ) -> builtins.list[TenantModelType]:
        """See :meth:`BaseRepository.list` — scoped to the tenant.

        Args:
            filters (dict[str, Any] | None): Column name to the value it must match,
                ANDed with the tenant scope, e.g.
                ``{"user_id": 1234, "is_active": True}``. A list is ``IN``, ``None``
                is ``IS NULL``, and a ``<column>__<op>`` key applies an operator
                (``{"price__gte": 10}``); every convention is listed on
                :class:`~tempest_fastapi_sdk.BaseRepository`. An unknown column or
                operator raises ``UnknownFilterKeyException``.
            order_by (Any | None): Column to sort by; ``None`` keeps the
                repository default.
            ascending (bool): Whether to sort ascending.
            with_ (builtins.list[str] | None): Relationship names to eager-
                load.
            where (WhereClause | None): Extra filter narrowing the tenant-scoped query.

        Returns:
            builtins.list[TenantModelType]: Matching rows, scoped to the
                tenant.
        """
        return await super().list(
            self._with_tenant(filters),
            order_by=order_by,
            ascending=ascending,
            with_=with_,
            where=where,
        )

    async def count(
        self, filters: dict[str, Any] | None = None, where: WhereClause | None = None
    ) -> int:
        """See :meth:`BaseRepository.count` — scoped to the tenant.

        Args:
            filters (dict[str, Any] | None): Column name to the value it must match,
                ANDed with the tenant scope, e.g.
                ``{"user_id": 1234, "is_active": True}``. A list is ``IN``, ``None``
                is ``IS NULL``, and a ``<column>__<op>`` key applies an operator
                (``{"price__gte": 10}``); every convention is listed on
                :class:`~tempest_fastapi_sdk.BaseRepository`. An unknown column or
                operator raises ``UnknownFilterKeyException``.
            where (WhereClause | None): Extra filter narrowing the tenant-scoped query.

        Returns:
            int: How many rows matched.
        """
        return await super().count(self._with_tenant(filters), where=where)

    async def paginate(
        self,
        filters: dict[str, Any] | None = None,
        order_by: str | None = None,
        page: int = 1,
        page_size: int = 20,
        ascending: bool = True,
        query: Any | None = None,
        where: WhereClause | None = None,
    ) -> dict[str, Any]:
        """See :meth:`BaseRepository.paginate` — scoped to the tenant.

        When a pre-built ``query`` is passed, scope it yourself with
        ``.where(self.tenant_column == self.tenant_id)`` — the override
        only auto-scopes the default ``select(self.model)`` path.

        Args:
            filters (dict[str, Any] | None): Column name to the value it must match,
                ANDed with the tenant scope, e.g.
                ``{"user_id": 1234, "is_active": True}``. A list is ``IN``, ``None``
                is ``IS NULL``, and a ``<column>__<op>`` key applies an operator
                (``{"price__gte": 10}``); every convention is listed on
                :class:`~tempest_fastapi_sdk.BaseRepository`. An unknown column or
                operator raises ``UnknownFilterKeyException``.
            order_by (str | None): Column to sort by; ``None`` keeps the
                repository default.
            page (int): 1-indexed page number.
            page_size (int): Rows per page.
            ascending (bool): Whether to sort ascending.
            query (Any | None): A pre-built query to scope; ``None`` starts a
                fresh one.
            where (WhereClause | None): Extra filter narrowing the tenant-scoped query.

        Returns:
            dict[str, Any]: The pagination envelope.
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

        Args:
            filters (dict[str, Any] | None): Column name to the value it must match,
                ANDed with the tenant scope, e.g.
                ``{"user_id": 1234, "is_active": True}``. A list is ``IN``, ``None``
                is ``IS NULL``, and a ``<column>__<op>`` key applies an operator
                (``{"price__gte": 10}``); every convention is listed on
                :class:`~tempest_fastapi_sdk.BaseRepository`. An unknown column or
                operator raises ``UnknownFilterKeyException``.
            cursor (str | None): Opaque cursor from a previous page.
            limit (int): Maximum rows to return.
            order_by (str): Column to sort by; ``None`` keeps the repository
                default.
            ascending (bool): Whether to sort ascending.
            query (Any | None): A pre-built query to scope; ``None`` starts a
                fresh one.

        Returns:
            dict[str, Any]: The pagination envelope.
        """
        return await super().cursor_paginate(
            self._with_tenant(filters),
            cursor=cursor,
            limit=limit,
            order_by=order_by,
            ascending=ascending,
            query=query,
        )

    async def delete_many(
        self, filters: dict[str, Any], where: WhereClause | None = None
    ) -> int:
        """See :meth:`BaseRepository.delete_many` — scoped to the tenant.

        Because the tenant predicate is always added, an empty
        ``filters`` deletes only **this tenant's** rows, never the whole
        table.

        Args:
            filters (dict[str, Any]): Column name to the value it must match, ANDed
                with the tenant scope, e.g.
                ``{"user_id": 1234, "is_active": True}``. A list is ``IN``, ``None``
                is ``IS NULL``, and a ``<column>__<op>`` key applies an operator
                (``{"price__gte": 10}``); every convention is listed on
                :class:`~tempest_fastapi_sdk.BaseRepository`. An unknown column or
                operator raises ``UnknownFilterKeyException``.
            where (WhereClause | None): Extra filter narrowing the tenant-scoped query.

        Returns:
            int: How many rows matched.
        """
        return await super().delete_many(self._with_tenant(filters), where=where)

    # -- id-based mutation ----------------------------------------------

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
