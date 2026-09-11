"""Form-field introspection + submission parsing for the admin CRUD views.

Turns a model's mapped columns into typed widget descriptors the
template renders, and parses a posted form back into coerced Python
values. Kept separate from the router so the (fiddly) type handling is
unit-testable in isolation.
"""

from __future__ import annotations

import datetime as _dt
import json as _json
import uuid as _uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON
from sqlalchemy import inspect as sa_inspect

from tempest_fastapi_sdk.utils.password import PasswordUtils, check_password_policy

_DEFAULT_PASSWORD_COLUMN: str = "hashed_password"
"""The column ``BaseUserModel.set_password`` writes.

``set_password`` hashes into this specific attribute, so it is only the
right setter for this column; a second password column on the same model
is hashed directly instead.
"""

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from sqlalchemy import Column

    from tempest_fastapi_sdk.admin.config import AdminModel


@dataclass
class FormField:
    """A single rendered form control.

    Attributes:
        name (str): Column key (form field name).
        label (str): Human-readable label.
        widget (str): One of ``text`` / ``textarea`` / ``number`` /
            ``checkbox`` / ``datetime`` / ``date`` / ``select`` /
            ``file`` / ``password``.
        value (Any): Value to pre-fill (string for most widgets).
        required (bool): Whether the field is mandatory.
        checked (bool): Checkbox state (``checkbox`` widget only).
        step (str | None): ``step`` attribute for ``number`` widgets.
        options (list[tuple[str, str]]): ``(value, label)`` pairs for
            ``select`` widgets.
        error (str | None): Per-field validation error, if any.
        autocomplete_url (str | None): For the ``autocomplete`` widget,
            the HTMX search endpoint backing the input. Set by the
            router (needs the prefix + slug).
        display_label (str): For the ``autocomplete`` widget, the label
            of the currently-selected row shown in the search box.
        timezone (str | None): For a ``datetime`` widget on an admin
            with a ``display_timezone``, the zone the box is read in.
            The template shows it next to the label, because
            ``<input type="datetime-local">`` carries no offset and the
            operator has no other way to know which zone the value
            means.
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
    timezone: str | None = None
    autocomplete_url: str | None = None
    display_label: str = ""


def _label(name: str) -> str:
    """Humanize a column key into a form label.

    Args:
        name (str): The column key.

    Returns:
        str: Title-cased label.
    """
    return name.replace("_", " ").strip().title()


def _password_label(name: str) -> str:
    """Label a password box by what the operator types, not by the column.

    ``hashed_password`` humanizes to ``Hashed Password``, which describes
    what the column stores and contradicts what the box takes — the field
    accepts plaintext. The stored/typed distinction is exactly the one
    this widget exists to hide, so the label drops the ``hashed_`` prefix.

    Args:
        name (str): The column key.

    Returns:
        str: The label to render.
    """
    return _label(name.removeprefix("hashed_"))


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

    Notes:
        Two checks are order-sensitive. ``JSON`` columns are matched first
        because their ``python_type`` is undefined, so they would otherwise
        fall through to a plain text input. And ``datetime`` is matched
        before ``date``, since it is a subclass and the ``date`` branch
        would swallow it.
    """
    if isinstance(column.type, JSON):
        return ("json", None, [])
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
    if py is _dt.datetime:
        return ("datetime", None, [])
    if py is _dt.date:
        return ("date", None, [])
    if py is _dt.time:
        return ("time", None, [])
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


def fk_fields(admin: AdminModel[Any]) -> dict[str, str]:
    """Return editable foreign-key columns as ``{field: target_table}``.

    Args:
        admin (AdminModel[Any]): The admin configuration.

    Returns:
        dict[str, str]: Mapping of FK column key → referenced table name.
    """
    columns = sa_inspect(admin.model).columns
    out: dict[str, str] = {}
    for name in admin.editable_field_names():
        column = columns.get(name)
        if column is None or not column.foreign_keys:
            continue
        fk = next(iter(column.foreign_keys))
        out[name] = fk.column.table.name
    return out


def fk_label(admin: AdminModel[Any], instance: Any) -> str:
    """Build a human label for a referenced row (Django ``__str__`` analog).

    Prefers the referenced admin's first search field, then a common
    display attribute, then the primary key.

    Args:
        admin (AdminModel[Any]): The referenced model's admin config.
        instance (Any): The referenced row.

    Returns:
        str: A label for the option.
    """
    for fname in admin.search_fields:
        value = getattr(instance, fname, None)
        if value:
            return str(value)
    for attr in ("name", "title", "email", "label"):
        value = getattr(instance, attr, None)
        if value:
            return str(value)
    return str(getattr(instance, "id", instance))


def inline_editable_names(admin: AdminModel[Any]) -> list[str]:
    """Return the editable fields safe to render in an inline formset.

    Drops upload, autocomplete and password fields — those need a file
    input, an HTMX search box or a hashing step on save that don't belong
    in a compact inline row, so they stay on the child's own full form.

    Args:
        admin (AdminModel[Any]): The child admin configuration.

    Returns:
        list[str]: The editable field names minus upload/autocomplete.
    """
    skip = (
        set(admin.upload_fields)
        | set(admin.autocomplete_fields)
        | set(admin.password_fields)
    )
    return [name for name in admin.editable_field_names() if name not in skip]


