"""Pin the renderer behaviour the ``ui`` layer is built on.

Every assertion here is a property of ``tempest_core`` +
``tempestweb.html`` that ``ui`` depends on. They live in one file so an
upstream change surfaces as a named failure ("Stack stopped rendering a
bare div") instead of as subtly broken markup somewhere in a component.
"""

from __future__ import annotations

from tempest_core import Column, Row, Style, Text
from tempest_core.widgets import Stack
from tempestweb.html import render_to_html, style_to_css


def test_stack_renders_a_bare_element_without_injected_style() -> None:
    """``Stack`` is the neutral container the layer uses for semantics."""
    html = render_to_html(
        Stack(
            tag="select",
            attrs={"name": "role"},
            children=[Text(content="A", tag="option")],
        ),
    )
    assert html == '<select name="role"><option>A</option></select>'
    assert "style=" not in html


def test_column_and_row_inject_flex_by_widget_type() -> None:
    """Flex containers are flex even with no explicit style — hence Stack."""
    column = render_to_html(Column(children=[]))
    assert "display: flex; flex-direction: column" in column
    assert "display: flex; flex-direction: row" in render_to_html(Row(children=[]))


def test_text_honours_tag_and_attrs() -> None:
    """The tag/attrs escape hatch is what emits real form controls."""
    html = render_to_html(
        Text(content="", tag="input", attrs={"name": "email", "type": "email"}),
    )
    assert html == '<input name="email" type="email" />'


def test_text_content_is_escaped() -> None:
    """User data reaching a page cannot inject markup."""
    assert render_to_html(Text(content="<script>x</script>")) == (
        "<span>&lt;script&gt;x&lt;/script&gt;</span>"
    )


def test_core_form_widgets_render_no_name_attribute() -> None:
    """Why ``ui.forms`` emits elements itself instead of reusing them.

    Measured, not assumed, and the measurement moved: on
    ``tempest-core`` 0.14.0 these widgets rendered a bare ``<div>``,
    losing both the element type and the option list. On 0.18.0 the
    element types are right — ``<input>``, ``<textarea>``, and a
    ``<select>`` carrying its ``<option>`` list — so that half of the
    reason is upstream's now.

    What remains is the half that decides it: none of the three renders
    a ``name`` attribute, and a control without a name submits nothing.
    A form built out of them would post an empty body, which is a
    failure with no error message anywhere.
    """
    from tempest_core.widgets import Dropdown, Form, Input, TextArea

    dropdown = render_to_html(Dropdown(options=["a", "b"]))

    assert render_to_html(Form()) == "<div></div>"

    assert "name=" not in render_to_html(Input(value=""))
    assert "name=" not in render_to_html(TextArea(value=""))
    assert "name=" not in dropdown

    assert render_to_html(Input(value="")).startswith("<input")
    assert render_to_html(TextArea(value="")).startswith("<textarea")
    assert dropdown.startswith("<select")
    assert '<option value="a">a</option>' in dropdown


def test_style_rejects_non_hex_colours() -> None:
    """Why token references live in ``Rule.declarations``, not in ``Style``."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="invalid hex color"):
        Style(color="var(--t-color-primary)")


def test_style_to_css_emits_whole_alpha_without_decimal() -> None:
    """``ThemeTokens`` matches this spelling so token and inline agree."""
    assert style_to_css(Style(color="#584785").model_dump()) == (
        "color: rgba(88, 71, 133, 1)"
    )
