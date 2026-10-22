"""Tests for tempest_fastapi_sdk.utils.email.EmailUtils."""

import logging
from pathlib import Path
from typing import Any

import aiosmtplib
import pytest

import tempest_fastapi_sdk.utils.email as email_module
from tempest_fastapi_sdk import (
    EmailDeliveryException,
    EmailUtils,
    IntegrationDisabledException,
)
from tests.utils.smtp_server import TinySMTP


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

    async def test_starttls_is_opportunistic_by_default(
        self, fake_send: FakeSendCalls
    ) -> None:
        # use_starttls defaults True, but must map to start_tls=None
        # (opportunistic) — NOT True (force) — so a plain server like
        # MailHog doesn't crash with "STARTTLS extension not supported".
        utils = EmailUtils(host="localhost", port=1025, from_addr="dev@local")
        await utils.send("u@example.com", "Hi", "Body")
        assert fake_send.calls[0]["start_tls"] is None
        assert fake_send.calls[0]["use_tls"] is False

    async def test_starttls_disabled_maps_to_false(
        self, fake_send: FakeSendCalls
    ) -> None:
        utils = EmailUtils(
            host="localhost",
            port=1025,
            from_addr="dev@local",
            use_starttls=False,
        )
        await utils.send("u@example.com", "Hi", "Body")
        assert fake_send.calls[0]["start_tls"] is False

    async def test_implicit_tls_disables_starttls_upgrade(
        self, fake_send: FakeSendCalls
    ) -> None:
        # SMTPS (use_tls) is mutually exclusive with STARTTLS in aiosmtplib;
        # the default use_starttls=True must not collide with use_tls=True.
        utils = EmailUtils(
            host="smtp.example.com",
            port=465,
            from_addr="bot@example.com",
            use_tls=True,
        )
        await utils.send("u@example.com", "Hi", "Body")
        assert fake_send.calls[0]["use_tls"] is True
        assert fake_send.calls[0]["start_tls"] is False

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


class TestRenderTemplate:
    def test_renders_html_with_context(self, tmp_path: Path) -> None:
        (tmp_path / "welcome.html").write_text(
            "<p>Hello, {{ user_name }}!</p>",
            encoding="utf-8",
        )
        utils = EmailUtils(
            host="smtp.example.com",
            port=587,
            from_addr="bot@example.com",
            template_dir=tmp_path,
        )
        rendered = utils.render_template(
            "welcome.html",
            {"user_name": "Ana"},
        )
        assert rendered == "<p>Hello, Ana!</p>"

    def test_html_autoescape_protects_against_xss(self, tmp_path: Path) -> None:
        (tmp_path / "x.html").write_text(
            "<p>{{ user_name }}</p>",
            encoding="utf-8",
        )
        utils = EmailUtils(
            host="smtp.example.com",
            port=587,
            from_addr="bot@example.com",
            template_dir=tmp_path,
        )
        rendered = utils.render_template(
            "x.html",
            {"user_name": "<script>alert(1)</script>"},
        )
        assert "<script>" not in rendered
        assert "&lt;script&gt;" in rendered

    def test_text_template_no_autoescape(self, tmp_path: Path) -> None:
        (tmp_path / "msg.txt").write_text(
            "Hi {{ name }} — token: {{ token }}",
            encoding="utf-8",
        )
        utils = EmailUtils(
            host="smtp.example.com",
            port=587,
            from_addr="bot@example.com",
            template_dir=tmp_path,
        )
        rendered = utils.render_template(
            "msg.txt",
            {"name": "Ana", "token": "<abc>"},
        )
        assert rendered == "Hi Ana — token: <abc>"

    def test_without_template_dir_falls_back_to_sdk_bundled(self) -> None:
        # Since v0.31.0 the env falls back to the SDK's bundled
        # auth templates so the default activation / reset flows
        # work without the caller wiring template_dir. Since v0.59.0
        # the bundled templates are per-locale; a locale-less render
        # falls back to the default locale (pt-BR).
        utils = EmailUtils(
            host="smtp.example.com",
            port=587,
            from_addr="bot@example.com",
        )
        from datetime import datetime

        rendered = utils.render_template(
            "activation.html",
            {
                "user": type("U", (), {"name": "Ana", "email": "ana@x"})(),
                "activation_url": "https://app/activate?token=abc",
                "expires_at": datetime(2026, 6, 4),
                "expires_at_str": "04/06/2026 00:00 (UTC)",
            },
        )
        # Default locale is pt-BR.
        assert "Ativar conta" in rendered
        assert "abc" in rendered

    def test_explicit_locale_selects_bundled_language(self) -> None:
        # Passing locale picks the matching bundled template language.
        utils = EmailUtils(
            host="smtp.example.com",
            port=587,
            from_addr="bot@example.com",
        )
        from datetime import datetime

        ctx: dict[str, Any] = {
            "user": type("U", (), {"name": "Ana", "email": "ana@x"})(),
            "activation_url": "https://app/activate?token=abc",
            "expires_at": datetime(2026, 6, 4),
            "expires_at_str": "2026-06-04 00:00 (UTC)",
        }
        assert "Activate account" in utils.render_template(
            "activation.html", ctx, locale="en-US"
        )
        assert "Ativar conta" in utils.render_template(
            "activation.html", ctx, locale="pt-BR"
        )


