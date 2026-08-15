"""Grid: a responsive CSS grid container.

``Column`` and ``Row`` cover flex layout, which the renderer emits
inline. A grid needs ``grid-template-columns``, which ``Style`` does not
model — so this component writes the two grid declarations directly into
the element's ``style`` attribute rather than pretending a flex container
is a grid.
"""

from __future__ import annotations

from pydantic import Field

from tempest_fastapi_sdk.ui._core import Component, Stack, Widget
from tempest_fastapi_sdk.ui.components.classes import (
    DEFAULT_CLASSES,
    ComponentClasses,
)


class Grid(Component):
    """A CSS grid of equal-width, wrapping columns.

    The default ``min_column_width`` makes the grid responsive with no
    media query: columns fit as many as the width allows and reflow to
    one per row on a phone.

    Attributes:
        children (list[Widget]): The grid items.
        columns (int): Fixed column count. ``0`` (the default) uses the
            auto-fitting track based on :attr:`min_column_width`.
        min_column_width (str): Minimum width of an auto-fitted column,
            as a CSS length.
        gap (str): Gap between items, as a CSS length.
        classes (ComponentClasses): Class names to apply.

    Example:
        ```python
        from tempest_core import Text

        from tempest_fastapi_sdk.ui.layout import Grid

        grid = Grid(children=[Text(content="a"), Text(content="b")], gap="1rem")
        ```
    """

    children: list[Widget] = Field(default_factory=list)
    columns: int = 0
    min_column_width: str = "16rem"
    gap: str = "1rem"
    classes: ComponentClasses = DEFAULT_CLASSES

    def template(self) -> str:
        """Return the ``grid-template-columns`` value.

        Returns:
            str: A fixed ``repeat(n, 1fr)`` when :attr:`columns` is set,
            otherwise an auto-fitting track.
        """
        if self.columns > 0:
            return f"repeat({self.columns}, 1fr)"
        return f"repeat(auto-fit, minmax({self.min_column_width}, 1fr))"

    def render(self) -> Widget:
        """Compose the grid.

        Returns:
            Widget: A ``<div>`` with ``display: grid`` and the computed
            track, holding the items unchanged.
        """
        return Stack(
            tag="div",
            attrs={
                "class": self.classes.grid,
                "style": (
                    f"display: grid; grid-template-columns: {self.template()}; "
                    f"gap: {self.gap}"
                ),
            },
            children=list(self.children),
        )


__all__: list[str] = ["Grid"]
