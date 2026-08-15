"""A ready stylesheet for the bundled components.

Same idea as :func:`~tempest_fastapi_sdk.ui.forms.form_stylesheet`: the
rules are written with the typed CSS API against design tokens, so the
components inherit the service's palette and spacing in both colour
schemes without a hand-maintained CSS file.
"""

from __future__ import annotations

from tempest_fastapi_sdk.ui.components.classes import (
    DEFAULT_CLASSES,
    ComponentClasses,
)
from tempest_fastapi_sdk.ui.css.rules import Rule, StyleSheet
from tempest_fastapi_sdk.ui.css.tokens import ThemeTokens

_ALERT_ROLES: dict[str, str] = {
    "info": "info",
    "success": "success",
    "warning": "warning",
    "error": "error",
}


def component_stylesheet(
    *,
    classes: ComponentClasses | None = None,
    theme: ThemeTokens | None = None,
) -> StyleSheet:
    """Build the stylesheet that styles the bundled components.

    Args:
        classes (ComponentClasses | None): The class names to target.
            Defaults to the set the components apply.
        theme (ThemeTokens | None): The tokens whose custom properties
            the rules reference. Pass the same theme the application
            sheet emits so the prefixes line up.

    Returns:
        StyleSheet: The component rules, with no reset and no token
        block of their own, ready to merge into the application sheet.
    """
    names = classes or DEFAULT_CLASSES
    tokens = theme or ThemeTokens()

    rules: list[Rule] = [
        Rule(
            f".{names.shell}",
            declarations={
                "display": "flex",
                "flex-direction": "column",
                "min-height": "100vh",
                "background": tokens.color("background"),
                "color": tokens.color("on_background"),
                "font-size": tokens.font_size("body_medium"),
            },
        ),
        Rule(
            f".{names.shell_main}",
            declarations={
                "flex": "1",
                "width": "100%",
                "max-width": "72rem",
                "margin": "0 auto",
                "padding": tokens.space("lg"),
                "display": "flex",
                "flex-direction": "column",
                "gap": tokens.space("lg"),
            },
        ),
        Rule(
            f".{names.shell_header}, .{names.shell_footer}",
            declarations={
                "padding": f"{tokens.space('sm')} {tokens.space('lg')}",
                "background": tokens.color("surface"),
                "color": tokens.color("on_surface"),
            },
        ),
        Rule(
            f".{names.grid}",
            declarations={"width": "100%"},
        ),
        Rule(
            f".{names.nav}",
            declarations={
                "display": "flex",
                "gap": tokens.space("md"),
                "flex-wrap": "wrap",
            },
        ),
        Rule(
            f".{names.nav_link}",
            declarations={
                "color": tokens.color("on_surface_variant"),
                "text-decoration": "none",
            },
        ),
        Rule(
            f".{names.nav_active}",
            declarations={
                "color": tokens.color("primary"),
                "font-weight": "600",
            },
        ),
        Rule(
            f".{names.card}",
            declarations={
                "display": "flex",
                "flex-direction": "column",
                "gap": tokens.space("sm"),
                "padding": tokens.space("md"),
                "border-radius": tokens.radius("md"),
                "background": tokens.color("surface"),
                "color": tokens.color("on_surface"),
                "border": f"1px solid {tokens.color('outline_variant')}",
            },
        ),
        Rule(
            f".{names.card_title}",
            declarations={
                "margin": "0",
                "font-size": tokens.font_size("title_medium"),
            },
        ),
        Rule(
            f".{names.card_body}",
            declarations={
                "display": "flex",
                "flex-direction": "column",
                "gap": tokens.space("sm"),
            },
        ),
        Rule(
            f".{names.card_footer}",
            declarations={
                "padding-top": tokens.space("sm"),
                "border-top": f"1px solid {tokens.color('outline_variant')}",
            },
        ),
        Rule(
            f".{names.alert}",
            declarations={
                "padding": tokens.space("sm"),
                "border-radius": tokens.radius("sm"),
            },
        ),
        Rule(f".{names.alert} p", declarations={"margin": "0"}),
        Rule(
            f".{names.table}",
            declarations={
                "width": "100%",
                "border-collapse": "collapse",
                "font-size": tokens.font_size("body_medium"),
            },
        ),
        Rule(
            f".{names.table} th, .{names.table} td",
            declarations={
                "padding": tokens.space("sm"),
                "text-align": "left",
                "border-bottom": f"1px solid {tokens.color('outline_variant')}",
            },
        ),
        Rule(
            f".{names.table} th",
            declarations={
                "color": tokens.color("on_surface_variant"),
                "font-size": tokens.font_size("label_large"),
            },
        ),
        Rule(
            f".{names.table_empty}",
            declarations={
                "text-align": "center",
                "color": tokens.color("on_surface_variant"),
            },
        ),
        Rule(
            f".{names.pagination}",
            declarations={
                "display": "flex",
                "gap": tokens.space("xs"),
                "flex-wrap": "wrap",
                "align-items": "center",
            },
        ),
        Rule(
            f".{names.pagination_link}",
            declarations={
                "padding": f"{tokens.space('xs')} {tokens.space('sm')}",
                "border-radius": tokens.radius("sm"),
                "color": tokens.color("on_surface"),
                "text-decoration": "none",
            },
        ),
        Rule(
            f".{names.pagination_current}",
            declarations={
                "background": tokens.color("primary"),
                "color": tokens.color("on_primary"),
            },
        ),
        Rule(
            f".{names.pagination_disabled}",
            declarations={
                "opacity": "0.5",
                "pointer-events": "none",
            },
        ),
        Rule(
            f".{names.empty_state}",
            declarations={
                "display": "flex",
                "flex-direction": "column",
                "align-items": "center",
                "gap": tokens.space("sm"),
                "padding": tokens.space("xl"),
                "text-align": "center",
                "color": tokens.color("on_surface_variant"),
            },
        ),
        Rule(
            f".{names.empty_state_title}",
            declarations={
                "margin": "0",
                "font-size": tokens.font_size("title_large"),
                "color": tokens.color("on_surface"),
            },
        ),
        Rule(f".{names.empty_state_text}", declarations={"margin": "0"}),
    ]

    rules.extend(
        Rule(
            f".{names.alert_variant}{variant}",
            declarations={
                "background": tokens.color(f"{role}_container"),
                "color": tokens.color(f"on_{role}_container"),
            },
        )
        for variant, role in _ALERT_ROLES.items()
    )

    return StyleSheet(reset=False, rules=rules)


__all__: list[str] = ["component_stylesheet"]
