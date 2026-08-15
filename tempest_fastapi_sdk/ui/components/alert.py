"""Alert: a short, colour-coded message block."""

from __future__ import annotations

from typing import Literal

from tempest_fastapi_sdk.ui._core import Component, Stack, Text, Widget
from tempest_fastapi_sdk.ui.components.classes import (
    DEFAULT_CLASSES,
    ComponentClasses,
)

AlertVariant = Literal["info", "success", "warning", "error"]
"""Severity of an :class:`Alert`, mapped to a colour role."""


class Alert(Component):
    """A short message, coloured by severity.

    ``warning`` and ``error`` render with ``role="alert"`` so assistive
    technology announces them immediately; the quieter variants use
    ``role="status"``.

    Attributes:
        message (str): The message text.
        variant (AlertVariant): The severity.
        title (str): Optional heading above the message.
        classes (ComponentClasses): Class names to apply.

    Example:
        ```python
        from tempest_fastapi_sdk.ui.components import Alert

        alert = Alert(message="Conta criada com sucesso.", variant="success")
        ```
    """

    message: str
    variant: AlertVariant = "info"
    title: str = ""
    classes: ComponentClasses = DEFAULT_CLASSES

    def render(self) -> Widget:
        """Compose the alert.

        Returns:
            Widget: A ``<div>`` carrying the variant modifier class and
            the ARIA role matching its severity.
        """
        children: list[Widget] = []
        if self.title:
            children.append(Text(content=self.title, tag="strong"))
        children.append(Text(content=self.message, tag="p"))
        role = "alert" if self.variant in ("warning", "error") else "status"
        return Stack(
            tag="div",
            attrs={
                "class": (
                    f"{self.classes.alert} {self.classes.alert_variant}{self.variant}"
                ),
                "role": role,
            },
            children=children,
        )


__all__: list[str] = ["Alert", "AlertVariant"]
