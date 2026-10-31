"""Replace fenced Mermaid blocks in Markdown with inline SVG.

WeasyPrint does not run JavaScript, so a ```` ```mermaid ```` block would
reach the PDF as source code. This step renders each block in headless
Chromium through Playwright and splices the resulting SVG into the
Markdown before it is converted. A block that cannot be rendered — bad
syntax, Playwright or Chromium missing, timeout — becomes a styled box
holding the escaped source, so the document still renders and says where
the diagram should have been.

Ported from ``mermaid_service.py`` of Leviathan's ``slide-generator``, with
two changes: one browser renders every block of a document instead of one
browser per block, and the Mermaid script is pinned to an exact version
instead of the floating ``mermaid@10``.

Needs the ``[slides-mermaid]`` extra **and** a browser:
``playwright install chromium``. Chromium fetches the Mermaid script from
:data:`MERMAID_JS_URL` on every render; that request is made by the
browser, outside the asset policy that governs the PDF itself.
"""

from __future__ import annotations

import base64
import html
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

MERMAID_BLOCK_RE: re.Pattern[str] = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)
"""A fenced Mermaid block; group 1 is the diagram source."""

MERMAID_JS_URL: str = "https://cdn.jsdelivr.net/npm/mermaid@10.9.8/dist/mermaid.min.js"
"""Mermaid build loaded by the headless page. ``10.9.8`` is what the
original's ``mermaid@10`` resolved to on jsDelivr when this was ported."""

DEFAULT_MERMAID_TIMEOUT: float = 15.0
"""Seconds one diagram has to render."""

PAGE_WIDTH_PX: float = 642.0
"""Usable width of an A4 page with 2 cm side margins, in CSS pixels; wider
diagrams are scaled down to it."""

_HOST_PAGE: str = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><script src="{script}"></script></head>
<body><script>
mermaid.initialize({{
    startOnLoad: false,
    securityLevel: "strict",
    theme: "default",
    htmlLabels: false,
    flowchart: {{ htmlLabels: false }},
    sequence: {{ htmlLabels: false }}
}});
</script></body></html>"""

_RENDER_SCRIPT: str = """async (source) => {
    const { svg } = await mermaid.render("mermaid-diagram", source);
    return svg;
}"""

_LOGGER: logging.Logger = logging.getLogger(__name__)


def fit_svg_to_page(svg: str) -> str:
    """Cap a Mermaid SVG's width at the printable width of the page.

    Mermaid emits ``width="100%"`` plus an inline ``max-width`` that
    WeasyPrint resolves against the page box, so a wide flowchart runs off
    the margin. The width is replaced by the ``viewBox`` width, capped at
    :data:`PAGE_WIDTH_PX`.

    Args:
        svg (str): The SVG markup.

    Returns:
        str: The SVG with an explicit pixel width.
    """
    svg = re.sub(r'\sstyle="max-width:\s*[\d.]+px;"', "", svg)
    view_box = re.search(r'viewBox="[\d.\-]+\s+[\d.\-]+\s+([\d.]+)\s+([\d.]+)"', svg)
    if view_box:
        width = min(float(view_box.group(1)), PAGE_WIDTH_PX)
        svg = re.sub(r'width="100%"', f'width="{width:.0f}px"', svg)
    return svg


def mermaid_error_html(source: str, message: str) -> str:
    """Build the box that stands in for a diagram that did not render.

    Args:
        source (str): The diagram source.
        message (str): The sentence shown above it.

    Returns:
        str: HTML with both values escaped.
    """
    return (
        f'<div class="mermaid-error"><strong>{html.escape(message)}</strong>'
        f"<pre>{html.escape(source.strip())}</pre></div>"
    )


async def _render_all(sources: list[str], timeout: float) -> list[str | None]:
    """Render every diagram in one browser session.

    Args:
        sources (list[str]): Diagram sources, in document order.
        timeout (float): Seconds each diagram has.

    Returns:
        list[str | None]: The SVG of each diagram, ``None`` where it failed.
        All ``None`` when Playwright or Chromium is unavailable.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        _LOGGER.warning("Mermaid rendering skipped: playwright is not installed")
        return [None] * len(sources)
    page_html = _HOST_PAGE.format(script=MERMAID_JS_URL)
    encoded = base64.b64encode(page_html.encode("utf-8")).decode("ascii")
    results: list[str | None] = []
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                page.set_default_timeout(timeout * 1000)
                await page.goto(
                    f"data:text/html;base64,{encoded}", wait_until="networkidle"
                )
                for source in sources:
                    results.append(await _render_one(page, source))
            finally:
                await browser.close()
    except Exception as exc:
        _LOGGER.warning(
            "Mermaid rendering failed", extra={"error_type": type(exc).__name__}
        )
    return results + [None] * (len(sources) - len(results))


async def _render_one(page: Page, source: str) -> str | None:
    """Render one diagram on an already loaded page.

    Args:
        page (Page): The Playwright page holding Mermaid.
        source (str): The diagram source.

    Returns:
        str | None: The SVG, or ``None`` when Mermaid rejected the source.
    """
    try:
        svg = await page.evaluate(_RENDER_SCRIPT, source.strip())
    except Exception as exc:
        _LOGGER.info(
            "Mermaid diagram rejected", extra={"error_type": type(exc).__name__}
        )
        return None
    if not isinstance(svg, str) or "<svg" not in svg.lower():
        return None
    return svg


async def render_mermaid_blocks(
    markdown_text: str,
    *,
    error_message: str,
    timeout: float = DEFAULT_MERMAID_TIMEOUT,
) -> str:
    """Replace every fenced Mermaid block with its SVG, or an error box.

    Args:
        markdown_text (str): The Markdown source.
        error_message (str): Sentence printed in the box of a diagram that
            did not render.
        timeout (float): Seconds each diagram has.

    Returns:
        str: The Markdown, Mermaid blocks replaced by raw HTML. Text with
        no Mermaid block is returned unchanged, without starting a browser.
    """
    matches = list(MERMAID_BLOCK_RE.finditer(markdown_text))
    if not matches:
        return markdown_text
    rendered = await _render_all([match.group(1) for match in matches], timeout)
    pieces: list[str] = []
    cursor = 0
    for match, svg in zip(matches, rendered, strict=True):
        pieces.append(markdown_text[cursor : match.start()])
        if svg is None:
            pieces.append(mermaid_error_html(match.group(1), error_message))
        else:
            pieces.append(f'<div class="mermaid-diagram">{fit_svg_to_page(svg)}</div>')
        cursor = match.end()
    pieces.append(markdown_text[cursor:])
    return "".join(pieces)
