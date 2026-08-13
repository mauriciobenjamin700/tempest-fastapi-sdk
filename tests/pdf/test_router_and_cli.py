"""Tests for the PDF router and the ``tempest pdf`` commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.cli.main import app as cli_app
from tempest_fastapi_sdk.pdf import PdfRenderer, make_pdf_router, safe_filename

RECEIPT_PAYLOAD: dict[str, object] = {
    "number": "0001/2026",
    "issue_date": "2026-08-13",
    "issuer": {"name": "Acme LTDA", "document": "12345678000195"},
    "payer": {"name": "Ana Souza", "document": "12345678901"},
    "amount_cents": 125000,
    "reference": "consultoria de julho/2026",
    "place": "Recife",
}


def _app(**kwargs: object) -> FastAPI:
    """Build an app with the PDF router mounted.

    Args:
        **kwargs (object): Router keyword arguments.

    Returns:
        FastAPI: The application.
    """
    app = FastAPI()
    app.include_router(make_pdf_router(PdfRenderer(), **kwargs))  # type: ignore[arg-type]
    register_exception_handlers(app)
    return app


class TestSafeFilename:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, "fallback.pdf"),
            ("", "fallback.pdf"),
            ("recibo", "recibo.pdf"),
            ("recibo.pdf", "recibo.pdf"),
            ('evil"; rm -rf /', "evil rm -rf.pdf"),
            ("../../etc/passwd", "....etcpasswd.pdf"),
            ("..", "fallback.pdf"),
        ],
    )
    def test_reduces_to_something_safe_in_a_header(
        self,
        value: str | None,
        expected: str,
    ) -> None:
        """A quote ends the header value; a newline splits the response."""
        assert safe_filename(value, default="fallback.pdf") == expected

    def test_never_leaves_a_quote_or_newline(self) -> None:
        """The property, independent of the cases above."""
        for hostile in ('a"b', "a\nb", "a\r\nContent-Length: 0"):
            result = safe_filename(hostile, default="d.pdf")
            assert '"' not in result
            assert "\n" not in result
            assert "\r" not in result


class TestRouter:
    async def test_lists_documents_with_their_schemas(self) -> None:
        """A client should not have to read the SDK to build the form."""
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get("/pdf/documents")
        assert response.status_code == 200
        names = [row["name"] for row in response.json()]
        assert "receipt" in names
        receipt = next(row for row in response.json() if row["name"] == "receipt")
        assert receipt["schema_name"] == "ReceiptDocument"
        assert "amount_cents" in receipt["json_schema"]["properties"]

    async def test_renders_and_offers_a_download(self) -> None:
        """The whole point of the route."""
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post(
                "/pdf/documents/receipt",
                json={"payload": RECEIPT_PAYLOAD, "filename": "recibo-0001"},
            )
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"] == (
            'attachment; filename="recibo-0001.pdf"'
        )
        assert response.content.startswith(b"%PDF-")

    async def test_inline_mode_displays_instead_of_downloading(self) -> None:
        """Some callers want the document in a viewer, not on disk."""
        transport = ASGITransport(app=_app(inline=True))
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post(
                "/pdf/documents/receipt",
                json={"payload": RECEIPT_PAYLOAD},
            )
        assert response.headers["content-disposition"].startswith("inline;")

    async def test_unknown_document_is_a_404_naming_the_alternatives(self) -> None:
        """A typo should be one read away from fixed."""
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post(
                "/pdf/documents/nota",
                json={"payload": {}},
            )
        assert response.status_code == 404
        assert "receipt" in response.json()["detail"]

    async def test_an_invalid_payload_names_the_field(self) -> None:
        """The error has to point at the document's own schema."""
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post(
                "/pdf/documents/receipt",
                json={"payload": {**RECEIPT_PAYLOAD, "amount_cents": -1}},
            )
        assert response.status_code == 422
        assert "amount_cents" in json.dumps(response.json())

    async def test_dependencies_are_applied_to_every_route(self) -> None:
        """This is where a project's auth and rate limit go."""
        from fastapi import Depends, HTTPException

        def _deny() -> None:
            raise HTTPException(status_code=403, detail="nope")

        transport = ASGITransport(app=_app(dependencies=[Depends(_deny)]))
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            listing = await client.get("/pdf/documents")
            render = await client.post(
                "/pdf/documents/receipt",
                json={"payload": RECEIPT_PAYLOAD},
            )
        assert listing.status_code == 403
        assert render.status_code == 403


