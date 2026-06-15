"""SMTP email helpers backed by aiosmtplib.

Requires the ``[email]`` extra. The dependency is imported lazily so
``import tempest_fastapi_sdk`` keeps working when the extra is not
installed — :class:`EmailUtils` raises :class:`ImportError` on first
instantiation instead.
"""

from __future__ import annotations

from collections.abc import Iterable
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
        # One Jinja environment per resolved locale (plus the ``None`` key
        # for locale-less renders), built lazily and memoized.
        self._jinja_envs: dict[str | None, jinja2.Environment] = {}

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

        assert _aiosmtplib is not None, "guarded by __init__"
        # STARTTLS is negotiated *opportunistically*: aiosmtplib upgrades
        # the connection only when the server advertises STARTTLS, so a
        # plain server (e.g. MailHog) no longer hard-fails with
        # "SMTP STARTTLS extension not supported by server." Passing
        # ``start_tls=True`` would force it and raise on plain servers.
        # ``use_tls`` (implicit TLS / SMTPS) is mutually exclusive with
        # STARTTLS, so the upgrade is disabled in that case.
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
        """
        if Environment is None:
            raise ImportError(
                "EmailUtils.render_template requires Jinja2. "
                "Install with `pip install tempest-fastapi-sdk[email]`."
            )
        env = self._jinja_envs.get(locale)
        if env is None:
            # ChoiceLoader: project templates first, SDK bundled
            # templates (auth/activation, auth/password_reset) as
            # fallback. Lets the bundled auth flow render its
            # default emails without forcing the caller to ship
            # ``template_dir``.
            from jinja2 import ChoiceLoader

            search_paths: list[Path] = []
            sdk_auth_templates = Path(__file__).resolve().parent.parent / (
                "auth/templates"
            )
            # The SDK bundled templates live under per-locale subdirs
            # (``pt-BR`` / ``en-US``). A locale-less render still needs to
            # reach them, so fall back to the default locale subdir.
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
    "EmailUtils",
]
