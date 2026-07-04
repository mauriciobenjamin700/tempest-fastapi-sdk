"""Server-side rendering: typed Python components rendered to HTML.

Build your FastAPI service full-stack and fully typed — no template
language. Declare pages as :class:`Page` subclasses (typed
``tempest_core`` components), and return :func:`html_response` from a
route to render them to HTML on the server. Serve HTMX locally with
:func:`make_htmx_router` for progressive, server-driven interactivity.

The rendering backend (``tempestweb``) is imported lazily, so importing
this package never hard-requires the optional ``[ssr]`` extra; the
dependency is touched only when a page is actually rendered.
"""

from tempest_fastapi_sdk.ssr.assets import make_htmx_router as make_htmx_router
from tempest_fastapi_sdk.ssr.page import Page as Page
from tempest_fastapi_sdk.ssr.response import html_response as html_response

__all__: list[str] = [
    "Page",
    "html_response",
    "make_htmx_router",
]
