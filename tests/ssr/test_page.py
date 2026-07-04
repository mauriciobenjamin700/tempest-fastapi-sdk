"""Unit tests for the typed Page component base."""

from __future__ import annotations

import pytest
from tempest_core import Column, Text, Widget

from tempest_fastapi_sdk.ssr import Page


class SimplePage(Page):
    """A minimal page carrying one typed field."""

    name: str

    def body(self) -> Widget:
        return Column(children=[Text(content=f"Hello {self.name}")])


class ShellPage(Page):
    """A page that wraps its body with a shell layout."""

    name: str

    def body(self) -> Widget:
        return Text(content=f"body-{self.name}")

    def shell(self, body: Widget) -> Widget:
        return Column(children=[Text(content="chrome"), body])


class NoBodyPage(Page):
    """A page that never implements body()."""


def test_page_is_a_widget() -> None:
    page = SimplePage(title="T", name="Ana")
    assert isinstance(page, Widget)
    assert page.title == "T"
    assert page.name == "Ana"


def test_body_renders() -> None:
    page = SimplePage(title="T", name="Ana")
    tree = page.body()
    assert isinstance(tree, Column)
    assert isinstance(tree.children[0], Text)
    assert tree.children[0].content == "Hello Ana"


def test_default_shell_returns_body_unchanged() -> None:
    page = SimplePage(title="T", name="Ana")
    body = page.body()
    assert page.shell(body) is body


def test_render_composes_shell_over_body() -> None:
    page = ShellPage(title="T", name="Bob")
    rendered = page.render()
    assert isinstance(rendered, Column)
    # shell wraps: [chrome, body]
    assert isinstance(rendered.children[0], Text)
    assert rendered.children[0].content == "chrome"
    assert isinstance(rendered.children[1], Text)
    assert rendered.children[1].content == "body-Bob"


def test_render_calls_shell_of_body() -> None:
    """render() must equal shell(body()) for a plain page too."""
    page = SimplePage(title="T", name="Zoe")
    rendered = page.render()
    body = page.body()
    # default shell is identity, so render mirrors body structure
    assert isinstance(rendered, Column)
    assert rendered.children[0].content == body.children[0].content


def test_body_not_implemented_raises() -> None:
    page = NoBodyPage(title="T")
    with pytest.raises(NotImplementedError):
        page.body()
