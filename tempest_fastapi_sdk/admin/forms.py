"""Form-field introspection + submission parsing for the admin CRUD views.

Turns a model's mapped columns into typed widget descriptors the
template renders, and parses a posted form back into coerced Python
values. Kept separate from the router so the (fiddly) type handling is
unit-testable in isolation.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any

from sqlalchemy import inspect as sa_inspect

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy import Column

    from tempest_fastapi_sdk.admin.config import AdminModel


@dataclass
class FormField:
    """A single rendered form control.

    Attributes:
        name (str): Column key (form field name).
        label (str): Human-readable label.
        widget (str): One of ``text`` / ``textarea`` / ``number`` /
            ``checkbox`` / ``datetime`` / ``date`` / ``select``.
        value (Any): Value to pre-fill (string for most widgets).
        required (bool): Whether the field is mandatory.
        checked (bool): Checkbox state (``checkbox`` widget only).
        step (str | None): ``step`` attribute for ``number`` widgets.
        options (list[tuple[str, str]]): ``(value, label)`` pairs for
            ``select`` widgets.
        error (str | None): Per-field validation error, if any.
    """

    name: str
    label: str
    widget: str
    value: Any = ""
    required: bool = False
    checked: bool = False
    step: str | None = None
    options: list[tuple[str, str]] = field(default_factory=list)
    error: str | None = None


def _label(name: str) -> str:
    """Humanize a column key into a form label.

    Args:
        name (str): The column key.

    Returns:
        str: Title-cased label.
    """
    return name.replace("_", " ").strip().title()


def _python_type(column: Column[Any]) -> type:
    """Best-effort Python type for a column, defaulting to ``str``.

    Args:
        column (Column[Any]): The mapped column.

    Returns:
        type: The column's Python type (or ``str`` when undeterminable).
    """
    try:
        return column.type.python_type
    except (NotImplementedError, AttributeError):
        return str


def _widget_for(
    column: Column[Any], py: type
) -> tuple[str, str | None, list[tuple[str, str]]]:
    """Map a column to ``(widget, step, options)``.

    Args:
        column (Column[Any]): The mapped column.
        py (type): The column's Python type.

    Returns:
        tuple[str, str | None, list[tuple[str, str]]]: Widget name, the
        ``number`` step (or ``None``), and ``select`` options.
    """
    if py is bool:
        return ("checkbox", None, [])
    if isinstance(py, type) and issubclass(py, Enum):
        return (
            "select",
            None,
            [(str(member.value), _label(member.name)) for member in py],
        )
    if py is int:
        return ("number", "1", [])
    if py is float or py is Decimal:
        return ("number", "any", [])
    # datetime is a subclass of date — check it first.
    if py is _dt.datetime:
        return ("datetime", None, [])
    if py is _dt.date:
        return ("date", None, [])
    if py is str:
        length = getattr(column.type, "length", None)
        if length is None or length > 255:
            return ("textarea", None, [])
        return ("text", None, [])
    return ("text", None, [])


def _is_optional(column: Column[Any]) -> bool:
    """Whether a column can be left blank (nullable or defaulted).

    Args:
        column (Column[Any]): The mapped column.

    Returns:
        bool: ``True`` when the column is nullable or carries a default.
    """
    return (
        bool(column.nullable)
        or column.default is not None
        or (column.server_default is not None)
    )


def build_form_fields(
    admin: AdminModel[Any],
    *,
    instance: Any | None = None,
    submitted: Mapping[str, Any] | None = None,
    errors: Mapping[str, str] | None = None,
) -> list[FormField]:
    """Build the ordered list of form fields for create/edit.

    Args:
        admin (AdminModel[Any]): The admin configuration.
        instance (Any | None): The row being edited (``None`` for
            create), used to pre-fill values.
        submitted (Mapping[str, Any] | None): A rejected submission to
            re-render (takes precedence over ``instance``).
        errors (Mapping[str, str] | None): Per-field error messages.

    Returns:
        list[FormField]: Descriptors ready for the template.
    """
    columns = sa_inspect(admin.model).columns
    errors = errors or {}
    fields: list[FormField] = []
    for name in admin.editable_field_names():
        column = columns.get(name)
        if column is None:
            continue
        py = _python_type(column)
        widget, step, options = _widget_for(column, py)
        required = not _is_optional(column)

        value: Any = ""
        checked = False
        if submitted is not None:
            if widget == "checkbox":
                checked = _truthy(submitted.get(name))
            else:
                value = submitted.get(name, "")
        elif instance is not None:
            current = getattr(instance, name, None)
            if widget == "checkbox":
                checked = bool(current)
            elif current is None:
                value = ""
            elif widget == "datetime" and isinstance(current, _dt.datetime):
                value = current.isoformat()[:16]
            elif widget == "date" and isinstance(current, _dt.date):
                value = current.isoformat()[:10]
            elif isinstance(current, Enum):
                value = str(current.value)
            else:
                value = current

        fields.append(
            FormField(
                name=name,
                label=_label(name),
                widget=widget,
                value=value,
                required=required,
                checked=checked,
                step=step,
                options=options,
                error=errors.get(name),
            )
        )
    return fields


def _truthy(raw: Any) -> bool:
    """Interpret a posted checkbox value as a boolean.

    Args:
        raw (Any): The submitted value (absent → unchecked).

    Returns:
        bool: ``True`` when the box was checked.
    """
    if raw is None:
        return False
    return str(raw).lower() not in {"", "false", "off", "0", "no"}


def parse_submission(
    admin: AdminModel[Any],
    form: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Coerce a posted form into model kwargs + collect field errors.

    Args:
        admin (AdminModel[Any]): The admin configuration.
        form (Mapping[str, Any]): The posted form data.

    Returns:
        tuple[dict[str, Any], dict[str, str]]: ``(data, errors)`` where
        ``data`` holds coerced values for the fields that validated and
        ``errors`` maps field name → message for the ones that did not.
        Optional + blank fields are set to ``None`` when nullable, or
        omitted so the column default applies.
    """
    columns = sa_inspect(admin.model).columns
    data: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name in admin.editable_field_names():
        column = columns.get(name)
        if column is None:
            continue
        py = _python_type(column)
        widget, _step, _options = _widget_for(column, py)

        if widget == "checkbox":
            data[name] = _truthy(form.get(name))
            continue

        raw = form.get(name)
        if isinstance(raw, str):
            raw = raw.strip()
        if raw in (None, ""):
            if _is_optional(column):
                if column.nullable:
                    data[name] = None
                # else: leave unset so the column default applies
            else:
                errors[name] = "This field is required."
            continue
        try:
            data[name] = _coerce_scalar(py, str(raw))
        except (ValueError, TypeError, KeyError):
            errors[name] = f"Invalid value for {_label(name)}."
    return data, errors


def _coerce_scalar(py: type, raw: str) -> Any:
    """Coerce a non-empty form string to the column's Python type.

    Args:
        py (type): Target Python type.
        raw (str): The submitted string.

    Returns:
        Any: The coerced value.

    Raises:
        ValueError: When the string does not parse for the type.
        KeyError: When an enum value is unknown.
    """
    if isinstance(py, type) and issubclass(py, Enum):
        try:
            return py(raw)
        except ValueError:
            return py(int(raw))
    if py is int:
        return int(raw)
    if py is float:
        return float(raw)
    if py is Decimal:
        return Decimal(raw)
    if py is _dt.datetime:
        return _dt.datetime.fromisoformat(raw)
    if py is _dt.date:
        return _dt.date.fromisoformat(raw)
    return raw


__all__: list[str] = [
    "FormField",
    "build_form_fields",
    "parse_submission",
]
