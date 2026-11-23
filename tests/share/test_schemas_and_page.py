"""The share page escapes every value and refuses unsafe URLs."""

import pytest
from pydantic import ValidationError

from tempest_fastapi_sdk.share import SharePreviewSchema, render_share_page

PAYLOAD: str = "\"><script>alert('x')</script>&"
"""Breaks out of an attribute and of a text node when not escaped."""

URL_FIELDS: tuple[str, ...] = ("redirect_url", "canonical_url", "image_url")
"""Every URL field of :class:`SharePreviewSchema`."""


def _preview(**overrides: str | None) -> SharePreviewSchema:
    """Build a valid preview with ``overrides`` applied.

    Args:
        **overrides (str | None): Field values to replace.

    Returns:
        SharePreviewSchema: The preview.
    """
    values: dict[str, str | None] = {
        "title": "Carona",
        "description": "Sexta",
        "redirect_url": "https://app.example.com/c/1",
    }
    values.update(overrides)
    return SharePreviewSchema.model_validate(values)


def _text_fields() -> list[str]:
    """List the ``str`` fields of the preview that are not URLs.

    Derived from ``model_fields``, so a field added later is covered by
    the escape test without editing it.

    Returns:
        list[str]: Field names.
    """
    names: list[str] = []
    for name, field in SharePreviewSchema.model_fields.items():
        if name in URL_FIELDS:
            continue
        if field.annotation in (str, str | None):
            names.append(name)
    return names


class TestUrlValidation:
    """URL fields accept only absolute ``http``/``https``."""

    @pytest.mark.parametrize("field", URL_FIELDS)
    @pytest.mark.parametrize(
        "value",
        [
            "javascript:alert(1)",
            "JaVaScRiPt:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "/c/1",
            "//evil.example.com/c/1",
            "https://",
        ],
    )
    def test_refuses(self, field: str, value: str) -> None:
        """Script, data, relative and host-less URLs are refused."""
        with pytest.raises(ValidationError):
            _preview(**{field: value})

    @pytest.mark.parametrize("field", URL_FIELDS)
    def test_accepts_absolute_https(self, field: str) -> None:
        """An absolute ``https`` URL passes unchanged."""
        preview = _preview(**{field: "https://app.example.com/c/1?a=1&b=2"})
        assert getattr(preview, field) == "https://app.example.com/c/1?a=1&b=2"


class TestEscape:
    """Every interpolated value reaches the HTML escaped."""

    def test_covers_text_fields(self) -> None:
        """The derived field list is not empty, so the test is not vacuous."""
        assert {"title", "description", "image_alt", "version"} <= set(_text_fields())

    @pytest.mark.parametrize("field", _text_fields())
    def test_preview_field(self, field: str) -> None:
        """A text field carrying markup never appears raw."""
        page = render_share_page(
            _preview(**{field: PAYLOAD}),
            url="https://api.example.com/s/1",
            image_url="https://api.example.com/s/1/og.png",
        )
        assert "<script>" not in page
        assert '"><' not in page

    @pytest.mark.parametrize("argument", ["site_name", "locale", "link_text"])
    def test_router_level_argument(self, argument: str) -> None:
        """Arguments shared by every item are escaped as well."""
        arguments: dict[str, str] = {
            "site_name": "BusCar",
            "locale": "pt_BR",
            "link_text": "Abrir",
        }
        arguments[argument] = PAYLOAD
        page = render_share_page(
            _preview(),
            url="https://api.example.com/s/1",
            image_url=None,
            site_name=arguments["site_name"],
            locale=arguments["locale"],
            link_text=arguments["link_text"],
        )
        assert "<script>" not in page

    def test_url_quotes(self) -> None:
        """A quote inside a valid URL cannot close the attribute."""
        page = render_share_page(
            _preview(redirect_url='https://app.example.com/c/1?q="><script>'),
            url='https://api.example.com/s/"><script>',
            image_url='https://cdn.example.com/a.png?"><script>',
        )
        assert "<script>" not in page

    def test_guard_fires_without_escape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With ``escape`` disabled the same assertion sees the payload."""

        def identity(value: str, quote: bool = True) -> str:
            """Return ``value`` unescaped.

            Args:
                value (str): The text.
                quote (bool): Ignored.

            Returns:
                str: ``value``.
            """
            return value

        monkeypatch.setattr("tempest_fastapi_sdk.share.page.escape", identity)
        page = render_share_page(
            _preview(title=PAYLOAD),
            url="https://api.example.com/s/1",
            image_url=None,
        )
        assert "<script>" in page


class TestPage:
    """Tags a crawler reads and the way a human leaves the page."""

    def test_open_graph_and_twitter_tags(self) -> None:
        """Title, description, URL, image and its size are written."""
        page = render_share_page(
            _preview(),
            url="https://api.example.com/s/1",
            image_url="https://api.example.com/s/1/og.png?v=1",
            image_width=1200,
            image_height=630,
            site_name="BusCar",
            locale="pt_BR",
            link_text="Abrir no app",
        )
        assert '<meta property="og:title" content="Carona">' in page
        assert '<meta property="og:url" content="https://api.example.com/s/1">' in page
        assert (
            '<meta property="og:image" content="https://api.example.com/s/1/og.png?v=1">'
            in page
        )
        assert '<meta property="og:image:width" content="1200">' in page
        assert '<meta property="og:site_name" content="BusCar">' in page
        assert '<meta name="twitter:card" content="summary_large_image">' in page
        assert '<link rel="canonical" href="https://api.example.com/s/1">' in page
        assert '<html lang="pt">' in page
        assert (
            '<meta http-equiv="refresh" content="0; url=https://app.example.com/c/1">'
            in page
        )
        assert '<a href="https://app.example.com/c/1">Abrir no app</a>' in page

    def test_without_image(self) -> None:
        """No ``og:image`` and a ``summary`` Twitter card."""
        page = render_share_page(
            _preview(), url="https://api.example.com/s/1", image_url=None
        )
        assert "og:image" not in page
        assert '<meta name="twitter:card" content="summary">' in page
