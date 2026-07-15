"""Tests for introspection-based bearer authentication."""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.security import HTTPAuthorizationCredentials

from tempest_fastapi_sdk import IntrospectionAuth, register_exception_handlers
from tempest_fastapi_sdk.exceptions import (
    ForbiddenException,
    UnauthorizedException,
)

_SUBJECT = uuid4()


class _CountingHandler:
    """Callable httpx handler that records how often it was invoked.

    Attributes:
        calls (int): The number of requests the handler has served.
        status_code (int): The status code returned for each request.
        payload (dict[str, Any]): The JSON body returned on success.
        last_authorization (str | None): The last ``Authorization`` header
            seen, for asserting the bearer was forwarded.
    """

    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the counting handler.

        Args:
            status_code (int): Status code to return. Defaults to ``200``.
            payload (dict[str, Any] | None): JSON body for ``200`` responses.
                Defaults to a minimal claims dict with a subject.
        """
        self.calls: int = 0
        self.status_code: int = status_code
        self.payload: dict[str, Any] = (
            payload
            if payload is not None
            else {"sub": str(_SUBJECT), "access_apps": ["famacha"]}
        )
        self.last_authorization: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Serve a request, incrementing the call counter.

        Args:
            request (httpx.Request): The incoming request.

        Returns:
            httpx.Response: The configured response.
        """
        self.calls += 1
        self.last_authorization = request.headers.get("Authorization")
        return httpx.Response(self.status_code, json=self.payload)


def _client(handler: _CountingHandler) -> httpx.AsyncClient:
    """Build an AsyncClient backed by a MockTransport around a handler.

    Args:
        handler (_CountingHandler): The handler serving mocked responses.

    Returns:
        httpx.AsyncClient: A client whose requests hit the handler.
    """
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _credentials(token: str) -> HTTPAuthorizationCredentials:
    """Wrap a raw token as bearer credentials.

    Args:
        token (str): The raw bearer token.

    Returns:
        HTTPAuthorizationCredentials: The bearer credentials object.
    """
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


class TestFetchUserinfo:
    async def test_valid_token_returns_claims(self) -> None:
        handler = _CountingHandler()
        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            http_client=_client(handler),
        )
        claims = await auth.fetch_userinfo("tok")
        assert claims["sub"] == str(_SUBJECT)
        assert handler.last_authorization == "Bearer tok"

    async def test_cache_hit_skips_upstream(self) -> None:
        handler = _CountingHandler()
        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            cache_ttl_seconds=60,
            http_client=_client(handler),
        )
        await auth.fetch_userinfo("tok")
        await auth.fetch_userinfo("tok")
        assert handler.calls == 1

    async def test_ttl_expiry_rehits_upstream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        handler = _CountingHandler()
        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            cache_ttl_seconds=30,
            http_client=_client(handler),
        )
        clock = {"now": 1_000.0}
        monkeypatch.setattr(time, "monotonic", lambda: clock["now"])
        await auth.fetch_userinfo("tok")
        clock["now"] += 31.0
        await auth.fetch_userinfo("tok")
        assert handler.calls == 2

    async def test_zero_ttl_disables_cache(self) -> None:
        handler = _CountingHandler()
        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            cache_ttl_seconds=0,
            http_client=_client(handler),
        )
        await auth.fetch_userinfo("tok")
        await auth.fetch_userinfo("tok")
        assert handler.calls == 2

    async def test_401_raises_and_evicts_cache(self) -> None:
        handler = _CountingHandler(status_code=401)
        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            cache_ttl_seconds=60,
            http_client=_client(handler),
        )
        with pytest.raises(UnauthorizedException):
            await auth.fetch_userinfo("tok")
        assert "tok" not in auth._cache

    async def test_403_raises_unauthorized(self) -> None:
        handler = _CountingHandler(status_code=403)
        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            http_client=_client(handler),
        )
        with pytest.raises(UnauthorizedException):
            await auth.fetch_userinfo("tok")

    async def test_other_non_200_raises_unauthorized(self) -> None:
        handler = _CountingHandler(status_code=500)
        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            http_client=_client(handler),
        )
        with pytest.raises(UnauthorizedException):
            await auth.fetch_userinfo("tok")

    async def test_unreachable_raises_unauthorized(self) -> None:
        def _boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(_boom)),
        )
        with pytest.raises(UnauthorizedException):
            await auth.fetch_userinfo("tok")

    async def test_callable_userinfo_url_is_resolved(self) -> None:
        handler = _CountingHandler()
        seen: list[str] = []

        def _url() -> str:
            seen.append("resolved")
            return "https://id.example.com/users/me"

        auth = IntrospectionAuth(
            userinfo_url=_url,
            http_client=_client(handler),
        )
        await auth.fetch_userinfo("tok")
        assert seen == ["resolved"]


