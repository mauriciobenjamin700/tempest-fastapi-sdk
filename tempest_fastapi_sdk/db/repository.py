"""Generic async repository with CRUD + filter + pagination primitives."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, ClassVar, Generic, List, TypeVar, cast
from uuid import UUID

from sqlalchemy import CursorResult, Select, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.db.model import BaseModel
from tempest_fastapi_sdk.exceptions.base import AppException
from tempest_fastapi_sdk.exceptions.conflict import ConflictException
from tempest_fastapi_sdk.exceptions.not_found import NotFoundException

logger = logging.getLogger(__name__)

ModelType = TypeVar("ModelType", bound=BaseModel)


def _escape_like(value: str) -> str:
    """Escape LIKE/ILIKE wildcards so user input is treated literally.

    Backslash is escaped first to avoid double-escaping the others.

    Args:
        value (str): The raw user-supplied search term.

    Returns:
        str: The same string with ``\\``, ``%`` and ``_`` escaped.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class BaseRepository(Generic[ModelType]):
    """Base async repository with generic CRUD operations.

    Subclasses MUST set the ``model`` class attribute and SHOULD
    override ``not_found_exception`` with a domain-specific subclass
    of :class:`NotFoundException`. The default filter logic supports
    equality on every column plus the following conventions:

    * ``name`` (string) → case-insensitive ``ILIKE %value%`` search.
    * ``bool`` values → ``.is_(value)`` (correct SQL boolean check).
    * ``list`` values → ``.in_(values)`` membership.
    * ``date`` values → ``func.date(column) == value`` whole-day match.
    * ``start_in`` / ``end_in`` (date) → range filter against the
      model's ``date`` column when present, falling back to
      ``created_at``.

    All error messages can be customized per repository instance via
    the constructor kwargs (``not_found_message``,
    ``create_conflict_message``, etc.); when omitted, sensible defaults
    derived from ``self.model.__name__`` are used.

    The same three abstract mappers ``map_to_schema`` / ``map_to_model``
    / ``map_to_response`` are kept so concrete repositories own the
    translation between ORM rows and DTOs.

    Attributes:
        model (type[ModelType]): The SQLAlchemy model class operated
            on. MUST be set by subclasses.
        not_found_exception (type[AppException]): The exception class
            raised when single-record lookups miss. Defaults to
            :class:`NotFoundException`; override in subclasses for
            domain-specific 404 messages.
        session (AsyncSession): The async database session.
    """

    model: type[ModelType]
    not_found_exception: ClassVar[type[AppException]] = NotFoundException

    def __init__(
        self,
        session: AsyncSession,
        *,
        not_found_message: str | None = None,
        create_conflict_message: str | None = None,
        update_conflict_message: str | None = None,
        bulk_create_conflict_message: str | None = None,
        bulk_update_conflict_message: str | None = None,
    ) -> None:
        """Initialize the repository.

        Every ``*_message`` kwarg is optional — when not provided, the
        repository falls back to a generic message derived from the
        model class name (e.g. ``"User not found"``,
        ``"Conflict creating User"``).

        Args:
            session (AsyncSession): The async database session.
            not_found_message (str | None): Message used when ``get``,
                ``get_by_id``, ``delete``, ``soft_delete`` or
                ``restore`` find no matching record.
            create_conflict_message (str | None): Message used when
                ``add`` raises ``IntegrityError``.
            update_conflict_message (str | None): Message used when
                ``update`` raises ``IntegrityError``.
            bulk_create_conflict_message (str | None): Message used
                when ``add_all`` raises ``IntegrityError``.
            bulk_update_conflict_message (str | None): Message used
                when ``update_many`` or ``bulk_update`` raises
                ``IntegrityError``.
        """
        self.session: AsyncSession = session
        name = self.model.__name__
        self._not_found_message: str = not_found_message or f"{name} not found"
        self._create_conflict_message: str = (
            create_conflict_message or f"Conflict creating {name}"
        )
        self._update_conflict_message: str = (
            update_conflict_message or f"Conflict updating {name}"
        )
        self._bulk_create_conflict_message: str = (
            bulk_create_conflict_message or f"Conflict creating {name} batch"
        )
        self._bulk_update_conflict_message: str = (
            bulk_update_conflict_message or f"Conflict updating {name} batch"
        )

    def _apply_filters(
        self,
        query: Any,
        filters: dict[str, Any],
    ) -> Any:
        """Apply filter conditions to a select/delete/update query.

        See the class docstring for the recognized conventions.

        Args:
            query: The SQLAlchemy ``Select``, ``Delete`` or
                ``Update`` to mutate.
            filters (dict[str, Any]): The column-value pairs to apply.

        Returns:
            The same query with the additional ``WHERE`` clauses.
        """
        for field, value in filters.items():
            if value is None:
                continue

            if field == "start_in" and isinstance(value, date):
                column = getattr(
                    self.model,
                    "date",
                    getattr(self.model, "created_at", None),
                )
                if column is not None:
                    query = query.where(func.date(column) >= value)
                continue

            if field == "end_in" and isinstance(value, date):
                column = getattr(
                    self.model,
                    "date",
                    getattr(self.model, "created_at", None),
                )
                if column is not None:
                    query = query.where(func.date(column) <= value)
                continue

            column = getattr(self.model, field, None)
            if column is None:
                continue

            if field == "name" and isinstance(value, str):
                pattern = f"%{_escape_like(value)}%"
                query = query.where(column.ilike(pattern, escape="\\"))
            elif isinstance(value, bool):
                query = query.where(column.is_(value))
            elif isinstance(value, list):
                query = query.where(column.in_(value))
            elif isinstance(value, date):
                query = query.where(func.date(column) == value)
            else:
                query = query.where(column == value)
        return query

    def _raise_not_found(self) -> None:
        """Raise the configured not-found exception with the resolved message.

        Raises:
            AppException: Always — ``self.not_found_exception``
                instantiated with ``self._not_found_message``.
        """
        raise self.not_found_exception(message=self._not_found_message)

    async def get(
        self,
        filters: dict[str, Any],
        for_update: bool = False,
    ) -> ModelType:
        """Return the single record matching ``filters``.

        Args:
            filters (dict[str, Any]): The column-value pairs.
            for_update (bool): Whether to acquire a row-level lock
                (``SELECT ... FOR UPDATE``). Defaults to ``False``.

        Returns:
            ModelType: The matching row.

        Raises:
            AppException: ``self.not_found_exception`` with the
                configured ``not_found_message`` if no record
                matches the filters.
        """
        instance = await self.get_or_none(filters, for_update=for_update)
        if instance is None:
            self._raise_not_found()
        return cast(ModelType, instance)

    async def get_or_none(
        self,
        filters: dict[str, Any],
        for_update: bool = False,
    ) -> ModelType | None:
        """Return the single record matching ``filters`` or ``None``.

        Unlike :meth:`get`, never raises when nothing matches.

        Args:
            filters (dict[str, Any]): The column-value pairs.
            for_update (bool): Whether to acquire a row-level lock.

        Returns:
            ModelType | None: The matching row, or ``None``.
        """
        query = select(self.model)
        query = self._apply_filters(query, filters)
        if for_update:
            query = query.with_for_update()
        result = await self.session.execute(query)
        instance = result.unique().scalars().one_or_none()
        return cast("ModelType | None", instance)

    async def get_by_id(
        self,
        id: UUID,
        for_update: bool = False,
    ) -> ModelType:
        """Return the record with the given primary key.

        Args:
            id (UUID): The primary key to look up.
            for_update (bool): Whether to acquire a row-level lock.

        Returns:
            ModelType: The matching row.

        Raises:
            AppException: ``self.not_found_exception`` if no record
                with ``id`` exists.
        """
        return await self.get({"id": id}, for_update=for_update)

    async def exists(self, filters: dict[str, Any]) -> bool:
        """Return whether at least one row matches ``filters``.

        Executes a ``SELECT 1 ... LIMIT 1`` so the row is never
        fully loaded.

        Args:
            filters (dict[str, Any]): The filter conditions.

        Returns:
            bool: ``True`` if at least one row matches.
        """
        query = select(self.model.id)
        query = self._apply_filters(query, filters)
        query = query.limit(1)
        result = await self.session.execute(query)
        return result.scalar() is not None

    async def first(
        self,
        filters: dict[str, Any] | None = None,
        order_by: Any | None = None,
        ascending: bool = True,
    ) -> ModelType | None:
        """Return the first matching row or ``None``.

        Convenience wrapper around :meth:`list` for cases that only
        need one row but want to control ordering.

        Args:
            filters (dict[str, Any] | None): The filter conditions.
            order_by: A SQLAlchemy column expression to order by.
                ``None`` keeps insertion order.
            ascending (bool): Whether to order ascending.

        Returns:
            ModelType | None: The first matching row, or ``None``.
        """
        query = select(self.model)
        if filters:
            query = self._apply_filters(query, filters)
        if order_by is not None:
            query = query.order_by(order_by if ascending else order_by.desc())
        query = query.limit(1)
        result = await self.session.execute(query)
        instance = result.unique().scalars().one_or_none()
        return instance

    async def list(
        self,
        filters: dict[str, Any] | None = None,
        order_by: Any | None = None,
        ascending: bool = True,
    ) -> list[ModelType]:
        """Return every record matching ``filters``.

        Returns ``[]`` (never raises) when nothing matches, in line
        with the SDK collection convention.

        Args:
            filters (dict[str, Any] | None): The filter conditions.
            order_by: A SQLAlchemy column expression (e.g.
                ``MyModel.name``). ``None`` keeps insertion order.
            ascending (bool): Whether to order ascending. Ignored
                when ``order_by`` is ``None``.

        Returns:
            list[ModelType]: The matching rows.
        """
        query = select(self.model)

        if filters:
            query = self._apply_filters(query, filters)

        if order_by is not None:
            query = query.order_by(order_by if ascending else order_by.desc())

        result = await self.session.execute(query)
        return list(result.unique().scalars().all())

    async def paginate(
        self,
        filters: dict[str, Any] | None = None,
        order_by: str | None = None,
        page: int = 1,
        page_size: int = 20,
        ascending: bool = True,
        query: Select[Any] | None = None,
    ) -> dict[str, Any]:
        """Return a single page of records matching ``filters``.

        When ``order_by`` is ``None``, falls back to
        ``self.model.created_at.desc()``. The total count is computed
        from the same filtered (and possibly joined) query, so custom
        queries with joins still report a correct total.

        Args:
            filters (dict[str, Any] | None): Filter conditions.
            order_by (str | None): Column name to order by, or
                ``None`` to fall back to ``created_at desc``.
            page (int): The 1-indexed page number.
            page_size (int): The number of items per page.
            ascending (bool): Whether to order ascending. Ignored
                when ``order_by`` is ``None``.
            query (Select[Any] | None): A pre-built ``Select``; if
                ``None``, defaults to ``select(self.model)``.

        Returns:
            dict[str, Any]: A mapping with keys ``items``, ``total``,
            ``page``, ``size``, ``pages``.
        """
        if query is None:
            query = select(self.model)

        if filters:
            query = self._apply_filters(query, filters)

        if order_by is None:
            query = query.order_by(self.model.created_at.desc())
        else:
            column = getattr(self.model, order_by)
            query = query.order_by(column if ascending else column.desc())

        count_query = select(func.count()).select_from(query.subquery())

        total_result = await self.session.execute(count_query)
        total = total_result.scalar() or 0

        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)

        result = await self.session.execute(query)
        items = list(result.unique().scalars().all())

        pages = (total + page_size - 1) // page_size

        return {
            "items": items,
            "total": total,
            "page": page,
            "size": page_size,
            "pages": pages,
        }

    async def add(self, model: ModelType) -> ModelType:
        """Insert ``model`` into the database.

        Args:
            model (ModelType): The instance to insert.

        Returns:
            ModelType: The same instance after ``refresh`` so the
            ``id`` and timestamp columns are populated.

        Raises:
            ConflictException: On integrity violations (unique
                constraint, FK error, etc.).
        """
        try:
            self.session.add(model)
            await self.session.commit()
            await self.session.refresh(model)
            return model
        except IntegrityError as exc:
            await self.session.rollback()
            logger.warning(
                "IntegrityError on %s.add: %s", self.model.__name__, exc.orig
            )
            raise ConflictException(
                message=self._create_conflict_message,
            ) from exc
        except Exception:
            await self.session.rollback()
            raise

    async def add_all(self, models: List[ModelType]) -> List[ModelType]:
        """Insert several models in a single transaction.

        Args:
            models (list[ModelType]): The instances to insert.

        Returns:
            list[ModelType]: The same list after every instance is
            refreshed.

        Raises:
            ConflictException: On integrity violations.
        """
        try:
            self.session.add_all(models)
            await self.session.commit()
            for model in models:
                await self.session.refresh(model)
            return models
        except IntegrityError as exc:
            await self.session.rollback()
            logger.warning(
                "IntegrityError on %s.add_all: %s", self.model.__name__, exc.orig
            )
            raise ConflictException(
                message=self._bulk_create_conflict_message,
            ) from exc
        except Exception:
            await self.session.rollback()
            raise

    async def update(self, model: ModelType) -> ModelType:
        """Persist mutations made on an attached ``model``.

        The instance must already be tracked by the session (e.g.
        returned by :meth:`get`) with its fields modified. This
        method only commits and refreshes.

        Args:
            model (ModelType): The mutated instance.

        Returns:
            ModelType: The same instance after ``refresh``.

        Raises:
            ConflictException: On integrity violations.
        """
        try:
            await self.session.commit()
            await self.session.refresh(model)
            return model
        except IntegrityError as exc:
            await self.session.rollback()
            logger.warning(
                "IntegrityError on %s.update: %s", self.model.__name__, exc.orig
            )
            raise ConflictException(
                message=self._update_conflict_message,
            ) from exc
        except Exception:
            await self.session.rollback()
            raise

    async def update_many(self, models: List[ModelType]) -> List[ModelType]:
        """Commit several mutated instances in a single transaction.

        Args:
            models (list[ModelType]): The mutated instances.

        Returns:
            list[ModelType]: The same list.

        Raises:
            ConflictException: On integrity violations.
        """
        try:
            await self.session.commit()
            return models
        except IntegrityError as exc:
            await self.session.rollback()
            logger.warning(
                "IntegrityError on %s.update_many: %s",
                self.model.__name__,
                exc.orig,
            )
            raise ConflictException(
                message=self._bulk_update_conflict_message,
            ) from exc
        except Exception:
            await self.session.rollback()
            raise

    async def bulk_update(
        self,
        filters: dict[str, Any],
        values: dict[str, Any],
    ) -> int:
        """Issue a single ``UPDATE ... WHERE`` against the table.

        Bypasses the unit-of-work entirely — useful for mass mutations
        that don't need to refresh each affected row in the session.

        Args:
            filters (dict[str, Any]): Filter conditions identifying
                the rows to mutate. An empty mapping is rejected to
                prevent accidental table-wide updates.
            values (dict[str, Any]): Column-value pairs to set on the
                matching rows.

        Returns:
            int: The number of rows affected.

        Raises:
            ValueError: If ``filters`` is empty.
            ConflictException: On integrity violations.
        """
        if not filters:
            raise ValueError(
                "bulk_update requires non-empty filters; "
                "pass an explicit truthy condition to update every row."
            )
        try:
            query = update(self.model)
            query = self._apply_filters(query, filters)
            query = query.values(**values)
            result = cast(CursorResult[Any], await self.session.execute(query))
            await self.session.commit()
            return result.rowcount or 0
        except IntegrityError as exc:
            await self.session.rollback()
            logger.warning(
                "IntegrityError on %s.bulk_update: %s",
                self.model.__name__,
                exc.orig,
            )
            raise ConflictException(
                message=self._bulk_update_conflict_message,
            ) from exc
        except Exception:
            await self.session.rollback()
            raise

    async def delete(self, id: UUID) -> None:
        """Delete a single row by its primary key.

        Args:
            id (UUID): The primary key.

        Raises:
            AppException: ``self.not_found_exception`` if no record
                with ``id`` exists.
        """
        try:
            query = delete(self.model).where(self.model.id == id)
            result = cast(CursorResult[Any], await self.session.execute(query))
            if result.rowcount == 0:
                self._raise_not_found()
            await self.session.commit()
        except AppException:
            raise
        except Exception:
            await self.session.rollback()
            raise

    async def delete_many(self, filters: dict[str, Any]) -> int:
        """Delete every row matching ``filters``.

        An empty ``filters`` dict deletes every row in the table.
        Callers must opt in explicitly — the behavior is intentional.

        Args:
            filters (dict[str, Any]): The conditions identifying the
                rows to delete.

        Returns:
            int: The number of rows deleted.
        """
        try:
            query = delete(self.model)
            if filters:
                query = self._apply_filters(query, filters)
            result = cast(CursorResult[Any], await self.session.execute(query))
            await self.session.commit()
            return result.rowcount or 0
        except Exception:
            await self.session.rollback()
            raise

    async def delete_batch(self, ids: List[UUID]) -> int:
        """Delete several rows by primary key.

        Args:
            ids (list[UUID]): The primary keys to delete.

        Returns:
            int: The number of rows deleted.
        """
        try:
            query = delete(self.model).where(self.model.id.in_(ids))
            result = cast(CursorResult[Any], await self.session.execute(query))
            await self.session.commit()
            return result.rowcount or 0
        except Exception:
            await self.session.rollback()
            raise

    async def soft_delete(self, id: UUID) -> ModelType:
        """Soft-delete a row by setting ``is_active=False``.

        Loads the row, flips ``is_active``, persists. Returns the
        refreshed instance so callers can inspect the post-state.

        Args:
            id (UUID): The primary key.

        Returns:
            ModelType: The row with ``is_active=False``.

        Raises:
            AppException: ``self.not_found_exception`` if no record
                with ``id`` exists.
        """
        instance = await self.get_by_id(id)
        instance.is_active = False
        return await self.update(instance)

    async def restore(self, id: UUID) -> ModelType:
        """Reactivate a soft-deleted row by setting ``is_active=True``.

        Args:
            id (UUID): The primary key.

        Returns:
            ModelType: The row with ``is_active=True``.

        Raises:
            AppException: ``self.not_found_exception`` if no record
                with ``id`` exists.
        """
        instance = await self.get_by_id(id)
        instance.is_active = True
        return await self.update(instance)

    async def count(self, filters: dict[str, Any] | None = None) -> int:
        """Count the rows matching ``filters``.

        Args:
            filters (dict[str, Any] | None): The filter conditions.

        Returns:
            int: The matching row count.
        """
        query = select(func.count()).select_from(self.model)
        if filters:
            query = self._apply_filters(query, filters)
        result = await self.session.execute(query)
        return result.scalar() or 0

    def map_to_schema(self, instance: ModelType) -> Any:
        """Map an ORM row to its schema/domain representation.

        Concrete repositories MUST implement this to bridge the data
        layer and the rest of the application.

        Args:
            instance (ModelType): The ORM row to convert.

        Returns:
            Any: The schema/domain object.

        Raises:
            NotImplementedError: Always — subclasses must override.
        """
        raise NotImplementedError(
            "Subclasses must implement map_to_schema",
        )

    def map_to_model(self, data: dict[str, Any]) -> ModelType:
        """Build an ORM instance from a plain ``dict`` payload.

        Default implementation constructs ``self.model(**data)``;
        override for custom field mapping.

        Args:
            data (dict[str, Any]): The payload.

        Returns:
            ModelType: A new (unpersisted) ORM instance.
        """
        return self.model(**data)

    def map_to_response(self, instance: ModelType) -> Any:
        """Map an ORM row to its API response schema.

        Concrete repositories MUST implement this when used from the
        router layer.

        Args:
            instance (ModelType): The ORM row to convert.

        Returns:
            Any: The response schema.

        Raises:
            NotImplementedError: Always — subclasses must override.
        """
        raise NotImplementedError(
            "Subclasses must implement map_to_response",
        )


__all__: list[str] = [
    "BaseRepository",
]
