"""RAG context — feed LLMs with web search + PDF knowledge.

Retrieve grounding for a local LLM without shipping data to a third
party: search a self-hosted SearXNG, extract page bodies, read PDFs, and
assemble it all into a prompt-ready context block. The heavy bits
(``httpx`` / ``trafilatura`` / ``pymupdf``) live behind the
``[genai-rag]`` extra and import lazily, so this package imports without
it — you only need the extra to actually fetch and read.
"""

from tempest_fastapi_sdk.genai.rag.chunking import chunk_text as chunk_text
from tempest_fastapi_sdk.genai.rag.context import build_context as build_context
from tempest_fastapi_sdk.genai.rag.extract import (
    ContentExtractor as ContentExtractor,
)
from tempest_fastapi_sdk.genai.rag.extract import (
    ExtractionResult as ExtractionResult,
)
from tempest_fastapi_sdk.genai.rag.pdf import PdfReader as PdfReader
from tempest_fastapi_sdk.genai.rag.schemas import Chunk as Chunk
from tempest_fastapi_sdk.genai.rag.schemas import Document as Document
from tempest_fastapi_sdk.genai.rag.schemas import PdfPage as PdfPage
from tempest_fastapi_sdk.genai.rag.schemas import SearchResult as SearchResult
from tempest_fastapi_sdk.genai.rag.search import SearxngBackend as SearxngBackend
from tempest_fastapi_sdk.genai.rag.search import WebSearch as WebSearch
from tempest_fastapi_sdk.genai.rag.search import WebSearchBackend as WebSearchBackend

__all__: list[str] = [
    "Chunk",
    "ContentExtractor",
    "Document",
    "ExtractionResult",
    "PdfPage",
    "PdfReader",
    "SearchResult",
    "SearxngBackend",
    "WebSearch",
    "WebSearchBackend",
    "build_context",
    "chunk_text",
]
