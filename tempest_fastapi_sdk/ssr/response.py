"""Render typed widget trees into FastAPI :class:`HTMLResponse` objects.

This module is the bridge between the typed component layer
(:mod:`tempest_core` widgets / :class:`tempest_fastapi_sdk.ssr.Page`) and
the HTTP layer. A FastAPI route builds a Python component tree and returns
:func:`html_response`, which renders it to HTML on the server and hands
FastAPI a ready-to-send response.

The heavy dependency (``tempestweb``) is imported **lazily** inside the
function body so ``import tempest_fastapi_sdk.ssr`` never hard-requires the
optional ``[ssr]`` extra. The import only runs when a response is actually
rendered, mirroring how the ``[webpush]`` / ``[minio]`` extras behave.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.responses import HTMLResponse

if TYPE_CHECKING:
    from tempest_core import Widget


def _require_html_backend() -> object:
    """Import and return the ``tempestweb.html`` module lazily.

    Returns:
        The imported ``tempestweb.html`` module.

    Raises:
        ImportError: When the optional ``[ssr]`` extra is missing.
    """
    try:
        import tempestweb.html as html_backend
    except ImportError as exc:
        raise ImportError(
            "Server-side rendering requires the optional [ssr] extra. "
            "Install with: pip install tempest-fastapi-sdk[ssr]",
        ) from exc
    return html_backend


def _htmx_script_tag() -> str:
    """Return the ``<script>`` tag pointing at the locally-served HTMX asset.

    The path matches the default prefix of
    :func:`tempest_fastapi_sdk.ssr.make_htmx_router`, so mounting that
    router serves the bundled HTMX file with no CDN dependency (CSP- and
    offline-friendly).

    Returns:
        The HTML ``<script>`` tag for ``/_ssr/htmx.js``.
    """
    return '<script src="/_ssr/htmx.js" defer></script>'


def html_response(
    widget: Widget,
    *,
    title: str | None = None,
    status_code: int = 200,
    htmx: bool = False,
    document: bool = True,
    lang: str = "pt-BR",
) -> HTMLResponse:
    """Render a widget tree and return it as a FastAPI ``HTMLResponse``.

    Args:
        widget (Widget): The component / widget tree to render. A
            :class:`tempest_fastapi_sdk.ssr.Page` (or any
            ``tempest_core`` widget) is accepted; ``Component`` subtrees
            are expanded via their ``render()`` hook by the renderer.
        title (str | None): The document ``<title>``. Required when
            ``document`` is ``True``; ignored when ``document`` is
            ``False``.
        status_code (int): HTTP status code for the response. Defaults to
            ``200``.
        htmx (bool): When ``True`` and ``document`` is ``True``, inject a
            ``<script>`` tag pointing at the SDK's locally-served HTMX
            asset (``/_ssr/htmx.js``) rather than a CDN. Has no effect on
            bare fragments (``document=False``).
        document (bool): When ``True`` (default), render a full HTML5
            document via ``render_document``. When ``False``, render a
            bare HTML fragment via ``render_to_html`` — the shape HTMX
            expects for partial swaps.
        lang (str): The document language attribute. Defaults to
            ``"pt-BR"``. Only used when ``document`` is ``True``.

    Returns:
        An :class:`~fastapi.responses.HTMLResponse` with the rendered
        HTML and the given status code. The media type is ``text/html``.

    Raises:
        ValueError: When ``document`` is ``True`` and ``title`` is
            ``None``.
        ImportError: When the optional ``[ssr]`` extra is not installed.
    """
    html_backend = _require_html_backend()

    if document:
        if title is None:
            raise ValueError(
                "html_response(document=True) requires a `title`; "
                "pass title=... or use document=False for a fragment.",
            )
        head = _htmx_script_tag() if htmx else ""
        content = html_backend.render_document(  # type: ignore[attr-defined]
            widget,
            title=title,
            lang=lang,
            head=head,
            htmx=False,
        )
    else:
        content = html_backend.render_to_html(widget)  # type: ignore[attr-defined]

    return HTMLResponse(content=content, status_code=status_code)
