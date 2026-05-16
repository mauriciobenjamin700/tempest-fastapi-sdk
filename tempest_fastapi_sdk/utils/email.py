"""SMTP email helpers backed by aiosmtplib.

Requires the ``[email]`` extra. The dependency is imported lazily so
``import tempest_fastapi_sdk`` keeps working when the extra is not
installed — :class:`EmailUtils` raises :class:`ImportError` on first
instantiation instead.
"""

from collections.abc import Iterable
from email.message import EmailMessage
from pathlib import Path
from typing import Any

try:
    import aiosmtplib as _aiosmtplib
except ImportError:  # pragma: no cover - guarded by extras
    _aiosmtplib: Any = None  # type: ignore[no-redef]


class EmailUtils:
    """Send transactional emails via SMTP.

    Connection configuration is supplied at construction time; each
    :meth:`send` call opens a fresh SMTP connection (aiosmtplib's
    high-level ``send`` helper handles connect/login/quit). For
    high-volume scenarios consider holding a persistent connection
    via ``aiosmtplib.SMTP`` directly.

    Attributes:
        host (str): SMTP server hostname.
        port (int): SMTP port.
        from_addr (str): Default sender address used as the ``From``
            header.
        use_tls (bool): Whether to connect using SSL/TLS from the
            start (port 465 style).
        use_starttls (bool): Whether to upgrade to TLS via STARTTLS
            after connect (port 587 style).
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        from_addr: str,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = False,
        use_starttls: bool = True,
        timeout: float = 30.0,
    ) -> None:
        """Initialize.

        Args:
            host (str): SMTP server hostname.
            port (int): SMTP port. Common values: ``25`` (plain),
                ``465`` (SSL/TLS), ``587`` (STARTTLS).
            from_addr (str): Default sender address.
            username (str | None): Auth username.
            password (str | None): Auth password.
            use_tls (bool): Connect using SSL/TLS immediately. Set
                this for port ``465``.
            use_starttls (bool): Upgrade to TLS via STARTTLS after
                connect. Set this for port ``587`` (default).
            timeout (float): SMTP socket timeout in seconds.

        Raises:
            ImportError: When the ``[email]`` extra is not installed.
        """
        if _aiosmtplib is None:
            raise ImportError(
                "EmailUtils requires the [email] extra. "
                "Install with `pip install tempest-fastapi-sdk[email]`."
            )
        self.host: str = host
        self.port: int = port
        self.from_addr: str = from_addr
        self._username: str | None = username
        self._password: str | None = password
        self.use_tls: bool = use_tls
        self.use_starttls: bool = use_starttls
        self._timeout: float = timeout

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
        """Send a single email.

        Args:
            to (str | Iterable[str]): Recipient address(es). Listed
                in the ``To`` header.
            subject (str): Subject line.
            body (str): Plain-text body. Always sent; the HTML
                alternative is added as a multipart child when
                ``html`` is also provided.
            html (str | None): Optional HTML alternative body.
            cc (Iterable[str] | None): Additional ``Cc`` recipients.
            bcc (Iterable[str] | None): ``Bcc`` recipients (added to
                the envelope, not the headers).
            attachments (Iterable[Path] | None): Files to attach.
            reply_to (str | None): Value for the ``Reply-To`` header.
            from_addr (str | None): Override the default sender for
                this message.

        Raises:
            aiosmtplib.errors.SMTPException: Re-raised on any SMTP
                error so callers can branch on the specific failure.
        """
        recipients: list[str] = [to] if isinstance(to, str) else list(to)
        cc_list: list[str] = list(cc or [])
        bcc_list: list[str] = list(bcc or [])

        message = EmailMessage()
        message["From"] = from_addr or self.from_addr
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        if cc_list:
            message["Cc"] = ", ".join(cc_list)
        if reply_to:
            message["Reply-To"] = reply_to
        message.set_content(body)
        if html is not None:
            message.add_alternative(html, subtype="html")

        if attachments:
            for path in attachments:
                data = path.read_bytes()
                message.add_attachment(
                    data,
                    maintype="application",
                    subtype="octet-stream",
                    filename=path.name,
                )

        await _aiosmtplib.send(
            message,
            hostname=self.host,
            port=self.port,
            username=self._username,
            password=self._password,
            use_tls=self.use_tls,
            start_tls=self.use_starttls,
            timeout=self._timeout,
            recipients=recipients + cc_list + bcc_list,
        )


__all__: list[str] = [
    "EmailUtils",
]