class TestSendMany:
    async def _serve(
        self,
        refuse: dict[str, tuple[int, str]] | None = None,
    ) -> tuple[TinySMTP, EmailUtils]:
        server = TinySMTP(refuse=refuse)
        port = await server.start()
        mailer = EmailUtils(
            host="127.0.0.1",
            port=port,
            from_addr="no-reply@tempest.dev",
            use_starttls=False,
        )
        return server, mailer

    async def test_one_connection_per_batch_not_per_message(self) -> None:
        """The reason this exists instead of a loop over `send`.

        `send` opens, authenticates and quits per message, so five thousand
        recipients cost five thousand handshakes. Fourteen recipients in
        batches of five must cost three connections, not fourteen.
        """
        server, mailer = await self._serve()
        try:
            report = await mailer.send_many(
                [f"user{n}@x.com" for n in range(14)],
                subject="Maintenance",
                body="02:00 to 03:00.",
                batch_size=5,
                max_concurrency=2,
            )
        finally:
            await server.stop()

        assert report.delivered == 14
        assert server.connections == 3
        assert len(server.delivered) == 14

    async def test_a_refusal_is_reported_not_raised(self) -> None:
        """One bad address must not end the send — that is the loop's bug."""
        server, mailer = await self._serve(
            refuse={"dead@x.com": (550, "No such user here")}
        )
        try:
            report = await mailer.send_many(
                ["a@x.com", "dead@x.com", "b@x.com"],
                subject="Maintenance",
                body="02:00 to 03:00.",
            )
        finally:
            await server.stop()

        assert report.delivered == 2
        assert [row.email for row in report.permanent] == ["dead@x.com"]
        assert report.permanent[0].code == 550
        assert "No such user" in report.permanent[0].message

    async def test_5xx_and_4xx_are_reported_apart(self) -> None:
        """Prune versus requeue is the whole reason the split exists."""
        server, mailer = await self._serve(
            refuse={
                "dead@x.com": (550, "No such user here"),
                "full@x.com": (452, "Mailbox full, try later"),
            }
        )
        try:
            report = await mailer.send_many(
                ["a@x.com", "dead@x.com", "full@x.com"],
                subject="Maintenance",
                body="02:00 to 03:00.",
            )
        finally:
            await server.stop()

        assert report.delivered == 1
        assert [row.email for row in report.permanent] == ["dead@x.com"]
        assert [row.email for row in report.transient] == ["full@x.com"]
        assert report.failed == 2

    async def test_recipients_do_not_see_each_other(self) -> None:
        server, mailer = await self._serve()
        try:
            await mailer.send_many(
                ["a@x.com", "b@x.com"],
                subject="Maintenance",
                body="02:00 to 03:00.",
                batch_size=10,
            )
        finally:
            await server.stop()

        assert server.delivered == ["a@x.com", "b@x.com"]

    async def test_empty_recipient_list_opens_no_connection(self) -> None:
        server, mailer = await self._serve()
        try:
            report = await mailer.send_many([], subject="s", body="b")
        finally:
            await server.stop()

        assert report.delivered == 0
        assert report.failed == 0
        assert server.connections == 0

    async def test_non_positive_knobs_are_refused(self) -> None:
        """Silently delivering nothing is the failure this prevents."""
        server, mailer = await self._serve()
        try:
            with pytest.raises(ValueError, match="must be positive"):
                await mailer.send_many(["a@x.com"], subject="s", body="b", batch_size=0)
            with pytest.raises(ValueError, match="must be positive"):
                await mailer.send_many(
                    ["a@x.com"], subject="s", body="b", max_concurrency=0
                )
        finally:
            await server.stop()

    async def test_connection_failure_raises_email_unavailable(self) -> None:
        """A failure of the operation is not a per-recipient failure.

        Implicit TLS against a plaintext server fails the handshake on the
        first batch: the whole send raises, translated like :meth:`send`.
        """
        server = TinySMTP()
        port = await server.start()
        mailer = EmailUtils(
            host="127.0.0.1",
            port=port,
            from_addr="no-reply@tempest.dev",
            use_tls=True,
            timeout=5.0,
        )
        try:
            with pytest.raises(EmailDeliveryException) as caught:
                await mailer.send_many(["a@x.com"], subject="s", body="b")
        finally:
            await server.stop()

        assert isinstance(caught.value.__cause__, aiosmtplib.SMTPConnectError)
        assert server.delivered == []


