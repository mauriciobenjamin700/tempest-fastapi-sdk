"""SMTP email helpers backed by aiosmtplib.

Requires the ``[email]`` extra. The dependency is imported lazily so
``import tempest_fastapi_sdk`` keeps working when the extra is not
installed — :class:`EmailUtils` raises :class:`ImportError` on first
instantiation instead.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import jinja2

try:
    import aiosmtplib as _aiosmtplib_mod

    _aiosmtplib: ModuleType | None = _aiosmtplib_mod
except ImportError:  # pragma: no cover - guarded by extras
    _aiosmtplib = None

try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
except ImportError:  # pragma: no cover - guarded by extras
    Environment = None  # type: ignore[assignment,misc]
    FileSystemLoader = None  # type: ignore[assignment,misc]
    select_autoescape = None  # type: ignore[assignment]


DEFAULT_BULK_BATCH_SIZE: int = 500
"""Recipients served by one SMTP connection in :meth:`EmailUtils.send_many`.

Mirrors the cursor page the Web Push broadcast walks in. Larger means fewer
handshakes and a longer-lived connection — which some providers close on a
schedule of their own, so the win flattens out.
"""

DEFAULT_BULK_CONCURRENCY: int = 32
"""Connections open at once during a bulk send.

Same default as the Web Push fan-out, and for the same reason: unbounded
concurrency is what a provider answers with throttling or a closed socket.
"""

_PERMANENT_FLOOR: int = 500
"""SMTP codes at or above this are permanent (``5xx``) refusals."""


@dataclass(frozen=True)
class FailedRecipient:
    """One address a bulk send could not deliver to.

    Attributes:
        email (str): The address that failed.
        code (int | None): SMTP reply code, when the server gave one.
        message (str): The server's reply text, verbatim.
    """

    email: str
    code: int | None
    message: str


@dataclass(frozen=True)
class BulkEmailReport:
    """What a bulk send actually did.

    Split by what the caller should do next, which is the whole point:
    ``5xx`` means the mailbox does not exist and the address should be
    pruned, ``4xx`` means full or greylisted and the message should be
    requeued. Collapsing them into one "failed" list forces the caller to
    re-parse SMTP codes it already had.

    Attributes:
        delivered (int): Messages the server accepted.
        permanent (list[FailedRecipient]): ``5xx`` refusals — prune these.
        transient (list[FailedRecipient]): ``4xx`` refusals — retry these.
    """

    delivered: int
    permanent: list[FailedRecipient]
    transient: list[FailedRecipient]

    @property
    def failed(self) -> int:
        """Return how many recipients did not receive the message.

        Returns:
            int: ``len(permanent) + len(transient)``.
        """
        return len(self.permanent) + len(self.transient)


@dataclass(frozen=True)
class _BatchOutcome:
    """One connection's share of a bulk send.

    Attributes:
        delivered (int): Messages accepted on this connection.
        permanent (list[FailedRecipient]): ``5xx`` refusals in this batch.
        transient (list[FailedRecipient]): ``4xx`` refusals in this batch.
    """

    delivered: int
    permanent: list[FailedRecipient]
    transient: list[FailedRecipient]


def _classify(
    failure: FailedRecipient,
    *,
    permanent: list[FailedRecipient],
    transient: list[FailedRecipient],
) -> None:
    """File a refusal under permanent or transient by its SMTP code.

    A missing code is filed as transient: the safe error is retrying an
    address that will never work, not pruning one that would have.

    Args:
        failure (FailedRecipient): The refusal to file.
        permanent (list[FailedRecipient]): Accumulator for ``5xx``.
        transient (list[FailedRecipient]): Accumulator for ``4xx``.
    """
    if failure.code is not None and failure.code >= _PERMANENT_FLOOR:
        permanent.append(failure)
    else:
        transient.append(failure)


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
            after connect (port 587 style). Opportunistic — the upgrade
            happens only when the server advertises STARTTLS, so a plain
            server (e.g. MailHog) is left as-is instead of raising.
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
        template_dir: str | Path | None = None,
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
                connect. Set this for port ``587`` (default). The upgrade
                is opportunistic — it is skipped (rather than raising)
                when the server doesn't advertise STARTTLS, so plain dev
                servers like MailHog work without extra config.
            timeout (float): SMTP socket timeout in seconds.
            template_dir (str | Path | None): Directory holding Jinja2
                templates for :meth:`render_template`. Optional —
                templates can be opted into later, and the directory is
                only loaded on first render. Requires the ``[email]``
                extra (Jinja2 ships alongside aiosmtplib).

        Raises:
            ImportError: When the ``[email]`` extra is not installed.

        Notes:
            Jinja environments are memoized one per resolved locale, plus a
            ``None`` key for locale-less renders, and built lazily on first
            use.
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
        self._template_dir: Path | None = (
            Path(template_dir) if template_dir is not None else None
        )
        self._jinja_envs: dict[str | None, jinja2.Environment] = {}

    def _build_message(
        self,
        recipients: list[str],
        *,
        subject: str,
        body: str,
        html: str | None,
        cc: list[str],
        reply_to: str | None,
        from_addr: str | None,
        attachments: Iterable[Path] | None,
    ) -> EmailMessage:
        """Assemble one message, shared by :meth:`send` and the bulk path.

        Args:
            recipients (list[str]): Addresses for the ``To`` header.
            subject (str): Subject line.
            body (str): Plain-text body.
            html (str | None): Optional HTML alternative.
            cc (list[str]): ``Cc`` recipients.
            reply_to (str | None): ``Reply-To`` header.
            from_addr (str | None): Sender override.
            attachments (Iterable[Path] | None): Files to attach.

        Returns:
            EmailMessage: The assembled message.
        """
        message = EmailMessage()
        message["From"] = from_addr or self.from_addr
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        if cc:
            message["Cc"] = ", ".join(cc)
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
        return message

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

        Notes:
            STARTTLS is negotiated **opportunistically**: aiosmtplib upgrades
            the connection only when the server advertises support, so a
            plain server such as MailHog no longer hard-fails with
            ``SMTP STARTTLS extension not supported by server``. Passing
            ``start_tls=True`` would force the upgrade and raise there
            instead. ``use_tls`` (implicit TLS / SMTPS) is mutually exclusive
            with STARTTLS, so the upgrade is disabled in that case.
        """
        recipients: list[str] = [to] if isinstance(to, str) else list(to)
        cc_list: list[str] = list(cc or [])
        bcc_list: list[str] = list(bcc or [])

        message = self._build_message(
            recipients,
            subject=subject,
            body=body,
            html=html,
            cc=cc_list,
            reply_to=reply_to,
            from_addr=from_addr,
            attachments=attachments,
        )

        assert _aiosmtplib is not None, "guarded by __init__"
        start_tls: bool | None = (
            None if (self.use_starttls and not self.use_tls) else False
        )
        await _aiosmtplib.send(
            message,
            hostname=self.host,
            port=self.port,
            username=self._username,
            password=self._password,
            use_tls=self.use_tls,
            start_tls=start_tls,
            timeout=self._timeout,
            recipients=recipients + cc_list + bcc_list,
        )

    async def send_many(
        self,
        recipients: Iterable[str],
        subject: str,
        body: str,
        *,
        html: str | None = None,
        reply_to: str | None = None,
        from_addr: str | None = None,
        batch_size: int = DEFAULT_BULK_BATCH_SIZE,
        max_concurrency: int = DEFAULT_BULK_CONCURRENCY,
    ) -> BulkEmailReport:
        """Send one message to many recipients and report what happened.

        The loop a caller would otherwise write — ``for user in users:
        await mailer.send(...)`` — has four problems this method exists to
        remove, and the first two are why it is not simply ``gather`` over
        :meth:`send`:

        * **One connection per message.** :meth:`send` opens, authenticates
          and quits every time, so five thousand recipients cost five
          thousand TLS handshakes. Here each batch opens **one** connection
          and reuses it, which is where the time goes: connections drop
          from ``len(recipients)`` to ``len(recipients) / batch_size``.
        * **Unbounded fan-out.** ``gather`` over the whole list opens as
          many connections as there are recipients, and every hosted SMTP
          provider caps how many a sender may hold at once — past the cap
          the extra ones are throttled or dropped, and the cap belongs to
          the provider, not to us. At most ``max_concurrency`` connections
          are open at once — the same default the Web Push broadcast
          uses.
        * **Partial failure is unreportable.** The first bad address ends
          the loop; wrapping each item in ``try/except`` throws the detail
          away. Per-recipient failures never raise here, they land in the
          report.
        * **A dead address looks like a busy one.** SMTP separates ``5xx``
          (this mailbox does not exist — stop trying) from ``4xx`` (full,
          greylisted — try later), and the two call for opposite actions:
          prune versus requeue. They are reported apart.

        Concurrency is per connection, not per message: SMTP is a serial
        protocol on a single socket, so a batch is sent sequentially over
        its own connection and parallelism comes from running several
        batches at once.

        Args:
            recipients (Iterable[str]): Addresses to deliver to. Each gets
                its own message — nobody sees anyone else's address.
            subject (str): Subject line, shared by every message.
            body (str): Plain-text body, shared by every message.
            html (str | None): Optional HTML alternative body.
            reply_to (str | None): Value for the ``Reply-To`` header.
            from_addr (str | None): Override the default sender.
            batch_size (int): Recipients per connection. Larger means
                fewer handshakes and a longer-lived connection, which some
                providers cut off on their own schedule.
            max_concurrency (int): Connections open at once.

        Returns:
            BulkEmailReport: Delivered count plus the permanent and
            transient failures, each with the code and message the server
            gave.

        Raises:
            aiosmtplib.errors.SMTPException: Only for a failure of the
                operation itself — the host does not resolve, the
                connection is refused, authentication is rejected. A
                failure that belongs to one recipient is reported, never
                raised.
            ValueError: When ``batch_size`` or ``max_concurrency`` is not
                positive, which would otherwise deliver nothing at all.
        """
        if batch_size <= 0 or max_concurrency <= 0:
            raise ValueError(
                "send_many: batch_size and max_concurrency must be positive; "
                f"got batch_size={batch_size}, max_concurrency={max_concurrency}"
            )

        targets: list[str] = [address for address in recipients if address]
        if not targets:
            return BulkEmailReport(delivered=0, permanent=[], transient=[])

        batches: list[list[str]] = [
            targets[start : start + batch_size]
            for start in range(0, len(targets), batch_size)
        ]
        limit = asyncio.Semaphore(max_concurrency)

        async def run(batch: list[str]) -> _BatchOutcome:
            """Deliver one batch over a single connection."""
            async with limit:
                return await self._send_batch(
                    batch,
                    subject=subject,
                    body=body,
                    html=html,
                    reply_to=reply_to,
                    from_addr=from_addr,
                )

        outcomes = await asyncio.gather(*(run(batch) for batch in batches))
        return BulkEmailReport(
            delivered=sum(outcome.delivered for outcome in outcomes),
            permanent=[row for outcome in outcomes for row in outcome.permanent],
            transient=[row for outcome in outcomes for row in outcome.transient],
        )

    async def _send_batch(
        self,
        batch: list[str],
        *,
        subject: str,
        body: str,
        html: str | None,
        reply_to: str | None,
        from_addr: str | None,
    ) -> _BatchOutcome:
        """Deliver one batch of recipients over a single SMTP connection.

        Args:
            batch (list[str]): The addresses this connection serves.
            subject (str): Subject line.
            body (str): Plain-text body.
            html (str | None): Optional HTML alternative.
            reply_to (str | None): ``Reply-To`` header.
            from_addr (str | None): Sender override.

        Returns:
            _BatchOutcome: Counts and per-recipient failures for this
            batch.

        Raises:
            aiosmtplib.errors.SMTPException: When the connection or the
                login fails — that is the operation failing, not one
                recipient.
        """
        assert _aiosmtplib is not None, "guarded by __init__"
        delivered = 0
        permanent: list[FailedRecipient] = []
        transient: list[FailedRecipient] = []

        client = _aiosmtplib.SMTP(
            hostname=self.host,
            port=self.port,
            use_tls=self.use_tls,
            start_tls=None if (self.use_starttls and not self.use_tls) else False,
            timeout=self._timeout,
        )
        await client.connect()
        try:
            if self._username is not None and self._password is not None:
                await client.login(self._username, self._password)
            for address in batch:
                message = self._build_message(
                    [address],
                    subject=subject,
                    body=body,
                    html=html,
                    cc=[],
                    reply_to=reply_to,
                    from_addr=from_addr,
                    attachments=None,
                )
                try:
                    await client.send_message(message, recipients=[address])
                except _aiosmtplib.SMTPRecipientsRefused as exc:
                    for refusal in exc.recipients:
                        _classify(
                            FailedRecipient(
                                email=refusal.recipient,
                                code=refusal.code,
                                message=str(refusal.message),
                            ),
                            permanent=permanent,
                            transient=transient,
                        )
                except _aiosmtplib.SMTPResponseException as exc:
                    _classify(
                        FailedRecipient(
                            email=address,
                            code=exc.code,
                            message=str(exc.message),
                        ),
                        permanent=permanent,
                        transient=transient,
                    )
                else:
                    delivered += 1
        finally:
            with contextlib.suppress(Exception):
                await client.quit()

        return _BatchOutcome(
            delivered=delivered,
            permanent=permanent,
            transient=transient,
        )

    def render_template(
        self,
        template_name: str,
        context: dict[str, Any],
        *,
        locale: str | None = None,
    ) -> str:
        """Render a Jinja2 template from ``template_dir`` with ``context``.

        The Jinja environment is built lazily on first call and
        memoized per ``locale`` — subsequent renders for the same locale
        reuse the same loader. HTML autoescaping is enabled for
        ``.html`` / ``.htm`` / ``.xml`` templates so caller-supplied
        values cannot break out into markup.

        Template lookup order (first hit wins):

        1. ``template_dir/<locale>/<name>`` — project override, this locale.
        2. ``template_dir/<name>`` — project override, legacy flat layout.
        3. ``<sdk>/auth/templates/<locale>/<name>`` — SDK bundled, this
           locale (e.g. the localized activation / password-reset emails).

        When ``locale`` is ``None`` the locale subdirectories are skipped
        and only the flat ``template_dir`` and the SDK bundled root are
        searched — this preserves the pre-0.59 behavior for generic
        callers that ship their own ``template_dir``.

        Args:
            template_name (str): Template filename (e.g. ``"welcome.html"``,
                ``"password_reset.txt"``).
            context (dict[str, Any]): Variables exposed inside the
                template.
            locale (str | None): Canonical locale (e.g. ``"pt-BR"`` /
                ``"en-US"``) selecting the per-locale template
                subdirectory. ``None`` uses the flat layout.

        Returns:
            str: Rendered template body — pass this directly to
            :meth:`send` as ``body`` (text) or ``html``.

        Raises:
            RuntimeError: When ``template_dir`` was not configured at
                construction time.
            ImportError: When Jinja2 is missing (it ships with the
                ``[email]`` extra since v0.24.0; older installs may
                need to upgrade).
            jinja2.TemplateNotFound: When the file cannot be located
                under ``template_dir``.

        Example:

            >>> emails = EmailUtils(..., template_dir="emails/")
            >>> html = emails.render_template(
            ...     "welcome.html",
            ...     {"user_name": "Ana", "app_url": "https://app/"},
            ...     locale="pt-BR",
            ... )
            >>> await emails.send(
            ...     "ana@example.com",
            ...     subject="Bem-vinda!",
            ...     body="Bem-vinda, Ana!",
            ...     html=html,
            ... )

        Notes:
            Templates resolve through a ``ChoiceLoader``: the project's own
            ``template_dir`` first, then the SDK's bundled ones
            (``auth/activation``, ``auth/password_reset``). That is what lets
            the bundled auth flow render its default emails without the
            caller having to ship a ``template_dir`` at all.

            The bundled templates live under per-locale subdirectories
            (``pt-BR`` / ``en-US``), so a locale-less render falls back to
            the default locale's subdirectory to reach them.
        """
        if Environment is None:
            raise ImportError(
                "EmailUtils.render_template requires Jinja2. "
                "Install with `pip install tempest-fastapi-sdk[email]`."
            )
        env = self._jinja_envs.get(locale)
        if env is None:
            from jinja2 import ChoiceLoader

            search_paths: list[Path] = []
            sdk_auth_templates = Path(__file__).resolve().parent.parent / (
                "auth/templates"
            )
            from tempest_fastapi_sdk.auth.locale import DEFAULT_AUTH_LOCALE

            bundled_locale = locale or DEFAULT_AUTH_LOCALE
            if self._template_dir is not None:
                if locale is not None:
                    search_paths.append(self._template_dir / locale)
                search_paths.append(self._template_dir)
            if (sdk_auth_templates / bundled_locale).is_dir():
                search_paths.append(sdk_auth_templates / bundled_locale)
            if sdk_auth_templates.is_dir():
                search_paths.append(sdk_auth_templates)
            if not search_paths:
                raise RuntimeError(
                    "EmailUtils.render_template needs either ``template_dir`` "
                    "set or the SDK auth templates to be reachable."
                )
            env = Environment(
                loader=ChoiceLoader([FileSystemLoader(str(p)) for p in search_paths]),
                autoescape=select_autoescape(["html", "htm", "xml"]),
                enable_async=False,
            )
            self._jinja_envs[locale] = env
        template = env.get_template(template_name)
        rendered: str = template.render(**context)
        return rendered


__all__: list[str] = [
    "DEFAULT_BULK_BATCH_SIZE",
    "DEFAULT_BULK_CONCURRENCY",
    "BulkEmailReport",
    "EmailUtils",
    "FailedRecipient",
]
