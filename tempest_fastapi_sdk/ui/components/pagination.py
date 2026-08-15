"""Pagination: page links for a paginated listing.

Pairs with :class:`~tempest_fastapi_sdk.schemas.BasePaginationSchema` —
:func:`pagination_for` reads an envelope a repository already returned
and produces the matching control, so the page numbers on screen and the
ones the query used can never disagree.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from pydantic import Field

from tempest_fastapi_sdk.ui._core import Component, Stack, Text, Widget
from tempest_fastapi_sdk.ui.components.classes import (
    DEFAULT_CLASSES,
    ComponentClasses,
)


class Pagination(Component):
    """Numbered page links with previous / next controls.

    Attributes:
        page (int): The current page, 1-indexed.
        pages (int): The total number of pages. ``0`` or ``1`` renders
            nothing — a single page needs no control.
        url (str): Path the links point at. The page number is appended
            as a query parameter.
        query_param (str): Name of the page query parameter.
        extra_query (dict[str, str]): Query parameters carried over on
            every link, so active filters survive a page change.
        window (int): How many neighbours of the current page to list on
            each side.
        previous_label (str): Text of the previous-page link.
        next_label (str): Text of the next-page link.
        classes (ComponentClasses): Class names to apply.

    Example:
        ```python
        from tempest_fastapi_sdk.ui.components import Pagination

        control = Pagination(page=3, pages=10, url="/users")
        ```
    """

    page: int = 1
    pages: int = 1
    url: str = ""
    query_param: str = "page"
    extra_query: dict[str, str] = Field(default_factory=dict)
    window: int = 2
    previous_label: str = "Anterior"
    next_label: str = "Próxima"
    classes: ComponentClasses = DEFAULT_CLASSES

    def href(self, page: int) -> str:
        """Build the URL of one page.

        Args:
            page (int): The target page number.

        Returns:
            str: ``{url}?{extra}&{query_param}={page}``, with the extra
            parameters preserved in insertion order.
        """
        query = {**self.extra_query, self.query_param: str(page)}
        return f"{self.url}?{urlencode(query)}"

    def visible_pages(self) -> list[int]:
        """Return the page numbers rendered as links.

        Returns:
            list[int]: The current page plus :attr:`window` neighbours
            on each side, clamped to the available range.
        """
        first = max(1, self.page - self.window)
        last = min(self.pages, self.page + self.window)
        return list(range(first, last + 1))

    def _link(self, page: int, label: str, *, extra_class: str = "") -> Widget:
        """Build one link element.

        Args:
            page (int): The target page.
            label (str): The link text.
            extra_class (str): Modifier class appended to the link.

        Returns:
            Widget: An ``<a>`` element, marked ``aria-current="page"``
            when it points at the current page.
        """
        attrs = {
            "href": self.href(page),
            "class": f"{self.classes.pagination_link} {extra_class}".strip(),
        }
        if page == self.page:
            attrs["aria-current"] = "page"
        return Text(content=label, tag="a", attrs=attrs)

    def _disabled(self, label: str) -> Widget:
        """Build a disabled previous/next marker.

        Args:
            label (str): The text to show.

        Returns:
            Widget: A ``<span>`` carrying the disabled modifier class,
            so the control keeps its shape at the range ends.
        """
        return Text(
            content=label,
            tag="span",
            attrs={
                "class": (
                    f"{self.classes.pagination_link} {self.classes.pagination_disabled}"
                ),
                "aria-disabled": "true",
            },
        )

    def render(self) -> Widget:
        """Compose the pagination control.

        Returns:
            Widget: A ``<nav>`` holding the links, or an empty ``<nav>``
            when there is at most one page.
        """
        attrs = {"class": self.classes.pagination, "aria-label": "Paginação"}
        if self.pages <= 1:
            return Stack(tag="nav", attrs=attrs, children=[])

        children: list[Widget] = []
        if self.page > 1:
            children.append(self._link(self.page - 1, self.previous_label))
        else:
            children.append(self._disabled(self.previous_label))

        for number in self.visible_pages():
            children.append(
                self._link(
                    number,
                    str(number),
                    extra_class=(
                        self.classes.pagination_current if number == self.page else ""
                    ),
                ),
            )

        if self.page < self.pages:
            children.append(self._link(self.page + 1, self.next_label))
        else:
            children.append(self._disabled(self.next_label))

        return Stack(tag="nav", attrs=attrs, children=children)


def pagination_for(
    envelope: Any,
    *,
    url: str,
    query_param: str = "page",
    extra_query: dict[str, str] | None = None,
    window: int = 2,
    classes: ComponentClasses | None = None,
) -> Pagination:
    """Build a :class:`Pagination` from a pagination envelope.

    Args:
        envelope (Any): A
            :class:`~tempest_fastapi_sdk.schemas.BasePaginationSchema`
            (or anything exposing ``page`` and ``pages``).
        url (str): Path the links point at.
        query_param (str): Name of the page query parameter.
        extra_query (dict[str, str] | None): Query parameters carried
            over on every link.
        window (int): Neighbour pages listed on each side.
        classes (ComponentClasses | None): Class name overrides.

    Returns:
        Pagination: The control matching the envelope.

    Raises:
        AttributeError: When the envelope exposes no ``page`` /
            ``pages``.

    Example:
        ```python
        from tempest_fastapi_sdk.schemas import BasePaginationSchema
        from tempest_fastapi_sdk.ui.components import pagination_for

        envelope: BasePaginationSchema[str] = BasePaginationSchema[str](
            items=["a"], total=30, page=2, page_size=10, pages=3
        )
        control = pagination_for(envelope, url="/users")
        ```
    """
    return Pagination(
        page=int(envelope.page),
        pages=int(envelope.pages),
        url=url,
        query_param=query_param,
        extra_query=dict(extra_query or {}),
        window=window,
        classes=classes or DEFAULT_CLASSES,
    )


__all__: list[str] = ["Pagination", "pagination_for"]
