"""Typed page base for server-side rendering.

A :class:`Page` is a ``tempest_core`` ``Component`` with an opinionated
shape for building full-stack, typed pages:

* declare data as typed fields (it is a Pydantic model),
* implement :meth:`Page.body` to return the main content widget tree,
* optionally override :meth:`Page.shell` to wrap every page in a shared
  header / nav / footer layout — inherited through normal Python class
  inheritance (a ``BasePage(Page)`` with a ``shell()`` subclassed by
  concrete pages).

Pages live in the ``ui`` layer of a service (``src/ui/pages/``), one
module per screen, and receive already-loaded data: the controller does
the orchestration, the page only decides what it looks like.

The composite base (``Component``) comes from ``tempest_core``, which is
a transitive dependency of ``tempestweb`` (the ``[ssr]`` extra). Without
the extra ``Page`` still imports; constructing one raises a helpful
``ImportError``.
"""

from __future__ import annotations

from tempest_fastapi_sdk.ui._core import Component, Widget


class Page(Component):
    """Typed page base.

    Subclass it, declare fields, and implement :meth:`body`. Optionally
    override :meth:`shell` to wrap every page in a shared layout. Do not
    override :meth:`render` — it is the ``Component`` hook and already
    composes ``shell(body())`` for you.

    Attributes:
        title (str): The page title. Pass it to
            :func:`tempest_fastapi_sdk.ssr.html_response` as the document
            ``<title>``.

    Example:
        ```python
        from tempest_core import Text, Widget

        from tempest_fastapi_sdk.ui.pages import Page


        class HomePage(Page):
            user_name: str

            def body(self) -> Widget:
                return Text(content=f"Olá, {self.user_name}", tag="h1")
        ```
    """

    title: str

    def body(self) -> Widget:
        """Return the page's main content widget tree.

        Subclasses must implement this.

        Returns:
            Widget: The widget tree for the page body.

        Raises:
            NotImplementedError: When a subclass does not implement it.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement body() -> Widget.",
        )

    def shell(self, body: Widget) -> Widget:
        """Wrap the page body in a shared layout.

        The default returns ``body`` unchanged. Override in a base page
        to add chrome (header / nav / footer) inherited by every concrete
        page — :class:`~tempest_fastapi_sdk.ui.layout.Shell` is the
        ready-made frame.

        Args:
            body (Widget): The widget tree returned by :meth:`body`.

        Returns:
            Widget: The wrapped widget tree.
        """
        return body

    def render(self) -> Widget:
        """Compose the page into a single widget tree.

        This is the ``Component`` render hook. Subclasses override
        :meth:`body` and :meth:`shell` instead of this method.

        Returns:
            Widget: ``shell(body())`` — the fully composed page.
        """
        return self.shell(self.body())


__all__: list[str] = ["Page"]
