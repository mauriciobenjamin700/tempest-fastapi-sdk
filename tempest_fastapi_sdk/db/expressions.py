"""Django-style ``F`` and ``Q`` expression wrappers for SQLAlchemy.

SQLAlchemy already exposes column expressions and ``and_`` / ``or_`` /
``not_``; these wrappers give the terser, string-column-name ergonomics
Django users reach for, and plug straight into
:class:`~tempest_fastapi_sdk.db.repository.BaseRepository`:

* **``F``** references a column *by name* and builds arithmetic against
  it, so an atomic in-database update avoids the read-modify-write race:

  ```python
  # stock = stock - 1, computed in the database, no lost update
  await repository.bulk_update({"id": product_id}, {"stock": F("stock") - 1})
  ```

* **``Q``** captures the repository's dict-filter conventions (``name``
  ILIKE, ``field__gte`` comparisons, iterable ``IN``, …) as an object you
  combine with ``&`` / ``|`` / ``~`` for real ``OR`` / ``NOT`` trees:

  ```python
  rows = await repository.list(
      where=Q(status="open") | Q(priority__gte=5),
  )
  ```

Both resolve lazily against the repository's model, so the same wrapper
works for any model the repository is bound to.
"""

from __future__ import annotations

import operator
from collections.abc import Callable, Iterable, Mapping
from datetime import date
from typing import Any, cast

from sqlalchemy import and_, func, not_, or_
from sqlalchemy.sql.elements import ColumnElement

#: Suffix-to-operator map for ``<column>__<op>`` comparison filters.
COMPARISON_OPS: dict[str, Callable[[Any, Any], Any]] = {
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "ne": operator.ne,
}