class TestTransportFailure:
    """``send`` answers ``503 EMAIL_UNAVAILABLE`` instead of an aiosmtplib error."""

    async def test_implicit_tls_on_a_starttls_port_is_the_incident(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``SMTP_USE_SSL=true`` against a plaintext greeting.

        Reproduces the consumer incident (``smtp.gmail.com:587`` with
        ``SMTP_USE_SSL=true``): the TLS handshake reads the plaintext
        ``220`` banner and fails with ``WRONG_VERSION_NUMBER``.
        """
        server = TinySMTP()
        port = await server.start()
        mailer = EmailUtils(
            "127.0.0.1", port, from_addr="no-reply@tempest.dev", use_tls=True
        )
        caplog.set_level(logging.ERROR, logger="tempest_fastapi_sdk.utils.email")
        try:
            with pytest.raises(EmailDeliveryException) as caught:
                await mailer.send("ana@acme.com", "Hi", "Body")
        finally:
            await server.stop()

        exc = caught.value
        assert exc.status_code == 503
        assert exc.code == "EMAIL_UNAVAILABLE"
        assert exc.details == {}
        assert isinstance(exc.__cause__, aiosmtplib.SMTPConnectError)
        assert "WRONG_VERSION_NUMBER" in str(exc.__cause__)
        (record,) = caplog.records
        assert record.levelno == logging.ERROR
        assert record.exc_info is not None
        assert record.__dict__["smtp_host"] == "127.0.0.1"
        assert record.__dict__["smtp_port"] == port
        assert "ana@acme.com" not in caplog.text

    async def test_refused_recipient_is_logged_without_the_address(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A server reply can quote the address, so it carries no traceback."""
        server = TinySMTP(refuse={"dead@acme.com": (550, "No such user here")})
        port = await server.start()
        mailer = EmailUtils(
            "127.0.0.1", port, from_addr="no-reply@tempest.dev", use_starttls=False
        )
        caplog.set_level(logging.ERROR, logger="tempest_fastapi_sdk.utils.email")
        try:
            with pytest.raises(EmailDeliveryException) as caught:
                await mailer.send("dead@acme.com", "Hi", "Body")
        finally:
            await server.stop()

        assert isinstance(caught.value.__cause__, aiosmtplib.SMTPRecipientsRefused)
        (record,) = caplog.records
        assert record.exc_info is None
        assert "dead@acme.com" not in caplog.text

    async def test_os_error_is_translated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A socket error that aiosmtplib lets through is a transport failure."""

        async def unreachable(*_args: Any, **_kwargs: Any) -> None:
            raise ConnectionResetError("reset by peer")

        monkeypatch.setattr(email_module._aiosmtplib, "send", unreachable)
        mailer = EmailUtils("smtp.example.com", 587, from_addr="bot@example.com")
        with pytest.raises(EmailDeliveryException) as caught:
            await mailer.send("u@example.com", "Hi", "Body")
        assert isinstance(caught.value.__cause__, ConnectionResetError)

    async def test_programming_errors_are_not_translated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only the transport is wrapped; a bug keeps its own type."""

        async def broken(*_args: Any, **_kwargs: Any) -> None:
            raise ValueError("bad argument")

        monkeypatch.setattr(email_module._aiosmtplib, "send", broken)
        mailer = EmailUtils("smtp.example.com", 587, from_addr="bot@example.com")
        with pytest.raises(ValueError, match="bad argument"):
            await mailer.send("u@example.com", "Hi", "Body")

    async def test_unreadable_attachment_is_the_callers_error(
        self, fake_send: FakeSendCalls, tmp_path: Path
    ) -> None:
        """``FileNotFoundError`` is an ``OSError``, but it is not the transport."""
        mailer = EmailUtils("smtp.example.com", 587, from_addr="bot@example.com")
        with pytest.raises(FileNotFoundError):
            await mailer.send(
                "u@example.com", "Hi", "Body", attachments=[tmp_path / "missing"]
            )
        assert fake_send.calls == []

    async def test_disabled_mailer_still_answers_integration_disabled(self) -> None:
        mailer = EmailUtils(
            "smtp.example.com", 587, from_addr="bot@example.com", enabled=False
        )
        with pytest.raises(IntegrationDisabledException):
            await mailer.send("u@example.com", "Hi", "Body")
