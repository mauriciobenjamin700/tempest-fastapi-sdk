"""Locale resolution for the bundled auth emails and HTML pages.

The bundled activation / password-reset **emails** and the backend-only
**HTML pages** ship in two languages out of the box — Brazilian
Portuguese (``pt-BR``, the default) and US English (``en-US``). This
module centralizes three things so the rest of the auth flow never
hard-codes a language again:

1. :data:`SUPPORTED_LOCALES` — the locales the SDK bundles templates for.
2. :func:`normalize_locale` — turn a loose user value (``"PT-BR"``,
   ``"pt_br"``, ``"ptbr"``) into one canonical supported tag.
3. :func:`negotiate_locale` — pick the best supported locale for a
   browser request from its ``Accept-Language`` header, falling back to a
   configured default.

It also owns the localized **subject lines and plain-text bodies** for the
two transactional emails (:data:`AUTH_EMAIL_MESSAGES`) and the per-locale
:func:`format_expires_at` helper that renders a token expiry as a short
``YYYY-MM-DD HH:MM (UTC)`` / ``DD/MM/YYYY HH:MM (UTC)`` string — no
seconds, no microseconds.
"""

from __future__ import annotations

from datetime import datetime

from tempest_fastapi_sdk.exceptions.i18n import parse_accept_language

SUPPORTED_LOCALES: tuple[str, ...] = ("pt-BR", "en-US")
"""Locales the SDK ships bundled auth templates for. ``pt-BR`` is first
so it acts as the default when nothing else matches."""

DEFAULT_AUTH_LOCALE: str = "pt-BR"
"""Locale used when no value is configured and nothing is negotiated."""

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
    """
    if not value:
        return default
    key = value.strip().lower().replace("_", "-")
    if key in _CANONICAL:
        return _CANONICAL[key]
    # Fall back to the primary subtag (e.g. ``"pt-pt"`` -> ``"pt"``).
    primary = key.split("-", 1)[0]
    return _CANONICAL.get(primary, default)


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
    "SUPPORTED_LOCALES",
    "auth_email_message",
    "auth_page_message",
    "format_expires_at",
    "negotiate_locale",
    "normalize_locale",
]
