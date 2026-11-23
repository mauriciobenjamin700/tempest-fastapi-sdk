"""The router that answers a shared link: the page and its preview image.

Both routes answer ``GET`` and ``HEAD``. ``<prefix>/{item_id}`` always
answers ``200`` with HTML: a missing or unavailable item gets the
``fallback`` preview, so a link shared before the item was removed
neither breaks nor leaks what the item was.
``GET <prefix>/{item_id}/og.png`` exists only with a renderer; it checks
``If-None-Match`` before drawing, so a crawler revalidating a card costs a
resolver call and no rendering.

The public URL comes from a parameter, never from ``request.base_url``:
behind a reverse proxy that is the internal ``http://`` address, and the
``Host`` header is the client's to choose.
"""

import asyncio
import hashlib
from collections.abc import Awaitable, Sequence
from typing import Any, Protocol
from urllib.parse import quote

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import HTMLResponse

from tempest_fastapi_sdk.share.card import (
    OG_IMAGE_HEIGHT,
    OG_IMAGE_WIDTH,
    ShareCardRenderer,
    card_etag,
)
from tempest_fastapi_sdk.share.page import render_share_page
from tempest_fastapi_sdk.share.schemas import (
    SharePreviewSchema,
    require_absolute_http_url,
)
from tempest_fastapi_sdk.utils._http_cache import is_not_modified

PNG_MEDIA_TYPE: str = "image/png"
"""Media type of the preview image."""


class ShareResolver(Protocol):
    """Looks up the preview of one item.

    Returns ``None`` when the item does not exist or must not be shown
    (deleted, private, sold out): the router answers with the fallback
    preview and no image. An exception propagates — a resolver failure
    is a bug or an outage, not an unavailable item, and swallowing it
    would hide both.
    """

    def __call__(self, item_id: str, /) -> Awaitable[SharePreviewSchema | None]:
        """Resolve ``item_id``.

        Args:
            item_id (str): The path segment, as sent.

        Returns:
            Awaitable[SharePreviewSchema | None]: The preview, or ``None``.
        """
        ...


