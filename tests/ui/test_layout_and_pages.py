"""Tests for the layout containers and the page base."""

from __future__ import annotations

import pytest
from tempest_core import Text, Widget
from tempestweb.html import render_to_html

from tempest_fastapi_sdk.ui.components import ComponentClasses, NavBar, NavItem
from tempest_fastapi_sdk.ui.layout import Grid, Shell
from tempest_fastapi_sdk.ui.pages import Page


class SimplePage(Page):
    """A page with one typed field."""

    name: str

    def body(self) -> Widget:
        return Text(content=f"Olá {self.name}", tag="h1")


class ShellPage(SimplePage):
    """A page inheriting shared chrome."""

    def shell(self, body: Widget) -> Widget:
        return Shell(
            children=[body],
            header=NavBar(items=[NavItem(label="Início", href="/")]),
            footer=Text(content="rodapé", tag="small"),
        )


class NoBodyPage(Page):
    """A page that never implements body()."""


def test_shell_renders_landmarks() -> None:
    html = render_to_html(
        Shell(children=[Text(content="conteúdo")], header=Text(content="topo")),
    )
    assert '<div class="tui-shell">' in html
    assert '<header class="tui-shell__header"><span>topo</span></header>' in html
    assert '<main class="tui-shell__main"><span>conteúdo</span></main>' in html
    assert "<footer" not in html


def test_shell_class_names_are_configurable() -> None:
    html = render_to_html(
        Shell(
            children=[],
            classes=ComponentClasses(shell="app", shell_main="app__main"),
        ),
    )
    assert 'class="app"' in html
    assert 'class="app__main"' in html


def test_grid_auto_fits_by_default() -> None:
    html = render_to_html(Grid(children=[Text(content="a")]))
    assert "display: grid" in html
    assert "repeat(auto-fit, minmax(16rem, 1fr))" in html
    assert "gap: 1rem" in html


def test_grid_fixed_column_count() -> None:
    assert Grid(columns=3).template() == "repeat(3, 1fr)"
    assert "repeat(3, 1fr)" in render_to_html(Grid(columns=3, gap="2rem"))


def test_page_composes_shell_around_body() -> None:
    html = render_to_html(ShellPage(title="T", name="Ana"))
    assert html.index("tui-shell__header") < html.index("Olá Ana")
    assert "rodapé" in html


def test_page_without_a_shell_renders_the_body_alone() -> None:
    assert render_to_html(SimplePage(title="T", name="Ana")) == "<h1>Olá Ana</h1>"


def test_page_fields_are_typed_and_required() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SimplePage(title="T")


def test_page_without_body_fails_loudly() -> None:
    with pytest.raises(NotImplementedError, match="must implement body"):
        NoBodyPage(title="T").render()


def test_ssr_page_is_the_same_class() -> None:
    """The pre-0.224 import path keeps working."""
    from tempest_fastapi_sdk.ssr import Page as SsrPage

    assert SsrPage is Page
