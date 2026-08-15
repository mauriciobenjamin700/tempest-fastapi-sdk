"""One call for the whole default look: tokens, reset, forms, components.

:func:`app_stylesheet` composes the three sheets a service normally
wants — the design tokens, the form rules and the component rules — into
a single :class:`~tempest_fastapi_sdk.ui.css.StyleSheet`, so getting a
styled UI is one router include rather than an assembly step every
project repeats.

Add your own rules by merging: the application sheet comes last, so it
wins on equal specificity.
"""

from __future__ import annotations

from tempest_fastapi_sdk.ui.components.classes import ComponentClasses
from tempest_fastapi_sdk.ui.components.styles import component_stylesheet
from tempest_fastapi_sdk.ui.css.rules import StyleSheet
from tempest_fastapi_sdk.ui.css.tokens import ThemeTokens
from tempest_fastapi_sdk.ui.forms.spec import FormClasses
from tempest_fastapi_sdk.ui.forms.styles import form_stylesheet


def app_stylesheet(
    *,
    theme: ThemeTokens | None = None,
    classes: ComponentClasses | None = None,
    form_classes: FormClasses | None = None,
    reset: bool = True,
    extra: StyleSheet | None = None,
) -> StyleSheet:
    """Build the default application stylesheet.

    Args:
        theme (ThemeTokens | None): The design tokens emitted as custom
            properties and referenced by every rule. Defaults to
            ``tempest_core``'s token set.
        classes (ComponentClasses | None): Class names the component
            rules target.
        form_classes (FormClasses | None): Class names the form rules
            target.
        reset (bool): Whether to include the minimal base reset.
        extra (StyleSheet | None): Your own rules, merged last so they
            win on equal specificity.

    Returns:
        StyleSheet: Tokens, reset, form rules, component rules and your
        own, in that order.

    Example:
        ```python
        from fastapi import FastAPI

        from tempest_fastapi_sdk.ui import app_stylesheet
        from tempest_fastapi_sdk.ui.css import make_css_router

        app: FastAPI = FastAPI()
        app.include_router(make_css_router(app_stylesheet()))
        ```
    """
    tokens = theme or ThemeTokens()
    sheet = StyleSheet(theme=tokens, reset=reset)
    sheet = sheet.merge(form_stylesheet(classes=form_classes, theme=tokens))
    sheet = sheet.merge(component_stylesheet(classes=classes, theme=tokens))
    if extra is not None:
        sheet = sheet.merge(extra)
    return sheet


__all__: list[str] = ["app_stylesheet"]
