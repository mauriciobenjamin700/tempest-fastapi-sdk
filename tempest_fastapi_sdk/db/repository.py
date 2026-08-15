"""Generic async repository with CRUD + filter + pagination primitives."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
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
from sqlalchemy.sql.elements import ColumnElement

from tempest_fastapi_sdk.db.audit import BaseAuditLogModel, snapshot_model
from tempest_fastapi_sdk.db.explain import ExplainReport, explain_queries
from tempest_fastapi_sdk.db.expressions import (
    F,
    Q,
    WhereClause,
    build_filter_condition,
)
from tempest_fastapi_sdk.db.model import BaseModel
from tempest_fastapi_sdk.db.search import (
    ColumnRef,
    TextSearchLanguage,
    TextSearchWeight,
    TokenMatch,
    full_text_rank,
    like_search_condition,
    supports_full_text,
)
from tempest_fastapi_sdk.db.search import (
    full_text_condition as build_full_text_condition,
)
from tempest_fastapi_sdk.db.signals import RepositorySignal, emit, has_handlers
from tempest_fastapi_sdk.db.transaction import in_transaction, savepoint, transaction
from tempest_fastapi_sdk.exceptions.base import AppException
from tempest_fastapi_sdk.exceptions.conflict import ConflictException
from tempest_fastapi_sdk.exceptions.not_found import NotFoundException
from tempest_fastapi_sdk.exceptions.validation import ValidationException
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
    * non-string iterable values (``list`` / ``set`` / ``tuple`` /
      ``frozenset`` / ``range`` / generator / ``dict`` view) →
      ``.in_(values)`` membership; the iterable is materialized once, so
      passing a ``set`` needs no manual conversion to a ``list``.
    * ``date`` values → ``func.date(column) == value`` whole-day match.
    * ``start_in`` / ``end_in`` (date) → range filter against the
      model's ``date`` column when present, falling back to
      ``created_at``.
    * ``<column>__<op>`` suffix → operator filter, where ``<op>`` is one of:
      ``gt`` / ``gte`` / ``lt`` / ``lte`` / ``ne`` (comparison, e.g.
      ``{"updated_at__gt": watermark}`` → ``updated_at > watermark``),
      ``in`` / ``notin`` / ``not_in`` (membership; ``not_in`` aliases
      ``notin``), ``between`` (``{"price__between": (10, 20)}`` →
      ``price BETWEEN 10 AND 20``; value is an ordered two-item list/tuple),
      ``iexact`` (case-insensitive equality), ``like`` / ``ilike`` (raw
      un-escaped ``LIKE`` with your own wildcards), ``isnull``, and
      ``contains`` / ``icontains`` / ``startswith`` / ``endswith`` (escaped
      ``ILIKE``). Comparison suffixes are timestamp-precise, unlike
      ``start_in`` / ``end_in`` (whole-day); this is what delta-sync queries
      filter on. A ``None`` value skips the condition, like every other
      filter.

    All error messages can be customized per repository instance via
    the constructor kwargs (``not_found_message``,
    ``create_conflict_message``, etc.); when omitted, sensible defaults
    derived from ``self.model.__name__`` are used.

    Each message has a matching **exception class** kwarg
    (``not_found_exception``, ``conflict_exception`` and the
    per-operation ``create_conflict_exception`` / ``update_…`` /
    ``bulk_create_…`` / ``bulk_update_…``). They exist because a message
    alone cannot be branched on: the default ``ConflictException``
    answers ``code = "CONFLICT"``, so every duplicate-key failure in the
    service looks identical to a client. Passing a domain subclass —
    which declares its own ``code`` — makes the 409 identifiable without
    the repository knowing anything about the domain.

    The same three abstract mappers ``map_to_schema`` / ``map_to_model``
    / ``map_to_response`` are kept so concrete repositories own the
    translation between ORM rows and DTOs.

    Attributes:
        model (type[ModelType]): The SQLAlchemy model class operated on.
        not_found_exception (type[AppException]): Exception class raised
            when single-record lookups miss.
        create_conflict_exception (type[AppException]): Exception class
            raised when ``add`` / ``save_with_outbox`` / ``add_audited``
            hit ``IntegrityError``.
        update_conflict_exception (type[AppException]): Same, for
            ``update`` / ``update_audited``.
        bulk_create_conflict_exception (type[AppException]): Same, for
            ``add_all`` / ``bulk_create_values`` / ``bulk_upsert``.
        bulk_update_conflict_exception (type[AppException]): Same, for
            ``update_many`` / ``bulk_update``.
        session (AsyncSession): The async database session.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        model: type[ModelType],
        not_found_exception: type[AppException] = NotFoundException,
        conflict_exception: type[AppException] = ConflictException,
        create_conflict_exception: type[AppException] | None = None,
        update_conflict_exception: type[AppException] | None = None,
        bulk_create_conflict_exception: type[AppException] | None = None,
        bulk_update_conflict_exception: type[AppException] | None = None,
        not_found_message: str | None = None,
        create_conflict_message: str | None = None,
        update_conflict_message: str | None = None,
        bulk_create_conflict_message: str | None = None,
        bulk_update_conflict_message: str | None = None,
        audit_model: type[BaseAuditLogModel] | None = None,
        autocommit: bool = True,
    ) -> None:
        """Initialize the repository.

        Every ``*_message`` kwarg is optional — when not provided, the
        repository falls back to a generic message derived from the
        model class name (e.g. ``"User not found"``,
        ``"Conflict creating User"``).

        Every ``*_exception`` kwarg is optional too, and each one pairs
        with the ``*_message`` of the same name. Passing a domain
        subclass is what gives the failure a ``code`` of its own:
        ``ConflictException`` answers ``"CONFLICT"``, so a client cannot
        tell a duplicate coin pack apart from any other 409 (the same
        reason :class:`AppException` warns when a subclass declares no
        ``code``). Conflicts resolve most-specific-first —
        ``create_conflict_exception`` if given, else
        ``conflict_exception``, else :class:`ConflictException` — so one
        kwarg can cover every write, or each write can differ.

        The class is instantiated as ``cls(message=...)``, the same
        contract ``not_found_exception`` already has, so a subclass must
        accept a ``message`` keyword. Declaring ``code`` in the class
        body and taking ``message`` optionally satisfies both.

        Args:
            session (AsyncSession): The async database session.
            model (type[ModelType]): The SQLAlchemy model class this
                repository operates on. Required.
            not_found_exception (type[AppException]): Exception class
                raised when single-record lookups miss. Defaults to
                :class:`NotFoundException`; pass a domain-specific
                subclass for richer 404 messages. It is instantiated as
                ``exception_class(message=...)``, so a class whose
                ``__init__`` takes only the record id — the shape one
                writes first, since the id is what the *caller* holds —
                turns every miss into a ``TypeError``: a 500 where the
                404 belongs, with nothing naming the cause.
                :func:`~tempest_fastapi_sdk.not_found_exception` builds
                a class that accepts both call shapes.
            conflict_exception (type[AppException]): Exception class
                raised when a write hits ``IntegrityError``. Defaults to
                :class:`ConflictException`; the per-operation kwargs
                below override it.
            create_conflict_exception (type[AppException] | None):
                Overrides ``conflict_exception`` for ``add``,
                ``save_with_outbox`` and ``add_audited``.
            update_conflict_exception (type[AppException] | None):
                Overrides ``conflict_exception`` for ``update`` and
                ``update_audited``.
            bulk_create_conflict_exception (type[AppException] | None):
                Overrides ``conflict_exception`` for ``add_all``,
                ``bulk_create_values`` and ``bulk_upsert``.
            bulk_update_conflict_exception (type[AppException] | None):
                Overrides ``conflict_exception`` for ``update_many`` and
                ``bulk_update``.
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
            autocommit (bool): Whether a write method commits on its own.
                ``True`` (the default) keeps the historical behavior —
                ``add`` / ``update`` / ``delete`` each end in a
                ``COMMIT``. ``False`` makes every write flush instead,
                leaving the commit to an explicit :meth:`commit` or to a
                :meth:`transaction` block; reach for it when a whole
                repository belongs to a caller-owned unit of work. It
                does **not** disable :meth:`commit` — an explicit call
                still commits.

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
        self.create_conflict_exception: type[AppException] = (
            create_conflict_exception or conflict_exception
        )
        self.update_conflict_exception: type[AppException] = (
            update_conflict_exception or conflict_exception
        )
        self.bulk_create_conflict_exception: type[AppException] = (
            bulk_create_conflict_exception or conflict_exception
        )
        self.bulk_update_conflict_exception: type[AppException] = (
            bulk_update_conflict_exception or conflict_exception
        )
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
        self.autocommit: bool = autocommit

    async def _commit(self) -> None:
        """End a write method, committing only when this repository owns it.

        The write methods call this instead of ``session.commit()`` so
        one implementation decides, in one place, whether the statement
        becomes durable now or joins a larger unit of work. It flushes —
        making the rows visible to the rest of the transaction without
        committing — in either of two cases:

        * a :func:`~tempest_fastapi_sdk.db.transaction.transaction` block
          is open on the session, so the block owns the single commit; or
        * the repository was built with ``autocommit=False``, so the
          caller owns it.

        Otherwise it commits, which is the default single-write behavior.
        """
        if self.autocommit and not in_transaction(self.session):
            await self.session.commit()
            return
        await self.session.flush()

    async def _rollback_after_failure(self) -> None:
        """Undo a failed write, unless an open block owns the rollback.

        SQLAlchemy requires a rollback after a failed flush before the
        session can be used again, which is why each write method calls
        this in its error path. Inside a
        :func:`~tempest_fastapi_sdk.db.transaction.transaction` block,
        rolling back here would silently discard every earlier write in
        the block while the caller is still holding it open — so the
        rollback is left to the block, which performs it as the
        exception propagates out.
        """
        if in_transaction(self.session):
            return
        await self.session.rollback()

    async def commit(self) -> None:
        """Commit the session's pending work.

        Exists because the repository is the boundary that owns the
        session: a service composing business rules should be able to
        say "this is the durable point" without reaching for
        ``repository.session`` and coupling itself to SQLAlchemy. It is
        also more honest than the alternative it replaces — calling
        ``update`` purely for its commit side effect.

        Inside an open
        :func:`~tempest_fastapi_sdk.db.transaction.transaction` block
        this flushes instead, because committing there would break the
        block's all-or-nothing guarantee. That makes the call safe to
        leave in place when a caller later wraps the code in a block,
        which a bare ``session.commit()`` is not.
        """
        if in_transaction(self.session):
            await self.session.flush()
            return
        await self.session.commit()

    async def flush(self) -> None:
        """Send pending changes to the database without committing.

        Use it to make a row visible to subsequent statements in the
        same transaction — typically to read back a server-generated
        primary key before inserting a child row that references it.

        This forwards to the session unconditionally; it is the one
        member of the transaction group with no branching of its own,
        and it is kept for the same reason as :meth:`commit` — so
        callers never need a reference to ``session``.
        """
        await self.session.flush()

    async def rollback(self) -> None:
        """Discard the session's pending, uncommitted work.

        Raises:
            RuntimeError: When a
                :func:`~tempest_fastapi_sdk.db.transaction.transaction`
                block is open. A rollback there would discard the whole
                block — including writes made by other repositories
                sharing the session — while the caller believes it is
                undoing only its own step. Raise or let the exception
                propagate to abort the block, or wrap the recoverable
                part in :meth:`savepoint`.
        """
        if in_transaction(self.session):
            raise RuntimeError(
                "rollback() inside an open transaction() block would discard "
                "the entire block, not just this repository's work. Let the "
                "exception propagate to abort the block, or use savepoint() "
                "for a step you intend to recover from.",
            )
        await self.session.rollback()

    def transaction(self) -> AbstractAsyncContextManager[AsyncSession]:
        """Group every write on this session into a single commit.

        Sugar for
        :func:`tempest_fastapi_sdk.db.transaction.transaction` bound to
        this repository's session. Because the block is tracked on the
        session — not on the repository — repositories that share the
        session join the same block:

        ```python
        async with orders.transaction():
            await orders.add(order)
            await items.add_all(rows)   # different repository, same block
        ```

        Returns:
            AbstractAsyncContextManager[AsyncSession]: The block; the
            session is yielded so it can be bound with ``as``.
        """
        return transaction(self.session)

    def explain(
        self,
        *,
        analyze: bool = True,
    ) -> AbstractAsyncContextManager[ExplainReport]:
        """Capture the query plan of everything run inside the block.

        Sugar for
        :func:`tempest_fastapi_sdk.db.explain.explain_queries` bound to
        this repository's session. A development tool — it re-runs each
        captured ``SELECT`` to measure it, so it does not belong in a
        hot request path:

        ```python
        async with repository.explain() as report:
            await repository.paginate(filters={"status": "open"}, page=3)
        print(report.report())
        ```

        Args:
            analyze (bool): Whether ``SELECT`` statements may be executed
                a second time to collect measured timings. Writes are
                never re-executed regardless.

        Returns:
            AbstractAsyncContextManager[ExplainReport]: The block; the
            report fills in on exit.
        """
        return explain_queries(self.session, analyze=analyze)

    def savepoint(self) -> AbstractAsyncContextManager[AsyncSession]:
        """Run a nested, individually revertible unit of work.

        Sugar for :func:`tempest_fastapi_sdk.db.transaction.savepoint`
        bound to this repository's session. Use it for a step whose
        failure you intend to catch without losing the surrounding work.

        Returns:
            AbstractAsyncContextManager[AsyncSession]: The savepoint
            block; the session is yielded so it can be bound with ``as``.
        """
        return savepoint(self.session)

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

    def _apply_where(self, query: Any, where: WhereClause | None) -> Any:
        """Apply a :class:`Q` tree or a ready-made clause to a query.

        A :class:`Q` is resolved against ``self.model`` first; anything
        else is already a bound SQLAlchemy clause and goes straight into
        the ``WHERE``. That second path is what lets a search condition
        from :mod:`tempest_fastapi_sdk.db.search` compose with the normal
        read methods instead of needing its own pagination.

        Args:
            query: The SQLAlchemy query to mutate.
            where (WhereClause | None): The condition tree, a resolved
                clause, or ``None``.

        Returns:
            The query, with the resolved clause added when non-empty.
        """
        if where is None:
            return query
        clause = where.resolve(self.model) if isinstance(where, Q) else where
        if clause is not None:
            query = query.where(clause)
        return query

    @property
    def dialect(self) -> str:
        """Return the SQLAlchemy dialect name of the bound database.

        Read at call time rather than cached at construction because the
        same repository class runs against PostgreSQL in production and
        SQLite under test, and the search layer branches on it.

        Returns:
            str: The dialect name (``"postgresql"``, ``"sqlite"``, …).
        """
        return self.session.get_bind().dialect.name

    @property
    def supports_full_text(self) -> bool:
        """Whether the bound database can rank a full-text search.

        Lets a caller tell a ranked result from an unranked one — on
        SQLite :meth:`full_text_search` still returns the right rows, it
        just cannot order them by relevance.

        Returns:
            bool: ``True`` when the backend has a full-text engine.
        """
        return supports_full_text(self.dialect)

    def search_condition(
        self,
        term: str,
        *,
        fields: Sequence[ColumnRef],
        token_match: TokenMatch = TokenMatch.ALL,
    ) -> ColumnElement[bool] | None:
        """Build a portable substring-search condition over ``fields``.

        Identical on every backend: each whitespace-separated token is
        matched case-insensitively against every listed column, with the
        user's own ``%`` and ``_`` escaped so they match literally.

        Returned rather than executed so it composes with the normal
        reads — pass it as ``where=`` to :meth:`paginate` and the search
        is paginated and counted like any other filter:

        ```python
        page = await repository.paginate(
            where=repository.search_condition("joao", fields=["name", "email"]),
            page=2,
        )
        ```

        Args:
            term (str): The raw search term.
            fields (Sequence[ColumnRef]): Columns to search, by name or
                as mapped attributes (``UserModel.name``).
            token_match (TokenMatch): Whether every token must match
                (default) or any single one is enough.

        Returns:
            ColumnElement[bool] | None: The condition, or ``None`` for a
            blank term — passing that back as ``where=`` applies no
            filter, so an empty search box lists everything.

        Raises:
            ValidationException: When ``fields`` is empty or names a
                column the model does not have.
        """
        return like_search_condition(self.model, term, fields, token_match=token_match)

    def full_text_condition(
        self,
        term: str,
        *,
        fields: Sequence[ColumnRef],
        language: TextSearchLanguage = TextSearchLanguage.PORTUGUESE,
        weights: Mapping[str, TextSearchWeight] | None = None,
        token_match: TokenMatch = TokenMatch.ALL,
    ) -> ColumnElement[bool] | None:
        """Build a full-text condition, or the portable fallback.

        On PostgreSQL this stems the term (so ``comprou`` finds
        ``comprar``), drops stop words and accepts the search syntax
        users already type — ``"exact phrase"``, ``-excluded``. On any
        other backend it degrades to :meth:`search_condition`, which
        finds the right rows without stemming or ranking.

        Args:
            term (str): The raw search term.
            fields (Sequence[ColumnRef]): Columns to search.
            language (TextSearchLanguage): Stemming configuration.
            weights (Mapping[str, TextSearchWeight] | None): Per-column
                weight for the relevance score, keyed by column name.
                Pass the same mapping to :meth:`full_text_search`.
            token_match (TokenMatch): Used only by the fallback path.

        Returns:
            ColumnElement[bool] | None: The condition, or ``None`` for a
            blank term.

        Raises:
            ValidationException: When ``fields`` is empty or names a
                column the model does not have.
        """
        return build_full_text_condition(
            self.model,
            term,
            fields,
            language=language,
            weights=weights,
            dialect=self.dialect,
            token_match=token_match,
        )

    def _resolve_order_column(self, order_by: str) -> Any:
        """Resolve ``order_by`` to a real column on the model.

        ``order_by`` reaches the repository straight from a query parameter
        (:class:`~tempest_fastapi_sdk.BasePaginationFilterSchema` declares it
        as a plain ``str``), so it is untrusted input. A bare
        ``getattr(self.model, order_by)`` turned any other name into an
        ``AttributeError`` — and, worse, a name that happens to exist on the
        class but is not a column (``metadata``, ``registry``) into an
        ``AttributeError`` one frame later on ``.desc()``. Both surfaced as an
        HTTP 500 on a request that is merely wrong.

        Resolution goes through the mapper's column set, so only mapped
        columns are orderable and anything else is a 422.

        Args:
            order_by (str): The column name requested by the caller.

        Returns:
            Any: The ``InstrumentedAttribute`` to order by.

        Raises:
            ValidationException: When ``order_by`` is not a mapped column.
        """
        mapper = inspect(self.model)
        if order_by in mapper.columns:
            column: Any = getattr(self.model, order_by)
            return column
        raise ValidationException(
            message=f"{self.model.__name__!r} has no column {order_by!r}",
            details={
                "order_by": order_by,
                "allowed": sorted(mapper.columns.keys()),
            },
        )

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
        where: WhereClause | None = None,
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
            where (WhereClause | None): A :class:`Q` condition tree ANDed with
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
        where: WhereClause | None = None,
    ) -> ModelType | None:
        """Return the single record matching ``filters`` or ``None``.

        Unlike :meth:`get`, never raises when nothing matches.

        Args:
            filters (dict[str, Any]): The column-value pairs.
            for_update (bool): Whether to acquire a row-level lock.
            with_ (list[str] | None): Relationship paths to eager-load;
                see :meth:`get`.
            where (WhereClause | None): A :class:`Q` condition tree ANDed with
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
        where: WhereClause | None = None,
    ) -> bool:
        """Return whether at least one row matches ``filters``.

        Executes a ``SELECT 1 ... LIMIT 1`` so the row is never
        fully loaded.

        Args:
            filters (dict[str, Any]): The filter conditions.
            where (WhereClause | None): A :class:`Q` condition tree ANDed with
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
        where: WhereClause | None = None,
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
            where (WhereClause | None): A :class:`Q` condition tree ANDed with
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
        where: WhereClause | None = None,
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
            where (WhereClause | None): A :class:`Q` condition tree ANDed with
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

    async def search(
        self,
        term: str,
        *,
        fields: Sequence[ColumnRef],
        token_match: TokenMatch = TokenMatch.ALL,
        filters: dict[str, Any] | None = None,
        where: WhereClause | None = None,
        order_by: Any | None = None,
        ascending: bool = True,
        with_: List[str] | None = None,
        limit: int | None = None,
    ) -> List[ModelType]:
        """Return rows whose ``fields`` contain the search term.

        The portable layer: same behavior on PostgreSQL and SQLite, no
        index or extension required. Each whitespace-separated token is
        matched case-insensitively against every listed column, and the
        user's own ``%`` / ``_`` are escaped so they match literally.

        Returns ``[]`` when nothing matches, per the SDK collection
        convention. A blank term applies no text filter, so the result
        is whatever ``filters`` / ``where`` alone select — an empty
        search box lists rather than hides.

        Args:
            term (str): The raw search term.
            fields (Sequence[ColumnRef]): Columns to search, by name or
                as mapped attributes (``UserModel.name``).
            token_match (TokenMatch): Whether every token must match
                (default) or any single one is enough.
            filters (dict[str, Any] | None): Extra conditions ANDed with
                the search, using the usual filter conventions.
            where (WhereClause | None): A further condition ANDed in.
            order_by: A SQLAlchemy column expression. ``None`` keeps
                insertion order — this layer has no relevance score to
                order by; use :meth:`full_text_search` for that.
            ascending (bool): Order direction; ignored without
                ``order_by``.
            with_ (List[str] | None): Relationship paths to eager-load.
            limit (int | None): Cap on rows returned.

        Returns:
            List[ModelType]: The matching rows.

        Raises:
            ValidationException: When ``fields`` is empty or names a
                column the model does not have.
            ValueError: When a ``with_`` path names a non-relationship.
        """
        condition = like_search_condition(
            self.model, term, fields, token_match=token_match
        )
        return await self._run_search(
            condition,
            rank=None,
            filters=filters,
            where=where,
            order_by=order_by,
            ascending=ascending,
            with_=with_,
            limit=limit,
        )

    async def full_text_search(
        self,
        term: str,
        *,
        fields: Sequence[ColumnRef],
        language: TextSearchLanguage = TextSearchLanguage.PORTUGUESE,
        weights: Mapping[str, TextSearchWeight] | None = None,
        token_match: TokenMatch = TokenMatch.ALL,
        filters: dict[str, Any] | None = None,
        where: WhereClause | None = None,
        order_by: Any | None = None,
        ascending: bool = True,
        with_: List[str] | None = None,
        limit: int | None = None,
    ) -> List[ModelType]:
        """Return rows matching the term, ranked by relevance on PostgreSQL.

        Stems the term against ``language`` (so ``comprou`` finds
        ``comprar``), drops stop words, and accepts the syntax users
        already type in a search box — ``"exact phrase"`` and
        ``-excluded``. Results come back ordered by ``ts_rank``,
        strongest first.

        On a backend without full-text support this degrades to
        :meth:`search`: the right rows, unranked and unstemmed. Read
        :attr:`supports_full_text` when the caller needs to know which
        of the two it got.

        Args:
            term (str): The raw search term.
            fields (Sequence[ColumnRef]): Columns to search.
            language (TextSearchLanguage): Stemming configuration.
            weights (Mapping[str, TextSearchWeight] | None): Per-column
                weight for the score, keyed by column name; columns left
                out rank lowest. A term in a title outranking the same
                term in a body is this parameter.
            token_match (TokenMatch): Used only on the fallback path;
                PostgreSQL already treats separate words as ``AND``.
            filters (dict[str, Any] | None): Extra conditions ANDed in.
            where (WhereClause | None): A further condition ANDed in.
            order_by: An explicit ordering that **replaces** the
                relevance ranking. Leave it ``None`` to rank.
            ascending (bool): Direction for an explicit ``order_by``.
            with_ (List[str] | None): Relationship paths to eager-load.
            limit (int | None): Cap on rows returned.

        Returns:
            List[ModelType]: The matching rows, ranked where the backend
            can rank them.

        Raises:
            ValidationException: When ``fields`` is empty or names a
                column the model does not have.
            ValueError: When a ``with_`` path names a non-relationship.
        """
        condition = build_full_text_condition(
            self.model,
            term,
            fields,
            language=language,
            weights=weights,
            dialect=self.dialect,
            token_match=token_match,
        )
        rank = (
            None
            if order_by is not None
            else full_text_rank(
                self.model,
                term,
                fields,
                language=language,
                weights=weights,
                dialect=self.dialect,
            )
        )
        return await self._run_search(
            condition,
            rank=rank,
            filters=filters,
            where=where,
            order_by=order_by,
            ascending=ascending,
            with_=with_,
            limit=limit,
        )

    async def _run_search(
        self,
        condition: ColumnElement[bool] | None,
        *,
        rank: ColumnElement[Any] | None,
        filters: dict[str, Any] | None,
        where: WhereClause | None,
        order_by: Any | None,
        ascending: bool,
        with_: List[str] | None,
        limit: int | None,
    ) -> List[ModelType]:
        """Execute a search query built by :meth:`search` or its full-text peer.

        Shared so both entry points assemble the query the same way and
        only differ in the condition and the ordering they hand in.

        Args:
            condition (ColumnElement[bool] | None): The text condition,
                or ``None`` for a blank term (no text filter applied).
            rank (ColumnElement[Any] | None): Relevance expression to
                order by, descending. ``None`` leaves ordering to
                ``order_by``.
            filters (dict[str, Any] | None): Extra filter conditions.
            where (WhereClause | None): A further condition ANDed in.
            order_by: Explicit ordering, applied when ``rank`` is
                ``None``.
            ascending (bool): Direction for ``order_by``.
            with_ (List[str] | None): Relationship paths to eager-load.
            limit (int | None): Cap on rows returned.

        Returns:
            List[ModelType]: The matching rows.
        """
        query = select(self.model)
        if condition is not None:
            query = query.where(condition)
        if filters:
            query = self._apply_filters(query, filters)
        query = self._apply_where(query, where)
        if with_:
            query = query.options(*self._relationship_options(with_))
        if rank is not None:
            query = query.order_by(rank.desc())
        elif order_by is not None:
            query = query.order_by(order_by if ascending else order_by.desc())
        if limit is not None:
            query = query.limit(limit)

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
        where: WhereClause | None = None,
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
            where (WhereClause | None): A :class:`Q` condition tree ANDed with
                ``filters``; applied to both the page and its count.

        Returns:
            dict[str, Any]: A mapping with keys ``items``, ``total``,
            ``page``, ``size``, ``pages``.

        Raises:
            ValidationException: When ``order_by`` names something that is
                not a mapped column. It arrives from a query parameter, so
                a bad value answers 422 rather than crashing the request.
        """
        if query is None:
            query = select(self.model)

        if filters:
            query = self._apply_filters(query, filters)

        query = self._apply_where(query, where)

        if order_by is None:
            query = query.order_by(self.model.created_at.desc())
        else:
            column = self._resolve_order_column(order_by)
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
            ValidationException: When ``order_by`` is not a mapped column
                on the model — it comes from a query parameter, so a bad
                value is a 422 and not a server error.
            ValueError: When ``cursor`` is malformed.
        """
        from tempest_fastapi_sdk.schemas.pagination import (
            decode_cursor,
            encode_cursor,
        )

        column = self._resolve_order_column(order_by)

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
            ValidationException: When ``order_by`` is not a mapped column
                on the model.
            ValueError: When ``cursor`` is malformed.
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
            await self._commit()
            await self.session.refresh(model)
            await self._emit_signal(RepositorySignal.POST_SAVE, model)
            return model
        except IntegrityError as exc:
            await self._rollback_after_failure()
            logger.warning(
                "IntegrityError on %s.add: %s", self.model.__name__, exc.orig
            )
            raise self.create_conflict_exception(
                message=self._create_conflict_message,
            ) from exc
        except Exception:
            await self._rollback_after_failure()
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
            await self._commit()
            for model in models:
                await self.session.refresh(model)
                await self._emit_signal(RepositorySignal.POST_SAVE, model)
            return models
        except IntegrityError as exc:
            await self._rollback_after_failure()
            logger.warning(
                "IntegrityError on %s.add_all: %s", self.model.__name__, exc.orig
            )
            raise self.bulk_create_conflict_exception(
                message=self._bulk_create_conflict_message,
            ) from exc
        except Exception:
            await self._rollback_after_failure()
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
            await self._commit()
            await self.session.refresh(model)
            return model
        except IntegrityError as exc:
            await self._rollback_after_failure()
            logger.warning(
                "IntegrityError on %s.save_with_outbox: %s",
                self.model.__name__,
                exc.orig,
            )
            raise self.create_conflict_exception(
                message=self._create_conflict_message,
            ) from exc
        except Exception:
            await self._rollback_after_failure()
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
            await self._commit()
            await self.session.refresh(model)
            return model
        except IntegrityError as exc:
            await self._rollback_after_failure()
            logger.warning(
                "IntegrityError on %s.add_audited: %s",
                self.model.__name__,
                exc.orig,
            )
            raise self.create_conflict_exception(
                message=self._create_conflict_message,
            ) from exc
        except Exception:
            await self._rollback_after_failure()
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
            await self._commit()
            await self.session.refresh(model)
            return model
        except IntegrityError as exc:
            await self._rollback_after_failure()
            logger.warning(
                "IntegrityError on %s.update_audited: %s",
                self.model.__name__,
                exc.orig,
            )
            raise self.update_conflict_exception(
                message=self._update_conflict_message,
            ) from exc
        except Exception:
            await self._rollback_after_failure()
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
            await self._commit()
        except Exception:
            await self._rollback_after_failure()
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
            await self._commit()
            await self.session.refresh(model)
            await self._emit_signal(RepositorySignal.POST_SAVE, model)
            return model
        except IntegrityError as exc:
            await self._rollback_after_failure()
            logger.warning(
                "IntegrityError on %s.update: %s", self.model.__name__, exc.orig
            )
            raise self.update_conflict_exception(
                message=self._update_conflict_message,
            ) from exc
        except Exception:
            await self._rollback_after_failure()
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
            await self._commit()
            for model in models:
                await self._emit_signal(RepositorySignal.POST_SAVE, model)
            return models
        except IntegrityError as exc:
            await self._rollback_after_failure()
            logger.warning(
                "IntegrityError on %s.update_many: %s",
                self.model.__name__,
                exc.orig,
            )
            raise self.bulk_update_conflict_exception(
                message=self._bulk_update_conflict_message,
            ) from exc
        except Exception:
            await self._rollback_after_failure()
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
            await self._commit()
            return result.rowcount or 0
        except IntegrityError as exc:
            await self._rollback_after_failure()
            logger.warning(
                "IntegrityError on %s.bulk_update: %s",
                self.model.__name__,
                exc.orig,
            )
            raise self.bulk_update_conflict_exception(
                message=self._bulk_update_conflict_message,
            ) from exc
        except Exception:
            await self._rollback_after_failure()
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
            await self._commit()
            return result.rowcount or len(rows)
        except IntegrityError as exc:
            await self._rollback_after_failure()
            logger.warning(
                "IntegrityError on %s.bulk_create_values: %s",
                self.model.__name__,
                exc.orig,
            )
            raise self.bulk_create_conflict_exception(
                message=self._bulk_create_conflict_message,
            ) from exc
        except Exception:
            await self._rollback_after_failure()
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
            await self._commit()
            return result.rowcount or len(rows)
        except IntegrityError as exc:
            await self._rollback_after_failure()
            logger.warning(
                "IntegrityError on %s.bulk_upsert: %s",
                self.model.__name__,
                exc.orig,
            )
            raise self.bulk_create_conflict_exception(
                message=self._bulk_create_conflict_message,
            ) from exc
        except Exception:
            await self._rollback_after_failure()
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
            await self._commit()

            if instance is not None:
                await self._emit_signal(RepositorySignal.POST_DELETE, instance)
        except AppException:
            raise
        except Exception:
            await self._rollback_after_failure()
            raise

    async def delete_many(
        self,
        filters: dict[str, Any],
        where: WhereClause | None = None,
    ) -> int:
        """Delete every row matching ``filters``.

        An empty ``filters`` dict (and no ``where``) deletes every row
        in the table. Callers must opt in explicitly — the behavior is
        intentional.

        Args:
            filters (dict[str, Any]): The conditions identifying the
                rows to delete.
            where (WhereClause | None): A :class:`Q` condition tree ANDed with
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
            await self._commit()
            return result.rowcount or 0
        except Exception:
            await self._rollback_after_failure()
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
            await self._commit()
            return result.rowcount or 0
        except Exception:
            await self._rollback_after_failure()
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
        where: WhereClause | None = None,
    ) -> int:
        """Count the rows matching ``filters``.

        Args:
            filters (dict[str, Any] | None): The filter conditions.
            where (WhereClause | None): A :class:`Q` condition tree ANDed with
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
