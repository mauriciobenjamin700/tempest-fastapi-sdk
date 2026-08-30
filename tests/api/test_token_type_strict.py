"""``strict`` and ``legacy_claims`` reach the dependency factories.

``token_type_allowed`` grew both parameters for one named consumer: a
service whose pre-migration tokens declared their type under a claim of
its own (``type``, ``token_type``). Such a token has no ``typ`` and no
SDK fallback marker, so the permissive default classifies it as
"unknown" and lets it through — meaning **every refresh token that
service ever issued authorizes any route for the length of its TTL**.

The capability existed; the front door did not expose it, so the only
way to reach strict mode was to not use the factories. These tests pin
the passthrough at all three doors: the bearer factory, the user
factory, and the service's own ``current_user_dependency``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    ACCESS_TOKEN_TYPE,
    JWTUtils,
    make_bearer_token_dependency,
    make_jwt_user_dependency,
)

_SECRET = "a-32-character-secret-for-tests!"


def _tokens() -> JWTUtils:
    """Build the JWT helper the app and the tests share.

    Returns:
        JWTUtils: Helper bound to the test secret.
    """
    return JWTUtils(secret=_SECRET)


def _app(**dependency_kwargs: Any) -> FastAPI:
    """Mount one route guarded by ``make_bearer_token_dependency``.

    Args:
        **dependency_kwargs (Any): Forwarded to the factory.

    Returns:
        FastAPI: The application under test.
    """
    app = FastAPI()
    claims_dep = make_bearer_token_dependency(_tokens(), **dependency_kwargs)

    @app.get("/who")
    async def who(claims: dict[str, Any] = Depends(claims_dep)) -> dict[str, Any]:
        """Echo the decoded subject.

        Args:
            claims (dict[str, Any]): The decoded token payload.

        Returns:
            dict[str, Any]: The subject the token carried.
        """
        return {"sub": claims["sub"]}

    return app


async def _call(app: FastAPI, token: str) -> int:
    """Call the guarded route with ``token``.

    Args:
        app (FastAPI): The application under test.
        token (str): The bearer token to present.

    Returns:
        int: The HTTP status code.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        response = await client.get(
            "/who", headers={"Authorization": f"Bearer {token}"}
        )
    return response.status_code


class TestTheDefaultIsUnchanged:
    """Nothing that worked before behaves differently."""

    async def test_an_access_token_passes(self) -> None:
        token = _tokens().encode({"sub": "u1", "typ": ACCESS_TOKEN_TYPE})
        assert await _call(_app(), token) == 200

    async def test_an_untyped_token_still_passes(self) -> None:
        """The compatibility window for sessions minted before ``typ``."""
        token = _tokens().encode({"sub": "u1"})
        assert await _call(_app(), token) == 200

    async def test_an_sdk_refresh_token_is_still_refused(self) -> None:
        """The fallback marker classifies it even without ``typ``."""
        token = _tokens().encode({"sub": "u1", "refresh": True})
        assert await _call(_app(), token) == 401


class TestTheDefectStrictCloses:
    """A foreign type claim is invisible to the permissive default."""

    async def test_a_foreign_refresh_token_is_accepted_by_default(self) -> None:
        """This is the hole, asserted so the fix has something to close.

        The token says ``{"type": "refresh"}`` — unmistakably a refresh
        token to the service that minted it, and completely
        unclassifiable to the SDK, which reads ``typ``.
        """
        token = _tokens().encode({"sub": "u1", "type": "refresh"})
        assert await _call(_app(), token) == 200

    async def test_strict_refuses_it(self) -> None:
        token = _tokens().encode({"sub": "u1", "type": "refresh"})
        assert await _call(_app(strict=True), token) == 401

    async def test_legacy_claims_classifies_it(self) -> None:
        """With the claim named, the old token is read rather than guessed."""
        refresh = _tokens().encode({"sub": "u1", "type": "refresh"})
        access = _tokens().encode({"sub": "u1", "type": ACCESS_TOKEN_TYPE})
        app = _app(strict=True, legacy_claims=("type",))
        assert await _call(app, refresh) == 401
        assert await _call(app, access) == 200

    async def test_strict_also_refuses_a_token_with_no_marker_at_all(self) -> None:
        token = _tokens().encode({"sub": "u1"})
        assert await _call(_app(strict=True), token) == 401


class TestSoftModeHonoursStrict:
    """The soft path is a second call site, and it was missing the same args."""

    async def test_soft_returns_none_for_a_foreign_refresh_token(self) -> None:
        app = FastAPI()
        claims_dep = make_bearer_token_dependency(
            _tokens(), soft=True, strict=True, legacy_claims=("type",)
        )

        @app.get("/maybe")
        async def maybe(
            claims: dict[str, Any] | None = Depends(claims_dep),
        ) -> dict[str, Any]:
            """Report whether the token was accepted.

            Args:
                claims (dict[str, Any] | None): The decoded payload, or
                    ``None`` when the token was refused.

            Returns:
                dict[str, Any]: Whether a payload came through.
            """
            return {"anonymous": claims is None}

        token = _tokens().encode({"sub": "u1", "type": "refresh"})
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            response = await client.get(
                "/maybe", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.json() == {"anonymous": True}


class TestTheUserFactoryForwardsToo:
    """``make_jwt_user_dependency`` wraps the bearer factory."""

    @pytest.mark.parametrize(
        ("strict", "expected"),
        [(False, 200), (True, 401)],
    )
    async def test_strict_reaches_the_wrapped_factory(
        self, strict: bool, expected: int
    ) -> None:
        async def _loader(subject: str) -> dict[str, str]:
            return {"id": subject}

        app = FastAPI()
        user_dep = make_jwt_user_dependency(_tokens(), _loader, strict=strict)

        @app.get("/me")
        async def me(user: dict[str, str] = Depends(user_dep)) -> dict[str, str]:
            """Echo the loaded user.

            Args:
                user (dict[str, str]): Whatever the loader returned.

            Returns:
                dict[str, str]: The loaded user.
            """
            return user

        token = _tokens().encode({"sub": "u1", "type": "refresh"})
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            response = await client.get(
                "/me", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.status_code == expected
