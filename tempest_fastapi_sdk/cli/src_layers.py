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

A single :class:`AsyncQueueManager` owns the broker for the whole
process. Routers and services reach it through :func:`get_broker`;
connect/disconnect run from the app lifespan.
"""

from __future__ import annotations

import os

from faststream.rabbit import RabbitBroker
from tempest_fastapi_sdk.queue import AsyncQueueManager

RABBITMQ_URL: str = os.environ.get(
    "RABBITMQ_URL",
    "amqp://guest:guest@localhost:5672/",
)
"""AMQP URL the broker connects to (override via the ``.env`` file)."""

broker: RabbitBroker = RabbitBroker(RABBITMQ_URL)
"""Process-wide FastStream broker; subscribers register against it."""

broker_manager: AsyncQueueManager = AsyncQueueManager(broker)
"""SDK manager wrapping :data:`broker` (connect/disconnect/publish)."""


def get_broker() -> AsyncQueueManager:
    """Return the process-wide FastStream broker manager.

    Returns:
        AsyncQueueManager: The shared manager (connected during the
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
"""TaskIQ background tasks: the queue, the task names and the dispatcher.

Task modules are **registration only**: ``tasks/example.py`` declares the
task, its name and schedule come from :mod:`__ROOT__.tasks.names`, and
its body calls a script in ``__ROOT__/scripts/``. Only the worker imports
the task modules (:mod:`__ROOT__.tasks.worker`); the API enqueues by
name through :class:`TaskDispatcher`, so it never pulls a task's
dependencies into a request process.

The transport comes from ``TASKIQ_BROKER_URL``: ``redis://`` selects
Redis Streams, ``amqp://`` selects RabbitMQ, and an **empty** value
selects the in-memory broker.

Open the queue in ``api/app.py`` — nothing here connects on import::

    from collections.abc import AsyncGenerator
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    from __ROOT__.tasks import tq


    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        await db.connect()
        try:
            async with tq.lifespan():
                yield
        finally:
            await db.disconnect()

Run the worker and the scheduler as their own processes::

    taskiq worker __ROOT__.tasks.worker:broker
    taskiq scheduler __ROOT__.tasks.worker:scheduler
"""

from .dispatch import TaskDispatcher as TaskDispatcher
from .dispatch import get_task_dispatcher as get_task_dispatcher
from .names import EXAMPLE_SCHEDULE as EXAMPLE_SCHEDULE
from .names import EXAMPLE_TASK_NAME as EXAMPLE_TASK_NAME
from .queue import get_task_queue as get_task_queue
from .queue import tq as tq

__all__: list[str] = [
    "EXAMPLE_SCHEDULE",
    "EXAMPLE_TASK_NAME",
    "TaskDispatcher",
    "get_task_dispatcher",
    "get_task_queue",
    "tq",
]
'''


_TASKS_QUEUE = '''\
"""The process-wide task queue every task registers against."""

from __future__ import annotations

from tempest_fastapi_sdk.tasks import TaskQueue

from __ROOT__.core import settings

tq: TaskQueue = TaskQueue.from_settings(settings)
"""Process-wide task queue; the API sends through it, the worker runs it."""


def get_task_queue() -> TaskQueue:
    """Return the process-wide task queue.

    Returns:
        TaskQueue: The shared queue (connected during the app lifespan).
    """
    return tq


__all__: list[str] = ["get_task_queue", "tq"]
'''


_TASKS_NAMES = '''\
"""Task names and schedules — the strings the API and the worker share.

This module imports nothing from the project, so the API can read a name
without importing the task that owns it. A name is the contract between
the two processes: renaming one strands every message already queued
under the old name, so a task keeps its name for life.
"""

from __future__ import annotations

from tempest_fastapi_sdk.tasks import daily

EXAMPLE_TASK_NAME: str = "example:run"
"""Registered name of :func:`__ROOT__.tasks.example.run_example_task`."""

EXAMPLE_SCHEDULE: str = daily(hour=3)
"""When the scheduler fires the example task: every day at 03:00."""


__all__: list[str] = ["EXAMPLE_SCHEDULE", "EXAMPLE_TASK_NAME"]
'''


_TASKS_EXAMPLE = '''\
"""Registration of the example task — no logic lives here.

The body calls the script that does the work, so the same code runs from
the worker, from a one-off command and from a test without a broker.
"""

from __future__ import annotations

from __ROOT__.scripts.example import run_example
from __ROOT__.tasks.names import EXAMPLE_SCHEDULE, EXAMPLE_TASK_NAME
from __ROOT__.tasks.queue import tq


@tq.cron(EXAMPLE_SCHEDULE, name=EXAMPLE_TASK_NAME)
async def run_example_task(payload: str = "scheduled") -> str:
    """Run the example script in the worker.

    Args:
        payload (str): Input for the script; the scheduled run passes
            none and gets the default.

    Returns:
        str: What the script returned.
    """
    return await run_example(payload)


__all__: list[str] = ["run_example_task"]
'''


_TASKS_DISPATCH = '''\
"""The dispatcher: how the API enqueues work without importing a task.

Each method sends one task by its name from :mod:`__ROOT__.tasks.names`
(:meth:`TaskQueue.enqueue_by_name`). The worker restores the labels the
task was registered with, so a retry policy still applies.
"""

from __future__ import annotations

from tempest_fastapi_sdk.tasks import TaskQueue

from __ROOT__.tasks.names import EXAMPLE_TASK_NAME
from __ROOT__.tasks.queue import tq


class TaskDispatcher:
    """Enqueues the service's tasks by name.

    Attributes:
        queue (TaskQueue): The queue the calls are sent through.
    """

    def __init__(self, queue: TaskQueue) -> None:
        """Bind the dispatcher to a queue.

        Args:
            queue (TaskQueue): The queue the calls are sent through.
        """
        self.queue: TaskQueue = queue

    async def example(self, payload: str) -> None:
        """Ask the worker to run the example task.

        Args:
            payload (str): Input for the example script.
        """
        await self.queue.enqueue_by_name(EXAMPLE_TASK_NAME, payload)


def get_task_dispatcher() -> TaskDispatcher:
    """Return a dispatcher over the process-wide queue (a ``Depends`` provider).

    Returns:
        TaskDispatcher: The dispatcher.
    """
    return TaskDispatcher(tq)


__all__: list[str] = ["TaskDispatcher", "get_task_dispatcher"]
'''


_TASKS_WORKER = '''\
"""Worker entry point: imports every task module so the tasks register.

The TaskIQ CLI resolves ``module:attr`` with a plain ``getattr``, so the
broker and the scheduler are bound to names of their own::

    taskiq worker __ROOT__.tasks.worker:broker
    taskiq scheduler __ROOT__.tasks.worker:scheduler

Only the worker and the scheduler import this module; the API never
does. A new task module is imported here, or it never registers.
"""

from __future__ import annotations

from __ROOT__.tasks import example as example
from __ROOT__.tasks.queue import tq

broker = tq.broker
"""Bound for ``taskiq worker __ROOT__.tasks.worker:broker``."""

scheduler = tq.scheduler
"""Bound for ``taskiq scheduler __ROOT__.tasks.worker:scheduler``."""


__all__: list[str] = ["broker", "example", "scheduler"]
'''


_SCRIPTS_EXAMPLE = '''\
"""The example task's work, callable with or without a worker."""

from __future__ import annotations


async def run_example(payload: str) -> str:
    """Process one example payload.

    Args:
        payload (str): Arbitrary input.

    Returns:
        str: A short result echoing the processed payload.
    """
    return f"processed: {payload}"


__all__: list[str] = ["run_example"]
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

from .layout import BasePage as BasePage
from .pages import HomePage as HomePage
from .styles import CSS_PATH as CSS_PATH
from .styles import CSS_URL as CSS_URL
from .styles import STYLESHEET as STYLESHEET

__all__: list[str] = ["CSS_PATH", "CSS_URL", "STYLESHEET", "BasePage", "HomePage"]
'''


_UI_STYLES = '''\
"""The stylesheet of this service, written in typed Python.

:data:`STYLESHEET` composes the SDK defaults (design tokens, form rules,
component rules) with this service's own rules. It is served from the app
itself at :data:`CSS_PATH`, and every page links :data:`CSS_URL` — the same
path with a content version, so a deploy that changes the CSS is a URL the
browser has never cached. No build step and no CDN.

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

CSS_URL: str = STYLESHEET.url(CSS_PATH)
"""What every page links: ``CSS_PATH`` plus ``?v=<content version>``.

The CSS router caches this URL for a year as ``immutable``; changing any
rule changes the version, so the browser fetches the new sheet on the
first page load after a deploy.
"""


__all__: list[str] = ["CSS_PATH", "CSS_URL", "STYLESHEET", "THEME"]
'''


_UI_LAYOUT_INIT = '''\
"""Layout: the chrome every page of this service inherits."""

from .base import BasePage as BasePage

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

from .stat import Stat as Stat

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

from .home import HomePage as HomePage

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

from __ROOT__.ui import CSS_URL, HomePage

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
        stylesheets=[CSS_URL],
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
        "tasks/queue.py": _TASKS_QUEUE,
        "tasks/names.py": _TASKS_NAMES,
        "tasks/example.py": _TASKS_EXAMPLE,
        "tasks/dispatch.py": _TASKS_DISPATCH,
        "tasks/worker.py": _TASKS_WORKER,
        "scripts/example.py": _SCRIPTS_EXAMPLE,
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


LAYER_ALIASES: dict[str, str] = {"tasks-redis": "tasks"}
"""Extras that bring the same layer as another one, mapped to its key.

``[tasks]`` and ``[tasks-redis]`` differ only in the broker transport,
which ``TaskQueue.from_settings`` reads from ``TASKIQ_BROKER_URL`` at
runtime, so both get the one ``tasks`` layer.
"""


def layers_for_extras(extras: set[str]) -> list[str]:
    """Return the sorted layer keys triggered by the given extras.

    Args:
        extras (set[str]): The parsed SDK extras.

    Returns:
        list[str]: Layer keys (keys of :data:`LAYER_FILES`) the extras
        contribute, sorted and without duplicates — ``tasks`` and
        ``tasks-redis`` together still yield one ``tasks``.
    """
    resolved = {LAYER_ALIASES.get(extra, extra) for extra in extras}
    return sorted(resolved & LAYER_FILES.keys())


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
    "LAYER_ALIASES",
    "LAYER_FILES",
    "add_src_layers",
    "detect_source_root",
    "layers_for_extras",
]
