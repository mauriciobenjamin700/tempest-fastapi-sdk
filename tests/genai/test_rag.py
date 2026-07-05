"""Tests for the GenAI RAG context layer (web search + PDF + context)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tempest_fastapi_sdk.genai.rag import (
    Chunk,
    ContentExtractor,
    PdfReader,
    SearchResult,
    SearxngBackend,
    WebSearch,
    build_context,
)


class TestSearxngBackend:
    async def test_parses_results(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["format"] == "json"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "PIX",
                            "url": "http://a",
                            "content": "snip",
                            "score": 1.0,
                        },
                        {"title": "No URL"},  # skipped (no url)
                        {"title": "B", "url": "http://b", "content": "x"},
                    ]
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        backend = SearxngBackend("http://searx:8080", http_client=client)
        results = await backend.search("pix", max_results=5)
        await client.aclose()

        assert [r.url for r in results] == ["http://a", "http://b"]
        assert results[0].title == "PIX"
        assert results[0].snippet == "snip"

    async def test_respects_max_results(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"results": [{"url": f"http://{i}"} for i in range(10)]},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        backend = SearxngBackend("http://searx", http_client=client)
        results = await backend.search("q", max_results=3)
        await client.aclose()
        assert len(results) == 3

    async def test_http_error_raises_runtimeerror(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        backend = SearxngBackend("http://searx", http_client=client)
        with pytest.raises(RuntimeError, match="SearXNG"):
            await backend.search("q", max_results=5)
        await client.aclose()

    async def test_websearch_facade_delegates(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": [{"url": "http://a"}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        search = WebSearch(SearxngBackend("http://searx", http_client=client))
        results = await search.search("q")
        await client.aclose()
        assert results[0].url == "http://a"


class TestContentExtractor:
    async def test_failed_fetch_is_not_raised(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        extractor = ContentExtractor(http_client=client)
        result = await extractor.extract("http://x")
        await client.aclose()
        assert result.failed is True
        assert result.text == ""

    async def test_extracts_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import trafilatura

        monkeypatch.setattr(trafilatura, "extract", lambda _html: "clean body")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html><body>...</body></html>")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        extractor = ContentExtractor(http_client=client)
        result = await extractor.extract("http://x")
        await client.aclose()
        assert result.failed is False
        assert result.text == "clean body"


class TestPdfReader:
    def _make_pdf(self, path: str, text: str) -> None:
        import pymupdf

        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), text)
        doc.save(path)
        doc.close()

    def test_read_extracts_text(self, tmp_path: Path) -> None:
        pdf = tmp_path / "k.pdf"
        self._make_pdf(str(pdf), "Hello PDF knowledge base")
        doc = PdfReader().read(str(pdf))
        assert "Hello PDF knowledge base" in doc.text
        assert len(doc.pages) == 1
        assert doc.pages[0].number == 1

    def test_chunks_carry_page(self, tmp_path: Path) -> None:
        pdf = tmp_path / "k.pdf"
        self._make_pdf(str(pdf), "some content here")
        chunks = PdfReader().chunks(str(pdf), max_chars=50)
        assert chunks
        assert chunks[0].page == 1
        assert chunks[0].source == str(pdf)


class TestBuildContext:
    def test_empty_sources(self) -> None:
        out = build_context("q?", [])
        assert "No sources" in out

    def test_renders_sources_with_labels(self) -> None:
        sources = [
            SearchResult(title="PIX", url="http://a", content="full body"),
            Chunk(text="pdf slice", source="/k.pdf", index=0, page=2),
        ]
        out = build_context("what is pix?", sources)
        assert "what is pix?" in out
        assert "http://a" in out
        assert "full body" in out
        assert "/k.pdf (page 2)" in out
        assert out.count("---") >= 3  # delimiters around 2 sources

    def test_truncates_when_not_long_text(self) -> None:
        sources = [SearchResult(url="http://a", content="x" * 5000)]
        out = build_context("q", sources, long_text=False, max_chars=100)
        assert "…" in out
        assert "x" * 5000 not in out
