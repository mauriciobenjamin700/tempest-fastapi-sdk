"""Turn a Pydantic schema into form field specifications.

The schema is the single source of truth: field names, types, defaults,
constraints (``min_length``, ``ge``, ``pattern``, …), titles and
descriptions all come from the model that already validates the request.
Writing the same form twice — once as HTML, once as a schema — is what
this module exists to delete.

Mapping rules, in the order they are checked:

| Schema field | Control |
| --- | --- |
| ``ui`` override in ``json_schema_extra`` | whatever it names |
| ``Enum`` / ``Literal`` | ``<select>`` |
| ``bool`` | ``<input type="checkbox">`` |
| ``int`` | ``number`` with ``step="1"`` |
| ``float`` / ``Decimal`` | ``number`` with ``step="any"`` |
| ``EmailStr`` | ``email`` |
| ``HttpUrl`` / ``AnyUrl`` | ``url`` |
| ``SecretStr``, or a name containing ``password``/``senha`` | ``password`` |
| ``date`` / ``datetime`` / ``time`` | ``date`` / ``datetime-local`` / ``time`` |
| ``UUID`` | ``text`` |
| ``str`` with ``max_length > 255`` | ``<textarea>`` |
| ``str`` | ``text`` |
| ``list[...]`` of enum values | multiple ``<select>`` |
| other ``list[...]`` | ``<textarea>``, one value per line |

Nested models and binary fields raise :class:`UnsupportedFieldError`
rather than rendering something that cannot round-trip — a nested model
needs its own form, and an upload needs FastAPI's ``UploadFile``.

Override anything per field with ``json_schema_extra``:

```python
from pydantic import BaseModel, Field


class ArticleSchema(BaseModel):
    body: str = Field(
        default="",
        json_schema_extra={"ui": {"control": "textarea", "rows": 12}},
    )
```

Recognised ``ui`` keys: ``control``, ``input_type``, ``label``,
``placeholder``, ``help_text``, ``autocomplete``, ``rows``, ``hidden``,
``attrs``.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Mapping, Sequence
from decimal import Decimal
from enum import Enum
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel, TypeAdapter
from pydantic_core import PydanticUndefined

from tempest_fastapi_sdk.ui.forms.spec import (
    Control,
    FieldSpec,
    FormClasses,
    FormSpec,
    SelectOption,
)

_TEXTAREA_LENGTH = 255
_PASSWORD_HINTS = ("password", "senha", "passwd")

_FORMAT_INPUT_TYPES: dict[str, str] = {
    "email": "email",
    "uri": "url",
    "password": "password",
    "date": "date",
    "date-time": "datetime-local",
    "time": "time",
    "uuid": "text",
    "ipv4": "text",
    "ipv6": "text",
}

_AUTOCOMPLETE_BY_TYPE: dict[str, str] = {
    "email": "email",
    "url": "url",
    "tel": "tel",
}


class UnsupportedFieldError(ValueError):
    """Raised when a schema field has no meaningful HTML control.

    Nested models and binary payloads are the two cases: a nested model
    needs a form of its own, and a file upload needs FastAPI's
    ``UploadFile`` rather than a value coerced from a string.
    """


def _humanize(name: str) -> str:
    """Turn a field name into a human label.

    Args:
        name (str): The schema field name.

    Returns:
        str: Title-cased label, matching the SDK's admin convention
        (``full_name`` becomes ``Full Name``).
    """
    return name.replace("_", " ").strip().title()


def _ui_overrides(field_info: Any) -> dict[str, Any]:
    """Read the ``ui`` block out of a field's ``json_schema_extra``.

    Args:
        field_info (Any): The Pydantic ``FieldInfo``.

    Returns:
        dict[str, Any]: The override mapping, empty when absent or when
        ``json_schema_extra`` is a callable rather than a mapping.
    """
    extra = getattr(field_info, "json_schema_extra", None)
    if not isinstance(extra, Mapping):
        return {}
    block = extra.get("ui")
    if not isinstance(block, Mapping):
        return {}
    return dict(block)


def _field_json_schema(annotation: Any) -> dict[str, Any]:
    """Build the JSON schema of a single annotation.

    Args:
        annotation (Any): The field annotation.

    Returns:
        dict[str, Any]: The generated schema, or an empty mapping when
        the type cannot produce one (custom types without a schema).
    """
    try:
        return dict(TypeAdapter(annotation).json_schema())
    except Exception:
        return {}


def _unwrap_optional_schema(node: Mapping[str, Any]) -> dict[str, Any]:
    """Collapse an ``anyOf`` produced by ``X | None`` into ``X``.

    Also collapses the ``anyOf`` Pydantic emits for ``Decimal``
    (number or numeric string) onto its first typed branch.

    Args:
        node (Mapping[str, Any]): A JSON-schema node.

    Returns:
        dict[str, Any]: The meaningful branch, merged with any sibling
        keys of the original node.
    """
    branches = node.get("anyOf") or node.get("oneOf")
    if not isinstance(branches, list):
        return dict(node)
    typed = [
        branch
        for branch in branches
        if isinstance(branch, Mapping) and branch.get("type") != "null"
    ]
    if not typed:
        return dict(node)
    merged = {
        key: value for key, value in node.items() if key not in {"anyOf", "oneOf"}
    }
    merged.update(typed[0])
    return merged


def _base_annotation(annotation: Any) -> Any:
    """Strip ``Optional`` from an annotation.

    Args:
        annotation (Any): The declared annotation.

    Returns:
        Any: The single non-``None`` member of a union, or the
        annotation unchanged when it is not an optional union.
    """
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        members = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation


def _enum_options(annotation: Any, node: Mapping[str, Any]) -> list[SelectOption]:
    """Build select options for an enum-like field.

    Enum classes are read from the annotation so option labels can use
    the member names; plain ``Literal`` values fall back to the JSON
    schema's ``enum`` list.

    Args:
        annotation (Any): The (optional-stripped) field annotation.
        node (Mapping[str, Any]): The field's JSON-schema node.

    Returns:
        list[SelectOption]: The options, in declaration order. Empty when
        the field is not enum-like.
    """
    inner = _base_annotation(annotation)
    if get_origin(inner) is list:
        args = get_args(inner)
        inner = args[0] if args else inner
    if isinstance(inner, type) and issubclass(inner, Enum):
        return [
            SelectOption(value=str(member.value), label=_humanize(member.name))
            for member in inner
        ]
    values = node.get("enum")
    if isinstance(values, list):
        return [SelectOption(value=str(value), label=str(value)) for value in values]
    items = node.get("items")
    if isinstance(items, Mapping) and isinstance(items.get("enum"), list):
        return [
            SelectOption(value=str(value), label=str(value)) for value in items["enum"]
        ]
    return []


def _constraints(field_info: Any, *, numeric: bool, integer: bool) -> dict[str, str]:
    """Derive native HTML validation attributes from field metadata.

    ``Gt`` / ``Lt`` map onto ``min`` / ``max``, which are inclusive in
    HTML: the browser hint is therefore one step looser than the schema.
    Pydantic still rejects the boundary value on submit, so the strict
    bound is enforced — just server-side.

    Args:
        field_info (Any): The Pydantic ``FieldInfo``.
        numeric (bool): Whether the field renders as a number input.
        integer (bool): Whether the numeric field is an integer.

    Returns:
        dict[str, str]: Attributes such as ``minlength``, ``max``,
        ``step`` and ``pattern``.
    """
    out: dict[str, str] = {}
    multiple_of: str | None = None

    for item in getattr(field_info, "metadata", ()):
        for attribute, target in (
            ("min_length", "minlength"),
            ("max_length", "maxlength"),
            ("ge", "min"),
            ("gt", "min"),
            ("le", "max"),
            ("lt", "max"),
            ("pattern", "pattern"),
        ):
            value = getattr(item, attribute, None)
            if value is not None:
                out[target] = str(value)
        found = getattr(item, "multiple_of", None)
        if found is not None:
            multiple_of = str(found)

    if numeric:
        out["step"] = multiple_of or ("1" if integer else "any")
    else:
        out.pop("min", None)
        out.pop("max", None)
    return out


def _format_value(value: Any) -> str:
    """Render a Python value as the string an HTML control carries.

    Args:
        value (Any): The value to format.

    Returns:
        str: The control value. ``None`` becomes an empty string, enums
        become their value, dates their ISO form, booleans ``"true"`` /
        ``""``.
    """
    if value is None or value is PydanticUndefined:
        return ""
    if isinstance(value, bool):
        return "true" if value else ""
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, _dt.datetime):
        return value.isoformat(timespec="minutes")
    if isinstance(value, (_dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return "\n".join(_format_value(item) for item in value)
    return str(value)


def _default_value(field_info: Any) -> Any:
    """Return a field's default, calling its factory when it has one.

    Args:
        field_info (Any): The Pydantic ``FieldInfo``.

    Returns:
        Any: The default value, or ``None`` when the field is required
        or the factory raises.
    """
    if field_info.default is not PydanticUndefined:
        return field_info.default
    factory = getattr(field_info, "default_factory", None)
    if factory is None:
        return None
    try:
        return factory()
    except Exception:
        return None


def _control_for(
    name: str,
    annotation: Any,
    node: Mapping[str, Any],
    field_info: Any,
) -> tuple[Control, str, bool]:
    """Pick the HTML control for one field.

    Args:
        name (str): The field name, used for the password heuristic.
        annotation (Any): The optional-stripped annotation.
        node (Mapping[str, Any]): The field's JSON-schema node.
        field_info (Any): The Pydantic ``FieldInfo``.

    Returns:
        tuple[Control, str, bool]: The control, the ``type`` attribute
        for an input, and whether a select accepts many values.

    Raises:
        UnsupportedFieldError: For nested models and binary fields.
    """
    json_type = node.get("type")
    json_format = node.get("format")

    if json_format == "binary" or json_type == "binary":
        raise UnsupportedFieldError(
            f"Field {name!r} is a binary upload; generated forms do not "
            "coerce files. Declare it as `UploadFile = File(...)` on the "
            "route and keep it out of the form schema.",
        )
    inner = _base_annotation(annotation)
    if isinstance(inner, type) and issubclass(inner, BaseModel):
        raise UnsupportedFieldError(
            f"Field {name!r} is a nested model ({inner.__name__}); render it "
            "with its own form_for(...) call, or exclude it and set it "
            "server-side.",
        )

    if node.get("enum") is not None:
        return ("select", "text", False)
    if json_type == "array":
        options = _enum_options(annotation, node)
        if options:
            return ("select", "text", True)
        return ("textarea", "text", False)
    if json_type == "boolean":
        return ("checkbox", "checkbox", False)
    if json_type == "integer":
        return ("input", "number", False)
    if json_type == "number":
        return ("input", "number", False)

    lowered = name.lower()
    if json_format == "password" or any(hint in lowered for hint in _PASSWORD_HINTS):
        return ("input", "password", False)
    if isinstance(json_format, str) and json_format in _FORMAT_INPUT_TYPES:
        return ("input", _FORMAT_INPUT_TYPES[json_format], False)

    max_length = next(
        (
            item.max_length
            for item in getattr(field_info, "metadata", ())
            if getattr(item, "max_length", None) is not None
        ),
        None,
    )
    if max_length is not None and int(max_length) > _TEXTAREA_LENGTH:
        return ("textarea", "text", False)
    return ("input", "text", False)


def fields_for(
    schema: type[BaseModel],
    *,
    values: Mapping[str, Any] | None = None,
    errors: Mapping[str, Sequence[str]] | None = None,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
) -> list[FieldSpec]:
    """Derive the field specifications of a Pydantic schema.

    Args:
        schema (type[BaseModel]): The schema describing the form.
        values (Mapping[str, Any] | None): Current values, overriding the
            schema defaults. Accepts raw submitted strings (as returned
            by :class:`~tempest_fastapi_sdk.ui.forms.FormResult`) or
            Python objects (as returned by ``model.model_dump()``).
        errors (Mapping[str, Sequence[str]] | None): Validation messages
            per field name, rendered under the matching control.
        include (Sequence[str]): When non-empty, render only these
            fields, in the given order.
        exclude (Sequence[str]): Field names to drop.

    Returns:
        list[FieldSpec]: One spec per rendered field, in schema
        declaration order (or ``include`` order when given).

    Raises:
        UnsupportedFieldError: When a field is a nested model or a binary
            upload and was not excluded.
        KeyError: When ``include`` names a field the schema lacks.

    Example:
        ```python
        from pydantic import BaseModel, EmailStr

        from tempest_fastapi_sdk.ui.forms import fields_for


        class SignupSchema(BaseModel):
            email: EmailStr
            age: int


        specs = fields_for(SignupSchema)
        assert [spec.input_type for spec in specs] == ["email", "number"]
        ```
    """
    model_fields: dict[str, Any] = dict(schema.model_fields)
    if include:
        missing = [name for name in include if name not in model_fields]
        if missing:
            raise KeyError(
                f"{schema.__name__} has no field(s): {', '.join(missing)}.",
            )
        names = list(include)
    else:
        names = [name for name in model_fields if name not in set(exclude)]

    supplied = dict(values or {})
    messages = dict(errors or {})
    specs: list[FieldSpec] = []

    for name in names:
        field_info = model_fields[name]
        overrides = _ui_overrides(field_info)
        if overrides.get("hidden"):
            continue

        annotation = field_info.annotation
        node = _unwrap_optional_schema(_field_json_schema(annotation))
        control, input_type, multiple = _control_for(name, annotation, node, field_info)

        control = str(overrides.get("control", control))  # type: ignore[assignment]
        input_type = str(overrides.get("input_type", input_type))

        raw = supplied[name] if name in supplied else _default_value(field_info)
        already_text = isinstance(raw, str) and not isinstance(raw, Enum)
        value = raw if already_text else _format_value(raw)
        selected = (
            [item for item in value.splitlines() if item]
            if multiple
            else [value]
            if value
            else []
        )
        if multiple and isinstance(raw, (list, tuple, set)):
            selected = [_format_value(item) for item in raw]

        options = [
            SelectOption(
                value=option.value,
                label=option.label,
                selected=option.value in selected,
            )
            for option in _enum_options(annotation, node)
        ]

        numeric = input_type == "number"
        constraints = _constraints(
            field_info,
            numeric=numeric,
            integer=node.get("type") == "integer",
        )

        extra_attrs = overrides.get("attrs")
        specs.append(
            FieldSpec(
                name=name,
                label=str(
                    overrides.get("label") or field_info.title or _humanize(name)
                ),
                control=control,
                input_type=input_type,
                value="" if multiple or control == "select" else value,
                selected_values=selected if multiple else (),
                options=options,
                required=field_info.is_required(),
                multiple=multiple,
                placeholder=str(overrides.get("placeholder", "")),
                help_text=str(
                    overrides.get("help_text") or field_info.description or "",
                ),
                errors=tuple(messages.get(name, ())),
                autocomplete=str(
                    overrides.get(
                        "autocomplete",
                        _AUTOCOMPLETE_BY_TYPE.get(input_type, ""),
                    ),
                ),
                rows=int(overrides.get("rows", 4)),
                constraints=constraints,
                attrs=dict(extra_attrs) if isinstance(extra_attrs, Mapping) else {},
            ),
        )
        if control == "select" and not multiple:
            specs[-1] = specs[-1].with_value(value)

    return specs


def form_spec_for(
    schema: type[BaseModel],
    *,
    action: str,
    method: str = "post",
    values: Mapping[str, Any] | None = None,
    errors: Mapping[str, Sequence[str]] | None = None,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    submit_label: str = "Enviar",
    submit: bool = True,
    form_errors: Sequence[str] = (),
    attrs: Mapping[str, str] | None = None,
    classes: FormClasses | None = None,
    id_prefix: str = "f",
) -> FormSpec:
    """Build the full :class:`FormSpec` of a schema.

    Args:
        schema (type[BaseModel]): The schema describing the form.
        action (str): The form ``action`` URL.
        method (str): ``"post"`` (default) or ``"get"``.
        values (Mapping[str, Any] | None): Current values.
        errors (Mapping[str, Sequence[str]] | None): Per-field messages.
        include (Sequence[str]): Render only these fields, in order.
        exclude (Sequence[str]): Field names to drop.
        submit_label (str): Text of the submit button.
        submit (bool): Whether to render the submit button.
        form_errors (Sequence[str]): Form-level messages.
        attrs (Mapping[str, str] | None): Extra attributes on the
            ``<form>`` element (``hx-post``, ``enctype``, …).
        classes (FormClasses | None): CSS class overrides.
        id_prefix (str): Prefix of the generated control ids. Give each
            form on the same page its own prefix.

    Returns:
        FormSpec: The form description, ready to render or to patch.

    Raises:
        ValueError: When ``method`` is neither ``"post"`` nor ``"get"``.
    """
    normalized = method.lower()
    if normalized not in {"post", "get"}:
        raise ValueError(f"Form method must be 'post' or 'get', got {method!r}.")
    return FormSpec(
        action=action,
        fields=fields_for(
            schema,
            values=values,
            errors=errors,
            include=include,
            exclude=exclude,
        ),
        method="post" if normalized == "post" else "get",
        submit_label=submit_label,
        errors=tuple(form_errors),
        submit=submit,
        attrs=dict(attrs or {}),
        classes=classes or FormClasses(),
        id_prefix=id_prefix,
    )


__all__: list[str] = [
    "UnsupportedFieldError",
    "fields_for",
    "form_spec_for",
]
