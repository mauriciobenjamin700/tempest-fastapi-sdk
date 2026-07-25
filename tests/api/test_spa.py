"""Tests for tempest_fastapi_sdk.api.spa."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import make_spa_router
from tempest_fastapi_sdk.api.spa import (
    DEFAULT_ASSET_CACHE_CONTROL,
    DEFAULT_DOCUMENT_CACHE_CONTROL,
)


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    """Write a minimal Vite-shaped build tree.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.

    Returns:
        Path: The build directory, containing ``index.html``, a hashed
        asset and a root-level file.
    """
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><div id=root></div>")
    (root / "assets" / "index-a1b2c3.js").write_text("console.log(1)")
    (root / "favicon.ico").write_text("icon")
    return root


@pytest.fixture
def client(dist: Path) -> TestClient:
    """Build an app with an API router mounted before the SPA router.

    Args:
        dist (Path): The build directory.

    Returns:
        TestClient: A client over the composed app.
    """
    api = APIRouter()

    @api.get("/ping")
    async def ping() -> dict[str, bool]:
        """Answer a health probe."""
        return {"ok": True}

    app = FastAPI()
    app.include_router(api, prefix="/api")
    app.include_router(make_spa_router(dist))
    return TestClient(app)


class TestWiring:
    """A missing or unbuilt directory fails at wiring time."""

    def test_missing_directory_raises(self, tmp_path: Path) -> None:
        """Failing early beats booting and 404-ing every page."""
        with pytest.raises(FileNotFoundError, match="build directory not found"):
            make_spa_router(tmp_path / "nope")

    def test_directory_without_index_raises(self, tmp_path: Path) -> None:
        """A directory that is not a build is reported as such."""
        (tmp_path / "dist").mkdir()
        with pytest.raises(FileNotFoundError, match="does not look like a built SPA"):
            make_spa_router(tmp_path / "dist")

    def test_build_without_assets_dir_is_accepted(self, tmp_path: Path) -> None:
        """A build that emits everything at the root still works."""
        root = tmp_path / "dist"
        root.mkdir()
        (root / "index.html").write_text("<!doctype html>")
        app = FastAPI()
        app.include_router(make_spa_router(root))
        assert TestClient(app).get("/").status_code == 200


class TestServing:
    """Real files are served; unknown routes fall back to the document."""

    def test_root_serves_the_document(self, client: TestClient) -> None:
        """``/`` returns ``index.html``."""
        response = client.get("/")
        assert response.status_code == 200
        assert "<div id=root>" in response.text

    def test_hashed_asset_is_served(self, client: TestClient) -> None:
        """A built asset is served from the mount."""
        response = client.get("/assets/index-a1b2c3.js")
        assert response.status_code == 200
        assert response.text == "console.log(1)"

    def test_root_level_file_is_served(self, client: TestClient) -> None:
        """Files outside ``assets/`` (favicon, manifest) still resolve."""
        response = client.get("/favicon.ico")
        assert response.status_code == 200
        assert response.text == "icon"

    def test_deep_link_falls_back_to_the_document(self, client: TestClient) -> None:
        """A client-side route returns the document, not a 404.

        This is the whole reason the router exists: ``/users/42`` exists in
        the browser's router and not on disk, so a bare static mount would
        404 on every refresh and shared link.
        """
        response = client.get("/users/42")
        assert response.status_code == 200
        assert "<div id=root>" in response.text


class TestCachePolicy:
    """The entry document must not be cached; hashed assets should be."""

    def test_document_is_not_cached(self, client: TestClient) -> None:
        """Caching ``index.html`` is what pins users to a stale bundle."""
        assert client.get("/").headers["cache-control"] == (
            DEFAULT_DOCUMENT_CACHE_CONTROL
        )

    def test_fallback_document_is_not_cached(self, client: TestClient) -> None:
        """The fallback path gets the same policy as the root."""
        assert client.get("/deep/link").headers["cache-control"] == (
            DEFAULT_DOCUMENT_CACHE_CONTROL
        )

    def test_hashed_asset_is_immutable(self, client: TestClient) -> None:
        """Content-hashed names make a permanent cache safe."""
        headers = client.get("/assets/index-a1b2c3.js").headers
        assert headers["cache-control"] == DEFAULT_ASSET_CACHE_CONTROL

    def test_security_headers_are_stamped(self, client: TestClient) -> None:
        """Static responses carry the SDK's anti-XSS headers."""
        headers = client.get("/assets/index-a1b2c3.js").headers
        assert headers["x-content-type-options"] == "nosniff"


class TestApiIsolation:
    """The catch-all must never swallow the API surface."""

    def test_real_api_route_still_answers(self, client: TestClient) -> None:
        """A route registered before the SPA router keeps working."""
        assert client.get("/api/ping").json() == {"ok": True}

    def test_unknown_api_path_stays_a_json_404(self, client: TestClient) -> None:
        """A typo'd endpoint must not return an HTML 200.

        Returning the document here is the subtle failure: the client gets
        ``200`` with HTML and reports a JSON parse error, which sends people
        debugging the wrong layer.
        """
        response = client.get("/api/nope")
        assert response.status_code == 404
        assert "<div id=root>" not in response.text

    @pytest.mark.parametrize(
        "path", ["/docs", "/openapi.json", "/health", "/metrics", "/admin/x"]
    )
    def test_reserved_prefixes_are_not_swallowed(
        self, client: TestClient, path: str
    ) -> None:
        """Every SDK-mounted prefix is excluded from the fallback.

        Asserted on the body rather than the status: ``/docs`` and
        ``/openapi.json`` legitimately answer 200 from FastAPI itself, so the
        thing to prove is that the SPA document is not what came back.

        Args:
            client (TestClient): The composed app.
            path (str): A reserved path.
        """
        assert "<div id=root>" not in client.get(path).text

    def test_prefix_match_is_boundary_aware(self, dist: Path) -> None:
        """``/apixyz`` is not ``/api`` and must still reach the SPA."""
        app = FastAPI()
        app.include_router(make_spa_router(dist))
        response = TestClient(app).get("/apixyz")
        assert response.status_code == 200
        assert "<div id=root>" in response.text


class TestPathTraversal:
    """A crafted path cannot escape the build directory."""

    @pytest.mark.parametrize(
        "path",
        [
            "/../secret.txt",
            "/%2e%2e/secret.txt",
            "/..%2Fsecret.txt",
            "/assets/../../secret.txt",
        ],
    )
    def test_dotdot_does_not_escape(
        self, dist: Path, tmp_path: Path, path: str
    ) -> None:
        """``..`` segments fall back to the document instead of leaking.

        Several encodings are exercised because normalization happens at
        more than one layer, and a guard that only holds for the plain form
        is not a guard.

        Args:
            dist (Path): The build directory.
            tmp_path (Path): Its parent, holding the file to not serve.
            path (str): The traversal attempt.
        """
        (tmp_path / "secret.txt").write_text("do-not-serve")
        app = FastAPI()
        app.include_router(make_spa_router(dist))
        assert "do-not-serve" not in TestClient(app).get(path).text
