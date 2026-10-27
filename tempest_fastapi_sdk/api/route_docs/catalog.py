"""Localized OpenAPI text for the routers the SDK builds.

Every bundled factory (``make_auth_router``, ``make_sse_router``,
``make_health_router``, …) reads the ``summary`` and ``description`` of
its operations, and the ``description`` of the parameters and responses it
declares, from this catalog instead of carrying them inline. The language
comes from the factory's ``docs_locale=`` argument, or from
``ServerSettings.API_DOCS_LOCALE`` (the ``API_DOCS_LOCALE`` environment
variable) when the argument is ``None``.

Only prose moves between languages. Paths, operation ids, parameter and
field names, error codes and schema titles are identical in every locale,
so a client generated from the Portuguese document is the client generated
from the English one.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from tempest_fastapi_sdk.api.route_docs.doc import RouteDoc
from tempest_fastapi_sdk.api.route_docs.en_us import ROUTES as EN_US_ROUTES
from tempest_fastapi_sdk.api.route_docs.en_us import TEXTS as EN_US_TEXTS
from tempest_fastapi_sdk.api.route_docs.pt_br import ROUTES as PT_BR_ROUTES
from tempest_fastapi_sdk.api.route_docs.pt_br import TEXTS as PT_BR_TEXTS
from tempest_fastapi_sdk.core.docs_locale import DocsLocale, normalize_docs_locale
from tempest_fastapi_sdk.settings.mixins import ServerSettings

ROUTE_DOCS: Final[Mapping[DocsLocale, Mapping[str, RouteDoc]]] = {
    "en-US": EN_US_ROUTES,
    "pt-BR": PT_BR_ROUTES,
}
"""Operation text per locale, keyed ``<router>.<endpoint name>``."""

ROUTE_TEXTS: Final[Mapping[DocsLocale, Mapping[str, str]]] = {
    "en-US": EN_US_TEXTS,
    "pt-BR": PT_BR_TEXTS,
}
"""Parameter and response descriptions per locale.

Keyed ``<router>.<endpoint name>.<parameter name>`` for a parameter and
``<router>.<endpoint name>.<status code>`` for a response.
"""


def resolve_docs_locale(docs_locale: str | None = None) -> DocsLocale:
    """Decide the language a router documents itself in.

    Args:
        docs_locale (str | None): The factory's ``docs_locale=`` argument.
            ``None`` reads ``API_DOCS_LOCALE`` through
            :class:`~tempest_fastapi_sdk.settings.ServerSettings` (the
            environment and the ``.env`` file), whose default is
            ``"en-US"``.

    Returns:
        DocsLocale: ``"en-US"`` or ``"pt-BR"``.

    Raises:
        ValueError: When the argument names a language the catalog does
            not carry.
        pydantic.ValidationError: When ``API_DOCS_LOCALE`` does.
    """
    if docs_locale is not None:
        return normalize_docs_locale(docs_locale)
    return ServerSettings().API_DOCS_LOCALE


class RouteDocs:
    """The catalog, bound to the language one router is built in.

    A factory builds one at the top (``route_docs = RouteDocs(docs_locale)``)
    and reads every operation's text through it. An unknown key raises
    :class:`KeyError` while the router is being built, so a route whose
    text is missing fails the first test that mounts it rather than
    shipping with an empty summary.

    Attributes:
        locale (DocsLocale): The resolved language.
    """

    def __init__(self, docs_locale: str | None = None) -> None:
        """Resolve the language and bind its tables.

        Args:
            docs_locale (str | None): Passed to :func:`resolve_docs_locale`.
        """
        self.locale: DocsLocale = resolve_docs_locale(docs_locale)
        self._routes: Mapping[str, RouteDoc] = ROUTE_DOCS[self.locale]
        self._texts: Mapping[str, str] = ROUTE_TEXTS[self.locale]

    def _route(self, key: str) -> RouteDoc:
        """Return the entry for ``key``.

        Args:
            key (str): The operation key.

        Returns:
            RouteDoc: The entry.

        Raises:
            KeyError: When the catalog has no such operation.
        """
        try:
            return self._routes[key]
        except KeyError:
            raise KeyError(
                f"route-docs catalog has no {self.locale} entry for {key!r}"
            ) from None

    def summary(self, key: str) -> str | None:
        """Return the operation summary.

        Args:
            key (str): The operation key (``"auth.login"``).

        Returns:
            str | None: The summary, or ``None`` to let FastAPI derive it.
        """
        return self._route(key).summary

    def description(self, key: str, **params: str) -> str | None:
        """Return the operation description, formatted when asked.

        Args:
            key (str): The operation key.
            **params (str): Placeholder values (``prefix=...``). Without
                any, the text is returned as stored, so a literal brace
                (``/activate/{token}``) needs no escaping.

        Returns:
            str | None: The description, or ``None`` to let FastAPI use
            the endpoint docstring.
        """
        text: str | None = self._route(key).description
        if text is None or not params:
            return text
        return text.format(**params)

    def text(self, key: str) -> str:
        """Return a parameter or response description.

        Args:
            key (str): The text key (``"logs.read_logs.page"``).

        Returns:
            str: The description.

        Raises:
            KeyError: When the catalog has no such text.
        """
        try:
            return self._texts[key]
        except KeyError:
            raise KeyError(
                f"route-docs catalog has no {self.locale} text for {key!r}"
            ) from None


__all__: list[str] = [
    "ROUTE_DOCS",
    "ROUTE_TEXTS",
    "RouteDocs",
    "resolve_docs_locale",
]
