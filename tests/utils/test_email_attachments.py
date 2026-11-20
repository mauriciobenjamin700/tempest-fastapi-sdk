"""Tests for in-memory attachments and content-type resolution (#797)."""

from collections.abc import Iterable
from email import message_from_bytes
from email.message import EmailMessage, Message
from email.policy import default
from pathlib import Path
from typing import Any

import pytest

import tempest_fastapi_sdk.utils.email as email_module
from tempest_fastapi_sdk import EmailAttachment, EmailUtils
from tempest_fastapi_sdk.testing.fakes import FakeEmailUtils
from tests.utils.smtp_server import TinySMTP

PDF_BYTES: bytes = b"%PDF-1.4\n%fake pdf body\n%%EOF\n"
"""Stand-in PDF content; only the bytes and the declared type matter here."""


class CapturingSend:
    """Capture ``aiosmtplib.send`` calls instead of opening a socket."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.messages: list[EmailMessage] = []

    async def __call__(self, message: EmailMessage, **kwargs: Any) -> None:
        """Record the message.

        Args:
            message (EmailMessage): The message the mailer built.
            **kwargs (Any): Transport options, ignored.
        """
        self.messages.append(message)


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> CapturingSend:
    """Replace the transport with a recorder.

    Args:
        monkeypatch (pytest.MonkeyPatch): Pytest's patcher.

    Returns:
        CapturingSend: The recorder.
    """
    fake = CapturingSend()
    monkeypatch.setattr(email_module._aiosmtplib, "send", fake)
    return fake


def _mailer() -> EmailUtils:
    """Build a mailer pointed at nothing.

    Returns:
        EmailUtils: A mailer whose transport the fixture replaces.
    """
    return EmailUtils(host="smtp.example.com", port=587, from_addr="bot@example.com")


def _attachment_parts(raw: bytes) -> list[Message]:
    """Parse serialized message bytes and return the attachment parts.

    Args:
        raw (bytes): The message as it would cross the wire.

    Returns:
        list[Message]: The attachment parts, in order.
    """
    parsed = message_from_bytes(raw, policy=default)
    assert isinstance(parsed, EmailMessage)
    return list(parsed.iter_attachments())


async def _send(
    captured: CapturingSend,
    attachments: Iterable[Path | EmailAttachment],
) -> list[Message]:
    """Send one message and return its attachment parts, re-parsed from bytes.

    Args:
        captured (CapturingSend): The transport recorder.
        attachments (Iterable[Path | EmailAttachment]): What to attach.

    Returns:
        list[Message]: The attachment parts of the serialized message.
    """
    await _mailer().send("u@example.com", "Hi", "Body", attachments=attachments)
    return _attachment_parts(captured.messages[-1].as_bytes())


class TestContentType:
    async def test_in_memory_pdf_goes_out_as_application_pdf(
        self, captured: CapturingSend
    ) -> None:
        parts = await _send(
            captured,
            [EmailAttachment("comprovante.pdf", PDF_BYTES, "application/pdf")],
        )
        assert len(parts) == 1
        assert parts[0].get_content_type() == "application/pdf"
        assert parts[0].get_filename() == "comprovante.pdf"
        assert parts[0].get_payload(decode=True) == PDF_BYTES

    async def test_in_memory_type_is_guessed_from_the_filename(
        self, captured: CapturingSend
    ) -> None:
        parts = await _send(captured, [EmailAttachment("comprovante.pdf", PDF_BYTES)])
        assert parts[0].get_content_type() == "application/pdf"

    async def test_explicit_type_wins_over_the_extension(
        self, captured: CapturingSend
    ) -> None:
        parts = await _send(
            captured, [EmailAttachment("data.bin", b"a,b\n", "text/csv")]
        )
        assert parts[0].get_content_type() == "text/csv"
        assert parts[0].get_payload(decode=True) == b"a,b\n"

    async def test_pdf_path_now_goes_out_as_application_pdf(
        self, captured: CapturingSend, tmp_path: Path
    ) -> None:
        path = tmp_path / "boleto.pdf"
        path.write_bytes(PDF_BYTES)
        parts = await _send(captured, [path])
        assert parts[0].get_content_type() == "application/pdf"
        assert parts[0].get_filename() == "boleto.pdf"
        assert parts[0].get_payload(decode=True) == PDF_BYTES

    @pytest.mark.parametrize(
        "filename",
        ["dump.unknownext", "noextension", "export.csv.gz", "backup.tar.gz"],
    )
    async def test_unknown_or_compressed_falls_back_to_octet_stream(
        self, captured: CapturingSend, tmp_path: Path, filename: str
    ) -> None:
        path = tmp_path / filename
        path.write_bytes(b"\x00\x01")
        parts = await _send(captured, [path, EmailAttachment(filename, b"\x00")])
        assert [p.get_content_type() for p in parts] == [
            "application/octet-stream",
            "application/octet-stream",
        ]

    async def test_paths_and_in_memory_attachments_mix_in_order(
        self, captured: CapturingSend, tmp_path: Path
    ) -> None:
        image = tmp_path / "logo.png"
        image.write_bytes(b"\x89PNG\r\n")
        parts = await _send(
            captured,
            [
                image,
                EmailAttachment("comprovante.pdf", PDF_BYTES),
                EmailAttachment("notes.txt", b"hello", "text/plain"),
            ],
        )
        assert [(p.get_filename(), p.get_content_type()) for p in parts] == [
            ("logo.png", "image/png"),
            ("comprovante.pdf", "application/pdf"),
            ("notes.txt", "text/plain"),
        ]

    async def test_any_iterable_is_accepted(self, captured: CapturingSend) -> None:
        parts = await _send(
            captured,
            (EmailAttachment(f"{n}.pdf", PDF_BYTES) for n in ("a", "b")),
        )
        assert [p.get_filename() for p in parts] == ["a.pdf", "b.pdf"]


class TestOverTheWire:
    async def test_pdf_crosses_a_real_smtp_socket_with_its_type(self) -> None:
        server = TinySMTP()
        port = await server.start()
        mailer = EmailUtils(
            host="127.0.0.1",
            port=port,
            from_addr="no-reply@tempest.dev",
            use_starttls=False,
        )
        try:
            await mailer.send(
                "u@example.com",
                "Pagamento confirmado",
                "Segue o comprovante.",
                attachments=[EmailAttachment("comprovante.pdf", PDF_BYTES)],
            )
        finally:
            await server.stop()
        assert len(server.messages) == 1
        parts = _attachment_parts(server.messages[0])
        assert len(parts) == 1
        assert parts[0].get_content_type() == "application/pdf"
        assert parts[0].get_filename() == "comprovante.pdf"
        assert parts[0].get_payload(decode=True) == PDF_BYTES


class TestConstruction:
    @pytest.mark.parametrize(
        "content_type",
        [
            "pdf",
            "application",
            "application/",
            "/pdf",
            "text/csv; charset=utf-8",
            "application/pdf\r\nBcc: x@example.com",
            "a/b/c",
            "application /pdf",
            "",
        ],
    )
    def test_malformed_content_type_is_refused(self, content_type: str) -> None:
        with pytest.raises(ValueError, match="content_type"):
            EmailAttachment("a.pdf", PDF_BYTES, content_type)

    @pytest.mark.parametrize(
        "filename",
        [
            "a\r\nBcc: evil@example.com",
            "a\nb.pdf",
            "a\rb.pdf",
            "../etc/passwd",
            "dir/a.pdf",
            "dir\\a.pdf",
            "a\0.pdf",
        ],
    )
    def test_dangerous_filename_is_refused(self, filename: str) -> None:
        with pytest.raises(ValueError, match="forbidden characters"):
            EmailAttachment(filename, PDF_BYTES)

    def test_empty_filename_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            EmailAttachment("", PDF_BYTES)

    def test_non_ascii_filename_is_accepted(self) -> None:
        attachment = EmailAttachment("relatório.pdf", PDF_BYTES)
        assert attachment.resolved_content_type == "application/pdf"

    def test_is_frozen(self) -> None:
        attachment = EmailAttachment("a.pdf", PDF_BYTES)
        with pytest.raises(AttributeError):
            attachment.filename = "b.pdf"  # type: ignore[misc]

    def test_from_path_reads_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / "x.pdf"
        path.write_bytes(PDF_BYTES)
        assert EmailAttachment.from_path(path) == EmailAttachment("x.pdf", PDF_BYTES)

    async def test_path_with_a_line_break_is_refused_before_sending(
        self, captured: CapturingSend, tmp_path: Path
    ) -> None:
        path = tmp_path / "a\nb.pdf"
        path.write_bytes(PDF_BYTES)
        with pytest.raises(ValueError, match="forbidden characters"):
            await _mailer().send("u@example.com", "Hi", "Body", attachments=[path])
        assert captured.messages == []


class TestFake:
    async def test_fake_records_in_memory_attachments_as_given(self) -> None:
        mailer = FakeEmailUtils()
        attachment = EmailAttachment("a.pdf", PDF_BYTES)
        await mailer.send("u@example.com", "Hi", "Body", attachments=[attachment])
        assert mailer.outbox[0].attachments == (attachment,)
