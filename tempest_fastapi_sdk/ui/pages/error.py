"""ErrorPage: the screen an HTML route answers when a request fails."""

from __future__ import annotations

from typing import ClassVar, Self

from tempest_fastapi_sdk.ui._core import Stack, Text, Widget
from tempest_fastapi_sdk.ui.components.alert import Alert
from tempest_fastapi_sdk.ui.pages.page import Page


class ErrorPage(Page):
    """A page that states a failed request's status and message.

    :func:`tempest_fastapi_sdk.ssr.register_html_error_handlers` renders
    it for a ``GET`` to an HTML route that raised. To give it the
    service's chrome, combine it with the service's base page —
    ``class AdminErrorPage(AdminBasePage, ErrorPage)`` inherits
    ``shell()``, ``stylesheets`` and ``head`` from the base page and
    ``body()`` from this one — and pass that class as ``error_page=``.

    Attributes:
        title_template (ClassVar[str]): The ``title`` :meth:`from_error`
            builds, formatted with ``status_code``. Defaults to
            ``"Erro {status_code}"``; set ``"Error {status_code}"`` on a
            subclass for an English panel.
        status_code (int): The HTTP status of the failure.
        detail (str): The message, already localized by the JSON handler
            when a catalog is configured. Rendered escaped.
        code (str | None): The machine-readable error code, when the
            failure carried one.

    Example:
        ```python
        from tempest_fastapi_sdk.ui.pages import ErrorPage

        page = ErrorPage(
            title="Erro 404",
            status_code=404,
            detail="Bucket não encontrado.",
        )
        ```
    """

    title_template: ClassVar[str] = "Erro {status_code}"

    status_code: int
    detail: str
    code: str | None = None

    @classmethod
    def from_error(
        cls,
        *,
        status_code: int,
        detail: str,
        code: str | None = None,
    ) -> Self:
        """Build the page for a failed request.

        Args:
            status_code (int): The HTTP status of the failure.
            detail (str): The message to show.
            code (str | None): The machine-readable error code, if any.

        Returns:
            Self: An instance of ``cls`` titled from
            :attr:`title_template`.
        """
        return cls(
            title=cls.title_template.format(status_code=status_code),
            status_code=status_code,
            detail=detail,
            code=code,
        )

    def body(self) -> Widget:
        """Compose the error body.

        Returns:
            Widget: A ``<section>`` with the page title as heading and
            the message in an error :class:`Alert`.
        """
        return Stack(
            tag="section",
            children=[
                Text(content=self.title, tag="h1"),
                Alert(message=self.detail, variant="error"),
            ],
        )


__all__: list[str] = ["ErrorPage"]
