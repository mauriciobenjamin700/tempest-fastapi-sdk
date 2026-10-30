"""Security and idempotency rules of the RevenueCat webhook, one class each.

* authentication refused: missing header, wrong header, empty secret;
* HMAC: wrong secret, stale timestamp, missing header;
* replay: the same ``event.id`` twice is one inbox row;
* minimal collection: ``subscriber_attributes`` never reaches the inbox.

Requests go through a real FastAPI app with the SDK's exception handlers, so
the status code asserted is the one RevenueCat would see.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, Request
from sqlalchemy import func, select

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.db import (
    AsyncDatabaseManager,
    BaseWebhookEventModel,
    WebhookInbox,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat import (
    REVENUECAT_PROVIDER,
    REVENUECAT_SIGNATURE_HEADER,
    RevenueCatEventTypeEnum,
    RevenueCatWebhookDelivery,
    make_revenuecat_webhook_dependency,
    make_revenuecat_webhook_router,
    sign_payload,
    verify_authorization,
    verify_signature,
)

SECRET = "whk-secret-value"
SIGNING = "hmac-signing-secret"


class _RevenueCatInbox(BaseWebhookEventModel):
    """Inbox table for these tests."""

    __tablename__ = "test_revenuecat_inbox"


def _body(event_id: str = "evt-1", **event: Any) -> bytes:
    """A minimal webhook body in the documented shape."""
    payload: dict[str, Any] = {"id": event_id, "type": "RENEWAL", **event}
    return json.dumps({"api_version": "1.0", "event": payload}).encode()


def _dependency_app(**kwargs: Any) -> FastAPI:
    """An app whose one route returns what the dependency produced."""
    app = FastAPI()
    register_exception_handlers(app)
    verify = make_revenuecat_webhook_dependency(**kwargs)

    @app.post("/hook")
    async def hook(
        delivery: RevenueCatWebhookDelivery = Depends(verify),
    ) -> dict[str, Any]:
        return {
            "id": delivery.webhook.event.id,
            "known": delivery.event_type is not None,
        }

    return app


async def _post(
    app: FastAPI,
    body: bytes,
    headers: dict[str, str] | None = None,
    *,
    path: str = "/hook",
) -> httpx.Response:
    """POST ``body`` to the app in-process."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post(path, content=body, headers=headers or {})


ROUTE = "/webhooks/revenuecat"


class TestAuthorizationHeader:
    """The dashboard value is required, compared in constant time."""

    async def test_a_missing_header_is_401(self) -> None:
        response = await _post(_dependency_app(secret=SECRET), _body())

        assert response.status_code == 401

    async def test_a_wrong_header_is_401(self) -> None:
        response = await _post(
            _dependency_app(secret=SECRET), _body(), {"Authorization": "nope"}
        )

        assert response.status_code == 401

    async def test_the_right_header_passes(self) -> None:
        response = await _post(
            _dependency_app(secret=SECRET), _body(), {"Authorization": SECRET}
        )

        assert response.status_code == 200
        assert response.json() == {"id": "evt-1", "known": True}

    async def test_a_bearer_prefix_is_accepted(self) -> None:
        response = await _post(
            _dependency_app(secret=SECRET),
            _body(),
            {"Authorization": f"Bearer {SECRET}"},
        )

        assert response.status_code == 200

    @pytest.mark.parametrize("header", ["", "Bearer ", "Bearer", " "])
    async def test_an_empty_secret_refuses_everything(self, header: str) -> None:
        response = await _post(
            _dependency_app(secret=""), _body(), {"Authorization": header}
        )

        assert response.status_code == 401

    async def test_the_secret_can_be_read_per_request(self) -> None:
        def per_tenant(request: Request) -> str:
            return SECRET if request.headers.get("X-Tenant") == "a" else ""

        app = _dependency_app(secret=per_tenant)

        ok = await _post(app, _body(), {"Authorization": SECRET, "X-Tenant": "a"})
        refused = await _post(app, _body(), {"Authorization": SECRET, "X-Tenant": "b"})

        assert ok.status_code == 200
        assert refused.status_code == 401

    def test_verify_authorization_refuses_an_empty_secret(self) -> None:
        assert verify_authorization("", "") is False
        assert verify_authorization("Bearer ", "Bearer ") is False

    def test_no_check_at_all_is_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match="accept any request"):
            make_revenuecat_webhook_dependency(None)


