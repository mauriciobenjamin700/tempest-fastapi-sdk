"""Conditional requests and immutable caching of a served tempestweb build."""

from __future__ import annotations

import json
import shutil
from email.utils import formatdate
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tempestweb.cli import build_artifact, scaffold_project

from tempest_fastapi_sdk.ssr import build_web_app, make_web_app_router

IMMUTABLE = "public, max-age=31536000, immutable"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Scaffold a tempestweb project and build both artifacts once."""
    parent = tmp_path_factory.mktemp("tw-cache")
    scaffold_project("demo", parent=str(parent))
    root = parent / "demo"
    wasm = build_artifact(str(root), mode="wasm").out_dir
    server = build_artifact(str(root), mode="server").out_dir
    return {"wasm": Path(wasm), "server": Path(server)}


@pytest.fixture
def dist(built: dict[str, Path], tmp_path: Path) -> Path:
    """A private copy of the wasm build, free to gain extra files."""
    copy = tmp_path / "wasm"
    shutil.copytree(built["wasm"], copy)
    return copy


def _client(directory: Path, **kwargs: object) -> TestClient:
    app = FastAPI()
    app.include_router(make_web_app_router(directory, **kwargs))  # type: ignore[arg-type]
    return TestClient(app)


class TestConditionalRequests:
    """`no-cache` must mean "revalidate", answered with an empty 304."""

    def test_matching_etag_is_304_with_no_body(self, dist: Path) -> None:
        with _client(dist, asset_cache_control="no-cache") as client:
            first = client.get("/bootstrap.js")
            again = client.get(
                "/bootstrap.js",
                headers={"If-None-Match": first.headers["etag"]},
            )
        assert first.status_code == 200
        assert again.status_code == 304
        assert again.content == b""
        assert again.headers["etag"] == first.headers["etag"]
        assert again.headers["cache-control"] == "no-cache"
        assert again.headers["x-content-type-options"] == "nosniff"

    def test_mismatching_etag_is_full_200(self, dist: Path) -> None:
        with _client(dist) as client:
            response = client.get(
                "/bootstrap.js",
                headers={"If-None-Match": '"not-the-etag"'},
            )
        assert response.status_code == 200
        assert response.content == (dist / "bootstrap.js").read_bytes()

    def test_etag_inside_a_list_matches(self, dist: Path) -> None:
        with _client(dist) as client:
            etag = client.get("/bootstrap.js").headers["etag"]
            response = client.get(
                "/bootstrap.js",
                headers={"If-None-Match": f'"a", {etag} , "b"'},
            )
        assert response.status_code == 304

    def test_star_matches_an_existing_file(self, dist: Path) -> None:
        with _client(dist) as client:
            response = client.get("/bootstrap.js", headers={"If-None-Match": "*"})
        assert response.status_code == 304

    def test_weak_etag_uses_weak_comparison(self, dist: Path) -> None:
        with _client(dist) as client:
            etag = client.get("/bootstrap.js").headers["etag"]
            response = client.get(
                "/bootstrap.js",
                headers={"If-None-Match": f"W/{etag}"},
            )
        assert response.status_code == 304

    def test_if_modified_since_not_older_is_304(self, dist: Path) -> None:
        with _client(dist) as client:
            last_modified = client.get("/bootstrap.js").headers["last-modified"]
            response = client.get(
                "/bootstrap.js",
                headers={"If-Modified-Since": last_modified},
            )
        assert response.status_code == 304

    def test_if_modified_since_older_is_200(self, dist: Path) -> None:
        with _client(dist) as client:
            response = client.get(
                "/bootstrap.js",
                headers={"If-Modified-Since": formatdate(0, usegmt=True)},
            )
        assert response.status_code == 200

    def test_unparseable_if_modified_since_is_ignored(self, dist: Path) -> None:
        with _client(dist) as client:
            response = client.get(
                "/bootstrap.js",
                headers={"If-Modified-Since": "yesterday-ish"},
            )
        assert response.status_code == 200

    def test_if_none_match_overrides_if_modified_since(self, dist: Path) -> None:
        """RFC 9110 §13.1.3: IMS is ignored when INM is present."""
        with _client(dist) as client:
            last_modified = client.get("/bootstrap.js").headers["last-modified"]
            response = client.get(
                "/bootstrap.js",
                headers={
                    "If-None-Match": '"stale"',
                    "If-Modified-Since": last_modified,
                },
            )
        assert response.status_code == 200

    def test_head_is_served_and_conditional(self, dist: Path) -> None:
        with _client(dist) as client:
            head = client.head("/bootstrap.js")
            conditional = client.head(
                "/bootstrap.js",
                headers={"If-None-Match": head.headers["etag"]},
            )
            index_head = client.head("/")
        assert head.status_code == 200
        assert head.content == b""
        assert head.headers["content-length"] == str(
            (dist / "bootstrap.js").stat().st_size,
        )
        assert conditional.status_code == 304
        assert index_head.status_code == 200

    def test_head_routes_stay_out_of_openapi(self, dist: Path) -> None:
        app = FastAPI()
        app.include_router(make_web_app_router(dist))
        operations = app.openapi()["paths"]["/{resource}"]
        assert set(operations) == {"get"}

    def test_index_and_spa_fallback_revalidate(self, dist: Path) -> None:
        with _client(dist) as client:
            etag = client.get("/").headers["etag"]
            root = client.get("/", headers={"If-None-Match": etag})
            deep = client.get("/some/client/route", headers={"If-None-Match": etag})
        assert root.status_code == 304
        assert deep.status_code == 304
        assert deep.headers["cache-control"] == "no-cache"

    def test_service_worker_revalidates(self, dist: Path) -> None:
        with _client(dist) as client:
            etag = client.get("/sw.js").headers["etag"]
            response = client.get("/sw.js", headers={"If-None-Match": etag})
        assert response.status_code == 304
        assert response.headers["cache-control"] == "no-cache"

    def test_changed_file_gets_a_new_etag(self, dist: Path) -> None:
        with _client(dist) as client:
            etag = client.get("/bootstrap.js").headers["etag"]
            (dist / "bootstrap.js").write_text("console.log(2);\n", encoding="utf-8")
            response = client.get("/bootstrap.js", headers={"If-None-Match": etag})
        assert response.status_code == 200
        assert response.text == "console.log(2);\n"

    def test_shell_path_override_is_conditional_and_no_cache(
        self,
        dist: Path,
        tmp_path: Path,
    ) -> None:
        """A shell file not named index.html still revalidates every time."""
        custom = tmp_path / "shell.html"
        custom.write_text('<html lang="pt-BR"><body>x</body></html>', encoding="utf-8")
        with _client(dist, shell=custom) as client:
            first = client.get("/")
            again = client.get("/", headers={"If-None-Match": first.headers["etag"]})
        assert first.headers["cache-control"] == "no-cache"
        assert again.status_code == 304

    def test_index_html_path_serves_the_shell_override(self, dist: Path) -> None:
        document = '<html lang="pt-BR"><body>override</body></html>'
        with _client(dist, shell=document) as client:
            response = client.get("/index.html")
        assert response.text == document

    def test_build_web_app_index_is_conditional(self, built: dict[str, Path]) -> None:
        with TestClient(build_web_app(built["server"])) as client:
            etag = client.get("/").headers["etag"]
            response = client.get("/", headers={"If-None-Match": etag})
        assert response.status_code == 304


class TestImmutablePaths:
    """Content-addressed paths are cached for a year; entry points never."""

    def test_explicit_pattern_marks_matching_paths(self, dist: Path) -> None:
        with _client(
            dist,
            asset_cache_control="no-cache",
            immutable=["client/**", "tempestweb-pkg.zip"],
        ) as client:
            nested = client.get("/client/native/index.js")
            archive = client.get("/tempestweb-pkg.zip")
            other = client.get("/icons/icon-192.png")
        assert nested.headers["cache-control"] == IMMUTABLE
        assert archive.headers["cache-control"] == IMMUTABLE
        assert other.headers["cache-control"] == "no-cache"

    def test_single_star_stays_in_one_segment(self, dist: Path) -> None:
        with _client(dist, immutable=["client/*.js"]) as client:
            shallow = client.get("/client/dom.js")
            deep = client.get("/client/native/index.js")
        assert shallow.headers["cache-control"] == IMMUTABLE
        assert deep.headers["cache-control"] == "public, max-age=3600"

    def test_entry_points_are_never_immutable(self, dist: Path) -> None:
        with _client(dist, immutable=["**"]) as client:
            responses = {
                path: client.get(path).headers["cache-control"]
                for path in (
                    "/",
                    "/index.html",
                    "/sw.js",
                    "/bootstrap.js",
                    "/register.js",
                    "/manifest.webmanifest",
                    "/some/client/route",
                )
            }
            asset = client.get("/client/dom.js")
        assert all(value != IMMUTABLE for value in responses.values()), responses
        assert asset.headers["cache-control"] == IMMUTABLE

    def test_manifest_in_the_build_is_read(self, dist: Path) -> None:
        hashed = dist / "client-3f2a9c"
        hashed.mkdir()
        (hashed / "dom.js").write_text("export {};\n", encoding="utf-8")
        (dist / "immutable.json").write_text(
            json.dumps({"immutable": ["client-3f2a9c/**"], "version": 1}),
            encoding="utf-8",
        )
        with _client(dist, asset_cache_control="no-cache") as client:
            hashed_response = client.get("/client-3f2a9c/dom.js")
            plain = client.get("/client/dom.js")
            manifest = client.get("/immutable.json")
        assert hashed_response.headers["cache-control"] == IMMUTABLE
        assert plain.headers["cache-control"] == "no-cache"
        assert manifest.headers["cache-control"] == "no-cache"

    def test_manifest_cannot_mark_itself(self, dist: Path) -> None:
        (dist / "immutable.json").write_text(
            json.dumps({"immutable": ["*.json"]}),
            encoding="utf-8",
        )
        with _client(dist) as client:
            response = client.get("/immutable.json")
        assert response.headers["cache-control"] == "public, max-age=3600"

    def test_manifest_lookup_can_be_disabled(self, dist: Path) -> None:
        (dist / "immutable.json").write_text(
            json.dumps({"immutable": ["client/**"]}),
            encoding="utf-8",
        )
        with _client(dist, immutable_manifest=None) as client:
            response = client.get("/client/dom.js")
        assert response.headers["cache-control"] == "public, max-age=3600"

    def test_custom_manifest_name(self, dist: Path) -> None:
        (dist / "cache.json").write_text(
            json.dumps({"immutable": ["client/**"]}),
            encoding="utf-8",
        )
        with _client(dist, immutable_manifest="cache.json") as client:
            response = client.get("/client/dom.js")
        assert response.headers["cache-control"] == IMMUTABLE

    @pytest.mark.parametrize(
        "content",
        [
            "not json",
            json.dumps(["client/**"]),
            json.dumps({"immutable": "client/**"}),
            json.dumps({"immutable": [1]}),
            json.dumps({"paths": ["client/**"]}),
        ],
    )
    def test_malformed_manifest_fails_the_boot(
        self,
        dist: Path,
        content: str,
    ) -> None:
        (dist / "immutable.json").write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match=r"immutable\.json"):
            make_web_app_router(dist)

    @pytest.mark.parametrize("pattern", ["", "/client/**"])
    def test_absolute_or_empty_pattern_is_rejected(
        self,
        dist: Path,
        pattern: str,
    ) -> None:
        with pytest.raises(ValueError, match="relative to the build root"):
            make_web_app_router(dist, immutable=[pattern])

    def test_immutable_path_still_revalidates_with_304(self, dist: Path) -> None:
        with _client(dist, immutable=["client/**"]) as client:
            etag = client.get("/client/dom.js").headers["etag"]
            response = client.get("/client/dom.js", headers={"If-None-Match": etag})
        assert response.status_code == 304
        assert response.headers["cache-control"] == IMMUTABLE
