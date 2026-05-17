"""Tests for tempest_fastapi_sdk.utils.regex."""

import pytest
from pydantic import BaseModel, ValidationError

from tempest_fastapi_sdk.utils import (
    CEP,
    CNPJ,
    CPF,
    CPFOrCNPJ,
    PhoneBR,
    is_valid_cep,
    is_valid_cnpj,
    is_valid_cpf,
    is_valid_cpf_cnpj,
    is_valid_phone_br,
    normalize_cep,
    normalize_cnpj,
    normalize_cpf,
    normalize_cpf_cnpj,
    normalize_phone_br,
    only_digits,
)

VALID_CPF: str = "52998224725"
VALID_CPF_MASKED: str = "529.982.247-25"
ANOTHER_VALID_CPF: str = "39053344705"

VALID_CNPJ: str = "11222333000181"
VALID_CNPJ_MASKED: str = "11.222.333/0001-81"
ANOTHER_VALID_CNPJ: str = "27865757000102"


class TestOnlyDigits:
    def test_strips_mask(self) -> None:
        assert only_digits("529.982.247-25") == "52998224725"

    def test_strips_phone_chars(self) -> None:
        assert only_digits("+55 (11) 98888-7777") == "5511988887777"

    def test_empty(self) -> None:
        assert only_digits("") == ""

    def test_already_digits(self) -> None:
        assert only_digits("12345") == "12345"


class TestIsValidCpf:
    def test_unmasked_valid(self) -> None:
        assert is_valid_cpf(VALID_CPF) is True

    def test_masked_valid(self) -> None:
        assert is_valid_cpf(VALID_CPF_MASKED) is True

    def test_partially_masked_valid(self) -> None:
        assert is_valid_cpf("529982247-25") is True

    def test_wrong_check_digit(self) -> None:
        assert is_valid_cpf("52998224724") is False

    def test_all_same_digits_rejected(self) -> None:
        assert is_valid_cpf("11111111111") is False

    def test_wrong_length(self) -> None:
        assert is_valid_cpf("123") is False

    def test_letters_rejected(self) -> None:
        assert is_valid_cpf("abc.def.ghi-jk") is False


class TestIsValidCnpj:
    def test_unmasked_valid(self) -> None:
        assert is_valid_cnpj(VALID_CNPJ) is True

    def test_masked_valid(self) -> None:
        assert is_valid_cnpj(VALID_CNPJ_MASKED) is True

    def test_wrong_check_digit(self) -> None:
        assert is_valid_cnpj("11222333000180") is False

    def test_all_same_digits_rejected(self) -> None:
        assert is_valid_cnpj("11111111111111") is False

    def test_wrong_length(self) -> None:
        assert is_valid_cnpj("123") is False

    def test_cpf_not_a_cnpj(self) -> None:
        assert is_valid_cnpj(VALID_CPF) is False


class TestIsValidCpfCnpj:
    def test_accepts_cpf(self) -> None:
        assert is_valid_cpf_cnpj(VALID_CPF) is True
        assert is_valid_cpf_cnpj(VALID_CPF_MASKED) is True

    def test_accepts_cnpj(self) -> None:
        assert is_valid_cpf_cnpj(VALID_CNPJ) is True
        assert is_valid_cpf_cnpj(VALID_CNPJ_MASKED) is True

    def test_rejects_garbage(self) -> None:
        assert is_valid_cpf_cnpj("12345") is False
        assert is_valid_cpf_cnpj("00000000000") is False


class TestIsValidPhoneBr:
    @pytest.mark.parametrize(
        "value",
        [
            "11988887777",
            "1133334444",
            "+5511988887777",
            "55 11 98888-7777",
            "(11) 98888-7777",
            "(11) 3333-4444",
            "11 98888 7777",
        ],
    )
    def test_accepts_valid_shapes(self, value: str) -> None:
        assert is_valid_phone_br(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "abc",
            "123",
            "123456789012345",
            "+1 415 555 0100",
        ],
    )
    def test_rejects_invalid(self, value: str) -> None:
        assert is_valid_phone_br(value) is False


