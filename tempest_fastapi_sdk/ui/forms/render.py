"""Render a :class:`FormSpec` into a typed widget tree.

The markup is deliberately plain and accessible: a ``<form>``, one
wrapper per field, a real ``<label for=…>``, the control, an optional
hint, and one paragraph per validation message. Invalid controls carry
``aria-invalid="true"`` and point at their hint and error through
``aria-describedby``.

!!! info "Why not `tempest_core`'s `Input` / `Dropdown` widgets"
    Measured against the ``tempestweb`` HTML renderer, the client-side
    form widgets do not survive server rendering: ``Form`` renders as a
    ``<div>``, ``Input`` renders without a ``name`` (so nothing is
    submitted), and ``Dropdown`` / ``TextArea`` render as empty
    ``<div>``s. This module therefore emits the elements directly
    through the documented ``tag`` / ``attrs`` escape hatch.
    ``tests/test_ui_forms_render.py`` pins the generated markup.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from tempest_fastapi_sdk.ui._core import Stack, Text, Widget, require_core
from tempest_fastapi_sdk.ui.forms.spec import FieldSpec, FormClasses, FormSpec

_TRUE = "true"


def _merge(*mappings: dict[str, str]) -> dict[str, str]:
    """Merge attribute mappings, dropping empty values.

    Later mappings win, so caller-supplied attributes override the
    generated ones.

    Args:
        *mappings (dict[str, str]): Attribute mappings, in priority
            order.

    Returns:
        dict[str, str]: The merged mapping without blank values.
    """
    out: dict[str, str] = {}
    for mapping in mappings:
        for key, value in mapping.items():
            if value == "":
                continue
            out[key] = value
    return out


def _field_id(spec: FormSpec, field: FieldSpec) -> str:
    """Return the DOM id of a field's control.

    Args:
        spec (FormSpec): The form the field belongs to.
        field (FieldSpec): The field.

    Returns:
        str: ``{id_prefix}-{name}``, unique per form on a page as long as
        each form carries its own ``id_prefix``.
    """
    return f"{spec.id_prefix}-{field.name}"


def _describedby(spec: FormSpec, field: FieldSpec) -> str:
    """Build the ``aria-describedby`` value of a control.

    Args:
        spec (FormSpec): The form the field belongs to.
        field (FieldSpec): The field.

    Returns:
        str: Space-separated ids of the hint and error elements that
        exist, empty when the field has neither.
    """
    base = _field_id(spec, field)
    parts: list[str] = []
    if field.help_text:
        parts.append(f"{base}-help")
    if field.has_errors:
        parts.append(f"{base}-error")
    return " ".join(parts)


def _control_attrs(spec: FormSpec, field: FieldSpec) -> dict[str, str]:
    """Build the attributes shared by every control of a field.

    Args:
        spec (FormSpec): The form the field belongs to.
        field (FieldSpec): The field.

    Returns:
        dict[str, str]: ``name``, ``id``, ``class``, ``required``,
        ``aria-invalid``, ``aria-describedby``, ``autocomplete`` and the
        schema-derived validation attributes.
    """
    return _merge(
        {
            "name": field.name,
            "id": _field_id(spec, field),
            "class": spec.classes.control,
            "required": "required" if field.required else "",
            "aria-invalid": _TRUE if field.has_errors else "",
            "aria-describedby": _describedby(spec, field),
            "autocomplete": field.autocomplete,
        },
        dict(field.constraints),
        dict(field.attrs),
    )


def _render_input(spec: FormSpec, field: FieldSpec) -> Widget:
    """Render an ``<input>`` control.

    Args:
        spec (FormSpec): The owning form.
        field (FieldSpec): The field to render.

    Returns:
        Widget: The input element.
    """
    checkbox = field.control == "checkbox"
    attrs = _merge(
        _control_attrs(spec, field),
        {
            "type": "checkbox" if checkbox else field.input_type,
            "value": _TRUE if checkbox else field.value,
            "checked": "checked" if checkbox and field.value else "",
            "placeholder": "" if checkbox else field.placeholder,
        },
    )
    return Text(content="", tag="input", attrs=attrs)


def _render_textarea(spec: FormSpec, field: FieldSpec) -> Widget:
    """Render a ``<textarea>`` control.

    Args:
        spec (FormSpec): The owning form.
        field (FieldSpec): The field to render.

    Returns:
        Widget: The textarea element, with the current value as its text
        content (escaped by the renderer).
    """
    attrs = _merge(
        _control_attrs(spec, field),
        {"rows": str(field.rows), "placeholder": field.placeholder},
    )
    return Text(content=field.value, tag="textarea", attrs=attrs)


def _render_select(spec: FormSpec, field: FieldSpec) -> Widget:
    """Render a ``<select>`` control and its options.

    An optional single select gets a leading empty option so the reader
    can clear the choice; its label is the field placeholder when one is
    set.

    Args:
        spec (FormSpec): The owning form.
        field (FieldSpec): The field to render.

    Returns:
        Widget: The select element with its options.
    """
    attrs = _merge(
        _control_attrs(spec, field),
        {"multiple": "multiple" if field.multiple else ""},
    )
    options: list[Widget] = []
    if not field.required and not field.multiple:
        options.append(
            Text(content=field.placeholder, tag="option", attrs={"value": ""}),
        )
    for option in field.options:
        selected = option.selected or option.value in field.selected_values
        options.append(
            Text(
                content=option.label,
                tag="option",
                attrs=_merge(
                    {"value": option.value},
                    {"selected": "selected" if selected else ""},
                ),
            ),
        )
    return Stack(tag="select", attrs=attrs, children=options)


def _render_control(spec: FormSpec, field: FieldSpec) -> Widget:
    """Render the control matching a field's :attr:`FieldSpec.control`.

    Args:
        spec (FormSpec): The owning form.
        field (FieldSpec): The field to render.

    Returns:
        Widget: The rendered control.
    """
    if field.control == "select":
        return _render_select(spec, field)
    if field.control == "textarea":
        return _render_textarea(spec, field)
    return _render_input(spec, field)


def render_field(spec: FormSpec, field: FieldSpec) -> Widget:
    """Render one field: label, control, hint and messages.

    Args:
        spec (FormSpec): The owning form, read for class names and the
            id prefix.
        field (FieldSpec): The field to render.

    Returns:
        Widget: The field wrapper.

    Raises:
        ImportError: When the optional ``[ssr]`` extra is missing.
    """
    require_core()
    base = _field_id(spec, field)
    classes = spec.classes
    label_children: list[Widget] = [Text(content=field.label, tag="span")]
    if field.required:
        label_children.append(
            Text(
                content="*",
                tag="span",
                attrs={"class": classes.required_mark, "aria-hidden": _TRUE},
            ),
        )

    children: list[Widget] = [
        Stack(
            tag="label",
            attrs={"class": classes.label, "for": base},
            children=label_children,
        ),
        _render_control(spec, field),
    ]
    if field.help_text:
        children.append(
            Text(
                content=field.help_text,
                tag="small",
                attrs={"class": classes.help_text, "id": f"{base}-help"},
            ),
        )
    for index, message in enumerate(field.errors):
        children.append(
            Text(
                content=message,
                tag="p",
                attrs=_merge(
                    {"class": classes.error},
                    {"id": f"{base}-error"} if index == 0 else {},
                ),
            ),
        )

    wrapper_class = classes.field
    if field.has_errors:
        wrapper_class = f"{classes.field} {classes.field_invalid}"
    return Stack(tag="div", attrs={"class": wrapper_class}, children=children)


def render_form(spec: FormSpec) -> Widget:
    """Render a whole form from its specification.

    Args:
        spec (FormSpec): The form description.

    Returns:
        Widget: The ``<form>`` element, holding the form-level errors,
        every field, and the submit button when
        :attr:`FormSpec.submit` is on.

    Raises:
        ImportError: When the optional ``[ssr]`` extra is missing.
    """
    require_core()
    classes = spec.classes
    children: list[Widget] = []

    if spec.errors:
        children.append(
            Stack(
                tag="div",
                attrs={"class": classes.errors, "role": "alert"},
                children=[Text(content=message, tag="p") for message in spec.errors],
            ),
        )
    children.extend(render_field(spec, field) for field in spec.fields)
    if spec.submit:
        children.append(
            Stack(
                tag="div",
                attrs={"class": classes.actions},
                children=[
                    Text(
                        content=spec.submit_label,
                        tag="button",
                        attrs={"type": "submit", "class": classes.submit},
                    ),
                ],
            ),
        )

    attrs = _merge(
        {
            "method": spec.method,
            "action": spec.action,
            "class": classes.form,
        },
        dict(spec.attrs),
    )
    return Stack(tag="form", attrs=attrs, children=children)


def form_for(
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
) -> Widget:
    """Generate and render a form for a Pydantic schema in one call.

    Sugar over :func:`~tempest_fastapi_sdk.ui.forms.form_spec_for`
    followed by :func:`render_form`. Reach for the two-step version when
    you want to patch the generated specification before rendering.

    Args:
        schema (type[BaseModel]): The schema describing the form.
        action (str): The form ``action`` URL.
        method (str): ``"post"`` (default) or ``"get"``.
        values (Mapping[str, Any] | None): Current values, overriding
            the schema defaults.
        errors (Mapping[str, Sequence[str]] | None): Per-field
            validation messages.
        include (Sequence[str]): Render only these fields, in order.
        exclude (Sequence[str]): Field names to drop.
        submit_label (str): Text of the submit button.
        submit (bool): Whether to render the submit button.
        form_errors (Sequence[str]): Form-level messages.
        attrs (Mapping[str, str] | None): Extra attributes on the
            ``<form>`` element (``hx-post``, ``enctype``, …).
        classes (FormClasses | None): CSS class overrides.
        id_prefix (str): Prefix of the generated control ids.

    Returns:
        Widget: The rendered ``<form>`` tree.

    Raises:
        ImportError: When the optional ``[ssr]`` extra is missing.
        UnsupportedFieldError: When a field is a nested model or a
            binary upload and was not excluded.

    Example:
        ```python
        from pydantic import BaseModel, EmailStr

        from tempest_fastapi_sdk.ui.forms import form_for


        class SignupSchema(BaseModel):
            email: EmailStr
            password: str


        widget = form_for(SignupSchema, action="/signup")
        ```
    """
    from tempest_fastapi_sdk.ui.forms.introspect import form_spec_for

    return render_form(
        form_spec_for(
            schema,
            action=action,
            method=method,
            values=values,
            errors=errors,
            include=include,
            exclude=exclude,
            submit_label=submit_label,
            submit=submit,
            form_errors=form_errors,
            attrs=attrs,
            classes=classes,
            id_prefix=id_prefix,
        ),
    )


__all__: list[str] = ["form_for", "render_field", "render_form"]