def escape_like(value: str) -> str:
    """Escape LIKE/ILIKE wildcards so user input is treated literally.

    Backslash is escaped first to avoid double-escaping the others.

    Args:
        value (str): The raw user-supplied search term.

    Returns:
        str: The same string with ``\\``, ``%`` and ``_`` escaped.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _as_membership(value: Any) -> list[Any] | None:
    """Materialize a membership collection to a list, or ``None`` for a scalar.

    A membership collection is any iterable that is not a string, bytes-like
    object or mapping — ``list``, ``tuple``, ``set``, ``frozenset``, ``range``,
    ``dict`` views and one-shot generators all qualify. Materializing to a list
    consumes a one-shot iterator exactly once and hands SQLAlchemy's ``in_`` a
    concrete, re-iterable sequence (a bare generator would be exhausted by the
    count/page double-use). ``str`` / ``bytes`` / ``Mapping`` are treated as
    scalars, so a plain string value never degrades into a character ``IN``.

    Args:
        value (Any): The candidate filter value.

    Returns:
        list[Any] | None: The materialized members, or ``None`` when ``value``
            is a scalar to be matched by equality.
    """
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        return None
    if isinstance(value, Iterable):
        return list(value)
    return None


def _suffix_condition(column: Any, op: str, value: Any) -> Any:
    """Build the clause for a ``<column>__<op>`` suffix (non-``isnull``).

    Kept explicit — a plain ``if`` per operator, no operator-name magic —
    so the supported set is greppable and typed. The recognized operators:

    * ``gt`` / ``gte`` / ``lt`` / ``lte`` / ``ne`` — comparisons (from
      :data:`COMPARISON_OPS`).
    * ``in`` / ``notin`` / ``not_in`` — membership. ``not_in`` is a
      readability alias for ``notin``. The value is any non-string iterable
      (materialized once) or a bare scalar wrapped into a single-item list.
    * ``between`` — ``col BETWEEN lo AND hi``. The value must be an ordered
      two-item ``list`` / ``tuple`` ``(lo, hi)``; anything else is skipped
      (returns ``None``). A ``set`` is rejected because its order is undefined.
    * ``iexact`` — case-insensitive equality, ``lower(col) == lower(value)``.
    * ``like`` / ``ilike`` — raw ``LIKE`` / ``ILIKE`` with the caller's own
      ``%`` / ``_`` wildcards, **not** escaped. Use these when you want to
      control the pattern yourself; prefer ``contains`` / ``startswith`` /
      ``endswith`` for escaped user input. ``ilike`` is always
      case-insensitive; plain ``like`` case-sensitivity is backend-defined
      (SQLite folds ASCII case, PostgreSQL does not) — reach for ``ilike``
      or ``iexact`` when you need portable case handling.
    * ``contains`` / ``icontains`` / ``startswith`` / ``endswith`` —
      case-insensitive ``ILIKE`` with the value escaped as a literal substring
      / prefix / suffix.

    Args:
        column (Any): The resolved model column.
        op (str): The suffix operator (``gt``/``in``/``between``/…).
        value (Any): The comparison value.

    Returns:
        Any: The SQLAlchemy clause, or ``None`` for an unknown operator or an
            ill-formed ``between`` value.
    """
    op_func = COMPARISON_OPS.get(op)
    if op_func is not None:
        return op_func(column, value)
    if op in ("in", "notin", "not_in"):
        members = _as_membership(value)
        if members is None:
            members = [value]
        return column.in_(members) if op == "in" else column.notin_(members)
    if op == "between":
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return column.between(value[0], value[1])
        return None
    if op == "iexact":
        return func.lower(column) == func.lower(value)
    if op == "like":
        return column.like(value)
    if op == "ilike":
        return column.ilike(value)
    if op in ("contains", "icontains"):
        return column.ilike(f"%{escape_like(str(value))}%", escape="\\")
    if op == "startswith":
        return column.ilike(f"{escape_like(str(value))}%", escape="\\")
    if op == "endswith":
        return column.ilike(f"%{escape_like(str(value))}", escape="\\")
    return None


def build_filter_condition(
    model: type[Any],
    field: str,
    value: Any,
) -> ColumnElement[bool] | None:
    """Build one WHERE condition from a ``field``/``value`` pair.

    Implements the shared filter conventions used by both
    ``BaseRepository`` dict filters and :class:`Q`:

    * ``<column>__<op>`` suffix operator, one of:
      ``gt`` / ``gte`` / ``lt`` / ``lte`` / ``ne`` (comparison),
      ``in`` / ``notin`` / ``not_in`` (membership; value is any non-string
      iterable — ``list`` / ``set`` / ``tuple`` / generator — or a bare
      scalar; ``not_in`` is an alias of ``notin``),
      ``between`` (``col BETWEEN lo AND hi``; value is an ordered two-item
      ``(lo, hi)`` ``list`` / ``tuple``),
      ``iexact`` (case-insensitive equality),
      ``like`` / ``ilike`` (raw, un-escaped ``LIKE`` with caller wildcards),
      ``isnull`` (``IS NULL`` when truthy, ``IS NOT NULL`` when falsy),
      ``contains`` / ``icontains`` / ``startswith`` / ``endswith``
      (case-insensitive ``ILIKE`` substring / prefix / suffix, escaped).
    * ``name`` (string) → case-insensitive ``ILIKE %value%``.
    * ``bool`` → ``.is_(value)``.
    * ``date`` → ``func.date(column) == value`` whole-day match.
    * non-string iterable (``list`` / ``set`` / ``tuple`` / ``frozenset`` /
      ``range`` / generator / ``dict`` view) → ``.in_(value)``.
    * otherwise → equality.

    A ``None`` value (except ``isnull``, whose value is a bool), an
    unknown column, or an unknown operator yields ``None`` (the caller
    skips the condition).

    Args:
        model (type[Any]): The model class the field belongs to.
        field (str): The filter key, optionally ``<column>__<op>``.
        value (Any): The value to compare against.

    Returns:
        ColumnElement[bool] | None: The condition, or ``None`` to skip.
    """
    # ``isnull`` carries a bool (possibly False), so it must be handled
    # before the "None value skips" rule.
    if "__" in field and field.rpartition("__")[2] == "isnull":
        base = field.rpartition("__")[0]
        column = getattr(model, base, None)
        if column is None:
            return None
        return cast(
            "ColumnElement[bool]",
            column.is_(None) if value else column.isnot(None),
        )

    if value is None:
        return None

    condition: Any
    if "__" in field:
        base, _, op = field.rpartition("__")
        op_column = getattr(model, base, None)
        if op_column is None:
            return None
        condition = _suffix_condition(op_column, op, value)
        if condition is None:
            return None
    else:
        column = getattr(model, field, None)
        if column is None:
            return None
        if field == "name" and isinstance(value, str):
            condition = column.ilike(f"%{escape_like(value)}%", escape="\\")
        elif isinstance(value, bool):
            condition = column.is_(value)
        elif isinstance(value, date):
            condition = func.date(column) == value
        else:
            members = _as_membership(value)
            condition = column.in_(members) if members is not None else column == value
    return cast("ColumnElement[bool]", condition)


class F:
    """A lazily-resolved reference to a model column, plus arithmetic.

    ``F("stock")`` resolves to the model's ``stock`` column;
    ``F("stock") - 1`` resolves to ``stock - 1``. Arithmetic accepts
    plain values or other ``F`` instances (``F("price") * F("qty")``)
    and works from either side (``10 - F("stock")``). Pass the result as
    a value in :meth:`BaseRepository.bulk_update` to compute the new
    value in the database.
    """

    __slots__ = ("_builder", "name")

    def __init__(
        self,
        name: str,
        _builder: Callable[[type[Any]], Any] | None = None,
    ) -> None:
        """Initialize a column reference.

        Args:
            name (str): The column name on the target model.
            _builder (Callable[[type[Any]], Any] | None): Internal —
                the expression builder; defaults to reading the column.
        """
        self.name: str = name
        self._builder: Callable[[type[Any]], Any] = _builder or (
            lambda model: getattr(model, name)
        )

    def resolve(self, model: type[Any]) -> Any:
        """Resolve to a SQLAlchemy column expression against ``model``.

        Args:
            model (type[Any]): The model class to bind the column to.

        Returns:
            Any: The resolved SQLAlchemy expression.
        """
        return self._builder(model)

    def _combine(
        self,
        op: Callable[[Any, Any], Any],
        other: Any,
        *,
        reflected: bool = False,
    ) -> F:
        """Return a new ``F`` applying ``op`` against ``other``.

        Args:
            op (Callable[[Any, Any], Any]): The binary operator.
            other (Any): The right-hand operand (value or ``F``).
            reflected (bool): Whether ``self`` is the right-hand side
                (``other op self``) for reflected operators.

        Returns:
            F: A new lazily-resolved expression.
        """

        def builder(model: type[Any]) -> Any:
            left = self._builder(model)
            right = other.resolve(model) if isinstance(other, F) else other
            return op(right, left) if reflected else op(left, right)

        return F(self.name, builder)

    def __add__(self, other: Any) -> F:
        return self._combine(operator.add, other)

    def __radd__(self, other: Any) -> F:
        return self._combine(operator.add, other, reflected=True)

    def __sub__(self, other: Any) -> F:
        return self._combine(operator.sub, other)

    def __rsub__(self, other: Any) -> F:
        return self._combine(operator.sub, other, reflected=True)

    def __mul__(self, other: Any) -> F:
        return self._combine(operator.mul, other)

    def __rmul__(self, other: Any) -> F:
        return self._combine(operator.mul, other, reflected=True)

    def __truediv__(self, other: Any) -> F:
        return self._combine(operator.truediv, other)

    def __rtruediv__(self, other: Any) -> F:
        return self._combine(operator.truediv, other, reflected=True)


class Q:
    """A composable set of filter conditions.

    ``Q(status="open", priority__gte=5)`` is the ``AND`` of those two
    conditions (same conventions as a repository filter dict). Combine
    with ``&`` / ``|`` and negate with ``~`` to build arbitrary boolean
    trees:

    ```python
    Q(status="open") | Q(status="pending")   # OR
    Q(active=True) & ~Q(role="guest")         # AND NOT
    ```

    Pass the tree as ``where=`` to a repository read; it resolves to a
    single SQLAlchemy clause against the repository's model.
    """

    __slots__ = ("children", "conditions", "connector", "negated")

    def __init__(self, **conditions: Any) -> None:
        """Build a leaf node from keyword conditions.

        Args:
            **conditions (Any): Filter key/value pairs, ANDed together.
        """
        self.conditions: dict[str, Any] = conditions
        self.children: list[Q] = []
        self.connector: str = "AND"
        self.negated: bool = False

    def _combine(self, other: Q, connector: str) -> Q:
        """Return a new node joining ``self`` and ``other``.

        Args:
            other (Q): The right-hand node.
            connector (str): ``"AND"`` or ``"OR"``.

        Returns:
            Q: The combined node.
        """
        combined = Q()
        combined.connector = connector
        combined.children = [self, other]
        return combined

    def __and__(self, other: Q) -> Q:
        return self._combine(other, "AND")

    def __or__(self, other: Q) -> Q:
        return self._combine(other, "OR")

    def __invert__(self) -> Q:
        clone = Q(**self.conditions)
        clone.children = self.children
        clone.connector = self.connector
        clone.negated = not self.negated
        return clone

    def resolve(self, model: type[Any]) -> ColumnElement[bool] | None:
        """Resolve the tree to a SQLAlchemy clause against ``model``.

        Conditions with a ``None`` value or an unknown column are
        skipped (matching the repository's dict-filter behavior). A
        node that resolves to nothing returns ``None``.

        Args:
            model (type[Any]): The model class to bind columns to.

        Returns:
            ColumnElement[bool] | None: The combined clause, or ``None``
            when the node carries no usable condition.
        """
        clauses: list[ColumnElement[bool]] = []
        for field, value in self.conditions.items():
            condition = build_filter_condition(model, field, value)
            if condition is not None:
                clauses.append(condition)
        for child in self.children:
            resolved = child.resolve(model)
            if resolved is not None:
                clauses.append(resolved)

        if not clauses:
            return None

        combined = or_(*clauses) if self.connector == "OR" else and_(*clauses)
        return not_(combined) if self.negated else combined


__all__: list[str] = [
    "COMPARISON_OPS",
    "F",
    "Q",
    "build_filter_condition",
    "escape_like",
]
