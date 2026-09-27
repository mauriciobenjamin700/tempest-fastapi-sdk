"""Server-side rendering: typed Python components rendered to HTML.

Build your FastAPI service full-stack and fully typed — no template
language. Declare pages as :class:`Page` subclasses (typed
``tempest_core`` components), and return :func:`html_response` from a
route to render them to HTML on the server. Serve HTMX locally with
:func:`make_htmx_router` for progressive, server-driven interactivity.

Build the open ``attrs`` map of a widget from typed keyword arguments
with :func:`htmx`, :func:`aria` and :func:`data` — they turn
``hx-*`` / ``aria-*`` / ``data-*`` call sites from stringly-typed dicts
into autocompleted, statically-checked code, returning exactly the plain
``dict[str, str]`` you would have written.

To ship a **compiled** tempestweb build instead of rendering per
request, point the SDK at a ``tempestweb build`` output directory:
:func:`make_web_app_router` serves a static (wasm) SPA build with a
single-page history fallback, and :func:`build_web_app` hosts a
server-mode build (WebSocket/SSE) as a mountable sub-application.
:func:`detect_build_mode` tells the two apart.

Guard a destructive action with :func:`confirm` (or
``form_for(..., confirm=...)``): the attribute is read by a locally-served
listener, never interpolated into script. Carry a one-shot notice across
a redirect with :class:`FlashMiddleware`, :func:`flash` and
:func:`get_flashes`; send the browser back where it came from with
:func:`redirect_back`, which refuses an off-host or off-prefix
``Referer``; and let :func:`register_html_error_handlers` answer HTML
routes with an error page or a flash-and-redirect while the API keeps its
JSON.

The rendering backend (``tempestweb``) is imported lazily, so importing
this package never hard-requires the optional ``[ssr]`` extra; the
dependency is touched only when a page is actually rendered.
"""

from tempest_fastapi_sdk.ssr.assets import make_htmx_router as make_htmx_router
from tempest_fastapi_sdk.ssr.attributes import CONFIRM_ATTRIBUTE as CONFIRM_ATTRIBUTE
from tempest_fastapi_sdk.ssr.attributes import aria as aria
from tempest_fastapi_sdk.ssr.attributes import confirm as confirm
from tempest_fastapi_sdk.ssr.attributes import data as data
from tempest_fastapi_sdk.ssr.attributes import htmx as htmx
from tempest_fastapi_sdk.ssr.errors import (
    GENERIC_ERROR_MESSAGE as GENERIC_ERROR_MESSAGE,
)
from tempest_fastapi_sdk.ssr.errors import (
    register_html_error_handlers as register_html_error_handlers,
)
from tempest_fastapi_sdk.ssr.flashes import FLASH_COOKIE_NAME as FLASH_COOKIE_NAME
from tempest_fastapi_sdk.ssr.flashes import (
    MAX_FLASH_COOKIE_BYTES as MAX_FLASH_COOKIE_BYTES,
)
from tempest_fastapi_sdk.ssr.flashes import (
    MAX_FLASH_MESSAGE_LENGTH as MAX_FLASH_MESSAGE_LENGTH,
)
from tempest_fastapi_sdk.ssr.flashes import FlashMiddleware as FlashMiddleware
from tempest_fastapi_sdk.ssr.flashes import flash as flash
from tempest_fastapi_sdk.ssr.flashes import flash_enabled as flash_enabled
from tempest_fastapi_sdk.ssr.flashes import get_flashes as get_flashes
from tempest_fastapi_sdk.ssr.page import Page as Page
from tempest_fastapi_sdk.ssr.redirects import back_url as back_url
from tempest_fastapi_sdk.ssr.redirects import redirect_back as redirect_back
from tempest_fastapi_sdk.ssr.response import html_response as html_response
from tempest_fastapi_sdk.ssr.webapp import BuildMode as BuildMode
from tempest_fastapi_sdk.ssr.webapp import build_web_app as build_web_app
from tempest_fastapi_sdk.ssr.webapp import detect_build_mode as detect_build_mode
from tempest_fastapi_sdk.ssr.webapp import make_web_app_router as make_web_app_router

__all__: list[str] = [
    "CONFIRM_ATTRIBUTE",
    "FLASH_COOKIE_NAME",
    "GENERIC_ERROR_MESSAGE",
    "MAX_FLASH_COOKIE_BYTES",
    "MAX_FLASH_MESSAGE_LENGTH",
    "BuildMode",
    "FlashMiddleware",
    "Page",
    "aria",
    "back_url",
    "build_web_app",
    "confirm",
    "data",
    "detect_build_mode",
    "flash",
    "flash_enabled",
    "get_flashes",
    "html_response",
    "htmx",
    "make_htmx_router",
    "make_web_app_router",
    "redirect_back",
    "register_html_error_handlers",
]
