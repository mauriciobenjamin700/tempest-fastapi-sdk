"""Add optional ``src`` layers to an existing project from its extras.

``tempest new`` scaffolds the always-present layers (api, controllers,
services, schemas, db, core, utils). The layers that only make sense
for a specific SDK extra — ``[queue]`` (FastStream), ``[tasks]``
(TaskIQ) and ``[ssr]`` (the ``ui`` layer: pages, layout, components and
the typed stylesheet) — are NOT part of the base skeleton: dropping
empty placeholder packages on every service contradicts the layout
rules.

``tempest generate --src`` reads the extras pinned in the project's
``pyproject.toml`` and writes only the matching layers. The operation
is idempotent: existing files are left untouched unless ``--force`` is
passed, so a hand-edited handler is never clobbered silently.
"""

from __future__ import annotations

from pathlib import Path

# Each ``__ROOT__`` placeholder is replaced with the detected source
# root package (``src`` or ``app``) so intra-project imports resolve.
_ROOT_PLACEHOLDER = "__ROOT__"


_QUEUE_INIT = '''\
"""FastStream (RabbitMQ) message-queue wiring for this service.

A single :class:`AsyncBrokerManager` owns the broker for the whole
process. Routers and services reach it through :func:`get_broker`;
connect/disconnect run from the app lifespan.
"""

from __future__ import annotations

import os

from faststream.rabbit import RabbitBroker
from tempest_fastapi_sdk import AsyncBrokerManager

RABBITMQ_URL: str = os.environ.get(
    "RABBITMQ_URL",
    "amqp://guest:guest@localhost:5672/",
)
"""AMQP URL the broker connects to (override via the ``.env`` file)."""

broker: RabbitBroker = RabbitBroker(RABBITMQ_URL)
"""Process-wide FastStream broker; subscribers register against it."""

broker_manager: AsyncBrokerManager = AsyncBrokerManager(broker)
"""SDK manager wrapping :data:`broker` (connect/disconnect/publish)."""


def get_broker() -> AsyncBrokerManager:
    """Return the process-wide FastStream broker manager.

    Returns:
        AsyncBrokerManager: The shared manager (connected during the
        app lifespan).
    """
    return broker_manager


__all__: list[str] = ["broker", "broker_manager", "get_broker"]
'''


_QUEUE_HANDLERS = '''\
"""FastStream subscribers and publishers for this service.

Handlers are declared against the shared :data:`broker` so they
register automatically once the broker starts. Import this module from
the app lifespan (or ``src.queue``) to make sure the decorators run.
"""

from __future__ import annotations

from __ROOT__.queue import broker


@broker.subscriber("example")
async def handle_example(message: str) -> None:
    """Consume one message from the ``example`` queue.

    Args:
        message (str): The decoded message payload.

    Returns:
        None: Side-effecting consumer; nothing is returned.
    """
    print(f"received: {message}")


__all__: list[str] = ["handle_example"]
'''


_TASKS_INIT = '''\
"""TaskIQ (RabbitMQ) background-task wiring for this service.

A single :class:`AsyncTaskBrokerManager` owns the broker for the whole
process. Request handlers enqueue jobs via ``.kiq(...)``; a separate
worker process consumes them. connect/disconnect run from the app
lifespan.
"""

from __future__ import annotations

import os

from taskiq_aio_pika import AioPikaBroker
from tempest_fastapi_sdk import AsyncTaskBrokerManager

TASKIQ_BROKER_URL: str = os.environ.get(
    "TASKIQ_BROKER_URL",
    "amqp://guest:guest@localhost:5672/",
)
"""AMQP URL the TaskIQ broker connects to (override via ``.env``)."""

broker: AioPikaBroker = AioPikaBroker(TASKIQ_BROKER_URL)
"""Process-wide TaskIQ broker; tasks register against it."""

task_manager: AsyncTaskBrokerManager = AsyncTaskBrokerManager(broker)
"""SDK manager wrapping :data:`broker` (connect/disconnect/task)."""


def get_task_manager() -> AsyncTaskBrokerManager:
    """Return the process-wide TaskIQ broker manager.

    Returns:
        AsyncTaskBrokerManager: The shared manager (connected during
        the app lifespan).
    """
    return task_manager


__all__: list[str] = ["broker", "get_task_manager", "task_manager"]
'''


