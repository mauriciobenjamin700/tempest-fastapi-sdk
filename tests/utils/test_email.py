"""Tests for tempest_fastapi_sdk.utils.email.EmailUtils."""

import asyncio
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


class _TinySMTP:
    """A real SMTP server, small enough to live in a test file.

    The bulk path is about the *protocol*: how many connections it opens,
    and what it does with a per-recipient refusal. A monkeypatched
    `aiosmtplib.send` can answer neither, so this speaks the handful of
    verbs aiosmtplib needs over a real socket and counts what arrives.
    """

    def __init__(self, refuse: dict[str, tuple[int, str]] | None = None) -> None:
        """Initialize.

        Args:
            refuse (dict[str, tuple[int, str]] | None): Addresses to answer
                with ``(code, message)`` at ``RCPT TO`` instead of ``250``.
        """
        self.refuse: dict[str, tuple[int, str]] = refuse or {}
        self.delivered: list[str] = []
        self.connections: int = 0
        self.server: asyncio.AbstractServer | None = None
        self.port: int = 0

    async def start(self) -> int:
        """Listen on an ephemeral port.

        Returns:
            int: The port the server bound to.
        """
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = int(self.server.sockets[0].getsockname()[1])
        return self.port

    async def stop(self) -> None:
        """Close the listening socket."""
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Serve one connection until QUIT.

        Args:
            reader (asyncio.StreamReader): Client stream.
            writer (asyncio.StreamWriter): Server stream.
        """
        self.connections += 1
        writer.write(b"220 tiny ESMTP\r\n")
        await writer.drain()
        pending: list[str] = []
        while True:
            line = await reader.readline()
            if not line:
                break
            command = line.decode("utf-8", "replace").strip()
            upper = command.upper()
            if upper.startswith(("HELO", "EHLO")):
                writer.write(b"250-tiny\r\n250 SIZE 10240000\r\n")
            elif upper.startswith("MAIL FROM"):
                writer.write(b"250 OK\r\n")
            elif upper.startswith("RCPT TO"):
                address = command.split("<", 1)[-1].split(">", 1)[0]
                if address in self.refuse:
                    code, text = self.refuse[address]
                    writer.write(f"{code} {text}\r\n".encode())
                else:
                    pending.append(address)
                    writer.write(b"250 OK\r\n")
            elif upper == "DATA":
                writer.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                await writer.drain()
                while True:
                    chunk = await reader.readline()
                    if chunk in (b".\r\n", b".\n", b""):
                        break
                self.delivered.extend(pending)
                pending = []
                writer.write(b"250 Message accepted\r\n")
            elif upper == "QUIT":
                writer.write(b"221 Bye\r\n")
                await writer.drain()
                break
            elif upper == "RSET":
                pending = []
                writer.write(b"250 OK\r\n")
            else:
                writer.write(b"250 OK\r\n")
            await writer.drain()
        writer.close()


class TestSendMany:
    async def _serve(
        self,
        refuse: dict[str, tuple[int, str]] | None = None,
    ) -> tuple[_TinySMTP, EmailUtils]:
        server = _TinySMTP(refuse=refuse)
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

    async def test_connection_failure_still_raises(self) -> None:
        """A failure of the operation is not a per-recipient failure."""
        mailer = EmailUtils(
            host="127.0.0.1",
            port=1,
            from_addr="no-reply@tempest.dev",
            use_starttls=False,
            timeout=2.0,
        )
        with pytest.raises(Exception):  # noqa: B017 — aiosmtplib's connect errors
            await mailer.send_many(["a@x.com"], subject="s", body="b")
