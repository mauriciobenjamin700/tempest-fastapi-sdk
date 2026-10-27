"""An opt-in FastAPI router that returns rendered documents.

Mounting it is a decision, not a default: a PDF endpoint is a CPU-bound
route that a caller can hold open, so it belongs behind whatever auth and
rate limit the service already has. Pass ``dependencies=`` to wire those
in — the router adds none of its own, because guessing which ones a
project wants is how a document endpoint ends up public.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Response, status
from pydantic import Field

from tempest_fastapi_sdk.api.route_docs import RouteDocs
from tempest_fastapi_sdk.exceptions import ValidationException
from tempest_fastapi_sdk.pdf.documents import BUNDLED_DOCUMENTS
from tempest_fastapi_sdk.pdf.renderer import bundled_document_names, document_schema
from tempest_fastapi_sdk.schemas.base import BaseSchema

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tempest_fastapi_sdk.pdf.renderer import PdfRenderer

PDF_MEDIA_TYPE: str = "application/pdf"
"""Media type of every response this router produces."""


class DocumentRequestSchema(BaseSchema):
    """Request body for rendering a bundled document.

    Attributes:
        payload (dict[str, Any]): The document's fields, validated
            against the schema the ``document`` path parameter selects.
            Kept as a mapping here so one endpoint serves every bundled
            document; the real validation happens against the concrete
            schema, and its errors name the real fields.
        filename (str | None): Name suggested to the browser.
    """

    payload: dict[str, Any] = Field(
        title="Dados do documento",
        description=(
            "Fields for the selected document. Validated against its own "
            "schema — see ``GET /pdf/documents`` for the list."
        ),
        examples=[{"issue_date": "2026-08-13", "amount_cents": 125000}],
    )
    filename: str | None = Field(
        default=None,
        title="Nome do arquivo",
        description=(
            "Suggested download name. Path separators and quotes are "
            "stripped before it reaches the header."
        ),
        examples=["recibo-0001.pdf", None],
    )


class DocumentListSchema(BaseSchema):
    """One entry of the bundled-document listing.

    Attributes:
        name (str): Name to pass as the path parameter.
        schema_name (str): Python class validating its payload.
        json_schema (dict[str, Any]): The JSON Schema of that class, so a
            client can build a form without reading the SDK.
    """

    name: str = Field(
        title="Nome",
        description="Value for the ``document`` path parameter.",
        examples=["receipt", "quote"],
    )
    schema_name: str = Field(
        title="Schema",
        description="Class that validates this document's payload.",
        examples=["ReceiptDocument"],
    )
    json_schema: dict[str, Any] = Field(
        title="JSON Schema",
        description="Full JSON Schema of the payload.",
    )


def safe_filename(name: str | None, *, default: str) -> str:
    """Reduce a caller-supplied name to something safe in a header.

    A filename reaches ``Content-Disposition``, where a quote ends the
    quoted string and a newline splits the response into two. Directory
    separators matter less here — nothing writes the file server-side —
    but a name carrying them is never what the caller meant either.

    Args:
        name (str | None): The requested name.
        default (str): Used when ``name`` is missing or reduces to
            nothing.

    Returns:
        str: A name safe to interpolate, always ending in ``.pdf``.
    """
    if not name:
        return default
    cleaned = "".join(
        char for char in name if char.isalnum() or char in {"-", "_", ".", " "}
    ).strip()
    if not cleaned or cleaned in {".", ".."}:
        return default
    return cleaned if cleaned.lower().endswith(".pdf") else f"{cleaned}.pdf"


def make_pdf_router(
    renderer: PdfRenderer,
    *,
    prefix: str = "/pdf",
    tags: list[str] | None = None,
    dependencies: Sequence[Any] | None = None,
    inline: bool = False,
    docs_locale: str | None = None,
) -> APIRouter:
    """Build the router that renders the bundled documents.

    Args:
        renderer (PdfRenderer): The configured renderer. Its asset
            policy and template directory apply to every route here.
        prefix (str): URL prefix. Defaults to ``"/pdf"``.
        tags (list[str] | None): OpenAPI tags. Defaults to ``["pdf"]``.
        dependencies (Sequence[Any] | None): FastAPI dependencies applied
            to every route — this is where auth and rate limiting go. The
            router adds none by itself.
        inline (bool): Serve with ``Content-Disposition: inline`` so the
            browser displays the document instead of downloading it.
        docs_locale (str | None): Language of the OpenAPI ``summary``
            and ``description`` text — ``"en-US"`` or ``"pt-BR"``.
            ``None`` (default) reads ``API_DOCS_LOCALE`` through
            :class:`~tempest_fastapi_sdk.settings.ServerSettings`,
            whose default is ``"en-US"``.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.
    """
    route_docs: RouteDocs = RouteDocs(docs_locale)
    router = APIRouter(
        prefix=prefix,
        tags=list(tags or ["pdf"]),
        dependencies=list(dependencies or []),
    )
    disposition = "inline" if inline else "attachment"

    @router.get(
        "/documents",
        response_model=list[DocumentListSchema],
        summary=route_docs.summary("pdf.list_documents"),
        description=route_docs.description("pdf.list_documents"),
    )
    async def list_documents() -> list[DocumentListSchema]:
        """Return the bundled documents with their schemas.

        Returns:
            list[DocumentListSchema]: One entry per bundled document,
            sorted by name.
        """
        return [
            DocumentListSchema(
                name=name,
                schema_name=BUNDLED_DOCUMENTS[name].__name__,
                json_schema=BUNDLED_DOCUMENTS[name].model_json_schema(),
            )
            for name in bundled_document_names()
        ]

    @router.post(
        "/documents/{document}",
        response_class=Response,
        status_code=status.HTTP_200_OK,
        responses={
            200: {
                "content": {PDF_MEDIA_TYPE: {}},
                "description": route_docs.text("pdf.render_document.200"),
            },
        },
        summary=route_docs.summary("pdf.render_document"),
        description=route_docs.description("pdf.render_document"),
    )
    async def render_document(
        document: str,
        request: DocumentRequestSchema,
    ) -> Response:
        """Render one bundled document.

        Args:
            document (str): Bundled document name.
            request (DocumentRequestSchema): Payload plus the suggested
                filename.

        Returns:
            Response: The PDF bytes with a ``Content-Disposition`` header.

        Raises:
            ValidationException: When ``payload`` does not match the
                selected document's schema. Converted explicitly:
                FastAPI turns a ``ValidationError`` into a 422 only for
                the models it declared itself, and this one is validated
                inside the body — left alone it escapes as a **500**,
                telling the caller nothing about the field they got
                wrong.
        """
        from pydantic import ValidationError

        schema = document_schema(document)
        try:
            parsed = schema.model_validate(request.payload)
        except ValidationError as exc:
            raise ValidationException(
                message=f"payload does not match {schema.__name__}",
                details={"errors": json.loads(exc.json(include_url=False))},
            ) from exc
        pdf = await renderer.render_document(parsed)
        filename = safe_filename(request.filename, default=f"{document}.pdf")
        return Response(
            content=pdf,
            media_type=PDF_MEDIA_TYPE,
            headers={
                "Content-Disposition": f'{disposition}; filename="{filename}"',
            },
        )

    return router


__all__: list[str] = [
    "PDF_MEDIA_TYPE",
    "DocumentListSchema",
    "DocumentRequestSchema",
    "make_pdf_router",
    "safe_filename",
]