class TestSignature:
    """``X-RevenueCat-Webhook-Signature``, as the webhooks page describes it."""

    async def test_a_correct_signature_passes(self) -> None:
        body = _body()
        header = sign_payload(body, SIGNING, timestamp=int(time.time()))

        response = await _post(
            _dependency_app(secret=None, signing_secret=SIGNING),
            body,
            {REVENUECAT_SIGNATURE_HEADER: header},
        )

        assert response.status_code == 200

    async def test_a_missing_signature_is_401(self) -> None:
        response = await _post(
            _dependency_app(secret=None, signing_secret=SIGNING), _body()
        )

        assert response.status_code == 401

    async def test_a_signature_from_another_secret_is_401(self) -> None:
        body = _body()
        header = sign_payload(body, "other", timestamp=int(time.time()))

        response = await _post(
            _dependency_app(secret=None, signing_secret=SIGNING),
            body,
            {REVENUECAT_SIGNATURE_HEADER: header},
        )

        assert response.status_code == 401

    async def test_a_signature_over_another_body_is_401(self) -> None:
        header = sign_payload(_body("evt-a"), SIGNING, timestamp=int(time.time()))

        response = await _post(
            _dependency_app(secret=None, signing_secret=SIGNING),
            _body("evt-b"),
            {REVENUECAT_SIGNATURE_HEADER: header},
        )

        assert response.status_code == 401

    async def test_both_checks_are_required_when_both_are_set(self) -> None:
        body = _body()
        header = sign_payload(body, SIGNING, timestamp=int(time.time()))
        app = _dependency_app(secret=SECRET, signing_secret=SIGNING)

        only_hmac = await _post(app, body, {REVENUECAT_SIGNATURE_HEADER: header})
        both = await _post(
            app, body, {REVENUECAT_SIGNATURE_HEADER: header, "Authorization": SECRET}
        )

        assert only_hmac.status_code == 401
        assert both.status_code == 200

    def test_a_stale_timestamp_is_refused(self) -> None:
        body = _body()
        header = sign_payload(body, SIGNING, timestamp=1_000)

        assert verify_signature(body, header, SIGNING, now=1_000 + 301) is False
        assert verify_signature(body, header, SIGNING, now=1_000 + 299) is True

    def test_an_empty_signing_secret_refuses_everything(self) -> None:
        body = _body()
        digest = hmac.new(b"", b"1000." + body, hashlib.sha256).hexdigest()
        header = f"t=1000,v1={digest}"

        assert verify_signature(body, header, "", now=1_000) is False


