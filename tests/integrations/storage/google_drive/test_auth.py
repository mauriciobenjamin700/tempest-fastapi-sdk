"""The service-account token exchange, checked on the wire."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import jwt
import pytest

from tempest_fastapi_sdk.exceptions import GoogleDriveException
from tempest_fastapi_sdk.integrations.storage.google_drive import (
    ASSERTION_LIFETIME_SECONDS,
    DRIVE_FILE_SCOPE,
    DRIVE_SCOPE,
    JWT_BEARER_GRANT_TYPE,
    AccessTokenProvider,
    ServiceAccountCredentials,
    ServiceAccountTokenProvider,
)
from tests.integrations.storage.google_drive.conftest import (
    CLIENT_EMAIL,
    TOKEN_URL,
    FakeDrive,
)


class Clock:
    """A settable wall clock."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


class TestAssertionOnTheWire:
    async def test_token_request_carries_a_valid_rs256_assertion(
        self,
        fake: FakeDrive,
        http: httpx.AsyncClient,
        credentials_json: str,
        public_pem: bytes,
    ) -> None:
        clock = Clock(1_800_000_000.0)
        provider = ServiceAccountTokenProvider(
            ServiceAccountCredentials.from_json(credentials_json),
            http_client=http,
            clock=clock,
        )

        token = await provider.access_token()

        assert token == "ya29.token-1"
        (request,) = fake.requests
        assert str(request.url) == TOKEN_URL
        assert request.method == "POST"
        assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
        form = _form(request)
        assert set(form) == {"grant_type", "assertion"}
        assert form["grant_type"] == JWT_BEARER_GRANT_TYPE
        header = jwt.get_unverified_header(form["assertion"])
        assert header["alg"] == "RS256"
        assert header["kid"] == "kid-0001"
        claims = jwt.decode(
            form["assertion"],
            public_pem,
            algorithms=["RS256"],
            audience=TOKEN_URL,
            options={"verify_exp": False, "verify_iat": False},
        )
        assert claims == {
            "iss": CLIENT_EMAIL,
            "scope": DRIVE_SCOPE,
            "aud": TOKEN_URL,
            "iat": 1_800_000_000,
            "exp": 1_800_000_000 + ASSERTION_LIFETIME_SECONDS,
        }
        assert claims["exp"] - claims["iat"] <= 3600

    async def test_scopes_are_space_separated(
        self, fake: FakeDrive, http: httpx.AsyncClient, credentials_json: str
    ) -> None:
        provider = ServiceAccountTokenProvider(
            ServiceAccountCredentials.from_json(credentials_json),
            http_client=http,
            scopes=(DRIVE_FILE_SCOPE, DRIVE_SCOPE),
        )
        await provider.access_token()
        assertion = _form(fake.requests[0])["assertion"]
        claims = jwt.decode(assertion, options={"verify_signature": False})
        assert claims["scope"] == f"{DRIVE_FILE_SCOPE} {DRIVE_SCOPE}"

    async def test_assertion_signed_by_another_key_does_not_verify(
        self, fake: FakeDrive, http: httpx.AsyncClient, credentials_json: str
    ) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other_public = other.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        provider = ServiceAccountTokenProvider(
            ServiceAccountCredentials.from_json(credentials_json), http_client=http
        )
        await provider.access_token()
        assertion = _form(fake.requests[0])["assertion"]
        with pytest.raises(jwt.InvalidSignatureError):
            jwt.decode(
                assertion, other_public, algorithms=["RS256"], audience=TOKEN_URL
            )

    def test_provider_satisfies_the_protocol(
        self, http: httpx.AsyncClient, credentials_json: str
    ) -> None:
        provider = ServiceAccountTokenProvider(
            ServiceAccountCredentials.from_json(credentials_json), http_client=http
        )
        assert isinstance(provider, AccessTokenProvider)


