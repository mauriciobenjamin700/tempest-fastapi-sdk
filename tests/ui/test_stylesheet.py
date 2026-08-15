"""Tests for the composed application stylesheet."""

from __future__ import annotations

from tempest_fastapi_sdk.ui import app_stylesheet
from tempest_fastapi_sdk.ui.components import ComponentClasses
from tempest_fastapi_sdk.ui.css import Rule, StyleSheet, ThemeTokens
from tempest_fastapi_sdk.ui.forms import FormClasses


def test_composes_reset_tokens_forms_and_components() -> None:
    css = app_stylesheet().to_css()
    assert css.index("box-sizing") < css.index("--t-color-primary")
    assert css.index("--t-color-primary") < css.index(".tui-form {")
    assert css.index(".tui-form {") < css.index(".tui-card {")


def test_extra_rules_come_last() -> None:
    extra = StyleSheet(
        rules=[Rule(".mine", declarations={"color": "red"})],
        reset=False,
    )
    css = app_stylesheet(extra=extra).to_css()
    assert css.index(".tui-card {") < css.index(".mine {")


def test_reset_can_be_switched_off() -> None:
    assert "box-sizing" not in app_stylesheet(reset=False).to_css()


def test_custom_theme_prefix_reaches_every_rule() -> None:
    css = app_stylesheet(theme=ThemeTokens(prefix="app")).to_css()
    assert "--app-color-primary:" in css
    assert "var(--app-color-primary)" in css
    assert "var(--t-" not in css


def test_custom_class_names_reach_the_rules() -> None:
    sheet = app_stylesheet(
        classes=ComponentClasses(card="box"),
        form_classes=FormClasses(form="my-form"),
    )
    names = sheet.class_names()
    assert "box" in names
    assert "my-form" in names