_TASKS_JOBS = '''\
"""TaskIQ background jobs for this service.

Jobs are declared against the shared :data:`broker` so they register
on startup. Enqueue them from a request handler with
``await example_job.kiq("payload")``.
"""

from __future__ import annotations

from __ROOT__.tasks import broker


@broker.task
async def example_job(payload: str) -> str:
    """Process one background job.

    Args:
        payload (str): Arbitrary job input.

    Returns:
        str: A short result string echoing the processed payload.
    """
    return f"processed: {payload}"


__all__: list[str] = ["example_job"]
'''


_UI_INIT = '''\
"""The UI layer: what this service looks like, in typed Python.

``ui`` sits beside ``controllers`` / ``services`` / ``schemas`` and
answers one question — how the data is presented. It never opens a
database session, calls an external API or decides business rules: a
router loads data through a controller, hands it to a page, and the page
returns a widget tree.

Where things go:

* ``ui/pages/`` — one class per screen, subclassing :class:`BasePage`.
* ``ui/layout/`` — the shared chrome every page inherits.
* ``ui/components/`` — reusable pieces pages are built from.
* ``ui/styles.py`` — the typed stylesheet served to the browser.

Forms are not written by hand: generate them from the Pydantic schema
in ``schemas/`` with ``tempest_fastapi_sdk.ui.forms.form_for``, and read
the submission back with ``parse_form``.
"""

from __ROOT__.ui.layout import BasePage
from __ROOT__.ui.pages import HomePage
from __ROOT__.ui.styles import CSS_PATH, STYLESHEET

__all__: list[str] = ["CSS_PATH", "STYLESHEET", "BasePage", "HomePage"]
'''


_UI_STYLES = '''\
"""The stylesheet of this service, written in typed Python.

:data:`STYLESHEET` composes the SDK defaults (design tokens, form rules,
component rules) with this service's own rules. It is served from the app
itself at :data:`CSS_PATH` — no build step and no CDN.

Add a rule by appending to ``_OWN_RULES``; reference a design token
through ``THEME`` so light and dark stay consistent.
"""

from __future__ import annotations

from tempest_fastapi_sdk.ui import app_stylesheet
from tempest_fastapi_sdk.ui.css import Media, Rule, StyleSheet, ThemeTokens

CSS_PATH: str = "/static/app.css"
"""Route the stylesheet is served from, and linked from every page."""

THEME: ThemeTokens = ThemeTokens()
"""Design tokens emitted as CSS custom properties (light and dark)."""

_OWN_RULES: list[Rule | Media] = [
    Rule(
        ".stat",
        declarations={
            "display": "flex",
            "flex-direction": "column",
            "gap": THEME.space("none"),
        },
    ),
    Rule(
        ".stat strong",
        declarations={
            "font-size": THEME.font_size("headline_medium"),
            "color": THEME.color("primary"),
        },
    ),
    Rule(
        ".stat small",
        declarations={"color": THEME.color("on_surface_variant")},
    ),
    Rule(
        ".page-title",
        declarations={
            "margin": "0",
            "font-size": THEME.font_size("headline_small"),
            "color": THEME.color("on_background"),
        },
    ),
    Media.min_width(
        THEME.breakpoint("lg"),
        [
            Rule(
                ".page-title",
                declarations={"font-size": THEME.font_size("headline_large")},
            ),
        ],
    ),
]
"""Rules specific to this service, applied after the SDK defaults."""

STYLESHEET: StyleSheet = app_stylesheet(
    theme=THEME,
    extra=StyleSheet(rules=_OWN_RULES, reset=False),
)
"""The whole sheet: tokens, reset, SDK rules and the rules above."""


__all__: list[str] = ["CSS_PATH", "STYLESHEET", "THEME"]
'''


