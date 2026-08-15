"""Pages: one class per screen, composed of components.

A page is the top of the ``ui`` layer. It declares the data it needs as
typed fields, builds its content in :meth:`~Page.body`, and inherits its
chrome from a base page's :meth:`~Page.shell`. Routes stay thin: load
data through a controller, construct the page, hand it to
:func:`tempest_fastapi_sdk.ssr.html_response`.
"""

from tempest_fastapi_sdk.ui.pages.page import Page as Page

__all__: list[str] = ["Page"]
