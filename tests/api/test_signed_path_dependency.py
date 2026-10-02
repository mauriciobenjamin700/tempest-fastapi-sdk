"""Tests for ``make_signed_path_dependency`` on a real route."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import (
    make_signed_path_dependency,
    register_exception_handlers,
    sign_path,
    utcnow,
)

SECRET: str = "app-secret"


def _build_app() -> FastAPI:
    """Build an app with one ``files`` route and one ``email-link`` route.

    Returns:
        FastAPI: The app, with the SDK exception handlers registered.
    """
    app = FastAPI()
    register_exception_handlers(app)
    files_url = make_signed_path_dependency(secret=SECRET, purpose="files")
    email_url = make_signed_path_dependency(secret=SECRET, purpose="email-link")

    @app.get("/api/files/{key:path}", dependencies=[Depends(files_url)])
    async def download(key: str) -> dict[str, str]:
        """Echo the key the route received.

        Args:
            key (str): The decoded object key.

        Returns:
            dict[str, str]: ``{"key": key}``.
        """
        return {"key": key}

    @app.get("/api/confirm/{token}", dependencies=[Depends(email_url)])
    async def confirm(token: str) -> dict[str, str]:
        """Echo the token the route received.

        Args:
            token (str): The path token.

        Returns:
            dict[str, str]: ``{"token": token}``.
        """
        return {"token": token}

    return app


@pytest.fixture
def client() -> TestClient:
    """Return a client over a fresh app.

    Returns:
        TestClient: The client.
    """
    return TestClient(_build_app())


def _files_url(key: str, *, expires_in: timedelta = timedelta(minutes=5)) -> str:
    """Sign the download URL of ``key`` for the ``files`` purpose.

    Args:
        key (str): The decoded object key.
        expires_in (timedelta): Validity window.

    Returns:
        str: The signed URL.
    """
    return sign_path(
        f"/api/files/{key}", secret=SECRET, expires_in=expires_in, purpose="files"
    )


class TestSignedPathDependency:
    """The dependency guarding a route, through real HTTP."""

    def test_valid_url_passes(self, client: TestClient) -> None:
        """A freshly signed URL reaches the handler."""
        response = client.get(_files_url("report.pdf"))
        assert response.status_code == 200
        assert response.json() == {"key": "report.pdf"}

    @pytest.mark.parametrize(
        "key", ["a b.pdf", "100%.pdf", "dir/sub/á.txt", "what?.txt", "x+y.txt"]
    )
    def test_awkward_keys_round_trip(self, client: TestClient, key: str) -> None:
        """Keys that need percent-encoding verify and arrive intact."""
        response = client.get(_files_url(key))
        assert response.status_code == 200
        assert response.json() == {"key": key}

    def test_encoded_slash_shares_the_signature(self, client: TestClient) -> None:
        """``%2F`` and ``/`` reach the route as the same path."""
        url = _files_url("a/b.pdf")
        response = client.get(url.replace("/a/b.pdf", "/a%2Fb.pdf"))
        assert response.status_code == 200
        assert response.json() == {"key": "a/b.pdf"}

    def test_altered_expires_is_403(self, client: TestClient) -> None:
        """Extending ``expires`` is rejected as invalid."""
        url = _files_url("report.pdf")
        expires = parse_qs(urlsplit(url).query)["expires"][0]
        response = client.get(url.replace(expires, str(int(expires) + 3600)))
        assert response.status_code == 403
        assert response.json()["code"] == "SIGNED_URL_INVALID"

    def test_altered_path_is_403(self, client: TestClient) -> None:
        """A signature for one key does not open another."""
        url = _files_url("report.pdf")
        response = client.get(url.replace("report.pdf", "salaries.pdf"))
        assert response.status_code == 403
        assert response.json()["code"] == "SIGNED_URL_INVALID"

    def test_other_purpose_is_403(self, client: TestClient) -> None:
        """A ``files`` signature does not open an ``email-link`` route."""
        url = sign_path(
            "/api/confirm/abc",
            secret=SECRET,
            expires_in=timedelta(minutes=5),
            purpose="files",
        )
        response = client.get(url)
        assert response.status_code == 403
        assert response.json()["code"] == "SIGNED_URL_INVALID"

    def test_expired_url_is_403(self, client: TestClient) -> None:
        """A URL signed an hour ago for five minutes is expired."""
        url = sign_path(
            "/api/files/report.pdf",
            secret=SECRET,
            expires_in=timedelta(minutes=5),
            purpose="files",
            now=utcnow() - timedelta(hours=1),
        )
        response = client.get(url)
        assert response.status_code == 403
        assert response.json()["code"] == "SIGNED_URL_EXPIRED"

    def test_other_secret_is_403(self, client: TestClient) -> None:
        """A URL signed with another secret is rejected."""
        url = sign_path(
            "/api/files/report.pdf",
            secret="another-secret",
            expires_in=timedelta(minutes=5),
            purpose="files",
        )
        response = client.get(url)
        assert response.status_code == 403
        assert response.json()["code"] == "SIGNED_URL_INVALID"

    @pytest.mark.parametrize(
        "query",
        [
            "",
            "?expires=1",
            "?signature=abc",
            "?expires=soon&signature=abc",
            "?expires=-1&signature=abc",
        ],
    )
    def test_missing_or_malformed_params_are_403(
        self, client: TestClient, query: str
    ) -> None:
        """Absent or non-numeric parameters are 403, never 422."""
        response = client.get(f"/api/files/report.pdf{query}")
        assert response.status_code == 403
        assert response.json()["code"] == "SIGNED_URL_INVALID"

    @pytest.mark.parametrize("digits", [4300, 4301, 5000])
    def test_oversized_expires_is_403(self, digits: int) -> None:
        """An ``expires`` past the int-conversion digit limit is 403, not 500.

        Python refuses ``int()`` over 4300 digits with ``ValueError``; the
        ``isdigit`` check lets such a value through, so the dependency must
        turn the conversion failure into the same invalid-URL answer.
        """
        client = TestClient(_build_app(), raise_server_exceptions=False)
        response = client.get(
            f"/api/files/report.pdf?expires={'9' * digits}&signature=abc"
        )
        assert response.status_code == 403
        assert response.json()["code"] == "SIGNED_URL_INVALID"

    def test_params_are_declared_in_openapi(self, client: TestClient) -> None:
        """``expires`` and ``signature`` appear as query parameters."""
        operation = client.app.openapi()["paths"]["/api/files/{key}"]["get"]
        names = {(p["name"], p["in"]) for p in operation["parameters"]}
        assert {("expires", "query"), ("signature", "query")} <= names

    def test_mounted_app_signs_the_full_path(self) -> None:
        """Under ``mount`` the route sees, and verifies, the mount prefix."""
        outer = FastAPI()
        outer.mount("/v1", _build_app())
        url = sign_path(
            "/v1/api/files/report.pdf",
            secret=SECRET,
            expires_in=timedelta(minutes=5),
            purpose="files",
        )
        response = TestClient(outer).get(url)
        assert response.status_code == 200

    @pytest.mark.parametrize(("secret", "purpose"), [("", "files"), ("s", "")])
    def test_empty_material_fails_at_build_time(
        self, secret: str, purpose: str
    ) -> None:
        """A missing setting fails when the dependency is built."""
        with pytest.raises(ValueError):
            make_signed_path_dependency(secret=secret, purpose=purpose)
