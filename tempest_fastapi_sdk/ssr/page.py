"""Typed page base for server-side rendering.

A :class:`Page` is a ``tempest_core`` ``Component`` with an opinionated
shape for building full-stack, typed pages:

* declare data as typed fields (it is a Pydantic model),
* implement :meth:`Page.body` to return the main content widget tree,
* optionally override :meth:`Page.shell` to wrap every page in a shared
  header / nav / footer layout — inherited through normal Python class
  inheritance (a ``BasePage(Page)`` with a ``shell()`` subclassed by
  concrete pages).

The composite base (``Component``) comes from ``tempest_core``, which is a
transitive dependency of ``tempestweb`` (the ``[ssr]`` extra). To keep
``from tempest_fastapi_sdk.ssr import Page`` importable even when the extra
is absent, the ``Component`` import is guarded: without the extra, ``Page``
still imports, but constructing one raises a helpful ``ImportError``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tempest_core import Widget

try:
    from tempest_core.widgets import Component as _Component

    _CORE_IMPORT_ERROR: ImportError | None = None
except ImportError as exc:  # pragma: no cover - exercised only without [ssr]
    _CORE_IMPORT_ERROR = exc

    class _Component:  # type: ignore[no-redef]
        """Placeholder base used when ``tempest_core`` is not installed.

        Raises a helpful error on instantiation so the missing ``[ssr]``
        extra is reported at first use rather than at import time.
        """

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            """Reject construction when the ``[ssr]`` extra is missing.

            Raises:
                ImportError: Always, pointing at the ``[ssr]`` extra.
            """
            raise ImportError(
                "tempest_fastapi_sdk.ssr.Page requires the optional [ssr] "
                "extra. Install with: pip install tempest-fastapi-sdk[ssr]",
            ) from _CORE_IMPORT_ERROR


class Page(_Component):
    """Typed page base.

    Subclass it, declare fields, and implement :meth:`body`. Optionally
    override :meth:`shell` to wrap every page in a shared layout. Do not
    override :meth:`render` — it is the ``Component`` hook and already
    composes ``shell(body())`` for you.

    Attributes:
        title (str): The page title. Pass it to
            :func:`tempest_fastapi_sdk.ssr.html_response` as the document
            ``<title>``.
    """

    title: str

    def body(self) -> Widget:
        """Return the page's main content widget tree.

        Subclasses must implement this.

        Returns:
            The widget tree for the page body.

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
        page.

        Args:
            body (Widget): The widget tree returned by :meth:`body`.

        Returns:
            The wrapped widget tree.
        """
        return body

    def render(self) -> Widget:
        """Compose the page into a single widget tree.

        This is the ``Component`` render hook. Subclasses override
        :meth:`body` and :meth:`shell` instead of this method.

        Returns:
            ``shell(body())`` — the fully composed page.
        """
        return self.shell(self.body())
