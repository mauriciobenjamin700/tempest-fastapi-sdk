"""NavBar: the primary navigation of a server-rendered application."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import Field

from tempest_fastapi_sdk.ui._core import Component, Stack, Text, Widget
from tempest_fastapi_sdk.ui.components.classes import (
    DEFAULT_CLASSES,
    ComponentClasses,
)


@dataclass(frozen=True, slots=True)
class NavItem:
    """One entry of a :class:`NavBar`.

    Attributes:
        label (str): The link text.
        href (str): The link target.
    """

    label: str
    href: str


class NavBar(Component):
    """A list of links, with the current one marked.

    The active link renders ``aria-current="page"`` so assistive
    technology reports the location, not just the styling.

    Attributes:
        items (list[NavItem]): The entries, in order.
        active_href (str): The ``href`` of the current page.
        label (str): The ``aria-label`` of the ``<nav>`` element.
        classes (ComponentClasses): Class names to apply.

    Example:
        ```python
        from tempest_fastapi_sdk.ui.components import NavBar, NavItem

        nav = NavBar(
            items=[
                NavItem(label="Início", href="/"),
                NavItem(label="Usuários", href="/users"),
            ],
            active_href="/users",
        )
        ```
    """

    items: list[NavItem] = Field(default_factory=list)
    active_href: str = ""
    label: str = "Navegação principal"
    classes: ComponentClasses = DEFAULT_CLASSES

    def render(self) -> Widget:
        """Compose the navigation bar.

        Returns:
            Widget: A ``<nav>`` holding one ``<a>`` per entry.
        """
        links: list[Widget] = []
        for item in self.items:
            active = item.href == self.active_href
            attrs = {
                "href": item.href,
                "class": (
                    f"{self.classes.nav_link} {self.classes.nav_active}"
                    if active
                    else self.classes.nav_link
                ),
            }
            if active:
                attrs["aria-current"] = "page"
            links.append(Text(content=item.label, tag="a", attrs=attrs))
        return Stack(
            tag="nav",
            attrs={"class": self.classes.nav, "aria-label": self.label},
            children=links,
        )


__all__: list[str] = ["NavBar", "NavItem"]
