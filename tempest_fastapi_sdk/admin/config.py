"""Declarative configuration objects describing a managed model."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Generic, TypeVar, cast

from sqlalchemy.inspection import inspect

from tempest_fastapi_sdk.db.model import BaseModel
from tempest_fastapi_sdk.db.repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

ModelT = TypeVar("ModelT", bound=BaseModel)


class AdminModel(Generic[ModelT]):
    """Declarative admin configuration for one SQLAlchemy model.

    Subclass per managed model and assign to a registered
    :class:`AdminSite`. Mirrors the surface of Django's ``ModelAdmin``:

    * ``list_display`` — columns shown in the list view. Defaults to
      every column except the password hash.
    * ``list_filter`` — fields surfaced as filter controls. The list
      view emits one dropdown per entry; values are matched via the
      repository's standard filter pipeline.
    * ``search_fields`` — string columns searched for matches.
      Uses ``ILIKE %value%`` via the repository's ``name`` convention
      (rerouted automatically per field).
    * ``readonly_fields`` — fields locked in the detail view.
    * ``ordering`` — default ordering column. ``None`` falls back to
      ``created_at desc``.
    * ``page_size`` — default rows per page in the list view.
    * ``repository_class`` — concrete :class:`BaseRepository`. The
      default builds an anonymous repository bound to :attr:`model`
      with no overrides.
    * ``identity_field`` — the column used to look up a single row
      from the detail/edit URL. Defaults to ``"id"`` (UUID PK).

    Attributes:
        model (type[ModelT]): The SQLAlchemy model class.
        verbose_name (str): Singular display name; defaults to the
            class name humanized.
        verbose_name_plural (str): Plural display name; defaults to
            ``verbose_name + "s"``.
    """

    model: type[ModelT]
    repository_class: ClassVar[type[BaseRepository[Any]] | None] = None
    list_display: ClassVar[list[str] | None] = None
    list_filter: ClassVar[list[str]] = []
    search_fields: ClassVar[list[str]] = []
    readonly_fields: ClassVar[list[str]] = []
    ordering: ClassVar[str | None] = None
    page_size: ClassVar[int] = 25
    identity_field: ClassVar[str] = "id"
    verbose_name: ClassVar[str | None] = None
    verbose_name_plural: ClassVar[str | None] = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Validate the subclass at definition time.

        Raises:
            TypeError: When ``model`` is missing or not a subclass of
                :class:`BaseModel`.
        """
        super().__init_subclass__(**kwargs)
        if not hasattr(cls, "model"):
            raise TypeError(f"{cls.__name__} must set `model` to a BaseModel subclass")
        if not isinstance(cls.model, type) or not issubclass(cls.model, BaseModel):
            raise TypeError(
                f"{cls.__name__}.model must be a subclass of BaseModel",
            )

    @classmethod
    def get_verbose_name(cls) -> str:
        """Return the configured (or auto-derived) singular display name.

        Returns:
            str: The display name.
        """
        if cls.verbose_name:
            return cls.verbose_name
        return _humanize(cls.model.__name__.removesuffix("Model"))

    @classmethod
    def get_verbose_name_plural(cls) -> str:
        """Return the configured (or auto-derived) plural display name.

        Returns:
            str: The plural display name.
        """
        if cls.verbose_name_plural:
            return cls.verbose_name_plural
        singular = cls.get_verbose_name()
        return f"{singular}s"

    @classmethod
    def get_slug(cls) -> str:
        """Return the URL slug under which the model is exposed.

        Defaults to ``__tablename__`` so admin URLs and DB tables
        stay in sync.

        Returns:
            str: The slug.
        """
        return cls.model.__tablename__

    @classmethod
    def column_names(cls) -> list[str]:
        """Return every mapped column name on :attr:`model`.

        Returns:
            list[str]: Column keys in declaration order.
        """
        return [attr.key for attr in inspect(cls.model).mapper.column_attrs]

    @classmethod
    def resolved_list_display(cls) -> list[str]:
        """Return the effective ``list_display`` column list.

        Defaults to every column except ``hashed_password`` when
        unconfigured.

        Returns:
            list[str]: The list of columns to render.
        """
        if cls.list_display is not None:
            return list(cls.list_display)
        return [name for name in cls.column_names() if name not in {"hashed_password"}]

    @classmethod
    def build_repository(cls, session: AsyncSession) -> BaseRepository[ModelT]:
        """Instantiate the repository for ``session``.

        Args:
            session (AsyncSession): The DB session to bind.

        Returns:
            BaseRepository[ModelT]: A repository ready to use.
        """
        repo_cls = cls.repository_class
        if repo_cls is None:
            repo_cls = _build_default_repository_class(cls.model)
        return repo_cls(session)


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


def _build_default_repository_class(
    model: type[BaseModel],
) -> type[BaseRepository[Any]]:
    """Synthesize a minimal repository class for ``model``.

    The synthesized class is cached on the model so subsequent calls
    reuse the same type instead of producing distinct anonymous
    classes (which would confuse :func:`isinstance` checks).

    Args:
        model (type[BaseModel]): The model class.

    Returns:
        type[BaseRepository[Any]]: The repository subclass.
    """
    attr = "_tempest_default_admin_repository"
    cached = getattr(model, attr, None)
    if cached is not None:
        return cast("type[BaseRepository[Any]]", cached)
    name = f"{model.__name__}AdminRepository"
    new_cls = type(
        name,
        (BaseRepository,),
        {
            "model": model,
            "__module__": model.__module__,
        },
    )
    setattr(model, attr, new_cls)
    return cast("type[BaseRepository[Any]]", new_cls)


__all__: list[str] = [
    "AdminModel",
]
