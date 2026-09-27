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

from collections.abc import Sequence
from typing import TYPE_CHECKING

from fastapi.responses import HTMLResponse

from tempest_fastapi_sdk.ssr.attributes import CONFIRM_ATTRIBUTE
from tempest_fastapi_sdk.ui.css.router import stylesheet_links
from tempest_fastapi_sdk.ui.pages.page import Page

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


def _confirm_script_tag() -> str:
    """Return the ``<script>`` tag pointing at the locally-served confirm listener.

    The path matches the default prefix of
    :func:`tempest_fastapi_sdk.ssr.make_htmx_router`, which serves
    ``confirm.js`` next to ``htmx.js``.

    Returns:
        The HTML ``<script>`` tag for ``/_ssr/confirm.js``.
    """
    return '<script src="/_ssr/confirm.js" defer></script>'


def _uses_confirm(markup: str) -> bool:
    """Tell whether rendered markup carries a ``data-confirm`` attribute.

    A plain substring test on the rendered HTML: a text node that happens
    to spell the attribute also matches, which only costs loading a
    script that finds nothing to guard.

    Args:
        markup (str): The rendered HTML.

    Returns:
        bool: ``True`` when ``data-confirm="`` appears in the markup.
    """
    return f' {CONFIRM_ATTRIBUTE}="' in markup


def _insert_into_head(document_html: str, markup: str) -> str:
    """Insert markup right before the closing ``</head>`` of a document.

    Args:
        document_html (str): A full document rendered by ``render_document``.
        markup (str): The markup to insert.

    Returns:
        str: The document with ``markup`` at the end of its head. The
        first ``</head>`` is the real one: the title is escaped and the
        body comes after it.
    """
    index = document_html.find("</head>")
    if index < 0:
        return document_html + markup
    return document_html[:index] + markup + document_html[index:]


def html_response(
    widget: Widget,
    *,
    title: str | None = None,
    status_code: int = 200,
    htmx: bool = False,
    document: bool = True,
    lang: str = "pt-BR",
    stylesheets: Sequence[str] | None = None,
    head: str | None = None,
    confirm: bool | None = None,
) -> HTMLResponse:
    """Render a widget tree and return it as a FastAPI ``HTMLResponse``.

    Args:
        widget (Widget): The component / widget tree to render. A
            :class:`tempest_fastapi_sdk.ui.pages.Page` (or any
            ``tempest_core`` widget) is accepted; ``Component`` subtrees
            are expanded via their ``render()`` hook by the renderer.
        title (str | None): The document ``<title>``. When omitted and
            ``widget`` is a :class:`~tempest_fastapi_sdk.ui.pages.Page`,
            it is :meth:`Page.document_title` — the page's ``title``
            plus its ``title_suffix``. Required for any other widget
            when ``document`` is ``True``; ignored when ``document``
            is ``False``.
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
        stylesheets (Sequence[str] | None): URLs added to the document
            head as ``<link rel="stylesheet">``, in order. Point them at
            the path served by
            :func:`tempest_fastapi_sdk.ui.css.make_css_router`. ``None``
            (the default) uses the page's :attr:`Page.stylesheets` when
            ``widget`` is a page, and no stylesheet otherwise. An
            explicit value **replaces** the page's list (``()`` links
            nothing). Ignored for fragments.
        head (str | None): Raw markup appended to the document head, for
            what no argument covers (meta tags, preloads). It is inserted
            **verbatim** — never build it from user input. ``None``
            (the default) uses the page's :attr:`Page.head` when
            ``widget`` is a page; an explicit value replaces it.
            Ignored for fragments.
        confirm (bool | None): Whether to include the locally-served
            ``data-confirm`` listener (``/_ssr/confirm.js``). ``None``
            (the default) includes it when the rendered document carries
            a ``data-confirm`` attribute **or** when ``htmx`` is on,
            since an HTMX swap can bring a guarded form into a page that
            had none. ``True`` forces it, ``False`` leaves it out.
            Ignored for fragments — the listener is delegated on the
            document, so it already covers a swapped-in fragment.

    Returns:
        An :class:`~fastapi.responses.HTMLResponse` with the rendered
        HTML and the given status code. The media type is ``text/html``.

    Raises:
        ValueError: When ``document`` is ``True``, ``title`` is
            ``None`` and ``widget`` is not a page.
        ImportError: When the optional ``[ssr]`` extra is not installed.
    """
    html_backend = _require_html_backend()

    if document:
        page = widget if isinstance(widget, Page) else None
        if title is None and page is not None:
            title = page.document_title()
        if title is None:
            raise ValueError(
                "html_response(document=True) requires a `title`; "
                "pass title=... or use document=False for a fragment.",
            )
        if stylesheets is None:
            stylesheets = page.stylesheets if page is not None else ()
        if head is None:
            head = page.head if page is not None else ""
        head_markup = "".join(
            (
                stylesheet_links(*stylesheets),
                _htmx_script_tag() if htmx else "",
                head,
            ),
        )
        content = html_backend.render_document(  # type: ignore[attr-defined]
            widget,
            title=title,
            lang=lang,
            head=head_markup,
            htmx=False,
        )
        wants_confirm = (htmx or _uses_confirm(content)) if confirm is None else confirm
        if wants_confirm:
            content = _insert_into_head(content, _confirm_script_tag())
    else:
        content = html_backend.render_to_html(widget)  # type: ignore[attr-defined]

    return HTMLResponse(content=content, status_code=status_code)