def to_display_timezone(value: _dt.datetime, zone: _dt.tzinfo) -> _dt.datetime:
    """Express a stored datetime in the operator's zone.

    A naive value is read as UTC first, because that is what a
    ``TIMESTAMP(timezone=True)`` column stores — and what SQLAlchemy
    hands back naive on SQLite even when the column is declared aware.

    Args:
        value (datetime): The stored value.
        zone (tzinfo): The zone to express it in.

    Returns:
        datetime: An aware datetime in ``zone``, the same instant.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=_dt.UTC)
    return value.astimezone(zone)


def from_display_timezone(value: _dt.datetime, zone: _dt.tzinfo) -> _dt.datetime:
    """Read a datetime the operator typed and return it in UTC.

    A value with no offset is what ``<input type="datetime-local">``
    submits, and it means wall-clock time in ``zone`` — that is the
    whole point of declaring the zone. A value that *does* carry an
    offset already names its instant, so the zone is not applied to it;
    it is only converted.

    Args:
        value (datetime): The submitted value.
        zone (tzinfo): The zone a naive value is read in.

    Returns:
        datetime: An aware datetime in UTC.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=zone)
    return value.astimezone(_dt.UTC)


def build_form_fields(
    admin: AdminModel[Any],
    *,
    instance: Any | None = None,
    submitted: Mapping[str, Any] | None = None,
    errors: Mapping[str, str] | None = None,
    fk_options: Mapping[str, list[tuple[str, str]]] | None = None,
    only: Collection[str] | None = None,
) -> list[FormField]:
    """Build the ordered list of form fields for create/edit.

    Args:
        admin (AdminModel[Any]): The admin configuration.
        instance (Any | None): The row being edited (``None`` for
            create), used to pre-fill values.
        submitted (Mapping[str, Any] | None): A rejected submission to
            re-render (takes precedence over ``instance``).
        errors (Mapping[str, str] | None): Per-field error messages.
        fk_options (Mapping[str, list[tuple[str, str]]] | None): For
            foreign-key fields whose target is a registered admin, the
            ``(value, label)`` option pairs — turns the field into a
            select. Resolved by the router (needs a DB query).
        only (Collection[str] | None): Restrict to this subset of field
            names (used by inline formsets); ``None`` keeps all editable
            fields.

    Returns:
        list[FormField]: Descriptors ready for the template.

    Notes:
        Autocomplete fields get an empty option list on purpose: the router
        fills ``autocomplete_url`` + ``display_label`` and the options are
        fetched on demand over HTMX rather than pre-loaded.

        Upload fields cannot be pre-filled — a browser will not accept a
        value for a file input — so the stored key is surfaced as a
        read-only hint instead, and an existing file never forces the user
        to re-upload to save the form.

        Password fields are never pre-filled either, not even with the
        stored digest, and they are required only on create: an empty box
        on edit means *leave the password alone*, which is the only
        behaviour that lets an operator fix a typo in an e-mail without
        knowing (or resetting) the password.
    """
    columns = sa_inspect(admin.model).columns
    errors = errors or {}
    fk_options = fk_options or {}
    fields: list[FormField] = []
    for name in admin.editable_field_names():
        if only is not None and name not in only:
            continue
        column = columns.get(name)
        if column is None:
            continue
        py = _python_type(column)
        label = _label(name)
        widget, step, options = _widget_for(column, py)
        is_autocomplete = name in admin.autocomplete_fields
        if is_autocomplete:
            widget = "autocomplete"
            options = []
        elif name in fk_options:
            widget = "select"
            options = list(fk_options[name])
        is_upload = name in admin.upload_fields
        if is_upload:
            widget = "file"
        is_password = name in admin.password_fields
        if is_password:
            widget = "password"
            label = _password_label(name)
        required = not _is_optional(column)
        if is_password:
            required = instance is None and not column.nullable

        value: Any = ""
        checked = False
        if is_password:
            value = ""
        elif is_upload:
            current = None if instance is None else getattr(instance, name, None)
            value = current or ""
            if current:
                required = False
        elif submitted is not None:
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
                if admin.display_tzinfo is not None:
                    current = to_display_timezone(current, admin.display_tzinfo)
                value = current.isoformat()[:16]
            elif widget == "date" and isinstance(current, _dt.date):
                value = current.isoformat()[:10]
            elif widget == "time" and isinstance(current, _dt.time):
                value = current.isoformat()[:5]
            elif widget == "json":
                value = _json.dumps(current, indent=2, default=str, sort_keys=True)
            elif isinstance(current, Enum):
                value = str(current.value)
            else:
                value = current

        fields.append(
            FormField(
                name=name,
                label=label,
                widget=widget,
                value=value,
                required=required,
                checked=checked,
                step=step,
                options=options,
                error=errors.get(name),
                timezone=(admin.display_timezone if widget == "datetime" else None),
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
    *,
    only: Collection[str] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Coerce a posted form into model kwargs + collect field errors.

    Args:
        admin (AdminModel[Any]): The admin configuration.
        form (Mapping[str, Any]): The posted form data.
        only (Collection[str] | None): Restrict parsing to this subset of
            field names (used by inline formsets); ``None`` parses all
            editable fields.

    Returns:
        tuple[dict[str, Any], dict[str, str]]: ``(data, errors)`` where
        ``data`` holds coerced values for the fields that validated and
        ``errors`` maps field name → message for the ones that did not.
        Optional + blank fields are set to ``None`` when nullable, or
        omitted so the column default applies.

    Notes:
        Upload fields are skipped here. They carry an ``UploadFile`` rather
        than a scalar, and the router saves the file and injects the
        resulting key into ``data`` separately.

        Password fields are skipped for the same reason: what the operator
        typed is plaintext and what the column stores is a digest, so
        :func:`parse_password_submission` reads them and
        :func:`apply_password_fields` writes them onto the instance.
    """
    columns = sa_inspect(admin.model).columns
    data: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name in admin.editable_field_names():
        if only is not None and name not in only:
            continue
        column = columns.get(name)
        if column is None:
            continue
        if name in admin.upload_fields or name in admin.password_fields:
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
            else:
                errors[name] = "This field is required."
            continue
        if widget == "json":
            try:
                data[name] = _json.loads(str(raw))
            except (ValueError, TypeError):
                errors[name] = f"Invalid JSON for {_label(name)}."
            continue
        try:
            coerced = _coerce_scalar(py, str(raw))
        except (ValueError, TypeError, KeyError):
            errors[name] = f"Invalid value for {_label(name)}."
            continue
        if (
            widget == "datetime"
            and admin.display_tzinfo is not None
            and isinstance(coerced, _dt.datetime)
        ):
            coerced = from_display_timezone(coerced, admin.display_tzinfo)
        data[name] = coerced
    return data, errors


def parse_password_submission(
    admin: AdminModel[Any],
    form: Mapping[str, Any],
    *,
    creating: bool,
) -> tuple[dict[str, str], dict[str, str]]:
    """Read the plaintext typed into each password field.

    Kept apart from :func:`parse_submission` because the value never
    reaches the column as typed: what the form carries is plaintext and
    what the column stores is a digest, so the router hashes it onto the
    instance through :func:`apply_password_fields`.

    Blank means two different things by mode, and that asymmetry is the
    whole feature: on create a required column has to be filled, while
    on edit blank means *do not touch the stored hash*.

    Args:
        admin (AdminModel[Any]): The admin configuration.
        form (Mapping[str, Any]): The posted form data.
        creating (bool): Whether this is the create form.

    Returns:
        tuple[dict[str, str], dict[str, str]]: ``(values, errors)``
        where ``values`` maps field name → plaintext for the fields that
        were filled and validated, and ``errors`` maps field name →
        message for the ones that were not.
    """
    columns = sa_inspect(admin.model).columns
    values: dict[str, str] = {}
    errors: dict[str, str] = {}
    for name in admin.password_fields:
        column = columns.get(name)
        if column is None:
            continue
        raw = form.get(name)
        plain = raw.strip() if isinstance(raw, str) else ""
        if not plain:
            if creating and not column.nullable:
                errors[name] = "This field is required."
            continue
        violation = check_password_policy(plain, admin.password_policy)
        if violation is not None:
            errors[name] = violation.message
            continue
        values[name] = plain
    return values, errors


def apply_password_fields(
    admin: AdminModel[Any],
    instance: Any,
    values: Mapping[str, str],
) -> None:
    """Hash each submitted plaintext onto the instance.

    Prefers the model's own ``set_password`` — which is what
    :class:`~tempest_fastapi_sdk.BaseUserModel` ships and where a project
    that overrides the hashing algorithm puts it — and falls back to
    :class:`~tempest_fastapi_sdk.PasswordUtils` for a model that declares
    a password column without one.

    Args:
        admin (AdminModel[Any]): The admin configuration.
        instance (Any): The row being created or edited.
        values (Mapping[str, str]): ``{field: plaintext}`` from
            :func:`parse_password_submission`.

    Raises:
        ImportError: When the model has no ``set_password`` and the
            ``[auth]`` extra (bcrypt) is not installed.
    """
    if not values:
        return
    setter = getattr(instance, "set_password", None)
    hasher: PasswordUtils | None = None
    for name, plain in values.items():
        if callable(setter) and name == _DEFAULT_PASSWORD_COLUMN:
            setter(plain)
            continue
        if hasher is None:
            hasher = PasswordUtils()
        setattr(instance, name, hasher.hash(plain))


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
    if py is _dt.time:
        return _dt.time.fromisoformat(raw)
    if py is _uuid.UUID:
        return _uuid.UUID(raw)
    return raw


__all__: list[str] = [
    "FormField",
    "build_form_fields",
    "fk_fields",
    "fk_label",
    "from_display_timezone",
    "inline_editable_names",
    "parse_submission",
    "to_display_timezone",
]
