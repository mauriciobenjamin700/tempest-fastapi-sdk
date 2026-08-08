"""Enum columns that stay type-safe in the application *and* in the database.

SQLAlchemy already maps ``Mapped[MyEnum]`` to a column, but its defaults
are wrong for how this SDK is used, in three ways that each cost real
safety:

1. **It stores the member *name*, not its value.** ``Status.IN_PROGRESS
   = "in_progress"`` lands in the database as ``IN_PROGRESS``. Every
   consumer that is not this Python process — a report, a dashboard
   query, a sibling service — then reads a string the domain never
   defined.
2. **On SQLite it emits a bare ``VARCHAR``, with no constraint.** The
   production PostgreSQL column rejects an invalid value; the test
   column silently accepts it. A bug that the database would have caught
   in production therefore passes the test suite.
3. **The PostgreSQL type is named after the enum class alone** —
   ``status`` — which collides with any table or column of that name in
   the same schema.

:class:`TempestEnum` fixes all three, and
:class:`~tempest_fastapi_sdk.db.model.BaseModel` installs it as the
default for every ``Mapped[SomeEnum]`` annotation, so the safe behavior
is what you get without asking:

```python
class OrderStatus(BaseStrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"

class OrderModel(BaseModel):
    status: Mapped[OrderStatus]           # native ENUM on PG, CHECK on SQLite
    fallback: Mapped[OrderStatus] = enum_column(OrderStatus, default=OrderStatus.OPEN)
```

Changing an enum's members later is a schema change on both backends.
Neither one is detected by Alembic's autogenerate on its own; see
:mod:`tempest_fastapi_sdk.db.enum_migrations` for the operation and the
hook that close that gap.
"""

from __future__ import annotations

import enum
from typing import Any, Final, cast

from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.utils.naming import to_snake_case

ENUM_TYPE_SUFFIX: Final[str] = "_enum"
"""Suffix appended to a generated PostgreSQL ``ENUM`` type name.

PostgreSQL keeps types and tables in the same namespace, so a type named
after the enum class alone (``status``) collides with a table or column
called ``status``. The suffix makes ``OrderStatus`` become
``order_status_enum``, which is unambiguous and still readable in
``\\dT`` output and in a migration diff.
"""


def enum_values(enum_class: type[enum.Enum]) -> list[str]:
    """Return the member **values** of ``enum_class``, in declaration order.

    Used as SQLAlchemy's ``values_callable``, which is what makes the
    column store ``"in_progress"`` rather than ``"IN_PROGRESS"``.
    Declaration order is preserved because it becomes the order of the
    PostgreSQL type's labels, and PostgreSQL sorts an ``ENUM`` column by
    that order — so ``ORDER BY status`` follows the workflow the enum
    describes instead of the alphabet.

    Args:
        enum_class (type[enum.Enum]): The enum to read.

    Returns:
        list[str]: Each member's value, coerced to ``str``.
    """
    return [str(member.value) for member in enum_class]


def enum_type_name(enum_class: type[enum.Enum]) -> str:
    """Return the deterministic database type name for ``enum_class``.

    Deterministic because the name has to match across three places that
    never see each other: the model's DDL, the migration that creates the
    type, and the migration that later replaces it. ``OrderStatus``
    becomes ``order_status_enum``.

    Args:
        enum_class (type[enum.Enum]): The enum to name.

    Returns:
        str: The snake-cased class name plus :data:`ENUM_TYPE_SUFFIX`.
    """
    return f"{to_snake_case(enum_class.__name__)}{ENUM_TYPE_SUFFIX}"