class TestCli:
    def test_list_names_every_bundled_document(self) -> None:
        """The command that tells you what the other ones accept."""
        result = CliRunner().invoke(cli_app, ["pdf", "list"])
        assert result.exit_code == 0
        for name in ("receipt", "quote", "report", "contract", "voucher"):
            assert name in result.stdout

    def test_schema_prints_json(self) -> None:
        """Enough to build a payload without reading the source."""
        result = CliRunner().invoke(cli_app, ["pdf", "schema", "receipt"])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert "amount_cents" in parsed["properties"]

    def test_schema_rejects_an_unknown_document(self) -> None:
        """Exit 2 with the available names, never a traceback."""
        result = CliRunner().invoke(cli_app, ["pdf", "schema", "nota"])
        assert result.exit_code == 2
        assert "available" in result.output

    def test_render_writes_a_pdf(self, tmp_path: Path) -> None:
        """The command that closes the template-editing loop."""
        data = tmp_path / "data.json"
        data.write_text(json.dumps(RECEIPT_PAYLOAD), encoding="utf-8")
        out = tmp_path / "recibo.pdf"
        result = CliRunner().invoke(
            cli_app,
            ["pdf", "render", "receipt", str(data), "-o", str(out)],
        )
        assert result.exit_code == 0, result.output
        assert out.read_bytes().startswith(b"%PDF-")

    def test_render_html_skips_layout(self, tmp_path: Path) -> None:
        """The fast path for iterating on a template in a browser."""
        data = tmp_path / "data.json"
        data.write_text(json.dumps(RECEIPT_PAYLOAD), encoding="utf-8")
        out = tmp_path / "preview.html"
        result = CliRunner().invoke(
            cli_app,
            ["pdf", "render", "receipt", str(data), "--html", "-o", str(out)],
        )
        assert result.exit_code == 0, result.output
        assert out.read_text(encoding="utf-8").lstrip().startswith("<!DOCTYPE html>")

    def test_render_reports_a_bad_payload_with_the_field(
        self,
        tmp_path: Path,
    ) -> None:
        """A validation error names what to fix."""
        data = tmp_path / "data.json"
        data.write_text('{"amount_cents": -5}', encoding="utf-8")
        result = CliRunner().invoke(cli_app, ["pdf", "render", "receipt", str(data)])
        assert result.exit_code == 2
        assert "issue_date" in result.output

    def test_render_reports_unparseable_json(self, tmp_path: Path) -> None:
        """A JSON error should not surface as a traceback."""
        data = tmp_path / "data.json"
        data.write_text("{not json", encoding="utf-8")
        result = CliRunner().invoke(cli_app, ["pdf", "render", "receipt", str(data)])
        assert result.exit_code == 2
        assert "not valid JSON" in result.output

    def test_render_reports_a_missing_file(self, tmp_path: Path) -> None:
        """The most common mistake gets the clearest message."""
        result = CliRunner().invoke(
            cli_app,
            ["pdf", "render", "receipt", str(tmp_path / "absent.json")],
        )
        assert result.exit_code == 2
        assert "not found" in result.output

    def test_render_rejects_a_json_array(self, tmp_path: Path) -> None:
        """A list would fail deep inside pydantic with a worse message."""
        data = tmp_path / "data.json"
        data.write_text("[1, 2, 3]", encoding="utf-8")
        result = CliRunner().invoke(cli_app, ["pdf", "render", "receipt", str(data)])
        assert result.exit_code == 2
        assert "JSON object" in result.output
