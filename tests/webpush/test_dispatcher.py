"""Tests for tempest_fastapi_sdk.webpush.dispatcher."""

from __future__ import annotations

from typing import Any

import pytest

pywebpush = pytest.importorskip("pywebpush")

from tempest_fastapi_sdk.webpush.dispatcher import (  # noqa: E402
    WebPushDispatcher,
    WebPushError,
    WebPushGoneError,
)
from tempest_fastapi_sdk.webpush.schemas import (  # noqa: E402
    WebPushKeysSchema,
    WebPushPayloadSchema,
    WebPushSubscriptionSchema,
)


def _subscription(
    endpoint: str = "https://push.example.com/abc",
) -> WebPushSubscriptionSchema:
    return WebPushSubscriptionSchema(
        endpoint=endpoint,
        keys=WebPushKeysSchema(p256dh="pk", auth="a"),
    )


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = f"status {status_code}"


class _FakeException(pywebpush.WebPushException):
    def __init__(self, status_code: int | None) -> None:
        super().__init__("fake")
        if status_code is not None:
            self.response = _FakeResponse(status_code)  # type: ignore[assignment]
        else:
            self.response = None  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_send_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_webpush(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)

    dispatcher = WebPushDispatcher("priv", vapid_subject="mailto:ops@x.com")
    payload = WebPushPayloadSchema(title="t", body="b")
    await dispatcher.send(_subscription(), payload, ttl_seconds=42)

    assert len(calls) == 1
    assert calls[0]["ttl"] == 42
    assert calls[0]["vapid_claims"]["sub"] == "mailto:ops@x.com"
    assert '"title"' in calls[0]["data"]


@pytest.mark.asyncio
async def test_send_dict_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_webpush(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    dispatcher = WebPushDispatcher("priv", vapid_subject="mailto:ops@x.com")
    await dispatcher.send(_subscription(), {"hello": "world"})
    assert '"hello"' in captured["data"]


@pytest.mark.asyncio
async def test_send_raises_gone_for_404(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_webpush(**kwargs: Any) -> None:
        raise _FakeException(404)

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    dispatcher = WebPushDispatcher("priv", vapid_subject="mailto:ops@x.com")
    with pytest.raises(WebPushGoneError) as info:
        await dispatcher.send(_subscription(), "p")
    assert info.value.status_code == 404


@pytest.mark.asyncio
async def test_send_raises_gone_for_410(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_webpush(**kwargs: Any) -> None:
        raise _FakeException(410)

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    dispatcher = WebPushDispatcher("priv", vapid_subject="mailto:ops@x.com")
    with pytest.raises(WebPushGoneError):
        await dispatcher.send(_subscription(), "p")


@pytest.mark.asyncio
async def test_send_raises_generic_for_500(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_webpush(**kwargs: Any) -> None:
        raise _FakeException(500)

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    dispatcher = WebPushDispatcher("priv", vapid_subject="mailto:ops@x.com")
    with pytest.raises(WebPushError) as info:
        await dispatcher.send(_subscription(), "p")
    assert info.value.status_code == 500
    assert not isinstance(info.value, WebPushGoneError)


@pytest.mark.asyncio
async def test_send_many_collects_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_webpush(**kwargs: Any) -> None:
        endpoint = kwargs["subscription_info"]["endpoint"]
        if "gone" in endpoint:
            raise _FakeException(410)

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    dispatcher = WebPushDispatcher("priv", vapid_subject="mailto:ops@x.com")
    subs = [
        _subscription("https://push.example.com/ok-1"),
        _subscription("https://push.example.com/gone-1"),
        _subscription("https://push.example.com/ok-2"),
        _subscription("https://push.example.com/gone-2"),
    ]
    gone = await dispatcher.send_many(subs, "payload")
    assert set(gone) == {
        "https://push.example.com/gone-1",
        "https://push.example.com/gone-2",
    }
