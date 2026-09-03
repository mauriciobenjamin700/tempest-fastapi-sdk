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

    Notes:
        The header index is the sort tiebreaker, so tags sharing a quality
        value keep the order the client sent them in.
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

        Notes:
            A primary-subtag index is built alongside the full-tag one, so a
            request for ``"en"`` matches a catalog holding ``"en-US"`` and
            vice versa. The first locale registered for a given primary
            subtag wins.
        """
        self._translations: dict[str, dict[str, str]] = {
            locale.lower(): dict(table) for locale, table in translations.items()
        }
        self._by_primary: dict[str, dict[str, str]] = {}
        for locale, table in self._translations.items():
            self._by_primary.setdefault(locale.split("-", 1)[0], table)

    @property
    def locales(self) -> list[str]:
        """Return the locale tags this catalog knows, lower-cased.

        Returns:
            list[str]: The catalog's locale tags, lower-cased.
        """
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


VALIDATION_KEY_PREFIX: str = "VALIDATION."
"""Namespace for the request-validation messages in the catalog.

Kept separate from the exception ``code`` keys so the two sets are
*checkable* rather than merely non-colliding: a code is ``UPPER_SNAKE``
and a pydantic error type is ``lower_snake``, which keeps them apart
today by accident and not by rule.
"""

_PYDANTIC_ERROR_TYPES_PT_BR: dict[str, str] = {
    "arguments_type": "Os argumentos devem ser uma tupla, uma lista ou um dicionário",
    "assertion_error": "Falha na asserção: {error}",
    "bool_parsing": "Deve ser um booleano válido; não foi possível interpretar o valor",
    "bool_type": "Deve ser um booleano válido",
    "bytes_invalid_encoding": (
        "Os dados devem estar em {encoding} válido: {encoding_error}"
    ),
    "bytes_too_long": "Os dados devem ter no máximo {max_length} byte(s)",
    "bytes_too_short": "Os dados devem ter no mínimo {min_length} byte(s)",
    "bytes_type": "Deve ser uma sequência de bytes válida",
    "callable_type": "Deve ser algo chamável",
    "complex_str_parsing": "Deve ser um texto de número complexo válido",
    "complex_type": (
        "Deve ser um número complexo válido, um número, ou um texto de número complexo"
        " válido"
    ),
    "dataclass_exact_type": "Deve ser uma instância de {class_name}",
    "dataclass_type": "Deve ser um dicionário ou uma instância de {class_name}",
    "date_from_datetime_inexact": (
        "Data e hora informada onde se espera uma data deve ter a hora zerada, ou "
        "seja, ser uma data exata"
    ),
    "date_from_datetime_parsing": (
        "Deve ser uma data, ou uma data e hora, válida: {error}"
    ),
    "date_future": "A data deve estar no futuro",
    "date_parsing": "Deve ser uma data válida no formato AAAA-MM-DD: {error}",
    "date_past": "A data deve estar no passado",
    "date_type": "Deve ser uma data válida",
    "datetime_from_date_parsing": (
        "Deve ser uma data e hora, ou uma data, válida: {error}"
    ),
    "datetime_future": "Deve estar no futuro",
    "datetime_object_invalid": "Objeto de data e hora inválido: {error}",
    "datetime_parsing": "Deve ser uma data e hora válida: {error}",
    "datetime_past": "Deve estar no passado",
    "datetime_type": "Deve ser uma data e hora válida",
    "decimal_max_digits": (
        "O decimal deve ter no máximo {max_digits} dígito(s) no total"
    ),
    "decimal_max_places": (
        "O decimal deve ter no máximo {decimal_places} casa(s) decimal(is)"
    ),
    "decimal_parsing": "Deve ser um decimal válido",
    "decimal_type": (
        "O decimal deve ser um inteiro, um número, um texto ou um objeto Decimal"
    ),
    "decimal_whole_digits": (
        "O decimal deve ter no máximo {whole_digits} dígito(s) antes da vírgula"
    ),
    "default_factory_not_called": (
        "A fábrica de valor padrão usa dados validados, mas houve pelo menos um erro "
        "de validação"
    ),
    "dict_type": "Deve ser um dicionário válido",
    "enum": "Deve ser {expected}",
    "extra_forbidden": "Campos extras não são permitidos",
    "finite_number": "Deve ser um número finito",
    "float_parsing": (
        "Deve ser um número válido; não foi possível interpretar o texto como número"
    ),
    "float_type": "Deve ser um número válido",
    "frozen_field": "O campo é somente leitura",
    "frozen_instance": "O objeto é somente leitura",
    "frozen_set_type": "Deve ser um frozenset válido",
    "get_attribute_error": "Erro ao extrair o atributo: {error}",
    "greater_than": "Deve ser maior que {gt}",
    "greater_than_equal": "Deve ser maior ou igual a {ge}",
    "int_from_float": (
        "Deve ser um inteiro válido; foi recebido um número com parte fracionária"
    ),
    "int_parsing": (
        "Deve ser um inteiro válido; não foi possível interpretar o texto como inteiro"
    ),
    "int_parsing_size": (
        "Não foi possível interpretar o texto como inteiro: tamanho máximo excedido"
    ),
    "int_type": "Deve ser um inteiro válido",
    "invalid_key": "As chaves devem ser textos",
    "is_instance_of": "Deve ser uma instância de {class}",
    "is_subclass_of": "Deve ser uma subclasse de {class}",
    "iterable_type": "Deve ser iterável",
    "iteration_error": "Erro ao iterar sobre o objeto: {error}",
    "json_invalid": "JSON inválido: {error}",
    "json_type": "A entrada JSON deve ser texto, bytes ou bytearray",
    "less_than": "Deve ser menor que {lt}",
    "less_than_equal": "Deve ser menor ou igual a {le}",
    "list_type": "Deve ser uma lista válida",
    "literal_error": "Deve ser {expected}",
    "mapping_type": "Deve ser um mapeamento válido: {error}",
    "missing": "Campo obrigatório",
    "missing_argument": "Argumento obrigatório ausente",
    "missing_keyword_only_argument": "Argumento nomeado obrigatório ausente",
    "missing_positional_only_argument": "Argumento posicional obrigatório ausente",
    "missing_sentinel_error": "Deve ser o sentinela 'MISSING'",
    "model_attributes_type": (
        "Deve ser um dicionário válido, ou um objeto do qual extrair os campos"
    ),
    "model_type": "Deve ser um dicionário válido ou uma instância de {class_name}",
    "multiple_argument_values": "Foram recebidos vários valores para o mesmo argumento",
    "multiple_of": "Deve ser múltiplo de {multiple_of}",
    "needs_python_object": (
        "Não é possível verificar `{method_name}` ao validar a partir de JSON; use um "
        "validador JsonOrPython"
    ),
    "no_such_attribute": "O objeto não tem o atributo '{attribute}'",
    "none_required": "Deve ser nulo",
    "recursion_loop": "Erro de recursão: referência cíclica detectada",
    "set_item_not_hashable": "Os itens do conjunto devem ser hasheáveis",
    "set_type": "Deve ser um conjunto válido",
    "string_not_ascii": "O texto deve conter apenas caracteres ASCII",
    "string_pattern_mismatch": "O texto deve casar com o padrão '{pattern}'",
    "string_sub_type": "Deve ser um texto, não uma instância de uma subclasse de str",
    "string_too_long": "O texto deve ter no máximo {max_length} caractere(s)",
    "string_too_short": "O texto deve ter no mínimo {min_length} caractere(s)",
    "string_type": "Deve ser um texto válido",
    "string_unicode": (
        "Deve ser um texto válido; não foi possível interpretar os dados como texto "
        "unicode"
    ),
    "time_delta_parsing": "Deve ser uma duração válida: {error}",
    "time_delta_type": "Deve ser uma duração válida",
    "time_parsing": "Deve estar em um formato de hora válido: {error}",
    "time_type": "Deve ser uma hora válida",
    "timezone_aware": "Deve incluir informação de fuso horário",
    "timezone_naive": "Não deve incluir informação de fuso horário",
    "timezone_offset": "É exigido o fuso {tz_expected}, e foi recebido {tz_actual}",
    "too_long": (
        "Deve ter no máximo {max_length} item(ns) após a validação, e tem "
        "{actual_length}"
    ),
    "too_short": (
        "Deve ter no mínimo {min_length} item(ns) após a validação, e tem "
        "{actual_length}"
    ),
    "tuple_type": "Deve ser uma tupla válida",
    "unexpected_keyword_argument": "Argumento nomeado inesperado",
    "unexpected_positional_argument": "Argumento posicional inesperado",
    "union_tag_invalid": (
        "A tag '{tag}', encontrada por {discriminator}, não corresponde a nenhuma das "
        "esperadas: {expected_tags}"
    ),
    "union_tag_not_found": (
        "Não foi possível extrair a tag usando o discriminador {discriminator}"
    ),
    "url_parsing": "Deve ser uma URL válida: {error}",
    "url_scheme": "O esquema da URL deve ser {expected_schemes}",
    "url_syntax_violation": "A URL viola as regras estritas de sintaxe: {error}",
    "url_too_long": "A URL deve ter no máximo {max_length} caractere(s)",
    "url_type": "A URL deve ser um texto ou um objeto URL",
    "uuid_parsing": "Deve ser um UUID válido: {error}",
    "uuid_type": "O UUID deve ser um texto, bytes ou um objeto UUID",
    "uuid_version": "É esperado UUID versão {expected_version}",
    "value_error": "Valor inválido: {error}",
}
"""Portuguese message for every ``pydantic_core`` error type.

