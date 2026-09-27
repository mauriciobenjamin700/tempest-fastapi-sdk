"""The page base carries the document head: stylesheets, head markup, title (#352)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from tempest_core import Text, Widget

from tempest_fastapi_sdk.ssr import Page, html_response


class BasePage(Page):
    """A service base page declaring the shared document head."""

    stylesheets: ClassVar[Sequence[str]] = ("/static/app.css",)
    head: ClassVar[str] = '<link rel="icon" href="/static/favicon.ico">'
    title_suffix: ClassVar[str] = " · tempest-bucket"

    def body(self) -> Widget:
        return Text(content="base", tag="p")


class BucketsPage(BasePage):
    """A concrete screen inheriting the head from the base."""

    def body(self) -> Widget:
        return Text(content="buckets", tag="p")


class OverridingPage(BasePage):
    """A screen replacing the inherited class attributes without annotating."""

    stylesheets = ("/static/other.css",)
    head = '<meta name="robots" content="noindex">'


def _render(page: Page, **kwargs: Any) -> str:
    return bytes(html_response(page, **kwargs).body).decode()


def test_subclass_inherits_stylesheets_head_and_title_suffix() -> None:
    body = _render(BucketsPage(title="Buckets"))
    assert '<link rel="stylesheet" href="/static/app.css">' in body
    assert '<link rel="icon" href="/static/favicon.ico">' in body
    assert "<title>Buckets · tempest-bucket</title>" in body


def test_class_attributes_are_not_model_fields() -> None:
    assert "stylesheets" not in BucketsPage.model_fields
    assert "head" not in BucketsPage.model_fields
    assert "title_suffix" not in BucketsPage.model_fields


def test_subclass_can_override_without_annotation() -> None:
    body = _render(OverridingPage(title="Outra"))
    assert "/static/other.css" in body
    assert "/static/app.css" not in body
    assert 'content="noindex"' in body
    assert "favicon" not in body


def test_explicit_stylesheets_replace_the_page_list() -> None:
    body = _render(BucketsPage(title="Buckets"), stylesheets=["/x.css"])
    assert "/x.css" in body
    assert "/static/app.css" not in body


def test_explicit_empty_stylesheets_link_nothing() -> None:
    body = _render(BucketsPage(title="Buckets"), stylesheets=())
    assert 'rel="stylesheet"' not in body


def test_explicit_head_replaces_the_page_head() -> None:
    body = _render(BucketsPage(title="Buckets"), head="<meta name=x>")
    assert "<meta name=x>" in body
    assert "favicon" not in body


def test_explicit_title_is_used_verbatim() -> None:
    body = _render(BucketsPage(title="Buckets"), title="Outro título")
    assert "<title>Outro título</title>" in body


def test_document_title_can_be_overridden() -> None:
    class CountedPage(BasePage):
        count: int

        def document_title(self) -> str:
            return f"({self.count}) {self.title}"

    body = _render(CountedPage(title="Fila", count=3))
    assert "<title>(3) Fila</title>" in body


def test_title_is_escaped() -> None:
    body = _render(BucketsPage(title="<script>x</script>"))
    assert "<title>&lt;script&gt;" in body


def test_plain_page_keeps_the_old_defaults() -> None:
    class PlainPage(Page):
        def body(self) -> Widget:
            return Text(content="plain")

    body = _render(PlainPage(title="Plain"))
    assert "<title>Plain</title>" in body
    assert 'rel="stylesheet"' not in body


def test_fragment_ignores_the_page_head() -> None:
    body = _render(BucketsPage(title="Buckets"), document=False)
    assert "app.css" not in body
    assert "buckets" in body