_UI_LAYOUT_INIT = '''\
"""Layout: the chrome every page of this service inherits."""

from __ROOT__.ui.layout.base import BasePage

__all__: list[str] = ["BasePage"]
'''


_UI_LAYOUT_BASE = '''\
"""The base page: shared header, navigation and footer.

Every screen subclasses :class:`BasePage` and implements ``body()``.
The chrome is inherited through plain Python inheritance — change it
here and every page follows.
"""

from __future__ import annotations

from tempest_core import Text, Widget
from tempest_fastapi_sdk.ui.components import NavBar, NavItem
from tempest_fastapi_sdk.ui.layout import Shell
from tempest_fastapi_sdk.ui.pages import Page

NAV_ITEMS: list[NavItem] = [NavItem(label="Início", href="/")]
"""Entries of the main navigation, in display order."""


class BasePage(Page):
    """Page base carrying this service's chrome.

    Attributes:
        active_href (str): ``href`` of the current screen, so the
            navigation marks the right entry.
    """

    active_href: str = "/"

    def shell(self, body: Widget) -> Widget:
        """Wrap a page body in the shared chrome.

        Args:
            body (Widget): The widget tree returned by ``body()``.

        Returns:
            Widget: The page framed by header, main and footer.
        """
        return Shell(
            children=[body],
            header=NavBar(items=NAV_ITEMS, active_href=self.active_href),
            footer=Text(content="Tempest", tag="small"),
        )


__all__: list[str] = ["NAV_ITEMS", "BasePage"]
'''


_UI_COMPONENTS_INIT = '''\
"""Components: the reusable pieces this service's pages are built from.

Keep them free of data access — a component receives what it renders.
The SDK ships the common ones (``Card``, ``Alert``, ``DataTable``,
``Pagination``, ``EmptyState``, ``NavBar``); add here only what is
specific to this service.
"""

from __ROOT__.ui.components.stat import Stat

__all__: list[str] = ["Stat"]
'''


_UI_COMPONENTS_STAT = '''\
"""A single headline number with its label."""

from __future__ import annotations

from tempest_core import Text, Widget
from tempest_core.widgets import Component, Stack


class Stat(Component):
    """One metric: a big value over a small label.

    Attributes:
        label (str): What the number measures.
        value (str): The number, already formatted for display.
    """

    label: str
    value: str

    def render(self) -> Widget:
        """Compose the metric.

        Returns:
            Widget: A ``<div>`` holding the value and its label.
        """
        return Stack(
            tag="div",
            attrs={"class": "stat"},
            children=[
                Text(content=self.value, tag="strong"),
                Text(content=self.label, tag="small"),
            ],
        )


__all__: list[str] = ["Stat"]
'''


_UI_PAGES_INIT = '''\
"""Pages: one class per screen.

A page declares the data it needs as typed fields and builds its content
in ``body()``. Routers construct it with data a controller already
loaded — pages never query anything themselves.
"""

from __ROOT__.ui.pages.home import HomePage

__all__: list[str] = ["HomePage"]
'''


_UI_PAGES_HOME = '''\
"""The home screen."""

from __future__ import annotations

from tempest_core import Text, Widget
from tempest_fastapi_sdk.ui.components import Card
from tempest_fastapi_sdk.ui.layout import Grid

from __ROOT__.ui.components import Stat
from __ROOT__.ui.layout import BasePage


class HomePage(BasePage):
    """Landing screen showing a couple of headline metrics.

    Attributes:
        users (int): How many users exist.
        orders (int): How many orders exist.
    """

    users: int = 0
    orders: int = 0

    def body(self) -> Widget:
        """Build the screen content.

        Returns:
            Widget: The page body.
        """
        return Grid(
            children=[
                Card(
                    title="Usuários",
                    children=[Stat(label="cadastrados", value=str(self.users))],
                ),
                Card(
                    title="Pedidos",
                    children=[Stat(label="no total", value=str(self.orders))],
                ),
                Text(content="Bem-vindo", tag="h1", attrs={"class": "page-title"}),
            ],
        )


__all__: list[str] = ["HomePage"]
'''