class TestEmptySecretMeansUnconfigured:
    """An empty secret skips its check; no secret with a value refuses all.

    The recipe passes ``secret=settings.REVENUECAT_WEBHOOK_SECRET``, which is
    ``""`` (not ``None``) in a service that authenticates by HMAC alone, and
    ``RevenueCatSettings.production_violations`` accepts that configuration.
    Before this rule the empty ``secret`` still ran the ``Authorization``
    check and refused every correctly signed delivery.
    """

    async def test_an_empty_authorization_secret_leaves_hmac_only(self) -> None:
        body = _body()
        header = sign_payload(body, SIGNING, timestamp=int(time.time()))

        response = await _post(
            _dependency_app(secret="", signing_secret=SIGNING),
            body,
            {REVENUECAT_SIGNATURE_HEADER: header},
        )

        assert response.status_code == 200

    async def test_hmac_only_still_refuses_a_bad_signature(self) -> None:
        body = _body()
        header = sign_payload(body, "other", timestamp=int(time.time()))

        response = await _post(
            _dependency_app(secret="", signing_secret=SIGNING),
            body,
            {REVENUECAT_SIGNATURE_HEADER: header, "Authorization": "anything"},
        )

        assert response.status_code == 401

    async def test_an_empty_signing_secret_leaves_authorization_only(self) -> None:
        app = _dependency_app(secret="abc", signing_secret="")

        ok = await _post(app, _body(), {"Authorization": "abc"})
        refused = await _post(app, _body(), {"Authorization": "nope"})

        assert ok.status_code == 200
        assert refused.status_code == 401

    @pytest.mark.parametrize("empty", ["", " ", "Bearer "])
    async def test_both_empty_refuses_even_a_signed_delivery(self, empty: str) -> None:
        body = _body()
        now = int(time.time())
        digest = hmac.new(
            empty.encode(), f"{now}.".encode() + body, hashlib.sha256
        ).hexdigest()
        header = f"t={now},v1={digest}"

        response = await _post(
            _dependency_app(secret=empty, signing_secret=empty),
            body,
            {REVENUECAT_SIGNATURE_HEADER: header, "Authorization": empty},
        )

        assert response.status_code == 401

    async def test_both_configured_requires_both(self) -> None:
        body = _body()
        header = sign_payload(body, SIGNING, timestamp=int(time.time()))
        app = _dependency_app(secret=SECRET, signing_secret=SIGNING)

        only_hmac = await _post(app, body, {REVENUECAT_SIGNATURE_HEADER: header})
        only_authorization = await _post(app, body, {"Authorization": SECRET})
        both = await _post(
            app, body, {REVENUECAT_SIGNATURE_HEADER: header, "Authorization": SECRET}
        )

        assert only_hmac.status_code == 401
        assert only_authorization.status_code == 401
        assert both.status_code == 200

    async def test_emptiness_is_read_per_request(self) -> None:
        def per_tenant(request: Request) -> str:
            return "" if request.headers.get("X-Tenant") == "hmac" else SECRET

        body = _body()
        header = sign_payload(body, SIGNING, timestamp=int(time.time()))
        app = _dependency_app(secret=per_tenant, signing_secret=SIGNING)

        hmac_tenant = await _post(
            app, body, {REVENUECAT_SIGNATURE_HEADER: header, "X-Tenant": "hmac"}
        )
        both_tenant = await _post(
            app, body, {REVENUECAT_SIGNATURE_HEADER: header, "X-Tenant": "both"}
        )

        assert hmac_tenant.status_code == 200
        assert both_tenant.status_code == 401

    async def test_the_router_built_like_the_recipe_accepts_hmac_only(
        self, inbox: WebhookInbox[_RevenueCatInbox]
    ) -> None:
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(
            make_revenuecat_webhook_router(
                inbox=inbox, secret="", signing_secret=SIGNING
            )
        )
        body = _body("evt-hmac-only")
        header = sign_payload(body, SIGNING, timestamp=int(time.time()))

        response = await _post(
            app, body, {REVENUECAT_SIGNATURE_HEADER: header}, path=ROUTE
        )

        assert response.status_code == 200
        assert response.json() == {"recorded": True, "event_id": "evt-hmac-only"}

    def test_two_none_still_fails_at_construction(self) -> None:
        with pytest.raises(ValueError, match="accept any request"):
            make_revenuecat_webhook_dependency(None, signing_secret=None)


class TestBody:
    """An authenticated body that is not an event."""

    async def test_a_body_without_event_id_is_422(self) -> None:
        body = json.dumps({"api_version": "1.0", "event": {"type": "X"}}).encode()

        response = await _post(
            _dependency_app(secret=SECRET), body, {"Authorization": SECRET}
        )

        assert response.status_code == 422

    async def test_a_non_json_body_is_422(self) -> None:
        response = await _post(
            _dependency_app(secret=SECRET), b"not json", {"Authorization": SECRET}
        )

        assert response.status_code == 422

    async def test_an_unauthenticated_bad_body_is_401_not_422(self) -> None:
        response = await _post(_dependency_app(secret=SECRET), b"not json")

        assert response.status_code == 401

    async def test_an_unknown_type_is_accepted(self) -> None:
        response = await _post(
            _dependency_app(secret=SECRET),
            _body(type="SOMETHING_NEW"),
            {"Authorization": SECRET},
        )

        assert response.status_code == 200
        assert response.json() == {"id": "evt-1", "known": False}


@pytest_asyncio.fixture
async def inbox(tmp_path: Path) -> AsyncGenerator[WebhookInbox[_RevenueCatInbox]]:
    """A file-backed inbox."""
    db = AsyncDatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'rc.db'}")
    await db.connect()
    await db.create_tables()
    try:
        yield WebhookInbox(db, model=_RevenueCatInbox)
    finally:
        await db.drop_tables()
        await db.disconnect()


