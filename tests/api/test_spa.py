"""Tests for tempest_fastapi_sdk.api.spa."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import (
    DEFAULT_STATIC_SECURITY_HEADERS,
    make_spa_router,
)
from tempest_fastapi_sdk.api.spa import (
    DEFAULT_ASSET_CACHE_CONTROL,
    DEFAULT_DOCUMENT_CACHE_CONTROL,
    DEFAULT_SPA_CONTENT_SECURITY_POLICY,
    DEFAULT_SPA_SECURITY_HEADERS,
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


class TestContentSecurityPolicy:
    """The default policy has to let the page it serves work.

    ``make_spa_router`` used to default to
    ``DEFAULT_STATIC_SECURITY_HEADERS``, whose ``default-src 'none';
    sandbox`` is the right policy for a file nobody trusts and the wrong one
    for an application: the browser refused the page's own bundle and
    stylesheet, and the sandbox blocked script execution, so the SPA rendered
    blank.
    """

    def test_the_document_gets_the_application_policy(
        self,
        client: TestClient,
    ) -> None:
        """Not the untrusted-file policy."""
        csp = client.get("/").headers["content-security-policy"]
        assert csp == DEFAULT_SPA_CONTENT_SECURITY_POLICY

    def test_assets_get_the_same_policy(self, client: TestClient) -> None:
        """A bundle refused by its own headers is the same bug."""
        csp = client.get("/assets/index-a1b2c3.js").headers["content-security-policy"]
        assert csp == DEFAULT_SPA_CONTENT_SECURITY_POLICY

    def test_the_policy_allows_same_origin_script_and_style(self) -> None:
        """The two directives whose absence blanked the page."""
        assert "script-src 'self'" in DEFAULT_SPA_CONTENT_SECURITY_POLICY
        assert "style-src 'self' 'unsafe-inline'" in (
            DEFAULT_SPA_CONTENT_SECURITY_POLICY
        )

    def test_the_policy_has_no_sandbox(self) -> None:
        """``sandbox`` without ``allow-scripts`` blocks execution outright."""
        assert "sandbox" not in DEFAULT_SPA_CONTENT_SECURITY_POLICY

    def test_nothing_loads_from_another_origin(self) -> None:
        """Restrictive is still the point — just restrictive to ``'self'``."""
        assert DEFAULT_SPA_CONTENT_SECURITY_POLICY.startswith("default-src 'self'")
        assert "object-src 'none'" in DEFAULT_SPA_CONTENT_SECURITY_POLICY
        assert "frame-ancestors 'none'" in DEFAULT_SPA_CONTENT_SECURITY_POLICY
        assert "*" not in DEFAULT_SPA_CONTENT_SECURITY_POLICY

    def test_the_two_defaults_are_not_the_same_headers(self) -> None:
        """Pins the distinction the defect erased.

        Both dicts are legitimate; using the static one for an application
        is what was wrong. If a future edit makes them equal again, the SPA
        goes back to serving itself a blank page.
        """
        assert DEFAULT_SPA_SECURITY_HEADERS != DEFAULT_STATIC_SECURITY_HEADERS
        assert (
            DEFAULT_STATIC_SECURITY_HEADERS["Content-Security-Policy"]
            == "default-src 'none'; sandbox"
        )

    def test_an_explicit_override_still_wins(self, dist: Path) -> None:
        """A caller who wants the strict policy can still have it.

        Including the static one — this is a default, not a policy the
        router enforces. Since v0.277.0 that override has to be deliberate:
        the shape blanks the page, so `allow_blocking_headers=True` is what
        separates "I meant this" from "I copied an old snippet".
        """
        app = FastAPI()
        strict = dict(DEFAULT_STATIC_SECURITY_HEADERS)
        app.include_router(
            make_spa_router(
                dist,
                security_headers=strict,
                allow_blocking_headers=True,
            )
        )
        csp = TestClient(app).get("/").headers["content-security-policy"]
        assert csp == "default-src 'none'; sandbox"

    def test_the_other_hardening_headers_travel_with_it(
        self,
        client: TestClient,
    ) -> None:
        """The policy is one header of five."""
        headers = client.get("/").headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert headers["cross-origin-resource-policy"] == "same-origin"


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


class TestSelfBlockingCspIsRefused:
    """A policy that blanks the page fails at wiring, not in the browser.

    `DEFAULT_STATIC_SECURITY_HEADERS` was this router's default up to
    v0.251.0 and is meant for files the service does not trust. Pointed at a
    compiled SPA it blocks the page's own bundle, and the only symptom is a
    blank document with the reason in the browser console. Up to v0.277.0
    that was a `!!! danger` in the recipe; the check on the policy's shape is
    what makes the danger unreachable.
    """

    def test_the_shipped_footgun_is_refused(self, dist: Path) -> None:
        """The exact constant a reader of an old snippet would pass.

        Args:
            dist (Path): The build directory.
        """
        with pytest.raises(ValueError, match="allow-scripts"):
            make_spa_router(dist, security_headers=DEFAULT_STATIC_SECURITY_HEADERS)

    def test_default_src_none_without_script_src_is_refused(self, dist: Path) -> None:
        """A hand-written equivalent fails the same way, so it is caught too.

        Args:
            dist (Path): The build directory.
        """
        with pytest.raises(ValueError, match="script-src"):
            make_spa_router(
                dist,
                security_headers={"Content-Security-Policy": "default-src 'none'"},
            )

    @pytest.mark.parametrize(
        "headers",
        [
            pytest.param(None, id="default"),
            pytest.param(
                {
                    "Content-Security-Policy": (
                        "default-src 'none'; script-src 'self'; style-src 'self'"
                    )
                },
                id="declared-script-src",
            ),
            pytest.param(
                {
                    "Content-Security-Policy": (
                        "sandbox allow-scripts allow-same-origin"
                    )
                },
                id="sandbox-with-allow-scripts",
            ),
            pytest.param({"X-Content-Type-Options": "nosniff"}, id="no-csp-at-all"),
        ],
    )
    def test_a_workable_policy_is_accepted(
        self,
        dist: Path,
        headers: dict[str, str] | None,
    ) -> None:
        """The guard is narrow: only self-blocking shapes are refused.

        Args:
            dist (Path): The build directory.
            headers (dict[str, str] | None): The policy under test.
        """
        assert make_spa_router(dist, security_headers=headers) is not None

    def test_the_refusal_names_the_way_out(self, dist: Path) -> None:
        """An error that does not say what to do costs a search.

        Args:
            dist (Path): The build directory.
        """
        with pytest.raises(ValueError) as error:
            make_spa_router(dist, security_headers=DEFAULT_STATIC_SECURITY_HEADERS)
        assert "DEFAULT_SPA_SECURITY_HEADERS" in str(error.value)
