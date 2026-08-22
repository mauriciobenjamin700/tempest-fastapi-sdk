"""The palette a Mode B app hands to every session.

Colour reaches a tempestweb screen by two independent paths, and this is
the one a stylesheet cannot cover: a widget bakes its resolved colours
into an inline ``style``, so a page that rebranded only its CSS custom
properties keeps rendering baseline-purple buttons over a rebranded
background.

These pin the forwarding by observing it end to end — build a real
server artifact, open a real session, read the colour off the first
patch frame — because a passthrough that named the argument and dropped
it would look right in every signature and paint nothing on screen.

The view below passes ``app.theme`` into the widget on purpose: that is
the contract. A widget defaults to the baseline theme, so it follows the
session's palette only where the view hands it over. The
``tempestweb.components`` helpers (``filled_button`` and friends) do not
forward ``app.theme``, which is measured but deliberately left unpinned
here — it is upstream's behaviour to change, not this package's.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tempest_core import Theme, ThemeMode
from tempest_core.style import Color
from tempestweb.cli import build_artifact, scaffold_project

from tempest_fastapi_sdk.ssr import build_web_app

_APP: str = '''"""A view tinted by whatever theme the session carries."""

from __future__ import annotations

from tempest_core import App, Button, Widget
from tempest_core.variants import Variant


def make_state() -> object:
    """Build the initial state."""
    return object()


def view(app: App[object]) -> Widget:
    """Render a solid button resolving its fill against ``app.theme``."""
    return Button(
        label="Comprar",
        variant=Variant.SOLID,
        color_scheme="primary",
        theme=app.theme,
        key="buy",
    )
'''

SEED: Color = Color(r=255, g=0, b=0)


@pytest.fixture(scope="module")
def themed_build() -> Path:
    """Build a server artifact whose view reads the session's theme.

    Returns:
        Path: The ``dist/server`` directory of the built artifact.
    """
    parent = Path(tempfile.mkdtemp())
    scaffold_project("demo", parent=str(parent))
    root = parent / "demo"
    (root / "app.py").write_text(_APP)
    return Path(build_artifact(str(root), mode="server").out_dir)


def _first_fill(build: Path, theme: Theme | None) -> dict[str, float]:
    """Open one session and read the root widget's resolved fill.

    Args:
        build (Path): The server build directory to host.
        theme (Theme | None): The palette handed to ``build_web_app``.

    Returns:
        dict[str, float]: The inline ``background`` colour of the first
        patch frame's root node.
    """
    app = build_web_app(build, title="demo", theme=theme)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        frame = json.loads(ws.receive_text())
    fill: dict[str, float] = frame["data"][0]["node"]["props"]["style"]["background"]
    return fill


def test_the_theme_reaches_the_session(themed_build: Path) -> None:
    """The palette handed to the builder is the one the session paints."""
    theme = Theme.from_seed(SEED, mode=ThemeMode.LIGHT)
    primary = theme.tokens.schemes.light.primary

    fill = _first_fill(themed_build, theme)

    assert (fill["r"], fill["g"], fill["b"]) == (primary.r, primary.g, primary.b)


def test_without_a_theme_the_baseline_stands(themed_build: Path) -> None:
    """The assertion above can fail — no theme leaves the baseline fill.

    Without this the themed test would pass against any palette that
    happened to match the Material default.
    """
    theme = Theme.from_seed(SEED, mode=ThemeMode.LIGHT)
    primary = theme.tokens.schemes.light.primary

    fill = _first_fill(themed_build, None)

    assert (fill["r"], fill["g"], fill["b"]) != (primary.r, primary.g, primary.b)
