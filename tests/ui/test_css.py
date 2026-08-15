"""Unit tests for the typed CSS API."""

from __future__ import annotations

import pytest
from tempest_core import Style, default_tokens
from tempest_core.style import Edge
from tempestweb.html import style_to_css

from tempest_fastapi_sdk.ui.css import (
    Media,
    Rule,
    StyleSheet,
    ThemeTokens,
    cls,
)


def test_rule_renders_style_declarations() -> None:
    rendered = Rule(".card", style=Style(padding=Edge.all(16), radius=8.0)).to_css()
    assert rendered == (
        ".card {\n  padding: 16px 16px 16px 16px;\n  border-radius: 8px;\n}"
    )


def test_rule_declarations_win_over_style() -> None:
    rendered = Rule(
        ".card",
        style=Style(radius=8.0),
        declarations={"border-radius": "0"},
    ).to_css()
    assert rendered.count("border-radius") == 2
    assert rendered.index("border-radius: 0") > rendered.index("border-radius: 8px")


def test_rule_layout_adds_flex() -> None:
    assert Rule(".row", layout="row").to_css() == (
        ".row {\n  display: flex;\n  flex-direction: row;\n}"
    )


def test_rule_layout_with_style_matches_the_widget_rendering() -> None:
    """A rule and the equivalent widget emit the same declarations."""
    rule = Rule(".stack", style=Style(gap=12.0), layout="column").to_css()
    inline = style_to_css(Style(gap=12.0).model_dump(), "Column")
    for declaration in inline.split("; "):
        assert declaration in rule


def test_empty_rule_renders_nothing() -> None:
    assert Rule(".empty").to_css() == ""


def test_rule_class_names() -> None:
    assert Rule(".card:hover .card__title").class_names() == {"card", "card__title"}
    assert Rule("body > main").class_names() == set()


def test_media_wraps_and_indents() -> None:
    rendered = Media.min_width(768, [Rule(".card", declarations={"padding": "24px"})])
    assert rendered.to_css() == (
        "@media (min-width: 768px) {\n  .card {\n    padding: 24px;\n  }\n}"
    )


def test_media_helpers_build_expected_queries() -> None:
    rules = [Rule(".x", declarations={"color": "red"})]
    assert Media.max_width(600, rules).query == "(max-width: 600px)"
    assert Media.dark(rules).query == "(prefers-color-scheme: dark)"
    assert Media.reduced_motion(rules).query == "(prefers-reduced-motion: reduce)"


def test_media_with_no_declarations_renders_nothing() -> None:
    assert Media.min_width(768, [Rule(".empty")]).to_css() == ""


def test_stylesheet_orders_reset_tokens_then_rules() -> None:
    sheet = StyleSheet(
        theme=ThemeTokens(),
        rules=[Rule(".card", declarations={"color": "red"})],
    )
    css = sheet.to_css()
    assert css.index("box-sizing") < css.index("--t-color-primary")
    assert css.index("--t-color-primary") < css.index(".card")


def test_stylesheet_without_reset_or_theme() -> None:
    sheet = StyleSheet(rules=[Rule(".a", declarations={"color": "red"})], reset=False)
    css = sheet.to_css()
    assert css == ".a {\n  color: red;\n}\n"


def test_stylesheet_extra_css_comes_last() -> None:
    sheet = StyleSheet(
        rules=[Rule(".a", declarations={"color": "red"})],
        reset=False,
        extra_css="@font-face { font-family: X; }",
    )
    assert sheet.to_css().rstrip().endswith("@font-face { font-family: X; }")


def test_cls_joins_and_drops_blanks() -> None:
    assert cls("card", "", "card--wide") == {"class": "card card--wide"}
    assert cls() == {}
    assert cls("  ") == {}


def test_stylesheet_cls_rejects_unknown_names() -> None:
    sheet = StyleSheet(rules=[Rule(".card", declarations={"color": "red"})])
    assert sheet.cls("card") == {"class": "card"}
    with pytest.raises(KeyError, match="not defined by this stylesheet"):
        sheet.cls("crad")


def test_merge_concatenates_and_keeps_theme() -> None:
    first = StyleSheet(
        theme=ThemeTokens(),
        rules=[Rule(".a", declarations={"color": "red"})],
    )
    second = StyleSheet(rules=[Rule(".b", declarations={"color": "blue"})], reset=False)
    merged = first.merge(second)
    assert [rule.selector for rule in merged.rules] == [".a", ".b"]
    assert merged.theme is first.theme
    assert merged.reset is True


def test_etag_is_quoted_and_content_addressed() -> None:
    sheet = StyleSheet(rules=[Rule(".a", declarations={"color": "red"})])
    other = StyleSheet(rules=[Rule(".a", declarations={"color": "blue"})])
    assert sheet.etag().startswith('"') and sheet.etag().endswith('"')
    assert (
        sheet.etag()
        == StyleSheet(
            rules=[Rule(".a", declarations={"color": "red"})],
        ).etag()
    )
    assert sheet.etag() != other.etag()


def test_theme_tokens_emit_light_and_dark_blocks() -> None:
    css = ThemeTokens().to_css()
    assert css.startswith(":root {")
    assert "@media (prefers-color-scheme: dark) {" in css
    assert ':root:not([data-theme="light"])' in css
    assert ':root[data-theme="dark"]' in css


def test_theme_tokens_without_dark_mode() -> None:
    css = ThemeTokens(dark_mode=False).to_css()
    assert "prefers-color-scheme" not in css
    assert "data-theme" not in css


def test_theme_token_colour_matches_inline_rendering() -> None:
    """A token value and an inline style for the same colour agree."""
    primary = default_tokens().model_dump()["schemes"]["light"]["primary"]
    hex_color = f"#{primary['r']:02x}{primary['g']:02x}{primary['b']:02x}"
    inline = style_to_css(Style(color=hex_color).model_dump())
    expected = inline.removeprefix("color: ")
    assert f"--t-color-primary: {expected};" in ThemeTokens().to_css()


def test_theme_tokens_emit_every_scale() -> None:
    css = ThemeTokens().to_css()
    assert "--t-space-md: 16.0px;" in css
    assert "--t-radius-full: 999.0px;" in css
    assert "--t-font-size-body-medium: 14.0px;" in css
    assert "--t-font-weight-body-medium: 400;" in css
    assert "--t-duration-short: 150ms;" in css
    assert "--t-easing-standard: ease-in-out;" in css


def test_theme_var_helpers() -> None:
    theme = ThemeTokens()
    assert theme.color("on_surface") == "var(--t-color-on-surface)"
    assert theme.space("md") == "var(--t-space-md)"
    assert theme.radius("full") == "var(--t-radius-full)"
    assert theme.font_size("body_medium") == "var(--t-font-size-body-medium)"


def test_theme_prefix_is_configurable() -> None:
    theme = ThemeTokens(prefix="app")
    assert theme.color("primary") == "var(--app-color-primary)"
    assert "--app-color-primary:" in theme.to_css()


def test_theme_breakpoint_returns_pixels_and_rejects_unknown() -> None:
    theme = ThemeTokens()
    assert theme.breakpoint("md") == 600.0
    with pytest.raises(KeyError, match="Unknown breakpoint"):
        theme.breakpoint("xxl")
