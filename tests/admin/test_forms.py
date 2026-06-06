"""Unit tests for the admin form introspection + submission parsing."""

from __future__ import annotations

import enum

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AdminModel, BaseModel
from tempest_fastapi_sdk.admin.forms import build_form_fields, parse_submission


class Priority(enum.Enum):
    LOW = "low"
    HIGH = "high"


class FormWidget(BaseModel):
    __tablename__ = "admin_form_widget"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    priority: Mapped[Priority | None] = mapped_column(nullable=True, default=None)


_admin: AdminModel[FormWidget] = AdminModel(model=FormWidget)


def _by_name(fields: list) -> dict:
    return {f.name: f for f in fields}


class TestBuildFormFields:
    def test_excludes_auto_and_pk_columns(self) -> None:
        names = [f.name for f in build_form_fields(_admin)]
        assert "id" not in names
        assert "created_at" not in names
        assert "updated_at" not in names
        # editable business columns are present
        assert {"name", "bio", "count", "priority", "is_active"} <= set(names)

    def test_widget_mapping_per_type(self) -> None:
        fields = _by_name(build_form_fields(_admin))
        assert fields["name"].widget == "text"
        assert fields["bio"].widget == "textarea"  # Text → textarea
        assert fields["count"].widget == "number"
        assert fields["priority"].widget == "select"
        assert fields["is_active"].widget == "checkbox"

    def test_required_flag_from_nullable(self) -> None:
        fields = _by_name(build_form_fields(_admin))
        assert fields["name"].required is True  # non-nullable, no default
        assert fields["bio"].required is False  # nullable

    def test_select_options_from_enum(self) -> None:
        fields = _by_name(build_form_fields(_admin))
        values = [value for value, _label in fields["priority"].options]
        assert values == ["low", "high"]

    def test_prefills_from_instance(self) -> None:
        widget = FormWidget(name="Hello", count=7, is_active=False)
        fields = _by_name(build_form_fields(_admin, instance=widget))
        assert fields["name"].value == "Hello"
        assert fields["count"].value == 7
        assert fields["is_active"].checked is False


class TestParseSubmission:
    def test_coerces_scalar_types(self) -> None:
        data, errors = parse_submission(
            _admin,
            {"name": "Box", "count": "42", "priority": "high", "is_active": "true"},
        )
        assert errors == {}
        assert data["name"] == "Box"
        assert data["count"] == 42
        assert data["priority"] is Priority.HIGH
        assert data["is_active"] is True

    def test_missing_required_is_error(self) -> None:
        _data, errors = parse_submission(_admin, {"name": ""})
        assert "name" in errors
        assert "count" not in errors  # optional, omitted

    def test_blank_optional_becomes_none(self) -> None:
        data, errors = parse_submission(_admin, {"name": "X", "count": ""})
        assert errors == {}
        assert data["count"] is None

    def test_unchecked_checkbox_is_false(self) -> None:
        # checkbox absent from the form payload → False.
        data, _errors = parse_submission(_admin, {"name": "X"})
        assert data["is_active"] is False

    def test_bad_number_reports_error(self) -> None:
        _data, errors = parse_submission(_admin, {"name": "X", "count": "not-a-number"})
        assert "count" in errors
