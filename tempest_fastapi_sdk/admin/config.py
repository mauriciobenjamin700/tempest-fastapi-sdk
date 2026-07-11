"""Declarative configuration objects describing a managed model."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

from sqlalchemy.inspection import inspect
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import UnaryExpression

from tempest_fastapi_sdk.admin.actions import (
    ActionHandler,
    AdminAction,
    resolve_admin_action,
)
from tempest_fastapi_sdk.db.audit import BaseAuditLogModel
from tempest_fastapi_sdk.db.model import BaseModel
from tempest_fastapi_sdk.db.repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.utils.storage_backends import UploadStorage

ModelT = TypeVar("ModelT", bound=BaseModel)

FieldRef = InstrumentedAttribute[Any] | str
"""A column reference: either a mapped attribute (``Model.email``) or its
string key (``"email"``). Column references give editor autocomplete and
typo-checking; strings remain accepted for dynamic configuration."""

OrderRef = InstrumentedAttribute[Any] | UnaryExpression[Any] | str
"""An ordering reference: a column (``Model.created_at``, ascending), a
direction-wrapped column (``desc(Model.created_at)``), or a Django-style
string (``"created_at"`` / ``"-created_at"``)."""


class Inline:
    """A related child model surfaced on the parent's detail view.

    Django's ``TabularInline`` analog: the detail view of the parent
    lists the child rows that point back at it (via ``fk_field``) as a
    compact table, with a link to each child's own admin and an "Add"
    link that pre-fills the parent foreign key. The child model must
    have its own registered :class:`AdminModel` for the links to work
    (the inline reuses the child admin's ``list_display`` and CRUD
    routes); without it, the rows still render read-only.

    Attributes:
        model (type[BaseModel]): The child model class.
        fk_field (str): The child column that references the parent.
        list_display (list[str] | None): Columns to show; falls back to
            the child admin's ``list_display`` (or every column).
        label (str | None): Section heading; defaults to the model name.
    """

    def __init__(
        self,
        model: type[BaseModel],
        fk_field: FieldRef,
        *,
        list_display: Sequence[FieldRef] | None = None,
        label: str | None = None,
    ) -> None:
        """Build the inline config. See class docstring.

        Args:
            model (type[BaseModel]): The child model class.
            fk_field (FieldRef): The child column referencing the parent.
            list_display (Sequence[FieldRef] | None): Columns to show.
            label (str | None): Section heading.

        Raises:
            TypeError: When ``model`` is not a ``BaseModel`` subclass.
        """
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            raise TypeError("Inline `model` must be a subclass of BaseModel")
        self.model: type[BaseModel] = model
        self.fk_field: str = _field_key(fk_field)
        self.list_display: list[str] | None = (
            None if list_display is None else _normalize_fields(list_display)
        )
        self.label: str | None = label

    def get_slug(self) -> str:
        """Return the child model's admin slug (its table name).

        Returns:
            str: The ``__tablename__`` used to look the child admin up.
        """
        return str(self.model.__tablename__)

    def get_label(self) -> str:
        """Return the section heading for this inline.

        Returns:
            str: The configured label, or a name derived from the model.
        """
        return self.label or f"{self.model.__name__}"


class Lens:
    """A named, saved list-view preset (Nova-style lens).

    A lens bundles a set of filters and an optional ordering under a
    label. On the list view lenses render as tabs; clicking one applies
    its filters (ANDed with the user's search/filters) and ordering. A
    "support triage" lens might pin ``{"status": "open", "priority__gte": 3}``
    sorted by oldest-first, so an operator reaches the working set in one
    click instead of re-entering filters.

    Attributes:
        name (str): The lens identifier; its slug (lowercased,
            spaces→hyphens) is the ``?lens=`` value.
        filters (dict[str, Any]): Filter conditions merged into the
            query — same conventions as a repository filter dict
            (``field__gte`` etc.).
        order_by (str | None): Column to order by; ``-col`` for
            descending. Applied unless the user clicked a column sort.
        label (str | None): Tab label; defaults to ``name``.
    """

    def __init__(
        self,
        name: str,
        *,
        filters: dict[str, Any] | None = None,
        order_by: str | None = None,
        label: str | None = None,
    ) -> None:
        """Build a lens. See class docstring.

        Args:
            name (str): The lens identifier.
            filters (dict[str, Any] | None): Filter conditions.
            order_by (str | None): Ordering column (``-col`` = desc).
            label (str | None): Tab label.
        """
        self.name: str = name
        self.filters: dict[str, Any] = dict(filters or {})
        self.order_by: str | None = order_by
        self.label: str | None = label

    def slug(self) -> str:
        """Return the URL slug (``?lens=`` value) for this lens.

        Returns:
            str: Lowercased name with non-alphanumeric runs hyphenated.
        """
        return "-".join(
            "".join(c if c.isalnum() else " " for c in self.name).split()
        ).lower()

    def get_label(self) -> str:
        """Return the tab label.

        Returns:
            str: The configured label, or the name.
        """
        return self.label or self.name


class AdminModel(Generic[ModelT]):
    """Declarative admin configuration for one SQLAlchemy model.

    Instantiate once per managed model and pass it to
    :meth:`AdminSite.register`. Unlike Django's class-based ``ModelAdmin``,
    this is a plain typed instance — the constructor signature is the
    contract, fields accept real SQLAlchemy column attributes (so typos
    surface in the editor, not at runtime), and there is no metaclass
    magic::

        site.register(AdminModel(
            model=UserModel,
            list_display=[UserModel.email, UserModel.is_admin],
            search_fields=[UserModel.email],
            ordering=desc(UserModel.created_at),
        ))

    Args:
        model (type[ModelT]): The SQLAlchemy model class.
        list_display (Sequence[FieldRef] | None): Columns shown in the
            list view. ``None`` defaults to every column except the
            password hash.
        list_filter (Sequence[FieldRef]): Fields surfaced as filter
            dropdowns; matched via the repository's standard filter
            pipeline.
        search_fields (Sequence[FieldRef]): String columns searched with
            ``ILIKE %value%`` via the repository's ``name`` convention.
        readonly_fields (Sequence[FieldRef]): Fields locked in the detail
            view.
        ordering (OrderRef | None): Default ordering. Accepts a column
            (ascending), ``desc(column)`` / ``asc(column)``, or a string
            column name with an optional leading ``-`` for descending.
            ``None`` falls back to ``created_at`` descending.
        page_size (int): Default rows per page in the list view.
        identity_field (FieldRef): Column used to look up a single row
            from the detail URL. Defaults to ``"id"`` (UUID PK).
        repository_class (type[BaseRepository[Any]] | None): Concrete
            repository. ``None`` synthesizes an anonymous repository bound
            to :attr:`model`.
        verbose_name (str | None): Singular display name; defaults to the
            model name humanized.
        verbose_name_plural (str | None): Plural display name; defaults to
            ``verbose_name + "s"``.
        can_create (bool): Whether the admin exposes the create form +
            POST endpoint. Default ``True``.
        can_edit (bool): Whether the admin exposes the edit form + POST
            endpoint. Default ``True``.
        can_delete (bool): Whether the admin exposes the delete action.
            Default ``True``.
        can_import (bool): Whether the admin exposes a CSV import page
            (``GET/POST {prefix}/m/{slug}/import``) that bulk-creates
            rows from an uploaded CSV. Default ``False`` (opt-in); also
            requires ``can_create``.
        actions (Sequence[ActionHandler]): Custom bulk actions —
            functions decorated with
            :func:`~tempest_fastapi_sdk.admin.admin_action`. Each appears
            in the list view's bulk-action dropdown alongside the built-in
            activate / deactivate / delete.
        upload_fields (Sequence[FieldRef]): String columns rendered as
            file inputs in the create/edit form. The uploaded file is
            saved through ``upload_storage`` and the returned storage key
            is written to the column.
        upload_storage (UploadStorage | None): Backend used to persist
            uploaded files (``LocalUploadStorage`` / ``MinIOUploadStorage``).
            Required when ``upload_fields`` is non-empty.
        audit_model (type[BaseAuditLogModel] | None): When set, the detail
            view renders a per-row change timeline read from this audit
            table (matched on ``entity`` = the model name and
            ``entity_id`` = the row id). Pair it with
            ``BaseRepository(audit_model=...)`` +
            ``add_audited`` / ``update_audited`` / ``delete_audited`` so the
            history is actually written. ``None`` (default) shows only the
            ``created_by`` / ``updated_by`` stamps.
        autocomplete_fields (Sequence[FieldRef]): Foreign-key columns
            rendered as a typed search box (HTMX) instead of a
            ``<select>`` of every related row — removing the 1000-row cap
            and the plain-UUID fallback for large target tables. The
            target table must have its own registered ``AdminModel``
            (its ``search_fields`` drive the search).
        inlines (Sequence[Inline]): Related child models listed on this
            model's detail view (Django ``TabularInline`` analog). Each
            :class:`Inline` shows the child rows pointing back via its
            ``fk_field`` plus links to the child admin and an "Add" link
            that pre-fills the parent foreign key.
        lenses (Sequence[Lens]): Named saved list-view presets (Nova-style
            lenses) rendered as tabs above the list; each applies its
            filters + ordering via ``?lens=<slug>``.

    Raises:
        TypeError: When ``model`` is not a subclass of :class:`BaseModel`,
            or when a field reference cannot be resolved to a column key.
    """

    def __init__(
        self,
        model: type[ModelT],
        *,
        list_display: Sequence[FieldRef] | None = None,
        list_filter: Sequence[FieldRef] = (),
        search_fields: Sequence[FieldRef] = (),
        readonly_fields: Sequence[FieldRef] = (),
        ordering: OrderRef | None = None,
        page_size: int = 25,
        identity_field: FieldRef = "id",
        repository_class: type[BaseRepository[Any]] | None = None,
        verbose_name: str | None = None,
        verbose_name_plural: str | None = None,
        can_create: bool = True,
        can_edit: bool = True,
        can_delete: bool = True,
        can_import: bool = False,
        actions: Sequence[ActionHandler] = (),
        upload_fields: Sequence[FieldRef] = (),
        upload_storage: UploadStorage | None = None,
        audit_model: type[BaseAuditLogModel] | None = None,
        autocomplete_fields: Sequence[FieldRef] = (),
        inlines: Sequence[Inline] = (),
        lenses: Sequence[Lens] = (),
    ) -> None:
        """Build and validate the configuration. See class docstring."""
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            raise TypeError("AdminModel `model` must be a subclass of BaseModel")

        self.model: type[ModelT] = model
        self.list_display: list[str] | None = (
            None if list_display is None else _normalize_fields(list_display)
        )
        self.list_filter: list[str] = _normalize_fields(list_filter)
        self.search_fields: list[str] = _normalize_fields(search_fields)
        self.readonly_fields: list[str] = _normalize_fields(readonly_fields)
        self.order_key: str | None
        self.order_ascending: bool
        self.order_key, self.order_ascending = _normalize_ordering(ordering)
        self.page_size: int = page_size
        self.identity_field: str = _field_key(identity_field)
        self.repository_class: type[BaseRepository[Any]] | None = repository_class
        self.verbose_name: str | None = verbose_name
        self.verbose_name_plural: str | None = verbose_name_plural
        self.can_create: bool = can_create
        self.can_edit: bool = can_edit
        self.can_delete: bool = can_delete
        self.can_import: bool = can_import
        self._actions: dict[str, AdminAction] = {}
        for func in actions:
            action = resolve_admin_action(func)
            if action.name in self._actions:
                raise ValueError(
                    f"Duplicate admin action name {action.name!r} on {model.__name__}",
                )
            self._actions[action.name] = action
        self.upload_fields: list[str] = _normalize_fields(upload_fields)
        self.upload_storage: UploadStorage | None = upload_storage
        if self.upload_fields and self.upload_storage is None:
            raise ValueError(
                "AdminModel `upload_fields` requires an `upload_storage` "
                "(e.g. LocalUploadStorage / MinIOUploadStorage).",
            )
        if audit_model is not None and (
            not isinstance(audit_model, type)
            or not issubclass(audit_model, BaseAuditLogModel)
        ):
            raise TypeError(
                "AdminModel `audit_model` must be a subclass of BaseAuditLogModel",
            )
        self.audit_model: type[BaseAuditLogModel] | None = audit_model
        self.autocomplete_fields: list[str] = _normalize_fields(autocomplete_fields)
        self.inlines: list[Inline] = list(inlines)
        self.lenses: list[Lens] = list(lenses)

    def get_lens(self, slug: str) -> Lens | None:
        """Return the registered lens whose slug matches, or ``None``.

        Args:
            slug (str): The ``?lens=`` value.

        Returns:
            Lens | None: The matching lens.
        """
        for lens in self.lenses:
            if lens.slug() == slug:
                return lens
        return None

    def get_verbose_name(self) -> str:
        """Return the configured (or auto-derived) singular display name.

        Returns:
            str: The display name.
        """
        if self.verbose_name:
            return self.verbose_name
        return _humanize(self.model.__name__.removesuffix("Model"))

    def get_verbose_name_plural(self) -> str:
        """Return the configured (or auto-derived) plural display name.

        Returns:
            str: The plural display name.
        """
        if self.verbose_name_plural:
            return self.verbose_name_plural
        return f"{self.get_verbose_name()}s"

    def get_slug(self) -> str:
        """Return the URL slug under which the model is exposed.

        Defaults to ``__tablename__`` so admin URLs and DB tables
        stay in sync.

        Returns:
            str: The slug.
        """
        return self.model.__tablename__

    def column_names(self) -> list[str]:
        """Return every mapped column name on :attr:`model`.

        Returns:
            list[str]: Column keys in declaration order.
        """
        return [attr.key for attr in inspect(self.model).mapper.column_attrs]

    def resolved_list_display(self) -> list[str]:
        """Return the effective ``list_display`` column list.

        Defaults to every column except ``hashed_password`` when
        unconfigured.

        Returns:
            list[str]: The list of columns to render.
        """
        if self.list_display is not None:
            return list(self.list_display)
        return [name for name in self.column_names() if name not in {"hashed_password"}]

    def editable_field_names(self) -> list[str]:
        """Return the columns a create/edit form should expose.

        Excludes the primary key, the audit timestamps
        (``created_at`` / ``updated_at``), the password hash, and any
        column listed in ``readonly_fields`` — none of which a user
        edits directly through the generic admin form.

        Returns:
            list[str]: Editable column keys in declaration order.
        """
        skip = set(self.readonly_fields) | {
            "id",
            "created_at",
            "updated_at",
            "hashed_password",
        }
        return [name for name in self.column_names() if name not in skip]

    def build_repository(self, session: AsyncSession) -> BaseRepository[ModelT]:
        """Instantiate the repository for ``session``.

        Uses :attr:`repository_class` when provided (typically a subclass
        adding custom queries), otherwise instantiates :class:`BaseRepository`
        directly with ``model=self.model``.

        Args:
            session (AsyncSession): The DB session to bind.

        Returns:
            BaseRepository[ModelT]: A repository ready to use.
        """
        if self.repository_class is not None:
            factory = cast(
                "Callable[[AsyncSession], BaseRepository[ModelT]]",
                self.repository_class,
            )
            return factory(session)
        return BaseRepository(session, model=self.model)

    def custom_actions(self) -> list[AdminAction]:
        """Return the registered custom actions in declaration order.

        Returns:
            list[AdminAction]: The actions passed via ``actions=`` (``[]``
            when none were registered).
        """
        return list(self._actions.values())

    def get_action(self, name: str) -> AdminAction | None:
        """Return the custom action named ``name``, or ``None``.

        Args:
            name (str): The action identifier (its submitted form value).

        Returns:
            AdminAction | None: The matching action, or ``None`` when no
            custom action with that name is registered.
        """
        return self._actions.get(name)


def _field_key(ref: FieldRef) -> str:
    """Resolve a field reference to its column key.

    Args:
        ref (FieldRef): A mapped attribute or its string key.

    Returns:
        str: The column key.

    Raises:
        TypeError: When ``ref`` is neither a string nor a mapped
            attribute exposing a ``key``.
    """
    if isinstance(ref, str):
        return ref
    key = getattr(ref, "key", None)
    if not isinstance(key, str):
        raise TypeError(
            f"Expected a column attribute or string, got {ref!r}",
        )
    return key


def _normalize_fields(refs: Sequence[FieldRef]) -> list[str]:
    """Resolve a sequence of field references to column keys.

    Args:
        refs (Sequence[FieldRef]): The references to normalize.

    Returns:
        list[str]: The resolved column keys.
    """
    return [_field_key(ref) for ref in refs]


def _normalize_ordering(ref: OrderRef | None) -> tuple[str | None, bool]:
    """Resolve an ordering reference to a ``(column_key, ascending)`` pair.

    Args:
        ref (OrderRef | None): A column, ``desc()``/``asc()`` wrapper, or
            string column name (optionally prefixed with ``-``). ``None``
            defers to the repository default.

    Returns:
        tuple[str | None, bool]: The column key (or ``None``) and whether
        ordering is ascending.

    Raises:
        TypeError: When ``ref`` cannot be resolved to a column key.
    """
    if ref is None:
        return (None, True)
    if isinstance(ref, str):
        if ref.startswith("-"):
            return (ref[1:], False)
        return (ref, True)
    if isinstance(ref, UnaryExpression):
        ascending = ref.modifier is not operators.desc_op
        element = ref.element
        key = getattr(element, "key", None) or getattr(element, "name", None)
        if not isinstance(key, str):
            raise TypeError(f"Cannot resolve ordering column from {ref!r}")
        return (key, ascending)
    return (_field_key(ref), True)


def _humanize(value: str) -> str:
    """Convert ``CamelCase`` or ``snake_case`` to ``Title Case``.

    Args:
        value (str): The raw identifier.

    Returns:
        str: The humanized label.
    """
    out: list[str] = []
    last: str = ""
    for char in value.replace("_", " "):
        if char.isupper() and last and not last.isspace() and not last.isupper():
            out.append(" ")
        out.append(char)
        last = char
    return "".join(out).strip().title()


__all__: list[str] = [
    "AdminModel",
    "FieldRef",
    "Inline",
    "Lens",
    "OrderRef",
]
