"""Shell: the chrome every page shares — header, main region, footer."""

from __future__ import annotations

from pydantic import Field

from tempest_fastapi_sdk.ui._core import Component, Stack, Widget
from tempest_fastapi_sdk.ui.components.classes import (
    DEFAULT_CLASSES,
    ComponentClasses,
)


class Shell(Component):
    """The shared page chrome.

    Wraps the page content in ``<header>`` / ``<main>`` / ``<footer>``
    landmarks. Use it from a base page's
    :meth:`~tempest_fastapi_sdk.ui.pages.Page.shell` so every concrete
    page inherits the same frame through plain Python inheritance.

    Attributes:
        children (list[Widget]): The page content, placed in ``<main>``.
        header (Widget | None): Content of the ``<header>`` landmark —
            usually a :class:`~tempest_fastapi_sdk.ui.components.NavBar`.
        footer (Widget | None): Content of the ``<footer>`` landmark.
        classes (ComponentClasses): Class names to apply.

    Example:
        ```python
        from tempest_core import Text, Widget

        from tempest_fastapi_sdk.ui.components import NavBar, NavItem
        from tempest_fastapi_sdk.ui.layout import Shell
        from tempest_fastapi_sdk.ui.pages import Page


        class BasePage(Page):
            def shell(self, body: Widget) -> Widget:
                return Shell(
                    children=[body],
                    header=NavBar(items=[NavItem(label="Início", href="/")]),
                    footer=Text(content="© Tempest", tag="small"),
                )
        ```
    """

    children: list[Widget] = Field(default_factory=list)
    header: Widget | None = None
    footer: Widget | None = None
    classes: ComponentClasses = DEFAULT_CLASSES

    def render(self) -> Widget:
        """Compose the shell.

        Returns:
            Widget: A ``<div>`` holding the landmarks that were given,
            with the content always inside ``<main>``.
        """
        parts: list[Widget] = []
        if self.header is not None:
            parts.append(
                Stack(
                    tag="header",
                    attrs={"class": self.classes.shell_header},
                    children=[self.header],
                ),
            )
        parts.append(
            Stack(
                tag="main",
                attrs={"class": self.classes.shell_main},
                children=list(self.children),
            ),
        )
        if self.footer is not None:
            parts.append(
                Stack(
                    tag="footer",
                    attrs={"class": self.classes.shell_footer},
                    children=[self.footer],
                ),
            )
        return Stack(
            tag="div",
            attrs={"class": self.classes.shell},
            children=parts,
        )


__all__: list[str] = ["Shell"]
