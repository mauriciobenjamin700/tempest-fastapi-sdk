"""Tests for WebhookSender — signed outbound delivery with retries."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import Depends, FastAPI, Response

from tempest_fastapi_sdk import (
    WebhookSender,
    WebhookSignatureVerifier,
)

SECRET = "shared-secret"


def _receiver() -> tuple[FastAPI, dict[str, object]]:
    """A receiver app that verifies signatures + simulates flaky endpoints."""
    verifier = WebhookSignatureVerifier(SECRET, prefix="sha256=")
    state: dict[str, object] = {"received": [], "flaky_hits": 0, "broken_hits": 0}
    app = FastAPI()

    @app.post("/hook")
    async def hook(body: bytes = Depends(verifier.dependency())) -> dict[str, bool]:
        state["received"].append(json.loads(body))  # type: ignore[attr-defined]
        return {"ok": True}

    @app.post("/flaky")
    async def flaky() -> Response:
        state["flaky_hits"] = int(state["flaky_hits"]) + 1  # type: ignore[arg-type]
        if int(state["flaky_hits"]) < 3:  # type: ignore[arg-type]
            return Response(status_code=503)
        return Response(status_code=200)

    @app.post("/bad")
    async def bad() -> Response:
        return Response(status_code=400)

    @app.post("/broken")
    async def broken() -> Response:
        state["broken_hits"] = int(state["broken_hits"]) + 1  # type: ignore[arg-type]
        return Response(status_code=500)

    return app, state


@pytest.fixture
async def sender_and_state() -> AsyncIterator[tuple[WebhookSender, dict[str, object]]]:
    app, state = _receiver()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://recv") as client:
        verifier = WebhookSignatureVerifier(SECRET, prefix="sha256=")
        sender = WebhookSender(client, signer=verifier, backoff_base=0.01)
        yield sender, state


@pytest.mark.asyncio
async def test_delivers_and_signature_verifies(
    sender_and_state: tuple[WebhookSender, dict[str, object]],
) -> None:
    sender, state = sender_and_state
    result = await sender.send(
        "http://recv/hook", event="order.paid", payload={"id": "abc", "total": 42}
    )
    assert result.delivered is True
    assert result.status_code == 200
    assert result.attempts == 1
    # Receiver verified the signature (dependency) and got the payload.
    assert state["received"] == [{"id": "abc", "total": 42}]


@pytest.mark.asyncio
async def test_retries_transient_then_succeeds(
    sender_and_state: tuple[WebhookSender, dict[str, object]],
) -> None:
    sender, state = sender_and_state
    result = await sender.send("http://recv/flaky", event="e", payload={})
    assert result.delivered is True
    assert result.attempts == 3
    assert state["flaky_hits"] == 3


@pytest.mark.asyncio
async def test_4xx_not_retried(
    sender_and_state: tuple[WebhookSender, dict[str, object]],
) -> None:
    sender, _state = sender_and_state
    result = await sender.send("http://recv/bad", event="e", payload={})
    assert result.delivered is False
    assert result.status_code == 400
    assert result.attempts == 1  # client error → no retry
    assert result.error == "HTTP 400"


@pytest.mark.asyncio
async def test_5xx_exhausts_attempts(
    sender_and_state: tuple[WebhookSender, dict[str, object]],
) -> None:
    sender, state = sender_and_state
    result = await sender.send("http://recv/broken", event="e", payload={})
    assert result.delivered is False
    assert result.status_code == 500
    assert result.attempts == 3
    assert state["broken_hits"] == 3


@pytest.mark.asyncio
async def test_send_many(
    sender_and_state: tuple[WebhookSender, dict[str, object]],
) -> None:
    sender, state = sender_and_state
    results = await sender.send_many(
        [("http://recv/hook", {"n": 1}), ("http://recv/hook", {"n": 2})],
        event="batch",
    )
    assert [r.delivered for r in results] == [True, True]
    assert {item["n"] for item in state["received"]} == {1, 2}  # type: ignore[attr-defined]
