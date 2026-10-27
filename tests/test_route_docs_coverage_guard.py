"""Every route-docs key exists in every locale, and every key is used.

The catalog is two dictionaries per language keyed by strings, and nothing
type-checks a dictionary key. A route added with English text only would
render the English summary in a Portuguese document — or, for a route whose
English entry is ``None``, the endpoint name and the English docstring —
and read like a deliberate choice to whoever opens ``/docs``. That is the
same silent failure ``test_i18n_coverage_guard`` closes for error codes.

So this guard holds four things:

* both locales carry the same operation keys and the same text keys;
* every ``pt-BR`` operation sets **both** fields (an English ``None`` falls
  back to English, so the Portuguese one cannot);
* every key the package reads (``route_docs.summary("...")``,
  ``.description("...")``, ``.text("...")``) exists, and every key the
  catalog holds is read somewhere — an orphan is text nothing renders;
* the ``{placeholder}`` set of an entry is the same in both languages,
  because ``RouteDocs.description(key, prefix=...)`` formats with the
  same keyword arguments whatever the locale.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import pytest

import tempest_fastapi_sdk
from tempest_fastapi_sdk.api.route_docs import ROUTE_DOCS, ROUTE_TEXTS, RouteDoc
from tempest_fastapi_sdk.core import SUPPORTED_DOCS_LOCALES, DocsLocale

PACKAGE_DIR: Path = Path(tempest_fastapi_sdk.__file__).parent

KEY_READ: re.Pattern[str] = re.compile(
    r"route_docs\.(summary|description|text)\(\s*\"([^\"]+)\"",
)
"""A catalog read in package source, capturing the method and the key."""

PLACEHOLDER: re.Pattern[str] = re.compile(r"\{([a-z_]+)\}")
"""A ``str.format`` placeholder (or a literal ``{token}`` path segment)."""


def _used_keys() -> dict[str, set[str]]:
    """Scan the package for catalog reads.

    Returns:
        dict[str, set[str]]: ``{"route": {...}, "text": {...}}``.
    """
    used: dict[str, set[str]] = {"route": set(), "text": set()}
    for path in PACKAGE_DIR.rglob("*.py"):
        if "route_docs" in path.parts:
            continue
        for method, key in KEY_READ.findall(path.read_text(encoding="utf-8")):
            used["text" if method == "text" else "route"].add(key)
    return used


def catalog_problems(
    routes: Mapping[DocsLocale, Mapping[str, RouteDoc]],
    texts: Mapping[DocsLocale, Mapping[str, str]],
    used: Mapping[str, set[str]],
) -> list[str]:
    """List every way the catalog breaks the rules this guard holds.

    Args:
        routes (Mapping[DocsLocale, Mapping[str, RouteDoc]]): Operation entries
            per locale.
        texts (Mapping[DocsLocale, Mapping[str, str]]): Text entries per locale.
        used (Mapping[str, set[str]]): Keys the package reads, under
            ``"route"`` and ``"text"``.

    Returns:
        list[str]: One line per problem; empty when the catalog is sound.
    """
    problems: list[str] = []
    for kind, tables in (("route", routes), ("text", texts)):
        all_keys: set[str] = set().union(*(set(t) for t in tables.values()))
        for locale, table in tables.items():
            for key in sorted(all_keys - set(table)):
                problems.append(f"{kind} {key!r} missing in {locale}")
        for key in sorted(used[kind] - all_keys):
            problems.append(f"{kind} {key!r} is read by the package but absent")
        for key in sorted(all_keys - used[kind]):
            problems.append(f"{kind} {key!r} is never read (orphan)")
    for key, entry in routes.get("pt-BR", {}).items():
        if not entry.summary or not entry.description:
            problems.append(f"route {key!r} in pt-BR must set summary and description")
    for locale, table in texts.items():
        for key, value in table.items():
            if not value.strip():
                problems.append(f"text {key!r} is empty in {locale}")
    en_routes = routes.get("en-US", {})
    for key, entry in routes.get("pt-BR", {}).items():
        english = en_routes.get(key)
        if english is None or english.description is None:
            continue
        wanted = set(PLACEHOLDER.findall(english.description))
        got = set(PLACEHOLDER.findall(entry.description or ""))
        if wanted != got:
            problems.append(
                f"route {key!r} placeholders differ: en-US {sorted(wanted)}, "
                f"pt-BR {sorted(got)}"
            )
    return problems


def test_the_shipped_catalog_is_sound() -> None:
    """The catalog the package ships breaks none of the rules."""
    assert catalog_problems(ROUTE_DOCS, ROUTE_TEXTS, _used_keys()) == []


def test_the_scan_finds_the_reads() -> None:
    """The source scan is not vacuous: it sees the auth and SSE reads."""
    used = _used_keys()
    assert {"auth.login", "auth.signup", "sse.stream", "health.liveness"} <= used[
        "route"
    ]
    assert "auth.login.429" in used["text"]


def test_text_differs_between_languages() -> None:
    """A Portuguese text equal to the English one is an untranslated text.

    ``app_errors.list_app_errors.page_size`` is the exception the other
    way round: its English entry was already Portuguese before #643 and
    stays so to keep the English document byte for byte.
    """
    english: Mapping[str, str] = ROUTE_TEXTS["en-US"]
    same: list[str] = [
        key for key, value in ROUTE_TEXTS["pt-BR"].items() if english.get(key) == value
    ]
    assert same == ["app_errors.list_app_errors.page_size"]


def test_every_pt_operation_differs_from_english() -> None:
    """No Portuguese summary or description is a copy of the English one."""
    english: Mapping[str, RouteDoc] = ROUTE_DOCS["en-US"]
    copied: list[str] = [
        key
        for key, entry in ROUTE_DOCS["pt-BR"].items()
        if entry.summary == english[key].summary
        or entry.description == english[key].description
    ]
    assert copied == []


class TestTheGuardFires:
    """The guard reports the shapes it exists for."""

    def _sound(
        self,
    ) -> tuple[
        dict[DocsLocale, dict[str, RouteDoc]],
        dict[DocsLocale, dict[str, str]],
        dict[str, set[str]],
    ]:
        """Return a mutable copy of the shipped catalog and its reads.

        Returns:
            tuple: Routes per locale, texts per locale, used keys.
        """
        return (
            {loc: dict(t) for loc, t in ROUTE_DOCS.items()},
            {loc: dict(t) for loc, t in ROUTE_TEXTS.items()},
            _used_keys(),
        )

    def test_a_route_without_portuguese(self) -> None:
        """Removing one pt-BR entry is reported as missing."""
        routes, texts, used = self._sound()
        del routes["pt-BR"]["auth.login"]
        assert catalog_problems(routes, texts, used) == [
            "route 'auth.login' missing in pt-BR",
        ]

    def test_a_text_without_portuguese(self) -> None:
        """Removing one pt-BR text is reported as missing."""
        routes, texts, used = self._sound()
        del texts["pt-BR"]["auth.login.429"]
        assert catalog_problems(routes, texts, used) == [
            "text 'auth.login.429' missing in pt-BR",
        ]

    def test_an_orphan(self) -> None:
        """A key in both locales that nothing reads is an orphan."""
        routes, texts, used = self._sound()
        for table in routes.values():
            table["auth.gone"] = RouteDoc(summary="x", description="y")
        assert catalog_problems(routes, texts, used) == [
            "route 'auth.gone' is never read (orphan)",
        ]

    def test_a_read_without_an_entry(self) -> None:
        """A key the package reads that the catalog lacks is reported."""
        routes, texts, used = self._sound()
        used["route"].add("auth.new_route")
        assert catalog_problems(routes, texts, used) == [
            "route 'auth.new_route' is read by the package but absent",
        ]

    def test_a_portuguese_entry_relying_on_the_english_fallback(self) -> None:
        """A pt-BR ``None`` would render the English docstring."""
        routes, texts, used = self._sound()
        routes["pt-BR"]["push.register"] = RouteDoc(summary="Registrar")
        assert catalog_problems(routes, texts, used) == [
            "route 'push.register' in pt-BR must set summary and description",
        ]

    def test_a_dropped_placeholder(self) -> None:
        """A translation that loses ``{prefix}`` is reported."""
        routes, texts, used = self._sound()
        routes["pt-BR"]["sse.stream"] = RouteDoc(
            summary="Abrir o stream",
            description="Autentique com `?{ticket_param}=`.",
        )
        assert catalog_problems(routes, texts, used) == [
            "route 'sse.stream' placeholders differ: en-US "
            "['prefix', 'ticket_param'], pt-BR ['ticket_param']",
        ]


@pytest.mark.parametrize("locale", sorted(ROUTE_DOCS))
def test_locales_match_the_supported_set(locale: str) -> None:
    """The catalog holds exactly the locales ``DocsLocale`` names."""
    assert locale in SUPPORTED_DOCS_LOCALES
    assert set(ROUTE_DOCS) == set(SUPPORTED_DOCS_LOCALES) == set(ROUTE_TEXTS)
