"""A ready stylesheet for generated forms, written with the typed CSS API.

:func:`form_stylesheet` returns the rules that style the classes in
:class:`~tempest_fastapi_sdk.ui.forms.FormClasses`, expressed entirely
through design tokens — so a form picks up the service's palette, spacing
and radii, in light and dark, with no CSS file to maintain.

Merge it into the application sheet:

```python
from tempest_fastapi_sdk.ui.css import StyleSheet, ThemeTokens
from tempest_fastapi_sdk.ui.forms import form_stylesheet

theme: ThemeTokens = ThemeTokens()
sheet: StyleSheet = StyleSheet(theme=theme).merge(form_stylesheet(theme=theme))
```
"""

from __future__ import annotations

from tempest_fastapi_sdk.ui.css.rules import Rule, StyleSheet
from tempest_fastapi_sdk.ui.css.tokens import ThemeTokens
from tempest_fastapi_sdk.ui.forms.spec import FormClasses


def form_stylesheet(
    *,
    classes: FormClasses | None = None,
    theme: ThemeTokens | None = None,
) -> StyleSheet:
    """Build the stylesheet that styles a generated form.

    Args:
        classes (FormClasses | None): The class names to target.
            Defaults to the same names
            :func:`~tempest_fastapi_sdk.ui.forms.form_for` applies.
        theme (ThemeTokens | None): The tokens whose custom properties
            the rules reference. Only the prefix matters here — pass the
            same theme your application sheet emits, or leave it at the
            default ``--t-`` prefix.

    Returns:
        StyleSheet: The form rules, with no reset and no token block of
        their own, ready to :meth:`~StyleSheet.merge` into the
        application sheet.
    """
    names = classes or FormClasses()
    tokens = theme or ThemeTokens()

    return StyleSheet(
        reset=False,
        rules=[
            Rule(
                f".{names.form}",
                declarations={
                    "display": "flex",
                    "flex-direction": "column",
                    "gap": tokens.space("md"),
                    "max-width": "32rem",
                },
            ),
            Rule(
                f".{names.errors}",
                declarations={
                    "padding": tokens.space("sm"),
                    "border-radius": tokens.radius("sm"),
                    "background": tokens.color("error_container"),
                    "color": tokens.color("on_error_container"),
                },
            ),
            Rule(
                f".{names.errors} p",
                declarations={"margin": "0"},
            ),
            Rule(
                f".{names.field}",
                declarations={
                    "display": "flex",
                    "flex-direction": "column",
                    "gap": tokens.space("xs"),
                },
            ),
            Rule(
                f".{names.label}",
                declarations={
                    "display": "flex",
                    "gap": "0.25rem",
                    "font-size": tokens.font_size("label_large"),
                    "font-weight": "600",
                    "color": tokens.color("on_surface"),
                },
            ),
            Rule(
                f".{names.required_mark}",
                declarations={"color": tokens.color("error")},
            ),
            Rule(
                f".{names.control}",
                declarations={
                    "padding": "0.625rem 0.75rem",
                    "border": f"1px solid {tokens.color('outline')}",
                    "border-radius": tokens.radius("sm"),
                    "background": tokens.color("surface"),
                    "color": tokens.color("on_surface"),
                    "font-size": tokens.font_size("body_medium"),
                    "width": "100%",
                },
            ),
            Rule(
                f".{names.control}:focus-visible",
                declarations={
                    "outline": f"2px solid {tokens.color('primary')}",
                    "outline-offset": "1px",
                },
            ),
            Rule(
                f'.{names.control}[type="checkbox"]',
                declarations={"width": "auto", "align-self": "flex-start"},
            ),
            Rule(
                f".{names.field_invalid} .{names.control}",
                declarations={"border-color": tokens.color("error")},
            ),
            Rule(
                f".{names.help_text}",
                declarations={
                    "color": tokens.color("on_surface_variant"),
                    "font-size": tokens.font_size("body_small"),
                },
            ),
            Rule(
                f".{names.error}",
                declarations={
                    "margin": "0",
                    "color": tokens.color("error"),
                    "font-size": tokens.font_size("body_small"),
                },
            ),
            Rule(
                f".{names.actions}",
                declarations={
                    "display": "flex",
                    "gap": tokens.space("sm"),
                    "justify-content": "flex-end",
                },
            ),
            Rule(
                f".{names.submit}",
                declarations={
                    "padding": "0.625rem 1.25rem",
                    "border": "none",
                    "border-radius": tokens.radius("full"),
                    "background": tokens.color("primary"),
                    "color": tokens.color("on_primary"),
                    "font-size": tokens.font_size("label_large"),
                    "font-weight": "600",
                    "cursor": "pointer",
                },
            ),
            Rule(
                f".{names.submit}:disabled",
                declarations={"opacity": "0.6", "cursor": "not-allowed"},
            ),
        ],
    )


__all__: list[str] = ["form_stylesheet"]
