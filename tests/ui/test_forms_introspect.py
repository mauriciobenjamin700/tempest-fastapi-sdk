"""Tests for deriving form fields from Pydantic schemas."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

import pytest
from pydantic import BaseModel, EmailStr, Field, HttpUrl, SecretStr

from tempest_fastapi_sdk.ui.forms import (
    UnsupportedFieldError,
    fields_for,
    form_spec_for,
)


class Role(StrEnum):
    """Roles used by the select-mapping tests."""

    ADMIN = "admin"
    STAFF_USER = "staff"


class WideSchema(BaseModel):
    """One field per mapping rule."""

    email: EmailStr
    site: HttpUrl | None = None
    secret: SecretStr
    name: str = Field(min_length=3, max_length=50, description="Nome completo")
    bio: str = Field(default="", max_length=2000)
    age: int = Field(ge=18, le=120)
    price: Decimal = Field(gt=0, multiple_of=0.01)
    role: Role = Role.STAFF_USER
    kind: Literal["a", "b"] = "a"
    born: date | None = None
    seen_at: datetime | None = None
    alarm: time | None = None
    uid: UUID | None = None
    active: bool = True
    tags: list[str] = Field(default_factory=list)
    roles: list[Role] = Field(default_factory=list)


def _by_name(schema: type[BaseModel]) -> dict[str, object]:
    """Index the generated specs by field name.

    Args:
        schema (type[BaseModel]): The schema to introspect.

    Returns:
        dict[str, object]: Field name to its spec.
    """
    return {spec.name: spec for spec in fields_for(schema)}


def test_control_and_input_type_per_field() -> None:
    specs = _by_name(WideSchema)
    assert (specs["email"].control, specs["email"].input_type) == ("input", "email")
    assert specs["site"].input_type == "url"
    assert specs["secret"].input_type == "password"
    assert (specs["name"].control, specs["name"].input_type) == ("input", "text")
    assert specs["bio"].control == "textarea"
    assert specs["age"].input_type == "number"
    assert specs["price"].input_type == "number"
    assert specs["role"].control == "select"
    assert specs["kind"].control == "select"
    assert specs["born"].input_type == "date"
    assert specs["seen_at"].input_type == "datetime-local"
    assert specs["alarm"].input_type == "time"
    assert specs["uid"].input_type == "text"
    assert specs["active"].control == "checkbox"
    assert specs["tags"].control == "textarea"
    assert (specs["roles"].control, specs["roles"].multiple) == ("select", True)


def test_constraints_come_from_field_metadata() -> None:
    specs = _by_name(WideSchema)
    assert specs["name"].constraints == {"minlength": "3", "maxlength": "50"}
    assert specs["age"].constraints == {"min": "18", "max": "120", "step": "1"}
    assert specs["price"].constraints["step"] == "0.01"
    assert specs["price"].constraints["min"] == "0"


def test_required_reflects_the_schema() -> None:
    specs = _by_name(WideSchema)
    assert specs["email"].required is True
    assert specs["born"].required is False


def test_labels_help_and_autocomplete() -> None:
    specs = _by_name(WideSchema)
    assert specs["name"].label == "Name"
    assert specs["name"].help_text == "Nome completo"
    assert specs["email"].autocomplete == "email"
    assert specs["site"].autocomplete == "url"


def test_enum_options_use_member_names_literals_use_values() -> None:
    specs = _by_name(WideSchema)
    assert [(option.value, option.label) for option in specs["role"].options] == [
        ("admin", "Admin"),
        ("staff", "Staff User"),
    ]
    assert [option.value for option in specs["kind"].options] == ["a", "b"]


def test_defaults_prefill_the_control() -> None:
    specs = _by_name(WideSchema)
    assert specs["active"].value == "true"
    selected = [option.value for option in specs["role"].options if option.selected]
    assert selected == ["staff"]


def test_password_heuristic_by_field_name() -> None:
    class LoginSchema(BaseModel):
        """Password detected by name, not by type."""

        password: str
        senha_antiga: str

    specs = _by_name(LoginSchema)
    assert specs["password"].input_type == "password"
    assert specs["senha_antiga"].input_type == "password"


def test_ui_overrides_win() -> None:
    class ArticleSchema(BaseModel):
        """Overrides supplied through ``json_schema_extra``."""

        body: str = Field(
            default="",
            json_schema_extra={
                "ui": {
                    "control": "textarea",
                    "rows": 12,
                    "label": "Corpo",
                    "placeholder": "Escreva…",
                    "help_text": "Markdown aceito",
                    "autocomplete": "off",
                    "attrs": {"data-editor": "md"},
                },
            },
        )
        internal: str = Field(default="", json_schema_extra={"ui": {"hidden": True}})

    specs = _by_name(ArticleSchema)
    assert "internal" not in specs
    body = specs["body"]
    assert (body.control, body.rows, body.label) == ("textarea", 12, "Corpo")
    assert body.placeholder == "Escreva…"
    assert body.help_text == "Markdown aceito"
    assert body.autocomplete == "off"
    assert body.attrs == {"data-editor": "md"}


def test_values_override_defaults_and_accept_objects() -> None:
    from_strings = {
        spec.name: spec for spec in fields_for(WideSchema, values={"age": "42"})
    }
    assert from_strings["age"].value == "42"

    from_objects = {
        spec.name: spec
        for spec in fields_for(
            WideSchema,
            values={"born": date(2020, 5, 4), "role": Role.ADMIN},
        )
    }
    assert from_objects["born"].value == "2020-05-04"
    assert [o.value for o in from_objects["role"].options if o.selected] == ["admin"]


def test_multiple_select_marks_every_selected_value() -> None:
    specs = {
        spec.name: spec
        for spec in fields_for(
            WideSchema,
            values={"roles": [Role.ADMIN, Role.STAFF_USER]},
        )
    }
    assert specs["roles"].selected_values == ["admin", "staff"]
    assert all(option.selected for option in specs["roles"].options)


def test_errors_attach_to_their_field() -> None:
    specs = {
        spec.name: spec
        for spec in fields_for(WideSchema, errors={"email": ["já cadastrado"]})
    }
    assert specs["email"].errors == ("já cadastrado",)
    assert specs["email"].has_errors is True
    assert specs["age"].has_errors is False


def test_include_and_exclude() -> None:
    assert [spec.name for spec in fields_for(WideSchema, include=["age", "email"])] == [
        "age",
        "email",
    ]
    names = [spec.name for spec in fields_for(WideSchema, exclude=["email", "secret"])]
    assert "email" not in names and "secret" not in names


def test_include_rejects_unknown_names() -> None:
    with pytest.raises(KeyError, match="has no field"):
        fields_for(WideSchema, include=["nope"])


def test_nested_model_is_rejected() -> None:
    class AddressSchema(BaseModel):
        """Nested payload."""

        street: str

    class PersonSchema(BaseModel):
        """Holds a nested model."""

        address: AddressSchema

    with pytest.raises(UnsupportedFieldError, match="nested model"):
        fields_for(PersonSchema)
    assert fields_for(PersonSchema, exclude=["address"]) == []


def test_binary_field_is_rejected() -> None:
    class UploadSchema(BaseModel):
        """Holds raw bytes."""

        payload: bytes

    with pytest.raises(UnsupportedFieldError, match="binary upload"):
        fields_for(UploadSchema)


def test_form_spec_carries_form_level_settings() -> None:
    spec = form_spec_for(
        WideSchema,
        action="/users",
        method="POST",
        submit_label="Salvar",
        form_errors=["revise"],
        attrs={"hx-post": "/users"},
        id_prefix="new",
        exclude=["secret"],
    )
    assert spec.method == "post"
    assert spec.action == "/users"
    assert spec.submit_label == "Salvar"
    assert spec.errors == ("revise",)
    assert spec.attrs == {"hx-post": "/users"}
    assert spec.id_prefix == "new"
    assert spec.field_named("email").name == "email"
    with pytest.raises(KeyError, match="No field named"):
        spec.field_named("secret")


def test_form_spec_rejects_other_methods() -> None:
    with pytest.raises(ValueError, match="must be 'post' or 'get'"):
        form_spec_for(WideSchema, action="/users", method="put")
