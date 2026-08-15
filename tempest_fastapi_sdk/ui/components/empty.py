"""EmptyState: what a listing shows when there is nothing to list."""

from __future__ import annotations

from tempest_fastapi_sdk.ui._core import Component, Stack, Text, Widget
from tempest_fastapi_sdk.ui.components.classes import (
    DEFAULT_CLASSES,
    ComponentClasses,
)


class EmptyState(Component):
    """A titled placeholder for an empty collection.

    Collections return ``[]`` rather than a 404 (the SDK's convention),
    so "nothing here" is a normal, successful state and deserves a
    normal, informative screen.

    Attributes:
        title (str): The heading.
        description (str): Supporting text under the heading.
        action (Widget | None): Optional call to action — a link or a
            button leading to the screen that creates the first record.
        classes (ComponentClasses): Class names to apply.

    Example:
        ```python
        from tempest_fastapi_sdk.ui.components import EmptyState

        state = EmptyState(
            title="Nenhum pedido ainda",
            description="Os pedidos aparecem aqui assim que o primeiro entrar.",
        )
        ```
    """

    title: str
    description: str = ""
    action: Widget | None = None
    classes: ComponentClasses = DEFAULT_CLASSES

    def render(self) -> Widget:
        """Compose the empty state.

        Returns:
            Widget: A ``<div>`` with the heading, the description that
            was given, and the action when present.
        """
        children: list[Widget] = [
            Text(
                content=self.title,
                tag="h2",
                attrs={"class": self.classes.empty_state_title},
            ),
        ]
        if self.description:
            children.append(
                Text(
                    content=self.description,
                    tag="p",
                    attrs={"class": self.classes.empty_state_text},
                ),
            )
        if self.action is not None:
            children.append(self.action)
        return Stack(
            tag="div",
            attrs={"class": self.classes.empty_state},
            children=children,
        )


__all__: list[str] = ["EmptyState"]
