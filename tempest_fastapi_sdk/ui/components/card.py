"""Card: a titled surface holding arbitrary content."""

from __future__ import annotations

from pydantic import Field

from tempest_fastapi_sdk.ui._core import Component, Stack, Text, Widget
from tempest_fastapi_sdk.ui.components.classes import (
    DEFAULT_CLASSES,
    ComponentClasses,
)


class Card(Component):
    """A titled block of content.

    Renders a ``<section>`` with an optional heading, the body content
    and an optional footer.

    Attributes:
        title (str): The heading text. Empty renders no heading.
        children (list[Widget]): The body content.
        footer (Widget | None): Optional footer content.
        heading_tag (str): Which heading element to use — pick the one
            that fits the page outline (``h2`` inside a page with an
            ``h1``, ``h3`` inside a section).
        classes (ComponentClasses): Class names to apply.

    Example:
        ```python
        from tempest_core import Text

        from tempest_fastapi_sdk.ui.components import Card

        card = Card(title="Vendas", children=[Text(content="R$ 12.400")])
        ```
    """

    title: str = ""
    children: list[Widget] = Field(default_factory=list)
    footer: Widget | None = None
    heading_tag: str = "h3"
    classes: ComponentClasses = DEFAULT_CLASSES

    def render(self) -> Widget:
        """Compose the card.

        Returns:
            Widget: A ``<section>`` holding the heading, body and
            footer that were provided.
        """
        parts: list[Widget] = []
        if self.title:
            parts.append(
                Text(
                    content=self.title,
                    tag=self.heading_tag,
                    attrs={"class": self.classes.card_title},
                ),
            )
        parts.append(
            Stack(
                tag="div",
                attrs={"class": self.classes.card_body},
                children=list(self.children),
            ),
        )
        if self.footer is not None:
            parts.append(
                Stack(
                    tag="div",
                    attrs={"class": self.classes.card_footer},
                    children=[self.footer],
                ),
            )
        return Stack(
            tag="section",
            attrs={"class": self.classes.card},
            children=parts,
        )


__all__: list[str] = ["Card"]
