"""DataTable: render a list of Pydantic schemas as an HTML table.

The rows are the response schemas a service already returns, so listing
endpoints and list pages share one shape. Columns, headers and cell text
are all derived from the schema unless overridden.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Mapping
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from tempest_fastapi_sdk.ui._core import Component, Stack, Text, Widget
from tempest_fastapi_sdk.ui.components.classes import (
    DEFAULT_CLASSES,
    ComponentClasses,
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
        columns (list[str]): Column names, in order. Empty derives them
            from ``schema`` when given, otherwise from the first row.
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
        none_text (str): Text for ``None`` cells.
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
    columns: list[str] = Field(default_factory=list)
    row_schema: Any | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    caption: str = ""
    empty_text: str = "Nenhum registro."
    bool_labels: tuple[str, str] = ("Sim", "Não")
    none_text: str = "—"
    classes: ComponentClasses = DEFAULT_CLASSES

    def resolved_columns(self) -> list[str]:
        """Return the columns actually rendered.

        Returns:
            list[str]: The explicit ``columns`` when given, otherwise
            the schema's field names, otherwise the first row's keys.
            Empty when none of the three is available.
        """
        if self.columns:
            return list(self.columns)
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

    def render(self) -> Widget:
        """Compose the table.

        Returns:
            Widget: A ``<table>`` with a header row and one body row per
            record, or a single spanning row carrying
            :attr:`empty_text` when there is nothing to show.
        """
        columns = self.resolved_columns()
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
                                content=self.header_text(column),
                                tag="th",
                                attrs={"scope": "col"},
                            )
                            for column in columns
                        ],
                    ),
                ],
            ),
        )

        if self.rows:
            body: list[Widget] = [
                Stack(
                    tag="tr",
                    children=[
                        Text(content=self.cell_text(_row_value(row, column)), tag="td")
                        for column in columns
                    ],
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
                                "colspan": str(max(len(columns), 1)),
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


__all__: list[str] = ["DataTable"]
