"""Pages: one class per screen, composed of components.

A page is the top of the ``ui`` layer. It declares the data it needs as
typed fields, builds its content in :meth:`~Page.body`, and inherits its
chrome from a base page's :meth:`~Page.shell`. Routes stay thin: load
data through a controller, construct the page, hand it to
:func:`tempest_fastapi_sdk.ssr.html_response`.

:class:`ErrorPage` is the ready-made screen for a failed request, used by
:func:`tempest_fastapi_sdk.ssr.register_html_error_handlers`.
"""

from tempest_fastapi_sdk.ui.pages.error import ErrorPage as ErrorPage
from tempest_fastapi_sdk.ui.pages.page import Page as Page

__all__: list[str] = ["ErrorPage", "Page"]
