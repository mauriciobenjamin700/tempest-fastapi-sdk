"""Tests for tempest_fastapi_sdk.exceptions.i18n and localized handlers."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import (
    AppException,
    MessageCatalog,
    NotFoundException,
    default_message_catalog,
    parse_accept_language,
    register_exception_handlers,
)

# --------------------------------------------------------------------------- #
# parse_accept_language                                                       #
# --------------------------------------------------------------------------- #


def test_parse_accept_language_orders_by_quality() -> None:
    """Tags come back ordered by descending q, stable on ties."""
    assert parse_accept_language("fr;q=0.5,en;q=0.9,pt-BR") == ["pt-BR", "en", "fr"]


def test_parse_accept_language_empty() -> None:
    """An empty or missing header yields no tags."""
    assert parse_accept_language(None) == []
    assert parse_accept_language("") == []


def test_parse_accept_language_ignores_wildcard() -> None:
    """A bare ``*`` is dropped (it carries no concrete locale)."""
    assert parse_accept_language("*") == []


# --------------------------------------------------------------------------- #
# MessageCatalog                                                              #
# --------------------------------------------------------------------------- #


def test_resolve_exact_and_primary_subtag() -> None:
    """A catalog keyed by ``pt-BR`` answers ``pt`` and ``pt-BR`` alike."""
    catalog = MessageCatalog({"pt-BR": {"X": "olá"}, "en-US": {"X": "hi"}})
    assert catalog.resolve("X", "pt-BR") == "olá"
    assert catalog.resolve("X", "pt") == "olá"
    assert catalog.resolve("X", "en") == "hi"
    assert catalog.resolve("X", "en-GB") == "hi"


def test_resolve_unknown_key_returns_none() -> None:
    """An unknown key resolves to ``None`` (handler falls back)."""
    catalog = MessageCatalog({"pt-BR": {"X": "olá"}})
    assert catalog.resolve("MISSING", "pt-BR") is None


def test_resolve_formats_params() -> None:
    """Template placeholders are filled from params."""
    catalog = MessageCatalog({"pt-BR": {"X": "id {id} não encontrado"}})
    assert catalog.resolve("X", "pt-BR", {"id": 7}) == "id 7 não encontrado"


def test_resolve_missing_param_returns_template() -> None:
    """A template referencing a missing param is returned unformatted."""
    catalog = MessageCatalog({"pt-BR": {"X": "id {id}"}})
    assert catalog.resolve("X", "pt-BR", {"other": 1}) == "id {id}"


def test_negotiate_prefers_available_locale() -> None:
    """Negotiation returns the highest-q tag the catalog can serve."""
    catalog = default_message_catalog()
    assert catalog.negotiate("fr-FR,en;q=0.8", default_locale="pt-BR") == "en"
    assert catalog.negotiate("fr-FR", default_locale="pt-BR") == "pt-BR"
    assert catalog.negotiate(None, default_locale="pt-BR") == "pt-BR"


def test_merge_overlays_and_adds_locales() -> None:
    """Merge overrides keys and introduces new locales without mutating."""
    base = default_message_catalog()
    merged = base.merge(
        {
            "es": {"NOT_FOUND": "Recurso no encontrado"},
            "en-US": {"USER_NOT_FOUND": "User not found"},
        }
    )
    assert merged.resolve("NOT_FOUND", "es") == "Recurso no encontrado"
    assert merged.resolve("USER_NOT_FOUND", "en") == "User not found"
    # Base catalog is untouched.
    assert base.resolve("USER_NOT_FOUND", "en") is None


def test_default_catalog_covers_builtin_codes() -> None:
    """The built-in catalog carries PT-BR + EN for every SDK code."""
    catalog = default_message_catalog()
    codes = [
        "INTERNAL_SERVER_ERROR",
        "NOT_FOUND",
        "CONFLICT",
        "UNAUTHORIZED",
        "FORBIDDEN",
        "VALIDATION_ERROR",
        "TOO_MANY_REQUESTS",
        "INVALID_TOKEN",
        "TOKEN_EXPIRED",
        "FILE_TOO_LARGE",
        "INVALID_FILE_TYPE",
    ]
    for code in codes:
        assert catalog.resolve(code, "pt-BR") is not None
        assert catalog.resolve(code, "en") is not None


# --------------------------------------------------------------------------- #
# AppException carries the localization fields                                #
# --------------------------------------------------------------------------- #


def test_app_exception_localization_fields() -> None:
    """``message_key`` defaults to ``None`` and params default to empty."""
    exc = NotFoundException("nope")
    assert exc.message_key is None
    assert exc.message_params == {}


def test_app_exception_explicit_message_key_and_params() -> None:
    """Explicit ``message_key`` / ``message_params`` are stored."""
    exc = AppException(
        "x",
        code="USER_NOT_FOUND",
        status_code=404,
        message_key="USER_NOT_FOUND",
        message_params={"email": "a@b.com"},
    )
    assert exc.message_key == "USER_NOT_FOUND"
    assert exc.message_params == {"email": "a@b.com"}


# --------------------------------------------------------------------------- #
# Handler integration                                                         #
# --------------------------------------------------------------------------- #


def _make_app(*, catalog: MessageCatalog | None) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app, catalog=catalog)

    @app.get("/missing")
    async def missing() -> None:
        raise NotFoundException()

    @app.get("/user")
    async def user() -> None:
        raise AppException(
            "User not found",
            code="USER_NOT_FOUND",
            status_code=404,
            message_params={"email": "a@b.com"},
        )

    return app


def test_handler_localizes_by_accept_language() -> None:
    """The envelope ``detail`` follows the request's Accept-Language."""
    client = TestClient(_make_app(catalog=default_message_catalog()))
    pt = client.get("/missing", headers={"Accept-Language": "pt-BR"})
    en = client.get("/missing", headers={"Accept-Language": "en-US,en;q=0.9"})
    assert pt.json()["detail"] == "Recurso não encontrado"
    assert en.json()["detail"] == "Resource not found"
    assert pt.json()["code"] == "NOT_FOUND"