class TempestEnum(SQLEnum):
    """A SQLAlchemy ``Enum`` configured to be safe on PostgreSQL and SQLite.

    Differs from ``sqlalchemy.Enum`` in exactly the three defaults
    described in the module docstring: the column stores member values,
    a ``CHECK`` constraint is emitted on backends without a native enum
    type, and the generated PostgreSQL type name carries a suffix.

    Every default can still be overridden per column — passing
    ``name="legacy_status"`` keeps an existing type, and
    ``create_constraint=False`` opts a column out of the SQLite check
    (useful only when migrating a table that already holds invalid data
    you have not cleaned yet).

    The resulting DDL:

    ```sql
    -- PostgreSQL
    CREATE TYPE order_status_enum AS ENUM ('open', 'in_progress');
    status order_status_enum NOT NULL

    -- SQLite
    status VARCHAR(11) NOT NULL
    CONSTRAINT ck_order_status CHECK (status IN ('open', 'in_progress'))
    ```

    The ``CHECK`` name follows
    :data:`~tempest_fastapi_sdk.db.model.NAMING_CONVENTION`, so it is
    stable across machines and Alembic can address it by name.

    Notes:
        That name is derived from the **enum type**, not the column, so
        two columns of the same enum in one table produce two ``CHECK``
        constraints sharing a name. PostgreSQL never sees it — it uses
        the native type and emits no ``CHECK`` at all — and SQLite
        accepts the duplicate, so both supported backends are fine. The
        name is left alone deliberately: fixing it would mean changing
        ``NAMING_CONVENTION`` for *every* check constraint, which would
        rename constraints in migrations consumers have already applied.
    """

    def __init__(self, *enums: Any, **kwargs: Any) -> None:
        """Configure the enum type before handing off to SQLAlchemy.

        Only fills in defaults the caller left out, so an explicit
        keyword always wins.

        Args:
            *enums (Any): Either a single ``enum.Enum`` subclass or a
                sequence of string values, exactly as
                ``sqlalchemy.Enum`` accepts.
            **kwargs (Any): Forwarded to ``sqlalchemy.Enum``. ``name``,
                ``native_enum``, ``create_constraint`` and
                ``values_callable`` are defaulted when absent.
        """
        kwargs.setdefault("native_enum", True)
        kwargs.setdefault("create_constraint", True)
        kwargs.setdefault("values_callable", enum_values)
        enum_class = _sole_enum_class(enums)
        if enum_class is not None:
            kwargs.setdefault("name", enum_type_name(enum_class))
        super().__init__(*enums, **kwargs)

    def _resolve_for_python_type(
        self,
        python_type: type[Any],
        matched_on: Any,
        matched_on_flattened: type[Any],
    ) -> TempestEnum | None:
        """Rebuild this type for the concrete enum behind a ``Mapped[…]``.

        SQLAlchemy calls this when a ``Mapped[OrderStatus]`` annotation
        matches the generic ``TempestEnum()`` registered in
        :attr:`~tempest_fastapi_sdk.db.model.BaseModel.type_annotation_map`.
        Its own implementation rebuilds through ``_generic_type_affinity``,
        which returns the **base** ``Enum`` class — so a subclass and the
        type name it derives would be dropped, and the column would fall
        back to ``orderstatus`` instead of ``order_status_enum``.

        Constructing a fresh :class:`TempestEnum` around the concrete
        enum class is the same thing a hand-written
        ``mapped_column(TempestEnum(OrderStatus))`` does, so both routes
        produce identical DDL. Everything the caller configured on the
        generic instance is carried over.

        Args:
            python_type (type[Any]): The annotated Python type.
            matched_on (Any): The key that matched in the type map.
            matched_on_flattened (type[Any]): That key, flattened.

        Returns:
            TempestEnum | None: The resolved type, or ``None`` when
            this type does not apply.

        Notes:
            This overrides a private SQLAlchemy hook, which is a
            deliberate coupling: it is the only place the annotation form
            can be intercepted. ``tests/db/test_enum_column.py`` asserts
            the resulting DDL on both dialects, so a SQLAlchemy release
            that changes the hook fails the suite rather than quietly
            reverting the column to unsafe defaults.
        """
        if isinstance(python_type, type) and issubclass(python_type, enum.Enum):
            return TempestEnum(
                python_type,
                schema=self.schema,
                inherit_schema=self.inherit_schema,
                metadata=self.metadata,
                native_enum=self.native_enum,
                create_constraint=self.create_constraint,
                validate_strings=self.validate_strings,
                values_callable=self.values_callable,
            )
        return cast(
            "TempestEnum | None",
            super()._resolve_for_python_type(
                python_type, matched_on, matched_on_flattened
            ),
        )


def enum_column(
    enum_class: type[enum.Enum],
    **kwargs: Any,
) -> Mapped[Any]:
    """Declare a column backed by :class:`TempestEnum`.

    Equivalent to what ``Mapped[MyEnum]`` produces on a
    :class:`~tempest_fastapi_sdk.db.model.BaseModel`, spelled out. Reach
    for it when the column needs arguments the bare annotation cannot
    carry — a default, a server default, an index, a custom type name:

    ```python
    status: Mapped[OrderStatus] = enum_column(
        OrderStatus, default=OrderStatus.OPEN, index=True
    )
    ```

    Args:
        enum_class (type[enum.Enum]): The enum backing the column.
        **kwargs (Any): Forwarded to ``mapped_column`` (``default``,
            ``nullable``, ``index``, ``server_default``, …).

    Returns:
        Mapped[Any]: The mapped column declaration.
    """
    return mapped_column(TempestEnum(enum_class), **kwargs)


def _sole_enum_class(enums: tuple[Any, ...]) -> type[enum.Enum] | None:
    """Return the enum class when ``enums`` is exactly one, else ``None``.

    ``sqlalchemy.Enum`` accepts either one enum class or a list of bare
    string values; only the first form has a class to derive a name and
    a ``values_callable`` from.

    Args:
        enums (tuple[Any, ...]): The positional arguments as given.

    Returns:
        type[enum.Enum] | None: The enum class, or ``None`` for the
        string-values form.
    """
    if len(enums) != 1:
        return None
    candidate = enums[0]
    if isinstance(candidate, type) and issubclass(candidate, enum.Enum):
        return candidate
    return None


__all__: list[str] = [
    "ENUM_TYPE_SUFFIX",
    "TempestEnum",
    "enum_column",
    "enum_type_name",
    "enum_values",
]
