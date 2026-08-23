"""An outbox instead of an SMTP connection.

:class:`~tempest_fastapi_sdk.EmailUtils` is a concrete class, and
``UserAuthService`` is typed against it, so this fake **subclasses** it
rather than reimplementing a protocol: that is what lets it be passed
straight into ``UserAuthService(email=...)`` with the type-checker satisfied.
Only :meth:`send` is replaced — :meth:`EmailUtils.render_template` keeps
working, so a test can assert on the same HTML production renders.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from tempest_fastapi_sdk.utils.email import EmailUtils


@dataclass(frozen=True, slots=True)
class SentEmail:
    """One message the fake accepted.

    Attributes:
        to (tuple[str, ...]): Recipients, normalized to a tuple.
        subject (str): The subject line.
        body (str): The plain-text body.
        html (str | None): The HTML alternative, when one was given.
        cc (tuple[str, ...]): Carbon copies.
        bcc (tuple[str, ...]): Blind carbon copies.
        attachments (tuple[Path, ...]): Files that would have been attached.
        reply_to (str | None): Reply-To, when set.
        from_addr (str | None): Per-message sender override, when set.
    """

    to: tuple[str, ...]
    subject: str
    body: str
    html: str | None = None
    cc: tuple[str, ...] = field(default_factory=tuple)
    bcc: tuple[str, ...] = field(default_factory=tuple)
    attachments: tuple[Path, ...] = field(default_factory=tuple)
    reply_to: str | None = None
    from_addr: str | None = None


class FakeEmailUtils(EmailUtils):
    """An ``EmailUtils`` whose messages land in :attr:`outbox`.

    Example:

        >>> mailer = FakeEmailUtils()
        >>> await mailer.send("ana@example.test", "Ativação", "Seu link")
        >>> mailer.outbox[0].subject
        'Ativação'

    Attributes:
        outbox (list[SentEmail]): Messages accepted, in order.
    """

    def __init__(
        self,
        *,
        from_addr: str = "fake@example.test",
        template_dir: str | Path | None = None,
    ) -> None:
        """Build a mailer that never opens a socket.

        Args:
            from_addr (str): Default sender stamped on messages.
            template_dir (str | Path | None): Passed straight through, so
                :meth:`render_template` renders the service's real
                templates.

        The host and port the parent needs are placeholders on purpose:
        nothing here connects, and a plausible-looking host would invite the
        reader to think it might.
        """
        super().__init__(
            host="fake.invalid",
            port=0,
            from_addr=from_addr,
            template_dir=template_dir,
        )
        self.outbox: list[SentEmail] = []
        self._failures: list[BaseException] = []

    def fail_next(self, error: BaseException) -> None:
        """Make the next :meth:`send` raise ``error``.

        Args:
            error (BaseException): The exception to raise. Queue several to
                fail several sends, in the order queued.
        """
        self._failures.append(error)

    async def send(
        self,
        to: str | Iterable[str],
        subject: str,
        body: str,
        *,
        html: str | None = None,
        cc: Iterable[str] | None = None,
        bcc: Iterable[str] | None = None,
        attachments: Iterable[Path] | None = None,
        reply_to: str | None = None,
        from_addr: str | None = None,
    ) -> None:
        """Append the message to :attr:`outbox` instead of sending it.

        Args:
            to (str | Iterable[str]): Recipient or recipients.
            subject (str): Subject line.
            body (str): Plain-text body.
            html (str | None): HTML alternative.
            cc (Iterable[str] | None): Carbon copies.
            bcc (Iterable[str] | None): Blind carbon copies.
            attachments (Iterable[Path] | None): Files to attach.
            reply_to (str | None): Reply-To header.
            from_addr (str | None): Sender override for this message.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
        """
        if self._failures:
            raise self._failures.pop(0)
        recipients = (to,) if isinstance(to, str) else tuple(to)
        self.outbox.append(
            SentEmail(
                to=recipients,
                subject=subject,
                body=body,
                html=html,
                cc=tuple(cc or ()),
                bcc=tuple(bcc or ()),
                attachments=tuple(attachments or ()),
                reply_to=reply_to,
                from_addr=from_addr,
            ),
        )

    def sent_to(self, address: str) -> list[SentEmail]:
        """Every message addressed to one recipient.

        Args:
            address (str): The address to filter by, matched in ``to``,
                ``cc`` and ``bcc``.

        Returns:
            list[SentEmail]: Matching messages, in order.
        """
        return [
            message
            for message in self.outbox
            if address in message.to + message.cc + message.bcc
        ]
