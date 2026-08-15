"""Serve a typed stylesheet from the application itself.

A :class:`~tempest_fastapi_sdk.ui.css.StyleSheet` is rendered once, when
the router is built, and served with a strong ``ETag`` so a browser that
already holds the sheet gets a ``304`` instead of the bytes. No CDN and no
build step: the CSS is produced by the same Python process that renders
the pages.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from tempest_fastapi_sdk.ui.css.rules import StyleSheet

_CSS_MEDIA_TYPE = "text/css; charset=utf-8"
_DEFAULT_CACHE_CONTROL = "public, max-age=3600"


def css_response(
    sheet: StyleSheet,
    *,
    cache_control: str = _DEFAULT_CACHE_CONTROL,
    status_code: int = 200,
) -> Response:
    """Render a stylesheet into a ``text/css`` response.

    Args:
        sheet (StyleSheet): The sheet to render.
        cache_control (str): Value of the ``Cache-Control`` header.
        status_code (int): HTTP status code. Defaults to ``200``.

    Returns:
        Response: The rendered CSS with an ``ETag`` derived from its
        content and the given cache policy.
    """
    body = sheet.to_css()
    return Response(
        content=body,
        media_type=_CSS_MEDIA_TYPE,
        status_code=status_code,
        headers={"ETag": sheet.etag(), "Cache-Control": cache_control},
    )


def make_css_router(
    sheet: StyleSheet,
    *,
    path: str = "/static/app.css",
    cache_control: str = _DEFAULT_CACHE_CONTROL,
) -> APIRouter:
    """Build a router serving one stylesheet at a fixed path.

    The CSS is rendered eagerly, at router-construction time, so no
    request pays the cost of walking the rules.

    Args:
        sheet (StyleSheet): The sheet to serve.
        path (str): Absolute route path, including the leading slash.
            Point your pages' ``<link rel="stylesheet">`` at the same
            value — :func:`tempest_fastapi_sdk.ssr.html_response` takes
            it through its ``stylesheets`` argument.
        cache_control (str): Value of the ``Cache-Control`` header.

    Returns:
        APIRouter: A router exposing ``GET {path}``, answering ``304``
        when the request's ``If-None-Match`` matches the sheet's ETag.

    Raises:
        ValueError: When ``path`` does not start with ``"/"``.

    Example:
        ```python
        from fastapi import FastAPI

        from tempest_fastapi_sdk.ui.css import Rule, StyleSheet, make_css_router

        app: FastAPI = FastAPI()
        sheet: StyleSheet = StyleSheet(
            rules=[Rule(".card", declarations={"padding": "16px"})],
        )
        app.include_router(make_css_router(sheet))
        ```
    """
    if not path.startswith("/"):
        raise ValueError(f"CSS path must start with '/', got {path!r}.")

    router = APIRouter()
    body = sheet.to_css()
    etag = sheet.etag()
    headers = {"ETag": etag, "Cache-Control": cache_control}

    @router.get(path, include_in_schema=False)
    async def stylesheet(request: Request) -> Response:
        """Serve the rendered stylesheet.

        Args:
            request (Request): The incoming request, read for its
                ``If-None-Match`` header.

        Returns:
            Response: The CSS, or an empty ``304`` when the client's
            cached copy is current.
        """
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(content=body, media_type=_CSS_MEDIA_TYPE, headers=headers)

    return router


def stylesheet_links(*hrefs: str) -> str:
    """Build the ``<link rel="stylesheet">`` tags for a document head.

    Args:
        *hrefs (str): Stylesheet URLs, in load order.

    Returns:
        str: The concatenated link tags, with ``"`` escaped in each URL
        so a crafted path cannot break out of the attribute.
    """
    return "".join(
        f'<link rel="stylesheet" href="{href.replace(chr(34), "&quot;")}">'
        for href in hrefs
    )


__all__: list[str] = ["css_response", "make_css_router", "stylesheet_links"]