def test_handler_defaults_to_pt_br_without_header() -> None:
    """No Accept-Language falls back to the default locale (pt-BR)."""
    client = TestClient(_make_app(catalog=default_message_catalog()))
    response = client.get("/missing")
    assert response.json()["detail"] == "Recurso não encontrado"


def test_handler_without_catalog_keeps_literal_message() -> None:
    """Without a catalog the literal message is preserved (back-compat)."""
    client = TestClient(_make_app(catalog=None))
    response = client.get("/missing", headers={"Accept-Language": "en-US"})
    assert response.json()["detail"] == "Resource not found"  # class default


def test_handler_falls_back_when_key_missing_from_catalog() -> None:
    """An unknown code falls back to the exception's literal detail."""
    client = TestClient(_make_app(catalog=default_message_catalog()))
    response = client.get("/user", headers={"Accept-Language": "pt-BR"})
    # USER_NOT_FOUND is not in the built-in catalog → literal message.
    assert response.json()["detail"] == "User not found"
    assert response.json()["code"] == "USER_NOT_FOUND"


def test_handler_localizes_custom_code_with_params() -> None:
    """A merged catalog localizes a domain code and interpolates params."""
    catalog = default_message_catalog().merge(
        {
            "pt-BR": {"USER_NOT_FOUND": "Usuário {email} não encontrado"},
            "en-US": {"USER_NOT_FOUND": "User {email} not found"},
        }
    )
    client = TestClient(_make_app(catalog=catalog))
    pt = client.get("/user", headers={"Accept-Language": "pt-BR"})
    en = client.get("/user", headers={"Accept-Language": "en"})
    assert pt.json()["detail"] == "Usuário a@b.com não encontrado"
    assert en.json()["detail"] == "User a@b.com not found"
