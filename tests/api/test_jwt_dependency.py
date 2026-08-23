"""Tests for the JWT bearer + current-user dependency factories."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    ACCESS_TOKEN_TYPE,
    MFA_TOKEN_TYPE,
    REFRESH_TOKEN_TYPE,
    JWTUtils,
    make_bearer_token_dependency,
    make_jwt_user_dependency,
    register_exception_handlers,
)


def _make_tokens() -> JWTUtils:
    return JWTUtils(secret="a" * 32)


async def _load_user(subject: str) -> dict[str, str]:
    """Synthetic user loader — echoes the subject back."""
    return {"id": subject, "name": f"user-{subject}"}


def _make_bearer_app(*, soft: bool) -> tuple[FastAPI, JWTUtils]:
    tokens = _make_tokens()
    decode = make_bearer_token_dependency(tokens, soft=soft)

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/claims")
    async def claims(
        payload: dict[str, Any] | None = Depends(decode),
    ) -> dict[str, Any]:
        return {"payload": payload}

    return app, tokens


def _make_user_app(*, soft: bool) -> tuple[FastAPI, JWTUtils]:
    tokens = _make_tokens()
    current_user = make_jwt_user_dependency(tokens, _load_user, soft=soft)

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/me")
    async def me(user: dict[str, str] | None = Depends(current_user)) -> dict[str, Any]:
        return {"user": user}

    return app, tokens


@pytest.mark.asyncio
async def test_bearer_dependency_returns_claims_for_valid_token() -> None:
    app, tokens = _make_bearer_app(soft=False)
    token = tokens.encode({"sub": "42"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/claims",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json()["payload"]["sub"] == "42"


@pytest.mark.asyncio
async def test_bearer_dependency_rejects_missing_token() -> None:
    app, _ = _make_bearer_app(soft=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/claims")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bearer_dependency_rejects_invalid_token() -> None:
    app, _ = _make_bearer_app(soft=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/claims",
            headers={"Authorization": "Bearer not-a-jwt"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bearer_dependency_soft_returns_none_when_missing() -> None:
    app, _ = _make_bearer_app(soft=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/claims")
    assert response.status_code == 200
    assert response.json() == {"payload": None}


@pytest.mark.asyncio
async def test_bearer_dependency_soft_returns_none_when_invalid() -> None:
    app, _ = _make_bearer_app(soft=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/claims",
            headers={"Authorization": "Bearer not-a-jwt"},
        )
    assert response.status_code == 200
    assert response.json() == {"payload": None}


@pytest.mark.asyncio
async def test_jwt_user_dependency_loads_user_from_subject() -> None:
    app, tokens = _make_user_app(soft=False)
    token = tokens.encode({"sub": "abc-123"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/me",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["id"] == "abc-123"
    assert body["user"]["name"] == "user-abc-123"


@pytest.mark.asyncio
async def test_jwt_user_dependency_rejects_missing_token() -> None:
    app, _ = _make_user_app(soft=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_jwt_user_dependency_soft_returns_none() -> None:
    app, _ = _make_user_app(soft=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/me")
    assert response.status_code == 200
    assert response.json() == {"user": None}


@pytest.mark.asyncio
async def test_jwt_user_dependency_rejects_missing_subject() -> None:
    app, tokens = _make_user_app(soft=False)
    # Token without a "sub" claim.
    token = tokens.encode({"role": "admin"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/me",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bearer_dependency_reads_token_from_query_param() -> None:
    """Cookieless clients (EventSource) can pass the JWT in the query."""
    tokens = _make_tokens()
    decode = make_bearer_token_dependency(tokens, query_param="access_token")

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/claims")
    async def claims(
        payload: dict[str, Any] | None = Depends(decode),
    ) -> dict[str, Any]:
        return {"payload": payload}

    token = tokens.encode({"sub": "77"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/claims?access_token={token}")
    assert response.status_code == 200
    assert response.json()["payload"]["sub"] == "77"


@pytest.mark.asyncio
async def test_bearer_dependency_header_wins_over_query_param() -> None:
    """The Authorization header takes precedence over the query string."""
    tokens = _make_tokens()
    decode = make_bearer_token_dependency(tokens, query_param="access_token")

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/claims")
    async def claims(
        payload: dict[str, Any] | None = Depends(decode),
    ) -> dict[str, Any]:
        return {"payload": payload}

    header_token = tokens.encode({"sub": "header"})
    query_token = tokens.encode({"sub": "query"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/claims?access_token={query_token}",
            headers={"Authorization": f"Bearer {header_token}"},
        )
    assert response.status_code == 200
    assert response.json()["payload"]["sub"] == "header"


@pytest.mark.asyncio
async def test_jwt_user_dependency_reads_token_from_query_param() -> None:
    """current_user resolves from a query-string token end to end."""
    tokens = _make_tokens()
    current_user = make_jwt_user_dependency(
        tokens,
        _load_user,
        query_param="access_token",
    )

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/me")
    async def me(user: dict[str, str] | None = Depends(current_user)) -> dict[str, Any]:
        return {"user": user}

    token = tokens.encode({"sub": "sse-1"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/me?access_token={token}")
    assert response.status_code == 200
    assert response.json()["user"]["id"] == "sse-1"


@pytest.mark.asyncio
async def test_jwt_user_dependency_respects_custom_subject_claim() -> None:
    tokens = _make_tokens()
    current_user = make_jwt_user_dependency(
        tokens,
        _load_user,
        subject_claim="user_id",
    )

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/me")
    async def me(user: dict[str, str] = Depends(current_user)) -> dict[str, Any]:
        return {"user": user}

    token = tokens.encode({"user_id": "xyz"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/me",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json()["user"]["id"] == "xyz"


class TestTokenTypeIsolation:
    """A validly-signed token of the wrong kind must not authorize a request.

    Access, refresh and MFA-pending tokens share one signing secret, so the
    signature check alone cannot tell them apart. These cover the ``typ``
    gate that does.
    """

    async def test_mfa_pending_token_is_rejected_as_access(self) -> None:
        app, tokens = _make_bearer_app(soft=False)
        token = tokens.encode({"sub": "u1", "typ": MFA_TOKEN_TYPE})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/claims",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 401

    async def test_refresh_token_is_rejected_as_access(self) -> None:
        app, tokens = _make_bearer_app(soft=False)
        token = tokens.encode({"sub": "u1", "typ": REFRESH_TOKEN_TYPE})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/claims",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 401

    async def test_legacy_refresh_marker_is_rejected_as_access(self) -> None:
        app, tokens = _make_bearer_app(soft=False)
        token = tokens.encode({"sub": "u1", "refresh": True})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/claims",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 401

    async def test_access_token_passes(self) -> None:
        app, tokens = _make_bearer_app(soft=False)
        token = tokens.encode({"sub": "u1", "typ": ACCESS_TOKEN_TYPE})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/claims",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        assert response.json()["payload"]["sub"] == "u1"

    async def test_soft_mode_downgrades_wrong_type_to_anonymous(self) -> None:
        app, tokens = _make_bearer_app(soft=True)
        token = tokens.encode({"sub": "u1", "typ": MFA_TOKEN_TYPE})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/claims",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        assert response.json()["payload"] is None

    async def test_user_dependency_rejects_mfa_pending_token(self) -> None:
        app, tokens = _make_user_app(soft=False)
        token = tokens.encode({"sub": "u1", "typ": MFA_TOKEN_TYPE})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/me",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 401

    async def test_widened_accepted_typ_admits_refresh(self) -> None:
        tokens = _make_tokens()
        decode = make_bearer_token_dependency(
            tokens,
            accepted_typ=(REFRESH_TOKEN_TYPE,),
        )

        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/claims")
        async def claims(
            payload: dict[str, Any] | None = Depends(decode),
        ) -> dict[str, Any]:
            return {"payload": payload}

        token = tokens.encode({"sub": "u1", "typ": REFRESH_TOKEN_TYPE})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/claims",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200


class TestLoaderDecliningTheSubject:
    """A loader that returns ``None`` refuses the request.

    ``user_loader`` is documented as the seam where a service refuses a
    subject — deactivated account, id that no longer resolves, malformed
    subject. Until v0.252.0 that ``None`` reached the handler, so the route
    answered 200 with a user the loader had rejected: deactivating an account
    had no effect until the access token expired.
    """

    @staticmethod
    async def _decline(subject: str) -> None:
        """Refuse every subject, the way a deactivated account would.

        Args:
            subject (str): The subject claim, ignored.

        Returns:
            None: Always, which is the refusal.
        """
        return None

    @staticmethod
    async def _decline_with_session(subject: str, session: Any) -> None:
        """Same refusal, on the shared-session branch.

        Args:
            subject (str): The subject claim, ignored.
            session (Any): The request-scoped session, ignored.

        Returns:
            None: Always.
        """
        return None

    @staticmethod
    async def _session() -> Any:
        """Stand in for the request-scoped session dependency.

        Returns:
            Any: An opaque object; the loader never touches it.
        """
        return object()

    def _app(
        self,
        *,
        soft: bool,
        shared: bool,
    ) -> tuple[FastAPI, JWTUtils]:
        """Build an app whose loader declines every subject.

        Args:
            soft (bool): Passed through to the dependency factory.
            shared (bool): Whether to exercise the shared-session branch,
                which is a second closure with its own copy of the rule.

        Returns:
            tuple[FastAPI, JWTUtils]: The app and its token helper.
        """
        tokens = _make_tokens()
        current_user = (
            make_jwt_user_dependency(
                tokens,
                self._decline_with_session,
                soft=soft,
                session_dependency=self._session,
            )
            if shared
            else make_jwt_user_dependency(tokens, self._decline, soft=soft)
        )

        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/me")
        async def me(user: Any = Depends(current_user)) -> dict[str, Any]:
            return {"user": user}

        return app, tokens

    async def _get(self, app: FastAPI, tokens: JWTUtils) -> Any:
        """Call ``/me`` with a valid token.

        Args:
            app (FastAPI): The app under test.
            tokens (JWTUtils): Its token helper.

        Returns:
            Any: The HTTP response.
        """
        token = tokens.encode({"sub": "b0a5a5f6-0000-4000-8000-000000000000"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(
                "/me",
                headers={"Authorization": f"Bearer {token}"},
            )

    @pytest.mark.asyncio
    async def test_declined_subject_is_unauthorized(self) -> None:
        """Same answer an absent subject already got."""
        app, tokens = self._app(soft=False, shared=False)
        assert (await self._get(app, tokens)).status_code == 401

    @pytest.mark.asyncio
    async def test_declined_subject_is_unauthorized_on_shared_session(self) -> None:
        """The other closure carries the rule too."""
        app, tokens = self._app(soft=False, shared=True)
        assert (await self._get(app, tokens)).status_code == 401

    @pytest.mark.asyncio
    async def test_soft_mode_still_yields_none(self) -> None:
        """Anonymous access is what ``soft=True`` is for."""
        app, tokens = self._app(soft=True, shared=False)
        response = await self._get(app, tokens)
        assert response.status_code == 200
        assert response.json() == {"user": None}

    @pytest.mark.asyncio
    async def test_soft_mode_still_yields_none_on_shared_session(self) -> None:
        """Both branches agree in soft mode as well."""
        app, tokens = self._app(soft=True, shared=True)
        response = await self._get(app, tokens)
        assert response.status_code == 200
        assert response.json() == {"user": None}

    @pytest.mark.asyncio
    async def test_a_loaded_user_still_reaches_the_handler(self) -> None:
        """The refusal is about ``None``, not about every falsy user."""
        app, tokens = _make_user_app(soft=False)
        token = tokens.encode({"sub": "u1"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/me",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        assert response.json()["user"]["id"] == "u1"