class TestNormalize:
    def test_cpf_masked_to_digits(self) -> None:
        assert normalize_cpf(VALID_CPF_MASKED) == VALID_CPF

    def test_cpf_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid CPF"):
            normalize_cpf("00000000000")

    def test_cnpj_masked_to_digits(self) -> None:
        assert normalize_cnpj(VALID_CNPJ_MASKED) == VALID_CNPJ

    def test_cnpj_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid CNPJ"):
            normalize_cnpj("00000000000000")

    def test_cpf_cnpj_picks_either(self) -> None:
        assert normalize_cpf_cnpj(VALID_CPF_MASKED) == VALID_CPF
        assert normalize_cpf_cnpj(VALID_CNPJ_MASKED) == VALID_CNPJ

    def test_cpf_cnpj_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid CPF/CNPJ"):
            normalize_cpf_cnpj("123")

    def test_phone_to_digits(self) -> None:
        assert normalize_phone_br("(11) 98888-7777") == "11988887777"
        assert normalize_phone_br("+55 11 98888-7777") == "5511988887777"

    def test_phone_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid BR phone"):
            normalize_phone_br("abc")


class CPFSchema(BaseModel):
    document: CPF


class CNPJSchema(BaseModel):
    document: CNPJ


class CPFOrCNPJSchema(BaseModel):
    document: CPFOrCNPJ


class PhoneSchema(BaseModel):
    phone: PhoneBR


class TestPydanticTypes:
    def test_cpf_type_normalizes(self) -> None:
        result = CPFSchema(document=VALID_CPF_MASKED)
        assert result.document == VALID_CPF

    def test_cpf_type_rejects(self) -> None:
        with pytest.raises(ValidationError):
            CPFSchema(document="00000000000")

    def test_cnpj_type_normalizes(self) -> None:
        result = CNPJSchema(document=VALID_CNPJ_MASKED)
        assert result.document == VALID_CNPJ

    def test_cnpj_type_rejects(self) -> None:
        with pytest.raises(ValidationError):
            CNPJSchema(document="00000000000000")

    def test_cpf_or_cnpj_accepts_both(self) -> None:
        assert CPFOrCNPJSchema(document=VALID_CPF_MASKED).document == VALID_CPF
        assert CPFOrCNPJSchema(document=VALID_CNPJ_MASKED).document == VALID_CNPJ

    def test_phone_type_normalizes(self) -> None:
        result = PhoneSchema(phone="(11) 98888-7777")
        assert result.phone == "11988887777"

    def test_phone_type_rejects(self) -> None:
        with pytest.raises(ValidationError):
            PhoneSchema(phone="abc")


class TestIsValidCep:
    @pytest.mark.parametrize(
        "value",
        [
            "01310-100",
            "01310100",
            "00000-000",
            "12345-678",
        ],
    )
    def test_accepts_valid_shapes(self, value: str) -> None:
        assert is_valid_cep(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "1234567",       # 7 digits
            "123456789",     # 9 digits
            "01310.100",     # wrong mask char
            "abcde-123",     # letters
            "01310 100",     # space mask
        ],
    )
    def test_rejects_invalid(self, value: str) -> None:
        assert is_valid_cep(value) is False


class TestNormalizeCep:
    def test_masked_to_digits(self) -> None:
        assert normalize_cep("01310-100") == "01310100"

    def test_already_digits(self) -> None:
        assert normalize_cep("01310100") == "01310100"

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="CEP"):
            normalize_cep("01310 100")


class TestCepAnnotatedType:
    class _Schema(BaseModel):
        cep: CEP

    def test_normalizes_on_validate(self) -> None:
        assert self._Schema(cep="01310-100").cep == "01310100"

    def test_rejects_invalid(self) -> None:
        with pytest.raises(ValidationError):
            self._Schema(cep="not-a-cep")
