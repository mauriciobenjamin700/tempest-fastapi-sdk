"""DataTable: render a list of Pydantic schemas as an HTML table.

The rows are the response schemas a service already returns, so listing
endpoints and list pages share one shape. Columns, headers and cell text
are all derived from the schema unless overridden.

A column is a field name, or a :class:`TableColumn` when it needs more
than text: a per-cell renderer (a link to the detail page, a "remove"
form per row), an alignment, a class of its own. Both forms mix freely
in one ``columns`` list.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field

from tempest_fastapi_sdk.ui._core import Component, Stack, Text, Widget
from tempest_fastapi_sdk.ui.components.classes import (
    DEFAULT_CLASSES,
    ComponentClasses,
)

CellAlign = Literal["left", "center", "right"]
"""Horizontal alignment of a table column."""

CellRenderer = Callable[[Any], Widget]
"""Build one cell's content from its row.

Receives the row (the Pydantic model or mapping from ``rows``) and returns
a widget, a list of widgets, or a ``str`` — a string is rendered as
escaped text, exactly like a default cell.
"""


@dataclass(frozen=True, slots=True)
class TableColumn:
    """One column of a :class:`DataTable`, when a field name is not enough.

    Attributes:
        name (str): The column name — the field read from each row for a
            text cell, and the key of :attr:`DataTable.headers`.
        header (str | None): Header text. ``None`` falls back to
            :attr:`DataTable.headers`, then the schema field's ``title``,
            then the humanized name.
        render (CellRenderer | None): Builds the cell content from the
            row. ``None`` keeps the default: the field value formatted by
            :meth:`DataTable.cell_text`. Widgets go through the normal
            renderer, so nothing is ever inserted as raw HTML.
        align (CellAlign | None): Horizontal alignment of the header and
            every cell, applied as the
            :attr:`ComponentClasses.table_align` modifier (``"right"``
            also switches to tabular figures, for numbers).
        class_name (str): Extra class applied to the header and every
            cell of the column — for example one a media query hides on
            narrow screens.

    Example:
        ```python
        from pydantic import BaseModel
        from tempest_core import Text, Widget

        from tempest_fastapi_sdk.ui.components import DataTable, TableColumn


        class FileResponseSchema(BaseModel):
            id: int
            name: str
            size: int


        def name_link(row: FileResponseSchema) -> Widget:
            return Text(content=row.name, tag="a", attrs={"href": f"/files/{row.id}"})


        table = DataTable(
            rows=[FileResponseSchema(id=1, name="a.txt", size=120)],
            columns=[
                TableColumn("name", render=name_link),
                TableColumn("size", align="right", class_name="col-optional"),
            ],
        )
        ```
    """

    name: str
    header: str | None = None
    render: CellRenderer | None = None
    align: CellAlign | None = None
    class_name: str = ""

    def __post_init__(self) -> None:
        """Reject an alignment or renderer the table cannot use.

        A stdlib dataclass is not validated by pydantic when it sits in a
        model field, so without this check ``align="middle"`` would
        render an undefined modifier class and leave the column silently
        unaligned.

        Raises:
            ValueError: When ``align`` is not one of ``left``, ``center``,
                ``right``, or ``render`` is not callable.
        """
        if self.align is not None and self.align not in get_args(CellAlign):
            raise ValueError(
                f"TableColumn.align must be one of left, center, right; "
                f"got {self.align!r}.",
            )
        if self.render is not None and not callable(self.render):
            raise ValueError(
                f"TableColumn.render must be callable, got {self.render!r}.",
            )


def _humanize(name: str) -> str:
    """Turn a field name into a column header.

    Args:
        name (str): The schema field name.

    Returns:
        str: Title-cased header (``created_at`` becomes ``Created At``).
    """
    return name.replace("_", " ").strip().title()


def _row_value(row: Any, column: str) -> Any:
    """Read one column out of a row.

    Args:
        row (Any): A Pydantic model or a mapping.
        column (str): The column name.

    Returns:
        Any: The value, or ``None`` when the row lacks that key.
    """
    if isinstance(row, Mapping):
        return row.get(column)
    return getattr(row, column, None)


class DataTable(Component):
    """A table rendered from a list of schemas.

    Attributes:
        rows (list[Any]): The records — Pydantic models or mappings.
        columns (list[str | TableColumn]): Columns, in order — a field
            name for a text cell, or a :class:`TableColumn` for a custom
            renderer, alignment or class. Empty derives them from
            ``row_schema`` when given, otherwise from the first row.
        row_schema (Any | None): The Pydantic model class describing the
            rows. Supplying it means the header still renders when the
            list is empty, and column labels come from each field's
            ``title``.
        headers (dict[str, str]): Header text overrides, per column.
        caption (str): Optional ``<caption>`` text.
        empty_text (str): Text of the single row shown when there is
            nothing to list.
        bool_labels (tuple[str, str]): Text for ``True`` and ``False``
            cells.
        none_text (str): Text for ``None`` cells, and for a renderer
            that returns ``None``.
        classes (ComponentClasses): Class names to apply.

    Example:
        ```python
        from pydantic import BaseModel

        from tempest_fastapi_sdk.ui.components import DataTable


        class UserResponseSchema(BaseModel):
            name: str
            email: str


        table = DataTable(
            rows=[UserResponseSchema(name="Ana", email="ana@example.com")],
            row_schema=UserResponseSchema,
        )
        ```
    """

    rows: list[Any] = Field(default_factory=list)
    columns: list[str | TableColumn] = Field(default_factory=list)
    row_schema: Any | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    caption: str = ""
    empty_text: str = "Nenhum registro."
    bool_labels: tuple[str, str] = ("Sim", "Não")
    none_text: str = "—"
    classes: ComponentClasses = DEFAULT_CLASSES

    def column_specs(self) -> list[TableColumn]:
        """Return the columns actually rendered, as :class:`TableColumn`.

        Returns:
            list[TableColumn]: The explicit ``columns`` (a bare name
            becomes a default text column) when given, otherwise one text
            column per schema field, otherwise per key of the first row.
            Empty when none of the three is available.
        """
        if self.columns:
            return [
                column if isinstance(column, TableColumn) else TableColumn(column)
                for column in self.columns
            ]
        return [TableColumn(name) for name in self._derived_names()]

    def resolved_columns(self) -> list[str]:
        """Return the names of the columns actually rendered.

        Returns:
            list[str]: The name of every entry of :meth:`column_specs`,
            in order.
        """
        return [spec.name for spec in self.column_specs()]

    def _derived_names(self) -> list[str]:
        """Derive column names when ``columns`` is empty.

        Returns:
            list[str]: The schema's field names, otherwise the first
            row's keys or fields, otherwise nothing.
        """
        if self.row_schema is not None and hasattr(self.row_schema, "model_fields"):
            return list(self.row_schema.model_fields)
        if self.rows:
            first = self.rows[0]
            if isinstance(first, Mapping):
                return list(first)
            fields = getattr(type(first), "model_fields", None)
            if fields is not None:
                return list(fields)
        return []

    def header_text(self, column: str) -> str:
        """Return the header label of a column.

        Args:
            column (str): The column name.

        Returns:
            str: The override when given, else the schema field's
            ``title``, else the humanized column name.
        """
        if column in self.headers:
            return self.headers[column]
        if self.row_schema is not None and hasattr(self.row_schema, "model_fields"):
            field_info = self.row_schema.model_fields.get(column)
            title = getattr(field_info, "title", None)
            if title:
                return str(title)
        return _humanize(column)

    def cell_text(self, value: Any) -> str:
        """Format one cell value as text.

        Args:
            value (Any): The raw value read from the row.

        Returns:
            str: The rendered cell text. ``None`` becomes
            :attr:`none_text`, booleans use :attr:`bool_labels`, dates
            render ISO, sequences join with ``", "``, and a nested model
            renders its own field values.
        """
        if value is None:
            return self.none_text
        if isinstance(value, bool):
            return self.bool_labels[0] if value else self.bool_labels[1]
        if isinstance(value, Enum):
            return str(value.value)
        if isinstance(value, (_dt.date, _dt.time, _dt.datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, BaseModel):
            return ", ".join(
                self.cell_text(getattr(value, name))
                for name in type(value).model_fields
            )
        if isinstance(value, (list, tuple, set, frozenset)):
            return ", ".join(self.cell_text(item) for item in value)
        return str(value)

    def _cell_attrs(self, spec: TableColumn) -> dict[str, str]:
        """Build the ``class`` attribute shared by a column's cells.

        Args:
            spec (TableColumn): The column.

        Returns:
            dict[str, str]: ``{"class": ...}`` with the alignment modifier
            and the column's own class, or empty when it has neither.
        """
        names: list[str] = []
        if spec.align is not None:
            names.append(f"{self.classes.table_align}{spec.align}")
        if spec.class_name.strip():
            names.append(spec.class_name.strip())
        return {"class": " ".join(names)} if names else {}

    def _body_cell(self, spec: TableColumn, row: Any) -> Widget:
        """Build one ``<td>``.

        Args:
            spec (TableColumn): The column the cell belongs to.
            row (Any): The record the cell belongs to.

        Returns:
            Widget: A text ``<td>`` for the default path, a ``str``
            result or a ``None`` result; otherwise a ``<td>`` holding the
            renderer's widget (or widgets, for a list or tuple).
        """
        attrs = self._cell_attrs(spec)
        if spec.render is None:
            text = self.cell_text(_row_value(row, spec.name))
            return Text(content=text, tag="td", attrs=attrs)
        content = spec.render(row)
        if content is None:
            return Text(content=self.none_text, tag="td", attrs=attrs)
        if isinstance(content, str):
            return Text(content=content, tag="td", attrs=attrs)
        children = list(content) if isinstance(content, (list, tuple)) else [content]
        return Stack(tag="td", attrs=attrs, children=children)

    def render(self) -> Widget:
        """Compose the table.

        Returns:
            Widget: A ``<table>`` with a header row and one body row per
            record, or a single spanning row carrying
            :attr:`empty_text` when there is nothing to show.
        """
        specs = self.column_specs()
        parts: list[Widget] = []
        if self.caption:
            parts.append(Text(content=self.caption, tag="caption"))

        parts.append(
            Stack(
                tag="thead",
                children=[
                    Stack(
                        tag="tr",
                        children=[
                            Text(
                                content=(
                                    spec.header
                                    if spec.header is not None
                                    else self.header_text(spec.name)
                                ),
                                tag="th",
                                attrs={"scope": "col", **self._cell_attrs(spec)},
                            )
                            for spec in specs
                        ],
                    ),
                ],
            ),
        )

        if self.rows:
            body: list[Widget] = [
                Stack(
                    tag="tr",
                    children=[self._body_cell(spec, row) for spec in specs],
                )
                for row in self.rows
            ]
        else:
            body = [
                Stack(
                    tag="tr",
                    children=[
                        Text(
                            content=self.empty_text,
                            tag="td",
                            attrs={
                                "colspan": str(max(len(specs), 1)),
                                "class": self.classes.table_empty,
                            },
                        ),
                    ],
                ),
            ]
        parts.append(Stack(tag="tbody", children=body))

        return Stack(
            tag="table",
            attrs={"class": self.classes.table},
            children=parts,
        )


__all__: list[str] = ["CellAlign", "CellRenderer", "DataTable", "TableColumn"]
