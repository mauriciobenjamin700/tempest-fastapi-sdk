"""Locally-bundled static assets for server-side rendering.

The SDK ships two scripts inside the package
(``tempest_fastapi_sdk/ssr/_static/``) and serves them from the
application itself instead of a CDN:

* ``htmx.min.js`` — a minified copy of HTMX 2.x;
* ``confirm.js`` — the delegated listener behind ``data-confirm``: before
  a form submits (or a link navigates), it asks ``window.confirm`` with
  the text **read from the attribute**, and cancels on "no". The text is
  data, never code, so a message quoting a user-supplied name cannot
  become script.

This keeps SSR pages Content-Security-Policy friendly (``script-src
'self'`` is enough, no ``'unsafe-inline'``) and fully offline-capable —
no external host is ever contacted.

Mount :func:`make_htmx_router` on your FastAPI app and point pages at the
served paths (which :func:`tempest_fastapi_sdk.ssr.html_response` does
automatically for ``htmx=True`` and for documents that use
``data-confirm``).
"""

from __future__ import annotations

from importlib.resources import files

from fastapi import APIRouter
from fastapi.responses import Response

_STATIC_PACKAGE = "tempest_fastapi_sdk.ssr._static"
_HTMX_FILENAME = "htmx.min.js"
_CONFIRM_FILENAME = "confirm.js"
_JS_MEDIA_TYPE = "application/javascript"


def _read_static_bytes(filename: str) -> bytes:
    """Read a bundled static file from the package data.

    Args:
        filename (str): The file name inside ``ssr/_static``.

    Returns:
        The raw bytes of the file shipped inside the wheel.

    Raises:
        FileNotFoundError: When the bundled asset is missing from the
            installed package.
    """
    resource = files(_STATIC_PACKAGE) / filename
    return resource.read_bytes()


def _read_htmx_bytes() -> bytes:
    """Read the bundled HTMX file from the package data.

    Returns:
        The raw bytes of ``htmx.min.js`` shipped inside the wheel.

    Raises:
        FileNotFoundError: When the bundled asset is missing from the
            installed package.
    """
    return _read_static_bytes(_HTMX_FILENAME)


def make_htmx_router(prefix: str = "/_ssr") -> APIRouter:
    """Build a router that serves the bundled SSR scripts locally.

    The files are read once, at call time, so a real ``htmx.min.js``
    dropped into the package after import is still picked up when the
    router is constructed.

    Args:
        prefix (str): The router prefix. Defaults to ``"/_ssr"``, matching
            the ``<script>`` paths that
            :func:`tempest_fastapi_sdk.ssr.html_response` emits. Change
            both together if you customize it.

    Returns:
        An :class:`~fastapi.APIRouter` exposing ``GET {prefix}/htmx.js``
        (the bundled HTMX) and ``GET {prefix}/confirm.js`` (the
        ``data-confirm`` listener), both with an
        ``application/javascript`` media type.
    """
    router = APIRouter(prefix=prefix)
    htmx_payload = _read_htmx_bytes()
    confirm_payload = _read_static_bytes(_CONFIRM_FILENAME)

    @router.get("/htmx.js")
    async def htmx_js() -> Response:
        """Serve the bundled HTMX JavaScript file.

        Returns:
            The HTMX asset as an ``application/javascript`` response.
        """
        return Response(content=htmx_payload, media_type=_JS_MEDIA_TYPE)

    @router.get("/confirm.js")
    async def confirm_js() -> Response:
        """Serve the bundled ``data-confirm`` listener.

        Returns:
            The confirmation script as an ``application/javascript``
            response.
        """
        return Response(content=confirm_payload, media_type=_JS_MEDIA_TYPE)

    return router
