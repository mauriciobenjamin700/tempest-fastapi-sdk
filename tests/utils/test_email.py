"""Tests for tempest_fastapi_sdk.utils.email.EmailUtils."""

from pathlib import Path
from typing import Any

import pytest

import tempest_fastapi_sdk.utils.email as email_module
from tempest_fastapi_sdk import EmailUtils


class FakeSendCalls:
    """Capture aiosmtplib.send calls without actually contacting SMTP."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, message: Any, **kwargs: Any) -> None:
        self.calls.append({"message": message, **kwargs})


@pytest.fixture
def fake_send(monkeypatch: pytest.MonkeyPatch) -> FakeSendCalls:
    fake = FakeSendCalls()
    monkeypatch.setattr(email_module._aiosmtplib, "send", fake)
    return fake


class TestSend:
    async def test_basic_send(self, fake_send: FakeSendCalls) -> None:
        utils = EmailUtils(
            host="smtp.example.com",
            port=587,
            from_addr="bot@example.com",
        )
        await utils.send("u@example.com", "Hi", "Plain body")
        assert len(fake_send.calls) == 1
        call = fake_send.calls[0]
        message = call["message"]
        assert message["From"] == "bot@example.com"
        assert message["To"] == "u@example.com"
        assert message["Subject"] == "Hi"
        assert call["hostname"] == "smtp.example.com"
        assert call["port"] == 587

    async def test_multiple_recipients_joined(self, fake_send: FakeSendCalls) -> None:
        utils = EmailUtils(
            host="smtp.example.com",
            port=587,
            from_addr="bot@example.com",
        )
        await utils.send(["a@example.com", "b@example.com"], "Hi", "Body")
        message = fake_send.calls[0]["message"]
        assert "a@example.com" in message["To"]
        assert "b@example.com" in message["To"]

    async def test_html_alternative_added(self, fake_send: FakeSendCalls) -> None:
        utils = EmailUtils(
            host="smtp.example.com",
            port=587,
            from_addr="bot@example.com",
        )
        await utils.send(
            "u@example.com",
            "Hi",
            "Plain",
            html="<p>HTML</p>",
        )
        message = fake_send.calls[0]["message"]
        # When an HTML alternative is added the message becomes multipart.
        assert message.is_multipart()

    async def test_attachment_added(
        self,
        fake_send: FakeSendCalls,
        tmp_path: Path,
    ) -> None:
        utils = EmailUtils(
            host="smtp.example.com",
            port=587,
            from_addr="bot@example.com",
        )
        attachment = tmp_path / "doc.bin"
        attachment.write_bytes(b"\x00\x01\x02")
        await utils.send(
            "u@example.com",
            "Hi",
            "Body",
            attachments=[attachment],
        )
        message = fake_send.calls[0]["message"]
        parts = list(message.walk())
        # message + plain body + attachment
        assert any(p.get_filename() == "doc.bin" for p in parts)

    async def test_bcc_added_to_envelope(self, fake_send: FakeSendCalls) -> None:
        utils = EmailUtils(
            host="smtp.example.com",
            port=587,
            from_addr="bot@example.com",
        )
        await utils.send(
            "u@example.com",
            "Hi",
            "Body",
            bcc=["secret@example.com"],
        )
        # BCC should be in the recipients list passed to aiosmtplib
        # but not in the message headers.
        recipients = fake_send.calls[0]["recipients"]
        assert "secret@example.com" in recipients
        assert fake_send.calls[0]["message"].get("Bcc") is None

    async def test_reply_to_header(self, fake_send: FakeSendCalls) -> None:
        utils = EmailUtils(
            host="smtp.example.com",
            port=587,
            from_addr="bot@example.com",
        )
        await utils.send(
            "u@example.com",
            "Hi",
            "Body",
            reply_to="support@example.com",
        )
        message = fake_send.calls[0]["message"]
        assert message["Reply-To"] == "support@example.com"

    async def test_from_addr_override(self, fake_send: FakeSendCalls) -> None:
        utils = EmailUtils(
            host="smtp.example.com",
            port=587,
            from_addr="bot@example.com",
        )
        await utils.send(
            "u@example.com",
            "Hi",
            "Body",
            from_addr="alerts@example.com",
        )
        assert fake_send.calls[0]["message"]["From"] == "alerts@example.com"
