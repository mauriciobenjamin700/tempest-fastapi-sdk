"""The share router: page, fallback, image, conditional GET, event loop."""

import asyncio
import io
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from tempest_fastapi_sdk.share import (
    ShareCardRenderer,
    ShareCardSchema,
    SharePreviewSchema,
    make_share_router,
)
from tempest_fastapi_sdk.share import card as card_module

pytestmark = pytest.mark.skipif(
    not any(
        Path(regular).is_file() for regular, _ in card_module.DEFAULT_FONT_CANDIDATES
    ),
    reason="no system TrueType font (install fonts-dejavu-core)",
)

PUBLIC_URL: str = "https://api.example.com"
FALLBACK: SharePreviewSchema = SharePreviewSchema(
    title="BusCar",
    description="Caronas entre cidades",
    redirect_url="https://app.example.com",
)


class FakeResolver:
    """Resolver backed by a dict, counting calls.

    Attributes:
        items (dict[str, SharePreviewSchema]): Previews by id.
        calls (int): How many times it was awaited.
    """

    def __init__(self, items: dict[str, SharePreviewSchema]) -> None:
        """Store the previews.

        Args:
            items (dict[str, SharePreviewSchema]): Previews by id.
        """
        self.items: dict[str, SharePreviewSchema] = items
        self.calls: int = 0

    async def __call__(self, item_id: str, /) -> SharePreviewSchema | None:
        """Look ``item_id`` up.

        Args:
            item_id (str): The item.

        Returns:
            SharePreviewSchema | None: The preview, or ``None``.
        """
        self.calls += 1
        return self.items.get(item_id)


class CountingRenderer(ShareCardRenderer):
    """Renderer that counts renders.

    Attributes:
        renders (int): How many cards were drawn.
    """

    renders: int = 0

    def render(self, card: ShareCardSchema) -> bytes:
        """Count, then draw.

        Args:
            card (ShareCardSchema): What to draw.

        Returns:
            bytes: The PNG.
        """
        self.renders += 1
        return super().render(card)


def _preview(version: str | None = "v1", **overrides: Any) -> SharePreviewSchema:
    """Build an item preview with a card.

    Args:
        version (str | None): The preview's version.
        **overrides (Any): Fields to replace.

    Returns:
        SharePreviewSchema: The preview.
    """
    values: dict[str, Any] = {
        "title": "Teresina → Fortaleza",
        "description": "Sexta, 18h",
        "redirect_url": "https://app.example.com/c/42",
        "version": version,
        "card": ShareCardSchema(title="Teresina → Fortaleza"),
    }
    values.update(overrides)
    return SharePreviewSchema.model_validate(values)


def _client(
    resolver: FakeResolver, renderer: ShareCardRenderer | None = None, **kwargs: Any
) -> TestClient:
    """Mount the router on a fresh app.

    Args:
        resolver (FakeResolver): The resolver.
        renderer (ShareCardRenderer | None): The card renderer.
        **kwargs (Any): Extra router arguments.

    Returns:
        TestClient: Client of the app.
    """
    app = FastAPI()
    app.include_router(
        make_share_router(
            resolver,
            public_url=PUBLIC_URL,
            fallback=FALLBACK,
            renderer=renderer,
            **kwargs,
        )
    )
    return TestClient(app)


