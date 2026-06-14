"""Locale negotiation and message catalogs for ``AppException``.

This module turns the English-only error envelope into a localized one
without callers hand-translating each ``raise``. The flow is:

1. Each :class:`~tempest_fastapi_sdk.exceptions.base.AppException` carries
   a stable ``code`` (and optionally an explicit ``message_key`` plus
   ``message_params``).
2. A :class:`MessageCatalog` maps ``(locale, key) -> template`` and
   formats the template with the params.
3. The exception handler negotiates a locale from the request's
   ``Accept-Language`` header (or an explicit default) and resolves the
   localized string, falling back to the exception's own ``detail`` when
   no translation exists.

The SDK ships :func:`default_message_catalog` with PT-BR (default) and
EN-US strings for every built-in exception code; projects extend it with
:meth:`MessageCatalog.merge`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DEFAULT_LOCALE: str = "pt-BR"
"""Locale used when ``Accept-Language`` is absent or matches nothing."""


def parse_accept_language(header: str | None) -> list[str]:
    """Parse an ``Accept-Language`` header into locales by descending ``q``.

    Args:
        header (str | None): The raw header value (e.g.
            ``"pt-BR,pt;q=0.9,en;q=0.8"``). ``None`` yields an empty list.

    Returns:
        list[str]: Locale tags ordered from most to least preferred,
        with the quality values stripped (e.g.
        ``["pt-BR", "pt", "en"]``).
    """
    if not header:
        return []
    parsed: list[tuple[float, int, str]] = []
    for index, part in enumerate(header.split(",")):
        token = part.strip()
        if not token:
            continue
        tag, _, params = token.partition(";")
        tag = tag.strip()
        if not tag or tag == "*":
            continue
        quality = 1.0
        params = params.strip()
        if params.startswith("q="):
            try:
                quality = float(params[2:])
            except ValueError:
                quality = 1.0
        # ``index`` is the stable tiebreaker so equal-q tags keep order.
        parsed.append((quality, index, tag))
    parsed.sort(key=lambda item: (-item[0], item[1]))
    return [tag for _, _, tag in parsed]


class MessageCatalog:
    """Maps ``(locale, key)`` to message templates with locale fallback.

    Locale keys are matched case-insensitively, first by the full tag
    (``"pt-br"``) and then by the primary subtag (``"pt"``), so a catalog
    holding ``"pt-BR"`` answers a request for ``"pt"`` and vice versa.
    """

    def __init__(self, translations: Mapping[str, Mapping[str, str]]) -> None:
        """Initialize the catalog.

        Args:
            translations (Mapping[str, Mapping[str, str]]): A mapping of
                locale tag to ``{message_key: template}``. Templates use
                :meth:`str.format` placeholders (e.g. ``"{email}"``).
        """
        self._translations: dict[str, dict[str, str]] = {
            locale.lower(): dict(table) for locale, table in translations.items()
        }
        # Primary-subtag fallback so a request for ``"en"`` matches a
        # catalog holding ``"en-US"`` (and vice versa). First locale
        # registered for a primary subtag wins.
        self._by_primary: dict[str, dict[str, str]] = {}
        for locale, table in self._translations.items():
            self._by_primary.setdefault(locale.split("-", 1)[0], table)

    @property
    def locales(self) -> list[str]:
        """Return the locale tags this catalog knows, lower-cased."""
        return list(self._translations)

    def _table_for(self, locale: str) -> dict[str, str]:
        """Return the best-matching translation table for ``locale``."""
        normalized = locale.lower()
        table = self._translations.get(normalized)
        if table is not None:
            return table
        return self._by_primary.get(normalized.split("-", 1)[0], {})

    def negotiate(
        self,
        accept_language: str | None,
        *,
        default_locale: str = DEFAULT_LOCALE,
    ) -> str:
        """Pick the best available locale for an ``Accept-Language`` header.

        Args:
            accept_language (str | None): The raw header value.
            default_locale (str): Returned when no preferred locale
                matches the catalog.

        Returns:
            str: A locale tag the catalog can resolve, or
            ``default_locale``.
        """
        for tag in parse_accept_language(accept_language):
            normalized = tag.lower()
            if normalized in self._translations:
                return tag
            if normalized.split("-", 1)[0] in self._by_primary:
                return tag
        return default_locale

    def resolve(
        self,
        key: str,
        locale: str,
        params: Mapping[str, Any] | None = None,
    ) -> str | None:
        """Resolve a message key in a locale, formatting with params.

        Args:
            key (str): The message key (an exception ``code`` or an
                explicit ``message_key``).
            locale (str): The locale tag to resolve in.
            params (Mapping[str, Any] | None): Values interpolated into
                the template via :meth:`str.format`.

        Returns:
            str | None: The formatted message, or ``None`` when the key
            is unknown in the resolved locale. A template referencing a
            missing param is returned unformatted rather than raising.
        """
        template = self._table_for(locale).get(key)
        if template is None:
            return None
        if params:
            try:
                return template.format(**params)
            except (KeyError, IndexError):
                return template
        return template

    def merge(self, other: Mapping[str, Mapping[str, str]]) -> MessageCatalog:
        """Return a new catalog with ``other`` overlaid on this one.

        Per-locale tables are merged key-by-key (``other`` wins), so a
        project can add new locales or override individual messages
        without restating the built-in catalog.

        Args:
            other (Mapping[str, Mapping[str, str]]): Additional
                translations to overlay.

        Returns:
            MessageCatalog: A new, independent catalog.
        """
        merged: dict[str, dict[str, str]] = {
            locale: dict(table) for locale, table in self._translations.items()
        }
        for locale, table in other.items():
            merged.setdefault(locale.lower(), {}).update(table)
        return MessageCatalog(merged)


_BUILTIN_TRANSLATIONS: dict[str, dict[str, str]] = {
    "pt-BR": {
        "INTERNAL_SERVER_ERROR": "Erro interno do servidor",
        "NOT_FOUND": "Recurso não encontrado",
        "CONFLICT": "Conflito de recurso",
        "UNAUTHORIZED": "Não autorizado",
        "FORBIDDEN": "Acesso negado",
        "VALIDATION_ERROR": "Erro de validação",
        "TOO_MANY_REQUESTS": "Requisições em excesso",
        "INVALID_TOKEN": "Token inválido",
        "TOKEN_EXPIRED": "Token expirado",
        "FILE_TOO_LARGE": "Arquivo muito grande",
        "INVALID_FILE_TYPE": "Tipo de arquivo inválido",
    },
    "en-US": {
        "INTERNAL_SERVER_ERROR": "Internal server error",
        "NOT_FOUND": "Resource not found",
        "CONFLICT": "Resource conflict",
        "UNAUTHORIZED": "Unauthorized",
        "FORBIDDEN": "Forbidden",
        "VALIDATION_ERROR": "Validation error",
        "TOO_MANY_REQUESTS": "Too many requests",
        "INVALID_TOKEN": "Invalid token",
        "TOKEN_EXPIRED": "Token expired",
        "FILE_TOO_LARGE": "File too large",
        "INVALID_FILE_TYPE": "Invalid file type",
    },
}


def default_message_catalog() -> MessageCatalog:
    """Return a catalog with PT-BR + EN-US strings for the built-in codes.

    Keys match the ``code`` attribute of every SDK exception
    (``NOT_FOUND``, ``CONFLICT``, ``UNAUTHORIZED``, …). Extend it for
    domain codes via :meth:`MessageCatalog.merge`.

    Returns:
        MessageCatalog: A fresh catalog instance (safe to mutate via
        :meth:`MessageCatalog.merge`).
    """
    return MessageCatalog(_BUILTIN_TRANSLATIONS)


__all__: list[str] = [
    "DEFAULT_LOCALE",
    "MessageCatalog",
    "default_message_catalog",
    "parse_accept_language",
]