class TestCache:
    async def test_token_is_reused_until_the_refresh_margin(
        self, fake: FakeDrive, http: httpx.AsyncClient, credentials_json: str
    ) -> None:
        clock = Clock(1_000.0)
        provider = ServiceAccountTokenProvider(
            ServiceAccountCredentials.from_json(credentials_json),
            http_client=http,
            clock=clock,
            refresh_margin_seconds=300,
        )
        assert await provider.access_token() == "ya29.token-1"
        clock.now = 1_000.0 + 3599 - 301
        assert await provider.access_token() == "ya29.token-1"
        assert fake.token_calls == 1
        clock.now = 1_000.0 + 3599 - 299
        assert await provider.access_token() == "ya29.token-2"
        assert fake.token_calls == 2

    async def test_concurrent_callers_share_one_exchange(
        self, credentials_json: str
    ) -> None:
        calls = 0

        async def slow_token(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            return httpx.Response(
                200, json={"access_token": f"tok-{calls}", "expires_in": 3599}
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(slow_token)
        ) as client:
            provider = ServiceAccountTokenProvider(
                ServiceAccountCredentials.from_json(credentials_json),
                http_client=client,
            )
            tokens = await asyncio.gather(*(provider.access_token() for _ in range(20)))
        assert calls == 1
        assert set(tokens) == {"tok-1"}


class TestErrors:
    async def test_refused_assertion_maps_reason_and_hides_secrets(
        self,
        fake: FakeDrive,
        http: httpx.AsyncClient,
        credentials_json: str,
        private_pem: str,
    ) -> None:
        fake.token_status = 400
        provider = ServiceAccountTokenProvider(
            ServiceAccountCredentials.from_json(credentials_json), http_client=http
        )
        with pytest.raises(GoogleDriveException) as raised:
            await provider.access_token()
        error = raised.value
        assert error.status_code == 502
        assert error.upstream_status == 400
        assert error.reason == "invalid_grant"
        assert error.details == {"status": 400, "reason": "invalid_grant"}
        assert "Invalid JWT Signature." in error.message
        assertion = _form(fake.requests[0])["assertion"]
        rendered = f"{error.message} {error.details} {error!r}"
        assert assertion not in rendered
        assert "PRIVATE KEY" not in rendered
        assert private_pem.splitlines()[1] not in rendered

    async def test_unreachable_endpoint_is_a_drive_error(
        self, credentials_json: str
    ) -> None:
        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as client:
            provider = ServiceAccountTokenProvider(
                ServiceAccountCredentials.from_json(credentials_json),
                http_client=client,
            )
            with pytest.raises(GoogleDriveException) as raised:
                await provider.access_token()
        assert raised.value.upstream_status is None
        assert raised.value.reason == "token_endpoint_unreachable"

    async def test_missing_pyjwt_names_the_extra(
        self,
        http: httpx.AsyncClient,
        credentials_json: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(sys.modules, "jwt", None)
        provider = ServiceAccountTokenProvider(
            ServiceAccountCredentials.from_json(credentials_json), http_client=http
        )
        with pytest.raises(ImportError, match=r"\[google-drive\]"):
            await provider.access_token()

    @pytest.mark.parametrize(
        "pem",
        [
            "not a pem at all",
            "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n",
        ],
    )
    async def test_unparsable_private_key_is_an_actionable_drive_error(
        self, fake: FakeDrive, http: httpx.AsyncClient, credentials_json: str, pem: str
    ) -> None:
        info = json.loads(credentials_json)
        info["private_key"] = pem
        provider = ServiceAccountTokenProvider(
            ServiceAccountCredentials.from_json(json.dumps(info)), http_client=http
        )
        with pytest.raises(GoogleDriveException) as raised:
            await provider.access_token()
        error = raised.value
        assert error.reason == "invalid_private_key"
        assert error.upstream_status is None
        assert "private_key" in error.message
        assert CLIENT_EMAIL in error.message
        assert pem not in f"{error.message} {error.details} {error!r}"
        assert fake.requests == []

    def test_empty_scopes_are_refused(
        self, http: httpx.AsyncClient, credentials_json: str
    ) -> None:
        with pytest.raises(ValueError, match="scope"):
            ServiceAccountTokenProvider(
                ServiceAccountCredentials.from_json(credentials_json),
                http_client=http,
                scopes=(),
            )


class TestCredentials:
    def test_inline_json_wins_over_the_file(
        self, tmp_path: Path, credentials_json: str
    ) -> None:
        other = json.loads(credentials_json)
        other["client_email"] = "file@demo.iam.gserviceaccount.com"
        path = tmp_path / "sa.json"
        path.write_text(json.dumps(other))
        loaded = ServiceAccountCredentials.load(
            credentials_path=path, credentials_json=credentials_json
        )
        assert loaded.client_email == CLIENT_EMAIL
        from_file = ServiceAccountCredentials.load(credentials_path=str(path))
        assert from_file.client_email == "file@demo.iam.gserviceaccount.com"

    def test_no_source_is_refused(self) -> None:
        with pytest.raises(ValueError, match="GOOGLE_DRIVE_CREDENTIALS"):
            ServiceAccountCredentials.load()

    def test_not_a_service_account_is_refused(self, credentials_json: str) -> None:
        info = json.loads(credentials_json)
        info["type"] = "authorized_user"
        with pytest.raises(ValueError, match="service_account"):
            ServiceAccountCredentials.from_json(json.dumps(info))

    @pytest.mark.parametrize("missing", ["client_email", "private_key"])
    def test_missing_field_is_named(self, credentials_json: str, missing: str) -> None:
        info = json.loads(credentials_json)
        del info[missing]
        with pytest.raises(ValueError, match=missing):
            ServiceAccountCredentials.from_json(json.dumps(info))

    def test_invalid_json_does_not_echo_the_text(self) -> None:
        secret = '{"private_key": "-----BEGIN PRIVATE KEY-----abc'
        with pytest.raises(ValueError) as raised:
            ServiceAccountCredentials.from_json(secret)
        assert "abc" not in str(raised.value)
        assert raised.value.__cause__ is None

    def test_repr_hides_the_private_key(self, credentials_json: str) -> None:
        credentials = ServiceAccountCredentials.from_json(credentials_json)
        assert "PRIVATE KEY" not in repr(credentials)
        assert CLIENT_EMAIL in repr(credentials)

    def test_token_uri_defaults_to_google(self, credentials_json: str) -> None:
        info = json.loads(credentials_json)
        del info["token_uri"]
        credentials = ServiceAccountCredentials.from_info(info)
        assert credentials.token_uri == TOKEN_URL
