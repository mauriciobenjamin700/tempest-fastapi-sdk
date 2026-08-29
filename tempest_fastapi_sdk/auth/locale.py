"""Locale resolution for the bundled auth emails and HTML pages.

The bundled activation / password-reset **emails** and the backend-only
**HTML pages** ship in two languages out of the box — Brazilian
Portuguese (``pt-BR``, the default) and US English (``en-US``). This
module centralizes the language decision so the rest of the auth flow
never hard-codes one again:

1. :data:`SUPPORTED_LOCALES` — the locales the SDK bundles templates for.
2. :func:`normalize_locale` — turn a loose user value (``"PT-BR"``,
   ``"pt_br"``, ``"ptbr"``) into one canonical supported tag.
3. :func:`negotiate_locale` — pick the best supported locale for a
   browser request from its ``Accept-Language`` header, falling back to a
   configured default.
4. :func:`resolve_locale` — the one entry point both ends of a flow use:
   the ``?lang=`` on the link, then the stored user preference, then the
   header, then the configured default. Email and page called different
   things through v0.263.0, so a single activation could arrive in one
   language and open in the other.
5. :func:`stamp_locale` — write that ``?lang=`` onto the emailed link.

It also owns the localized **subject lines and plain-text bodies** for the
two transactional emails (:data:`AUTH_EMAIL_MESSAGES`) and the per-locale
:func:`format_expires_at` helper that renders a token expiry as a short
``YYYY-MM-DD HH:MM (UTC)`` / ``DD/MM/YYYY HH:MM (UTC)`` string — no
seconds, no microseconds.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode, urlsplit, urlunsplit

from tempest_fastapi_sdk.exceptions.i18n import parse_accept_language

SUPPORTED_LOCALES: tuple[str, ...] = ("pt-BR", "en-US")
"""Locales the SDK ships bundled auth templates for. ``pt-BR`` is first
so it acts as the default when nothing else matches."""

DEFAULT_AUTH_LOCALE: str = "pt-BR"
"""Locale used when no value is configured and nothing is negotiated."""

LOCALE_QUERY_PARAM: str = "lang"
"""Query parameter carrying the locale from an emailed link to the page it
opens. Read by :func:`resolve_locale`, written by :func:`stamp_locale`."""

# Maps the canonical lower-cased primary subtag (``"pt"`` / ``"en"``) and
# full tag (``"pt-br"`` / ``"en-us"``) to the canonical supported locale.
_CANONICAL: dict[str, str] = {
    "pt": "pt-BR",
    "ptbr": "pt-BR",
    "pt-br": "pt-BR",
    "en": "en-US",
    "enus": "en-US",
    "en-us": "en-US",
}


def _match(value: str | None) -> str | None:
    """Return the canonical supported locale for ``value``, or ``None``.

    The whole matching rule lives here, once: casing and separators are
    normalized (``"PT-BR"``, ``"pt_br"``, ``"ptbr"`` are the same tag), and
    an unknown full tag falls back to its primary subtag, so ``pt-pt``
    resolves as ``pt``.

    Returning ``None`` instead of a default is what lets
    :func:`resolve_locale` tell "this signal said nothing" from "this
    signal said the default" — the distinction the whole precedence chain
    is built on. :func:`normalize_locale` is this plus a fallback.

    Args:
        value (str | None): A loose locale string, or ``None``.

    Returns:
        str | None: One of :data:`SUPPORTED_LOCALES`, or ``None`` when
        ``value`` is empty or matches nothing.
    """
    if not value:
        return None
    key = value.strip().lower().replace("_", "-")
    if key in _CANONICAL:
        return _CANONICAL[key]
    return _CANONICAL.get(key.split("-", 1)[0])


def normalize_locale(value: str | None, *, default: str = DEFAULT_AUTH_LOCALE) -> str:
    """Coerce a loose locale string into a canonical supported tag.

    Accepts any casing and the common separators users type — ``"PT-BR"``,
    ``"pt_br"``, ``"ptbr"``, ``"pt"`` all map to ``"pt-BR"``; ``"EN"``,
    ``"en_US"``, ``"enus"`` all map to ``"en-US"``. Anything unrecognized
    falls back to ``default``.

    Args:
        value (str | None): The raw locale value (e.g. read from an env
            var). ``None`` or empty yields ``default``.
        default (str): Canonical locale returned when ``value`` cannot be
            matched. Defaults to :data:`DEFAULT_AUTH_LOCALE` (``"pt-BR"``).

    Returns:
        str: One of :data:`SUPPORTED_LOCALES`.

    Notes:
        An unknown full tag falls back to its primary subtag, so ``pt-pt``
        resolves as ``pt``.
    """
    return _match(value) or default


def negotiate_locale(
    accept_language: str | None,
    *,
    default: str = DEFAULT_AUTH_LOCALE,
) -> str:
    """Pick the best supported locale for an HTTP request.

    Parses the ``Accept-Language`` header (ordered by ``q`` weight) and
    returns the first tag that maps to a :data:`SUPPORTED_LOCALES` entry.
    When the header is absent or matches nothing, ``default`` is returned.

    Args:
        accept_language (str | None): Raw ``Accept-Language`` header value
            (e.g. ``"pt-BR,pt;q=0.9,en;q=0.8"``).
        default (str): Canonical locale used when negotiation fails.
            Should already be normalized (e.g. the configured
            ``AUTH_DEFAULT_LOCALE``).

    Returns:
        str: One of :data:`SUPPORTED_LOCALES`.
    """
    for tag in parse_accept_language(accept_language):
        key = tag.lower()
        if key in _CANONICAL:
            return _CANONICAL[key]
        primary = key.split("-", 1)[0]
        if primary in _CANONICAL:
            return _CANONICAL[primary]
    return default


def resolve_locale(
    *,
    user: object | None = None,
    query_locale: str | None = None,
    accept_language: str | None = None,
    default: str = DEFAULT_AUTH_LOCALE,
) -> str:
    """Pick one locale for a whole auth flow, from every signal available.

    Both ends of a flow call this, so the email and the page it links to
    cannot disagree. Precedence, first match wins:

    1. ``query_locale`` — the ``?lang=`` :data:`LOCALE_QUERY_PARAM` that
       :func:`stamp_locale` wrote on the emailed link. **It outranks the
       stored preference on purpose**: it records the language *this
       email* went out in, so a user who changes their preference between
       the send and the click still opens a page that matches the message
       they are reading. Preferring the row instead would recreate the
       exact split this function exists to close.
    2. ``user.locale`` — the preference the consumer persisted, and what
       the email side resolves from (there is no link yet when the email
       is built). Read with ``getattr`` because
       :class:`~tempest_fastapi_sdk.BaseUserModel` declares no such
       column; mix in
       :class:`~tempest_fastapi_sdk.LocaleColumnMixin` to have one.
    3. ``accept_language`` — the browser's own negotiation.
    4. ``default`` — usually ``AUTH_DEFAULT_LOCALE``.

    A signal that is absent, empty, or names an unsupported locale is
    skipped rather than treated as an answer, so a row saying ``fr-FR``
    still reaches the header instead of rendering in a language the SDK
    does not ship.

    Args:
        user (object | None): The user the flow is about, when the caller
            has one. Only its ``locale`` attribute is read.
        query_locale (str | None): Raw value of the ``lang`` query
            parameter, when the request carried one.
        accept_language (str | None): Raw ``Accept-Language`` header.
        default (str): Canonical locale used when nothing else matches.

    Returns:
        str: One of :data:`SUPPORTED_LOCALES`.
    """
    from_query = _match(query_locale)
    if from_query is not None:
        return from_query
    stored = getattr(user, "locale", None) if user is not None else None
    if isinstance(stored, str):
        matched = _match(stored)
        if matched is not None:
            return matched
    return negotiate_locale(accept_language, default=default)


def stamp_locale(
    url: str,
    locale: str,
    *,
    param: str = LOCALE_QUERY_PARAM,
) -> str:
    """Append ``?lang=<locale>`` to an emailed link.

    Lets the page the link opens render in the language of the email that
    carried it, which is the only signal available for an account that was
    just created and has no stored preference yet.

    Every other query parameter is carried over **verbatim** — the opaque
    token already sitting there is never re-encoded.

    Three cases for a URL that already mentions ``param``:

    * ``?lang=en-US`` — returned unchanged. A consumer who writes the
      language into their own template stays in charge.
    * ``?lang=`` — the blank pair is **dropped** and the real value
      appended, instead of leaving a repeated parameter whose answer
      depends on who parses it. Measured: Starlette's ``QueryParams.get``
      returns the *last* occurrence (``"pt-BR"``), while a consumer
      reading ``parse_qs(query)["lang"][0]`` gets the *blank* one.
      Dropping removes the ambiguity rather than betting on the reader.
    * ``?LANG=en-US`` — left alone, and stamped beside. Query-parameter
      names are case-sensitive, so that is somebody else's parameter, not
      another spelling of this one.

    Args:
        url (str): The absolute link that goes into the email.
        locale (str): A canonical supported locale.
        param (str): Query-parameter name. Defaults to
            :data:`LOCALE_QUERY_PARAM`.

    Returns:
        str: The URL with the locale parameter appended.
    """
    parts = urlsplit(url)
    kept: list[str] = []
    for segment in parts.query.split("&") if parts.query else []:
        name, _, value = segment.partition("=")
        if name == param:
            if value:
                return url
            continue
        kept.append(segment)
    kept.append(urlencode({param: locale}))
    query = "&".join(kept)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


# Per-locale ``strftime`` pattern for token expiry. No seconds, no
# microseconds — just the calendar date and the wall-clock minute.
_EXPIRY_FORMAT: dict[str, str] = {
    "pt-BR": "%d/%m/%Y %H:%M",
    "en-US": "%Y-%m-%d %H:%M",
}


def format_expires_at(value: datetime, locale: str) -> str:
    """Render a token-expiry datetime as a short, locale-aware string.

    Drops seconds and microseconds and appends ``(UTC)`` so the reader
    knows the timezone. ``pt-BR`` uses ``DD/MM/YYYY HH:MM``; ``en-US``
    uses ``YYYY-MM-DD HH:MM``.

    Args:
        value (datetime): The expiry timestamp. Naive values are assumed
            to be UTC (the SDK stores token expiries in UTC).
        locale (str): A canonical supported locale; unknown values fall
            back to the ``en-US`` pattern.

    Returns:
        str: e.g. ``"21/06/2026 23:25 (UTC)"`` (pt-BR) or
        ``"2026-06-21 23:25 (UTC)"`` (en-US).
    """
    pattern = _EXPIRY_FORMAT.get(locale, _EXPIRY_FORMAT["en-US"])
    return f"{value.strftime(pattern)} (UTC)"


# Localized subject + plain-text body for each transactional email. The
# plain-text body is the SMTP ``text/plain`` alternative; the rich HTML
# alternative comes from the per-locale Jinja templates. ``{url}`` is
# substituted at send time.
AUTH_EMAIL_MESSAGES: dict[str, dict[str, str]] = {
    "pt-BR": {
        "activation_subject": "Ative sua conta",
        "activation_body": "Abra este link para ativar sua conta: {url}",
        "password_reset_subject": "Redefina sua senha",
        "password_reset_body": "Abra este link para redefinir sua senha: {url}",
        "email_change_subject": "Confirme seu novo e-mail",
        "email_change_body": "Abra este link para confirmar seu novo e-mail: {url}",
        "email_verification_subject": "Verifique seu e-mail",
        "email_verification_body": "Abra este link para verificar seu e-mail: {url}",
        "email_changed_notice_subject": "Seu e-mail foi alterado",
        "email_changed_notice_body": (
            "O e-mail da sua conta foi alterado para {new_email}. "
            "Se não foi você, fale com o suporte imediatamente."
        ),
    },
    "en-US": {
        "activation_subject": "Activate your account",
        "activation_body": "Open this link to activate your account: {url}",
        "password_reset_subject": "Reset your password",
        "password_reset_body": "Open this link to reset your password: {url}",
        "email_change_subject": "Confirm your new email",
        "email_change_body": "Open this link to confirm your new email: {url}",
        "email_verification_subject": "Verify your email",
        "email_verification_body": "Open this link to verify your email: {url}",
        "email_changed_notice_subject": "Your email was changed",
        "email_changed_notice_body": (
            "Your account email was changed to {new_email}. "
            "If this wasn't you, contact support immediately."
        ),
    },
}


# Localized strings rendered *inside* the backend HTML pages (not the
# templates themselves, which are per-locale files, but dynamic messages
# the router injects into the page context).
AUTH_PAGE_MESSAGES: dict[str, dict[str, str]] = {
    "pt-BR": {
        "passwords_do_not_match": "As senhas não coincidem.",
    },
    "en-US": {
        "passwords_do_not_match": "Passwords do not match.",
    },
}


def auth_page_message(locale: str, key: str) -> str:
    """Return a localized backend-page message for ``key``.

    Args:
        locale (str): A canonical supported locale. Unknown locales fall
            back to :data:`DEFAULT_AUTH_LOCALE`.
        key (str): Currently ``"passwords_do_not_match"``.

    Returns:
        str: The localized string.
    """
    table = AUTH_PAGE_MESSAGES.get(locale, AUTH_PAGE_MESSAGES[DEFAULT_AUTH_LOCALE])
    return table[key]


def auth_email_message(locale: str, key: str) -> str:
    """Return a localized email subject/body template for ``key``.

    Args:
        locale (str): A canonical supported locale. Unknown locales fall
            back to :data:`DEFAULT_AUTH_LOCALE`.
        key (str): One of the ``*_subject`` / ``*_body`` keys —
            ``activation``, ``password_reset``, ``email_change``,
            ``email_verification`` and ``email_changed_notice``.

    Returns:
        str: The localized string. ``*_body`` strings still contain a
        placeholder (``{url}``, or ``{new_email}`` for
        ``email_changed_notice_body``) for the caller to ``.format(...)``.
    """
    table = AUTH_EMAIL_MESSAGES.get(locale, AUTH_EMAIL_MESSAGES[DEFAULT_AUTH_LOCALE])
    return table[key]


__all__: list[str] = [
    "AUTH_EMAIL_MESSAGES",
    "AUTH_PAGE_MESSAGES",
    "DEFAULT_AUTH_LOCALE",
    "LOCALE_QUERY_PARAM",
    "SUPPORTED_LOCALES",
    "auth_email_message",
    "auth_page_message",
    "format_expires_at",
    "negotiate_locale",
    "normalize_locale",
    "resolve_locale",
    "stamp_locale",
]
