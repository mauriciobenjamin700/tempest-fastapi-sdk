"""Tests for the Mermaid step that run without a browser.

Rendering a diagram needs Chromium, which neither the dev environment nor
CI is guaranteed to have, so the browser itself is replaced at the one
seam the module calls (``_render_all``). What is held here is everything
around it: blocks are found and replaced in order, a failed diagram
becomes an escaped error box, and text without diagrams never starts a
browser.
"""

from __future__ import annotations

import builtins
from typing import Any

import pytest

from tempest_fastapi_sdk.slides import mermaid
from tempest_fastapi_sdk.slides.mermaid import (
    fit_svg_to_page,
    mermaid_error_html,
    render_mermaid_blocks,
)

TWO_DIAGRAMS: str = (
    "Intro\n\n```mermaid\ngraph TD; A-->B\n```\n\nMiddle\n\n"
    "```mermaid\nnot valid <script>\n```\n\nEnd\n"
)


class FakeRenderer:
    """Stands in for ``_render_all``, recording what it was asked.

    Attributes:
        calls (list[list[str]]): Sources passed on each call.
        answers (list[str | None]): What each diagram renders to.
    """

    def __init__(self, answers: list[str | None]) -> None:
        """Store the canned answers.

        Args:
            answers (list[str | None]): One per diagram.
        """
        self.calls: list[list[str]] = []
        self.answers: list[str | None] = answers

    async def __call__(self, sources: list[str], timeout: float) -> list[str | None]:
        """Record the call and answer.

        Args:
            sources (list[str]): Diagram sources.
            timeout (float): Ignored.

        Returns:
            list[str | None]: The canned answers.
        """
        self.calls.append(sources)
        return self.answers


class TestRenderMermaidBlocks:
    async def test_text_without_diagrams_starts_no_browser(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No block, no call."""
        fake = FakeRenderer([])
        monkeypatch.setattr(mermaid, "_render_all", fake)
        assert await render_mermaid_blocks("# Plain", error_message="x") == "# Plain"
        assert fake.calls == []

    async def test_blocks_are_replaced_in_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A rendered diagram becomes SVG, a failed one an escaped box."""
        fake = FakeRenderer(['<svg width="100%" viewBox="0 0 900 100"></svg>', None])
        monkeypatch.setattr(mermaid, "_render_all", fake)
        output = await render_mermaid_blocks(TWO_DIAGRAMS, error_message="Falhou")
        assert fake.calls == [["graph TD; A-->B\n", "not valid <script>\n"]]
        assert (
            output.index("Intro")
            < output.index("mermaid-diagram")
            < output.index("Middle")
        )
        assert '<div class="mermaid-error"><strong>Falhou</strong>' in output
        assert "&lt;script&gt;" in output
        assert "<script>" not in output
        assert output.rstrip().endswith("End")

    async def test_missing_playwright_degrades_to_error_boxes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without the extra, every diagram becomes the error box."""
        real_import = builtins.__import__

        def refuse_playwright(name: str, *args: Any, **kwargs: Any) -> Any:
            """Fail the Playwright import only.

            Args:
                name (str): Module name.
                *args (Any): Forwarded.
                **kwargs (Any): Forwarded.

            Returns:
                Any: The imported module.

            Raises:
                ImportError: For ``playwright``.
            """
            if name.startswith("playwright"):
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse_playwright)
        output = await render_mermaid_blocks(
            TWO_DIAGRAMS, error_message="Sem navegador"
        )
        assert output.count("Sem navegador") == 2


class TestHelpers:
    def test_svg_wider_than_the_page_is_capped(self) -> None:
        """Width comes from the viewBox, capped at the printable width."""
        svg = '<svg width="100%" style="max-width: 900px;" viewBox="0 0 900 50"></svg>'
        fitted = fit_svg_to_page(svg)
        assert 'width="642px"' in fitted
        assert "max-width" not in fitted

    def test_narrow_svg_keeps_its_width(self) -> None:
        """A small diagram is not stretched."""
        assert 'width="300px"' in fit_svg_to_page(
            '<svg width="100%" viewBox="0 0 300 50">'
        )

    def test_error_box_escapes_message_and_source(self) -> None:
        """Both values are user-controlled."""
        box = mermaid_error_html("<b>src</b>", "<i>msg</i>")
        assert "<b>" not in box
        assert "<i>" not in box
