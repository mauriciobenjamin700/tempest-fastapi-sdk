"""Locally-bundled static assets for server-side rendering.

The SDK ships a minified copy of HTMX 2.x inside the package
(``tempest_fastapi_sdk/ssr/_static/htmx.min.js``) and serves it from the
application itself instead of a CDN. This keeps SSR pages
Content-Security-Policy friendly and fully offline-capable — no external
host is ever contacted.

Mount :func:`make_htmx_router` on your FastAPI app and point pages at the
served path (which :func:`tempest_fastapi_sdk.ssr.html_response` does
automatically when ``htmx=True``).
"""

from __future__ import annotations

from importlib.resources import files

from fastapi import APIRouter
from fastapi.responses import Response

_STATIC_PACKAGE = "tempest_fastapi_sdk.ssr._static"
_HTMX_FILENAME = "htmx.min.js"
_JS_MEDIA_TYPE = "application/javascript"


def _read_htmx_bytes() -> bytes:
    """Read the bundled HTMX file from the package data.

    Returns:
        The raw bytes of ``htmx.min.js`` shipped inside the wheel.

    Raises:
        FileNotFoundError: When the bundled asset is missing from the
            installed package.
    """
    resource = files(_STATIC_PACKAGE) / _HTMX_FILENAME
    return resource.read_bytes()


def make_htmx_router(prefix: str = "/_ssr") -> APIRouter:
    """Build a router that serves the bundled HTMX asset locally.

    The file is read once, at call time, so a real ``htmx.min.js`` dropped
    into the package after import is still picked up when the router is
    constructed.

    Args:
        prefix (str): The router prefix. Defaults to ``"/_ssr"``, matching
            the ``<script>`` path that
            :func:`tempest_fastapi_sdk.ssr.html_response` emits for
            ``htmx=True`` documents. Change both together if you customize
            it.

    Returns:
        An :class:`~fastapi.APIRouter` exposing ``GET {prefix}/htmx.js``
        which returns the bundled file with an
        ``application/javascript`` media type.
    """
    router = APIRouter(prefix=prefix)
    payload = _read_htmx_bytes()

    @router.get("/htmx.js")
    async def htmx_js() -> Response:
        """Serve the bundled HTMX JavaScript file.

        Returns:
            The HTMX asset as an ``application/javascript`` response.
        """
        return Response(content=payload, media_type=_JS_MEDIA_TYPE)

    return router
