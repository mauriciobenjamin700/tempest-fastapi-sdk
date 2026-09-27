"""Tests for the composed application stylesheet."""

from __future__ import annotations

from tempest_fastapi_sdk.ui import app_stylesheet
from tempest_fastapi_sdk.ui.components import ComponentClasses
from tempest_fastapi_sdk.ui.css import SYSTEM_FONT_STACK, Rule, StyleSheet, ThemeTokens
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


def test_body_font_family_is_a_token_applied_by_the_reset() -> None:
    """Without a family the browser default is serif; the reset sets one."""
    css = app_stylesheet().to_css()
    assert f"--t-font-family-body: {SYSTEM_FONT_STACK};" in css
    assert "body {\n  margin: 0;\n  font-family: var(--t-font-family-body);\n}" in css
    assert SYSTEM_FONT_STACK.startswith("system-ui")
    assert SYSTEM_FONT_STACK.endswith("sans-serif")


def test_body_font_family_follows_the_theme() -> None:
    theme = ThemeTokens(prefix="app", font_family_body='"Inter", sans-serif')
    css = app_stylesheet(theme=theme).to_css()
    assert '--app-font-family-body: "Inter", sans-serif;' in css
    assert "font-family: var(--app-font-family-body);" in css
    assert theme.font_family() == "var(--app-font-family-body)"


def test_reset_without_theme_uses_the_literal_stack() -> None:
    css = StyleSheet().to_css()
    assert f"font-family: {SYSTEM_FONT_STACK};" in css
    assert "var(--" not in css


def test_shell_widths_are_in_the_generated_css() -> None:
    css = app_stylesheet().to_css()
    contained = css[css.index(".tui-shell__main {") :]
    contained = contained[: contained.index("}")]
    assert "max-width: 72rem;" in contained
    assert "margin: 0 auto;" in contained
    assert (
        ".tui-shell__main--full {\n  max-width: none;\n  margin: 0;\n  padding: 0;\n}"
    ) in css
    assert css.index(".tui-shell__main {") < css.index(".tui-shell__main--full {")


def test_table_alignment_modifiers_are_in_the_generated_css() -> None:
    css = app_stylesheet().to_css()
    assert (
        ".tui-table .tui-table__cell--right {\n"
        "  text-align: right;\n"
        "  font-variant-numeric: tabular-nums;\n"
        "}"
    ) in css
    assert ".tui-table .tui-table__cell--center {" in css
    assert ".tui-table .tui-table__cell--left {" in css