Ported from pydantic-core's ``ErrorType``. The key set is the 104 members
of ``typing.get_args(pydantic_core.ErrorType)``, which
``pydantic_core.list_all_errors()`` reports identically — read on pydantic
2.13.5 / pydantic-core 2.46.5, the version this repo's floor declares and
the lock resolves. ``tests/test_pydantic_error_types_guard.py`` fails when
the installed pydantic and this table disagree in either direction.

Only Portuguese is carried, and that asymmetry is deliberate: pydantic's
own ``msg`` **is** the English message, so the validation handler falls
back to it instead of this SDK keeping a copy that can drift from
upstream wording.

Every placeholder used here is one pydantic actually puts in the error's
``ctx``, checked against its ``example_context``. ``{expected_plural}``
appears in upstream templates and is **not** in ``ctx``, so a template
using it would reach the client with literal braces —
:meth:`MessageCatalog.resolve` returns the template unformatted when a
param is missing. The Portuguese here says ``caractere(s)`` instead.
"""

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
        "OAUTH_ACCOUNT_INACTIVE": "Conta inativa",
        "OAUTH_ACCOUNT_NOT_LINKED": ("Este provedor não está vinculado a esta conta"),
        "OAUTH_AUDIENCE_UNVERIFIABLE": (
            "Este provedor não permite verificar para qual aplicação o "
            "token foi emitido"
        ),
        "OAUTH_CODE_MISSING": (
            "O retorno do provedor não trouxe o código de autorização"
        ),
        "OAUTH_EMAIL_MISSING": "O provedor de identidade não informou um e-mail",
        "OAUTH_EMAIL_TAKEN": (
            "E-mail já cadastrado — entre com sua senha e vincule o "
            "provedor nas configurações da conta"
        ),
        "OAUTH_EMAIL_UNVERIFIED": (
            "O provedor de identidade não verificou este e-mail"
        ),
        "OAUTH_PROVIDER_DENIED": "O provedor não autorizou o login",
        "OAUTH_PROVIDER_NOT_CONFIGURED": "Provedor OAuth desconhecido",
        "OAUTH_REGISTRATION_DISABLED": (
            "Esta conta não existe e o cadastro automático está desativado"
        ),
        "OAUTH_STATE_MISMATCH": (
            "Divergência no state do OAuth — o retorno não foi iniciado "
            "por este navegador"
        ),
        "OAUTH_TOKEN_AUDIENCE_MISMATCH": (
            "O token apresentado foi emitido para outra aplicação"
        ),
        "OAUTH_TOKEN_REJECTED": (
            "O provedor de identidade recusou o token apresentado"
        ),
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
        "OAUTH_ACCOUNT_INACTIVE": "Account is not active",
        "OAUTH_ACCOUNT_NOT_LINKED": "Provider is not linked to this account",
        "OAUTH_AUDIENCE_UNVERIFIABLE": (
            "This provider cannot verify who the token was issued to"
        ),
        "OAUTH_CODE_MISSING": "The callback carried no authorization code",
        "OAUTH_EMAIL_MISSING": ("The identity provider returned no email address"),
        "OAUTH_EMAIL_TAKEN": (
            "Email already registered — sign in and link the provider "
            "from your account settings"
        ),
        "OAUTH_EMAIL_UNVERIFIED": ("The identity provider did not verify this email"),
        "OAUTH_PROVIDER_DENIED": "The provider did not authorize the login",
        "OAUTH_PROVIDER_NOT_CONFIGURED": "Unknown OAuth provider",
        "OAUTH_REGISTRATION_DISABLED": (
            "This account does not exist and self-service registration is disabled"
        ),
        "OAUTH_STATE_MISMATCH": (
            "OAuth state mismatch — the callback was not started by this browser"
        ),
        "OAUTH_TOKEN_AUDIENCE_MISMATCH": (
            "The presented token was issued to a different application"
        ),
        "OAUTH_TOKEN_REJECTED": ("The identity provider rejected the presented token"),
    },
}


_BUILTIN_TRANSLATIONS["pt-BR"].update(
    {
        f"{VALIDATION_KEY_PREFIX}{error_type}": message
        for error_type, message in _PYDANTIC_ERROR_TYPES_PT_BR.items()
    },
)


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
    "VALIDATION_KEY_PREFIX",
    "MessageCatalog",
    "default_message_catalog",
    "parse_accept_language",
]
