"""Render HTML to PDF, from a string, a template, or a typed document.

The engine is WeasyPrint, chosen for CSS Paged Media: ``@page`` margins,
a header and footer that repeat, and ``counter(page)`` / ``counter(pages)``
for *página X de Y*. A browser-based renderer produces prettier CSS
support and costs a 150 MB browser in the image; a pure-Python one costs
no system library and cannot paginate a report properly. For documents —
which is what this module is for — paged media is the whole job.

Two properties this module keeps, both testable:

* **Rendering never blocks the loop.** Layout is CPU-bound and a long
  report takes hundreds of milliseconds, so every render goes through a
  worker thread behind a semaphore.
* **Same input, same bytes.** WeasyPrint writes no creation date and no
  document identifier unless asked, so two renders of one payload are
  byte-identical — across processes. That is what makes a rendered
  document hashable, cacheable and comparable in a test. Setting
  ``metadata`` gives it up on purpose.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.exceptions import NotFoundException, ValidationException
from tempest_fastapi_sdk.pdf.assets import AssetPolicy, build_url_fetcher
from tempest_fastapi_sdk.pdf.documents import BUNDLED_DOCUMENTS, PdfDocument
from tempest_fastapi_sdk.pdf.formatting import (
    format_cents,
    format_date,
    format_date_long,
    format_document,
    format_quantity,
    valor_por_extenso,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from jinja2 import Environment

DEFAULT_MAX_CONCURRENT_RENDERS: int = 4
"""Renders allowed to run at once.

