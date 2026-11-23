"""Link previews for shared items: the Open Graph page and its card image.

A link shared on WhatsApp, Telegram, Instagram or X becomes a preview
built from the Open Graph tags of the HTML the URL returns, fetched by a
crawler that runs no JavaScript. :func:`make_share_router` serves that
HTML per item, from a resolver the service writes, and — with a
:class:`ShareCardRenderer` — the 1200x630 PNG the preview shows.

    from tempest_fastapi_sdk.share import (
        ShareCardRenderer,
        SharePreviewSchema,
        make_share_router,
    )

    app.include_router(
        make_share_router(
            resolve_trip,
            public_url="https://api.example.com",
            fallback=SharePreviewSchema(
                title="BusCar",
                description="Caronas entre cidades",
                redirect_url="https://app.example.com",
            ),
            renderer=ShareCardRenderer(),
        )
    )

For the image someone posts rather than the preview a link gets,
:class:`ShareFrameRenderer` draws a frame — story or square — with a QR
code in the middle, and :func:`render_qr` draws the QR code alone, both
with the brand logo inside the code at a measured, capped size.

The page needs nothing beyond the base install; the renderers need the
``[image]`` extra (``pillow``, ``segno``) and a TrueType font on the machine
(``fonts-dejavu-core`` on Debian/Ubuntu). Importing this module needs
neither: Pillow is imported when a renderer is built.

Re-exports use the PEP 484 ``from x import Y as Y`` explicit re-export
form combined with ``__all__`` so every type-checker accepts
``from tempest_fastapi_sdk.share import make_share_router`` without a
diagnostic.
"""

from tempest_fastapi_sdk.share.card import (
    DEFAULT_FONT_CANDIDATES as DEFAULT_FONT_CANDIDATES,
)
from tempest_fastapi_sdk.share.card import OG_IMAGE_HEIGHT as OG_IMAGE_HEIGHT
from tempest_fastapi_sdk.share.card import OG_IMAGE_WIDTH as OG_IMAGE_WIDTH
from tempest_fastapi_sdk.share.card import ShareCardRenderer as ShareCardRenderer
from tempest_fastapi_sdk.share.frame import LAYOUTS as LAYOUTS
from tempest_fastapi_sdk.share.frame import MAX_PHOTO_PIXELS as MAX_PHOTO_PIXELS
from tempest_fastapi_sdk.share.frame import ShareFrameRenderer as ShareFrameRenderer
from tempest_fastapi_sdk.share.page import render_share_page as render_share_page
from tempest_fastapi_sdk.share.qr import (
    DEFAULT_QR_LOGO_FRACTION as DEFAULT_QR_LOGO_FRACTION,
)
from tempest_fastapi_sdk.share.qr import MAX_QR_LOGO_FRACTION as MAX_QR_LOGO_FRACTION
from tempest_fastapi_sdk.share.qr import render_qr as render_qr
from tempest_fastapi_sdk.share.router import PNG_MEDIA_TYPE as PNG_MEDIA_TYPE
from tempest_fastapi_sdk.share.router import ShareResolver as ShareResolver
from tempest_fastapi_sdk.share.router import make_share_router as make_share_router
from tempest_fastapi_sdk.share.schemas import (
    ShareCardLineSchema as ShareCardLineSchema,
)
from tempest_fastapi_sdk.share.schemas import ShareCardSchema as ShareCardSchema
from tempest_fastapi_sdk.share.schemas import (
    ShareFrameLayoutEnum as ShareFrameLayoutEnum,
)
from tempest_fastapi_sdk.share.schemas import ShareFrameSchema as ShareFrameSchema
from tempest_fastapi_sdk.share.schemas import (
    SharePreviewSchema as SharePreviewSchema,
)

__all__: list[str] = [
    "DEFAULT_FONT_CANDIDATES",
    "DEFAULT_QR_LOGO_FRACTION",
    "LAYOUTS",
    "MAX_PHOTO_PIXELS",
    "MAX_QR_LOGO_FRACTION",
    "OG_IMAGE_HEIGHT",
    "OG_IMAGE_WIDTH",
    "PNG_MEDIA_TYPE",
    "ShareCardLineSchema",
    "ShareCardRenderer",
    "ShareCardSchema",
    "ShareFrameLayoutEnum",
    "ShareFrameRenderer",
    "ShareFrameSchema",
    "SharePreviewSchema",
    "ShareResolver",
    "make_share_router",
    "render_qr",
    "render_share_page",
]
