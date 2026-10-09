"""``make_rate_limit_dependency`` — the per-route rate limit (#452).

``RateLimitMiddleware`` limits the whole app and its synchronous
``key_func`` never sees the body. These tests pin what the dependency
adds on top: one route limited and its neighbour untouched, a key read
from the JSON body without starving the endpoint of its payload, several
keys per request, and a ``429`` byte-for-byte equal (body and rate-limit
headers) to the middleware's.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.requests import Request

from tempest_fastapi_sdk import (
    MemoryRateLimitStore,
    RateLimitMiddleware,
    RateLimitStore,
    RedisRateLimitStore,
    key_by_body_field,
    key_by_ip,
    make_rate_limit_dependency,
    register_exception_handlers,
)

RATE_HEADERS: tuple[str, ...] = (
    "ratelimit-limit",
    "ratelimit-remaining",
    "ratelimit-reset",
    "retry-after",
)


class InviteSchema(BaseModel):
    """Body of the invite route under test."""

    email: str


def _rate_headers(headers: Any) -> dict[str, str]:
    """Keep only the rate-limit headers of a response.

    Args:
        headers (Any): The response headers.

    Returns:
        dict[str, str]: The rate-limit headers present, lowercased.
    """
    return {name: headers[name] for name in RATE_HEADERS if name in headers}


def _invite_app(store: RateLimitStore, **dependency_kwargs: Any) -> FastAPI:
    """Build an app whose ``POST /invites`` carries the dependency.

    Args:
        store (RateLimitStore): The counter backend.
        **dependency_kwargs (Any): Forwarded to the factory.

    Returns:
        FastAPI: The app, with ``POST /other`` left unlimited.
    """
    limit = make_rate_limit_dependency(store, **dependency_kwargs)
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/invites", dependencies=[Depends(limit)])
    async def invite(payload: InviteSchema) -> dict[str, str]:
        return {"email": payload.email}

    @app.post("/other")
    async def other() -> dict[str, str]:
        return {"ok": "yes"}

    return app


class TestOneRoute:
    """The limit belongs to the route it is attached to."""

    def test_refuses_the_request_past_the_budget(self) -> None:
        app = _invite_app(MemoryRateLimitStore(), max_requests=2, window_seconds=60)
        client = TestClient(app)
        codes = [
            client.post("/invites", json={"email": "a@x.com"}).status_code
            for _ in range(3)
        ]
        assert codes == [200, 200, 429]

    def test_other_route_is_not_affected(self) -> None:
        app = _invite_app(MemoryRateLimitStore(), max_requests=1, window_seconds=60)
        client = TestClient(app)
        client.post("/invites", json={"email": "a@x.com"})
        assert client.post("/invites", json={"email": "a@x.com"}).status_code == 429
        assert client.post("/other").status_code == 200

    def test_accepted_response_advertises_the_limit(self) -> None:
        app = _invite_app(MemoryRateLimitStore(), max_requests=3, window_seconds=60)
        response = TestClient(app).post("/invites", json={"email": "a@x.com"})
        assert response.status_code == 200
        assert _rate_headers(response.headers) == {
            "ratelimit-limit": "3",
            "ratelimit-remaining": "2",
        }

    def test_two_routes_get_their_own_budget_by_default(self) -> None:
        limit = make_rate_limit_dependency(
            MemoryRateLimitStore(), max_requests=1, window_seconds=60
        )
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/a", dependencies=[Depends(limit)])
        async def route_a() -> dict[str, str]:
            return {}

        @app.get("/b", dependencies=[Depends(limit)])
        async def route_b() -> dict[str, str]:
            return {}

        client = TestClient(app)
        assert client.get("/a").status_code == 200
        assert client.get("/b").status_code == 200
        assert client.get("/a").status_code == 429

    def test_shared_scope_shares_the_budget(self) -> None:
        limit = make_rate_limit_dependency(
            MemoryRateLimitStore(),
            max_requests=1,
            window_seconds=60,
            scope="invites",
        )
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/a", dependencies=[Depends(limit)])
        async def route_a() -> dict[str, str]:
            return {}

        @app.get("/b", dependencies=[Depends(limit)])
        async def route_b() -> dict[str, str]:
            return {}

        client = TestClient(app)
        assert client.get("/a").status_code == 200
        assert client.get("/b").status_code == 429

    def test_path_template_groups_every_resource(self) -> None:
        limit = make_rate_limit_dependency(
            MemoryRateLimitStore(), max_requests=1, window_seconds=60
        )
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/users/{user_id}", dependencies=[Depends(limit)])
        async def user(user_id: int) -> dict[str, int]:
            return {"id": user_id}

        client = TestClient(app)
        assert client.get("/users/1").status_code == 200
        assert client.get("/users/2").status_code == 429


class TestBodyKey:
    """A key read off the JSON body, alone or next to the IP."""

    def test_endpoint_still_receives_the_body(self) -> None:
        app = _invite_app(
            MemoryRateLimitStore(),
            max_requests=5,
            window_seconds=60,
            key=key_by_body_field("email"),
        )
        app.add_middleware(RateLimitMiddleware, max_requests=100)
        response = TestClient(app).post("/invites", json={"email": "ana@x.com"})
        assert response.status_code == 200
        assert response.json() == {"email": "ana@x.com"}

    def test_email_blocks_regardless_of_ip(self) -> None:
        app = _invite_app(
            MemoryRateLimitStore(),
            max_requests=2,
            window_seconds=60,
            key=[key_by_ip(trusted_header="x-real-ip"), key_by_body_field("email")],
        )
        client = TestClient(app)
        codes = [
            client.post(
                "/invites",
                json={"email": "ana@x.com"},
                headers={"x-real-ip": f"10.0.0.{n}"},
            ).status_code
            for n in range(3)
        ]
        assert codes == [200, 200, 429]

    def test_ip_blocks_regardless_of_email(self) -> None:
        app = _invite_app(
            MemoryRateLimitStore(),
            max_requests=2,
            window_seconds=60,
            key=[key_by_ip(trusted_header="x-real-ip"), key_by_body_field("email")],
        )
        client = TestClient(app)
        codes = [
            client.post(
                "/invites",
                json={"email": f"user{n}@x.com"},
                headers={"x-real-ip": "10.0.0.1"},
            ).status_code
            for n in range(3)
        ]
        assert codes == [200, 200, 429]

    def test_normalization_folds_case_and_whitespace(self) -> None:
        app = _invite_app(
            MemoryRateLimitStore(),
            max_requests=1,
            window_seconds=60,
            key=key_by_body_field("email"),
        )
        client = TestClient(app)
        assert client.post("/invites", json={"email": "Ana@X.com"}).status_code == 200
        assert client.post("/invites", json={"email": " ana@x.com "}).status_code == 429

    def test_invalid_body_is_still_a_422(self) -> None:
        app = _invite_app(
            MemoryRateLimitStore(),
            max_requests=1,
            window_seconds=60,
            key=key_by_body_field("email"),
        )
        client = TestClient(app)
        for _ in range(3):
            assert client.post("/invites", content=b"not json").status_code == 422


class TestKeyByBodyField:
    """The key function itself."""

    @staticmethod
    async def _keys(body: bytes, **kwargs: Any) -> list[str]:
        """Run ``key_by_body_field("email")`` over a raw body.

        Args:
            body (bytes): The request body.
            **kwargs (Any): Forwarded to ``key_by_body_field``.

        Returns:
            list[str]: The keys produced.
        """

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request({"type": "http", "method": "POST", "headers": []}, receive)
        return await key_by_body_field("email", **kwargs)(request)

    async def test_hashes_by_default(self) -> None:
        digest = hashlib.sha256(b"ana@x.com").hexdigest()
        assert await self._keys(b'{"email": "Ana@x.com"}') == [f"email:{digest}"]

    async def test_clear_text_when_asked(self) -> None:
        keys = await self._keys(b'{"email": "ana@x.com"}', hash_value=False)
        assert keys == ["email:ana@x.com"]

    @pytest.mark.parametrize(
        "body",
        [b"not json", b"[1, 2]", b'{"other": 1}', b'{"email": null}', b'{"email": ""}'],
    )
    async def test_unusable_body_yields_no_key(self, body: bytes) -> None:
        assert await self._keys(body) == []


class TestSameEnvelopeAsTheMiddleware:
    """The client cannot tell which of the two refused it."""

    def test_body_and_headers_match(self) -> None:
        middleware_app = FastAPI()
        middleware_app.add_middleware(
            RateLimitMiddleware, max_requests=1, window_seconds=60
        )

        @middleware_app.get("/x")
        async def x() -> dict[str, str]:
            return {}

        dependency_app = FastAPI()
        register_exception_handlers(dependency_app)
        limit = make_rate_limit_dependency(
            MemoryRateLimitStore(), max_requests=1, window_seconds=60
        )

        @dependency_app.get("/x", dependencies=[Depends(limit)])
        async def y() -> dict[str, str]:
            return {}

        refusals = []
        for app in (middleware_app, dependency_app):
            client = TestClient(app)
            client.get("/x")
            refusals.append(client.get("/x"))
        by_middleware, by_dependency = refusals
        assert by_middleware.status_code == by_dependency.status_code == 429
        assert by_middleware.json() == by_dependency.json()
        assert by_dependency.json() == {
            "detail": "Too many requests",
            "code": "TOO_MANY_REQUESTS",
            "details": {"retry_after_seconds": 60, "limit": 1},
        }
        assert _rate_headers(by_middleware.headers) == _rate_headers(
            by_dependency.headers
        )
        assert set(_rate_headers(by_dependency.headers)) == set(RATE_HEADERS)

    def test_custom_message_and_code(self) -> None:
        app = _invite_app(
            MemoryRateLimitStore(),
            max_requests=1,
            window_seconds=60,
            error_message="slow down",
            error_code="INVITE_RATE_LIMITED",
        )
        client = TestClient(app)
        client.post("/invites", json={"email": "a@x.com"})
        body = client.post("/invites", json={"email": "a@x.com"}).json()
        assert body["detail"] == "slow down"
        assert body["code"] == "INVITE_RATE_LIMITED"


class TestRedisStore:
    """The dependency counts in the shared Redis store too."""

    def test_refuses_past_the_budget(self) -> None:
        fakeredis = pytest.importorskip("fakeredis")
        store = RedisRateLimitStore(
            fakeredis.aioredis.FakeRedis(),
            fail_open=False,
        )
        app = _invite_app(
            store,
            max_requests=2,
            window_seconds=60,
            key=key_by_body_field("email"),
        )
        client = TestClient(app)
        codes = [
            client.post("/invites", json={"email": "a@x.com"}).status_code
            for _ in range(3)
        ]
        assert codes == [200, 200, 429]


class TestConstruction:
    """Misconfiguration fails at build time."""

    def test_key_and_trusted_header_together_is_refused(self) -> None:
        with pytest.raises(ValueError, match="trusted_ip_header"):
            make_rate_limit_dependency(
                MemoryRateLimitStore(),
                max_requests=1,
                window_seconds=60,
                key=key_by_ip(),
                trusted_ip_header="x-real-ip",
            )

    @pytest.mark.parametrize(("max_requests", "window_seconds"), [(0, 60.0), (1, 0.0)])
    def test_bounds(self, max_requests: int, window_seconds: float) -> None:
        with pytest.raises(ValueError):
            make_rate_limit_dependency(
                MemoryRateLimitStore(),
                max_requests=max_requests,
                window_seconds=window_seconds,
            )

    def test_trusted_header_shapes_the_default_key(self) -> None:
        app = _invite_app(
            MemoryRateLimitStore(),
            max_requests=1,
            window_seconds=60,
            trusted_ip_header="x-real-ip",
        )
        client = TestClient(app)
        first = client.post(
            "/invites", json={"email": "a@x.com"}, headers={"x-real-ip": "1.1.1.1"}
        )
        second = client.post(
            "/invites", json={"email": "a@x.com"}, headers={"x-real-ip": "2.2.2.2"}
        )
        assert (first.status_code, second.status_code) == (200, 200)
