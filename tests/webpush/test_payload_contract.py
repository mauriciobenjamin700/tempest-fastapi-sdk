"""Contract between ``WebPushPayloadSchema`` and tempest-react-sdk's service worker."""

import json
import logging

import pytest

from tempest_fastapi_sdk import WebPushActionSchema, WebPushPayloadSchema

SCHEMAS_LOGGER: str = "tempest_fastapi_sdk.webpush.schemas"
"""Logger the click-destination conflict warning is written to."""

REACT_PUSH_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        "title",
        "body",
        "icon",
        "badge",
        "image",
        "tag",
        "url",
        "actions",
        "requireInteraction",
        "vibrate",
        "silent",
        "renotify",
        "timestamp",
        "dir",
        "lang",
        "data",
    }
)
"""Keys of the push payload the browser service worker reads.

Mirrors tempest-react-sdk's ``PushPayload`` interface
(``src/sw/create-push-handler.ts``, v0.86.0). A key added on either side
without the other fails :class:`TestWireContract` instead of being silently
ignored by the service worker.
"""

REACT_PUSH_ACTION_KEYS: frozenset[str] = frozenset({"action", "title", "icon", "url"})
"""Keys of one notification button.

Mirrors tempest-react-sdk's ``PushNotificationAction`` interface
(``src/sw/create-push-handler.ts``, v0.86.0).
"""


def _full_payload() -> WebPushPayloadSchema:
    """Build a payload with every field set.

    Returns:
        WebPushPayloadSchema: A payload whose JSON carries every wire key.
    """
    return WebPushPayloadSchema(
        title="Pedido enviado",
        body="Seu pedido saiu para entrega.",
        icon="https://example.com/icon.png",
        badge="https://example.com/badge.png",
        image="https://example.com/order.png",
        tag="order:42",
        url="/orders/42",
        actions=[
            WebPushActionSchema(
                action="track",
                title="Rastrear",
                icon="https://example.com/track.png",
                url="/orders/42/track",
            )
        ],
        require_interaction=True,
        vibrate=[100, 50, 100],
        silent=False,
        renotify=True,
        timestamp=1_800_000_000_000,
        dir="ltr",
        lang="pt-BR",
        data={"order_id": "42"},
    )


class TestWireContract:
    """The JSON keys match what the React service worker reads."""

    def test_payload_keys_match_react_push_payload(self) -> None:
        """Every field serializes under the key ``PushPayload`` declares."""
        wire = json.loads(_full_payload().to_json())
        assert set(wire) == REACT_PUSH_PAYLOAD_KEYS

    def test_action_keys_match_react_push_notification_action(self) -> None:
        """An action button serializes under ``PushNotificationAction`` keys."""
        wire = json.loads(_full_payload().to_json())
        assert set(wire["actions"][0]) == REACT_PUSH_ACTION_KEYS

    def test_camel_case_input_round_trips(self) -> None:
        """A payload parsed back from its own JSON is equal to the original."""
        payload = _full_payload()
        assert WebPushPayloadSchema.model_validate_json(payload.to_json()) == payload

    def test_unset_fields_are_absent_not_null(self) -> None:
        """``None`` fields are dropped, so the browser keeps its defaults.

        The React handler forwards every option that is not ``undefined``;
        a ``null`` would reach ``showNotification`` as a value.
        """
        wire = json.loads(WebPushPayloadSchema(title="Oi").to_json())
        assert wire == {"title": "Oi"}

    def test_action_without_icon_or_url_omits_them(self) -> None:
        """Nested ``None`` fields are dropped too."""
        payload = WebPushPayloadSchema(
            actions=[WebPushActionSchema(action="open", title="Abrir")]
        )
        assert json.loads(payload.to_json()) == {
            "actions": [{"action": "open", "title": "Abrir"}]
        }


class TestClickDestination:
    """The click destination is the top-level ``url``."""

    def test_legacy_data_url_is_lifted(self) -> None:
        """``data={"url": ...}`` alone fills the top-level ``url``.

        Earlier releases documented the destination inside ``data``, which
        the React service worker overwrites, so every click opened ``/``.
        """
        payload = WebPushPayloadSchema(data={"url": "/inbox", "id": "1"})
        assert payload.url == "/inbox"
        assert payload.data == {"url": "/inbox", "id": "1"}
        assert json.loads(payload.to_json())["url"] == "/inbox"

    def test_lift_applies_on_assignment(self) -> None:
        """Assigning ``data`` later lifts the ``url`` as well."""
        payload = WebPushPayloadSchema(title="Oi")
        payload.data = {"url": "/late"}
        assert payload.url == "/late"

    def test_top_level_url_wins_and_logs_conflict(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A differing ``data["url"]`` never replaces the declared ``url``."""
        with caplog.at_level(logging.WARNING, logger=SCHEMAS_LOGGER):
            payload = WebPushPayloadSchema(url="/a", data={"url": "/b"})
        assert payload.url == "/a"
        assert "top-level url decides" in caplog.text

    def test_matching_urls_do_not_log(self, caplog: pytest.LogCaptureFixture) -> None:
        """The same value in both places is not a conflict."""
        with caplog.at_level(logging.WARNING, logger=SCHEMAS_LOGGER):
            WebPushPayloadSchema(url="/a", data={"url": "/a"})
        assert caplog.text == ""

    def test_non_string_data_url_is_ignored(self) -> None:
        """Only a string ``data["url"]`` is a destination."""
        payload = WebPushPayloadSchema(data={"url": 42})
        assert payload.url is None

    def test_action_dicts_are_coerced(self) -> None:
        """Plain dicts still validate as actions, now typed."""
        payload = WebPushPayloadSchema.model_validate(
            {"actions": [{"action": "open", "title": "Abrir", "url": "/x"}]}
        )
        assert payload.actions is not None
        assert payload.actions[0] == WebPushActionSchema(
            action="open", title="Abrir", url="/x"
        )

    def test_invalid_dir_is_rejected(self) -> None:
        """``dir`` only takes the three values ``NotificationDirection`` allows."""
        with pytest.raises(ValueError):
            WebPushPayloadSchema.model_validate({"dir": "up"})