class TestPage:
    """``GET <prefix>/{item_id}``."""

    def test_item_page(self) -> None:
        """200, HTML, cache header and absolute, versioned ``og:image``."""
        client = _client(FakeResolver({"42": _preview()}), ShareCardRenderer())
        response = client.get("/s/42")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "public, max-age=600"
        assert 'content="https://api.example.com/s/42/og.png?v=' in response.text
        assert (
            '<meta property="og:url" content="https://api.example.com/s/42">'
            in response.text
        )

    def test_image_url_changes_with_version(self) -> None:
        """A new version is a new image URL, so crawlers refetch."""
        resolver = FakeResolver({"42": _preview("v1")})
        client = _client(resolver, ShareCardRenderer())
        first = client.get("/s/42").text
        resolver.items["42"] = _preview("v2")
        assert client.get("/s/42").text != first

    def test_unavailable_item_gets_fallback(self) -> None:
        """No 500, no image, the fallback text and the page's own URL."""
        client = _client(FakeResolver({}), ShareCardRenderer())
        response = client.get("/s/missing")
        assert response.status_code == 200
        assert "Caronas entre cidades" in response.text
        assert "og:image" not in response.text
        assert "https://api.example.com/s/missing" in response.text

    def test_id_is_quoted_in_urls(self) -> None:
        """A strange id is percent-encoded in ``og:url``."""
        client = _client(FakeResolver({}))
        response = client.get("/s/a%22b")
        assert "https://api.example.com/s/a%22b" in response.text
        assert 'a"b' not in response.text

    def test_explicit_image_and_canonical(self) -> None:
        """Resolver-provided URLs win over the generated ones."""
        preview = _preview(
            image_url="https://cdn.example.com/x.png",
            canonical_url="https://example.com/caronas/42",
        )
        text = (
            _client(FakeResolver({"42": preview}), ShareCardRenderer())
            .get("/s/42")
            .text
        )
        assert 'content="https://cdn.example.com/x.png"' in text
        assert 'href="https://example.com/caronas/42"' in text
        assert "og.png" not in text

    def test_out_of_openapi_by_default(self) -> None:
        """Meta pages stay out of the schema unless asked."""
        for include, expected in (
            (False, set()),
            (True, {"/s/{item_id}", "/s/{item_id}/og.png"}),
        ):
            app = FastAPI()
            app.include_router(
                make_share_router(
                    FakeResolver({}),
                    public_url=PUBLIC_URL,
                    fallback=FALLBACK,
                    renderer=ShareCardRenderer(),
                    include_in_schema=include,
                )
            )
            assert set(app.openapi()["paths"]) == expected

    def test_resolver_error_propagates(self) -> None:
        """A failing resolver is not turned into the fallback."""

        async def broken(item_id: str, /) -> SharePreviewSchema | None:
            """Fail.

            Args:
                item_id (str): Ignored.

            Returns:
                SharePreviewSchema | None: Never.

            Raises:
                RuntimeError: Always.
            """
            raise RuntimeError("database down")

        app = FastAPI()
        app.include_router(
            make_share_router(broken, public_url=PUBLIC_URL, fallback=FALLBACK)
        )
        response = TestClient(app, raise_server_exceptions=False).get("/s/1")
        assert response.status_code == 500


