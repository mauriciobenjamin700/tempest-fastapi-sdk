"""The palette a Mode B app hands to every session.

Colour reaches a tempestweb screen by two independent paths, and this is
the one a stylesheet cannot cover: components resolve their colours in
**Python**, so a filled button carries its fill as an inline style,
resolved against whatever theme the ``App`` was built with. A page that
rebranded only its CSS custom properties kept rendering baseline-purple
buttons over a rebranded background.

These pin the forwarding — not the rendering, which belongs to tempestweb:
what this package owns is passing the theme through ``build_web_app``
without swallowing it.
"""

from __future__ import annotations

import inspect

from tempest_core import Theme, ThemeMode
from tempest_core.style import Color
from tempestweb.server import create_app

from tempest_fastapi_sdk.ssr import build_web_app


def test_build_web_app_offers_a_theme() -> None:
    """An app with a brand needs somewhere to declare it."""
    assert "theme" in inspect.signature(build_web_app).parameters


def test_the_theme_reaches_the_server_that_builds_the_sessions() -> None:
    """The parameter is worth nothing if it stops here.

    ``create_app`` is what hands the theme to each ``AppSession``; a
    passthrough that named the argument and dropped it would look right in
    the signature and paint nothing on screen.
    """
    assert "theme" in inspect.signature(create_app).parameters


def test_a_seeded_theme_is_a_real_palette() -> None:
    """The value being forwarded carries both schemes, not one colour."""
    theme = Theme.from_seed(Color(r=39, g=58, b=79), mode=ThemeMode.SYSTEM)

    assert theme.tokens.schemes.light.primary != theme.tokens.schemes.dark.primary
    assert theme.mode is ThemeMode.SYSTEM
