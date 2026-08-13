"""``tempest pdf`` — render the bundled documents from the command line.

Laying out a document is a loop of *change the template, look at the
page*, and going through the service for every iteration is the slow way
to do it. These commands close that loop:

    tempest pdf list
    tempest pdf schema receipt
    tempest pdf render receipt data.json -o recibo.pdf
    tempest pdf render receipt data.json --html -o preview.html

``--html`` stops before layout and writes the HTML, which opens in a
browser and reloads instantly — the fastest way to iterate on a template
before checking how it actually paginates.

Needs the ``[pdf]`` extra. A missing extra exits 2 with the install line,
never a traceback.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

pdf_app: typer.Typer = typer.Typer(
    name="pdf",
    help="Render the bundled PDF documents from a JSON payload.",
    no_args_is_help=True,
)


def _fail(message: str) -> None:
    """Print an error and exit with the CLI's validation code.

    Args:
        message (str): Message shown to the user, lowercase, saying how
            to fix the problem.

    Raises:
        typer.Exit: Always, with code 2.
    """
    typer.secho(f"error: {message}", fg="red", err=True)
    raise typer.Exit(2)


def _require_extra() -> None:
    """Exit with the install line when the ``[pdf]`` extra is absent.

    Raises:
        typer.Exit: With code 2 when WeasyPrint or Jinja2 is missing.
    """
    try:
        import jinja2  # noqa: F401
        import weasyprint  # noqa: F401
    except ImportError:
        _fail(
            'PDF rendering needs the [pdf] extra: uv add "tempest-fastapi-sdk[pdf]"\n'
            "  It also needs Pango and fontconfig from the system — on "
            "Debian/Ubuntu:\n"
            "    apt-get install libpango-1.0-0 libpangoft2-1.0-0 "
            "libharfbuzz0b fontconfig fonts-dejavu-core",
        )


def _load_payload(path: Path) -> dict[str, Any]:
    """Read and parse the JSON payload.

    Args:
        path (Path): File holding the document's fields.

    Returns:
        dict[str, Any]: The parsed mapping.

    Raises:
        typer.Exit: When the file is missing, unparseable, or holds
            something other than a JSON object.
    """
    if not path.is_file():
        _fail(f"{path} not found")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"{path} is not valid JSON: {exc}")
    if not isinstance(parsed, dict):
        _fail(f"{path} must hold a JSON object, got {type(parsed).__name__}")
    return dict(parsed)


@pdf_app.command("list")
def list_documents() -> None:
    """List the bundled documents and the schema validating each one."""
    from tempest_fastapi_sdk.pdf.documents import BUNDLED_DOCUMENTS
    from tempest_fastapi_sdk.pdf.renderer import bundled_document_names

    for name in bundled_document_names():
        schema = BUNDLED_DOCUMENTS[name]
        summary = (schema.__doc__ or "").strip().splitlines()[0]
        typer.echo(f"{name:10} {schema.__name__:20} {summary}")


@pdf_app.command("schema")
def show_schema(
    document: Annotated[
        str,
        typer.Argument(help="Document name, from `tempest pdf list`."),
    ],
) -> None:
    """Print the JSON Schema of a document's payload.

    Args:
        document (str): Bundled document name.

    Raises:
        typer.Exit: With code 2 when the name is unknown.
    """
    from tempest_fastapi_sdk.pdf.renderer import TemplateNotFound, document_schema

    try:
        schema = document_schema(document)
    except TemplateNotFound as exc:
        _fail(str(exc.detail))
    typer.echo(json.dumps(schema.model_json_schema(), indent=2, ensure_ascii=False))


@pdf_app.command("render")
def render(
    document: Annotated[
        str,
        typer.Argument(help="Document name, from `tempest pdf list`."),
    ],
    data: Annotated[
        Path,
        typer.Argument(help="JSON file holding the document's fields."),
    ],
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Where to write. Defaults to <document>.pdf."),
    ] = None,
    template_dir: Annotated[
        Path | None,
        typer.Option(
            "--template-dir",
            help="Directory whose templates shadow the bundled ones.",
        ),
    ] = None,
    html: Annotated[
        bool,
        typer.Option(
            "--html",
            help="Write the rendered HTML instead of a PDF, to preview in a browser.",
        ),
    ] = False,
) -> None:
    """Render a bundled document from a JSON payload.

    Args:
        document (str): Bundled document name.
        data (Path): JSON file with the document's fields.
        out (Path | None): Output path. Defaults to ``<document>.pdf``
            (or ``.html`` with ``--html``).
        template_dir (Path | None): Templates that shadow the bundled
            ones, so a project previews its own overrides.
        html (bool): Stop before layout and write HTML.

    Raises:
        typer.Exit: With code 2 on a missing extra, an unknown document,
            an unreadable payload, or a payload that fails validation —
            the validation error names the offending field.
    """
    import asyncio

    if not html:
        _require_extra()
    from pydantic import ValidationError

    from tempest_fastapi_sdk.pdf.renderer import (
        PdfRenderer,
        TemplateNotFound,
        document_schema,
    )

    try:
        schema = document_schema(document)
    except TemplateNotFound as exc:
        _fail(str(exc.detail))
    payload = _load_payload(data)
    try:
        parsed = schema.model_validate(payload)
    except ValidationError as exc:
        _fail(f"{data} does not match the {schema.__name__} schema:\n{exc}")

    if template_dir is not None and not template_dir.is_dir():
        _fail(f"--template-dir {template_dir} is not a directory")
    renderer = PdfRenderer(template_dir=template_dir)
    suffix = "html" if html else "pdf"
    destination = out or Path(f"{document}.{suffix}")

    if html:
        rendered = renderer.render_html_string(type(parsed).template, {"doc": parsed})
        destination.write_text(rendered, encoding="utf-8")
    else:
        destination.write_bytes(asyncio.run(renderer.render_document(parsed)))

    size = destination.stat().st_size
    typer.secho(f"wrote {destination} ({size:,} bytes)", fg="green")


__all__: list[str] = [
    "pdf_app",
]