_WEB_ROUTER = '''\
"""HTML routes: pages rendered on the server.

Wire it up in ``api/app.py`` alongside the API routers::

    from __ROOT__.api.routers.web import router as web_router
    from __ROOT__.ui import CSS_PATH, STYLESHEET
    from tempest_fastapi_sdk.ui.css import make_css_router

    app.include_router(make_css_router(STYLESHEET, path=CSS_PATH))
    app.include_router(web_router)

Routes stay thin: load data through a controller, hand it to a page,
return :func:`html_response`.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response
from tempest_fastapi_sdk.ssr import html_response

from __ROOT__.ui import CSS_PATH, HomePage

router: APIRouter = APIRouter(tags=["web"], include_in_schema=False)


@router.get("/")
async def home() -> Response:
    """Render the home screen.

    Returns:
        Response: The rendered HTML document.
    """
    return html_response(
        HomePage(title="Início", users=0, orders=0),
        title="Início",
        stylesheets=[CSS_PATH],
    )


__all__: list[str] = ["router"]
'''


# extra -> {relative path under the source root: file content}.
LAYER_FILES: dict[str, dict[str, str]] = {
    "queue": {
        "queue/__init__.py": _QUEUE_INIT,
        "queue/handlers.py": _QUEUE_HANDLERS,
    },
    "ssr": {
        "ui/__init__.py": _UI_INIT,
        "ui/styles.py": _UI_STYLES,
        "ui/layout/__init__.py": _UI_LAYOUT_INIT,
        "ui/layout/base.py": _UI_LAYOUT_BASE,
        "ui/components/__init__.py": _UI_COMPONENTS_INIT,
        "ui/components/stat.py": _UI_COMPONENTS_STAT,
        "ui/pages/__init__.py": _UI_PAGES_INIT,
        "ui/pages/home.py": _UI_PAGES_HOME,
        "api/routers/web.py": _WEB_ROUTER,
    },
    "tasks": {
        "tasks/__init__.py": _TASKS_INIT,
        "tasks/jobs.py": _TASKS_JOBS,
    },
}
"""Maps each layer-bearing extra to the files it contributes."""


def detect_source_root(target: Path) -> str:
    """Return the project's source-root package name (``src`` or ``app``).

    The layout rules allow either ``src/`` or ``app/`` as the root.
    When neither exists yet, ``"src"`` is assumed (the scaffold default).

    Args:
        target (Path): Project root directory.

    Returns:
        str: ``"src"`` or ``"app"``.
    """
    if (target / "app").is_dir() and not (target / "src").is_dir():
        return "app"
    return "src"


def layers_for_extras(extras: set[str]) -> list[str]:
    """Return the sorted layer keys triggered by the given extras.

    Args:
        extras (set[str]): The parsed SDK extras.

    Returns:
        list[str]: Extra names that contribute a source layer, sorted.
    """
    return sorted(extras & LAYER_FILES.keys())


def add_src_layers(
    target: Path,
    extras: set[str],
    *,
    force: bool,
) -> tuple[list[Path], list[Path]]:
    """Write the source layers triggered by ``extras`` into ``target``.

    Args:
        target (Path): Project root directory.
        extras (set[str]): Parsed SDK extras driving which layers land.
        force (bool): Overwrite files that already exist. When False,
            existing files are skipped (reported separately).

    Returns:
        tuple[list[Path], list[Path]]: ``(written, skipped)`` absolute
        paths — files written this run and files left untouched because
        they already existed and ``force`` was False.
    """
    root = detect_source_root(target)
    root_dir = target / root

    written: list[Path] = []
    skipped: list[Path] = []
    for extra in layers_for_extras(extras):
        for relative, content in LAYER_FILES[extra].items():
            destination = root_dir.joinpath(*relative.split("/"))
            if destination.exists() and not force:
                skipped.append(destination)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                content.replace(_ROOT_PLACEHOLDER, root),
                encoding="utf-8",
            )
            written.append(destination)
    return written, skipped


__all__: list[str] = [
    "LAYER_FILES",
    "add_src_layers",
    "detect_source_root",
    "layers_for_extras",
]
