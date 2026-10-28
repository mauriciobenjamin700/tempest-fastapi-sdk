"""The bundled routers document themselves in the configured language (#643).

Measured on the rendered OpenAPI document, which is what ``/docs`` and a
client generator read — not on the catalog, which the coverage guard
already checks.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import ValidationError

from tempest_fastapi_sdk.api.route_docs import RouteDocs, resolve_docs_locale
from tempest_fastapi_sdk.core import normalize_docs_locale
from tempest_fastapi_sdk.settings import ServerSettings
from tests.api.route_docs_apps import ROUTER_BUILDERS, build_app

ROUTERS: list[str] = sorted(ROUTER_BUILDERS)

HTTP_METHODS: frozenset[str] = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options"},
)


@pytest.fixture(autouse=True)
def _no_ambient_locale(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Run every test without ``API_DOCS_LOCALE`` and without a ``.env``.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture.
        tmp_path (Any): An empty directory to run from, so a developer's
            ``.env`` cannot pick the language.
    """
    monkeypatch.delenv("API_DOCS_LOCALE", raising=False)
    monkeypatch.chdir(tmp_path)


def _operations(document: dict[str, Any]) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """Yield every operation of an OpenAPI document.

    Args:
        document (dict[str, Any]): The OpenAPI document.

    Yields:
        tuple[str, str, dict[str, Any]]: Path, method and operation.
    """
    for path, item in document["paths"].items():
        for method, operation in item.items():
            if method in HTTP_METHODS:
                yield path, method, operation


def _contract(document: dict[str, Any]) -> Any:
    """Strip the prose of a document, keeping what a generated client reads.

    Args:
        document (dict[str, Any]): The OpenAPI document.

    Returns:
        Any: The document with every ``summary`` and ``description``
        removed. A form or file parameter's description lives in the
        ``Body_*`` component schema, so components are stripped too;
        every ``title`` stays and is compared.
    """

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                k: strip(v)
                for k, v in node.items()
                if k not in {"summary", "description"}
            }
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return {
        "paths": strip(document["paths"]),
        "components": strip(document.get("components")),
    }


