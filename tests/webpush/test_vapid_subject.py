"""A malformed VAPID subject fails where it is configured, not on send (#791).

The incident: ``VAPID_SUBJECT`` held a bare e-mail, settings loaded, and
every push failed with py_vapid's ``Missing 'sub' from claims``. The raw
``VapidException`` escaped ``send`` and ``send_many`` untyped: the service's
own notification path swallowed it, and its admin test route answered
``500``.
"""

from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

pywebpush = pytest.importorskip("pywebpush")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from py_vapid import Vapid  # noqa: E402

from tempest_fastapi_sdk.settings import PushSettings, WebPushSettings  # noqa: E402
from tempest_fastapi_sdk.webpush import dispatcher as dispatcher_module  # noqa: E402
from tempest_fastapi_sdk.webpush.dispatcher import (  # noqa: E402
    WebPushConfigurationError,
    WebPushDispatcher,
    WebPushError,
)
from tempest_fastapi_sdk.webpush.schemas import (  # noqa: E402
    WebPushKeysSchema,
    WebPushSubscriptionSchema,
)

BARE: str = "admin@example.com"
"""The value the incident had: an e-mail without ``mailto:``."""

MESSAGE: str = (
    "must be 'mailto:<email>' or 'https://<host>' "
    "(got a bare e-mail; prefix it with 'mailto:')"
)
"""The tail of the error both checks raise for :data:`BARE`."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _vapid_private_key() -> str:
    """Generate a real VAPID private key, as ``tempest secrets`` would."""
    vapid = Vapid()
    vapid.generate_keys()
    raw = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    return _b64(raw)


def _subscription() -> WebPushSubscriptionSchema:
    """Build a subscription with real browser keys, so encryption succeeds."""
    public = ec.generate_private_key(ec.SECP256R1()).public_key()
    p256dh = public.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return WebPushSubscriptionSchema(
        endpoint="https://push.example.com/abc",
        keys=WebPushKeysSchema(p256dh=_b64(p256dh), auth=_b64(b"0123456789abcdef")),
    )


@pytest.fixture(autouse=True)
def _public_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve the push host to a public address without touching DNS."""

    async def resolver(host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(dispatcher_module, "system_resolver", resolver)


class TestSettings:
    @pytest.mark.parametrize("settings_class", [WebPushSettings, PushSettings])
    def test_bare_email_with_a_key_fails_to_load(
        self, settings_class: type[WebPushSettings]
    ) -> None:
        with pytest.raises(ValidationError, match="VAPID_SUBJECT " + MESSAGE[:20]):
            settings_class(  # type: ignore[call-arg]
                _env_file=None, VAPID_PRIVATE_KEY="k", VAPID_SUBJECT=BARE
            )

    def test_empty_subject_with_a_key_fails_to_load(self) -> None:
        with pytest.raises(ValidationError, match="got an empty value"):
            WebPushSettings(  # type: ignore[call-arg]
                _env_file=None, VAPID_PRIVATE_KEY="k", VAPID_SUBJECT=""
            )

    def test_without_a_key_nothing_is_checked(self) -> None:
        settings = WebPushSettings(_env_file=None, VAPID_SUBJECT=BARE)  # type: ignore[call-arg]
        assert settings.webpush_enabled is False

    @pytest.mark.parametrize(
        "subject", ["mailto:admin@example.com", "https://example.com"]
    )
    def test_valid_subject_loads(self, subject: str) -> None:
        settings = WebPushSettings(  # type: ignore[call-arg]
            _env_file=None, VAPID_PRIVATE_KEY="k", VAPID_SUBJECT=subject
        )
        assert settings.webpush_kwargs()["vapid_subject"] == subject

    def test_environment_variable_is_checked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VAPID_PRIVATE_KEY", "k")
        monkeypatch.setenv("VAPID_SUBJECT", BARE)
        with pytest.raises(ValidationError, match="bare e-mail"):
            WebPushSettings(_env_file=None)  # type: ignore[call-arg]


class TestDispatcherConstruction:
    def test_bare_email_is_refused(self) -> None:
        with pytest.raises(ValueError, match="vapid_subject " + MESSAGE[:20]):
            WebPushDispatcher("k", vapid_subject=BARE)

    def test_sub_in_extra_claims_is_checked(self) -> None:
        with pytest.raises(ValueError, match="bare e-mail"):
            WebPushDispatcher(
                "k",
                vapid_subject="mailto:admin@example.com",
                extra_vapid_claims={"sub": BARE},
            )

    def test_valid_subject_is_kept(self) -> None:
        dispatcher = WebPushDispatcher("k", vapid_subject="https://example.com")
        assert dispatcher.vapid_claims == {"sub": "https://example.com"}


class TestSend:
    def _broken(self) -> WebPushDispatcher:
        """A dispatcher whose claim was changed after the construction check."""
        dispatcher = WebPushDispatcher(
            _vapid_private_key(), vapid_subject="mailto:admin@example.com"
        )
        dispatcher.vapid_claims["sub"] = BARE
        return dispatcher

    async def test_vapid_refusal_is_a_configuration_error(self) -> None:
        with pytest.raises(WebPushConfigurationError) as caught:
            await self._broken().send(_subscription(), {"title": "oi"})
        assert isinstance(caught.value, WebPushError)
        assert "Missing 'sub' from claims" in str(caught.value)
        assert type(caught.value.__cause__).__name__ == "VapidException"

    async def test_send_many_raises_it_instead_of_logging(self) -> None:
        with pytest.raises(WebPushConfigurationError):
            await self._broken().send_many(
                [_subscription(), _subscription()], {"title": "oi"}
            )

    async def test_valid_claims_reach_the_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With a valid claim, signing passes and the request is attempted."""
        sent: list[dict[str, str]] = []

        def fake_send(
            self: object, data: object, headers: dict[str, str], **kwargs: object
        ) -> object:
            sent.append(dict(headers))

            class _Response:
                status_code = 201
                text = ""

            return _Response()

        monkeypatch.setattr(pywebpush.WebPusher, "send", fake_send)
        dispatcher = WebPushDispatcher(
            _vapid_private_key(), vapid_subject="mailto:admin@example.com"
        )
        await dispatcher.send(_subscription(), {"title": "oi"})
        assert sent
        assert sent[0]["Authorization"].startswith("vapid t=")
