"""The HTML a social crawler reads to build a link preview.

WhatsApp, Telegram, Instagram and X fetch the shared URL without running
JavaScript and read the Open Graph tags from the HTML they get back, so
an SPA needs the backend to answer that URL with the tags already in
place. :func:`render_share_page` writes that document: every interpolated
value goes through :func:`html.escape` with ``quote=True``, including the
URLs, whose scheme :class:`~tempest_fastapi_sdk.share.SharePreviewSchema`
already restricted to ``http``/``https``.
"""

from html import escape

from tempest_fastapi_sdk.share.schemas import SharePreviewSchema

_REDIRECT_DELAY_SECONDS: int = 0
"""Delay of the ``<meta http-equiv=refresh>`` that sends a human onward."""


def _meta(attribute: str, name: str, content: str) -> str:
    """Write one escaped ``<meta>`` tag.

    Args:
        attribute (str): ``property`` (Open Graph) or ``name`` (Twitter).
        name (str): The tag name, a constant of this module.
        content (str): The value, escaped here.

    Returns:
        str: The tag.
    """
    return f'<meta {attribute}="{name}" content="{escape(content, quote=True)}">'


def render_share_page(
    preview: SharePreviewSchema,
    *,
    url: str,
    image_url: str | None,
    image_width: int | None = None,
    image_height: int | None = None,
    site_name: str | None = None,
    locale: str | None = None,
    link_text: str = "Open",
) -> str:
    """Write the share page of one item.

    A crawler reads the tags; a human is sent to ``preview.redirect_url``
    by ``<meta http-equiv=refresh>`` and, where refresh is blocked, by the
    visible link.

    Args:
        preview (SharePreviewSchema): What to say about the item.
        url (str): The canonical URL (``og:url``); the router passes
            ``preview.canonical_url`` or the page's own public URL.
        image_url (str | None): The absolute ``og:image``, or ``None`` for
            a page without image (``twitter:card`` becomes ``summary``).
        image_width (int | None): ``og:image:width``, when known.
        image_height (int | None): ``og:image:height``, when known.
        site_name (str | None): ``og:site_name``.
        locale (str | None): ``og:locale`` (``pt_BR``); its language also
            goes into ``<html lang>``.
        link_text (str): Text of the visible link to ``redirect_url``.

    Returns:
        str: The HTML document.

    Example:
        >>> from tempest_fastapi_sdk.share import SharePreviewSchema
        >>> page = render_share_page(
        ...     SharePreviewSchema(
        ...         title="<b>Promo</b>",
        ...         description="Hoje",
        ...         redirect_url="https://app.example.com/p/1",
        ...     ),
        ...     url="https://api.example.com/s/1",
        ...     image_url=None,
        ... )
        >>> "<b>Promo</b>" in page
        False
    """
    redirect = escape(preview.redirect_url, quote=True)
    title = escape(preview.title, quote=True)
    tags: list[str] = [
        _meta("property", "og:type", "website"),
        _meta("property", "og:title", preview.title),
        _meta("property", "og:description", preview.description),
        _meta("property", "og:url", url),
    ]
    if site_name is not None:
        tags.append(_meta("property", "og:site_name", site_name))
    if locale is not None:
        tags.append(_meta("property", "og:locale", locale))
    if image_url is not None:
        tags.append(_meta("property", "og:image", image_url))
        if image_width is not None and image_height is not None:
            tags.append(_meta("property", "og:image:width", str(image_width)))
            tags.append(_meta("property", "og:image:height", str(image_height)))
        tags.append(
            _meta("property", "og:image:alt", preview.image_alt or preview.title)
        )
        tags.append(_meta("name", "twitter:card", "summary_large_image"))
        tags.append(_meta("name", "twitter:image", image_url))
    else:
        tags.append(_meta("name", "twitter:card", "summary"))
    tags.append(_meta("name", "twitter:title", preview.title))
    tags.append(_meta("name", "twitter:description", preview.description))
    tags.append(_meta("name", "description", preview.description))
    lang = escape((locale or "en").replace("-", "_").split("_")[0], quote=True)
    head = "\n    ".join(tags)
    return (
        "<!doctype html>\n"
        f'<html lang="{lang}">\n'
        "  <head>\n"
        '    <meta charset="utf-8">\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"    <title>{title}</title>\n"
        f'    <link rel="canonical" href="{escape(url, quote=True)}">\n'
        f"    {head}\n"
        f'    <meta http-equiv="refresh" content="{_REDIRECT_DELAY_SECONDS}; '
        f'url={redirect}">\n'
        "  </head>\n"
        "  <body>\n"
        f"    <h1>{title}</h1>\n"
        f"    <p>{escape(preview.description, quote=True)}</p>\n"
        f'    <p><a href="{redirect}">{escape(link_text, quote=True)}</a></p>\n'
        "  </body>\n"
        "</html>\n"
    )


__all__: list[str] = ["render_share_page"]