class TestTheLocaleReachesTheDocument:
    """``API_DOCS_LOCALE`` and ``docs_locale=`` pick the language."""

    def test_auth_login_in_portuguese_from_the_environment(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The env var alone switches the auth router to Portuguese."""
        monkeypatch.setenv("API_DOCS_LOCALE", "pt-BR")
        login = build_app("auth").openapi()["paths"]["/auth/login"]["post"]
        assert login["summary"] == "Entrar com e-mail + senha → par JWT"
        assert login["description"].startswith("Autentica um usuário **ativo**")
        throttled = login["responses"]["429"]["description"]
        assert throttled.startswith("Logins com falha demais")

    def test_auth_login_in_english_by_default(self) -> None:
        """Without the env var the document is the English one."""
        login = build_app("auth").openapi()["paths"]["/auth/login"]["post"]
        assert login["summary"] == "Log in with email + password → JWT pair"

    def test_the_argument_wins_over_the_environment(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``docs_locale=`` overrides ``API_DOCS_LOCALE``."""
        monkeypatch.setenv("API_DOCS_LOCALE", "pt-BR")
        doc = build_app("health", "en-US").openapi()
        assert doc["paths"]["/health/liveness"]["get"]["summary"] == "Liveness probe"

    def test_sse_fills_its_placeholders_in_portuguese(self) -> None:
        """The SSE text carries the mounted prefix in both languages."""
        doc = build_app("sse", "pt-BR").openapi()
        ticket = doc["paths"]["/sse/ticket"]["post"]["description"]
        assert "`GET /sse/stream?ticket=...`" in ticket
        assert ticket.startswith("Troca a sessão autenticada")

    def test_a_parameter_description_follows_the_locale(self) -> None:
        """Declared parameter descriptions are translated too."""
        doc = build_app("logs", "pt-BR").openapi()
        params = {
            p["name"]: p["description"]
            for p in doc["paths"]["/logs"]["get"]["parameters"]
        }
        assert params["page"] == "Número da página (começa em 1)."

    def test_a_follow_up_router_in_portuguese(self) -> None:
        """The routers added after #643 shipped follow the locale too."""
        doc = build_app("chat", "pt-BR").openapi()
        start = doc["paths"]["/api/chat/conversations"]["post"]
        assert start["summary"] == "Iniciar uma conversa"
        assert start["description"].startswith("Cria uma conversa")

    def test_the_english_fallback_is_the_docstring(self) -> None:
        """A route that never declared text keeps its docstring in English."""
        doc = build_app("wallet").openapi()
        balance = doc["paths"]["/api/wallet/balance"]["get"]
        assert balance["summary"] == "Get Balance"
        assert balance["description"].startswith(
            "Return the caller's balance split into held and available."
        )


@pytest.mark.parametrize("name", ["agent", "ai_chat", "genai"])
def test_streaming_routers_render_their_document(name: str) -> None:
    """``/openapi.json`` renders for the routers with an SSE endpoint.

    Their ``-> StreamingResponse`` annotation named a class imported only
    under ``TYPE_CHECKING``; with ``from __future__ import annotations``
    FastAPI saw an unresolved forward reference and ``app.openapi()``
    raised ``PydanticUserError``, so ``/docs`` answered 500 for any app
    that mounted one of them.

    Args:
        name (str): The router.
    """
    paths = build_app(name).openapi()["paths"]
    stream = next(item for path, item in paths.items() if path.endswith("/stream"))
    assert "200" in stream["post"]["responses"]


@pytest.mark.parametrize("name", ROUTERS)
class TestEveryCoveredRouter:
    """The properties hold for every router the catalog covers."""

    def test_omitting_the_argument_is_explicit_english(self, name: str) -> None:
        """No argument and no env var renders the ``en-US`` document."""
        implicit = build_app(name).openapi()
        explicit = build_app(name, "en-US").openapi()
        assert json.dumps(implicit, sort_keys=True) == json.dumps(
            explicit,
            sort_keys=True,
        )

    def test_every_operation_is_translated(self, name: str) -> None:
        """Every pt-BR summary and description differs from the English one."""
        english = {(p, m): op for p, m, op in _operations(build_app(name).openapi())}
        portuguese = {
            (p, m): op for p, m, op in _operations(build_app(name, "pt-BR").openapi())
        }
        assert english.keys() == portuguese.keys()
        untranslated: list[str] = [
            f"{m.upper()} {p} {field}"
            for (p, m), op in portuguese.items()
            for field in ("summary", "description")
            if not op.get(field) or op.get(field) == english[(p, m)].get(field)
        ]
        assert untranslated == []

    def test_the_client_contract_is_the_same(self, name: str) -> None:
        """Paths, ids, parameters and schemas do not depend on the language."""
        english = _contract(build_app(name, "en-US").openapi())
        portuguese = _contract(build_app(name, "pt-BR").openapi())
        assert english == portuguese


class TestLocaleResolution:
    """How a locale string becomes ``en-US`` or ``pt-BR``."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("pt-BR", "pt-BR"),
            ("pt_br", "pt-BR"),
            ("PT", "pt-BR"),
            ("en", "en-US"),
            ("EN-us", "en-US"),
        ],
    )
    def test_loose_spellings(self, raw: str, expected: str) -> None:
        """Case, separator and the bare primary subtag are accepted."""
        assert normalize_docs_locale(raw) == expected
        assert resolve_docs_locale(raw) == expected

    @pytest.mark.parametrize("raw", ["es-ES", "pt-PT", "klingon"])
    def test_a_language_the_catalog_lacks_is_refused(self, raw: str) -> None:
        """No silent fallback to English for an unsupported language."""
        with pytest.raises(ValueError, match="API docs locale"):
            RouteDocs(raw)

    def test_the_environment_is_validated(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A bad ``API_DOCS_LOCALE`` fails the build, naming the variable."""
        monkeypatch.setenv("API_DOCS_LOCALE", "fr-FR")
        with pytest.raises(ValidationError, match="API_DOCS_LOCALE"):
            build_app("health")

    def test_server_settings_default_and_normalization(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The setting defaults to ``en-US`` and stores the canonical tag."""
        assert ServerSettings().API_DOCS_LOCALE == "en-US"
        monkeypatch.setenv("API_DOCS_LOCALE", "pt_br")
        assert ServerSettings().API_DOCS_LOCALE == "pt-BR"

    def test_an_unknown_key_fails_at_build_time(self) -> None:
        """A route reading a key the catalog lacks cannot be mounted."""
        with pytest.raises(KeyError, match=r"auth\.nope"):
            RouteDocs("pt-BR").summary("auth.nope")
