"""FlashMessages: the one-shot notices a redirect carries to the next screen."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from tempest_fastapi_sdk.ui._core import Component, Stack, Widget
from tempest_fastapi_sdk.ui.components.alert import Alert, AlertVariant
from tempest_fastapi_sdk.ui.components.classes import (
    DEFAULT_CLASSES,
    ComponentClasses,
)


class FlashMessage(BaseModel):
    """One notice queued for the next screen the user sees.

    Written by :func:`tempest_fastapi_sdk.ssr.flash` and read back by
    :func:`tempest_fastapi_sdk.ssr.get_flashes`, which travel it in a
    signed cookie — the text is always the server's, never read from the
    URL.

    Attributes:
        message (str): The notice text, rendered escaped.
        variant (AlertVariant): Its severity, which picks the colour and
            the ARIA role of the rendered :class:`Alert`.
    """

    model_config = ConfigDict(frozen=True)

    message: str
    variant: AlertVariant = "info"


class FlashMessages(Component):
    """The list of pending flash messages, one :class:`Alert` each.

    Place it once in the base page's shell and feed it what
    :func:`tempest_fastapi_sdk.ssr.get_flashes` returned for the request.
    With no messages it renders an empty wrapper, which the bundled
    stylesheet hides.

    Attributes:
        messages (list[FlashMessage]): The messages to show, in order.
        classes (ComponentClasses): Class names to apply.

    Example:
        ```python
        from tempest_fastapi_sdk.ui.components import FlashMessage, FlashMessages

        notices = FlashMessages(
            messages=[FlashMessage(message="Bucket criado.", variant="success")],
        )
        ```
    """

    messages: list[FlashMessage] = Field(default_factory=list)
    classes: ComponentClasses = DEFAULT_CLASSES

    def render(self) -> Widget:
        """Compose the messages.

        Returns:
            Widget: A ``<div>`` holding one :class:`Alert` per message.
        """
        return Stack(
            tag="div",
            attrs={"class": self.classes.flash},
            children=[
                Alert(
                    message=item.message,
                    variant=item.variant,
                    classes=self.classes,
                )
                for item in self.messages
            ],
        )


__all__: list[str] = ["FlashMessage", "FlashMessages"]
