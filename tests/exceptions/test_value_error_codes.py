"""The BR validators localize their own phrase, not just the template.

Pydantic files every ``ValueError`` a field validator raises under the
single error type ``value_error``, so the catalog — which localizes by
type — had one key for all of them. The useful half of the message was
the English phrase the SDK's own validator wrote, and it reached a PT-BR
client verbatim::

    Valor inválido: invalid CPF/CNPJ

:class:`ValidationValueError` names a code instead, and the validation
handler resolves ``VALIDATION.<code>`` with a fallback to
``VALIDATION.value_error`` — so a consumer's plain ``ValueError`` keeps
behaving exactly as it did.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import AfterValidator, BaseModel, ValidationError

from tempest_fastapi_sdk import (
    ValidationValueError,
    default_message_catalog,
    register_exception_handlers,
)
from tempest_fastapi_sdk.exceptions.i18n import VALIDATION_KEY_PREFIX
from tempest_fastapi_sdk.utils.locations import UFField
from tempest_fastapi_sdk.utils.regex import (
    CEPField,
    CNPJField,
    CPFField,
    CPFOrCNPJField,
    MobilePhoneBRField,
    PhoneBRField,
)

PT = {"Accept-Language": "pt-BR"}
EN = {"Accept-Language": "en-US"}


def _reject(_value: str) -> str:
    """Reject with a plain ``ValueError``, the way a consumer would.

    Args:
        _value (str): The submitted value, unused.

    Returns:
        str: Never — always raises.

    Raises:
        ValueError: Always.
    """
    raise ValueError("consumer rule failed")


class Body(BaseModel):
    """Every SDK-typed field, each optional so one test hits one."""

    cpf: CPFField | None = None
    cnpj: CNPJField | None = None
    doc: CPFOrCNPJField | None = None
    cep: CEPField | None = None
    phone: PhoneBRField | None = None
    mobile: MobilePhoneBRField | None = None
    uf: UFField | None = None
    custom: Annotated[str, AfterValidator(_reject)] | None = None


def _app() -> FastAPI:
    """Build an app whose 422 goes through the SDK envelope.

    Returns:
        FastAPI: The application under test.
    """
    app = FastAPI()
    register_exception_handlers(
        app,
        catalog=default_message_catalog(),
        default_locale="pt-BR",
        envelope_validation_errors=True,
    )

    @app.post("/check")
    async def check(body: Body) -> None: ...

    return app


def _message(payload: dict[str, Any], headers: dict[str, str]) -> str:
    """Post a payload and return the single validation message.

    Args:
        payload (dict[str, Any]): The request body.
        headers (dict[str, str]): Request headers (the locale).

    Returns:
        str: The ``msg`` of the first reported error.
    """
    response = TestClient(_app()).post("/check", json=payload, headers=headers)
    assert response.status_code == 422
    errors = response.json()["details"]["errors"]
    assert len(errors) == 1, errors
    return str(errors[0]["msg"])


class TestCodesReachTheError:
    """The attribute the handler reads."""

    @pytest.mark.parametrize(
        ("field", "value", "code"),
        [
            ("cpf", "123", "INVALID_CPF"),
            ("cnpj", "123", "INVALID_CNPJ"),
            ("doc", "123", "INVALID_CPF_CNPJ"),
            ("cep", "1", "INVALID_CEP"),
            ("phone", "1", "INVALID_PHONE_BR"),
            ("mobile", "1133334444", "INVALID_MOBILE_PHONE_BR"),
            ("uf", "ZZ", "INVALID_UF"),
        ],
    )
    def test_each_validator_names_its_code(
        self,
        field: str,
        value: str,
        code: str,
    ) -> None:
        with pytest.raises(ValidationError) as raised:
            Body(**{field: value})

        inner = raised.value.errors()[0]["ctx"]["error"]
        assert isinstance(inner, ValidationValueError)
        assert inner.code == code

    def test_english_phrase_is_preserved_as_str(self) -> None:
        """``str(exc)`` is what pydantic's own ``msg`` renders."""
        with pytest.raises(ValidationError) as raised:
            Body(doc="123")

        assert raised.value.errors()[0]["msg"] == "Value error, invalid CPF/CNPJ"