Layout is CPU-bound and single-threaded per document, so more workers
than cores turns latency into queueing without adding throughput. Four
matches the container sizes services here actually run on; raise it with
``max_concurrent`` when the host is bigger.
"""

BUNDLED_TEMPLATE_DIR: Path = Path(__file__).parent / "templates"
"""Directory holding the templates shipped with the SDK."""

_LOGGER: logging.Logger = logging.getLogger(__name__)


class TemplateNotFound(NotFoundException):
    """Raised when neither the project nor the SDK has the template."""

    code: str = "PDF_TEMPLATE_NOT_FOUND"
    message: str = "Template not found"


class PdfRenderer:
    """Renders HTML into PDF bytes.

    The Jinja environment looks in the project's ``template_dir`` first
    and falls back to the bundled templates, so a project overrides
    ``receipt.html`` by dropping a file of that name next to its own —
    the same shadowing rule
    :class:`~tempest_fastapi_sdk.utils.email.EmailUtils` uses.

    Attributes:
        template_dir (Path | None): Project templates, searched first.
        assets (AssetPolicy): What a template may load. The default
            denies every fetch.
        strict_assets (bool): Whether a refused asset fails the render.
    """

    def __init__(
        self,
        *,
        template_dir: str | Path | None = None,
        assets: AssetPolicy | None = None,
        strict_assets: bool = True,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT_RENDERS,
    ) -> None:
        """Initialize the renderer.

        Args:
            template_dir (str | Path | None): Directory whose templates
                shadow the bundled ones. ``None`` uses only the bundled
                set.
            assets (AssetPolicy | None): What templates may load.
                ``None`` builds a policy that denies every fetch, which
                is enough for the bundled templates — their CSS is
                inlined and images arrive as ``data:`` URIs.
            strict_assets (bool): When ``True`` (default), a refused or
                unreachable asset raises after the page renders. The
                renderer otherwise swallows it and returns a document
                with a hole where the logo was, which is the wrong
                answer for an invoice.
            max_concurrent (int): Renders allowed to run at once.

        Raises:
            ValueError: If ``max_concurrent`` is below 1, or
                ``template_dir`` is not a directory.
        """
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self.template_dir: Path | None = None
        if template_dir is not None:
            resolved = Path(template_dir)
            if not resolved.is_dir():
                raise ValueError(f"template_dir is not a directory: {template_dir}")
            self.template_dir = resolved
        self.assets: AssetPolicy = assets or AssetPolicy()
        self.strict_assets: bool = strict_assets
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(max_concurrent)
        self._env: Environment | None = None

    @property
    def environment(self) -> Environment:
        """The Jinja environment, built on first use.

        Autoescaping is on for every template: a customer name carrying
        ``<`` would otherwise reshape the document, and the caller of a
        PDF endpoint is frequently not the person the data came from.

        Returns:
            Environment: The configured environment.

        Raises:
            ImportError: When Jinja2 is missing — install the ``[pdf]``
                extra.
        """
        if self._env is not None:
            return self._env
        try:
            from jinja2 import ChoiceLoader, Environment, FileSystemLoader
        except ImportError as exc:  # pragma: no cover - extra-gated
            raise ImportError(
                "PDF rendering needs Jinja2. Install the extra: "
                'pip install "tempest-fastapi-sdk[pdf]"',
            ) from exc
        searchpath: list[Any] = []
        if self.template_dir is not None:
            searchpath.append(FileSystemLoader(str(self.template_dir)))
        searchpath.append(FileSystemLoader(str(BUNDLED_TEMPLATE_DIR)))
        env = Environment(
            loader=ChoiceLoader(searchpath),
            autoescape=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        env.filters["brl"] = format_cents
        env.filters["data"] = format_date
        env.filters["data_extenso"] = format_date_long
        env.filters["extenso"] = valor_por_extenso
        env.filters["doc"] = format_document
        env.filters["qtd"] = format_quantity
        self._env = env
        return env

    def render_html_string(
        self,
        template: str,
        context: Mapping[str, Any],
    ) -> str:
        """Render a template to HTML, without producing a PDF.

        Useful to preview a layout in a browser, and to test a template
        without paying for layout.

        Args:
            template (str): Template file name.
            context (Mapping[str, Any]): Values for the template.

        Returns:
            str: The rendered HTML.

        Raises:
            TemplateNotFound: When neither the project nor the SDK has
                a template by that name.
        """
        from jinja2 import TemplateNotFound as JinjaTemplateNotFound

        try:
            loaded = self.environment.get_template(template)
        except JinjaTemplateNotFound as exc:
            raise TemplateNotFound(
                message=f"template not found: {template}",
                details={"template": template},
            ) from exc
        return loaded.render(**dict(context))

    async def render_html(
        self,
        html: str,
        *,
        base_url: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> bytes:
        """Render an HTML string to PDF bytes.

        Args:
            html (str): The document source.
            base_url (str | None): Base for resolving relative URLs. The
                asset policy still decides what may be fetched, so this
                widens nothing on its own.
            metadata (Mapping[str, Any] | None): Extra ``write_pdf``
                options (``pdf_identifier``, ``custom_metadata``, …).
                Passing any of these generally makes the output stop
                being byte-identical between runs.

        Returns:
            bytes: The PDF.

        Raises:
            AssetRefused: When ``strict_assets`` is set and the document
                referenced something the policy denies.
            ImportError: When WeasyPrint is missing.
        """
        async with self._semaphore:
            return await asyncio.to_thread(
                self._render_sync,
                html,
                base_url,
                dict(metadata or {}),
            )

    async def render_template(
        self,
        template: str,
        context: Mapping[str, Any],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> bytes:
        """Render a template to PDF bytes.

        Args:
            template (str): Template file name.
            context (Mapping[str, Any]): Values for the template.
            metadata (Mapping[str, Any] | None): Extra ``write_pdf``
                options.

        Returns:
            bytes: The PDF.

        Raises:
            TemplateNotFound: When the template does not exist.
        """
        html = self.render_html_string(template, context)
        return await self.render_html(html, metadata=metadata)

    async def render_document(
        self,
        document: PdfDocument,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> bytes:
        """Render one of the bundled documents.

        The template comes from the payload's own type, so there is no
        second argument to keep in sync with it.

        Args:
            document (PdfDocument): A validated bundled document.
            metadata (Mapping[str, Any] | None): Extra ``write_pdf``
                options.

        Returns:
            bytes: The PDF.

        Raises:
            ValidationException: When ``document`` is a
                :class:`PdfDocument` subclass that names no template —
                the base class itself, or a subclass that forgot to set
                one.
        """
        template = type(document).template
        if not template:
            raise ValidationException(
                message=(
                    f"{type(document).__name__} declares no template; set the "
                    "`template` class variable"
                ),
            )
        return await self.render_template(
            template,
            {"doc": document},
            metadata=metadata,
        )

    def _render_sync(
        self,
        html: str,
        base_url: str | None,
        options: dict[str, Any],
    ) -> bytes:
        """Do the layout. Runs in a worker thread.

        Args:
            html (str): The document source.
            base_url (str | None): Base for relative URLs.
            options (dict[str, Any]): Extra ``write_pdf`` options.

        Returns:
            bytes: The PDF.

        Raises:
            ImportError: When WeasyPrint is missing.
            AssetRefused: When ``strict_assets`` is set and an asset was
                refused. The fetcher carries ``_fail_on_errors``, so
                WeasyPrint aborts at the first refusal rather than
                laying out a document that is already missing something;
                with ``strict_assets=False`` the refusals are logged at
                warning level instead, because a hole nobody is told
                about is the worst of the three outcomes.
        """
        try:
            from weasyprint import HTML
        except ImportError as exc:  # pragma: no cover - extra-gated
            raise ImportError(
                "PDF rendering needs WeasyPrint. Install the extra: "
                'pip install "tempest-fastapi-sdk[pdf]"\n'
                "It also needs Pango and fontconfig from the system — on "
                "Debian/Ubuntu: apt-get install libpango-1.0-0 "
                "libpangoft2-1.0-0 libharfbuzz0b fontconfig fonts-dejavu-core",
            ) from exc
        from weasyprint.urls import FatalURLFetchingError

        from tempest_fastapi_sdk.pdf.assets import AssetRefused

        self.assets.take_refusals()
        document = HTML(
            string=html,
            base_url=base_url,
            url_fetcher=build_url_fetcher(
                self.assets,
                fail_on_errors=self.strict_assets,
            ),
        )
        try:
            pdf: bytes = document.write_pdf(**options)
        except FatalURLFetchingError as exc:
            refused = self.assets.take_refusals()
            raise AssetRefused(
                message=(
                    "the document referenced an asset that is not allowed; "
                    "nothing was rendered"
                ),
                details={"refused": refused or [str(exc)]},
            ) from exc
        dropped = self.assets.take_refusals()
        if dropped:
            _LOGGER.warning(
                "rendered a document with %d refused asset(s): %s",
                len(dropped),
                "; ".join(dropped),
            )
        return pdf


def bundled_document_names() -> list[str]:
    """List the bundled documents by name.

    Returns:
        list[str]: Names accepted by ``tempest pdf render`` and the
        router, sorted.
    """
    return sorted(BUNDLED_DOCUMENTS)


def document_schema(name: str) -> type[PdfDocument]:
    """Resolve a bundled document name to its schema.

    Args:
        name (str): A name from :func:`bundled_document_names`.

    Returns:
        type[PdfDocument]: The schema to validate a payload with.

    Raises:
        TemplateNotFound: When no bundled document has that name. The
            message lists the ones that exist, so a typo is one read
            away from fixed.
    """
    try:
        return BUNDLED_DOCUMENTS[name]
    except KeyError as exc:
        available = ", ".join(bundled_document_names())
        raise TemplateNotFound(
            message=f"unknown document {name!r}; available: {available}",
            details={"document": name, "available": bundled_document_names()},
        ) from exc


__all__: list[str] = [
    "BUNDLED_TEMPLATE_DIR",
    "DEFAULT_MAX_CONCURRENT_RENDERS",
    "PdfRenderer",
    "TemplateNotFound",
    "bundled_document_names",
    "document_schema",
]