class TestGetClaims:
    async def test_missing_credentials_raises_unauthorized(self) -> None:
        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            http_client=_client(_CountingHandler()),
        )
        with pytest.raises(UnauthorizedException):
            await auth.get_claims(None)

    async def test_app_gate_pass(self) -> None:
        handler = _CountingHandler(
            payload={"sub": str(_SUBJECT), "access_apps": ["famacha"]}
        )
        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            required_app="famacha",
            http_client=_client(handler),
        )
        claims = await auth.get_claims(_credentials("tok"))
        assert claims["sub"] == str(_SUBJECT)

    async def test_app_gate_fail_raises_forbidden(self) -> None:
        handler = _CountingHandler(
            payload={"sub": str(_SUBJECT), "access_apps": ["other"]}
        )
        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            required_app="famacha",
            http_client=_client(handler),
        )
        with pytest.raises(ForbiddenException):
            await auth.get_claims(_credentials("tok"))

    async def test_app_gate_missing_claim_raises_forbidden(self) -> None:
        handler = _CountingHandler(payload={"sub": str(_SUBJECT)})
        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            required_app="famacha",
            http_client=_client(handler),
        )
        with pytest.raises(ForbiddenException):
            await auth.get_claims(_credentials("tok"))

    async def test_custom_app_claim(self) -> None:
        handler = _CountingHandler(payload={"sub": str(_SUBJECT), "apps": ["famacha"]})
        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            required_app="famacha",
            app_claim="apps",
            http_client=_client(handler),
        )
        claims = await auth.get_claims(_credentials("tok"))
        assert claims["apps"] == ["famacha"]


class TestGetUserId:
    async def test_valid_subject_returns_uuid(self) -> None:
        handler = _CountingHandler()
        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            http_client=_client(handler),
        )
        assert await auth.get_user_id(_credentials("tok")) == _SUBJECT

    async def test_invalid_subject_raises_unauthorized(self) -> None:
        handler = _CountingHandler(payload={"sub": "not-a-uuid"})
        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            http_client=_client(handler),
        )
        with pytest.raises(UnauthorizedException):
            await auth.get_user_id(_credentials("tok"))

    async def test_missing_subject_raises_unauthorized(self) -> None:
        handler = _CountingHandler(payload={"other": "x"})
        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            http_client=_client(handler),
        )
        with pytest.raises(UnauthorizedException):
            await auth.get_user_id(_credentials("tok"))

    async def test_custom_subject_claim(self) -> None:
        handler = _CountingHandler(payload={"user_id": str(_SUBJECT)})
        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            subject_claim="user_id",
            http_client=_client(handler),
        )
        assert await auth.get_user_id(_credentials("tok")) == _SUBJECT


class TestFastAPIWiring:
    async def test_dependencies_wire_end_to_end(self) -> None:
        handler = _CountingHandler(
            payload={"sub": str(_SUBJECT), "access_apps": ["famacha"]}
        )
        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            required_app="famacha",
            http_client=_client(handler),
        )
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/claims")
        async def read_claims(
            claims: dict[str, Any] = Depends(auth.get_claims),
        ) -> dict[str, Any]:
            return claims

        @app.get("/user-id")
        async def read_user_id(
            user_id: UUID = Depends(auth.get_user_id),
        ) -> dict[str, str]:
            return {"user_id": str(user_id)}

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            ok = await client.get("/claims", headers={"Authorization": "Bearer tok"})
            assert ok.status_code == 200
            assert ok.json()["sub"] == str(_SUBJECT)

            uid = await client.get("/user-id", headers={"Authorization": "Bearer tok"})
            assert uid.status_code == 200
            assert uid.json()["user_id"] == str(_SUBJECT)

            missing = await client.get("/claims")
            assert missing.status_code == 401

    async def test_forbidden_when_app_not_granted(self) -> None:
        handler = _CountingHandler(
            payload={"sub": str(_SUBJECT), "access_apps": ["other"]}
        )
        auth = IntrospectionAuth(
            userinfo_url="https://id.example.com/users/me",
            required_app="famacha",
            http_client=_client(handler),
        )
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/claims")
        async def read_claims(
            claims: dict[str, Any] = Depends(auth.get_claims),
        ) -> dict[str, Any]:
            return claims

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get(
                "/claims", headers={"Authorization": "Bearer tok"}
            )
            assert response.status_code == 403
