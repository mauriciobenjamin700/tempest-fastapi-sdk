"""Typed CSS: stylesheets, design tokens and the route that serves them.

``tempest_core``'s ``Style`` covers a single element, inline. This package
covers what only a stylesheet can express — class selectors,
pseudo-classes, media queries, design tokens — while staying typed Python:

* :class:`Rule` — one selector plus declarations, taken from a typed
  ``Style`` and/or a raw mapping.
* :class:`Media` — a rule group behind an at-rule, with
  :meth:`Media.min_width`, :meth:`Media.dark` and friends.
* :class:`ThemeTokens` — ``tempest_core``'s token set (palette, spacing,
  shape, typography, motion) emitted as CSS custom properties, light and
  dark.
* :class:`StyleSheet` — the whole sheet, with :meth:`StyleSheet.cls` to
  reference a class name and have a typo raise instead of silently
  rendering an unstyled element.
* :func:`make_css_router` — serve it from the app, with ETag and
  ``304`` support.

Example:
    ```python
    from fastapi import FastAPI

    from tempest_fastapi_sdk.ui.css import (
        Media,
        Rule,
        StyleSheet,
        ThemeTokens,
        make_css_router,
    )

    theme: ThemeTokens = ThemeTokens()
    sheet: StyleSheet = StyleSheet(
        theme=theme,
        rules=[
            Rule(
                ".card",
                declarations={
                    "padding": theme.space("md"),
                    "border-radius": theme.radius("md"),
                    "background": theme.color("surface"),
                },
            ),
            Media.min_width(768, [Rule(".card", declarations={"padding": "24px"})]),
        ],
    )

    app: FastAPI = FastAPI()
    app.include_router(make_css_router(sheet))
    ```
"""

from tempest_fastapi_sdk.ui.css.router import css_response as css_response
from tempest_fastapi_sdk.ui.css.router import make_css_router as make_css_router
from tempest_fastapi_sdk.ui.css.router import stylesheet_links as stylesheet_links
from tempest_fastapi_sdk.ui.css.rules import Layout as Layout
from tempest_fastapi_sdk.ui.css.rules import Media as Media
from tempest_fastapi_sdk.ui.css.rules import Rule as Rule
from tempest_fastapi_sdk.ui.css.rules import StyleSheet as StyleSheet
from tempest_fastapi_sdk.ui.css.rules import cls as cls
from tempest_fastapi_sdk.ui.css.tokens import ThemeTokens as ThemeTokens

__all__: list[str] = [
    "Layout",
    "Media",
    "Rule",
    "StyleSheet",
    "ThemeTokens",
    "cls",
    "css_response",
    "make_css_router",
    "stylesheet_links",
]
