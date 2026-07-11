"""Server-side rendering: typed Python components rendered to HTML.

Build your FastAPI service full-stack and fully typed — no template
language. Declare pages as :class:`Page` subclasses (typed
``tempest_core`` components), and return :func:`html_response` from a
route to render them to HTML on the server. Serve HTMX locally with
:func:`make_htmx_router` for progressive, server-driven interactivity.

To ship a **compiled** tempestweb build instead of rendering per
request, point the SDK at a ``tempestweb build`` output directory:
:func:`make_web_app_router` serves a static (wasm) SPA build with a
single-page history fallback, and :func:`build_web_app` hosts a
server-mode build (WebSocket/SSE) as a mountable sub-application.
:func:`detect_build_mode` tells the two apart.

The rendering backend (``tempestweb``) is imported lazily, so importing
this package never hard-requires the optional ``[ssr]`` extra; the
dependency is touched only when a page is actually rendered.
"""

from tempest_fastapi_sdk.ssr.assets import make_htmx_router as make_htmx_router
from tempest_fastapi_sdk.ssr.page import Page as Page
from tempest_fastapi_sdk.ssr.response import html_response as html_response
from tempest_fastapi_sdk.ssr.webapp import BuildMode as BuildMode
from tempest_fastapi_sdk.ssr.webapp import build_web_app as build_web_app
from tempest_fastapi_sdk.ssr.webapp import detect_build_mode as detect_build_mode
from tempest_fastapi_sdk.ssr.webapp import make_web_app_router as make_web_app_router

__all__: list[str] = [
    "BuildMode",
    "Page",
    "build_web_app",
    "detect_build_mode",
    "html_response",
    "make_htmx_router",
    "make_web_app_router",
]
