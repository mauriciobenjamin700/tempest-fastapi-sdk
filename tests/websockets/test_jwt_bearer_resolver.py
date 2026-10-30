"""Tests for ``make_jwt_bearer_resolver``.

The router delegates the whole handshake decision to its resolver. The one
the recipe used to show decoded the JWT and returned ``sub``, so an
MFA-pending token (handed out after the password, before the second factor)
or a refresh token, both signed with the same secret, opened a full socket.
The SDK resolver applies the same ``typ`` check as the HTTP guards.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import (
    ACCESS_TOKEN_TYPE,
    MFA_TOKEN_TYPE,
    REFRESH_TOKEN_TYPE,
    STREAM_TOKEN_TYPE,
    JWTUtils,
    WebSocketConnection,
    WebSocketHub,
    WebSocketSettings,
    make_jwt_bearer_resolver,
    make_websocket_router,
)

USER_ID: UUID = UUID("11111111-1111-1111-1111-111111111111")


def _tokens() -> JWTUtils:
    """Build the shared signing helper.

    Returns:
        JWTUtils: The helper every token in these tests is signed with.
    """
    return JWTUtils(secret="s" * 32)


def _app(tokens: JWTUtils) -> FastAPI:
    """Mount a router that echoes the resolved user id.

    Args:
        tokens (JWTUtils): The signing helper the resolver checks against.

    Returns:
        FastAPI: The application.
    """

    async def handler(
        ws: WebSocket,
        connection: WebSocketConnection,
        hub: WebSocketHub,
    ) -> None:
        """Send the connection's user id once.

        Args:
            ws (WebSocket): The accepted socket.
            connection (WebSocketConnection): The hub handle.
            hub (WebSocketHub): The shared hub.
        """
        await ws.send_json({"type": "whoami", "data": str(connection.user_id)})

    app = FastAPI()
    app.include_router(
        make_websocket_router(
            handler,
            hub=WebSocketHub(),
            bearer_resolver=make_jwt_bearer_resolver(tokens),
            settings=WebSocketSettings(
                WS_HEARTBEAT_SECONDS=3600,
                WS_HEARTBEAT_TIMEOUT_SECONDS=3600,
            ),
        )
    )
    return app


class TestTheResolver:
    async def test_an_access_token_resolves_to_its_subject(self) -> None:
        tokens = _tokens()
        resolve = make_jwt_bearer_resolver(tokens)
        token = tokens.encode({"sub": str(USER_ID), "typ": ACCESS_TOKEN_TYPE})
        assert await resolve(token) == USER_ID

    @pytest.mark.parametrize(
        "claims",
        [
            pytest.param(
                {"typ": MFA_TOKEN_TYPE, "purpose": "mfa_pending"}, id="mfa-pending"
            ),
            pytest.param({"typ": REFRESH_TOKEN_TYPE, "refresh": True}, id="refresh"),
            pytest.param({"typ": STREAM_TOKEN_TYPE}, id="stream-ticket"),
            pytest.param({"purpose": "password_reset"}, id="reset-purpose"),
            pytest.param({}, id="untyped"),
        ],
    )
    async def test_every_other_token_is_refused(self, claims: dict[str, Any]) -> None:
        tokens = _tokens()
        resolve = make_jwt_bearer_resolver(tokens)
        assert await resolve(tokens.encode({"sub": str(USER_ID), **claims})) is None

    async def test_an_untyped_token_passes_only_when_not_strict(self) -> None:
        tokens = _tokens()
        resolve = make_jwt_bearer_resolver(tokens, strict=False)
        assert await resolve(tokens.encode({"sub": str(USER_ID)})) == USER_ID

    @pytest.mark.parametrize(
        "claims",
        [
            pytest.param({"typ": ACCESS_TOKEN_TYPE}, id="no-sub"),
            pytest.param({"typ": ACCESS_TOKEN_TYPE, "sub": "not-a-uuid"}, id="bad"),
        ],
    )
    async def test_a_missing_or_malformed_subject_is_refused(
        self, claims: dict[str, Any]
    ) -> None:
        tokens = _tokens()
        resolve = make_jwt_bearer_resolver(tokens)
        assert await resolve(tokens.encode(claims)) is None

    async def test_a_token_signed_elsewhere_is_refused(self) -> None:
        other = JWTUtils(secret="o" * 32)
        resolve = make_jwt_bearer_resolver(_tokens())
        token = other.encode({"sub": str(uuid4()), "typ": ACCESS_TOKEN_TYPE})
        assert await resolve(token) is None


class TestTheHandshake:
    def test_an_access_token_opens_the_socket(self) -> None:
        tokens = _tokens()
        token = tokens.encode({"sub": str(USER_ID), "typ": ACCESS_TOKEN_TYPE})
        with (
            TestClient(_app(tokens)) as client,
            client.websocket_connect("/ws", subprotocols=["bearer", token]) as ws,
        ):
            assert ws.receive_json()["type"] == "hello"
            assert ws.receive_json()["data"] == str(USER_ID)

    @pytest.mark.parametrize(
        "claims",
        [
            pytest.param(
                {"typ": MFA_TOKEN_TYPE, "purpose": "mfa_pending"}, id="mfa-pending"
            ),
            pytest.param({"typ": REFRESH_TOKEN_TYPE, "refresh": True}, id="refresh"),
        ],
    )
    def test_a_non_access_token_is_closed_with_4401(
        self, claims: dict[str, Any]
    ) -> None:
        tokens = _tokens()
        token = tokens.encode({"sub": str(USER_ID), **claims})
        with (
            TestClient(_app(tokens)) as client,
            client.websocket_connect("/ws", subprotocols=["bearer", token]) as ws,
            pytest.raises(WebSocketDisconnect) as excinfo,
        ):
            ws.receive_json()
        assert excinfo.value.code == 4401
