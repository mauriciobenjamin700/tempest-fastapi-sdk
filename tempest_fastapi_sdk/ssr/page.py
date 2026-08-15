"""Compatibility re-export of the typed page base.

:class:`~tempest_fastapi_sdk.ui.pages.Page` moved to the ``ui`` layer in
v0.224.0, where it sits next to the components, layouts and forms a page
is built from. ``from tempest_fastapi_sdk.ssr import Page`` keeps
working and returns the very same class — the ``ssr`` package remains
the rendering and HTTP side (``html_response``, HTMX assets, compiled
builds).
"""

from tempest_fastapi_sdk.ui.pages.page import Page as Page

__all__: list[str] = ["Page"]
