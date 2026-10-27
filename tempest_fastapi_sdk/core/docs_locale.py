"""Languages the bundled routers can write their OpenAPI text in.

Kept in ``core`` rather than next to the catalog in ``api.route_docs``
because :class:`~tempest_fastapi_sdk.settings.ServerSettings` validates
``API_DOCS_LOCALE`` with it, and the settings package must stay importable
without FastAPI (the CLI loads it).
"""

from __future__ import annotations

from typing import Final, Literal

from tempest_fastapi_sdk.core.enums import normalize_locale_tag

DocsLocale = Literal["en-US", "pt-BR"]
"""A language the route-docs catalog carries."""

DEFAULT_DOCS_LOCALE: Final[DocsLocale] = "en-US"
"""The language the bundled routers documented themselves in before #643."""

SUPPORTED_DOCS_LOCALES: Final[tuple[DocsLocale, ...]] = ("en-US", "pt-BR")
"""Every :data:`DocsLocale`, in a stable order."""


def normalize_docs_locale(value: str) -> DocsLocale:
    """Coerce a loose locale string into a supported :data:`DocsLocale`.

    Accepts any case and either separator (``"pt_br"``, ``"PT-BR"``) and
    the bare primary subtag (``"pt"``, ``"en"``), through the same
    :func:`~tempest_fastapi_sdk.core.enums.normalize_locale_tag` the rest
    of the SDK uses. A tag the catalog does not carry is refused instead
    of falling back to English: a ``pt-PT`` deployment that silently
    renders English documentation is the failure this check exists for.

    Args:
        value (str): The raw configured value.

    Returns:
        DocsLocale: ``"en-US"`` or ``"pt-BR"``.

    Raises:
        ValueError: When ``value`` is not a locale tag, or names one the
            catalog does not carry.
    """
    try:
        tag: str = normalize_locale_tag(value).value
    except ValueError as exc:
        raise ValueError(
            f"invalid API docs locale {value!r}; supported: "
            f"{', '.join(SUPPORTED_DOCS_LOCALES)}"
        ) from exc
    if tag not in SUPPORTED_DOCS_LOCALES:
        raise ValueError(
            f"unsupported API docs locale {value!r}; supported: "
            f"{', '.join(SUPPORTED_DOCS_LOCALES)}"
        )
    return tag


__all__: list[str] = [
    "DEFAULT_DOCS_LOCALE",
    "SUPPORTED_DOCS_LOCALES",
    "DocsLocale",
    "normalize_docs_locale",
]
