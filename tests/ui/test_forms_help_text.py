"""Tests for how the hint under a control is chosen (#351)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field
from tempestweb.html import render_to_html

from tempest_fastapi_sdk.ui.forms import fields_for, form_for


class DocumentedSchema(BaseModel):
    """Fields whose descriptions are written for developers."""

    key: str = Field(description="Key of the object to remove.")
    label: str = Field(
        description="Developer note.",
        json_schema_extra={"ui": {"help_text": "Nome visível"}},
    )


@pytest.mark.parametrize("suppressed", ["", None, False])
def test_present_help_text_key_suppresses_the_hint(suppressed: object) -> None:
    class QuietSchema(BaseModel):
        """A field whose hint is explicitly turned off."""

        key: str = Field(
            description="Key of the object to remove.",
            json_schema_extra={"ui": {"help_text": suppressed}},
        )

    assert fields_for(QuietSchema)[0].help_text == ""
    html = render_to_html(form_for(QuietSchema, action="/x"))
    assert "<small" not in html
    assert "aria-describedby" not in html
    assert "Key of the object to remove." not in html


def test_description_is_the_default_hint() -> None:
    html = render_to_html(form_for(DocumentedSchema, action="/x"))
    assert "Key of the object to remove." in html


def test_describe_false_turns_the_description_fallback_off() -> None:
    html = render_to_html(form_for(DocumentedSchema, action="/x", describe=False))
    assert "Key of the object to remove." not in html
    assert "Nome visível" in html
    specs = fields_for(DocumentedSchema, describe=False)
    assert [spec.help_text for spec in specs] == ["", "Nome visível"]
