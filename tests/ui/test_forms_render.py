"""Tests for the HTML a generated form produces."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, EmailStr, Field
from tempestweb.html import render_to_html

from tempest_fastapi_sdk.ui.forms import (
    FormClasses,
    form_for,
    form_spec_for,
    render_form,
)


class Role(StrEnum):
    """Roles rendered as select options."""

    ADMIN = "admin"
    USER = "user"


class SignupSchema(BaseModel):
    """The schema every render test generates a form from."""

    email: EmailStr
    password: str = Field(min_length=8, description="Mínimo de 8 caracteres")
    role: Role = Role.USER
    bio: str = Field(default="", max_length=2000)
    active: bool = False


def _html(**options: object) -> str:
    """Render a form for :class:`SignupSchema`.

    Args:
        **options (object): Forwarded to :func:`form_for`.

    Returns:
        str: The rendered HTML.
    """
    return render_to_html(form_for(SignupSchema, action="/signup", **options))


def test_form_element_carries_method_action_and_class() -> None:
    html = _html()
    assert html.startswith('<form method="post" action="/signup" class="tui-form">')
    assert html.endswith("</form>")


def test_labels_point_at_their_control() -> None:
    html = _html()
    assert '<label class="tui-field__label" for="f-email">' in html
    assert '<input name="email" id="f-email"' in html


def test_required_fields_are_marked_twice() -> None:
    """Once for the browser, once for the reader."""
    html = _html()
    assert 'required="required"' in html
    assert '<span class="tui-field__required" aria-hidden="true">*</span>' in html


def test_native_validation_attributes_are_emitted() -> None:
    assert 'minlength="8"' in _html()


def test_help_text_is_wired_through_aria_describedby() -> None:
    html = _html()
    assert 'aria-describedby="f-password-help"' in html
    assert '<small class="tui-field__help" id="f-password-help">' in html


def test_errors_mark_the_control_and_the_wrapper() -> None:
    html = _html(errors={"email": ["já cadastrado", "domínio bloqueado"]})
    assert 'class="tui-field tui-field--invalid"' in html
    assert 'aria-invalid="true"' in html
    assert 'aria-describedby="f-email-error"' in html
    assert '<p class="tui-field__error" id="f-email-error">já cadastrado</p>' in html
    assert "domínio bloqueado" in html


def test_form_level_errors_render_with_an_alert_role() -> None:
    html = _html(form_errors=["revise os campos"])
    assert '<div class="tui-form__errors" role="alert">' in html
    assert "<p>revise os campos</p>" in html


def test_select_renders_options_and_marks_the_current_one() -> None:
    html = _html(values={"role": "admin"})
    assert '<select name="role" id="f-role" class="tui-field__control">' in html
    assert '<option value="admin" selected="selected">Admin</option>' in html
    assert '<option value="user">User</option>' in html


def test_optional_select_offers_an_empty_option() -> None:
    assert '<option value=""></option>' in _html()


def test_textarea_holds_the_value_as_content() -> None:
    html = _html(values={"bio": "olá"})
    assert '<textarea name="bio" id="f-bio"' in html
    assert ">olá</textarea>" in html


def test_checkbox_uses_the_true_value_and_checked_state() -> None:
    unchecked = _html()
    checked = _html(values={"active": "true"})
    assert 'type="checkbox" value="true"' in unchecked
    assert 'checked="checked"' not in unchecked
    assert 'checked="checked"' in checked


def test_values_survive_a_re_render() -> None:
    html = _html(values={"email": "ana@example.com"})
    assert 'value="ana@example.com"' in html


def test_submitted_values_are_escaped() -> None:
    html = _html(values={"email": '"><script>alert(1)</script>'})
    assert "<script>" not in html
    assert "&lt;script&gt;" in html or "&quot;&gt;" in html


def test_submit_button_can_be_relabelled_or_dropped() -> None:
    assert '<button type="submit" class="tui-btn">Salvar</button>' in _html(
        submit_label="Salvar",
    )
    assert "<button" not in _html(submit=False)


def test_extra_form_attributes_reach_the_element() -> None:
    html = _html(attrs={"hx-post": "/signup", "hx-target": "#main"})
    assert 'hx-post="/signup"' in html
    assert 'hx-target="#main"' in html


def test_id_prefix_keeps_two_forms_apart() -> None:
    html = _html(id_prefix="new")
    assert 'id="new-email"' in html
    assert 'for="new-email"' in html


def test_class_names_are_configurable() -> None:
    html = render_to_html(
        form_for(
            SignupSchema,
            action="/signup",
            classes=FormClasses(form="my-form", control="my-control"),
        ),
    )
    assert 'class="my-form"' in html
    assert 'class="my-control"' in html
    assert 'class="tui-form"' not in html
    assert 'class="tui-field__control"' not in html


def test_include_and_exclude_change_the_rendered_fields() -> None:
    only_email = _html(include=["email"])
    assert 'name="email"' in only_email
    assert 'name="password"' not in only_email


def test_spec_can_be_patched_before_rendering() -> None:
    """The two-step API is what makes a generated form editable."""
    from dataclasses import replace

    spec = form_spec_for(SignupSchema, action="/signup")
    patched = replace(
        spec,
        fields=[replace(field, label=field.label.upper()) for field in spec.fields],
    )
    assert "<span>EMAIL</span>" in render_to_html(render_form(patched))


def test_get_forms_render_the_get_method() -> None:
    assert 'method="get"' in _html(method="get")
