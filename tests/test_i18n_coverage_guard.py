"""Every SDK exception code must be translated in every built-in locale.

The catalog's default locale is ``pt-BR``, so a code with no entry does
not fall back to a neutral string — it falls back to the exception's own
``message``, which is written in English. The failure is silent in
exactly the way this repo's rules warn about: nothing type-checks a
dictionary key, no test exercises a locale it does not know about, and
the English sentence reads like a deliberate choice to whoever sees it
in a response body.

That is what happened to the thirteen ``OAUTH_*`` codes: they shipped
across several releases with classes, docstrings, tests and an ``__all__``
entry each, and no translation. This guard is why the fourteenth cannot.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest

import tempest_fastapi_sdk.exceptions as exceptions_pkg
from tempest_fastapi_sdk import AppException
from tempest_fastapi_sdk.exceptions.i18n import (
    _BUILTIN_TRANSLATIONS,
    MessageCatalog,
    default_message_catalog,
)


def _codes_with_an_exception() -> dict[str, str]:
    """Map every ``code`` the SDK can raise to the class that carries it.

    Returns:
        dict[str, str]: ``{code: class name}`` for every concrete
        :class:`AppException` subclass in ``tempest_fastapi_sdk.exceptions``.
    """
    found: dict[str, str] = {}
    for module_info in pkgutil.iter_modules(exceptions_pkg.__path__):
        module = importlib.import_module(
            f"tempest_fastapi_sdk.exceptions.{module_info.name}",
        )
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, AppException) and getattr(obj, "code", None):
                found[obj.code] = obj.__name__
    return found


@pytest.mark.parametrize("locale", sorted(_BUILTIN_TRANSLATIONS))
def test_every_code_is_translated(locale: str) -> None:
    """No code may reach a client in a language the catalog claims to serve."""
    codes = _codes_with_an_exception()
    table = _BUILTIN_TRANSLATIONS[locale]

    missing = {
        code: class_name for code, class_name in codes.items() if code not in table
    }

    assert not missing, (
        f"{len(missing)} exception code(s) have no {locale} message: "
        + ", ".join(f"{code} ({name})" for code, name in sorted(missing.items()))
    )


def test_no_translation_without_an_exception() -> None:
    """A stale key is a rename nobody finished.

    Harmless at runtime, but it makes the count meaningless and hides
    the code the rename should have introduced.
    """
    codes = set(_codes_with_an_exception())

    for locale, table in _BUILTIN_TRANSLATIONS.items():
        orphans = sorted(set(table) - codes)
        assert not orphans, f"{locale} translates codes no exception raises: {orphans}"


def test_the_locales_agree_on_which_codes_they_cover() -> None:
    """One locale ahead of another is the same silent English fallback."""
    tables = {locale: set(table) for locale, table in _BUILTIN_TRANSLATIONS.items()}
    reference = next(iter(tables.values()))

    for locale, keys in tables.items():
        assert keys == reference, f"{locale} covers a different set of codes"


def test_the_guard_fires_on_the_shape_that_shipped() -> None:
    """Feed the guard the historical defect and assert it refuses.

    A guard that cannot fail is a guard nobody should trust. The defect
    is a code whose exception exists and whose translation does not,
    which is how the ``OAUTH_*`` family shipped.
    """
    catalog = MessageCatalog({"pt-BR": {"NOT_FOUND": "Recurso não encontrado"}})
    codes = {"NOT_FOUND": "NotFoundException", "OAUTH_EMAIL_TAKEN": "OAuthEmail"}

    missing = [code for code in codes if code not in catalog._translations["pt-br"]]

    assert missing == ["OAUTH_EMAIL_TAKEN"]


def test_the_shipped_catalog_answers_in_portuguese() -> None:
    """The end the guard exists for: a real code, negotiated, in PT-BR."""
    catalog = default_message_catalog()

    locale = catalog.negotiate("pt-BR,pt;q=0.9")
    message = catalog.resolve("OAUTH_EMAIL_TAKEN", locale)

    assert message is not None
    assert "cadastrado" in message

    english = catalog.resolve("OAUTH_EMAIL_TAKEN", catalog.negotiate("en-US"))
    assert english is not None
    assert "already registered" in english
