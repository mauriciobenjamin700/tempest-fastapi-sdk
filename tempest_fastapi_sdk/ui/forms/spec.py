"""The data model of a generated form: what to render, before rendering.

Introspection (:mod:`tempest_fastapi_sdk.ui.forms.introspect`) produces
these objects; rendering (:mod:`tempest_fastapi_sdk.ui.forms.render`)
consumes them. Keeping the two apart means a service can inspect, reorder
or patch a generated form — change a label, add an ``autocomplete``, drop
a field — without reaching into widget trees, and means the mapping from
Pydantic to HTML controls is testable on plain data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

Control = Literal["input", "textarea", "select", "checkbox"]
"""Which HTML control renders a field."""


@dataclass(frozen=True, slots=True)
class SelectOption:
    """One ``<option>`` of a ``<select>`` control.

    Attributes:
        value (str): The submitted value.
        label (str): The text shown to the reader.
        selected (bool): Whether the option renders as selected.
    """

    value: str
    label: str
    selected: bool = False


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Everything needed to render one form field.

    Attributes:
        name (str): The submitted field name, matching the schema field.
        label (str): The ``<label>`` text.
        control (Control): Which HTML control to render.
        input_type (str): The ``type`` attribute when ``control`` is
            ``"input"`` (``text``, ``email``, ``number``, ``date``,
            ``password``, …).
        value (str): The current single value, pre-filled into the
            control.
        selected_values (Sequence[str]): Current values for a multiple
            select. Ignored by the other controls.
        options (Sequence[SelectOption]): Options of a ``select``.
        required (bool): Whether the control renders ``required``.
        multiple (bool): Whether a ``select`` accepts many values.
        placeholder (str): Placeholder text for text-like controls.
        help_text (str): Hint rendered under the control and wired to it
            through ``aria-describedby``.
        errors (Sequence[str]): Validation messages for this field. A
            non-empty list also marks the control ``aria-invalid``.
        autocomplete (str): The ``autocomplete`` attribute, when known.
        rows (int): Row count for a ``textarea``.
        constraints (Mapping[str, str]): Native validation attributes
            derived from the schema — ``minlength``, ``maxlength``,
            ``min``, ``max``, ``step``, ``pattern``.
        attrs (Mapping[str, str]): Extra attributes merged into the
            control, last, so they win.
    """

    name: str
    label: str
    control: Control = "input"
    input_type: str = "text"
    value: str = ""
    selected_values: Sequence[str] = ()
    options: Sequence[SelectOption] = ()
    required: bool = False
    multiple: bool = False
    placeholder: str = ""
    help_text: str = ""
    errors: Sequence[str] = ()
    autocomplete: str = ""
    rows: int = 4
    constraints: Mapping[str, str] = field(default_factory=dict)
    attrs: Mapping[str, str] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        """Whether the field carries at least one validation message.

        Returns:
            bool: ``True`` when :attr:`errors` is non-empty.
        """
        return bool(self.errors)

    def with_value(self, value: str) -> FieldSpec:
        """Return a copy pre-filled with a different value.

        Args:
            value (str): The new single value.

        Returns:
            FieldSpec: A copy with ``value`` replaced and any ``select``
            options re-marked to match.
        """
        options = tuple(
            replace(option, selected=option.value == value) for option in self.options
        )
        return replace(self, value=value, options=options)


@dataclass(frozen=True, slots=True)
class FormClasses:
    """CSS class names used by the rendered form.

    The defaults match the classes defined by
    :data:`tempest_fastapi_sdk.ui.forms.FORM_STYLESHEET`, so including
    that sheet styles a generated form with no further work. Override
    them to plug the form into a design system of your own.

    Attributes:
        form (str): Class of the ``<form>`` element.
        errors (str): Class of the form-level error list.
        field (str): Class of each field wrapper.
        field_invalid (str): Extra class added to a wrapper holding an
            invalid field.
        label (str): Class of each ``<label>``.
        control (str): Class of each input / select / textarea.
        help_text (str): Class of the hint under a control.
        error (str): Class of a field-level error message.
        actions (str): Class of the wrapper holding the submit button.
        submit (str): Class of the submit button.
        required_mark (str): Class of the marker appended to a required
            field's label.
    """

    form: str = "tui-form"
    errors: str = "tui-form__errors"
    field: str = "tui-field"
    field_invalid: str = "tui-field--invalid"
    label: str = "tui-field__label"
    control: str = "tui-field__control"
    help_text: str = "tui-field__help"
    error: str = "tui-field__error"
    actions: str = "tui-form__actions"
    submit: str = "tui-btn"
    required_mark: str = "tui-field__required"


@dataclass(frozen=True, slots=True)
class FormSpec:
    """A whole form: where it posts, what it holds, how it is labelled.

    Attributes:
        action (str): The ``action`` URL.
        fields (Sequence[FieldSpec]): The fields, in render order.
        method (Literal["post", "get"]): The HTTP method.
        submit_label (str): Text of the submit button.
        errors (Sequence[str]): Form-level messages, rendered above the
            fields.
        submit (bool): Whether to render the submit button. Turn it off
            to place your own actions around the generated fields.
        attrs (Mapping[str, str]): Extra attributes on the ``<form>``
            element — this is where ``hx-post`` / ``hx-target`` go.
        classes (FormClasses): The CSS class names to apply.
        id_prefix (str): Prefix of the generated control ids
            (``{id_prefix}-{field name}``), which also anchor the
            ``<label for=…>`` and ``aria-describedby`` wiring. Give each
            form on a page its own prefix so the ids stay unique.
    """

    action: str
    fields: Sequence[FieldSpec]
    method: Literal["post", "get"] = "post"
    submit_label: str = "Enviar"
    errors: Sequence[str] = ()
    submit: bool = True
    attrs: Mapping[str, str] = field(default_factory=dict)
    classes: FormClasses = field(default_factory=FormClasses)
    id_prefix: str = "f"

    def field_named(self, name: str) -> FieldSpec:
        """Return one field by name.

        Args:
            name (str): The field name to look up.

        Returns:
            FieldSpec: The matching field.

        Raises:
            KeyError: When no field carries that name.
        """
        for item in self.fields:
            if item.name == name:
                return item
        raise KeyError(
            f"No field named {name!r}. "
            f"Fields: {', '.join(item.name for item in self.fields) or '(none)'}.",
        )


__all__: list[str] = [
    "Control",
    "FieldSpec",
    "FormClasses",
    "FormSpec",
    "SelectOption",
]
