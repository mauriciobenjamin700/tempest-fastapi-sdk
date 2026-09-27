"""Serve a typed stylesheet from the application itself.

A :class:`~tempest_fastapi_sdk.ui.css.StyleSheet` is rendered once, when
the router is built, and served with a strong ``ETag`` so a browser that
already holds the sheet gets a ``304`` instead of the bytes. No CDN and no
build step: the CSS is produced by the same Python process that renders
the pages.

Two cache policies, picked per request:

* **Versioned URL** — the request carries ``?v=<version>`` matching
  :meth:`StyleSheet.version`, the URL :meth:`StyleSheet.url` builds. The
  content behind that URL never changes, so it is cached for a year and
  marked ``immutable``.
* **Anything else** — the bare path, or a version that is not the
  current one. Answered with ``no-cache``: the browser keeps its copy but
  revalidates it through the ``ETag`` before each use, so a deploy is
  seen on the next page load. A stale version is never cached long,
  which matters during a rolling deploy, when a new page can reach a
  replica still serving the old sheet.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from tempest_fastapi_sdk.ui.css.rules import _VERSION_PARAM, StyleSheet

_CSS_MEDIA_TYPE = "text/css; charset=utf-8"
_DEFAULT_CACHE_CONTROL = "no-cache"
_DEFAULT_VERSIONED_CACHE_CONTROL = "public, max-age=31536000, immutable"


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
            Defaults to ``no-cache`` (revalidate through the ``ETag``
            before each use).
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
    versioned_cache_control: str = _DEFAULT_VERSIONED_CACHE_CONTROL,
) -> APIRouter:
    """Build a router serving one stylesheet at a fixed path.

    The CSS is rendered eagerly, at router-construction time, so no
    request pays the cost of walking the rules. Link pages at
    ``sheet.url(path)`` — the content-versioned URL — so the browser
    keeps the sheet cached and fetches a new one exactly when a deploy
    changes it.

    Args:
        sheet (StyleSheet): The sheet to serve.
        path (str): Absolute route path, including the leading slash.
            Pass the same value to :meth:`StyleSheet.url` and hand the
            result to :func:`tempest_fastapi_sdk.ssr.html_response`
            through its ``stylesheets`` argument.
        cache_control (str): ``Cache-Control`` of a request **without**
            the current version (the bare path, or a stale ``?v=``).
            Defaults to ``no-cache``.
        versioned_cache_control (str): ``Cache-Control`` of a request
            whose ``?v=`` matches :meth:`StyleSheet.version`. Defaults
            to ``public, max-age=31536000, immutable``.

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
        CSS_URL: str = sheet.url("/static/app.css")
        app.include_router(make_css_router(sheet, path="/static/app.css"))
        ```
    """
    if not path.startswith("/"):
        raise ValueError(f"CSS path must start with '/', got {path!r}.")

    router = APIRouter()
    body = sheet.to_css()
    etag = sheet.etag()
    version = sheet.version()
    headers = {"ETag": etag, "Cache-Control": cache_control}
    versioned_headers = {"ETag": etag, "Cache-Control": versioned_cache_control}

    @router.get(path, include_in_schema=False)
    async def stylesheet(request: Request) -> Response:
        """Serve the rendered stylesheet.

        The long-lived policy is chosen only when the request's ``?v=``
        equals the version of the sheet this process serves; any other
        value gets the revalidating policy, so a replica never marks an
        old sheet ``immutable`` under a new URL.

        Args:
            request (Request): The incoming request, read for its
                ``?v=`` query parameter and ``If-None-Match`` header.

        Returns:
            Response: The CSS, or an empty ``304`` when the client's
            cached copy is current.
        """
        current = request.query_params.get(_VERSION_PARAM) == version
        chosen = versioned_headers if current else headers
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=chosen)
        return Response(content=body, media_type=_CSS_MEDIA_TYPE, headers=chosen)

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