def make_share_router(
    resolver: ShareResolver,
    *,
    public_url: str,
    fallback: SharePreviewSchema,
    prefix: str = "/s",
    renderer: ShareCardRenderer | None = None,
    site_name: str | None = None,
    locale: str | None = None,
    link_text: str = "Open",
    cache_control: str = "public, max-age=600",
    tags: Sequence[str] | None = None,
    dependencies: Sequence[Any] | None = None,
    include_in_schema: bool = False,
) -> APIRouter:
    """Build the share page and preview image routes.

    Args:
        resolver (ShareResolver): ``async (item_id) -> SharePreviewSchema
            | None``.
        public_url (str): Absolute ``http(s)`` origin the links are
            served from (``https://api.example.com``), without the
            prefix. ``og:url`` and the generated ``og:image`` are built
            on it.
        fallback (SharePreviewSchema): The preview served when the
            resolver returns ``None``. Its ``card`` and ``version`` are
            ignored; an ``image_url`` is kept (a static brand image).
        prefix (str): Path prefix of the routes; starts with ``/``.
        renderer (ShareCardRenderer | None): Draws ``og.png``. Without
            it the image route is not registered, and only previews with
            an ``image_url`` carry an image.
        site_name (str | None): ``og:site_name``, the same for every item.
        locale (str | None): ``og:locale`` (``pt_BR``).
        link_text (str): Text of the visible link a human clicks when
            ``<meta http-equiv=refresh>`` is blocked.
        cache_control (str): ``Cache-Control`` of every response,
            including the image ``404``, so a crawler that keeps asking
            for an unavailable card does not reach the resolver each time.
        tags (Sequence[str] | None): OpenAPI tags, when
            ``include_in_schema`` is set.
        dependencies (Sequence[Any] | None): Router-level dependencies
            (a rate limit). Crawlers send no credentials, so an auth
            dependency here makes every preview the error page.
        include_in_schema (bool): List the routes in OpenAPI. Off by
            default: they are public meta pages, not API.

    Returns:
        APIRouter: The router, to ``app.include_router``.

    Raises:
        ValueError: When ``public_url`` is not an absolute ``http(s)``
            URL or ``prefix`` does not start with ``/``.
    """
    origin = require_absolute_http_url(public_url.rstrip("/"))
    clean_prefix = prefix.rstrip("/")
    if not clean_prefix.startswith("/"):
        raise ValueError(f"prefix must start with '/', got {prefix!r}")
    router = APIRouter(
        prefix=clean_prefix,
        tags=list(tags) if tags is not None else None,
        dependencies=list(dependencies) if dependencies is not None else None,
        include_in_schema=include_in_schema,
    )

    def page_url(item_id: str) -> str:
        """Public URL of an item's share page.

        Args:
            item_id (str): The item.

        Returns:
            str: Absolute URL, with the id percent-encoded.
        """
        return f"{origin}{clean_prefix}/{quote(item_id, safe='')}"

    def image_version(preview: SharePreviewSchema) -> str:
        """Value of ``?v=`` on the generated image URL.

        Args:
            preview (SharePreviewSchema): The item's preview with a card.

        Returns:
            str: Short hash of the version (or of the card itself when
            the resolver gave no version) and of the renderer.
        """
        source = preview.version
        if source is None and preview.card is not None:
            source = preview.card.model_dump_json()
        material = f"{source}\x00{renderer.fingerprint if renderer else ''}"
        return hashlib.sha256(material.encode()).hexdigest()[:16]

    @router.get(
        "/{item_id}",
        response_class=HTMLResponse,
        summary="Share page",
        description="HTML with the Open Graph tags of one item.",
    )
    async def share_page(item_id: str) -> HTMLResponse:
        """Answer the share page of ``item_id``.

        Args:
            item_id (str): The item.

        Returns:
            HTMLResponse: Always ``200``; the fallback when unavailable.
        """
        preview = await resolver(item_id)
        image_url: str | None
        width: int | None = None
        height: int | None = None
        if preview is None:
            shown = fallback
            url = page_url(item_id)
            image_url = fallback.image_url
        else:
            shown = preview
            url = preview.canonical_url or page_url(item_id)
            image_url = preview.image_url
            if image_url is None and renderer is not None and preview.card is not None:
                image_url = f"{page_url(item_id)}/og.png?v={image_version(preview)}"
                width, height = OG_IMAGE_WIDTH, OG_IMAGE_HEIGHT
        html = render_share_page(
            shown,
            url=url,
            image_url=image_url,
            image_width=width,
            image_height=height,
            site_name=site_name,
            locale=locale,
            link_text=link_text,
        )
        return HTMLResponse(html, headers={"Cache-Control": cache_control})

    router.add_api_route(
        "/{item_id}",
        share_page,
        methods=["HEAD"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )

    if renderer is None:
        return router
    card_renderer: ShareCardRenderer = renderer

    @router.get(
        "/{item_id}/og.png",
        response_class=Response,
        summary="Share preview image",
        description="1200x630 PNG of one item's card; 404 when unavailable.",
        responses={
            200: {"content": {PNG_MEDIA_TYPE: {}}},
            304: {"description": "The cached image is current."},
            404: {"description": "The item has no card."},
        },
    )
    async def share_image(item_id: str, request: Request) -> Response:
        """Answer the preview image of ``item_id``.

        Args:
            item_id (str): The item.
            request (Request): Read for ``If-None-Match``.

        Returns:
            Response: The PNG, ``304`` when the client's copy is current,
            or ``404`` without a card.
        """
        preview = await resolver(item_id)
        if preview is None or preview.card is None:
            return Response(
                status_code=status.HTTP_404_NOT_FOUND,
                headers={"Cache-Control": cache_control},
            )
        etag = card_etag(card_renderer, image_version(preview), item_id)
        headers = {"ETag": etag, "Cache-Control": cache_control}
        if is_not_modified(
            if_none_match=request.headers.get("if-none-match"),
            if_modified_since=None,
            etag=etag,
            last_modified=None,
        ):
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
        png = await asyncio.to_thread(card_renderer.render, preview.card)
        return Response(png, media_type=PNG_MEDIA_TYPE, headers=headers)

    router.add_api_route(
        "/{item_id}/og.png",
        share_image,
        methods=["HEAD"],
        response_class=Response,
        include_in_schema=False,
    )

    return router


__all__: list[str] = ["PNG_MEDIA_TYPE", "ShareResolver", "make_share_router"]
