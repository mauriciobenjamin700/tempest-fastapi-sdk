"""Generic async repository with CRUD + filter + pagination primitives."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Generic, List, NoReturn, TypeVar, cast
from uuid import UUID

from sqlalchemy import (
    CursorResult,
    Select,
    delete,
    func,
    insert,
    inspect,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tempest_fastapi_sdk.db.audit import BaseAuditLogModel, snapshot_model
from tempest_fastapi_sdk.db.expressions import F, Q, build_filter_condition
from tempest_fastapi_sdk.db.model import BaseModel
from tempest_fastapi_sdk.db.signals import RepositorySignal, emit, has_handlers
from tempest_fastapi_sdk.exceptions.base import AppException
from tempest_fastapi_sdk.exceptions.conflict import ConflictException
from tempest_fastapi_sdk.exceptions.not_found import NotFoundException
from tempest_fastapi_sdk.utils.datetime import utcnow

logger = logging.getLogger(__name__)

ModelType = TypeVar("ModelType", bound=BaseModel)


class BaseRepository(Generic[ModelType]):
    """Base async repository with generic CRUD operations.

    Instantiate directly for plain CRUD (``BaseRepository(session,
    model=UserModel)``) or subclass when adding custom queries — the
    subclass forwards ``model`` / ``not_found_exception`` to
    ``super().__init__`` instead of declaring class attributes. The
    constructor signature is the contract; there are no magic class
    attributes to override.

    The default filter logic supports equality on every column plus
    the following conventions:

    * ``name`` (string) → case-insensitive ``ILIKE %value%`` search.
    * ``bool`` values → ``.is_(value)`` (correct SQL boolean check).
    * ``list`` values → ``.in_(values)`` membership.
    * ``date`` values → ``func.date(column) == value`` whole-day match.
    * ``start_in`` / ``end_in`` (date) → range filter against the
      model's ``date`` column when present, falling back to
      ``created_at``.
    * ``<column>__<op>`` suffix → comparison filter, where ``<op>`` is
      one of ``gt`` / ``gte`` / ``lt`` / ``lte`` / ``ne`` (e.g.
      ``{"updated_at__gt": watermark}`` → ``updated_at > watermark``).
      Timestamp-precise, unlike ``start_in`` / ``end_in`` (whole-day);
      this is what delta-sync queries filter on. A ``None`` value
      skips the condition, like every other filter.

    All error messages can be customized per repository instance via
    the constructor kwargs (``not_found_message``,
    ``create_conflict_message``, etc.); when omitted, sensible defaults
    derived from ``self.model.__name__`` are used.

    The same three abstract mappers ``map_to_schema`` / ``map_to_model``
    / ``map_to_response`` are kept so concrete repositories own the
    translation between ORM rows and DTOs.

    Attributes:
        model (type[ModelType]): The SQLAlchemy model class operated on.
        not_found_exception (type[AppException]): Exception class raised
            when single-record lookups miss.
        session (AsyncSession): The async database session.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        model: type[ModelType],
        not_found_exception: type[AppException] = NotFoundException,
        not_found_message: str | None = None,
        create_conflict_message: str | None = None,
        update_conflict_message: str | None = None,
        bulk_create_conflict_message: str | None = None,
        bulk_update_conflict_message: str | None = None,
        audit_model: type[BaseAuditLogModel] | None = None,
    ) -> None:
        """Initialize the repository.

        Every ``*_message`` kwarg is optional — when not provided, the
        repository falls back to a generic message derived from the
        model class name (e.g. ``"User not found"``,
        ``"Conflict creating User"``).

        Args:
            session (AsyncSession): The async database session.
            model (type[ModelType]): The SQLAlchemy model class this
                repository operates on. Required.
            not_found_exception (type[AppException]): Exception class
                raised when single-record lookups miss. Defaults to
                :class:`NotFoundException`; pass a domain-specific
                subclass for richer 404 messages.
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

        Raises:
            TypeError: When ``model`` is not a subclass of
                :class:`BaseModel`.
        """
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            raise TypeError(
                "BaseRepository `model` must be a subclass of BaseModel",
            )
        self.session: AsyncSession = session
        self.model: type[ModelType] = model
        self.not_found_exception: type[AppException] = not_found_exception
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
        self._audit_model: type[BaseAuditLogModel] | None = audit_model

    def _require_audit_model(self) -> type[BaseAuditLogModel]:
        """Return the configured audit model or raise.

        Returns:
            type[BaseAuditLogModel]: The repository's audit model.

        Raises:
            RuntimeError: When the repository was built without an
                ``audit_model``.
        """
        if self._audit_model is None:
            raise RuntimeError(
                f"{type(self).__name__} was created without an audit_model; "
                "pass audit_model=... to record an audit trail.",
            )
        return self._audit_model

    def _apply_filters(
        self,
        query: Any,
        filters: dict[str, Any],
    ) -> Any:
        """Apply filter conditions to a select/delete/update query.

        See the class docstring for the recognized conventions. The
        per-field logic is shared with :class:`Q` via
        :func:`build_filter_condition`; the ``start_in`` / ``end_in``
        whole-day range keys are dict-only sugar handled here.

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

            if field in ("start_in", "end_in") and isinstance(value, date):
                column = getattr(
                    self.model,
                    "date",
                    getattr(self.model, "created_at", None),
                )
                if column is not None:
                    if field == "start_in":
                        query = query.where(func.date(column) >= value)
                    else:
                        query = query.where(func.date(column) <= value)
                continue

            condition = build_filter_condition(self.model, field, value)
            if condition is not None:
                query = query.where(condition)
        return query

    def _apply_where(self, query: Any, where: Q | None) -> Any:
        """Apply a :class:`Q` tree to a query, if given.

        Args:
            query: The SQLAlchemy query to mutate.
            where (Q | None): The condition tree, or ``None``.

        Returns:
            The query, with the resolved clause added when non-empty.
        """
        if where is None:
            return query
        clause = where.resolve(self.model)
        if clause is not None:
            query = query.where(clause)
        return query

    def _relationship_options(self, with_: list[str]) -> list[Any]:
        """Build eager-load loader options for the given relationship paths.

        Each entry is a relationship name on ``self.model`` and may be
        dotted to traverse nested relationships (e.g. ``"posts.comments"``
        loads ``posts`` then each post's ``comments``). Every hop uses
        ``selectinload`` (a separate ``SELECT ... IN`` per level), which
        avoids the row multiplication of a join and works for both
        collection and scalar relationships.

        Args:
            with_ (list[str]): Relationship paths to eager-load.

        Returns:
            list[Any]: SQLAlchemy loader options to pass to
            ``query.options(*...)``.

        Raises:
            ValueError: When a path segment is not a relationship on the
                model reached at that hop.
        """
        options: list[Any] = []
        for path in with_:
            current: type[Any] = self.model
            loader: Any = None
            for part in path.split("."):
                mapper = inspect(current, raiseerr=False)
                if mapper is None or part not in mapper.relationships:
                    raise ValueError(
                        f"{current.__name__} has no relationship {part!r} "
                        f"(eager-load path {path!r})",
                    )
                attr = getattr(current, part)
                loader = (
                    selectinload(attr) if loader is None else loader.selectinload(attr)
                )
                current = mapper.relationships[part].mapper.class_
            options.append(loader)
        return options

    async def _emit_signal(
        self,
        signal: RepositorySignal,
        instance: ModelType,
    ) -> None:
        """Emit ``signal`` for ``instance`` to any registered handlers.

        Args:
            signal (RepositorySignal): The lifecycle moment.
            instance (ModelType): The ORM row passed to each handler.
        """
        await emit(type(instance), signal, instance)

    def _raise_not_found(self) -> NoReturn:
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
        with_: list[str] | None = None,
        where: Q | None = None,
    ) -> ModelType:
        """Return the single record matching ``filters``.

        Args:
            filters (dict[str, Any]): The column-value pairs.
            for_update (bool): Whether to acquire a row-level lock
                (``SELECT ... FOR UPDATE``). Defaults to ``False``.
            with_ (list[str] | None): Relationship paths to eager-load
                (``selectinload``), dotted for nested relations (e.g.
                ``["author", "comments.replies"]``). Avoids the
                lazy-load ``MissingGreenlet`` error when accessing
                relationships outside the session's async context.
            where (Q | None): A :class:`Q` condition tree ANDed with
                ``filters`` for ``OR`` / ``NOT`` logic the dict cannot
                express.

        Returns:
            ModelType: The matching row.

        Raises:
            AppException: ``self.not_found_exception`` with the
                configured ``not_found_message`` if no record
                matches the filters.
            ValueError: When a ``with_`` path names a non-relationship.
        """
        instance = await self.get_or_none(
            filters, for_update=for_update, with_=with_, where=where
        )
        if instance is None:
            self._raise_not_found()
        return instance

    async def get_or_none(
        self,
        filters: dict[str, Any],
        for_update: bool = False,
        with_: list[str] | None = None,
        where: Q | None = None,
    ) -> ModelType | None:
        """Return the single record matching ``filters`` or ``None``.

        Unlike :meth:`get`, never raises when nothing matches.

        Args:
            filters (dict[str, Any]): The column-value pairs.
            for_update (bool): Whether to acquire a row-level lock.
            with_ (list[str] | None): Relationship paths to eager-load;
                see :meth:`get`.
            where (Q | None): A :class:`Q` condition tree ANDed with
                ``filters``.

        Returns:
            ModelType | None: The matching row, or ``None``.

        Raises:
            ValueError: When a ``with_`` path names a non-relationship.
        """
        query = select(self.model)
        query = self._apply_filters(query, filters)
        query = self._apply_where(query, where)
        if with_:
            query = query.options(*self._relationship_options(with_))
        if for_update:
            query = query.with_for_update()
        result = await self.session.execute(query)
        instance = result.unique().scalars().one_or_none()
        return cast("ModelType | None", instance)

    async def get_by_id(
        self,
        id: UUID,
        for_update: bool = False,
        with_: list[str] | None = None,
    ) -> ModelType:
        """Return the record with the given primary key.

        Args:
            id (UUID): The primary key to look up.
            for_update (bool): Whether to acquire a row-level lock.
            with_ (list[str] | None): Relationship paths to eager-load;
                see :meth:`get`.

        Returns:
            ModelType: The matching row.

        Raises:
            AppException: ``self.not_found_exception`` if no record
                with ``id`` exists.
            ValueError: When a ``with_`` path names a non-relationship.
        """
        return await self.get({"id": id}, for_update=for_update, with_=with_)

    async def resolve(
        self,
        ref: UUID | ModelType,
        for_update: bool = False,
    ) -> ModelType:
        """Resolve an ``id``-or-instance reference to a model instance.

        Accepts either a primary-key ``UUID`` or an already-loaded model
        instance and always returns the instance. This removes the
        ``if isinstance(x, UUID): ... else: ...`` boilerplate services
        reimplement whenever a method takes ``UUID | Model``.

        When given a **detached** instance (one whose session has been
        closed — e.g. a user loaded by an auth dependency on its own
        short-lived session) it is re-attached to this repository's
        session via :meth:`AsyncSession.merge`. Returning it as-is would
        later raise ``InvalidRequestError: Instance is not persistent
        within this Session`` on the first ``commit`` / ``refresh``.
        ``merge`` issues a ``SELECT`` only when the row is not already in
        this session's identity map.

        Args:
            ref (UUID | ModelType): The primary key to look up, or an
                already-loaded instance to attach and return.
            for_update (bool): Whether to acquire a row-level lock when
                ``ref`` is a ``UUID`` (ignored when an instance is given).

        Returns:
            ModelType: The resolved instance, attached to this session.

        Raises:
            AppException: ``self.not_found_exception`` when ``ref`` is a
                ``UUID`` with no matching row.
        """
        if isinstance(ref, UUID):
            return await self.get_by_id(ref, for_update=for_update)
        if inspect(ref).detached:
            return await self.session.merge(ref)
        return ref

    async def exists(
        self,
        filters: dict[str, Any],
        where: Q | None = None,
    ) -> bool:
        """Return whether at least one row matches ``filters``.

        Executes a ``SELECT 1 ... LIMIT 1`` so the row is never
        fully loaded.

        Args:
            filters (dict[str, Any]): The filter conditions.
            where (Q | None): A :class:`Q` condition tree ANDed with
                ``filters``.

        Returns:
            bool: ``True`` if at least one row matches.
        """
        query = select(self.model.id)
        query = self._apply_filters(query, filters)
        query = self._apply_where(query, where)
        query = query.limit(1)
        result = await self.session.execute(query)
        return result.scalar() is not None

    async def exists_excluding(
        self,
        filters: dict[str, Any],
        *,
        exclude_id: UUID | None,
    ) -> bool:
        """Return whether another row matches ``filters``, excluding one id.

        The "is this value already taken by someone *else*?" check that
        unique-field validation needs on update — e.g. confirming a new
        email / phone / username isn't used by a different record before
        saving. When ``exclude_id`` is ``None`` (the create case, no row
        to exclude yet) it behaves exactly like :meth:`exists`.

        Args:
            filters (dict[str, Any]): The column-value pairs to match
                (e.g. ``{"phone": "+5511..."}``).
            exclude_id (UUID | None): The primary key to exclude from the
                match (typically the row being updated). ``None`` excludes
                nothing.

        Returns:
            bool: ``True`` if a row other than ``exclude_id`` matches.
        """
        effective = filters if exclude_id is None else {**filters, "id__ne": exclude_id}
        return await self.exists(effective)

    async def first(
        self,
        filters: dict[str, Any] | None = None,
        order_by: Any | None = None,
        ascending: bool = True,
        with_: list[str] | None = None,
        where: Q | None = None,
    ) -> ModelType | None:
        """Return the first matching row or ``None``.

        Convenience wrapper around :meth:`list` for cases that only
        need one row but want to control ordering.

        Args:
            filters (dict[str, Any] | None): The filter conditions.
            order_by: A SQLAlchemy column expression to order by.
                ``None`` keeps insertion order.
            ascending (bool): Whether to order ascending.
            with_ (list[str] | None): Relationship paths to eager-load;
                see :meth:`get`.
            where (Q | None): A :class:`Q` condition tree ANDed with
                ``filters``.

        Returns:
            ModelType | None: The first matching row, or ``None``.

        Raises:
            ValueError: When a ``with_`` path names a non-relationship.
        """
        query = select(self.model)
        if filters:
            query = self._apply_filters(query, filters)
        query = self._apply_where(query, where)
        if with_:
            query = query.options(*self._relationship_options(with_))
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
        with_: list[str] | None = None,
        where: Q | None = None,
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
            with_ (list[str] | None): Relationship paths to eager-load;
                see :meth:`get`. Uses ``selectinload``, so N related
                rows cost one extra query, not N.
            where (Q | None): A :class:`Q` condition tree ANDed with
                ``filters`` for ``OR`` / ``NOT`` logic (e.g.
                ``Q(a=1) | Q(b=2)``).

        Returns:
            list[ModelType]: The matching rows.

        Raises:
            ValueError: When a ``with_`` path names a non-relationship.
        """
        query = select(self.model)

        if filters:
            query = self._apply_filters(query, filters)

        query = self._apply_where(query, where)

        if with_:
            query = query.options(*self._relationship_options(with_))

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
        where: Q | None = None,
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
            where (Q | None): A :class:`Q` condition tree ANDed with
                ``filters``; applied to both the page and its count.

        Returns:
            dict[str, Any]: A mapping with keys ``items``, ``total``,
            ``page``, ``size``, ``pages``.
        """
        if query is None:
            query = select(self.model)

        if filters:
            query = self._apply_filters(query, filters)

        query = self._apply_where(query, where)

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
            "page_size": page_size,
            "pages": pages,
        }

    async def cursor_paginate(
        self,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 20,
        order_by: str = "created_at",
        ascending: bool = False,
        query: Select[Any] | None = None,
    ) -> dict[str, Any]:
        """Return a single cursor-paginated page of records.

        Cursor pagination orders by ``(order_by, id)`` so the result
        is stable under concurrent inserts and scales without a
        ``COUNT(*)``. The cursor encodes the last row's
        ``(order_by_value, id)`` so the next page can continue
        precisely past it.

        Args:
            filters (dict[str, Any] | None): Filter conditions.
            cursor (str | None): Opaque cursor from the previous page;
                ``None`` requests the first page.
            limit (int): Maximum items to return in this page.
            order_by (str): Column to sort by. Must exist on the model.
            ascending (bool): Whether to sort ascending. Defaults to
                ``False``.
            query (Select[Any] | None): A pre-built ``Select`` to
                paginate; if ``None``, defaults to
                ``select(self.model)``. Mirrors :meth:`paginate` so a
                hand-built query (joins, ``IS NULL`` predicates the
                filter dict can't express, etc.) can still be
                cursor-paginated. ``filters`` and the cursor/order
                clauses are applied on top of it.

        Returns:
            dict[str, Any]: Mapping with ``items``, ``next_cursor``,
            ``has_more`` and ``limit``.

        Raises:
            ValueError: When ``order_by`` is not a column on the
                model, or when ``cursor`` is malformed.
        """
        from tempest_fastapi_sdk.schemas.pagination import (
            decode_cursor,
            encode_cursor,
        )

        column = getattr(self.model, order_by, None)
        if column is None:
            raise ValueError(
                f"{self.model.__name__!r} has no column {order_by!r}",
            )

        if query is None:
            query = select(self.model)
        if filters:
            query = self._apply_filters(query, filters)

        if cursor is not None:
            payload = decode_cursor(cursor)
            last_value = payload.get("value")
            last_id_raw = payload.get("id")
            try:
                last_id = (
                    UUID(last_id_raw) if isinstance(last_id_raw, str) else last_id_raw
                )
            except (ValueError, AttributeError) as exc:
                raise ValueError("Invalid cursor id") from exc
            if ascending:
                query = query.where(
                    (column > last_value)
                    | ((column == last_value) & (self.model.id > last_id)),
                )
            else:
                query = query.where(
                    (column < last_value)
                    | ((column == last_value) & (self.model.id < last_id)),
                )

        primary = column if ascending else column.desc()
        secondary = self.model.id if ascending else self.model.id.desc()
        query = query.order_by(primary, secondary).limit(limit + 1)

        result = await self.session.execute(query)
        rows = list(result.unique().scalars().all())
        has_more = len(rows) > limit
        items = rows[:limit]

        next_cursor: str | None = None
        if has_more and items:
            last = items[-1]
            next_cursor = encode_cursor(
                {
                    "value": getattr(last, order_by),
                    "id": last.id,
                },
            )

        return {
            "items": items,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "limit": limit,
        }

    async def changes_since(
        self,
        since: datetime | None,
        *,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 50,
        order_by: str = "updated_at",
        include_deleted: bool = True,
    ) -> dict[str, Any]:
        """Return the rows that changed since a high-water mark.

        The backbone of offline-first / delta sync: an offline client
        (mobile app, PWA) replays only what changed since its last
        successful pull instead of refetching the whole table. Rows are
        ordered ascending by ``order_by`` (oldest change first) and
        tie-broken by ``id``, so the client can advance its watermark
        monotonically and resume mid-stream with the returned cursor.

        Recommended watermark protocol:

        1. First sync: call with ``since=None`` (returns everything,
           cursor-paginated). Drain every page via ``next_cursor``.
        2. Persist the returned ``server_time`` (NOT the max
           ``updated_at`` of the items) as the next ``since``.
        3. Next sync: call with that ``since``. The filter is
           ``updated_at > since`` (strict), and because ``server_time``
           is captured *before* the query runs it is a safe high-water
           mark — anything committed afterwards has a later
           ``updated_at`` and surfaces on the following pull.

        When the model mixes in
        :class:`tempest_fastapi_sdk.SoftDeleteMixin`, keep
        ``include_deleted=True`` (the default) so soft-deleted rows are
        returned as tombstones (``deleted_at`` set) and the client can
        mirror the deletion locally. A pull that filtered them out would
        leave deleted rows stranded on the device forever.

        Args:
            since (datetime | None): Only rows whose ``order_by`` column
                is strictly greater than this are returned. ``None``
                returns every row (initial full sync).
            filters (dict[str, Any] | None): Extra equality/operator
                filters applied on top — typically the tenant/owner
                scope, e.g. ``{"user_id": user_id}``. Do NOT pass an
                owner-less filter set: this method never scopes by
                itself.
            cursor (str | None): Opaque cursor from the previous page;
                ``None`` requests the first page.
            limit (int): Maximum items per page. Defaults to ``50``.
            order_by (str): Timestamp column the watermark applies to.
                Defaults to ``"updated_at"``. Must exist on the model.
            include_deleted (bool): Whether to include soft-deleted
                rows (tombstones). Defaults to ``True``. Ignored when
                the model has no ``deleted_at`` column.

        Returns:
            dict[str, Any]: The :meth:`cursor_paginate` mapping
            (``items`` / ``next_cursor`` / ``has_more`` / ``limit``)
            plus ``server_time`` (:class:`datetime`) — the instant the
            query started, to be persisted as the next ``since``.

        Raises:
            ValueError: When ``order_by`` is not a column on the model,
                or when ``cursor`` is malformed.
        """
        server_time = utcnow()

        combined: dict[str, Any] = dict(filters or {})
        if since is not None:
            combined[f"{order_by}__gt"] = since

        base_query: Select[Any] | None = None
        if not include_deleted and hasattr(self.model, "deleted_at"):
            base_query = select(self.model).where(
                self.model.deleted_at.is_(None),  # type: ignore[attr-defined]
            )

        page = await self.cursor_paginate(
            filters=combined,
            cursor=cursor,
            limit=limit,
            order_by=order_by,
            ascending=True,
            query=base_query,
        )
        page["server_time"] = server_time
        return page

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
            await self._emit_signal(RepositorySignal.PRE_SAVE, model)
            self.session.add(model)
            await self.session.commit()
            await self.session.refresh(model)
            await self._emit_signal(RepositorySignal.POST_SAVE, model)
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
            for model in models:
                await self._emit_signal(RepositorySignal.PRE_SAVE, model)
            self.session.add_all(models)
            await self.session.commit()
            for model in models:
                await self.session.refresh(model)
                await self._emit_signal(RepositorySignal.POST_SAVE, model)
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

    async def save_with_outbox(
        self,
        model: ModelType,
        event: BaseModel,
    ) -> ModelType:
        """Insert ``model`` and an outbox ``event`` in one transaction.

        This is the write half of the transactional outbox pattern: the
        business row and the event row commit together, so an event can
        never reference a row that was rolled back (and a committed row
        always has its event durably queued). A separate
        :class:`~tempest_fastapi_sdk.db.outbox.OutboxRelay` later
        publishes the event and marks it sent.

        Args:
            model (ModelType): The business instance to insert.
            event (BaseModel): The outbox row to insert alongside it —
                typically ``OutboxModel.new_event(topic, payload)``.

        Returns:
            ModelType: The ``model`` instance after ``refresh`` so its
            ``id`` and timestamp columns are populated.

        Raises:
            ConflictException: On integrity violations (the whole
                transaction — model and event — is rolled back).
        """
        try:
            self.session.add(model)
            self.session.add(event)
            await self.session.commit()
            await self.session.refresh(model)
            return model
        except IntegrityError as exc:
            await self.session.rollback()
            logger.warning(
                "IntegrityError on %s.save_with_outbox: %s",
                self.model.__name__,
                exc.orig,
            )
            raise ConflictException(
                message=self._create_conflict_message,
            ) from exc
        except Exception:
            await self.session.rollback()
            raise

    @staticmethod
    def snapshot(model: ModelType) -> dict[str, Any]:
        """Capture a model's column values for a later audit diff.

        Take this **before** mutating an instance, then pass it to
        :meth:`update_audited` so the audit entry can diff before/after.

        Args:
            model (ModelType): The instance to snapshot.

        Returns:
            dict[str, Any]: A JSON-able ``{column: value}`` snapshot.
        """
        return snapshot_model(model)

    async def add_audited(
        self,
        model: ModelType,
        *,
        actor: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> ModelType:
        """Insert ``model`` and a ``create`` audit row in one transaction.

        Requires the repository to be built with ``audit_model=...``. The
        business row and the audit row commit together, so the trail can
        never reference a row that was rolled back.

        Args:
            model (ModelType): The instance to insert.
            actor (str | None): Who performed the create (user id,
                e-mail, ``"system"``, ...).
            context (dict[str, Any] | None): Extra metadata (request id,
                ip, reason, ...).

        Returns:
            ModelType: The instance after ``refresh``.

        Raises:
            RuntimeError: When no ``audit_model`` was configured.
            ConflictException: On integrity violations (the whole
                transaction is rolled back).
        """
        audit_model = self._require_audit_model()
        try:
            self.session.add(model)
            await self.session.flush()
            entry = audit_model.for_create(model, actor=actor, context=context)
            self.session.add(entry)
            await self.session.commit()
            await self.session.refresh(model)
            return model
        except IntegrityError as exc:
            await self.session.rollback()
            logger.warning(
                "IntegrityError on %s.add_audited: %s",
                self.model.__name__,
                exc.orig,
            )
            raise ConflictException(
                message=self._create_conflict_message,
            ) from exc
        except Exception:
            await self.session.rollback()
            raise

    async def update_audited(
        self,
        model: ModelType,
        before: dict[str, Any],
        *,
        actor: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> ModelType:
        """Persist mutations on ``model`` and an ``update`` audit row.

        ``before`` is a snapshot taken with :meth:`snapshot` *before* the
        instance was mutated; the audit row stores the changed-field
        diff. The business update and the audit row commit together.

        Args:
            model (ModelType): The mutated, session-attached instance.
            before (dict[str, Any]): The pre-mutation snapshot.
            actor (str | None): Who performed the update.
            context (dict[str, Any] | None): Extra metadata.

        Returns:
            ModelType: The instance after ``refresh``.

        Raises:
            RuntimeError: When no ``audit_model`` was configured.
            ConflictException: On integrity violations.
        """
        audit_model = self._require_audit_model()
        try:
            entry = audit_model.for_update(
                model,
                before,
                actor=actor,
                context=context,
            )
            self.session.add(entry)
            await self.session.commit()
            await self.session.refresh(model)
            return model
        except IntegrityError as exc:
            await self.session.rollback()
            logger.warning(
                "IntegrityError on %s.update_audited: %s",
                self.model.__name__,
                exc.orig,
            )
            raise ConflictException(
                message=self._update_conflict_message,
            ) from exc
        except Exception:
            await self.session.rollback()
            raise

    async def delete_audited(
        self,
        model: ModelType,
        *,
        actor: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Delete ``model`` and write a ``delete`` audit row in one tx.

        Snapshots the row before deleting it, so the trail keeps the
        final state of what was removed.

        Args:
            model (ModelType): The session-attached instance to delete.
            actor (str | None): Who performed the delete.
            context (dict[str, Any] | None): Extra metadata.

        Raises:
            RuntimeError: When no ``audit_model`` was configured.
        """
        audit_model = self._require_audit_model()
        try:
            entry = audit_model.for_delete(model, actor=actor, context=context)
            await self.session.delete(model)
            self.session.add(entry)
            await self.session.commit()
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
            await self._emit_signal(RepositorySignal.PRE_SAVE, model)
            await self.session.commit()
            await self.session.refresh(model)
            await self._emit_signal(RepositorySignal.POST_SAVE, model)
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
            for model in models:
                await self._emit_signal(RepositorySignal.PRE_SAVE, model)
            await self.session.commit()
            for model in models:
                await self._emit_signal(RepositorySignal.POST_SAVE, model)
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

        A value may be an :class:`F` expression to compute the new value
        from existing columns in the database — ``{"stock": F("stock") -
        1}`` decrements atomically, with no read-modify-write race.

        Args:
            filters (dict[str, Any]): Filter conditions identifying
                the rows to mutate. An empty mapping is rejected to
                prevent accidental table-wide updates.
            values (dict[str, Any]): Column-value pairs to set on the
                matching rows. An :class:`F` value is resolved to a SQL
                expression against this repository's model.

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
        resolved_values = {
            key: value.resolve(self.model) if isinstance(value, F) else value
            for key, value in values.items()
        }
        try:
            query = update(self.model)
            query = self._apply_filters(query, filters)
            query = query.values(**resolved_values)
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

    async def bulk_create_values(
        self,
        rows: List[dict[str, Any]],
    ) -> int:
        """Insert many rows in a single ``INSERT ... VALUES (...), (...)`` statement.

        Unlike :meth:`add_all`, this bypasses the unit-of-work — the
        rows are not refreshed nor attached to the session. Use when
        you have a large batch (≥ 50 rows) and don't need the ORM
        instances back; the round-trip count drops from ``N`` to ``1``.

        Args:
            rows (list[dict[str, Any]]): One mapping per row,
                keyed by column name (not attribute name; usually
                they match for SDK models).

        Returns:
            int: Number of rows inserted (``len(rows)`` on success).

        Raises:
            ConflictException: On unique / FK violations.
            ValueError: When ``rows`` is empty.
        """
        if not rows:
            raise ValueError("bulk_create_values requires at least one row.")
        try:
            query = insert(self.model).values(rows)
            result = cast(CursorResult[Any], await self.session.execute(query))
            await self.session.commit()
            return result.rowcount or len(rows)
        except IntegrityError as exc:
            await self.session.rollback()
            logger.warning(
                "IntegrityError on %s.bulk_create_values: %s",
                self.model.__name__,
                exc.orig,
            )
            raise ConflictException(
                message=self._bulk_create_conflict_message,
            ) from exc
        except Exception:
            await self.session.rollback()
            raise

    async def bulk_upsert(
        self,
        rows: List[dict[str, Any]],
        *,
        conflict_columns: List[str],
        update_columns: List[str] | None = None,
    ) -> int:
        """Issue an ``INSERT ... ON CONFLICT DO UPDATE`` in one round-trip.

        Picks the dialect-specific upsert syntax automatically —
        Postgres (``postgresql.insert``) and SQLite
        (``sqlite.insert``) are supported. Other dialects raise
        :class:`NotImplementedError` so the caller can fall back to
        a transactional ``SELECT FOR UPDATE`` loop.

        Args:
            rows (list[dict[str, Any]]): One mapping per row.
            conflict_columns (list[str]): The columns whose
                conflict triggers the ``ON CONFLICT`` clause —
                typically the natural-key columns (e.g.
                ``["sku"]``). Must be backed by a UNIQUE index.
            update_columns (list[str] | None): Columns to refresh
                on conflict. ``None`` updates every column except
                ``conflict_columns`` and the primary key.

        Returns:
            int: Total rows touched (inserted + updated).

        Raises:
            ConflictException: On non-recoverable integrity errors.
            NotImplementedError: When the active SQLAlchemy dialect
                has no native upsert.
            ValueError: When ``rows`` is empty.
        """
        if not rows:
            raise ValueError("bulk_upsert requires at least one row.")

        bind = self.session.get_bind()
        dialect_name = bind.dialect.name
        stmt: Any
        if dialect_name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as _pg_insert

            stmt = _pg_insert(self.model).values(rows)
        elif dialect_name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as _sqlite_insert

            stmt = _sqlite_insert(self.model).values(rows)
        else:
            raise NotImplementedError(
                f"bulk_upsert: dialect {dialect_name!r} not supported. "
                f"Drop to a SELECT FOR UPDATE + UPDATE loop or open an "
                f"issue at https://github.com/mauriciobenjamin700/"
                f"tempest-fastapi-sdk/issues."
            )

        if update_columns is None:
            pk_columns = {col.name for col in self.model.__table__.primary_key}
            skip = set(conflict_columns) | pk_columns
            update_columns = [
                col.name for col in self.model.__table__.columns if col.name not in skip
            ]
        update_set = {col: getattr(stmt.excluded, col) for col in update_columns}
        stmt = stmt.on_conflict_do_update(
            index_elements=conflict_columns,
            set_=update_set,
        )

        try:
            result = cast(CursorResult[Any], await self.session.execute(stmt))
            await self.session.commit()
            return result.rowcount or len(rows)
        except IntegrityError as exc:
            await self.session.rollback()
            logger.warning(
                "IntegrityError on %s.bulk_upsert: %s",
                self.model.__name__,
                exc.orig,
            )
            raise ConflictException(
                message=self._bulk_create_conflict_message,
            ) from exc
        except Exception:
            await self.session.rollback()
            raise

    async def delete(self, id: UUID) -> None:
        """Delete a single row by its primary key.

        Fires ``PRE_DELETE`` before and ``POST_DELETE`` after the
        commit **only when a handler is registered** — otherwise the
        row is never loaded and this stays a single ``DELETE``
        statement. When signals are active the row is loaded once,
        passed to the handlers, and detached before commit so its
        column values remain readable in ``POST_DELETE``.

        Args:
            id (UUID): The primary key.

        Raises:
            AppException: ``self.not_found_exception`` if no record
                with ``id`` exists.
        """
        try:
            instance: ModelType | None = None
            wants_signals = has_handlers(
                self.model, RepositorySignal.PRE_DELETE
            ) or has_handlers(self.model, RepositorySignal.POST_DELETE)
            if wants_signals:
                instance = await self.get_or_none({"id": id})
                if instance is None:
                    self._raise_not_found()
                await self._emit_signal(RepositorySignal.PRE_DELETE, instance)

            query = delete(self.model).where(self.model.id == id)
            result = cast(CursorResult[Any], await self.session.execute(query))
            if result.rowcount == 0:
                self._raise_not_found()

            if instance is not None:
                self.session.expunge(instance)
            await self.session.commit()

            if instance is not None:
                await self._emit_signal(RepositorySignal.POST_DELETE, instance)
        except AppException:
            raise
        except Exception:
            await self.session.rollback()
            raise

    async def delete_many(
        self,
        filters: dict[str, Any],
        where: Q | None = None,
    ) -> int:
        """Delete every row matching ``filters``.

        An empty ``filters`` dict (and no ``where``) deletes every row
        in the table. Callers must opt in explicitly — the behavior is
        intentional.

        Args:
            filters (dict[str, Any]): The conditions identifying the
                rows to delete.
            where (Q | None): A :class:`Q` condition tree ANDed with
                ``filters``.

        Returns:
            int: The number of rows deleted.
        """
        try:
            query = delete(self.model)
            if filters:
                query = self._apply_filters(query, filters)
            query = self._apply_where(query, where)
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

    async def count(
        self,
        filters: dict[str, Any] | None = None,
        where: Q | None = None,
    ) -> int:
        """Count the rows matching ``filters``.

        Args:
            filters (dict[str, Any] | None): The filter conditions.
            where (Q | None): A :class:`Q` condition tree ANDed with
                ``filters``.

        Returns:
            int: The matching row count.
        """
        query = select(func.count()).select_from(self.model)
        if filters:
            query = self._apply_filters(query, filters)
        query = self._apply_where(query, where)
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