class TestLocalizedMessages:
    """What the client reads."""

    @pytest.mark.parametrize(
        ("field", "value", "expected"),
        [
            ("cpf", "123", "CPF inválido"),
            ("cnpj", "123", "CNPJ inválido"),
            ("doc", "123", "CPF ou CNPJ inválido"),
            ("cep", "1", "CEP inválido"),
            ("phone", "1", "Telefone brasileiro inválido"),
            ("mobile", "1133334444", "Número de celular brasileiro inválido"),
        ],
    )
    def test_pt_br_is_fully_portuguese(
        self,
        field: str,
        value: str,
        expected: str,
    ) -> None:
        assert _message({field: value}, PT) == expected

    def test_en_us_drops_the_pydantic_prefix(self) -> None:
        """Upstream renders ``Value error, invalid CPF/CNPJ``."""
        assert _message({"doc": "123"}, EN) == "Invalid CPF or CNPJ"

    def test_params_are_interpolated(self) -> None:
        assert _message({"uf": "ZZ"}, PT) == "UF inválida: ZZ"

    def test_consumer_value_error_still_uses_the_generic_template(self) -> None:
        """The fallback path: a plain ``ValueError`` is untouched."""
        assert _message({"custom": "x"}, PT) == "Valor inválido: consumer rule failed"


class TestCatalogKeys:
    """The keys themselves, resolved directly."""

    @pytest.mark.parametrize("locale", ["pt-BR", "en-US"])
    def test_every_code_is_translated_in_both_locales(self, locale: str) -> None:
        catalog = default_message_catalog()
        codes = [
            "INVALID_CEP",
            "INVALID_CNPJ",
            "INVALID_CPF",
            "INVALID_CPF_CNPJ",
            "INVALID_MOBILE_PHONE_BR",
            "INVALID_PHONE_BR",
            "INVALID_PIX_KEY",
            "INVALID_UF",
            "UNKNOWN_CITY",
        ]

        missing = [
            code
            for code in codes
            if catalog.resolve(f"{VALIDATION_KEY_PREFIX}{code}", locale) is None
        ]

        assert missing == []

    def test_upper_snake_codes_never_collide_with_pydantic_types(self) -> None:
        """The namespace is shared; the casing is what keeps it safe."""
        catalog = default_message_catalog()

        assert catalog.resolve(f"{VALIDATION_KEY_PREFIX}value_error", "pt-BR") == (
            "Valor inválido: {error}"
        )


class TestRaisedByHelpers:
    """The non-field helpers raise the same class."""

    def test_normalize_uf_raises_with_a_code(self) -> None:
        from tempest_fastapi_sdk.utils.locations import normalize_uf

        with pytest.raises(ValidationValueError) as raised:
            normalize_uf("ZZ")

        assert raised.value.code == "INVALID_UF"
        assert raised.value.params == {"value": "ZZ"}

    def test_normalize_city_raises_with_a_code(self) -> None:
        from tempest_fastapi_sdk.utils.locations import normalize_city

        with pytest.raises(ValidationValueError) as raised:
            normalize_city("SP", "Cidade Que Nao Existe")

        assert raised.value.code == "UNKNOWN_CITY"
        assert raised.value.params["uf"] == "SP"

    def test_pix_key_raises_with_a_code(self) -> None:
        from tempest_fastapi_sdk.utils.regex import normalize_pix_key

        with pytest.raises(ValidationValueError) as raised:
            normalize_pix_key("not a key")

        assert raised.value.code == "INVALID_PIX_KEY"

    def test_it_is_still_a_value_error(self) -> None:
        """Existing ``except ValueError`` handlers keep catching it."""
        assert issubclass(ValidationValueError, ValueError)