def _router_app(
    inbox: WebhookInbox[_RevenueCatInbox], **kwargs: Any
) -> tuple[FastAPI, list[UUID]]:
    """An app mounting the router; returns it and the ``on_recorded`` log."""
    recorded: list[UUID] = []

    async def on_recorded(event_id: UUID) -> None:
        recorded.append(event_id)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_revenuecat_webhook_router(
            inbox=inbox,
            secret=SECRET,
            on_recorded=kwargs.pop("on_recorded", on_recorded),
            **kwargs,
        )
    )
    return app, recorded


async def _rows(inbox: WebhookInbox[_RevenueCatInbox]) -> list[_RevenueCatInbox]:
    """Every inbox row."""
    async with inbox._db.get_session_context() as session:
        return list((await session.execute(select(_RevenueCatInbox))).scalars())


class TestRouter:
    """Record once, answer 200, never store the subscriber's attributes."""

    async def test_a_delivery_is_recorded_and_acknowledged(
        self, inbox: WebhookInbox[_RevenueCatInbox]
    ) -> None:
        app, recorded = _router_app(inbox)

        response = await _post(app, _body(), {"Authorization": SECRET}, path=ROUTE)

        assert response.status_code == 200
        assert response.json() == {"recorded": True, "event_id": "evt-1"}
        rows = await _rows(inbox)
        assert [(r.provider, r.external_id, r.event_type) for r in rows] == [
            (REVENUECAT_PROVIDER, "evt-1", "RENEWAL")
        ]
        assert recorded == [rows[0].id]

    async def test_a_replayed_event_is_recorded_once(
        self, inbox: WebhookInbox[_RevenueCatInbox]
    ) -> None:
        app, recorded = _router_app(inbox)

        first = await _post(app, _body(), {"Authorization": SECRET}, path=ROUTE)
        second = await _post(app, _body(), {"Authorization": SECRET}, path=ROUTE)

        assert first.json()["recorded"] is True
        assert second.status_code == 200
        assert second.json()["recorded"] is False
        assert len(await _rows(inbox)) == 1
        assert len(recorded) == 1

    async def test_an_unauthenticated_delivery_records_nothing(
        self, inbox: WebhookInbox[_RevenueCatInbox]
    ) -> None:
        app, _ = _router_app(inbox)

        response = await _post(app, _body(), {"Authorization": "wrong"}, path=ROUTE)

        assert response.status_code == 401
        assert await _rows(inbox) == []

    async def test_subscriber_attributes_are_not_persisted(
        self, inbox: WebhookInbox[_RevenueCatInbox]
    ) -> None:
        app, _ = _router_app(inbox)
        body = _body(
            subscriber_attributes={
                "$email": {"updated_at_ms": 1, "value": "person@example.com"}
            },
            app_user_id="user-1",
            some_future_field="kept",
        )

        await _post(app, body, {"Authorization": SECRET}, path=ROUTE)

        (row,) = await _rows(inbox)
        assert "subscriber_attributes" not in row.payload["event"]
        assert "person@example.com" not in json.dumps(row.payload)
        assert row.payload["event"]["app_user_id"] == "user-1"
        assert row.payload["event"]["some_future_field"] == "kept"

    async def test_a_failing_on_recorded_still_answers_200(
        self, inbox: WebhookInbox[_RevenueCatInbox]
    ) -> None:
        async def broken(event_id: UUID) -> None:
            raise RuntimeError("broker down")

        app, _ = _router_app(inbox, on_recorded=broken)

        response = await _post(app, _body(), {"Authorization": SECRET}, path=ROUTE)

        assert response.status_code == 200
        assert len(await _rows(inbox)) == 1

    async def test_the_documented_samples_are_recorded(
        self, inbox: WebhookInbox[_RevenueCatInbox], samples: list[dict[str, Any]]
    ) -> None:
        app, _ = _router_app(inbox)

        for body in samples:
            response = await _post(
                app, json.dumps(body).encode(), {"Authorization": SECRET}, path=ROUTE
            )
            assert response.status_code == 200

        rows = await _rows(inbox)
        assert len(rows) == len({body["event"]["id"] for body in samples})
        assert all(RevenueCatEventTypeEnum.has_value(row.event_type) for row in rows)
        async with inbox._db.get_session_context() as session:
            count = (
                await session.execute(
                    select(func.count()).select_from(_RevenueCatInbox)
                )
            ).scalar_one()
        assert count == len(rows)
