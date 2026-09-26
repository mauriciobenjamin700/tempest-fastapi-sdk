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
from tempest_fastapi_sdk.utils.http_client import HTTPClient, RetryPolicy
from tests.genai.conftest import public_resolver


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

        client = HTTPClient(transport=httpx.MockTransport(handler))
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

        client = HTTPClient(transport=httpx.MockTransport(handler))
        backend = SearxngBackend("http://searx", http_client=client)
        results = await backend.search("q", max_results=3)
        await client.aclose()
        assert len(results) == 3

    async def test_http_error_raises_runtimeerror(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        client = HTTPClient(
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=1),
        )
        backend = SearxngBackend("http://searx", http_client=client)
        with pytest.raises(RuntimeError, match="SearXNG"):
            await backend.search("q", max_results=5)
        await client.aclose()

    async def test_websearch_facade_delegates(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": [{"url": "http://a"}]})

        client = HTTPClient(transport=httpx.MockTransport(handler))
        search = WebSearch(SearxngBackend("http://searx", http_client=client))
        results = await search.search("q")
        await client.aclose()
        assert results[0].url == "http://a"


class TestContentExtractor:
    async def test_failed_fetch_is_not_raised(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        extractor = ContentExtractor(http_client=client, resolver=public_resolver)
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
        extractor = ContentExtractor(http_client=client, resolver=public_resolver)
        result = await extractor.extract("http://x")
        await client.aclose()
        assert result.failed is False
        assert result.text == "clean body"


class TestContentExtractorSSRF:
    """The fetch refuses internal destinations and oversized bodies."""

    async def test_redirect_to_metadata_endpoint_is_refused(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            if request.url.host == "93.184.216.34":
                return httpx.Response(
                    302,
                    headers={
                        "Location": "http://169.254.169.254/latest/meta-data/iam/"
                    },
                )
            return httpx.Response(200, text="<html><body>AKIA-SECRET</body></html>")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        extractor = ContentExtractor(http_client=client)
        result = await extractor.extract("http://93.184.216.34/article")
        await client.aclose()
        assert result.failed is True
        assert result.text == ""
        assert seen == ["http://93.184.216.34/article"]

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/",
            "http://10.0.0.5/",
            "http://192.168.1.1/",
            "http://172.16.0.1/",
            "http://100.64.0.1/",
            "http://0.0.0.0/",
            "http://224.0.0.1/",
            "http://240.0.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "http://[fe80::1]/",
            "http://[fd00::1]/",
            "http://[::ffff:127.0.0.1]/",
        ],
    )
    async def test_private_literal_is_never_requested(self, url: str) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, text="<html>internal</html>")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        extractor = ContentExtractor(http_client=client)
        result = await extractor.extract(url)
        await client.aclose()
        assert result.failed is True
        assert seen == []

    async def test_hostname_resolving_to_private_address_is_refused(self) -> None:
        seen: list[str] = []

        async def internal_resolver(host: str) -> list[str]:
            return ["93.184.216.34", "10.1.2.3"]

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, text="<html>internal</html>")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        extractor = ContentExtractor(http_client=client, resolver=internal_resolver)
        result = await extractor.extract("http://intranet.example/")
        await client.aclose()
        assert result.failed is True
        assert seen == []

    async def test_localhost_is_refused_by_the_system_resolver(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, text="<html>admin</html>")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        extractor = ContentExtractor(http_client=client)
        result = await extractor.extract("http://localhost:8080/admin")
        await client.aclose()
        assert result.failed is True
        assert seen == []

    @pytest.mark.parametrize(
        "url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://x/"]
    )
    async def test_non_http_scheme_is_refused(self, url: str) -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, text="x"))
        )
        extractor = ContentExtractor(http_client=client, resolver=public_resolver)
        result = await extractor.extract(url)
        await client.aclose()
        assert result.failed is True

    async def test_redirect_to_non_http_scheme_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"Location": "file:///etc/passwd"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        extractor = ContentExtractor(http_client=client, resolver=public_resolver)
        result = await extractor.extract("http://x/")
        await client.aclose()
        assert result.failed is True

    async def test_public_redirect_is_followed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trafilatura

        monkeypatch.setattr(trafilatura, "extract", lambda html: html)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/old":
                return httpx.Response(301, headers={"Location": "/new"})
            return httpx.Response(200, text="moved body")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        extractor = ContentExtractor(http_client=client, resolver=public_resolver)
        result = await extractor.extract("http://x/old")
        await client.aclose()
        assert result.failed is False
        assert result.text == "moved body"

    async def test_redirect_loop_stops_at_max_redirects(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(302, headers={"Location": request.url.path + "x"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        extractor = ContentExtractor(
            http_client=client, resolver=public_resolver, max_redirects=3
        )
        result = await extractor.extract("http://x/a")
        await client.aclose()
        assert result.failed is True
        assert len(seen) == 4

    async def test_oversized_body_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trafilatura

        calls: list[str] = []
        monkeypatch.setattr(trafilatura, "extract", lambda html: calls.append(html))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"a" * 4096)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        extractor = ContentExtractor(
            http_client=client, resolver=public_resolver, max_response_bytes=1024
        )
        result = await extractor.extract("http://x/")
        await client.aclose()
        assert result.failed is True
        assert calls == []

    async def test_allow_private_networks_opts_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trafilatura

        monkeypatch.setattr(trafilatura, "extract", lambda html: html)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="intranet wiki")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        extractor = ContentExtractor(http_client=client, allow_private_networks=True)
        result = await extractor.extract("http://10.0.0.5/wiki")
        await client.aclose()
        assert result.text == "intranet wiki"

    async def test_extraction_runs_off_the_event_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import threading

        import trafilatura

        loop_thread = threading.get_ident()
        threads: list[int] = []

        def fake_extract(html: str) -> str:
            threads.append(threading.get_ident())
            return "body"

        monkeypatch.setattr(trafilatura, "extract", fake_extract)
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, text="<p>"))
        )
        extractor = ContentExtractor(http_client=client, resolver=public_resolver)
        await extractor.extract("http://x/")
        await client.aclose()
        assert threads
        assert threads[0] != loop_thread


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
        chunks = PdfReader().chunks(str(pdf), max_chars=50, overlap=10)
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