class TestImage:
    """``GET <prefix>/{item_id}/og.png``."""

    def test_png(self) -> None:
        """A 1200x630 PNG with weak ETag and cache header."""
        response = _client(FakeResolver({"42": _preview()}), ShareCardRenderer()).get(
            "/s/42/og.png"
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.headers["etag"].startswith('W/"')
        assert response.headers["cache-control"] == "public, max-age=600"
        assert Image.open(io.BytesIO(response.content)).size == (1200, 630)

    @pytest.mark.parametrize("shape", ["{etag}", "W/{bare}", '"other", {etag}', "*"])
    def test_304_without_rendering(self, shape: str) -> None:
        """A matching ``If-None-Match`` answers 304 and draws nothing."""
        renderer = CountingRenderer()
        client = _client(FakeResolver({"42": _preview()}), renderer)
        etag = client.get("/s/42/og.png").headers["etag"]
        assert renderer.renders == 1
        header = shape.format(etag=etag, bare=etag.removeprefix("W/"))
        response = client.get("/s/42/og.png", headers={"If-None-Match": header})
        assert response.status_code == 304
        assert response.headers["etag"] == etag
        assert renderer.renders == 1

    def test_etag_changes_with_version(self) -> None:
        """A new version is a new ETag."""
        resolver = FakeResolver({"42": _preview("v1")})
        client = _client(resolver, ShareCardRenderer())
        first = client.get("/s/42/og.png").headers["etag"]
        resolver.items["42"] = _preview("v2")
        assert client.get("/s/42/og.png").headers["etag"] != first

    def test_etag_without_version_follows_card(self) -> None:
        """Without a version the card content keys the ETag."""
        resolver = FakeResolver({"42": _preview(None)})
        client = _client(resolver, ShareCardRenderer())
        first = client.get("/s/42/og.png").headers["etag"]
        assert client.get("/s/42/og.png").headers["etag"] == first
        resolver.items["42"] = _preview(None, card=ShareCardSchema(title="Outro"))
        assert client.get("/s/42/og.png").headers["etag"] != first

    def test_etag_changes_with_renderer(self) -> None:
        """New brand colors invalidate cached cards."""
        resolver = FakeResolver({"42": _preview()})
        red = _client(resolver, ShareCardRenderer(accent_color="#ff0000"))
        green = _client(resolver, ShareCardRenderer(accent_color="#00ff00"))
        assert (
            red.get("/s/42/og.png").headers["etag"]
            != green.get("/s/42/og.png").headers["etag"]
        )

    def test_etag_differs_between_items(self) -> None:
        """Two items with the same version do not share a tag."""
        client = _client(
            FakeResolver({"1": _preview(), "2": _preview()}), ShareCardRenderer()
        )
        assert (
            client.get("/s/1/og.png").headers["etag"]
            != client.get("/s/2/og.png").headers["etag"]
        )

    @pytest.mark.parametrize("items", [{}, {"42": _preview(card=None)}])
    def test_404_without_card(self, items: dict[str, SharePreviewSchema]) -> None:
        """Unavailable item or no card: 404."""
        assert (
            _client(FakeResolver(items), ShareCardRenderer())
            .get("/s/42/og.png")
            .status_code
            == 404
        )

    def test_404_is_cacheable(self) -> None:
        """The image 404 carries ``Cache-Control`` too."""
        response = _client(FakeResolver({}), ShareCardRenderer()).get("/s/1/og.png")
        assert response.headers["cache-control"] == "public, max-age=600"

    @pytest.mark.parametrize("path", ["/s/42", "/s/42/og.png"])
    def test_head(self, path: str) -> None:
        """Crawlers that probe with ``HEAD`` get 200 and no body."""
        client = _client(FakeResolver({"42": _preview()}), ShareCardRenderer())
        response = client.head(path)
        assert response.status_code == 200
        assert response.content == b""

    def test_no_route_without_renderer(self) -> None:
        """Without a renderer the image route does not exist."""
        client = _client(FakeResolver({"42": _preview()}))
        assert client.get("/s/42/og.png").status_code == 404
        assert "og.png" not in client.get("/s/42").text


class TestArguments:
    """Construction-time validation."""

    @pytest.mark.parametrize("url", ["api.example.com", "/s", "ftp://x.example.com"])
    def test_public_url(self, url: str) -> None:
        """``public_url`` must be absolute ``http(s)``."""
        with pytest.raises(ValueError):
            make_share_router(FakeResolver({}), public_url=url, fallback=FALLBACK)

    def test_prefix(self) -> None:
        """``prefix`` starts with ``/``."""
        with pytest.raises(ValueError, match="prefix"):
            make_share_router(
                FakeResolver({}), public_url=PUBLIC_URL, fallback=FALLBACK, prefix="s"
            )

    def test_custom_prefix_and_trailing_slash(self) -> None:
        """Trailing slashes are trimmed from both."""
        app = FastAPI()
        app.include_router(
            make_share_router(
                FakeResolver({"42": _preview()}),
                public_url=PUBLIC_URL + "/",
                fallback=FALLBACK,
                prefix="/c/",
            )
        )
        text = TestClient(app).get("/c/42").text
        assert '<meta property="og:url" content="https://api.example.com/c/42">' in text


async def _inline[T](func: Callable[..., T], /, *args: Any) -> T:
    """Stand-in for :func:`asyncio.to_thread` that runs on the loop.

    Args:
        func (Callable[..., T]): The function.
        *args (Any): Its arguments.

    Returns:
        T: Its result.
    """
    return func(*args)


async def _render_thread(
    monkeypatch: pytest.MonkeyPatch,
    to_thread: Callable[..., Awaitable[Any]] | None,
) -> tuple[int, int]:
    """Fetch an image and report the loop thread and the render thread.

    Args:
        monkeypatch (pytest.MonkeyPatch): Swaps ``asyncio.to_thread``.
        to_thread (Callable[..., Awaitable[Any]] | None): Replacement for
            the router's ``asyncio.to_thread``, or ``None`` for the real one.

    Returns:
        tuple[int, int]: Loop thread id and render thread id.
    """
    seen: list[int] = []

    class ProbeRenderer(ShareCardRenderer):
        """Records the thread it renders on."""

        def render(self, card: ShareCardSchema) -> bytes:
            """Record the thread, then draw.

            Args:
                card (ShareCardSchema): What to draw.

            Returns:
                bytes: The PNG.
            """
            seen.append(threading.get_ident())
            return super().render(card)

    app = FastAPI()
    app.include_router(
        make_share_router(
            FakeResolver({"42": _preview()}),
            public_url=PUBLIC_URL,
            fallback=FALLBACK,
            renderer=ProbeRenderer(),
        )
    )
    transport = httpx.ASGITransport(app=app)
    if to_thread is not None:
        monkeypatch.setattr(asyncio, "to_thread", to_thread)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/s/42/og.png")
    assert response.status_code == 200
    assert len(seen) == 1
    return threading.get_ident(), seen[0]


class TestEventLoop:
    """Drawing happens off the event loop."""

    async def test_render_runs_in_worker_thread(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The render thread is not the loop thread."""
        loop_thread, render_thread = await _render_thread(monkeypatch, None)
        assert render_thread != loop_thread

    async def test_guard_fires_when_inline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With ``to_thread`` replaced by an inline call the check sees it."""
        loop_thread, render_thread = await _render_thread(monkeypatch, _inline)
        assert render_thread == loop_thread
